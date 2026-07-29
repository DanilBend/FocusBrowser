# Focus Browser for macOS (Intel and Apple Silicon)

This directory is a small planning and validation layer for a native
Chromium/Views macOS port. `focus_macos.py` is strictly read-only: it does
**not** download Chromium, patch or copy into a Chromium tree, invoke
Xcode/GN/Ninja, publish artifacts, sign code, notarize an app, or configure an
updater. `generate_icns.py --generate` is a separate explicit small write that
creates only the fixed local `.icns`; `package_local_dmg.py` is a separate
fail-closed packager that accepts only an already-built, already-signed app and
creates one new local DMG without overwriting. The Windows build remains
outside this workflow.

See `INCOGNITO.md` for the native private-mode contract and runtime acceptance
matrix, `UNIVERSAL.md` for Intel/Apple Silicon and macOS 12+ compatibility, and
`LOCAL-DMG.md` for the local ad-hoc `.app`/DMG boundary.

## Fixed contracts

- Chromium must already exist locally and must be exactly `150.0.7871.128`.
- Supported architectures are native macOS `arm64` and `x64`. Each has a
  separate GN output and the only planned Ninja target is `chrome` (never
  `setup` or `mini_installer`). The exact Chromium 150 universalizer merges the
  accepted x86_64 and arm64 app trees into one universal app.
- The pinned Chromium 150 minimum is macOS 12.0. Both GN profiles explicitly
  pin `mac_deployment_target` and `mac_min_system_version` to `12.0`; macOS 11
  and older, 32-bit Intel, and PowerPC are not supported.
- Product name and artwork remain Focus Browser. Added lines in the common
  branding patch must set both product names, crash product name, and bundle ID
  `com.focusbrowser.browser`; removed Chromium lines cannot satisfy the check.
- The canonical 1024x1024 RGBA PNG is SHA-256 pinned to
  `0492cd1a9fca0f6e658910c85a21ea854f6a8494dc67b6f95998cd91f953f3a5`.
  The real generated `platform/macos/resources/FocusBrowser.icns` is pinned to
  `326ded57eec25c32ba405d3a9246fd80c1ab5cfc5a71735afc542df3594f4948`
  and its embedded 32, 64, 128, 256, 512, and 1024px images are parsed.
- The English source catalog and `en-GB`/`ru` JSON are structurally and
  semantically validated: types, required fields, counts, source references,
  placeholders, language mappings, and key Focus RU/EN messages.
- FocusBlock service/startup queue/fail-open behavior, Ghostery engine and
  native RU/EN bubble are content- and SHA-256-pinned. FocusYoutube validates
  its 25 native controls, 29 ordered storage keys, schema, manifest, extension
  ID, component loader/resources/allowlist, toolbar wiring, and RU/EN text.
- Incognito is Chromium's native Off-The-Record implementation, not a WebKit
  wrapper or a second browser. File > New Incognito Window and `Command-Shift-N`
  remain wired to the native command; the shortcut cannot be reassigned on
  macOS. A private/guest identity marker cannot be hidden by the regular avatar
  preference, custom NTP cannot replace the private new-tab page on macOS, and
  the runtime flag that could suppress Incognito identity is removed by the
  final macOS patch. FocusBlock uses its own OTR service instance. FocusYoutube
  and uBlock are available in private windows under Chromium's component
  extension Incognito semantics; separate extension-storage isolation remains
  a runtime acceptance check. The macOS-only RU/EN disclosure describes
  browser-profile retention and explicitly does not promise network anonymity.
- The common series is pinned as 323 ordered entries with SHA-256
  `19024bfebaad5f41feb9b656b4bcb5938297a49008a114e65d3a9109c2dbde97`.
  Removing only the two audited Windows entries must produce 321 ordered
  entries with SHA-256
  `18743abf37930f84f6ba31cde63e2d887a99cd997794189ec50eb0cfc1dd11ae`.
  Branding, versioning, uBlock/Focus services and FocusBlock native-service
  patches have independent position and content-hash pins. An authoritative
  manifest hashes the complete bodies of all 321 planned common patches; a
  59-item private-mode-sensitive subset is an additional diagnostic. The three
  platform patches (FocusBlock, FocusYoutube, native Incognito safety) are
  separately syntax-checked, hash-pinned, and ordered.
- The root Windows `patches/series`, `downloads.ini`, `flags.windows.gn`,
  `installer/`, and `resources/platform_resources.txt` are never part of this
  plan. The filtered overlay omits every prefix in `overlay-excludes.txt`; an
  authoritative manifest hashes all 2531 included regular files and rejects
  symlinks or special files. The 72-item sensitive subset remains diagnostic.
- GN flags use a strict full-line typed assignment grammar; trailing text,
  inline comments on assignments, duplicate names, altered architecture, or
  altered macOS 12 release values fail closed for both profiles.
- App updater integration, Developer ID signing, provisioning, notarization,
  publishing, and App Store distribution are deliberately off.
  This branch targets only local Intel, Apple Silicon, or universal `.app`
  bundles and an optional local DMG.
  A paid Apple Developer membership is not required: the eventual local binary
  may use ad-hoc signing. The DMG packager is implemented but cannot run until
  a real Chromium checkout produces the complete accepted app bundle. Required
  Chromium/helper entitlements still have to be preserved by the ad-hoc signing
  workflow; only the distribution identity, provisioning, and notarization
  flows are off.
- `proprietary_codecs=true`, `ffmpeg_branding="Chrome"`, and the common
  `enable_widevine=true` are local build inputs, not redistribution clearance.
  Shipping stays blocked until codec/patent rights are reviewed and the actual
  Widevine CDM availability, license, bundling permission, and architecture are
  verified. The repository does not supply those rights or a macOS CDM.
- The redistribution inventory also keeps shipping blocked for GPL-3.0 and
  per-filter-list obligations in retained uBlock code/assets, MPL-2.0 and
  dependency notices for Ghostery, and the restrictive Unhook EULA. The repo
  says the owner has separate written permission for FocusYoutube but does not
  contain the grant itself. Legal review must inspect the actual grant for
  modification/distribution/sublicensing, macOS and Apple App Store coverage,
  territories, versions/derivatives, and duration, plus GPL/App Store and
  Corresponding Source/notices requirements.

## Read-only commands

Use explicit, already-existing Chromium and Xcode Developer roots for every
command. On this host the full Xcode beta is installed but the global
`xcode-select` still points to Command Line Tools, so the future build must use
`DEVELOPER_DIR=/Users/danilbuga/Downloads/Xcode-beta.app/Contents/Developer`
without changing the global selector:

```sh
python3 platform/macos/focus_macos.py validate \
  --source-root /absolute/path/to/chromium/src \
  --developer-dir /Users/danilbuga/Downloads/Xcode-beta.app/Contents/Developer

python3 platform/macos/focus_macos.py plan \
  --source-root /absolute/path/to/chromium/src \
  --developer-dir /Users/danilbuga/Downloads/Xcode-beta.app/Contents/Developer \
  --min-free-gib 180 \
  --json
```

`180` above is only an example, not a project estimate. `plan` requires the
operator to supply a disk threshold derived from a measured successful build.
The deterministic gate is:

```text
free bytes on the Chromium source filesystem >= --min-free-gib * 1024^3
```

Set the threshold to the measured peak growth of the checkout, both native
`out/` builds, universal staging, packaging/profile space, and an explicit
safety reserve. Do not start a checkout or build merely because the current
machine reports 117 GiB free: the repository does not contain enough data to
derive a trustworthy Chromium + toolchain + two outputs + universal merge
peak. A failed gate makes `plan` exit non-zero.

Both commands are dry runs. `--dry-run` is accepted as an explicit assertion;
neither command has a write mode. `--json` emits a machine-readable report.

The pre-generated icon is verified without writing:

```sh
python3 platform/macos/generate_icns.py --verify
```

Regeneration is deliberately explicit and refuses to overwrite an existing
asset. With the fixed output absent, it creates a temporary
`FocusBrowser.iconset`, resizes only the pinned PNG with system `sips`, packages
it with system `iconutil`, validates the container, and atomically places only
`platform/macos/resources/FocusBrowser.icns`:

```sh
python3 platform/macos/generate_icns.py --generate
```

After a real signed thin or universal app passes runtime acceptance, create a
new local image without signing, notarizing, networking, or overwriting:

```sh
python3 platform/macos/package_local_dmg.py \
  --app '/absolute/path/Focus Browser.app' \
  --output '/absolute/path/Focus Browser-local.dmg' \
  --require-universal \
  --json
```

The packager validates the bundle ID, both main-executable slices, complete
nested signature, staged copy, read-only mounted image, and `/Applications`
link before atomically placing the output and reporting its size and SHA-256.
Omit `--require-universal` only for an explicitly architecture-specific local
test image.

## Planned production pipeline (not executed here)

1. Acquire Chromium/depot_tools by an approved external process; keep the
   checkout outside this repository and verify the exact version.
2. Apply `focus-chromium/patches/series`, excluding the audited Windows-only
   entries `focus/core/windows-first-run-locale.patch` and
   `focus/ui/fix-windows-ui-position.patch` until they are proven portable.
3. Apply this directory's three patches in series order: FocusBlock,
   FocusYoutube, then `native-incognito-contract.patch`. Never apply the root
   Windows series.
4. Run the common domain/name substitutions and common RU/EN i18n workflow.
5. Apply `source_overrides/` through a filtered overlay that skips
   `overlay-excludes.txt`. Treat `source_overrides/delete.txt` as a separate
   cleanup manifest: this CLI validates and displays its safe, platform-neutral
   relative paths but does not delete them. A future explicit write mode must
   filter them again and constrain every target inside the Chromium root.
6. Verify and copy only common Focus resources plus the already-generated,
   hash-pinned macOS `.icns`. Regenerate it only via the explicit command above
   when the output is deliberately absent.
7. Append Focus version metadata and write architecture-specific composed GN
   args to `out/FocusMacArm64/args.gn` and `out/FocusMacX64/args.gn`.
8. After the disk gate and human review, run the displayed `gn gen` and
   `autoninja ... chrome` command for each architecture with the validated
   full-Xcode `DEVELOPER_DIR`; do not rely on the global Command Line Tools
   selector.
9. Merge the two same-source app trees, x64 first and arm64 second, with the
   exact hash-pinned Chromium 150 `chrome/installer/mac/universalizer.py`.
10. Apply the reviewed nested ad-hoc signing workflow while preserving helper
   entitlements, then validate the universal app natively on Apple Silicon and
   Intel. The source gate is not a substitute for runtime checks: exercise
   Incognito, FocusBlock, FocusYoutube, history, session restore, custom NTP,
   policy, and the permanent private identity marker on macOS 12 and current
   macOS acceptance hosts.
11. Create a local drag-and-drop DMG with `package_local_dmg.py` only after the
   complete app passes nested signature and launch checks. Do not redistribute
   it while any GPL/uBlock/filter-list, Ghostery/MPL, Unhook permission/App
   Store, codec/FFmpeg, or Widevine gate is unresolved.
   Signing/notarization/updater work needs a separate reviewed implementation
   and credentials; it is not implied by an installed Xcode or Apple Developer
   account.

The planning CLI reports commands and paths only. It intentionally contains no
network, copy, delete, patch-application, build, or publishing operation. The
icon generator and local DMG packager are separate, explicit tools constrained
to their documented output operations.
