// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef COMPONENTS_FOCUS_SERVICES_FOCUS_SERVICES_HELPERS_H_
#define COMPONENTS_FOCUS_SERVICES_FOCUS_SERVICES_HELPERS_H_

#include "base/functional/bind.h"
#include "components/prefs/pref_change_registrar.h"
#include "components/prefs/pref_service.h"
#include "url/gurl.h"

namespace focus {

const char kFocusDefaultOrigin[] =
    "https://focus-services-disabled.invalid";

const char kFocusDummyOrigin[] =
    "https://focus-services-disabled.invalid";

bool ShouldAccessServices(const PrefService& prefs);

COMPONENT_EXPORT(FOCUS) bool ShouldFetchBangs(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) bool ShouldAccessExtensionService(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) bool ShouldAccessUpdateService(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) bool ShouldAccessComponentUpdateService(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) bool ShouldAccessUBlockAssets(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) GURL GetServicesBaseURL(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) GURL GetDummyURL();
COMPONENT_EXPORT(FOCUS) GURL GetExtensionUpdateURL(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) GURL GetWebstoreSnippetURL(const PrefService& prefs, std::string_view id);
COMPONENT_EXPORT(FOCUS) GURL GetSpellcheckURL(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) GURL GetComponentUpdateURL(const PrefService* prefs);
COMPONENT_EXPORT(FOCUS) GURL GetUBlockAssetsURL(const PrefService& prefs);
COMPONENT_EXPORT(FOCUS) std::optional<GURL> GetValidUserOverridenURL(std::string_view user_url_);
COMPONENT_EXPORT(FOCUS) void ConfigurePrefChangeRegistrarFor(std::string_view pref_name,
                            PrefChangeRegistrar& registrar, const base::RepeatingClosure& observer);
}  // namespace focus

#endif  // COMPONENTS_FOCUS_SERVICES_FOCUS_SERVICES_HELPERS_H_
