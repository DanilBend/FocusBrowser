# Third-party notices

Focus Browser is a Chromium derivative and contains or builds upon software
from multiple projects. Each imported component retains its own copyright and
license terms. This file is a practical index; the license files shipped next
to individual components remain authoritative.

## Chromium

- Project: Chromium
- Website: https://www.chromium.org/
- License: BSD-style licenses and additional third-party licenses
- Source obtained during the build process; this repository stores Focus
  Browser patches and overrides rather than a complete Chromium checkout.

## ungoogled-chromium tooling

- Project: ungoogled-chromium / ungoogled-chromium-windows
- License: BSD 3-Clause for the applicable imported material
- Local copy: `LICENSE.ungoogled_chromium`

## Ghostery Adblocker / active FocusBlock engine

- Upstream: https://github.com/ghostery/adblocker
- Package: `@ghostery/adblocker`
- Version: 2.18.1
- Revision: `67ef23276e93ebc5dd4621cc9df2b09ad9f490d7`
- License: MPL-2.0
- Local metadata:
  `source_overrides/third_party/ghostery_adblocker/README.chromium`
- Local license: `source_overrides/third_party/ghostery_adblocker/LICENSE`
- Bundled dependency notices:
  `source_overrides/third_party/ghostery_adblocker/DEPENDENCIES.md`

FocusBlock's active request-matching and cosmetic-filter engine is a generated
browser bundle of `@ghostery/adblocker` 2.18.1. A Focus Browser-owned native
service loads that bundle and supplies its filter-list resources. The Ghostery
extension product and its user interface are not included. The FocusBlock
interface includes a technical attribution footer identifying
`Ghostery 2.18.1`.

## uBlock Origin filter assets and legacy component material

- Upstream: https://github.com/gorhill/uBlock
- Version recorded by this tree: 1.72.2.2
- License: GPL-3.0
- Local metadata: `source_overrides/third_party/ublock/README.chromium`
- Local license: `source_overrides/third_party/ublock/LICENSE.txt`

Focus Browser retains this source/resource bundle for filter-list assets,
binary resources, legacy compatibility material and attribution. FocusBlock
loads selected filter-list resources from this bundle, but the uBlock
component extension itself is not loaded by the browser. Runtime matching is
performed by the Ghostery Adblocker engine described above. Keeping the legacy
bundle in the source and binary resources does not remove the GPL-3.0
obligations that apply to uBlock Origin-derived code.

Filter lists and libraries bundled below `source_overrides/third_party/ublock/`
may carry their own notices and license files. Those files are included next to
the corresponding material.

## adblock-rust / legacy inactive FocusBlock integration

- Upstream: https://github.com/brave/adblock-rust
- Version: 0.13.2
- License: MPL-2.0
- Browser bridge: `source_overrides/components/focus_block/rs/`

Vendored `adblock-rust` source and an earlier FocusBlock CXX bridge remain in
the source tree as legacy compatibility material. The active FocusBlock
browser target does not link this bridge; current runtime matching uses the
Ghostery Adblocker engine described above. The retained Rust source and bridge
keep their MPL-2.0 license notices and attribution.

## Unhook / FocusYoutube

- Upstream product: https://unhook.app/
- Version recorded by this tree: 1.6.9.1
- Local metadata: `source_overrides/third_party/focus_youtube/README.chromium`
- Local terms: `source_overrides/third_party/focus_youtube/LICENSE.txt`

FocusYoutube contains material derived from Unhook. The Focus Browser project
owner states that the Unhook rightsholder granted written permission to modify
and distribute this integration. The Unhook-derived material is not covered by
the repository-wide GPL-3.0 grant. Public visibility of its source does not by
itself grant downstream modification, redistribution or sublicensing rights;
downstream users must follow the component-specific terms or obtain their own
permission from the rightsholder.

## WinSparkle

- Project: https://github.com/vslavik/winsparkle
- Version pinned in `downloads.ini`
- License: MIT

WinSparkle support is optional at build time and requires an Ed25519 public key
and HTTPS appcast URL. No private signing key is stored in this repository.

## Other dependencies

Additional third-party code, assets and filter lists retain the notices and
licenses stored beside them. If a notice appears to be missing, please open an
issue before redistributing the affected material.
