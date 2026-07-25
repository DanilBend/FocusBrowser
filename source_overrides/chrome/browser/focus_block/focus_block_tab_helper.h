// Copyright 2026 Focus Browser contributors
// Use of this source code is governed by the license in the project root.

#ifndef CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_TAB_HELPER_H_
#define CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_TAB_HELPER_H_

#include <string>
#include <vector>

#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/values.h"
#include "chrome/browser/focus_block/focus_block_service.h"
#include "content/public/browser/weak_document_ptr.h"
#include "content/public/browser/web_contents_observer.h"
#include "content/public/browser/web_contents_user_data.h"

namespace content {
class Page;
class RenderFrameHost;
class WebContents;
}

namespace focus_block {

// Injects the CSS-only subset of FocusBlock cosmetic rules in an isolated
// world. It never executes filter-list scriptlets or changes page CSP.
class FocusBlockTabHelper
    : public content::WebContentsObserver,
      public content::WebContentsUserData<FocusBlockTabHelper>,
      public FocusBlockService::Observer {
 public:
  FocusBlockTabHelper(const FocusBlockTabHelper&) = delete;
  FocusBlockTabHelper& operator=(const FocusBlockTabHelper&) = delete;
  ~FocusBlockTabHelper() override;

 private:
  friend class content::WebContentsUserData<FocusBlockTabHelper>;

  explicit FocusBlockTabHelper(content::WebContents* web_contents);

  // content::WebContentsObserver:
  void DOMContentLoaded(content::RenderFrameHost* render_frame_host) override;
  void PrimaryPageChanged(content::Page& page) override;

  // FocusBlockService::Observer:
  void OnFocusBlockStateChanged() override;
  void OnFocusBlockServiceShuttingDown() override;

  void ApplyToFrame(content::RenderFrameHost* render_frame_host);
  void ClearFrame(content::RenderFrameHost* render_frame_host);
  void OnInitialCosmetics(
      content::WeakDocumentPtr document,
      std::optional<FocusBlockService::CosmeticResources> resources);
  void OnElementNamesCollected(
      content::WeakDocumentPtr document,
      base::Value result);
  void OnFinalCosmetics(
      content::WeakDocumentPtr document,
      std::optional<FocusBlockService::CosmeticResources> resources);

  std::u16string BuildApplyCssScript(const std::string& css,
                                     bool collect_element_names) const;

  raw_ptr<FocusBlockService> service_ = nullptr;
  base::WeakPtrFactory<FocusBlockTabHelper> weak_ptr_factory_{this};

  WEB_CONTENTS_USER_DATA_KEY_DECL();
};

}  // namespace focus_block

#endif  // CHROME_BROWSER_FOCUS_BLOCK_FOCUS_BLOCK_TAB_HELPER_H_
