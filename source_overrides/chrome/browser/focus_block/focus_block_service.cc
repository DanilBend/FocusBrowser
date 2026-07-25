// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_service.h"

#include <algorithm>
#include <array>
#include <utility>

#include "base/functional/bind.h"
#include "base/logging.h"
#include "base/memory/ref_counted_memory.h"
#include "base/task/task_traits.h"
#include "base/task/thread_pool.h"
#include "base/values.h"
#include "chrome/browser/profiles/profile.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_service.h"
#include "components/prefs/scoped_user_pref_update.h"
#include "content/public/browser/browser_thread.h"
#include "gin/array_buffer.h"
#include "gin/public/isolate_holder.h"
#include "gin/v8_initializer.h"
#include "net/base/registry_controlled_domains/registry_controlled_domain.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/mojom/fetch_api.mojom.h"
#include "third_party/ghostery_adblocker/resources/grit/ghostery_adblocker_resources.h"
#include "third_party/ublock/resources/grit/ublock_resources.h"
#include "tools/v8_context_snapshot/buildflags.h"
#include "ui/base/resource/resource_bundle.h"
#include "url/origin.h"

namespace focus_block {
namespace {

constexpr size_t kMaxCosmeticNames = 4096;
constexpr size_t kMaxCosmeticCssBytes = 1024 * 1024;

bool EnsureGhosteryV8Initialized() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (gin::IsolateHolder::Initialized()) {
    return true;
  }
#if defined(V8_USE_EXTERNAL_STARTUP_DATA)
#if BUILDFLAG(USE_V8_CONTEXT_SNAPSHOT)
  gin::V8Initializer::LoadV8Snapshot(
      gin::V8SnapshotFileType::kWithAdditionalContext);
#else
  gin::V8Initializer::LoadV8Snapshot();
#endif
#endif
  gin::IsolateHolder::Initialize(
      gin::IsolateHolder::kNonStrictMode,
      gin::ArrayBufferAllocator::SharedInstance(), nullptr, "--jitless",
      /*disallow_v8_feature_flag_overrides=*/true);
  return gin::IsolateHolder::Initialized();
}

bool IsOutermostMainDocumentRequest(
    const network::ResourceRequest& request) {
  return request.is_outermost_main_frame &&
         request.destination == network::mojom::RequestDestination::kDocument;
}

std::string RequestTypeForRequest(const network::ResourceRequest& request) {
  using Destination = network::mojom::RequestDestination;
  if (request.is_fetch_like_api) {
    return "xhr";
  }
  Destination destination = request.destination;
  if (destination == Destination::kEmpty &&
      request.original_destination != Destination::kEmpty) {
    destination = request.original_destination;
  }
  switch (destination) {
    case Destination::kDocument:
      return "document";
    case Destination::kFrame:
    case Destination::kIframe:
    case Destination::kFencedframe:
      return "subdocument";
    case Destination::kScript:
    case Destination::kServiceWorker:
    case Destination::kSharedWorker:
    case Destination::kWorker:
    case Destination::kSharedStorageWorklet:
      return "script";
    case Destination::kJson:
      return "xhr";
    case Destination::kImage:
      return "image";
    case Destination::kStyle:
    case Destination::kXslt:
      return "stylesheet";
    case Destination::kFont:
      return "font";
    case Destination::kAudio:
    case Destination::kTrack:
    case Destination::kVideo:
      return "media";
    case Destination::kEmbed:
    case Destination::kObject:
      return "object";
    case Destination::kReport:
      return "csp_report";
    case Destination::kEmpty:
      return request.keepalive ? "ping" : "other";
    case Destination::kAudioWorklet:
    case Destination::kManifest:
    case Destination::kPaintWorklet:
    case Destination::kWebBundle:
    case Destination::kWebIdentity:
    case Destination::kDictionary:
    case Destination::kSpeculationRules:
    case Destination::kEmailVerification:
      return "other";
  }
}

}  // namespace

FocusBlockService::FocusBlockService(Profile* profile)
    : profile_(profile), prefs_(profile->GetPrefs()) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  pref_change_registrar_.Init(prefs_);
  pref_change_registrar_.Add(
      prefs::kFocusBlockEnabled,
      base::BindRepeating(&FocusBlockService::OnProtectionPrefsChanged,
                          base::Unretained(this)));
  pref_change_registrar_.Add(
      prefs::kFocusBlockDisabledSites,
      base::BindRepeating(&FocusBlockService::OnProtectionPrefsChanged,
                          base::Unretained(this)));
  StartEngineBuild();
}

FocusBlockService::~FocusBlockService() = default;

bool FocusBlockService::enabled() const {
  return prefs_ && prefs_->GetBoolean(prefs::kFocusBlockEnabled);
}

void FocusBlockService::SetEnabled(bool enabled_value) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (prefs_ && enabled() != enabled_value) {
    prefs_->SetBoolean(prefs::kFocusBlockEnabled, enabled_value);
  }
}

bool FocusBlockService::IsEligibleUrl(const GURL& url) const {
  return url.is_valid() && url.SchemeIsHTTPOrHTTPS();
}

std::string FocusBlockService::SiteKeyForUrl(const GURL& url) const {
  if (!IsEligibleUrl(url)) {
    return std::string();
  }
  std::string registrable_domain =
      net::registry_controlled_domains::GetDomainAndRegistry(
          url,
          net::registry_controlled_domains::INCLUDE_PRIVATE_REGISTRIES);
  return registrable_domain.empty() ? url.GetHost() : registrable_domain;
}

bool FocusBlockService::IsEnabledForUrl(const GURL& url) const {
  if (!enabled() || !IsEligibleUrl(url)) {
    return false;
  }
  const std::string site_key = SiteKeyForUrl(url);
  for (const base::Value& value :
       prefs_->GetList(prefs::kFocusBlockDisabledSites)) {
    if (value.is_string() && value.GetString() == site_key) {
      return false;
    }
  }
  return true;
}

void FocusBlockService::SetEnabledForUrl(const GURL& url,
                                         bool enabled_value) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!prefs_) {
    return;
  }
  const std::string site_key = SiteKeyForUrl(url);
  if (site_key.empty()) {
    return;
  }

  bool currently_disabled = false;
  for (const base::Value& value :
       prefs_->GetList(prefs::kFocusBlockDisabledSites)) {
    if (value.is_string() && value.GetString() == site_key) {
      currently_disabled = true;
      break;
    }
  }
  if (currently_disabled == !enabled_value) {
    return;
  }

  ScopedListPrefUpdate update(prefs_, prefs::kFocusBlockDisabledSites);
  base::ListValue& disabled_sites = update.Get();
  if (enabled_value) {
    disabled_sites.EraseValue(base::Value(site_key));
  } else {
    disabled_sites.Append(site_key);
  }
}

bool FocusBlockService::engine_ready() const {
  return engine_ready_;
}

uint64_t FocusBlockService::GetBlockedCountForUrl(const GURL& url) const {
  const auto it = blocked_count_by_site_.find(SiteKeyForUrl(url));
  return it == blocked_count_by_site_.end() ? 0 : it->second;
}

void FocusBlockService::ShouldBlock(
    const network::ResourceRequest& request,
    const GURL& top_level_url,
    const GURL& source_url,
    ShouldBlockCallback callback) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!engine_ready_ || IsOutermostMainDocumentRequest(request) ||
      !IsEligibleUrl(request.url) || !enabled()) {
    std::move(callback).Run(false);
    return;
  }
  // Some worker/service-worker requests have no observable top-frame origin.
  // Keep global protection enabled in that case, but never reinterpret the ad
  // target as the policy site (which would turn an advertiser exception into
  // a cross-site allowlist).
  if (IsEligibleUrl(top_level_url) &&
      !IsEnabledForUrl(top_level_url)) {
    std::move(callback).Run(false);
    return;
  }

  const GURL effective_source_url =
      IsEligibleUrl(source_url) ? source_url : top_level_url;
  GhosteryMatchRequest match_request;
  match_request.url = request.url.spec();
  match_request.source_url = effective_source_url.spec();
  match_request.type = RequestTypeForRequest(request);
  engine_.AsyncCall(&FocusBlockGhosteryEngine::Match)
      .WithArgs(std::move(match_request))
      .Then(base::BindOnce(
          [](base::WeakPtr<FocusBlockService> service,
             GURL callback_top_level_url,
             ShouldBlockCallback callback, GhosteryMatchResult result) {
            if (!service) {
              std::move(callback).Run(false);
              return;
            }
            service->OnMatchCompleted(callback_top_level_url,
                                      std::move(callback), std::move(result));
          },
          weak_ptr_factory_.GetWeakPtr(), top_level_url,
          std::move(callback)));
}

void FocusBlockService::OnMatchCompleted(const GURL& top_level_url,
                                         ShouldBlockCallback callback,
                                         GhosteryMatchResult result) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  const bool protection_still_enabled =
      prefs_ && enabled() && engine_ready_ &&
      (!IsEligibleUrl(top_level_url) || IsEnabledForUrl(top_level_url));
  const bool should_block = protection_still_enabled && result.valid_input &&
                            result.matched;
  if (!result.valid_input && !result.error.empty()) {
    DVLOG(1) << "FocusBlock Ghostery match failed: " << result.error;
  }
  if (should_block) {
    ++blocked_count_session_;
    const std::string top_level_site_key = SiteKeyForUrl(top_level_url);
    if (!top_level_site_key.empty()) {
      ++blocked_count_by_site_[top_level_site_key];
    }
    NotifyStatsChanged();
  }
  std::move(callback).Run(should_block);
}

void FocusBlockService::GetCosmeticResourcesForUrl(
    const GURL& frame_url,
    const GURL& top_level_url,
    const std::vector<std::string>& classes,
    const std::vector<std::string>& ids,
    CosmeticResourcesCallback callback) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!engine_ready_ || !IsEligibleUrl(frame_url) || !enabled() ||
      (IsEligibleUrl(top_level_url) &&
       !IsEnabledForUrl(top_level_url))) {
    std::move(callback).Run(std::nullopt);
    return;
  }

  GhosteryCosmeticRequest cosmetic_request;
  cosmetic_request.url = frame_url.spec();
  cosmetic_request.classes.assign(
      classes.begin(),
      classes.begin() + std::min(classes.size(), kMaxCosmeticNames));
  cosmetic_request.ids.assign(
      ids.begin(), ids.begin() + std::min(ids.size(), kMaxCosmeticNames));
  engine_.AsyncCall(&FocusBlockGhosteryEngine::GetCosmetics)
      .WithArgs(std::move(cosmetic_request))
      .Then(base::BindOnce(
          [](base::WeakPtr<FocusBlockService> service,
             GURL callback_frame_url, GURL callback_top_level_url,
             CosmeticResourcesCallback callback,
             GhosteryCosmeticResult result) {
            if (!service) {
              std::move(callback).Run(std::nullopt);
              return;
            }
            service->OnCosmeticsCompleted(
                callback_frame_url, callback_top_level_url,
                std::move(callback), std::move(result));
          },
          weak_ptr_factory_.GetWeakPtr(), frame_url, top_level_url,
          std::move(callback)));
}

void FocusBlockService::OnCosmeticsCompleted(
    const GURL& frame_url,
    const GURL& top_level_url,
    CosmeticResourcesCallback callback,
    GhosteryCosmeticResult result) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!prefs_ || !engine_ready_ || !IsEligibleUrl(frame_url) || !enabled() ||
      (IsEligibleUrl(top_level_url) &&
       !IsEnabledForUrl(top_level_url)) ||
      !result.valid_input || !result.active ||
      result.styles.size() > kMaxCosmeticCssBytes) {
    if (!result.valid_input && !result.error.empty()) {
      DVLOG(1) << "FocusBlock Ghostery cosmetics failed: " << result.error;
    }
    std::move(callback).Run(std::nullopt);
    return;
  }
  CosmeticResources resources;
  resources.css = std::move(result.styles);
  std::move(callback).Run(std::move(resources));
}

void FocusBlockService::AddObserver(Observer* observer) {
  observers_.AddObserver(observer);
}

void FocusBlockService::RemoveObserver(Observer* observer) {
  observers_.RemoveObserver(observer);
}

base::WeakPtr<FocusBlockService> FocusBlockService::GetWeakPtr() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  return weak_ptr_factory_.GetWeakPtr();
}

void FocusBlockService::Shutdown() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  weak_ptr_factory_.InvalidateWeakPtrs();
  pref_change_registrar_.RemoveAll();
  for (Observer& observer : observers_) {
    observer.OnFocusBlockServiceShuttingDown();
  }
  observers_.Clear();
  engine_ready_ = false;
  engine_.Reset();
  prefs_ = nullptr;
  profile_ = nullptr;
}

void FocusBlockService::StartEngineBuild() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  // The normal multi-process browser executable does not otherwise create a
  // V8 isolate. Initialize Gin exactly once on the serialized UI sequence,
  // before the dedicated engine sequence constructs its private isolate.
  if (!EnsureGhosteryV8Initialized()) {
    LOG(ERROR) << "FocusBlock could not initialize Gin for Ghostery";
    return;
  }
  constexpr std::array<int, 3> kResourceIds = {
      IDR_UBLOCK_ASSETS_THIRDPARTIES_EASYLIST_EASYLIST_TXT,
      IDR_UBLOCK_ASSETS_THIRDPARTIES_EASYLIST_EASYPRIVACY_TXT,
      IDR_UBLOCK_ASSETS_UBLOCK_FILTERS_MIN_TXT,
  };
  std::string filter_text;
  ui::ResourceBundle& bundle = ui::ResourceBundle::GetSharedInstance();
  for (int resource_id : kResourceIds) {
    scoped_refptr<base::RefCountedMemory> bytes =
        bundle.LoadDataResourceBytes(resource_id);
    if (!bytes || bytes->size() == 0) {
      LOG(ERROR) << "FocusBlock bundled resource is missing: " << resource_id;
      continue;
    }
    filter_text.append(reinterpret_cast<const char*>(bytes->data()),
                       bytes->size());
    filter_text.push_back('\n');
  }
  scoped_refptr<base::RefCountedMemory> engine_bundle =
      bundle.LoadDataResourceBytes(IDR_FOCUS_GHOSTERY_ADBLOCKER_BUNDLE_JS);
  if (filter_text.empty() || !engine_bundle || engine_bundle->size() == 0) {
    LOG(ERROR) << "FocusBlock could not load the bundled Ghostery engine";
    return;
  }
  std::string bundle_source(
      reinterpret_cast<const char*>(engine_bundle->data()),
      engine_bundle->size());

  engine_.emplace(
      base::ThreadPool::CreateSingleThreadTaskRunner(
          {base::TaskPriority::USER_BLOCKING,
           base::TaskShutdownBehavior::SKIP_ON_SHUTDOWN}),
      std::move(bundle_source), std::move(filter_text));
  engine_.AsyncCall(&FocusBlockGhosteryEngine::IsReady)
      .Then(base::BindOnce(&FocusBlockService::OnEngineReady,
                           weak_ptr_factory_.GetWeakPtr()));
}

void FocusBlockService::OnEngineReady(bool ready) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  engine_ready_ = ready;
  if (!ready) {
    LOG(ERROR) << "FocusBlock failed to initialize Ghostery 2.18.1";
  }
  NotifyStateChanged();
}

void FocusBlockService::OnProtectionPrefsChanged() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  NotifyStateChanged();
}

void FocusBlockService::NotifyStateChanged() {
  for (Observer& observer : observers_) {
    observer.OnFocusBlockStateChanged();
  }
}

void FocusBlockService::NotifyStatsChanged() {
  for (Observer& observer : observers_) {
    observer.OnFocusBlockStatsChanged();
  }
}

}  // namespace focus_block
