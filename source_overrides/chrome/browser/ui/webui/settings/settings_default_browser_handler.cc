// Copyright 2015 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/webui/settings/settings_default_browser_handler.h"

#include <string>
#include <utility>

#include "base/functional/bind.h"
#include "base/metrics/histogram_functions.h"
#include "base/metrics/histogram_macros.h"
#include "base/metrics/user_metrics.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/default_browser/default_browser_controller.h"
#include "chrome/browser/default_browser/default_browser_manager.h"
#include "chrome/browser/global_features.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/startup/default_browser_prompt/default_browser_prompt.h"
#include "chrome/browser/ui/startup/default_browser_prompt/default_browser_prompt_manager.h"
#include "chrome/browser/ui/startup/default_browser_prompt/default_browser_prompt_prefs.h"
#include "chrome/common/chrome_features.h"
#include "chrome/common/pref_names.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_ui.h"

#if BUILDFLAG(IS_WIN)
#include "chrome/browser/win/taskbar_manager.h"
#include "chrome/installer/util/install_util.h"
#include "chrome/installer/util/shell_util.h"
#endif

namespace settings {

namespace {

bool DefaultBrowserIsDisabledByPolicy() {
  // Treat an unavailable or malformed policy value as disabled. This handler
  // is exposed by both Settings and Focus onboarding, so a stale WebUI must
  // never be able to turn a policy/state race into a browser-process crash.
  if (!g_browser_process || !g_browser_process->local_state()) {
    return true;
  }

  const PrefService::Preference* pref =
      g_browser_process->local_state()->FindPreference(
          prefs::kDefaultBrowserSettingEnabled);
  if (!pref || !pref->GetValue() || !pref->GetValue()->is_bool()) {
    return true;
  }

  return pref->IsManaged() && !pref->GetValue()->GetBool();
}

#if BUILDFLAG(IS_WIN)
void PinToTaskbarResult(bool result) {
  base::UmaHistogramBoolean("Windows.TaskbarPinFromSettingsSucceeded", result);
}
#endif  // BUILDFLAG(IS_WIN)

}  // namespace

DefaultBrowserHandler::DefaultBrowserHandler() = default;

DefaultBrowserHandler::~DefaultBrowserHandler() = default;

void DefaultBrowserHandler::RegisterMessages() {
  web_ui()->RegisterMessageCallback(
      "requestDefaultBrowserState",
      base::BindRepeating(&DefaultBrowserHandler::RequestDefaultBrowserState,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "requestUserValueStringsFeatureState",
      base::BindRepeating(
          &DefaultBrowserHandler::HandleRequestUserValueStringsFeatureState,
          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "setAsDefaultBrowser",
      base::BindRepeating(&DefaultBrowserHandler::SetAsDefaultBrowser,
                          base::Unretained(this)));
}

void DefaultBrowserHandler::OnJavascriptAllowed() {
  PrefService* local_state =
      g_browser_process ? g_browser_process->local_state() : nullptr;
  if (local_state && local_state->FindPreference(
                         prefs::kDefaultBrowserSettingEnabled)) {
    local_state_pref_registrar_.Init(local_state);
    local_state_pref_registrar_.Add(
        prefs::kDefaultBrowserSettingEnabled,
        base::BindRepeating(
            &DefaultBrowserHandler::OnDefaultBrowserSettingChange,
            base::Unretained(this)));
  }

  default_browser_controller_ =
      default_browser::DefaultBrowserManager::CreateControllerFor(
          default_browser::DefaultBrowserEntrypointType::kSettingsPage);
  if (default_browser_controller_) {
    default_browser_controller_->OnShown();
  }
  did_user_interact_ = false;
}

void DefaultBrowserHandler::OnJavascriptDisallowed() {
  if (!did_user_interact_ && default_browser_controller_) {
    default_browser_controller_->OnIgnored();
  }

  did_user_interact_ = false;
  local_state_pref_registrar_.RemoveAll();
  weak_ptr_factory_.InvalidateWeakPtrs();
  default_browser_controller_.reset();
}

void DefaultBrowserHandler::RequestDefaultBrowserState(
    const base::ListValue& args) {
  if (args.size() != 1u || !args[0].is_string()) {
    return;
  }

  const std::string callback_id = args[0].GetString();
  AllowJavascript();

  auto* manager =
      default_browser::DefaultBrowserManager::From(g_browser_process);
  if (!manager) {
    OnDefaultCheckFinished(callback_id, /*can_pin=*/false,
                           shell_integration::UNKNOWN_DEFAULT);
    return;
  }
  manager->GetDefaultBrowserState(
      base::BindOnce(&DefaultBrowserHandler::OnDefaultBrowserWorkerFinished,
                     weak_ptr_factory_.GetWeakPtr(), callback_id));
}

void DefaultBrowserHandler::HandleRequestUserValueStringsFeatureState(
    const base::ListValue& args) {
  if (args.size() != 1u || !args[0].is_string()) {
    return;
  }

  const std::string callback_id = args[0].GetString();
  AllowJavascript();

  bool is_enabled =
      base::FeatureList::IsEnabled(features::kUserValueDefaultBrowserStrings);
  ResolveJavascriptCallback(callback_id, base::Value(is_enabled));
}
void DefaultBrowserHandler::SetAsDefaultBrowser(const base::ListValue& args) {
  AllowJavascript();

  // The WebUI state is asynchronous and policy can change after the button is
  // rendered. Fail closed and refresh the UI instead of terminating the whole
  // browser with a CHECK.
  if (DefaultBrowserIsDisabledByPolicy() || !default_browser_controller_) {
    OnDefaultCheckFinished(std::nullopt, /*can_pin=*/false,
                           shell_integration::UNKNOWN_DEFAULT);
    return;
  }

  RecordSetAsDefaultUMA();

#if BUILDFLAG(IS_WIN)
  const bool should_pin =
      !args.empty() && args[0].is_bool() && args[0].GetBool();
  if (should_pin) {
    browser_util::PinAppToTaskbar(
        ShellUtil::GetBrowserModelId(InstallUtil::IsPerUserInstall()),
        browser_util::PinAppToTaskbarChannel::kSettingsPage,
        base::BindOnce(&PinToTaskbarResult));
  }
#endif  // BUILDFLAG(IS_WIN)

  did_user_interact_ = true;
  default_browser_controller_->OnAccepted(
      base::BindOnce(&DefaultBrowserHandler::OnDefaultBrowserWorkerFinished,
                     weak_ptr_factory_.GetWeakPtr(), std::nullopt));

  // If the user attempted to make Chrome the default browser, notify
  // them when this changes and close all open prompts.
  if (Profile* profile = Profile::FromWebUI(web_ui())) {
    chrome::startup::default_prompt::UpdatePrefsForDismissedPrompt(profile);
  }
  DefaultBrowserPromptManager::GetInstance()->CloseAllPrompts(
      DefaultBrowserPromptManager::CloseReason::kAccept);
}

void DefaultBrowserHandler::OnDefaultBrowserSettingChange() {
  auto* manager =
      default_browser::DefaultBrowserManager::From(g_browser_process);
  if (!manager) {
    OnDefaultCheckFinished(std::nullopt, /*can_pin=*/false,
                           shell_integration::UNKNOWN_DEFAULT);
    return;
  }
  manager->GetDefaultBrowserState(
      base::BindOnce(&DefaultBrowserHandler::OnDefaultBrowserWorkerFinished,
                     weak_ptr_factory_.GetWeakPtr(), std::nullopt));
}

void DefaultBrowserHandler::RecordSetAsDefaultUMA() {
  base::RecordAction(base::UserMetricsAction("Options_SetAsDefaultBrowser"));
  UMA_HISTOGRAM_COUNTS("Settings.StartSetAsDefault", true);
}

void DefaultBrowserHandler::OnCanPinToTaskbarResult(
    const std::optional<std::string>& js_callback_id,
    shell_integration::DefaultWebClientState state,
    bool can_pin) {
  OnDefaultCheckFinished(js_callback_id, can_pin, state);
}

void DefaultBrowserHandler::OnDefaultBrowserWorkerFinished(
    const std::optional<std::string>& js_callback_id,
    shell_integration::DefaultWebClientState state) {
  if (state == shell_integration::IS_DEFAULT) {
    // Notify the user in the future if Chrome ceases to be the user's chosen
    // default browser.
    if (Profile* profile = Profile::FromWebUI(web_ui())) {
      chrome::startup::default_prompt::ResetPromptPrefs(profile);
    }
  } else {
#if BUILDFLAG(IS_WIN)
    browser_util::ShouldOfferToPin(
        ShellUtil::GetBrowserModelId(InstallUtil::IsPerUserInstall()),
        browser_util::PinAppToTaskbarChannel::kSettingsPage,
        base::BindOnce(&DefaultBrowserHandler::OnCanPinToTaskbarResult,
                       weak_ptr_factory_.GetWeakPtr(), js_callback_id, state));
    return;
#endif  // BUILDFLAG(IS_WIN)
  }
  OnDefaultCheckFinished(js_callback_id, /*can_pin=*/false, state);
}

void DefaultBrowserHandler::OnDefaultCheckFinished(
    const std::optional<std::string>& js_callback_id,
    bool can_pin,
    shell_integration::DefaultWebClientState state) {
  base::DictValue dict;
  dict.Set("isDefault", state == shell_integration::IS_DEFAULT);
  dict.Set("canPin", can_pin);
  dict.Set("canBeDefault", shell_integration::CanSetAsDefaultBrowser());
  dict.Set("isUnknownError", state == shell_integration::UNKNOWN_DEFAULT);
  dict.Set("isDisabledByPolicy", DefaultBrowserIsDisabledByPolicy());

  if (js_callback_id) {
    ResolveJavascriptCallback(base::Value(*js_callback_id), dict);
  } else {
    FireWebUIListener("browser-default-state-changed", dict);
  }
}

}  // namespace settings
