# Focus Browser for macOS (Apple Silicon)

This directory is a small planning and validation layer for a native
Chromium/Views macOS port. `focus_macos.py` is strictly read-only: it does
**not** download Chromium, patch or copy into a Chromium tree, invoke
Xcode/GN/Ninja, publish artifacts, sign code, notarize an app, or configure an
updater. `generate_icns.py --generate` is the one separate, explicit small
write operation: it creates only the fixed local `.icns` output described
below. The Windows build remains outside this workflow.

## Fixed contracts

- Chromium must already exist locally and must be exactly `150.0.7871.128`.
- Architecture is native macOS `arm64`; the only planned Ninja target is
  `chrome` (never `setup` or `mini_installer`).
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
- The common series is pinned as 323 ordered entries with SHA-256
  `19024bfebaad5f41feb9b656b4bcb5938297a49008a114e65d3a9109c2dbde97`.
  Removing only the two audited Windows entries must produce 321 ordered
  entries with SHA-256
  `18743abf37930f84f6ba31cde63e2d887a99cd997794189ec50eb0cfc1dd11ae`.
  Branding, versioning, uBlock/Focus services and FocusBlock native-service
  patches have independent position and content-hash pins. The two platform
  FocusBlock/FocusYoutube Views patches are separately hash-pinned and ordered.
- The root Windows `patches/series`, `downloads.ini`, `flags.windows.gn`,
  `installer/`, and `resources/platform_resources.txt` are never part of this
  plan. The filtered overlay omits every prefix in `overlay-excludes.txt`.
- GN flags use a strict full-line typed assignment grammar; trailing text,
  inline comments on assignments, duplicate names, or altered macOS/arm64
  release values fail closed.
- App updater integration, signing, entitlements, provisioning, notarization,
  packaging, and App Store distribution are deliberately off/document-only.
  Installing Xcode or buying an Apple Developer membership does not change this
  state and is not required for the current local validation work.
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

Use an explicit, already-existing Chromium source root for every command:

```sh
python3 platform/macos/focus_macos.py validate \
  --source-root /absolute/path/to/chromium/src

python3 platform/macos/focus_macos.py plan \
  --source-root /absolute/path/to/chromium/src \
  --min-free-gib 180 \
  --json
```

`180` above is only an example, not a project estimate. `plan` requires the
operator to supply a disk threshold derived from a measured successful build.
The deterministic gate is:

```text
free bytes on the Chromium source filesystem >= --min-free-gib * 1024^3
```

Set the threshold to the measured peak growth of the chosen `out/` build plus
packaging/profile space and an explicit safety reserve. Do not start a checkout
or build merely because the current machine reports about 148 GiB free: the
repository does not contain enough data to derive a trustworthy Chromium +
toolchain + PGO + output peak. A failed gate makes `plan` exit non-zero.

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

## Planned production pipeline (not executed here)

1. Acquire Chromium/depot_tools by an approved external process; keep the
   checkout outside this repository and verify the exact version.
2. Apply `focus-chromium/patches/series`, excluding the audited Windows-only
   entries `focus/core/windows-first-run-locale.patch` and
   `focus/ui/fix-windows-ui-position.patch` until they are proven portable.
3. Apply this directory's two shared Views patches in series order: FocusBlock,
   then FocusYoutube. Never apply the root Windows series.
4. Run the common domain/name substitutions and common RU/EN i18n workflow.
5. Apply `source_overrides/` through a filtered overlay that skips
   `overlay-excludes.txt`. Treat `source_overrides/delete.txt` as a separate
   cleanup manifest: this CLI validates and displays its safe, platform-neutral
   relative paths but does not delete them. A future explicit write mode must
   filter them again and constrain every target inside the Chromium root.
6. Verify and copy only common Focus resources plus the already-generated,
   hash-pinned macOS `.icns`. Regenerate it only via the explicit command above
   when the output is deliberately absent.
7. Append Focus version metadata and write the composed common + ARM64 flags to
   the planned `out/FocusMacArm64/args.gn`.
8. After the disk gate and human review, run the displayed `gn gen` and
   `autoninja ... chrome` commands manually.
9. Validate the unsigned `.app` locally. Do not redistribute it while any
   GPL/uBlock/filter-list, Ghostery/MPL, Unhook permission/App Store,
   codec/FFmpeg, or Widevine gate is unresolved. Signing/notarization/updater
   work needs a separate reviewed implementation and credentials; it is not
   implied by an installed Xcode or Apple Developer account.

The planning CLI reports commands and paths only. It intentionally contains no
network, copy, delete, patch-application, build, or publishing operation. The
icon generator is separate and limited to its fixed local asset path.
