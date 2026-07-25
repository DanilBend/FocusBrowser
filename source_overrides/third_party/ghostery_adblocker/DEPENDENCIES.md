# Bundled dependencies

The generated `dist/focus_ghostery_adblocker.js` bundle is built exclusively
from the exact packages recorded in `package-lock.json`. The versions below
also match the dependency resolutions in Ghostery Adblocker tag `v2.18.1`.

## Runtime code shipped in the browser bundle

| Package | Version | License | Upstream source |
| --- | ---: | --- | --- |
| `@ghostery/adblocker` | 2.18.1 | MPL-2.0 | <https://github.com/ghostery/adblocker/tree/67ef23276e93ebc5dd4621cc9df2b09ad9f490d7/packages/adblocker> |
| `@ghostery/adblocker-content` | 2.18.1 | MPL-2.0 | <https://github.com/ghostery/adblocker/tree/67ef23276e93ebc5dd4621cc9df2b09ad9f490d7/packages/adblocker-content> |
| `@ghostery/adblocker-extended-selectors` | 2.18.1 | MPL-2.0 | <https://github.com/ghostery/adblocker/tree/67ef23276e93ebc5dd4621cc9df2b09ad9f490d7/packages/adblocker-extended-selectors> |
| `@ghostery/url-parser` | 1.3.1 | MPL-2.0 | <https://github.com/ghostery/url-parser> |
| `@remusao/guess-url-type` | 2.1.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/guess-url-type> |
| `@remusao/small` | 2.1.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/small> |
| `@remusao/smaz` | 2.2.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/smaz> |
| `@remusao/smaz-compress` | 2.2.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/smaz-compress> |
| `@remusao/smaz-decompress` | 2.2.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/smaz-decompress> |
| `@remusao/trie` | 2.1.0 | MPL-2.0 | <https://github.com/remusao/mono/tree/master/packages/trie> |
| `tldts-experimental` | 7.4.4 | MIT | <https://github.com/remusao/tldts> |
| `tldts-core` | 7.4.3 | MIT | <https://github.com/remusao/tldts> |

The complete MPL-2.0 text is in `LICENSE`. The tldts MIT notice and license are
in `LICENSE.tldts`.

## Build-only dependency

`esbuild` 0.28.1 (MIT, <https://github.com/evanw/esbuild>) produces the checked-in
browser IIFE. It is not shipped as a runtime module. Its license is in
`LICENSE.esbuild`; platform-specific esbuild packages in `package-lock.json`
are optional build executables under the same license.

Resolved tarball URLs and SHA-512 integrity values remain authoritative in
`package-lock.json`. `dist/focus_ghostery_adblocker.meta.json` records the
inputs actually incorporated into the browser bundle without timestamps or
machine-specific paths.
