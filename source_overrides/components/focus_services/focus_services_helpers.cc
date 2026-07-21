// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "components/focus_services/focus_services_helpers.h"

#include <optional>

#include "base/functional/bind.h"
#include "base/strings/stringprintf.h"
#include "components/focus_services/pref_names.h"
#include "components/focus_services/schema.h"
#include "components/prefs/pref_service.h"
#include "net/base/url_util.h"
#include "url/gurl.h"
#include "url/url_constants.h"

namespace focus {

std::optional<GURL> GetValidUserOverridenURL(std::string_view user_url_) {
    if (user_url_.empty()) {
        return std::nullopt;
    }

    GURL user_url = GURL(user_url_);
    if (!user_url.is_valid()) {
        return std::nullopt;
    }

    bool isSecure = user_url.SchemeIs(url::kHttpsScheme) || net::IsLocalhost(user_url);
    if (!isSecure) {
        return std::nullopt;
    }

    return user_url;
}

std::optional<GURL> GetValidUserOverridenURL(const PrefService& prefs) {
    return GetValidUserOverridenURL(
        prefs.GetString(prefs::kFocusServicesOrigin)
    );
}

GURL GetServicesBaseURL(const PrefService& prefs) {
    std::optional<GURL> user_url = GetValidUserOverridenURL(prefs);
    if (user_url) {
        return *user_url;
    }

    return GURL(kFocusDefaultOrigin);
}

GURL GetDummyURL() {
    return GURL(kFocusDummyOrigin);
}

bool ShouldAccessServices(const PrefService& prefs) {
    // Focus Browser has no private services endpoint.
    return false;
}

bool ShouldAccessExtensionService(const PrefService& prefs) {
    // Extensions are fetched directly from Chrome Web Store infrastructure.
    return true;
}

bool ShouldAccessDictionaryService(const PrefService& prefs) {
    return true;
}

bool ShouldFetchBangs(const PrefService& prefs) {
    return ShouldAccessServices(prefs) &&
            prefs.GetBoolean(prefs::kFocusBangsEnabled);
}

bool ShouldAccessUpdateService(const PrefService& prefs) {
  // Browser updates are a public, signed service and are deliberately
  // independent from Focus's optional private-services consent.
  return prefs.GetBoolean(prefs::kFocusUpdateFetchingEnabled);
}

bool ShouldAccessComponentUpdateService(const PrefService& prefs) {
    return true;
}

bool ShouldAccessUBlockAssets(const PrefService& prefs) {
    // FocusBlock uses uBlock's built-in asset sources directly.
    return false;
}

GURL GetExtensionUpdateURL(const PrefService& prefs) {
    return GURL("https://clients2.google.com/service/update2/crx");
}

GURL GetWebstoreSnippetURL(const PrefService& prefs, std::string_view id) {
    return GURL("https://chromewebstore.googleapis.com/")
        .Resolve(base::StringPrintf("v2/items/%s:fetchItemSnippet", id));
}

GURL GetSpellcheckURL(const PrefService& prefs) {
    return GURL("https://redirector.gvt1.com/edgedl/chrome/dict/");
}

GURL GetComponentUpdateURL(const PrefService* prefs) {
    return GURL("https://update.googleapis.com/service/update2/json");
}

GURL GetUBlockAssetsURL(const PrefService& prefs) {
    if (!ShouldAccessUBlockAssets(prefs)) {
        return GetDummyURL();
    }

    return GetServicesBaseURL(prefs).Resolve("/ubo/assets.json");
}

void ConfigurePrefChangeRegistrarFor(std::string_view pref_name,
    PrefChangeRegistrar& registrar, const base::RepeatingClosure& observer) {
  registrar.Add(prefs::kFocusServicesEnabled, observer);
  registrar.Add(prefs::kFocusServicesConsented, observer);
  registrar.Add(prefs::kFocusServicesOrigin, observer);
  registrar.Add(prefs::kFocusSchemaVersion, observer);
  registrar.Add(pref_name, observer);
}

}  // namespace focus
