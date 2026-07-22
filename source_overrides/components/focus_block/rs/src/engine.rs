/* Copyright (c) 2023 The Brave Authors. All rights reserved.
 * Copyright (c) 2026 Focus Browser contributors.
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at https://mozilla.org/MPL/2.0/. */

use std::collections::HashSet;
use std::str::Utf8Error;
use std::sync::OnceLock;

use adblock::blocker::BlockerResult as InnerBlockerResult;
use adblock::url_parser::ResolvesDomain;
use adblock::Engine as InnerEngine;
use cxx::{let_cxx_string, CxxString, CxxVector};

use crate::ffi::{
    resolve_domain_position, BlockerResult, OptionalString, StringResult, StringVectorResult,
};
use crate::filter_set::FilterSet;

pub struct Engine {
    engine: InnerEngine,
}

struct DomainResolver;

impl ResolvesDomain for DomainResolver {
    fn get_host_domain(&self, host: &str) -> (usize, usize) {
        let_cxx_string!(host_cxx = host);
        let position = resolve_domain_position(&host_cxx);
        (position.start as usize, position.end as usize)
    }
}

pub fn initialize_domain_resolver() -> bool {
    static INITIALIZED: OnceLock<()> = OnceLock::new();
    INITIALIZED.get_or_init(|| {
        // If another FocusBlock engine already installed the process-wide
        // resolver, set_domain_resolver returns our value. A resolver is still
        // present, so repeated initialization remains a success.
        let _ = adblock::url_parser::set_domain_resolver(Box::new(DomainResolver));
    });
    true
}

pub fn new_engine() -> Box<Engine> {
    initialize_domain_resolver();
    Box::new(Engine {
        engine: InnerEngine::default(),
    })
}

pub fn engine_from_filter_set(filter_set: Box<FilterSet>) -> Box<Engine> {
    initialize_domain_resolver();
    Box::new(Engine {
        engine: InnerEngine::new_with_filter_set(filter_set.0),
    })
}

impl From<Option<String>> for OptionalString {
    fn from(value: Option<String>) -> Self {
        match value {
            Some(value) => Self {
                has_value: true,
                value,
            },
            None => Self {
                has_value: false,
                value: String::new(),
            },
        }
    }
}

fn invalid_blocker_result(error_message: impl Into<String>) -> BlockerResult {
    BlockerResult {
        valid_input: false,
        matched: false,
        important: false,
        has_exception: false,
        redirect: None.into(),
        rewritten_url: None.into(),
        error_message: error_message.into(),
    }
}

fn convert_match(result: InnerBlockerResult) -> BlockerResult {
    BlockerResult {
        valid_input: true,
        matched: result.should_block(),
        important: result.important,
        has_exception: result.exception.is_some(),
        redirect: result.redirect.into(),
        rewritten_url: result.rewritten_url.into(),
        error_message: String::new(),
    }
}

fn as_utf8<'a>(value: &'a CxxString, field: &str) -> Result<&'a str, String> {
    value
        .to_str()
        .map_err(|_| format!("{field} is not valid UTF-8"))
}

fn cxx_strings_to_collection<C>(value: &CxxVector<CxxString>) -> Result<C, Utf8Error>
where
    C: FromIterator<String>,
{
    value
        .iter()
        .map(|item| item.to_str().map(str::to_owned))
        .collect()
}

impl Engine {
    pub fn matches(
        &self,
        url: &CxxString,
        hostname: &CxxString,
        source_hostname: &CxxString,
        request_type: &CxxString,
        third_party_request: bool,
        method: &CxxString,
        previously_matched_rule: bool,
        force_check_exceptions: bool,
    ) -> BlockerResult {
        initialize_domain_resolver();

        let url = match as_utf8(url, "url") {
            Ok(value) => value,
            Err(error) => return invalid_blocker_result(error),
        };
        let hostname = match as_utf8(hostname, "hostname") {
            Ok(value) => value,
            Err(error) => return invalid_blocker_result(error),
        };
        let source_hostname = match as_utf8(source_hostname, "source_hostname") {
            Ok(value) => value,
            Err(error) => return invalid_blocker_result(error),
        };
        let request_type = match as_utf8(request_type, "request_type") {
            Ok(value) => value,
            Err(error) => return invalid_blocker_result(error),
        };
        let method = match as_utf8(method, "method") {
            Ok(value) => value,
            Err(error) => return invalid_blocker_result(error),
        };

        let request = adblock::request::Request::preparsed(
            url,
            hostname,
            source_hostname,
            request_type,
            third_party_request,
            method,
        );
        convert_match(self.engine.check_network_request_subset(
            &request,
            previously_matched_rule,
            force_check_exceptions,
        ))
    }

    pub fn serialize(&self) -> Vec<u8> {
        self.engine.serialize()
    }

    pub fn deserialize(&mut self, serialized: &CxxVector<u8>) -> bool {
        initialize_domain_resolver();
        self.engine.deserialize(serialized.as_slice()).is_ok()
    }

    pub fn url_cosmetic_resources(&self, url: &CxxString) -> StringResult {
        let Ok(url) = as_utf8(url, "url") else {
            return StringResult {
                success: false,
                value: String::new(),
                error_message: "url is not valid UTF-8".to_string(),
            };
        };

        match serde_json::to_string(&self.engine.url_cosmetic_resources(url)) {
            Ok(value) => StringResult {
                success: true,
                value,
                error_message: String::new(),
            },
            Err(error) => StringResult {
                success: false,
                value: String::new(),
                error_message: error.to_string(),
            },
        }
    }

    pub fn hidden_class_id_selectors(
        &self,
        classes: &CxxVector<CxxString>,
        ids: &CxxVector<CxxString>,
        exceptions: &CxxVector<CxxString>,
    ) -> StringVectorResult {
        let Ok(classes) = cxx_strings_to_collection::<Vec<String>>(classes) else {
            return StringVectorResult {
                success: false,
                value: Vec::new(),
                error_message: "class list contains invalid UTF-8".to_string(),
            };
        };
        let Ok(ids) = cxx_strings_to_collection::<Vec<String>>(ids) else {
            return StringVectorResult {
                success: false,
                value: Vec::new(),
                error_message: "id list contains invalid UTF-8".to_string(),
            };
        };
        let Ok(exceptions) = cxx_strings_to_collection::<HashSet<String>>(exceptions) else {
            return StringVectorResult {
                success: false,
                value: Vec::new(),
                error_message: "exception list contains invalid UTF-8".to_string(),
            };
        };

        StringVectorResult {
            success: true,
            value: self
                .engine
                .hidden_class_id_selectors(&classes, &ids, &exceptions),
            error_message: String::new(),
        }
    }
}
