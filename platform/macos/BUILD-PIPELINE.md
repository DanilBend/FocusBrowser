# Native macOS build pipeline

`build_pipeline.py` is the executable, staged build boundary for the native
Focus Browser macOS port. It produces the same-source arm64 and x86_64 slices,
combines them with Chromium's pinned universalizer, applies a nested ad-hoc
signature, and creates a local drag-and-drop DMG. It never publishes,
notarizes, uses a Developer ID, changes `xcode-select`, or targets iOS,
Android, or Windows.

Every subcommand is a read-only dry run unless `--execute` is supplied. All
paths are explicit. The checkout and its provenance receipts must come from
`acquire_chromium.py` and `prepare_source.py`; a partial or replayed checkout
is rejected.

## Fixed build contract

- Chromium: `150.0.7871.128` at commit
  `81891e5ca708047763816c778216799ef14c66cb`
- Xcode: 27 beta 4 (`27A5228h`), macOS SDK 27.0
- deployment target: macOS 12.0
- slices: native `arm64`, then `x86_64`
- local parallelism: `-j4` on the 16 GiB host
- outputs: `chrome` and `chrome/installer/mac:copies`
- remote execution, Siso, updater, and branded entitlements: disabled
- signing: Chromium's generated nested signing workflow with ad-hoc identity
  `-`, development mode, no provisioning profile, no notarization, and no
  Chromium packaging
- packaging: the repository's verified local UDZO DMG packager

The final main executable must contain exactly `arm64` and `x86_64`. The
Chromium universalizer input order is deliberately x86_64 first and arm64
second, matching Chromium's own documented workflow.

## Required order

Assume these explicit paths in the examples:

```sh
SRC=/absolute/chromium-150-macos/src
XCODE=/Users/danilbuga/Downloads/Xcode-beta.app/Contents/Developer
DMG=/absolute/output/FocusBrowser-macOS-universal.dmg
```

Do not run later steps out of order.

### 1. Bootstrap pinned tools and hooks before source preparation

Dry run:

```sh
python3 platform/macos/build_pipeline.py bootstrap-tools \
  --source-root "$SRC" --developer-dir "$XCODE" --json
```

Execute:

```sh
python3 platform/macos/build_pipeline.py bootstrap-tools \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

This first proves the exact clean Chromium and depot_tools Git revisions and
hash-pinned upstream Mac signing inputs. It runs the hash-pinned depot_tools
`ensure_bootstrap` helper (which initializes the current checkout without
updating it), then pinned `gclient runhooks`, verifies the revisions again,
records GN/tool hashes, and writes `.focus-macos-tool-bootstrap.json`. It is
forbidden after preparation.

### 2. Prepare the source offline

Use `prepare_source.py` with the verified ten-archive dependency cache. It
contains the three shared Focus inputs plus pinned Chromium Node, general Node
modules, and Darwin arm64/x86_64 esbuild/Rollup packages; no package-manager
lifecycle scripts run. Its preflight is read-only; mutation requires
`--confirm-source-mutation`.

Preparation requires the exact ten-entry cache marker, starts from six absent
or completely empty dependency-owned roots, and proves the merged union as
13,212 regular files / 527,357,876 bytes with its pinned inventory SHA-256.
Ten validated onboarding `node_modules/.bin` symlinks are intentionally omitted
because copying their target bytes would change Node relative-import behavior;
their exact path/target inventory is separately count/hash pinned.
After the reviewed patches and translations, it runs the pinned native Node
and `generate-i18n.mts` twice offline, requires byte-identical `strings.ts`,
and records the final transformed dependency-tree hash for every later stage.

```sh
python3 platform/macos/prepare_source.py preflight \
  --source-root "$SRC" --cache /absolute/focus-dependency-cache --json

python3 platform/macos/prepare_source.py prepare \
  --source-root "$SRC" --cache /absolute/focus-dependency-cache \
  --confirm-source-mutation --workers 4 --json
```

Hooks must never be rerun after this point. Preparation writes both exact
`args.gn` files and `out/FocusMacPreparation.json`.

The low-space checkout intentionally omits Chromium's top-level Ninja CIPD.
Build stages instead verify the same pinned Ninja 1.12.1 already present under
Dawn (host architecture, executable SHA-256, CIPD package/version/instance),
place its directory after `depot_tools` in the child-only PATH, and scrub
compiler, Node/npm, Python, Rust, GN/GYP, Ninja, SDK, and dynamic-loader
override variables from the inherited build environment.

### 3. Build and preserve arm64

```sh
python3 platform/macos/build_pipeline.py build-arm64 \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json

python3 platform/macos/build_pipeline.py stage-arm64 \
  --source-root "$SRC" --execute --allow-reclaim-arm64-out --json
```

The staging command copies to a private `.part` tree under the checkout,
compares deterministic tree hashes, records a stage receipt, revalidates both
trees, and removes only the exact measured `out/FocusMacArm64`. A separate
`arm64-reclaim-complete.json` is written only after that path is absent. No
other source or output directory is reclaimed.

### 4. Build x86_64

```sh
python3 platform/macos/build_pipeline.py build-x64 \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

The x86_64 start gate uses the measured allocation of the reclaimed arm64
output. It also revalidates the staged app, receipts, tool hashes, preparation
hashes, and absence of `out/FocusMacArm64`.

### 5. Merge, ad-hoc sign, and create the local DMG

```sh
python3 platform/macos/build_pipeline.py merge-sign-package \
  --source-root "$SRC" --developer-dir "$XCODE" \
  --dmg-output "$DMG" --execute --json
```

The DMG path must be absolute, absent, and below a real non-symlink parent.
The pipeline verifies exact signing sources, combines both apps, signs nested
code with Chromium's generated scripts, requires `Signature=adhoc`, verifies
the complete signature and both architectures, then runs the local DMG
packager as a monitored process. The source and DMG filesystems are watched
throughout packaging.

## Disk safety and interrupted runs

- 35 GiB is the soft operational floor.
- 30 GiB is the non-negotiable hard floor.
- hooks must finish with at least 70 GiB free.
- every monitored process is checked before spawn, while running, and after
  exit; its complete process group is stopped on a floor crossing.
- the x86_64 and merge gates also reserve measured projected growth.

Receipts and final destinations are never overwritten. An interrupted stage
is left for inspection, except a staging `.part` tree created by the current
invocation, which is removed before returning failure. Recovery is explicit;
the pipeline never guesses that an incomplete output is reusable.

## Distribution boundary

An Apple Developer account is not required to create or locally install this
ad-hoc-signed DMG. Gatekeeper may require the user to approve opening an
unnotarized local build. Public redistribution is a separate task requiring
legal review, Developer ID signing, notarization, and release/update policy;
this pipeline intentionally performs none of those actions.
