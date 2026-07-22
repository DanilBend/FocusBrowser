// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef COMPONENTS_FOCUS_SERVICES_PREF_NAMES_H_
#define COMPONENTS_FOCUS_SERVICES_PREF_NAMES_H_

namespace prefs {

inline constexpr char kFocusSchemaVersion[] =
    "focus.services.schema_version";

inline constexpr char kFocusDisableSchemaAlerts[] =
    "focus.services.disable_schema_alerts";

inline constexpr char kFocusPrefPrefix[] = "focus.";

inline constexpr char kFocusServicesEnabled[] =
    "focus.services.enabled";

inline constexpr char kFocusServicesOrigin[] =
    "focus.services.origin_override";

inline constexpr char kFocusServicesConsented[] =
    "focus.services.user_consented";

inline constexpr char kFocusDidOnboarding[] =
    "focus.completed_onboarding";

inline constexpr char kFocusExtProxyEnabled[] =
    "focus.services.ext_proxy";

inline constexpr char kFocusBangsEnabled[] =
    "focus.services.bangs";

inline constexpr char kFocusUpdateFetchingEnabled[] =
    "focus.services.browser_updates";

inline constexpr char kFocusSpellcheckEnabled[] =
    "focus.services.spellcheck_files";

inline constexpr char kFocusUBlockAssetsEnabled[] =
    "focus.services.ublock_assets";

// Visibility of browser-owned protection controls. These preferences only
// hide toolbar entry points; they never disable the underlying engines.
inline constexpr char kShowFocusBlockButton[] =
    "focus.browser.show_focus_block_button";
inline constexpr char kShowFocusYoutubeButton[] =
    "focus.browser.show_focus_youtube_button";

// Native FocusBlock is enabled by default. Disabled sites are stored as
// registrable domains so one exception applies consistently to subdomains.
inline constexpr char kFocusBlockEnabled[] = "focus.block.enabled";
inline constexpr char kFocusBlockDisabledSites[] =
    "focus.block.disabled_sites";

// Controls motion in Focus-owned WebUI surfaces and built-in protection
// popups. The operating system's reduced-motion preference always wins.
inline constexpr char kFocusMotionEnabled[] =
    "focus.ui.motion_enabled";
}  // namespace prefs

#endif  // COMPONENTS_FOCUS_SERVICES_PREF_NAMES_H_
