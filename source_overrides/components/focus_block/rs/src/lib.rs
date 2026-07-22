/* Copyright (c) 2023 The Brave Authors. All rights reserved.
 * Copyright (c) 2026 Focus Browser contributors.
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

//! CXX bridge between Focus Browser and adblock-rust.
//!
//! Policy, preferences, list updates, and request cancellation stay in the
//! C++ browser service. This crate only owns the parser/matcher objects.

mod engine;
mod filter_set;

use engine::*;
use filter_set::*;

#[allow(unsafe_op_in_unsafe_fn)]
#[cxx::bridge(namespace = "focus_block")]
mod ffi {
    extern "Rust" {
        type FilterSet;

        fn new_filter_set(debug: bool) -> Box<FilterSet>;
        fn add_filter_list(&mut self, rules: &CxxVector<u8>) -> AddFilterListResult;
        fn add_filter_list_with_permissions(
            &mut self,
            rules: &CxxVector<u8>,
            permission_mask: u8,
        ) -> AddFilterListResult;
    }

    extern "Rust" {
        type Engine;

        fn new_engine() -> Box<Engine>;
        fn engine_from_filter_set(filter_set: Box<FilterSet>) -> Box<Engine>;

        /// Installs Chromium's public-suffix resolver. It is safe to call this
        /// more than once; engine construction also calls it automatically.
        fn initialize_domain_resolver() -> bool;

        /// Evaluates one network request. `request_type` uses adblock-rust's
        /// names (for example `script`, `image`, `stylesheet`, `xhr`,
        /// `document`, or `subdocument`).
        fn matches(
            &self,
            url: &CxxString,
            hostname: &CxxString,
            source_hostname: &CxxString,
            request_type: &CxxString,
            third_party_request: bool,
            method: &CxxString,
            previously_matched_rule: bool,
            force_check_exceptions: bool,
        ) -> BlockerResult;

        /// Serializes the compiled engine for an on-disk cache. The format is
        /// a cache detail and may change between adblock-rust versions.
        fn serialize(&self) -> Vec<u8>;
        fn deserialize(&mut self, serialized: &CxxVector<u8>) -> bool;

        /// Returns JSON-serialized hostname-specific cosmetic resources.
        fn url_cosmetic_resources(&self, url: &CxxString) -> StringResult;

        /// Returns generic CSS selectors for classes and ids observed by the
        /// renderer. The caller supplies exceptions from url_cosmetic_resources.
        fn hidden_class_id_selectors(
            &self,
            classes: &CxxVector<CxxString>,
            ids: &CxxVector<CxxString>,
            exceptions: &CxxVector<CxxString>,
        ) -> StringVectorResult;
    }

    unsafe extern "C++" {
        include!("components/focus_block/rs/resolver/domain_resolver.h");

        fn resolve_domain_position(host: &CxxString) -> DomainPosition;
    }

    struct DomainPosition {
        start: u32,
        end: u32,
    }

    #[derive(Default)]
    struct OptionalString {
        has_value: bool,
        value: String,
    }

    #[derive(Default)]
    struct AddFilterListResult {
        success: bool,
        source_index: usize,
        error_message: String,
    }

    #[derive(Default)]
    struct BlockerResult {
        valid_input: bool,
        matched: bool,
        important: bool,
        has_exception: bool,
        redirect: OptionalString,
        rewritten_url: OptionalString,
        error_message: String,
    }

    #[derive(Default)]
    struct StringResult {
        success: bool,
        value: String,
        error_message: String,
    }

    #[derive(Default)]
    struct StringVectorResult {
        success: bool,
        value: Vec<String>,
        error_message: String,
    }
}
