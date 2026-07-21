# Focus Browser

Focus Browser is a Chromium-based browser focused on privacy, speed, and a clean browsing experience.

This source package contains the shared Chromium patchset and branding resources for Focus Browser 1.0. It targets the Chromium version recorded in `chromium_version.txt`.

## Branding

- Product, installer, crash reporter, settings, onboarding text, and translations use the Focus Browser name.
- Internal browser pages use the `focus://` scheme.
- Application metadata uses `com.focusbrowser.browser` as the macOS bundle identifier.
- Focus Browser icons and wordmarks are stored in `resources/branding`.
- Product-specific patches use the `patches/focus` vendor directory.

## Building

This is the shared patchset, not a prebuilt browser binary. A platform build wrapper is still required to download Chromium, fetch the declared dependencies, apply `patches/series`, generate localized resources, and package the result for Windows, Linux, or macOS.

The helper scripts under `utils` and `devutils` perform source-name substitution, version injection, localization, patch validation, and resource generation.

## Internal identifiers

Product-specific patch paths, version helpers, resource manifests, workflows, and build tooling use Focus Browser naming. Upstream Chromium identifiers are retained only where required by Chromium compatibility.

## Credits and license

Focus Browser is built on Chromium, ungoogled-chromium, and the GPL-licensed Focus patchset. Imported code and patches retain their original copyright notices and licenses.

Focus Browser-specific modifications are distributed under GPL-3.0. See `LICENSE` and `LICENSE.ungoogled_chromium`.
