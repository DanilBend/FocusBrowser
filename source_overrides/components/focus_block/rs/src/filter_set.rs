/* Copyright (c) 2023 The Brave Authors. All rights reserved.
 * Copyright (c) 2026 Focus Browser contributors.
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

use adblock::lists::{FilterSet as InnerFilterSet, ParseOptions};
use adblock::resources::PermissionMask;
use cxx::CxxVector;

use crate::ffi::AddFilterListResult;

pub struct FilterSet(pub(crate) InnerFilterSet);

pub fn new_filter_set(debug: bool) -> Box<FilterSet> {
    Box::new(FilterSet(InnerFilterSet::new(debug)))
}

impl FilterSet {
    pub fn add_filter_list(&mut self, rules: &CxxVector<u8>) -> AddFilterListResult {
        self.add_filter_list_with_permissions(rules, 0)
    }

    pub fn add_filter_list_with_permissions(
        &mut self,
        rules: &CxxVector<u8>,
        permission_mask: u8,
    ) -> AddFilterListResult {
        let Ok(rules) = std::str::from_utf8(rules.as_slice()) else {
            return AddFilterListResult {
                success: false,
                source_index: 0,
                error_message: "filter list is not valid UTF-8".to_string(),
            };
        };

        let record = self.0.add_filter_list(
            rules.to_string(),
            ParseOptions {
                permissions: PermissionMask::from_bits(permission_mask),
                ..Default::default()
            },
        );

        AddFilterListResult {
            success: true,
            source_index: record.source_index,
            error_message: String::new(),
        }
    }
}
