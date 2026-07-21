// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef CHROME_BROWSER_UI_WEBUI_ONBOARDING_ONBOARDING_HANDLER_H_
#define CHROME_BROWSER_UI_WEBUI_ONBOARDING_ONBOARDING_HANDLER_H_

#include <optional>
#include <string>

#include "base/memory/raw_ptr.h"
#include "base/memory/weak_ptr.h"
#include "base/scoped_observation.h"
#include "base/values.h"
#include "chrome/browser/profiles/profile.h"
#include "extensions/browser/webstore_install_result.h"
#include "components/prefs/pref_change_registrar.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_ui_message_handler.h"
#include "extensions/browser/extension_registry_observer.h"

namespace content {
class BrowserContext;
}

class OnboardingMessageHandler : public content::WebUIMessageHandler,
                                 public extensions::ExtensionRegistryObserver {
 public:
  explicit OnboardingMessageHandler(content::BrowserContext* browser_context);
  OnboardingMessageHandler(const OnboardingMessageHandler&) = delete;
  OnboardingMessageHandler& operator=(const OnboardingMessageHandler&) = delete;
  ~OnboardingMessageHandler() override;

  void RegisterMessages() override;

 private:
  base::DictValue GetPreferencesDict();
  void HandleGetPreferences(const base::ListValue& args);
  void HandleAcceptSchema(const base::ListValue& args);
  void HandleApplyFocusSettings(const base::ListValue& args);
  void HandleSetNtpShortcuts(const base::ListValue& args);
  void HandleSetPreference(const base::ListValue& args);
  void HandleGetProfileName(const base::ListValue& args);
  void HandleSetProfileName(const base::ListValue& args);
  void HandleGetExtensions(const base::ListValue& args);
  void HandleInstallExtension(const base::ListValue& args);
  void OnPreferencesChanged();

  void DoInstallExtension(const std::string& callback_id,
                          const std::string& extension_id);
  void DoEnableExtension(const std::string& callback_id,
                         const std::string& extension_id);
  void OnExtensionInstallFinished(std::string callback_id,
                                  bool success,
                                  const std::string& error,
                                  extensions::webstore_install::Result);

  // extensions::ExtensionRegistryObserver:
  void OnExtensionLoaded(content::BrowserContext*,
                         const extensions::Extension*) override;
  void OnExtensionUnloaded(content::BrowserContext*,
                           const extensions::Extension*,
                           extensions::UnloadedExtensionReason) override;

  raw_ptr<content::BrowserContext> browser_context_ = nullptr;
  raw_ptr<Profile> profile_ = nullptr;
  raw_ptr<PrefService> pref_service_ = nullptr;
  PrefChangeRegistrar pref_change_registrar_;

  base::WeakPtrFactory<OnboardingMessageHandler> weak_ptr_factory_{this};

  // extensions:ExtensionRegistryObserver:
  base::ScopedObservation<extensions::ExtensionRegistry,
                          extensions::ExtensionRegistryObserver>
      extension_registry_observation_{this};
};

#endif  // CHROME_BROWSER_UI_WEBUI_ONBOARDING_ONBOARDING_HANDLER_H_
