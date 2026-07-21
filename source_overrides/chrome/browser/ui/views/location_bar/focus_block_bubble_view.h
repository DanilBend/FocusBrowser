// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CHROME_BROWSER_UI_VIEWS_LOCATION_BAR_FOCUS_BLOCK_BUBBLE_VIEW_H_
#define CHROME_BROWSER_UI_VIEWS_LOCATION_BAR_FOCUS_BLOCK_BUBBLE_VIEW_H_

#include <string>

#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/timer/timer.h"
#include "chrome/browser/ui/views/location_bar/location_bar_bubble_delegate_view.h"
#include "url/gurl.h"

class Browser;

namespace focus_block {
class FocusBlockService;
}  // namespace focus_block

namespace ui {
class Event;
}  // namespace ui

namespace views {
class Label;
class ToggleButton;
class View;
}  // namespace views

// Compact browser-native FocusBlock UI anchored to the shield inside the
// location bar. This is intentionally not an extension action or WebUI page.
class FocusBlockBubbleView : public LocationBarBubbleDelegateView {
  METADATA_HEADER(FocusBlockBubbleView, LocationBarBubbleDelegateView)

 public:
  FocusBlockBubbleView(const FocusBlockBubbleView&) = delete;
  FocusBlockBubbleView& operator=(const FocusBlockBubbleView&) = delete;
  ~FocusBlockBubbleView() override;

  // Toggles the FocusBlock bubble anchored to this browser window's shield.
  static void ShowBubble(Browser* browser, views::View* anchor_view);

  // LocationBarBubbleDelegateView:
  std::u16string GetAccessibleWindowTitle() const override;
  void Init() override;
  void WindowClosing() override;

 private:
  FocusBlockBubbleView(Browser* browser,
                       views::View* anchor_view,
                       content::WebContents* web_contents);

  void OnGlobalTogglePressed(const ui::Event& event);
  void OnSiteTogglePressed(const ui::Event& event);
  void RefreshFromService();
  void RefreshCounters();

  bool IsSiteControlAvailable() const;
  std::u16string GetSiteDescription() const;

  // The profile keyed service can shut down while a bubble widget is still
  // draining close tasks. A weak reference makes the timer and callbacks
  // harmless during that teardown window.
  base::WeakPtr<focus_block::FocusBlockService> service_;
  const GURL page_url_;

  raw_ptr<views::Label> engine_status_label_ = nullptr;
  raw_ptr<views::ToggleButton> global_toggle_ = nullptr;
  raw_ptr<views::ToggleButton> site_toggle_ = nullptr;
  raw_ptr<views::Label> page_blocked_value_ = nullptr;
  raw_ptr<views::Label> session_blocked_value_ = nullptr;

  base::RepeatingTimer state_refresh_timer_;
};

#endif  // CHROME_BROWSER_UI_VIEWS_LOCATION_BAR_FOCUS_BLOCK_BUBBLE_VIEW_H_
