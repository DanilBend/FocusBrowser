/* Copyright (c) 2022 The Brave Authors. All rights reserved.
 * Copyright (c) 2026 Focus Browser contributors.
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

#ifndef COMPONENTS_FOCUS_BLOCK_RS_RESOLVER_DOMAIN_RESOLVER_H_
#define COMPONENTS_FOCUS_BLOCK_RS_RESOLVER_DOMAIN_RESOLVER_H_

#include <string>

namespace focus_block {

struct DomainPosition;

DomainPosition resolve_domain_position(const std::string& host);

}  // namespace focus_block

#endif  // COMPONENTS_FOCUS_BLOCK_RS_RESOLVER_DOMAIN_RESOLVER_H_
