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
- local parallelism: `-j8` on the 10-core, 16 GiB host, leaving headroom for
  Chromium generators that create their own worker pools
- outputs: `chrome` and `chrome/installer/mac:copies`
- remote execution, Siso, updater, and branded entitlements: disabled
- signing: Chromium's generated nested signing workflow with ad-hoc identity
  `-`, development mode, no provisioning profile, no notarization, and no
  Chromium packaging
- packaging: the repository's verified local UDZO DMG packager
- merge/sign/package Python: depot_tools' pinned CIPD CPython 3.11.8; Apple
  `/usr/bin/python3` is rejected because Xcode 27 currently resolves it to
  Python 3.9, while Chromium 150 signing requires `asyncio.TaskGroup`

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

### 3. Apply the audited macOS/Xcode compatibility backports

```sh
python3 platform/macos/build_pipeline.py apply-gn-compat \
  --source-root "$SRC" --execute --json

python3 platform/macos/build_pipeline.py apply-xcode27-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json

python3 platform/macos/build_pipeline.py apply-xcode27-seatbelt-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json

python3 platform/macos/build_pipeline.py apply-screen-ai-disabled-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json

python3 platform/macos/build_pipeline.py apply-xcode27-linkedit-strip-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

The first command gates Chromium GN dependencies that are absent when the
prepared profiles disable Safe Browsing and SwiftShader. The second applies
Chromium upstream commit `f0ccfb5933f7daa9545159afbb35bdf8951efcc4`'s
one-file `_Builtin_float` dependency fix for Xcode 27 explicit modules. The
third applies canonical Chromium commit
`6c0a651f9cf91d07c87be8feba854a38a311aba6`, which removes the unused
`kSBXProfilePureComputation` SDK dependency deleted by macOS SDK 27. The
fourth restores Chromium's canonical `ENABLE_SCREEN_AI_SERVICE` guards around
the macOS `ScreenAIInstallState` caller. Chromium introduced that guarded
caller in `c5de29a7cd701daec46a7bf042dd0551e5e8c5c3`, then removed the guards in
`4ee66d6d1eb2b630a9e30f52f08e3233e23c5864` after declaring ScreenAI always
enabled on desktop. Focus deliberately keeps the service disabled and its GN
implementation dependency absent, so the guards are required; the pipeline
does not enable or download ScreenAI. The fifth command addresses LLVM issue
[#203678](https://github.com/llvm/llvm-project/issues/203678), fixed upstream
by [PR #203680](https://github.com/llvm/llvm-project/pull/203680) and commit
`18c1cbce6874a7341f357014befb66d4c11a04a9`. Chromium's pinned pre-fix
`llvm-strip` can move 64-bit `__LINKEDIT` tables to four-byte offsets that
macOS 27 dyld rejects. The one-file GN patch keeps LLD enabled and selects the
hash-pinned Xcode `strip` only for macOS toolchains with
`xcode_version_int >= 2700`; it never postprocesses an existing binary. Every
command is offline, hash-pinned, transactional, and publishes an immutable
receipt only after its exact post-image is verified.

### 4. Build and preserve arm64

```sh
python3 platform/macos/build_pipeline.py build-arm64 \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json

python3 platform/macos/build_pipeline.py stage-arm64 \
  --source-root "$SRC" --execute --allow-reclaim-arm64-out --json
```

The staging command copies to a private `.part` tree under the checkout,
compares deterministic tree hashes, records a stage receipt, revalidates both
trees, and requires every non-zero 64-bit Mach-O LINKEDIT offset to be
eight-byte aligned (code signatures require 16 bytes). The gate walks the
whole app, including Framework, ANGLE libraries, Crashpad, app-mode loader,
and helpers; checking only the main executable is insufficient. It then
removes only the exact measured `out/FocusMacArm64`. A separate
`arm64-reclaim-complete.json` is written only after that path is absent. No
other source or output directory is reclaimed.

### 5. Build x86_64

```sh
python3 platform/macos/build_pipeline.py build-x64 \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

The x86_64 start gate uses the measured allocation of the reclaimed arm64
output. It also revalidates the staged app, receipts, tool hashes, preparation
hashes, and absence of `out/FocusMacArm64`. Receipt validation explicitly
accepts the recorded reclaimed arm64 `args.gn` hash while keeping every other
preparation and compatibility input immutable.

After `gn gen`, each architecture also scans every generated
`toolchain.ninja`: all Apple linker-driver `strippath` tokens must resolve to
the pinned Xcode binary and no token may still select `llvm-strip`. This check
runs before Ninja may compile or relink. The completed thin-app receipt embeds
that generated-toolchain report and the full LINKEDIT audit.

### 6. Refresh signing metadata for disabled SwiftShader

```sh
python3 platform/macos/build_pipeline.py \
  apply-swiftshader-disabled-signing-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

Both Focus profiles deliberately set `enable_swiftshader=false`, and the two
verified app slices consequently omit `libvk_swiftshader.dylib` while retaining
`libEGL.dylib` and `libGLESv2.dylib`. Chromium 150's macOS signing list did not
carry that GN condition and otherwise tries to sign the intentionally absent
library. This Focus-profile-pinned correction removes only that stale signing
entry, then refreshes only `chrome/installer/mac:copy_signing` at `-j8`; it
does not rebuild or modify either app slice. The exact pre/post images, build
arguments, app library inventories, patch, Ninja identity, and receipt chain
are verified transactionally before the merge may continue.

When recovering the already-patched combined ad-hoc source image, both source
and generated `parts.py` must already have the exact combined post hash. The
SwiftShader receipt can then be safely regenerated against the new slice/tree
receipts without relying on mtimes or reverting the later ad-hoc correction;
mixed `post-adhoc`/`pre` states fail closed.

### 7. Validate and refresh ad-hoc runtime signing

```sh
python3 platform/macos/build_pipeline.py \
  apply-adhoc-runtime-signing-compat \
  --source-root "$SRC" --developer-dir "$XCODE" --execute --json
```

Chromium's hardened-runtime defaults enforce Library Validation even for an
ad-hoc identity, while an ad-hoc Framework has no matching Team ID. For the
local identity `-` only, this stage removes the explicit `library` option and
adds `com.apple.security.cs.disable-library-validation` to exactly the seven
executables that load Focus Browser Framework. It preserves their existing
JIT, device, and development entitlements; Crashpad, the Framework, ANGLE
libraries, and updater retain their original signing policy. The targeted
Chromium signing tests must pass before the stage refreshes only
`chrome/installer/mac:copy_signing` at `-j8`. Source, generated scripts, app
tree hashes, test command, and receipt publication are protected by one
transactional rollback boundary.

The refresh never trusts source/output mtimes. After backing up and hashing the
two exact generated outputs (`parts.py` and `modification.py`), the stage
removes only those outputs before invoking Ninja. Missing outputs force the
copy rule to run even during recovery from `source=post, packaging=pre`; any
failure restores generated files first and source files second.

### 8. Merge, ad-hoc sign, and create the local DMG

```sh
python3 platform/macos/build_pipeline.py merge-sign-package \
  --source-root "$SRC" --developer-dir "$XCODE" \
  --dmg-output "$DMG" --execute --json
```

The DMG path must be absolute, absent, and below a real non-symlink parent.
The pipeline verifies the completed disabled-SwiftShader and ad-hoc-runtime
signing receipts and exact signing sources, combines both apps, and signs nested
code with Chromium's generated scripts. It consumes Chromium's deterministic
unpackaged `stable/`
distribution output, requires `Signature=adhoc`, verifies the complete
signature and both architectures, and inspects the effective CodeDirectory
flags and entitlements for both slices of every protected signing part. Exactly
seven Framework loaders must retain hardened runtime while omitting explicit
Library Validation and carrying
`com.apple.security.cs.disable-library-validation=true`; Crashpad retains
Library Validation, while the Framework and every bundled dylib remain
unrelaxed.

Before packaging, the signed app must launch natively as arm64 and through a
mandatory Rosetta x86_64 probe. Each launch uses a distinct new profile,
`--incognito`, a nonce-bearing offline `data:text/html` marker, network-
disabling switches, a 60-second timeout, and an isolated process group that is
interrupted and killed during bounded cleanup. Only then does the monitored
DMG packager run. The exact final DMG is mounted read-only and the same two
runtime smokes are repeated from the mounted app. If this final acceptance
fails after the image is detached, the pipeline removes only the just-created
regular DMG with the same device/inode; it refuses to remove a replaced path.
If both normal and forced detach fail, the exact backing DMG is retained for
manual detach instead of unlinking a possibly mounted image. Both runtime
reports are included in the final JSON. The source and DMG filesystems are watched
throughout packaging. The LINKEDIT gate is repeated after universalization and
after signing, once per slice of every Mach-O file.

## One-time Xcode 27 LINKEDIT recovery

This section is only for the recorded current run whose old arm64 and x86_64
apps were built with the pre-fix `llvm-strip`. Do not use Apple `strip` to
rewrite those invalid apps: tests show that postprocessing an already-invalid
large Framework does not reliably repair its offsets.

Before this recovery, any old universal roots and DMG must be separately
quarantined with hashes so `out/FocusMacUnsignedUniversal` and
`out/FocusMacSignedUniversal` are absent. The recovery command refuses to
move them or overwrite any quarantine. First inspect its dry run, then provide
both explicit mutation flags:

```sh
python3 platform/macos/build_pipeline.py prepare-xcode27-linkedit-recovery \
  --source-root "$SRC" --developer-dir "$XCODE" --json

python3 platform/macos/build_pipeline.py prepare-xcode27-linkedit-recovery \
  --source-root "$SRC" --developer-dir "$XCODE" \
  --execute --allow-recovery-move --json
```

The command accepts only the exact recorded legacy app/tree and receipt
hashes. It audits those apps with `require_aligned=false`, atomically archives
the invalid staged arm64 app, x86_64 app, build/stage/reclaim receipts, and
stale SwiftShader receipt under `out/FocusMacXcode27LinkeditRecovery`, restores
the exact arm64 `args.gn`, and leaves x86_64 objects in place. It does not
modify any Mach-O bytes. Immediately before publishing the recovery root, it
re-hashes and re-sizes every moved destination against the plan. If an
interrupt lands after the `.part` root has already been renamed, rollback
first verifies the published manifest/artifacts, normalizes that exact root
back to `.part`, and restores every move; an unsafe tree is retained at the
path named in the error instead of being guessed at. Then run steps 4 through
8 in order: arm64 is rebuilt
from source and restaged/reclaimed; x86_64 is incrementally relinked after GN
regeneration; the SwiftShader and ad-hoc receipts are regenerated against the
new slices; only then may universal merge/sign/package begin. Preserve `-j8`.

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
The one-time LINKEDIT recovery uses a same-filesystem `.part` archive and
rolls every exact move back if publication fails.

## Distribution boundary

An Apple Developer account is not required to create or locally install this
ad-hoc-signed DMG. Gatekeeper may require the user to approve opening an
unnotarized local build. Public redistribution is a separate task requiring
legal review, Developer ID signing, notarization, and release/update policy;
this pipeline intentionally performs none of those actions.
