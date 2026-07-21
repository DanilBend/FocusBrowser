// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#include "chrome/browser/focus_block/focus_block_service.h"

#include <algorithm>
#include <array>
#include <utility>

#include "base/functional/bind.h"
#include "base/json/json_reader.h"
#include "base/logging.h"
#include "base/memory/ref_counted_memory.h"
#include "base/task/task_traits.h"
#include "base/task/thread_pool.h"
#include "base/values.h"
#include "chrome/browser/profiles/profile.h"
#include "components/focus_block/rs/src/lib.rs.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_service.h"
#include "components/prefs/scoped_user_pref_update.h"
#include "content/public/browser/browser_thread.h"
#include "net/base/registry_controlled_domains/registry_controlled_domain.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/mojom/fetch_api.mojom.h"
#include "third_party/ublock/resources/grit/ublock_resources.h"
#include "ui/base/resource/resource_bundle.h"
#include "url/origin.h"

namespace focus_block {
namespace {

constexpr size_t kMaxCosmeticNames = 4096;

bool IsOutermostMainDocumentRequest(
    const network::ResourceRequest& request) {
  return request.is_outermost_main_frame &&
         request.destination == network::mojom::RequestDestination::kDocument;
}

std::string RustStringToStdString(const rust::String& value) {
  return std::string(value.data(), value.size());
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

class FocusBlockService::EngineHolder {
 public:
  explicit EngineHolder(rust::Box<focus_block::Engine> engine)
      : engine(std::move(engine)) {}
  ~EngineHolder() = default;

  rust::Box<focus_block::Engine> engine;
};

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
  return engine_ != nullptr;
}

uint64_t FocusBlockService::GetBlockedCountForUrl(const GURL& url) const {
  const auto it = blocked_count_by_site_.find(SiteKeyForUrl(url));
  return it == blocked_count_by_site_.end() ? 0 : it->second;
}

bool FocusBlockService::ShouldBlock(
    const network::ResourceRequest& request,
    const GURL& top_level_url,
    const GURL& source_url) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!engine_ || IsOutermostMainDocumentRequest(request) ||
      !IsEligibleUrl(request.url) || !enabled()) {
    return false;
  }
  // Some worker/service-worker requests have no observable top-frame origin.
  // Keep global protection enabled in that case, but never reinterpret the ad
  // target as the policy site (which would turn an advertiser exception into
  // a cross-site allowlist).
  if (IsEligibleUrl(top_level_url) &&
      !IsEnabledForUrl(top_level_url)) {
    return false;
  }

  const GURL effective_source_url =
      IsEligibleUrl(source_url) ? source_url : top_level_url;

  const bool third_party =
      !IsEligibleUrl(effective_source_url) ||
      !net::registry_controlled_domains::SameDomainOrHost(
          request.url, effective_source_url,
          net::registry_controlled_domains::INCLUDE_PRIVATE_REGISTRIES);
  const std::string request_type = RequestTypeForRequest(request);
  focus_block::BlockerResult result = engine_->engine->matches(
      request.url.spec(), request.url.GetHost(), effective_source_url.GetHost(),
      request_type,
      third_party, request.method, /*previously_matched_rule=*/false,
      /*force_check_exceptions=*/false);
  if (!result.valid_input || !result.matched) {
    return false;
  }

  ++blocked_count_session_;
  const std::string top_level_site_key = SiteKeyForUrl(top_level_url);
  if (!top_level_site_key.empty()) {
    ++blocked_count_by_site_[top_level_site_key];
  }
  NotifyStatsChanged();
  return true;
}

std::optional<FocusBlockService::CosmeticResources>
FocusBlockService::GetCosmeticResourcesForUrl(
    const GURL& frame_url,
    const GURL& top_level_url) const {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!engine_ || !IsEligibleUrl(frame_url) || !enabled() ||
      (IsEligibleUrl(top_level_url) &&
       !IsEnabledForUrl(top_level_url))) {
    return std::nullopt;
  }

  focus_block::StringResult result =
      engine_->engine->url_cosmetic_resources(frame_url.spec());
  if (!result.success) {
    return std::nullopt;
  }
  std::optional<base::DictValue> dict = base::JSONReader::ReadDict(
      RustStringToStdString(result.value), base::JSON_PARSE_RFC);
  if (!dict) {
    return std::nullopt;
  }

  CosmeticResources resources;
  if (const base::ListValue* selectors = dict->FindList("hide_selectors")) {
    for (const base::Value& selector : *selectors) {
      if (selector.is_string()) {
        resources.hide_selectors.push_back(selector.GetString());
      }
    }
  }
  if (const base::ListValue* exceptions = dict->FindList("exceptions")) {
    for (const base::Value& exception : *exceptions) {
      if (exception.is_string()) {
        resources.exceptions.push_back(exception.GetString());
      }
    }
  }
  resources.generichide = dict->FindBool("generichide").value_or(false);
  return resources;
}

std::vector<std::string> FocusBlockService::GetGenericCosmeticSelectors(
    const CosmeticResources& resources,
    const std::vector<std::string>& classes,
    const std::vector<std::string>& ids) const {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  if (!engine_ || resources.generichide) {
    return {};
  }

  std::vector<std::string> bounded_classes(
      classes.begin(),
      classes.begin() + std::min(classes.size(), kMaxCosmeticNames));
  std::vector<std::string> bounded_ids(
      ids.begin(), ids.begin() + std::min(ids.size(), kMaxCosmeticNames));
  focus_block::StringVectorResult result =
      engine_->engine->hidden_class_id_selectors(
          bounded_classes, bounded_ids, resources.exceptions);
  if (!result.success) {
    return {};
  }

  std::vector<std::string> selectors;
  selectors.reserve(result.value.size());
  for (const rust::String& selector : result.value) {
    selectors.push_back(RustStringToStdString(selector));
  }
  return selectors;
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
  engine_.reset();
  prefs_ = nullptr;
  profile_ = nullptr;
}

// static
std::unique_ptr<FocusBlockService::EngineHolder>
FocusBlockService::BuildEngine(
    std::vector<std::vector<uint8_t>> filter_lists) {
  auto filter_set = focus_block::new_filter_set(/*debug=*/false);
  size_t accepted_lists = 0;
  for (const std::vector<uint8_t>& list : filter_lists) {
    if (list.empty()) {
      continue;
    }
    focus_block::AddFilterListResult result =
        filter_set->add_filter_list(list);
    if (result.success) {
      ++accepted_lists;
    } else {
      LOG(ERROR) << "FocusBlock failed to parse bundled list: "
                 << RustStringToStdString(result.error_message);
    }
  }
  if (accepted_lists == 0) {
    LOG(ERROR) << "FocusBlock could not load any bundled filter list";
    return nullptr;
  }
  return std::make_unique<EngineHolder>(
      focus_block::engine_from_filter_set(std::move(filter_set)));
}

void FocusBlockService::StartEngineBuild() {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  constexpr std::array<int, 3> kResourceIds = {
      IDR_UBLOCK_ASSETS_THIRDPARTIES_EASYLIST_EASYLIST_TXT,
      IDR_UBLOCK_ASSETS_THIRDPARTIES_EASYLIST_EASYPRIVACY_TXT,
      IDR_UBLOCK_ASSETS_UBLOCK_FILTERS_MIN_TXT,
  };
  std::vector<std::vector<uint8_t>> filter_lists;
  filter_lists.reserve(kResourceIds.size());
  ui::ResourceBundle& bundle = ui::ResourceBundle::GetSharedInstance();
  for (int resource_id : kResourceIds) {
    scoped_refptr<base::RefCountedMemory> bytes =
        bundle.LoadDataResourceBytes(resource_id);
    if (!bytes || bytes->size() == 0) {
      LOG(ERROR) << "FocusBlock bundled resource is missing: " << resource_id;
      continue;
    }
    filter_lists.emplace_back(bytes->begin(), bytes->end());
  }

  base::ThreadPool::PostTaskAndReplyWithResult(
      FROM_HERE,
      {base::TaskPriority::USER_VISIBLE,
       base::TaskShutdownBehavior::SKIP_ON_SHUTDOWN},
      base::BindOnce(&FocusBlockService::BuildEngine,
                     std::move(filter_lists)),
      base::BindOnce(&FocusBlockService::OnEngineBuilt,
                     weak_ptr_factory_.GetWeakPtr()));
}

void FocusBlockService::OnEngineBuilt(std::unique_ptr<EngineHolder> engine) {
  DCHECK_CURRENTLY_ON(content::BrowserThread::UI);
  engine_ = std::move(engine);
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
