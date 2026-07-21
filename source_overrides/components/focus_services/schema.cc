// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "components/focus_services/schema.h"

#include "components/focus_services/focus_services_helpers.h"
#include "components/focus_services/pref_names.h"
#include "components/prefs/pref_service.h"

namespace focus {

bool HasAcceptedSchema(const PrefService& prefs, int version) {
  return prefs.GetBoolean(prefs::kFocusDisableSchemaAlerts) ||
         prefs.GetInteger(prefs::kFocusSchemaVersion) >= version;
}

void AcceptCurrentSchema(PrefService& prefs) {
  prefs.SetInteger(prefs::kFocusSchemaVersion, kFocusCurrentSchemaVersion);
}

bool ShouldShowSchemaNotification(const PrefService& prefs) {
  return ShouldAccessServices(prefs) &&
         !HasAcceptedSchema(prefs, kFocusCurrentSchemaVersion);
}

ServicesChangelog& GetChangelog() {
  static constexpr auto kFocusSchemaChangelog =
      base::MakeFixedFlatMap<int, std::string_view>({
          {
            1,
            "Automatic component updates are now available. They're managed by the same toggle that enables automatic browser updates.\n"
            "From now on, you'll be notified about major changes to Focus Browser services. Even though these notifications are extremely rare, you can choose to ignore them and accept all future changes automatically."
          }
      });

  return kFocusSchemaChangelog;
}

}  // namespace focus
