/* Copyright (c) 2022 The Brave Authors. All rights reserved.
 * Copyright (c) 2026 Focus Browser contributors.
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

#include "components/focus_block/rs/resolver/domain_resolver.h"

#include <cstdint>
#include <limits>
#include <string>

#include "components/focus_block/rs/src/lib.rs.h"
#include "net/base/registry_controlled_domains/registry_controlled_domain.h"

namespace focus_block {
namespace {

uint32_t ClampToUint32(size_t value) {
  return value > std::numeric_limits<uint32_t>::max()
             ? std::numeric_limits<uint32_t>::max()
             : static_cast<uint32_t>(value);
}

}  // namespace

DomainPosition resolve_domain_position(const std::string& host) {
  const std::string domain =
      net::registry_controlled_domains::GetDomainAndRegistry(
          host,
          net::registry_controlled_domains::INCLUDE_PRIVATE_REGISTRIES);

  DomainPosition position{};
  if (domain.empty()) {
    position.start = 0;
    position.end = ClampToUint32(host.size());
    return position;
  }

  const size_t match = host.rfind(domain);
  if (match == std::string::npos ||
      match > std::numeric_limits<uint32_t>::max() ||
      domain.size() > std::numeric_limits<uint32_t>::max() - match) {
    position.start = 0;
    position.end = ClampToUint32(host.size());
    return position;
  }

  position.start = static_cast<uint32_t>(match);
  position.end = static_cast<uint32_t>(match + domain.size());
  return position;
}

}  // namespace focus_block
