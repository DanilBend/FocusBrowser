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

## uBlock Origin / FocusBlock

- Upstream: https://github.com/gorhill/uBlock
- Version recorded by this tree: 1.72.2.2
- License: GPL-3.0
- Local metadata: `source_overrides/third_party/ublock/README.chromium`
- Local license: `source_overrides/third_party/ublock/LICENSE.txt`

Focus Browser integrates a modified build as the non-removable FocusBlock
component. Those modifications do not remove the GPL-3.0 obligations that
apply to uBlock Origin-derived code.

Filter lists and libraries bundled below `source_overrides/third_party/ublock/`
may carry their own notices and license files. Those files are included next to
the corresponding material.

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
