// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_url_loader_factory.h"

#include <memory>
#include <optional>
#include <utility>

#include "base/functional/bind.h"
#include "base/functional/callback.h"
#include "base/memory/weak_ptr.h"
#include "chrome/browser/focus_block/focus_block_service.h"
#include "chrome/browser/focus_block/focus_block_service_factory.h"
#include "content/public/browser/render_frame_host.h"
#include "mojo/public/cpp/bindings/pending_receiver.h"
#include "mojo/public/cpp/bindings/pending_remote.h"
#include "mojo/public/cpp/bindings/receiver.h"
#include "mojo/public/cpp/bindings/remote.h"
#include "mojo/public/cpp/bindings/self_owned_receiver.h"
#include "net/base/isolation_info.h"
#include "net/base/net_errors.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/cpp/self_deleting_url_loader_factory.h"
#include "services/network/public/cpp/url_loader_completion_status.h"
#include "services/network/public/cpp/url_loader_factory_builder.h"
#include "services/network/public/mojom/early_hints.mojom.h"
#include "services/network/public/mojom/fetch_api.mojom.h"
#include "services/network/public/mojom/url_loader.mojom.h"
#include "services/network/public/mojom/url_loader_factory.mojom.h"
#include "services/network/public/mojom/url_response_head.mojom.h"
#include "url/origin.h"

namespace focus_block {
namespace {

using URLLoaderFactoryType =
    content::ContentBrowserClient::URLLoaderFactoryType;

bool IsOutermostMainDocumentRequest(
    const network::ResourceRequest& request) {
  return request.is_outermost_main_frame &&
         request.destination == network::mojom::RequestDestination::kDocument;
}

bool IsSupportedFactoryType(URLLoaderFactoryType type) {
  switch (type) {
    case URLLoaderFactoryType::kNavigation:
    case URLLoaderFactoryType::kDocumentSubResource:
    case URLLoaderFactoryType::kWorkerMainResource:
    case URLLoaderFactoryType::kWorkerSubResource:
    case URLLoaderFactoryType::kServiceWorkerScript:
    case URLLoaderFactoryType::kServiceWorkerSubResource:
    case URLLoaderFactoryType::kPrefetch:
    case URLLoaderFactoryType::kEarlyHints:
      return true;
    case URLLoaderFactoryType::kDownload:
    case URLLoaderFactoryType::kDevTools:
      return false;
  }
  return false;
}

GURL OriginUrl(const std::optional<url::Origin>& origin) {
  if (!origin || origin->opaque()) {
    return GURL();
  }
  return origin->GetURL();
}

GURL TopLevelUrlForRequest(
    const GURL& factory_top_level_url,
    const network::ResourceRequest& request) {
  // A factory snapshot is stable across redirects and also identifies the
  // correct page for prerender/BFCache factories.
  if (factory_top_level_url.SchemeIsHTTPOrHTTPS()) {
    return factory_top_level_url;
  }

  // Worker and service-worker requests may carry their top-frame identity
  // only in trusted per-request isolation info.
  if (request.trusted_params) {
    GURL request_top_level_url = OriginUrl(
        request.trusted_params->isolation_info.top_frame_origin());
    if (request_top_level_url.SchemeIsHTTPOrHTTPS()) {
      return request_top_level_url;
    }
  }
  GURL cookie_site = request.site_for_cookies.RepresentativeUrl();
  if (cookie_site.SchemeIsHTTPOrHTTPS()) {
    return cookie_site;
  }
  return GURL();
}

GURL MatchSourceUrlForRequest(const network::ResourceRequest& request,
                              const GURL& factory_initiator_url,
                              const GURL& top_level_url) {
  if (request.request_initiator && !request.request_initiator->opaque()) {
    GURL initiator = request.request_initiator->GetURL();
    if (initiator.SchemeIsHTTPOrHTTPS()) {
      return initiator;
    }
  }
  if (request.referrer.SchemeIsHTTPOrHTTPS()) {
    return request.referrer;
  }
  if (factory_initiator_url.SchemeIsHTTPOrHTTPS()) {
    return factory_initiator_url;
  }
  return top_level_url;
}

class FocusBlockURLLoader final : public network::mojom::URLLoader,
                                  public network::mojom::URLLoaderClient {
 public:
  FocusBlockURLLoader(
      int32_t request_id,
      uint32_t options,
      const network::ResourceRequest& request,
      mojo::PendingRemote<network::mojom::URLLoaderClient> client,
      const net::MutableNetworkTrafficAnnotationTag& traffic_annotation,
      mojo::PendingRemote<network::mojom::URLLoaderFactory> target_factory,
      base::WeakPtr<FocusBlockService> service,
      GURL top_level_url,
      GURL match_source_url)
      : request_id_(request_id),
        options_(options),
        request_(request),
        client_(std::move(client)),
        traffic_annotation_(traffic_annotation),
        service_(std::move(service)),
        top_level_url_(std::move(top_level_url)),
        match_source_url_(std::move(match_source_url)) {
    target_factory_.Bind(std::move(target_factory));
    client_.set_disconnect_handler(base::BindOnce(
        &FocusBlockURLLoader::OnUpstreamClientDisconnected,
        base::Unretained(this)));
    CheckRequest(
        request_,
        base::BindOnce(&FocusBlockURLLoader::OnInitialDecision,
                       weak_ptr_factory_.GetWeakPtr()));
  }

  FocusBlockURLLoader(const FocusBlockURLLoader&) = delete;
  FocusBlockURLLoader& operator=(const FocusBlockURLLoader&) = delete;
  ~FocusBlockURLLoader() override = default;

  // network::mojom::URLLoader:
  void FollowRedirect(
      network::HttpRequestHeadersUpdateParams headers_update_params,
      const std::optional<GURL>& new_url) override {
    if (completed_ || !target_loader_) {
      return;
    }

    if (!pending_redirect_request_) {
      return;
    }
    request_ = std::move(*pending_redirect_request_);
    pending_redirect_request_.reset();
    if (new_url) {
      network::ResourceRequest redirected_request = request_;
      redirected_request.url = *new_url;
      if (redirected_request.trusted_params) {
        redirected_request.trusted_params->isolation_info =
            redirected_request.trusted_params->isolation_info.CreateForRedirect(
                url::Origin::Create(*new_url));
      }
      // Keep the request inspected by Ghostery independent from the request
      // moved into the completion callback. Function argument evaluation order
      // is not guaranteed, so moving and inspecting one instance in the same
      // full-expression could otherwise produce a moved-from match request.
      network::ResourceRequest request_for_match = redirected_request;
      CheckRequest(
          request_for_match,
          base::BindOnce(&FocusBlockURLLoader::OnFollowRedirectDecision,
                         weak_ptr_factory_.GetWeakPtr(),
                         std::move(headers_update_params), new_url,
                         std::move(redirected_request)));
      return;
    }

    target_loader_->FollowRedirect(std::move(headers_update_params), new_url);
  }

  void SetPriority(net::RequestPriority priority,
                   int32_t intra_priority_value) override {
    if (target_loader_) {
      target_loader_->SetPriority(priority, intra_priority_value);
    }
  }

  // network::mojom::URLLoaderClient:
  void OnReceiveEarlyHints(network::mojom::EarlyHintsPtr early_hints) override {
    if (client_) {
      client_->OnReceiveEarlyHints(std::move(early_hints));
    }
  }

  void OnReceiveResponse(
      network::mojom::URLResponseHeadPtr head,
      mojo::ScopedDataPipeConsumerHandle body,
      std::optional<mojo_base::BigBuffer> cached_metadata) override {
    if (client_) {
      client_->OnReceiveResponse(std::move(head), std::move(body),
                                 std::move(cached_metadata));
    }
  }

  void OnReceiveRedirect(const net::RedirectInfo& redirect_info,
                         network::mojom::URLResponseHeadPtr head) override {
    if (completed_) {
      return;
    }

    network::ResourceRequest redirected_request = request_;
    redirected_request.UpdateOnRedirect(redirect_info);
    // Use a separate match copy for the same evaluation-order reason as the
    // caller-supplied redirect path in FollowRedirect().
    network::ResourceRequest request_for_match = redirected_request;
    CheckRequest(
        request_for_match,
        base::BindOnce(&FocusBlockURLLoader::OnReceiveRedirectDecision,
                       weak_ptr_factory_.GetWeakPtr(),
                       std::move(redirected_request), redirect_info,
                       std::move(head)));
  }

  void OnUploadProgress(int64_t current_position,
                        int64_t total_size,
                        base::OnceCallback<void()> callback) override {
    if (client_) {
      client_->OnUploadProgress(current_position, total_size,
                                std::move(callback));
    } else {
      if (callback) {
        std::move(callback).Run();
      }
    }
  }

  void OnTransferSizeUpdated(int32_t transfer_size_diff) override {
    if (client_) {
      client_->OnTransferSizeUpdated(transfer_size_diff);
    }
  }

  void OnComplete(const network::URLLoaderCompletionStatus& status) override {
    if (completed_) {
      return;
    }
    completed_ = true;
    if (client_) {
      client_->OnComplete(status);
    }
  }

 private:
  void CheckRequest(const network::ResourceRequest& request,
                    FocusBlockService::ShouldBlockCallback callback) {
    if (!service_) {
      std::move(callback).Run(false);
      return;
    }
    service_->ShouldBlock(request, top_level_url_, match_source_url_,
                          std::move(callback));
  }

  void OnInitialDecision(bool should_block) {
    if (completed_) {
      return;
    }
    if (should_block) {
      BlockRequest();
      return;
    }
    if (!target_factory_) {
      completed_ = true;
      if (client_) {
        client_->OnComplete(
            network::URLLoaderCompletionStatus(net::ERR_FAILED));
      }
      return;
    }
    mojo::PendingRemote<network::mojom::URLLoaderClient> proxy_client;
    client_receiver_.Bind(proxy_client.InitWithNewPipeAndPassReceiver());
    client_receiver_.set_disconnect_handler(base::BindOnce(
        &FocusBlockURLLoader::OnTargetDisconnected, base::Unretained(this)));
    target_factory_->CreateLoaderAndStart(
        target_loader_.BindNewPipeAndPassReceiver(), request_id_, options_,
        request_, std::move(proxy_client), traffic_annotation_);
  }

  void OnReceiveRedirectDecision(
      network::ResourceRequest redirected_request,
      net::RedirectInfo redirect_info,
      network::mojom::URLResponseHeadPtr head,
      bool should_block) {
    if (completed_) {
      return;
    }
    if (should_block) {
      BlockRequest();
      return;
    }
    pending_redirect_request_ = std::move(redirected_request);
    if (client_) {
      client_->OnReceiveRedirect(redirect_info, std::move(head));
    }
  }

  void OnFollowRedirectDecision(
      network::HttpRequestHeadersUpdateParams headers_update_params,
      std::optional<GURL> new_url,
      network::ResourceRequest redirected_request,
      bool should_block) {
    if (completed_ || !target_loader_) {
      return;
    }
    if (should_block) {
      BlockRequest();
      return;
    }
    request_ = std::move(redirected_request);
    target_loader_->FollowRedirect(std::move(headers_update_params), new_url);
  }

  void BlockRequest() {
    if (completed_) {
      return;
    }
    completed_ = true;
    target_loader_.reset();
    client_receiver_.reset();
    if (client_) {
      client_->OnComplete(
          network::URLLoaderCompletionStatus(net::ERR_BLOCKED_BY_CLIENT));
    }
  }

  void OnTargetDisconnected() {
    if (completed_) {
      return;
    }
    completed_ = true;
    target_loader_.reset();
    client_receiver_.reset();
    if (client_) {
      client_->OnComplete(network::URLLoaderCompletionStatus(net::ERR_FAILED));
    }
  }

  void OnUpstreamClientDisconnected() {
    completed_ = true;
    target_loader_.reset();
    client_receiver_.reset();
  }

  const int32_t request_id_;
  const uint32_t options_;
  network::ResourceRequest request_;
  std::optional<network::ResourceRequest> pending_redirect_request_;
  mojo::Remote<network::mojom::URLLoaderClient> client_;
  mojo::Remote<network::mojom::URLLoaderFactory> target_factory_;
  mojo::Remote<network::mojom::URLLoader> target_loader_;
  mojo::Receiver<network::mojom::URLLoaderClient> client_receiver_{this};
  const net::MutableNetworkTrafficAnnotationTag traffic_annotation_;
  base::WeakPtr<FocusBlockService> service_;
  const GURL top_level_url_;
  const GURL match_source_url_;
  bool completed_ = false;
  base::WeakPtrFactory<FocusBlockURLLoader> weak_ptr_factory_{this};
};

class FocusBlockProxyingURLLoaderFactory final
    : public network::SelfDeletingURLLoaderFactory {
 public:
  FocusBlockProxyingURLLoaderFactory(
      mojo::PendingReceiver<network::mojom::URLLoaderFactory> receiver,
      mojo::PendingRemote<network::mojom::URLLoaderFactory> target_factory,
      base::WeakPtr<FocusBlockService> service,
      GURL factory_top_level_url,
      GURL factory_initiator_url)
      : network::SelfDeletingURLLoaderFactory(std::move(receiver)),
        service_(std::move(service)),
        factory_top_level_url_(std::move(factory_top_level_url)),
        factory_initiator_url_(std::move(factory_initiator_url)) {
    target_factory_.Bind(std::move(target_factory));
    target_factory_.set_disconnect_handler(base::BindOnce(
        &FocusBlockProxyingURLLoaderFactory::OnTargetFactoryDisconnected,
        base::Unretained(this)));
  }

  FocusBlockProxyingURLLoaderFactory(
      const FocusBlockProxyingURLLoaderFactory&) = delete;
  FocusBlockProxyingURLLoaderFactory& operator=(
      const FocusBlockProxyingURLLoaderFactory&) = delete;
  ~FocusBlockProxyingURLLoaderFactory() override = default;

  void CreateLoaderAndStart(
      mojo::PendingReceiver<network::mojom::URLLoader> loader,
      int32_t request_id,
      uint32_t options,
      const network::ResourceRequest& request,
      mojo::PendingRemote<network::mojom::URLLoaderClient> client,
      const net::MutableNetworkTrafficAnnotationTag& traffic_annotation)
      override {
    // Once the profile service is gone there is no policy left to apply. Main
    // document requests are also guaranteed pass-through, including their
    // redirects, so avoid inserting a per-request proxy in both cases.
    if (!service_ || IsOutermostMainDocumentRequest(request)) {
      target_factory_->CreateLoaderAndStart(
          std::move(loader), request_id, options, request, std::move(client),
          traffic_annotation);
      return;
    }

    const GURL top_level_url =
        TopLevelUrlForRequest(factory_top_level_url_, request);
    if (!service_->enabled() || !service_->engine_ready() ||
        (top_level_url.SchemeIsHTTPOrHTTPS() &&
         !service_->IsEnabledForUrl(top_level_url))) {
      target_factory_->CreateLoaderAndStart(
          std::move(loader), request_id, options, request, std::move(client),
          traffic_annotation);
      return;
    }
    const GURL match_source_url =
        MatchSourceUrlForRequest(request, factory_initiator_url_,
                                 top_level_url);
    // The per-request proxy holds the Mojo request while Ghostery makes the
    // initial asynchronous decision. Allowed requests are then started and
    // every redirect target is checked before it reaches the renderer.
    mojo::PendingRemote<network::mojom::URLLoaderFactory> target_factory;
    target_factory_->Clone(
        target_factory.InitWithNewPipeAndPassReceiver());
    mojo::MakeSelfOwnedReceiver(
        std::make_unique<FocusBlockURLLoader>(
            request_id, options, request, std::move(client),
            traffic_annotation, std::move(target_factory), service_,
            top_level_url, match_source_url),
        std::move(loader));
  }

 private:
  void OnTargetFactoryDisconnected() { DisconnectReceiversAndDestroy(); }

  mojo::Remote<network::mojom::URLLoaderFactory> target_factory_;
  base::WeakPtr<FocusBlockService> service_;
  const GURL factory_top_level_url_;
  const GURL factory_initiator_url_;
};

}  // namespace

void MaybeProxyURLLoaderFactory(
    Profile* profile,
    content::RenderFrameHost* frame,
    URLLoaderFactoryType type,
    const url::Origin& request_initiator,
    const net::IsolationInfo& isolation_info,
    network::URLLoaderFactoryBuilder& factory_builder) {
  if (!profile || !IsSupportedFactoryType(type)) {
    return;
  }

  FocusBlockService* service =
      FocusBlockServiceFactory::GetForProfile(profile);
  if (!service) {
    return;
  }

  GURL factory_top_level_url = OriginUrl(isolation_info.top_frame_origin());
  const GURL factory_initiator_url =
      request_initiator.opaque() ? GURL() : request_initiator.GetURL();
  if (frame) {
    content::RenderFrameHost* outermost_main_frame =
        frame->GetOutermostMainFrame();
    if (!factory_top_level_url.SchemeIsHTTPOrHTTPS() &&
        outermost_main_frame &&
        outermost_main_frame->GetLastCommittedURL().SchemeIsHTTPOrHTTPS()) {
      factory_top_level_url = outermost_main_frame->GetLastCommittedURL();
    }
  }

  auto [receiver, target_factory] = factory_builder.Append();
  // SelfDeletingURLLoaderFactory owns itself until all cloned factory
  // receivers disconnect. Individual in-flight loads are separately
  // self-owned and use only weak browser objects.
  new FocusBlockProxyingURLLoaderFactory(
      std::move(receiver), std::move(target_factory), service->GetWeakPtr(),
      std::move(factory_top_level_url), factory_initiator_url);
}

}  // namespace focus_block
