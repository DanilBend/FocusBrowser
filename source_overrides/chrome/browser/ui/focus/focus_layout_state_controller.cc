// Copyright 2026 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "chrome/browser/ui/focus/focus_layout_state_controller.h"

#include "chrome/browser/ui/browser_window/public/browser_window_interface.h"
#include "chrome/browser/ui/tabs/features.h"
#include "chrome/common/pref_names.h"
#include "components/prefs/pref_service.h"

DEFINE_USER_DATA(FocusLayoutStateController);

FocusLayoutStateController::FocusLayoutStateController(
    BrowserWindowInterface* browser_window,
    PrefService* pref_service)
    : pref_service_(pref_service),
      scoped_unowned_user_data_(browser_window->GetUnownedUserDataHost(),
                                *this) {
  pref_change_registrar_.Init(pref_service_);

  pref_change_registrar_.AddMultiple(
      {prefs::kFocusLayout, prefs::kFocusZenMode},
      base::BindRepeating(&FocusLayoutStateController::NotifyStateChanged,
                          base::Unretained(this)));
}

FocusLayoutStateController::~FocusLayoutStateController() = default;

// static
FocusLayoutStateController* FocusLayoutStateController::From(
    BrowserWindowInterface* browser_window) {
  return Get(browser_window->GetUnownedUserDataHost());
}

bool FocusLayoutStateController::IsClassicLayout() const {
  return GetBrowserLayout() == FocusLayoutType::kClassic;
}

bool FocusLayoutStateController::IsCompactLayout() const {
  return GetBrowserLayout() == FocusLayoutType::kCompact;
}

bool FocusLayoutStateController::IsVerticalLayout() const {
  return GetBrowserLayout() == FocusLayoutType::kVertical;
}

bool FocusLayoutStateController::IsDynamicLayout() const {
  return GetBrowserLayout() == FocusLayoutType::kDynamic;
}

bool FocusLayoutStateController::IsZenModeEnabled() const {
  return pref_service_->GetBoolean(prefs::kFocusZenMode);
}

void FocusLayoutStateController::SetBrowserLayout(FocusLayoutType layout) {
  pref_service_->SetInteger(prefs::kFocusLayout, std::to_underlying(layout));
}

FocusLayoutType FocusLayoutStateController::GetBrowserLayout() const {
  int layout_value = pref_service_->GetInteger(prefs::kFocusLayout);

  if (!tabs::IsVerticalTabsFeatureEnabled() &&
      layout_value == std::to_underlying(FocusLayoutType::kVertical)) {
    return FocusLayoutType::kClassic;
  }

  if (layout_value < std::to_underlying(FocusLayoutType::kClassic) ||
      layout_value > std::to_underlying(FocusLayoutType::kMaxValue)) {
    return FocusLayoutType::kClassic;
  }

  return static_cast<FocusLayoutType>(layout_value);
}

base::CallbackListSubscription
FocusLayoutStateController::RegisterOnStateChanged(
    StateChangedCallback callback) {
  return on_state_changed_callback_list_.Add(std::move(callback));
}

void FocusLayoutStateController::NotifyStateChanged() {
  on_state_changed_callback_list_.Notify(this);
}
