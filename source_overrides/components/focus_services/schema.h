// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#ifndef COMPONENTS_FOCUS_SERVICES_SCHEMA_H_
#define COMPONENTS_FOCUS_SERVICES_SCHEMA_H_

#include "base/component_export.h"
#include "base/containers/fixed_flat_map.h"
#include "components/prefs/pref_service.h"

namespace focus {

inline constexpr int kFocusCurrentSchemaVersion = 1;

using ServicesChangelog = const base::
    fixed_flat_map<int, std::string_view, kFocusCurrentSchemaVersion>;

#define EX COMPONENT_EXPORT(FOCUS)

EX bool HasAcceptedSchema(const PrefService& prefs, int version);
EX void AcceptCurrentSchema(PrefService& prefs);
EX bool ShouldShowSchemaNotification(const PrefService& prefs);
EX ServicesChangelog& GetChangelog();

#undef EX

}  // namespace focus

#endif  // COMPONENTS_FOCUS_SERVICES_SCHEMA_H_
