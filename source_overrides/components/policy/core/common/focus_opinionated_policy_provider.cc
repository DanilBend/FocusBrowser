// Copyright 2025 The Focus Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

#include "components/policy/core/common/focus_opinionated_policy_provider.h"

#include <utility>

#include "components/policy/core/common/policy_bundle.h"

namespace policy {
void HopProvider::RefreshPolicies(PolicyFetchReason) {
  PolicyBundle bundle;
  UpdatePolicy(std::move(bundle));
}

HopProvider::HopProvider() {
  RefreshPolicies(PolicyFetchReason::kBrowserStart);
}

}  // namespace policy
