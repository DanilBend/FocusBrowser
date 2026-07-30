# Focus Browser for macOS (Intel and Apple Silicon)

This directory contains the planning, acquisition, offline preparation,
sequential universal build, and local-DMG layers for a native Chromium/Views
macOS port. `focus_macos.py` itself is strictly read-only: it does
**not** download Chromium, patch or copy into a Chromium tree, invoke
Xcode/GN/Ninja, publish artifacts, sign code, notarize an app, or configure an
updater. `generate_icns.py --generate` is a separate explicit small write that
creates only the fixed local `.icns`; `package_local_dmg.py` is a separate
fail-closed packager that accepts only an already-built, already-signed app and
creates one new local DMG without overwriting. The Windows build remains
outside this workflow.

See `INCOGNITO.md` for the native private-mode contract and runtime acceptance
matrix, `UNIVERSAL.md` for Intel/Apple Silicon and macOS 12+ compatibility,
`LOCAL-DMG.md` for the local ad-hoc `.app`/DMG boundary, and
`BUILD-PIPELINE.md` for the exact executable stage order.

The writable macOS sequence is provenance-gated: `acquire_chromium.py` must
first produce a completed pinned acquisition marker, then
`build_pipeline.py bootstrap-tools --execute` must run Chromium hooks and write
the exact `.focus-macos-tool-bootstrap.json` receipt beside `src`. Only then may
`prepare_source.py prepare --confirm-source-mutation` install verified cached
dependencies, prune listed binaries, apply patches and overlays, and write GN
arguments. The receipt binds the canonical Xcode Developer directory and
current hashes of `gclient`, `gn`, and `autoninja`; source preparation rejects
a missing, tampered, or stale-tool marker before its first source-tree mutation.

## Fixed contracts

- Chromium must already exist locally and must be exactly `150.0.7871.128`.
- Supported architectures are native macOS `arm64` and `x64`. Each has a
  separate GN output and builds only `chrome` plus the required
  `chrome/installer/mac:copies` signing support (never `setup` or
  `mini_installer`). The exact Chromium 150 universalizer merges the
  accepted x86_64 and arm64 app trees into one universal app.
- Both slices explicitly disable the Chromium updater, Google-identity-bound
  entitlements, Siso, and remote execution. The local build uses the same
  bounded Ninja workflow for both architectures and does not contact a remote
  compilation service.
- The pinned Chromium 150 minimum is macOS 12.0. Both GN profiles explicitly
  pin `mac_deployment_target` and `mac_min_system_version` to `12.0`; macOS 11
  and older, 32-bit Intel, and PowerPC are not supported.
- Xcode 27 uses the hash-pinned Apple `strip` for macOS linker-driver output.
  Chromium's pinned `llvm-strip` predates LLVM fix
  `18c1cbce6874a7341f357014befb66d4c11a04a9` and can emit LINKEDIT offsets
  rejected by macOS 27 dyld. LLD remains enabled. Every 64-bit slice of every
  Mach-O in thin, universalized, and signed app trees must pass the 8-byte
  LINKEDIT/16-byte code-signature alignment gate.
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

Set this generic planner threshold to the capacity policy chosen for a manual
workflow. The executable low-space workflow does not keep both native output
trees: it uses the fixed pre/post-sync gates, runtime 35/30 GiB floors, measured
arm64 reclamation, and projected x86_64/merge growth documented in
`BUILD-PIPELINE.md`. A failed gate makes either workflow exit non-zero.

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

## Executable production pipeline

The implemented order is:

1. `acquire_chromium.py`: exact macOS-only Chromium/depot_tools sync plus ten
   hash-pinned cache archives (three shared Focus inputs and seven arm64/x64
   Mac host-tool inputs); network use requires `--execute-acquisition`.
2. `build_pipeline.py bootstrap-tools`: clean-revision proof and Chromium hooks
   before any source pruning.
3. `prepare_source.py`: offline cache extraction, file-only pruning, 321 common
   plus three macOS patches, substitutions, RU/EN, filtered overlay, resources,
   Focus 1.0.5.0 metadata, ICNS, deterministic onboarding `strings.ts`, and
   both `args.gn` files. The exact ten-entry cache marker, pre-patch archive
   union, and final transformed dependency tree are bound into the receipt.
4. `build_pipeline.py`: hash-pinned Xcode compatibility stages (including the
   macOS-27 LINKEDIT strip selection), arm64 build, verified thin-app staging,
   exact arm64 output reclamation, x86_64 build, universalization, nested
   ad-hoc signing, and monitored local DMG packaging. Local Ninja is the
   hash/architecture-pinned Dawn CIPD binary already present in the checkout.
   The documented one-time recovery archives only exact known-invalid thin
   evidence, rebuilds arm64, incrementally relinks preserved x86_64 objects,
   and regenerates signing receipts; it never postprocesses invalid binaries.

All mutating stages require their explicit execution/confirmation flag and
refuse to overwrite receipts or outputs. Read `CHROMIUM-ACQUISITION.md` and
`BUILD-PIPELINE.md` before running them. No stage publishes, notarizes, uses a
Developer ID, modifies `xcode-select`, or touches the Windows/Android tree.
