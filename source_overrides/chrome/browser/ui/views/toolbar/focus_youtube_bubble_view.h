// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef CHROME_BROWSER_UI_VIEWS_TOOLBAR_FOCUS_YOUTUBE_BUBBLE_VIEW_H_
#define CHROME_BROWSER_UI_VIEWS_TOOLBAR_FOCUS_YOUTUBE_BUBBLE_VIEW_H_

#include <map>
#include <memory>
#include <string>

#include "base/memory/raw_ptr.h"
#include "base/memory/scoped_refptr.h"
#include "base/memory/weak_ptr.h"
#include "base/values.h"
#include "chrome/browser/ui/views/location_bar/location_bar_bubble_delegate_view.h"

class Browser;

namespace extensions {
class Extension;
}  // namespace extensions

namespace ui {
class Event;
}  // namespace ui

namespace views {
class Label;
class MdTextButton;
class ToggleButton;
class View;
}  // namespace views

// Browser-owned controls for the built-in FocusYoutube component. The
// component remains the content-script engine and storage owner; this class is
// only its compact native UI, so it never appears as a removable extension.
class FocusYoutubeBubbleView : public LocationBarBubbleDelegateView {
  METADATA_HEADER(FocusYoutubeBubbleView, LocationBarBubbleDelegateView)

 public:
  FocusYoutubeBubbleView(const FocusYoutubeBubbleView&) = delete;
  FocusYoutubeBubbleView& operator=(const FocusYoutubeBubbleView&) = delete;
  ~FocusYoutubeBubbleView() override;

  // Toggles the native popup anchored to the contextual YouTube toolbar icon.
  static void ShowBubble(Browser* browser, views::View* anchor_view);

  // LocationBarBubbleDelegateView:
  std::u16string GetAccessibleWindowTitle() const override;
  void Init() override;
  void DidFinishNavigation(
      content::NavigationHandle* navigation_handle) override;

 private:
  FocusYoutubeBubbleView(Browser* browser,
                         views::View* anchor_view,
                         content::WebContents* web_contents);

  std::unique_ptr<views::View> CreateMasterRow();
  std::unique_ptr<views::View> CreateFeatureRow(std::string key,
                                                std::u16string label);
  void LoadSettings();
  void ScheduleSettingsLoadRetry();
  void OnSettingsLoaded(bool success, base::DictValue values);
  void OnMasterTogglePressed(const ui::Event& event);
  void OnFeatureTogglePressed(std::string key, const ui::Event& event);
  void OnSettingWritten(std::string key,
                        bool previous_value,
                        bool requested_value,
                        bool success);
  void OnResetPressed(const ui::Event& event);
  void OnResetStorageCleared(bool success);
  void OnResetWritten(bool success);
  void SetControlsEnabled(bool enabled);
  void RefreshStatus();

  const raw_ptr<Browser> browser_;
  scoped_refptr<const extensions::Extension> extension_;

  raw_ptr<views::Label> master_title_label_ = nullptr;
  raw_ptr<views::ToggleButton> master_toggle_ = nullptr;
  raw_ptr<views::Label> status_label_ = nullptr;
  raw_ptr<views::MdTextButton> reset_button_ = nullptr;
  std::map<std::string, raw_ptr<views::ToggleButton>> feature_toggles_;
  std::map<std::string, bool> feature_state_;

  bool global_enabled_ = true;
  bool loading_ = true;
  bool storage_error_ = false;
  int settings_load_attempts_ = 0;

  base::WeakPtrFactory<FocusYoutubeBubbleView> weak_factory_{this};
};

#endif  // CHROME_BROWSER_UI_VIEWS_TOOLBAR_FOCUS_YOUTUBE_BUBBLE_VIEW_H_
