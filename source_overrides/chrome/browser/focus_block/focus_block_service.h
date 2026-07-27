// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#ifndef CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_H_
#define CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_H_

#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "base/functional/callback.h"
#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/observer_list.h"
#include "base/observer_list_types.h"
#include "base/threading/sequence_bound.h"
#include "chrome/browser/focus_block/focus_block_ghostery_engine.h"
#include "components/keyed_service/core/keyed_service.h"
#include "components/prefs/pref_change_registrar.h"
#include "url/gurl.h"

class PrefService;
class Profile;

namespace network {
struct ResourceRequest;
}

namespace focus_block {

// Profile-scoped owner of the native ad/tracker blocking engine. All public
// methods are called on the browser UI sequence. Ghostery parsing and matching
// run on a dedicated single-thread sequence and return asynchronously.
class FocusBlockService : public KeyedService {
 public:
  struct CosmeticResources {
    std::string css;
  };

  using ShouldBlockCallback = base::OnceCallback<void(bool)>;
  using CosmeticResourcesCallback =
      base::OnceCallback<void(std::optional<CosmeticResources>)>;

  class Observer : public base::CheckedObserver {
   public:
    virtual void OnFocusBlockStateChanged() {}
    virtual void OnFocusBlockStatsChanged() {}
    virtual void OnFocusBlockServiceShuttingDown() {}
  };

  explicit FocusBlockService(Profile* profile);
  ~FocusBlockService() override;

  FocusBlockService(const FocusBlockService&) = delete;
  FocusBlockService& operator=(const FocusBlockService&) = delete;

  bool enabled() const;
  void SetEnabled(bool enabled);

  // `enabled` here means that protection is enabled for the site. Setting it
  // to false adds a registrable-domain exception.
  bool IsEnabledForUrl(const GURL& url) const;
  void SetEnabledForUrl(const GURL& url, bool enabled);

  bool engine_ready() const;
  uint64_t blocked_count_session() const { return blocked_count_session_; }
  uint64_t GetBlockedCountForUrl(const GURL& url) const;

  // Asynchronously reports true only for a subresource matched by Ghostery.
  // The outermost main document and non-HTTP(S) schemes are never blocked.
  // `top_level_url` exclusively controls the per-site exception and stats;
  // `source_url` is immutable adblock matching context for a redirect chain.
  void ShouldBlock(const network::ResourceRequest& request,
                   const GURL& top_level_url,
                   const GURL& source_url,
                   ShouldBlockCallback callback);

  // Scriptlets, procedural filters and CSP rewrites are deliberately excluded
  // from the initial native integration. These APIs expose CSS-only rules.
  void GetCosmeticResourcesForUrl(
      const GURL& frame_url,
      const GURL& top_level_url,
      const std::vector<std::string>& classes,
      const std::vector<std::string>& ids,
      CosmeticResourcesCallback callback);

  void AddObserver(Observer* observer);
  void RemoveObserver(Observer* observer);

  base::WeakPtr<FocusBlockService> GetWeakPtr();

  // KeyedService:
  void Shutdown() override;

 private:
  struct PendingMatchRequest {
    GhosteryMatchRequest request;
    GURL top_level_url;
    ShouldBlockCallback callback;
  };

  void StartEngineBuild();
  void OnEngineReady(bool ready);
  void DispatchMatch(GhosteryMatchRequest request,
                     const GURL& top_level_url,
                     ShouldBlockCallback callback);
  void OnMatchCompleted(const GURL& top_level_url,
                        ShouldBlockCallback callback,
                        GhosteryMatchResult result);
  void OnCosmeticsCompleted(const GURL& frame_url,
                            const GURL& top_level_url,
                            CosmeticResourcesCallback callback,
                            GhosteryCosmeticResult result);
  void OnProtectionPrefsChanged();
  void NotifyStateChanged();
  void NotifyStatsChanged();

  std::string SiteKeyForUrl(const GURL& url) const;
  bool IsEligibleUrl(const GURL& url) const;

  raw_ptr<Profile> profile_;
  raw_ptr<PrefService> prefs_;
  PrefChangeRegistrar pref_change_registrar_;
  base::SequenceBound<FocusBlockGhosteryEngine> engine_;
  bool engine_ready_ = false;
  bool engine_initialization_finished_ = false;
  std::vector<PendingMatchRequest> pending_match_requests_;

  uint64_t blocked_count_session_ = 0;
  std::map<std::string, uint64_t> blocked_count_by_site_;
  base::ObserverList<Observer> observers_;
  base::WeakPtrFactory<FocusBlockService> weak_ptr_factory_{this};
};

}  // namespace focus_block

#endif  // CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_SERVICE_H_
