// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "chrome/browser/ui/webui/onboarding/onboarding_handler.h"

#include <array>
#include <set>
#include <string_view>
#include <utility>
#include <vector>

#include "base/functional/bind.h"
#include "base/strings/string_util.h"
#include "base/strings/utf_string_conversions.h"
#include "base/values.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/content_settings/host_content_settings_map_factory.h"
#include "chrome/browser/extensions/webstore_install_with_prompt.h"
#include "chrome/browser/new_tab_page/ntp_pref_names.h"
#include "chrome/browser/ntp_tiles/chrome_custom_links_manager_factory.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/profiles/profile_attributes_storage.h"
#include "chrome/browser/profiles/profile_manager.h"
#include "chrome/browser/profiles/profiles_state.h"
#include "chrome/browser/ui/focus/focus_layout_state_controller.h"
#include "chrome/common/pref_names.h"
#include "components/bookmarks/common/bookmark_pref_names.h"
#include "components/content_settings/core/browser/host_content_settings_map.h"
#include "components/content_settings/core/common/content_settings.h"
#include "components/content_settings/core/common/content_settings_types.h"
#include "components/ntp_tiles/custom_links_manager.h"
#include "components/ntp_tiles/ntp_tile.h"
#include "components/focus_services/pref_names.h"
#include "components/focus_services/schema.h"
#include "components/prefs/pref_change_registrar.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_contents.h"
#include "content/public/browser/web_ui.h"
#include "extensions/browser/extension_registrar.h"
#include "extensions/browser/extension_registry.h"
#include "extensions/browser/webstore_install_result.h"
#include "extensions/common/extension.h"

namespace {

struct OnboardingShortcut {
  std::string_view id;
  std::string_view url;
  std::u16string_view title;
};

// This allowlist is deliberately kept on the browser side. The onboarding UI
// only sends stable IDs, so a compromised WebUI cannot write arbitrary URLs to
// the profile's New Tab Page shortcuts.
constexpr std::array<OnboardingShortcut, 16> kOnboardingShortcuts = {{
    {"youtube", "https://www.youtube.com/", u"YouTube"},
    {"tiktok", "https://www.tiktok.com/", u"TikTok"},
    {"instagram", "https://www.instagram.com/", u"Instagram"},
    {"telegram", "https://web.telegram.org/", u"Telegram"},
    {"codex", "https://chatgpt.com/codex/", u"Codex"},
    {"claude", "https://claude.ai/", u"Claude"},
    {"github", "https://github.com/", u"GitHub"},
    {"stackoverflow", "https://stackoverflow.com/", u"Stack Overflow"},
    {"chatgpt", "https://chatgpt.com/", u"ChatGPT"},
    {"gemini", "https://gemini.google.com/app", u"Gemini"},
    {"perplexity", "https://www.perplexity.ai/", u"Perplexity"},
    {"copilot", "https://copilot.microsoft.com/", u"Copilot"},
    {"gmail", "https://mail.google.com/", u"Gmail"},
    {"drive", "https://drive.google.com/", u"Google Drive"},
    {"notion", "https://www.notion.so/", u"Notion"},
    {"calendar", "https://calendar.google.com/", u"Google Calendar"},
}};

constexpr size_t kMaxOnboardingShortcuts = 10;

const OnboardingShortcut* FindOnboardingShortcut(std::string_view id) {
  for (const auto& shortcut : kOnboardingShortcuts) {
    if (shortcut.id == id) {
      return &shortcut;
    }
  }
  return nullptr;
}

}  // namespace

OnboardingMessageHandler::OnboardingMessageHandler(
    content::BrowserContext* browser_context)
    : browser_context_(browser_context),
      profile_(Profile::FromBrowserContext(browser_context)),
      pref_service_(profile_->GetPrefs()) {
  pref_change_registrar_.Init(pref_service_);

  for (const auto [key, _] : GetPreferencesDict()) {
    pref_change_registrar_.Add(
        prefs::kFocusPrefPrefix + key,
        base::BindRepeating(&OnboardingMessageHandler::OnPreferencesChanged,
                            base::Unretained(this)));
  }
}

OnboardingMessageHandler::~OnboardingMessageHandler() = default;

base::DictValue OnboardingMessageHandler::GetPreferencesDict() {
  std::vector<PrefService::PreferenceValueAndStore> values =
      pref_service_->GetPreferencesValueAndStore();
  base::DictValue output;

  for (const auto& [name, value, _] : values) {
    if (!base::StartsWith(name, prefs::kFocusPrefPrefix,
                          base::CompareCase::SENSITIVE)) {
      continue;
    }

    output.Set(name.substr(sizeof(prefs::kFocusPrefPrefix) - 1),
               value.Clone());
  }

  return output;
}

void OnboardingMessageHandler::OnPreferencesChanged() {
  AllowJavascript();
  FireWebUIListener("focus-prefs-changed", GetPreferencesDict());
}

void OnboardingMessageHandler::OnExtensionLoaded(
    content::BrowserContext*,
    const extensions::Extension* extension) {
  AllowJavascript();
  FireWebUIListener("extension-state-changed",
                    base::DictValue().Set(extension->id(), true));
}

void OnboardingMessageHandler::OnExtensionUnloaded(
    content::BrowserContext*,
    const extensions::Extension* extension,
    extensions::UnloadedExtensionReason reason) {
  using extensions::UnloadedExtensionReason;
  if (reason != UnloadedExtensionReason::DISABLE &&
      reason != UnloadedExtensionReason::UNINSTALL) {
    return;
  }

  AllowJavascript();
  FireWebUIListener("extension-state-changed",
                    base::DictValue().Set(extension->id(), false));
}

void OnboardingMessageHandler::RegisterMessages() {
  web_ui()->RegisterMessageCallback(
      "getPrefs",
      base::BindRepeating(&OnboardingMessageHandler::HandleGetPreferences,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "setPref",
      base::BindRepeating(&OnboardingMessageHandler::HandleSetPreference,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "acceptLatestSchema",
      base::BindRepeating(&OnboardingMessageHandler::HandleAcceptSchema,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "applyFocusSettings",
      base::BindRepeating(&OnboardingMessageHandler::HandleApplyFocusSettings,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "setNtpShortcuts",
      base::BindRepeating(&OnboardingMessageHandler::HandleSetNtpShortcuts,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "getProfileName",
      base::BindRepeating(&OnboardingMessageHandler::HandleGetProfileName,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "setProfileName",
      base::BindRepeating(&OnboardingMessageHandler::HandleSetProfileName,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "getExtensions",
      base::BindRepeating(&OnboardingMessageHandler::HandleGetExtensions,
                          base::Unretained(this)));
  web_ui()->RegisterMessageCallback(
      "installExtension",
      base::BindRepeating(&OnboardingMessageHandler::HandleInstallExtension,
                          base::Unretained(this)));
}

void OnboardingMessageHandler::HandleGetExtensions(
    const base::ListValue& args) {
  CHECK_EQ(1U, args.size());
  const auto& callback_id = args[0].GetString();
  auto* registry = extensions::ExtensionRegistry::Get(browser_context_);

  base::DictValue filtered_extension_data;
  for (const auto& extension : registry->enabled_extensions()) {
    filtered_extension_data.Set(extension->id(), true);
  }

  for (const auto& extension : registry->disabled_extensions()) {
    filtered_extension_data.Set(extension->id(), false);
  }

  // If no one asked for a list of extensions yet, they probably don't
  // care about extension changes, so we defer the observer init to here
  // to avoid sending useless events.
  if (!extension_registry_observation_.IsObserving()) {
    extension_registry_observation_.Observe(registry);
  }

  AllowJavascript();
  ResolveJavascriptCallback(base::Value(std::move(callback_id)),
                            std::move(filtered_extension_data));
}

void OnboardingMessageHandler::HandleInstallExtension(
    const base::ListValue& args) {
  CHECK_EQ(2U, args.size());
  const auto& callback_id = args[0].GetString();
  const auto& extension_id = args[1].GetString();

  // If the extension is already installed, but disabled, enable it.
  const auto* registry = extensions::ExtensionRegistry::Get(browser_context_);
  if (const auto* extension = registry->GetInstalledExtension(extension_id)) {
    DoEnableExtension(callback_id, extension->id());
    return;
  }

  // Otherwise, do the whole dance of installing an extension.
  DoInstallExtension(callback_id, extension_id);
}

void OnboardingMessageHandler::DoEnableExtension(
    const std::string& callback_id,
    const std::string& extension_id) {
  extensions::ExtensionRegistrar::Get(browser_context_)
      ->EnableExtension(extension_id);
  AllowJavascript();
  ResolveJavascriptCallback(base::Value(callback_id), base::Value());
}

void OnboardingMessageHandler::DoInstallExtension(
    const std::string& callback_id,
    const std::string& extension_id) {
  gfx::NativeWindow window =
      web_ui()->GetWebContents()->GetTopLevelNativeWindow();

  // Pass a weak_ptr for the callback, as WebstoreInstallWithPrompt is
  // ref-counted and might outlive OnboardingMessageHandler.
  auto installer = base::MakeRefCounted<extensions::WebstoreInstallWithPrompt>(
      extension_id, profile_, window,
      base::BindOnce(&OnboardingMessageHandler::OnExtensionInstallFinished,
                     weak_ptr_factory_.GetWeakPtr(), callback_id));

  installer->BeginInstall();
}

void OnboardingMessageHandler::OnExtensionInstallFinished(
    std::string callback_id,
    bool success,
    const std::string& error,
    extensions::webstore_install::Result result) {
  AllowJavascript();
  if (success) {
    ResolveJavascriptCallback(base::Value(callback_id), base::Value());
    return;
  }

  RejectJavascriptCallback(
      base::Value(callback_id),
      base::DictValue().Set("error", error).Set("code", result));
}

void OnboardingMessageHandler::HandleGetPreferences(
    const base::ListValue& args) {
  CHECK_EQ(1U, args.size());
  const auto& callback_id = args[0].GetString();

  AllowJavascript();
  ResolveJavascriptCallback(base::Value(callback_id),
                            base::Value(GetPreferencesDict()));
}

void OnboardingMessageHandler::HandleGetProfileName(
    const base::ListValue& args) {
  CHECK_EQ(1U, args.size());
  const auto& callback_id = args[0].GetString();

  ProfileAttributesEntry* entry =
      g_browser_process->profile_manager()
          ->GetProfileAttributesStorage()
          .GetProfileAttributesWithPath(profile_->GetPath());

  if (!entry) {
    return RejectJavascriptCallback(callback_id, "entry not found");
  }

  AllowJavascript();
  ResolveJavascriptCallback(base::Value(callback_id),
                            base::Value(entry->GetLocalProfileName()));
}

void OnboardingMessageHandler::HandleSetProfileName(
    const base::ListValue& args) {
  CHECK_EQ(1U, args.size());
  std::u16string new_name = base::UTF8ToUTF16(args[0].GetString());
  base::TrimWhitespace(new_name, base::TRIM_ALL, &new_name);

  if (!new_name.empty()) {
    profiles::UpdateProfileName(profile_, new_name);
  }
}

void OnboardingMessageHandler::HandleSetPreference(
    const base::ListValue& args) {
  if (args.size() == 0) {
    return;
  }

  AllowJavascript();

  const auto& callback_id = args[0].GetString();
  if (args.size() != 3) {
    return RejectJavascriptCallback(callback_id, "invalid arguments");
  }

  if (!args[1].is_string()) {
    return RejectJavascriptCallback(callback_id, "pref is not a string");
  }

  const auto& pref_name = prefs::kFocusPrefPrefix + args[1].GetString();
  auto* pref = pref_service_->FindPreference(pref_name);
  if (!pref) {
    return RejectJavascriptCallback(callback_id, pref_name + " does not exist");
  }

  if (args[2].type() != pref->GetType()) {
    return RejectJavascriptCallback(callback_id, "invalid value type");
  }

  pref_service_->Set(pref_name, args[2].Clone());

  ResolveJavascriptCallback(callback_id, {});
}

void OnboardingMessageHandler::HandleAcceptSchema(const base::ListValue&) {
  focus::AcceptCurrentSchema(*pref_service_);
}

void OnboardingMessageHandler::HandleApplyFocusSettings(
    const base::ListValue& args) {
  CHECK_EQ(2U, args.size());
  const auto& callback_id = args[0].GetString();

  if (!args[1].is_dict()) {
    return RejectJavascriptCallback(callback_id, "settings must be a dict");
  }

  const auto& settings = args[1].GetDict();
  const std::optional<bool> quiet_notifications =
      settings.FindBool("quietNotifications");
  const std::optional<bool> minimal_interface =
      settings.FindBool("minimalInterface");
  const std::optional<bool> smooth_animations =
      settings.FindBool("smoothAnimations");
  const std::string* location_bar_style =
      settings.FindString("locationBarStyle");
  if (!quiet_notifications || !minimal_interface || !smooth_animations ||
      !location_bar_style ||
      (*location_bar_style != "full" &&
       *location_bar_style != "centered" &&
       *location_bar_style != "minimal")) {
    return RejectJavascriptCallback(callback_id, "invalid focus settings");
  }

  auto* content_settings =
      HostContentSettingsMapFactory::GetForProfile(profile_);
  content_settings->SetDefaultContentSetting(
      ContentSettingsType::NOTIFICATIONS,
      *quiet_notifications ? CONTENT_SETTING_BLOCK : CONTENT_SETTING_ASK);

  pref_service_->SetBoolean(prefs::kShowHomeButton, !*minimal_interface);
  pref_service_->SetBoolean(bookmarks::prefs::kShowBookmarkBar,
                            !*minimal_interface);
  pref_service_->SetBoolean(prefs::kFocusMotionEnabled, *smooth_animations);

  // The first-run flow must never change the browser's tab layout. Advanced
  // layouts remain available from Settings, while every fresh setup starts
  // from the predictable classic horizontal layout.
  pref_service_->SetInteger(
      prefs::kFocusLayout,
      std::to_underlying(FocusLayoutType::kClassic));
  pref_service_->SetBoolean(prefs::kFocusCenteredLocationBar,
                            *location_bar_style != "full");
  pref_service_->SetBoolean(prefs::kFocusMinimalLocationBar,
                            *location_bar_style == "minimal");

  AllowJavascript();
  ResolveJavascriptCallback(callback_id, {});
}

void OnboardingMessageHandler::HandleSetNtpShortcuts(
    const base::ListValue& args) {
  if (args.empty() || !args[0].is_string()) {
    return;
  }

  const std::string& callback_id = args[0].GetString();
  AllowJavascript();
  if (args.size() != 2 || !args[1].is_list()) {
    return RejectJavascriptCallback(callback_id,
                                    "shortcut IDs must be a list");
  }

  if (profile_->IsOffTheRecord()) {
    return RejectJavascriptCallback(callback_id,
                                    "shortcuts are unavailable off the record");
  }

  std::set<std::string> seen_ids;
  std::vector<const OnboardingShortcut*> requested;
  requested.reserve(kMaxOnboardingShortcuts);

  for (const base::Value& value : args[1].GetList()) {
    if (!value.is_string()) {
      continue;
    }

    const std::string& id = value.GetString();
    if (!seen_ids.insert(id).second) {
      continue;
    }

    if (const OnboardingShortcut* shortcut = FindOnboardingShortcut(id)) {
      requested.push_back(shortcut);
      if (requested.size() == kMaxOnboardingShortcuts) {
        break;
      }
    }
  }

  auto manager = ChromeCustomLinksManagerFactory::NewForProfile(profile_);
  // Onboarding choices are authoritative. Clear links that may have been
  // created by an earlier setup attempt before applying the current list.
  if (manager->IsInitialized()) {
    manager->Uninitialize();
  }

  // An empty onboarding selection still initializes an empty custom-links
  // collection. This keeps the clean NTP free of suggested sites while
  // leaving its localized "Add shortcut" tile available.
  if (!manager->Initialize(ntp_tiles::NTPTilesVector{})) {
    return RejectJavascriptCallback(callback_id,
                                    "failed to initialize shortcuts");
  }

  int added = 0;
  for (const OnboardingShortcut* shortcut : requested) {
    if (manager->AddLink(GURL(shortcut->url),
                         std::u16string(shortcut->title))) {
      ++added;
    }
  }

  // Make the chosen custom links visible even if an imported profile had
  // previously hidden shortcuts or selected Top Sites instead.
  pref_service_->SetBoolean(ntp_prefs::kNtpCustomLinksVisible, true);
  pref_service_->SetBoolean(ntp_prefs::kNtpShortcutsVisible, true);
  pref_service_->SetBoolean(ntp_prefs::kNtpPersonalShortcutsVisible, true);

  ResolveJavascriptCallback(callback_id, base::Value(added));
}
