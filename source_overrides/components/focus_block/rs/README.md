# FocusBlock adblock-rust bridge

This directory contains the browser-owned CXX bridge for `adblock-rust`
0.13.2. It is intentionally limited to filter-list ingestion, engine creation,
network matching, engine cache serialization, and cosmetic selector lookup.

The bridge is derived from Brave's MPL-2.0 adblock CXX integration. Keep the
MPL-2.0 headers and the upstream `adblock-rust` license notice when modifying
or redistributing these files.

Consumers should depend on `//components/focus_block:native_engine`, which
links both the Rust library and its resolver. The resolver connects the Rust
engine to Chromium's public-suffix implementation.
