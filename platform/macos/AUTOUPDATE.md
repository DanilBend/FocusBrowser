# Focus Browser macOS automatic updates

The macOS release profile uses Sparkle 2.9.4 with a dedicated Ed25519 key. It
does not enable ChromiumUpdater, Keystone, Google Updater, JavaScript in
Sparkle, or Sparkle system profiling.

## Fixed production contract

- feed: `https://danilbend.github.io/FocusBrowser/appcast-macos.xml`;
- public key: `NcOw/DDSWLfV+kG111aN6fO8b0K4v3dygU7nYlLkkD0=`;
- minimum system: macOS 12.0;
- payload: the exact universal `arm64` + `x86_64`
  `FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg` from GitHub prerelease
  `v1.0.6-macos`;
- channel: that release must be an immutable non-Latest prerelease; the current
  Windows stable release remains `v1.0.5`, and plain `v1.0.6` remains reserved
  for a future coordinated stable release;
- checks: automatic once per 86,400 seconds and manual from the About page;
- install: Sparkle verifies the archive before extraction and requires a
  signed feed;
- private key: macOS Keychain account `com.focusbrowser.browser` only. Never
  export, print, commit, or pass it on the command line.

The first updater-capable release must be installed manually. A previous app
without Sparkle cannot discover it. Every later release can update from the
signed production feed.

The first updater-capable macOS build is Focus Browser `1.0.6.0` (shown as
`1.0.6`). The macOS source preparer applies this separate version without
changing the Windows or Android release inputs or channels.

## Dependency and build inputs

Acquire the pinned framework into a new directory and then copy its validated
`Sparkle.framework` to `//third_party/sparkle/Sparkle.framework` in the prepared
Chromium source tree:

```sh
python3 platform/macos/acquire_sparkle.py \
  --destination /absolute/path/to/sparkle-2.9.4 \
  --execute

python3 platform/macos/acquire_sparkle.py \
  --destination /absolute/path/to/sparkle-2.9.4 \
  --validate-root

python3 platform/macos/focus_macos.py plan \
  --source-root /absolute/path/to/chromium/src \
  --developer-dir /absolute/path/to/Xcode.app/Contents/Developer \
  --min-free-gib 70 \
  --update-mode autoupdate \
  --json

python3 platform/macos/write_autoupdate_args.py \
  --source-root /absolute/path/to/chromium/src \
  --architecture arm64 --json
python3 platform/macos/write_autoupdate_args.py \
  --source-root /absolute/path/to/chromium/src \
  --architecture arm64 --execute --json

python3 platform/macos/write_autoupdate_args.py \
  --source-root /absolute/path/to/chromium/src \
  --architecture x64 --json
python3 platform/macos/write_autoupdate_args.py \
  --source-root /absolute/path/to/chromium/src \
  --architecture x64 --execute --json
```

`flags.arm64.autoupdate.gn` and `flags.x64.autoupdate.gn` are identical except
for `target_cpu`. The writer consumes only those validated canonical Auto
profiles, writes one architecture at a time through an owner-only private
candidate, and publishes with a same-directory no-replace link. Run each pair
exactly once before `gn gen`; an existing `args.gn`, symlinked parent, changed
profile hash, or retry is a hard failure. An already-started output is resumed
through Ninja and the later seal, never by replacing its `args.gn`. The manual
profiles remain unchanged and updater-free.

The completed dependency root is accepted only with the real
`Sparkle.framework`, the pinned `LICENSE`, the original pinned
`Sparkle-2.9.4.tar.xz`, and canonical `SPARKLE-DEPENDENCY.json`. Validation
rechecks the retained archive's exact bytes/SHA-256, the exact top-level and
release-tool inventories, every file mode in the framework receipt, every
pinned framework/tool product hash and executable mode, universal slices,
Sparkle signature, and symlink inventory. Extra files, a non-`0755` Mach-O, or
group/world-write permission fail closed. Keep that owner-only `0700` root
until the final DMG and appcast pass provenance acceptance.

## Acceptance and release order

`autoupdate_contract.py` is the low-level universal-app gate used by the
receipt-bound release driver:

```sh
python3 platform/macos/autoupdate_contract.py \
  '/absolute/path/Focus Browser.app' \
  --sparkle-source-root /absolute/path/to/sparkle-2.9.4
```

This local bundle/DMG gate deliberately reports
`release_gate.update_e2e_verified = false` and
`update_e2e_required_for_public_release = true`: local packaging does not
pretend that an update was installed. Public release is fail-closed. In the
same GitHub Actions job, after acquiring the pinned dependency and before the
DMG verifier or Pages deployment, `sparkle_update_e2e.py` builds two tiny
synthetic bundles, serves an ephemeral Ed25519-signed feed and archive only on
`127.0.0.1`, and lets the real pinned Sparkle framework replace and relaunch
`1.0.5.0` as `1.0.6.0`. Its owner-only canonical receipt is bound to the exact
updater patch, harness, and Sparkle framework; `verify_public_macos_dmg.py`
rejects a missing, stale, altered, non-canonical, or non-private receipt before
mounting the public DMG. This test never uses the production private key, login
Keychain, public network, or an installed Focus Browser application.

It rejects a wrong feed/key or policy, any ChromiumUpdater/Keystone artifact,
non-universal or non-executable Mach-O, unexpected symlink or nested app,
visible helper, wrong Sparkle build, or Chromium icon. It inspects every
architecture's Mach-O deployment target, exact ad-hoc CodeDirectory flags, the
sole per-slice Sparkle load path/RPATH, and an exact entitlement dictionary for
every signed product. The app has the seven approved device/privacy
entitlements plus Library Validation disablement; Renderer and GPU have only
JIT plus Library Validation disablement; the other four loaders have only
Library Validation disablement; Framework, Crashpad, and dylibs have no
entitlements. Extra keys and non-`true` values fail closed. Chromium products
must target exactly macOS 12.0; the official Sparkle slices may target an older compatible
version but never newer than 12.0. The main app and Alerts helper must both use
the canonical Focus icon. The mandatory `--sparkle-source-root` also requires
the embedded framework subtree to be byte-for-byte and mode-for-mode identical
to the validated dependency receipt. Dependency validation additionally rejects
foreign ownership, file flags, hardlinked files/symlinks, extended ACLs, and
every extended attribute except macOS's canonical protected
`com.apple.provenance` value.

For a release candidate, do not invoke the low-level packager directly or skip
runtime acceptance. After the completed `prepare-auto` through `sign` receipt
chain, run each mandatory boundary first as a dry-run and then with
`--execute`:

```sh
python3 platform/macos/autoupdate_release.py accept \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/path/to/sparkle-2.9.4 --json
python3 platform/macos/autoupdate_release.py accept \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/path/to/sparkle-2.9.4 --execute --json

python3 platform/macos/autoupdate_release.py package \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/path/to/sparkle-2.9.4 \
  --dmg-output /absolute/path/FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg \
  --json
python3 platform/macos/autoupdate_release.py package \
  --source-root /absolute/path/to/chromium/src \
  --sparkle-source-root /absolute/path/to/sparkle-2.9.4 \
  --dmg-output /absolute/path/FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg \
  --execute --json
```

The source app, isolated staged copy, and app mounted back from the new DMG
must have the same descriptor-pinned exact tree digest (contents, paths,
symlink targets, ownership, modes, flags, hardlink counts, xattrs, and extended
ACLs) and each
pass exact architectures, executable modes, deployment targets,
per-slice CodeDirectory/entitlement policy, deep/strict code-signature
verification, Sparkle metadata, and dependency provenance. Attach/detach calls
are bounded; a failed normal detach is followed by one forced attempt, and an
unprovable detach retains its private mount root. A failure leaves no final
DMG.

Release order is strict:

1. finish and test the universal app;
2. package the final DMG and freeze its exact size and SHA-256;
3. generate and independently validate `appcast-macos.xml` with
   `prepare_sparkle_appcast.py` and Sparkle's Keychain-backed `sign_update`,
   then create the exact two-entry checksum inventory with the helper's
   non-signing `generate-checksums` mode;
4. create a draft GitHub Release and upload the fixed payload, checksum, and
   audit copy of the appcast; verify the draft inventory through authenticated
   release metadata and against the already-frozen local bytes;
5. publish and lock the non-Latest macOS prerelease without changing its three
   assets;
6. run `publish-macos-appcast.yml`, which first downloads all three now-public
   assets and verifies them byte-for-byte, then publishes the signed feed last
   while preserving the separate Windows `appcast-x64.xml`.

The public-release verifier does not mount a mutable download pathname or
assume descriptor-path support in `hdiutil`. It copies the already-opened DMG
into a `0600` file under an owner-only private root, fsyncs it, requires
identical size and SHA-256 while rebinding the original, and mounts only that
verified private copy read-only.

The Windows feed workflow may publish only while the production
`appcast-macos.xml` is absent. Once that feed exists, the Windows workflow fails
closed and requires a future coordinated cross-platform Pages workflow that
cryptographically revalidates and preserves both channels. It never republishes
a merely structural copy of the macOS feed or its landing page. The first macOS
feed and page are staged together by the macOS workflow.

Do not publish the feed before its payload. Do not use
`releases/latest/download`: the appcast enclosure points to the exact versioned
asset URL. Developer ID signing and Apple notarization are separate from the
Sparkle Ed25519 signature; an ad-hoc build must be described honestly as
ad-hoc and non-notarized.

Both signing modes of `prepare_sparkle_appcast.py` require the same completed
dependency root and its exact tool path:

```text
--sparkle-source-root /absolute/path/to/sparkle-2.9.4
--sparkle-tool /absolute/path/to/sparkle-2.9.4/bin/sign_update
```

After the final appcast has passed both local validation modes, create the
upload checksum file without Keychain or network access:

```sh
python3 platform/macos/prepare_sparkle_appcast.py generate-checksums \
  --payload /absolute/release/FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg \
  --appcast /absolute/release/appcast-macos.xml \
  --output /absolute/release/SHA256SUMS-macOS-1.0.6.txt
```

This mode accepts only those three exact basenames, opens and rebinds both
inputs by descriptor, emits exactly two lowercase `SHA-256`, two-space,
basename lines, and publishes an owner-only `0600` output without replacement.
It neither signs nor contacts the network.

The helper revalidates the complete dependency receipt, exact pinned tool
hash and inode, copies that binary into an owner-only private directory, makes
that directory non-writable while the tool is usable, and
revalidates the copy before and after every Keychain-backed invocation. The
accepted appcast inode is placed with no-overwrite descriptor/inode checks and
is signature-validated again at its final path. The transports are deliberately
operation-specific: archive signing and its immediate verification during feed
generation use the already-open payload through inherited `/dev/fd`; feed
signing uses an owner-private candidate pathname because Sparkle atomically
replaces that leaf; feed verification uses a byte-identical owner-private copy.
Descriptor/inode/hash checks and an independent Ed25519 verification bracket
the pathname-based operations. A committed local-placement cleanup failure
retains its private transaction root instead of recursively deleting recovery
state.
