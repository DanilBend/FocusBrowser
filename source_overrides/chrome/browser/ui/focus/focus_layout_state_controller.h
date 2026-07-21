// Copyright 2026 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef CHROME_BROWSER_UI_FOCUS_FOCUS_LAYOUT_STATE_CONTROLLER_H_
#define CHROME_BROWSER_UI_FOCUS_FOCUS_LAYOUT_STATE_CONTROLLER_H_

#include "base/callback_list.h"
#include "base/memory/raw_ptr.h"
#include "components/prefs/pref_change_registrar.h"
#include "ui/base/unowned_user_data/scoped_unowned_user_data.h"

class BrowserWindowInterface;
class PrefService;

enum class FocusLayoutType {
  kClassic = 0,
  kCompact = 1,
  kVertical = 2,
  kDynamic = 3,
  kMaxValue = kDynamic,
};

class FocusLayoutStateController {
 public:
  DECLARE_USER_DATA(FocusLayoutStateController);

  explicit FocusLayoutStateController(BrowserWindowInterface* browser_window,
                                       PrefService* pref_service);
  FocusLayoutStateController(const FocusLayoutStateController&) = delete;
  FocusLayoutStateController& operator=(const FocusLayoutStateController&) =
      delete;
  ~FocusLayoutStateController();

  static FocusLayoutStateController* From(
      BrowserWindowInterface* browser_window);

  bool IsClassicLayout() const;
  bool IsCompactLayout() const;
  bool IsVerticalLayout() const;
  bool IsDynamicLayout() const;
  bool IsZenModeEnabled() const;

  void SetBrowserLayout(FocusLayoutType layout);
  FocusLayoutType GetBrowserLayout() const;

  using StateChangedCallback =
      base::RepeatingCallback<void(FocusLayoutStateController*)>;
  base::CallbackListSubscription RegisterOnStateChanged(
      StateChangedCallback callback);

 private:
  void NotifyStateChanged();

  const raw_ptr<PrefService> pref_service_;
  PrefChangeRegistrar pref_change_registrar_;
  base::RepeatingCallbackList<void(FocusLayoutStateController*)>
      on_state_changed_callback_list_;
  ui::ScopedUnownedUserData<FocusLayoutStateController>
      scoped_unowned_user_data_;
};

#endif  // CHROME_BROWSER_UI_FOCUS_FOCUS_LAYOUT_STATE_CONTROLLER_H_
