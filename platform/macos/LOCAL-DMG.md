# Local Focus Browser app and DMG

The target is local installation on supported Intel and Apple Silicon Macs.
There is no App Store, Developer ID, or Apple notarization workflow. The
preserved `1.0.5` manual build contains no application updater; the separate
`1.0.6` Auto build embeds pinned Sparkle `2.9.4` and uses only the macOS feed.
Both local build/package paths remain ad-hoc signed and never publish an
artifact themselves.

A paid Apple Developer account is not required. Executable code in the final
bundle may use an ad-hoc signature, which has no certificate identity and is
suitable for local use. Chromium has multiple nested helper apps and
frameworks, so the build/package workflow must sign them in the correct order;
do not repair a broken bundle with a blanket `codesign --deep --force` command.

After the separate arm64 and x86_64 builds are merged and the complete
`Focus Browser.app` is signed, acceptance is:

1. Verify the complete nested signature with `codesign --verify --deep --strict
   --verbose=2`, then inspect both architecture CodeDirectories and
   entitlements for the seven Framework loaders, Crashpad, the Framework, and
   every dylib.
2. Before creating a DMG, launch the signed app natively as arm64 and through
   mandatory Rosetta as x86_64. For each slice, an Incognito launch writes a
   nonce to localStorage on a fixed offline `file:` origin and a normal launch
   using the same fresh profile must read it as absent; a separate normal/
   normal control profile must first prove persistence works under the same
   lifecycle. Bounded process-group cleanup is mandatory; either failure
   prevents packaging.
3. Place the verified app and an `/Applications` link in an isolated staging
   directory.
4. Run `package_local_dmg.py`; it stages with system `ditto`, creates a
   compressed drag-and-drop image with system `hdiutil`, verifies it, mounts the
   descriptor-pinned candidate inode read-only through an unpredictable
   same-inode hard link inside its owner-only inspection root, and revalidates
   the app and `/Applications` link. `hdiutil` receives that private pathname;
   the original descriptor, pathname, identity, metadata, and SHA-256 are
   rebound around the mount, and the private link is removed only after detach
   is proven. Source, staged, and mounted app trees must have the same exact
   descriptor digest, including file bytes, paths, symlinks, ownership, modes,
   flags, link counts, xattrs, and extended ACLs. The release invocation must
   include `--require-universal`; thin images are only for
   architecture-specific local testing.
5. Keep the packaged DMG unpublished inside an owner-only `0700` directory,
   mount that exact candidate read-only, and repeat both runtime smokes from its
   app. Hash the candidate before and after the mount. Only after acceptance and
   proven detach may the pipeline hard-link the accepted inode to the absent
   final path and remove the private link. This is an atomic no-overwrite local
   filesystem placement, not a GitHub release publication; a racing unrelated
   file is never replaced or removed.
6. A failed check removes only the exact candidate inode created by this run
   and leaves the final path absent. If neither normal nor forced detach can be
   proven, retain both the private backing candidate and its mount root for
   manual detach; never unlink a backing file while its detach state is
   unproven.
7. Record the app/DMG SHA-256, both runtime reports, and exact Chromium/Focus
   versions.

For the `1.0.6` Auto candidate, all seven base checks remain mandatory and are
supplemented by `autoupdate_contract.py` and
`package_local_dmg.py --require-autoupdate --sparkle-source-root ...`. The
Sparkle contract must pass for the signed source app, the packager's isolated
staged copy, and the app mounted from the finished DMG. It validates the
macOS-only feed/key/policy, version `1.0.6.0`, universal Sparkle products,
framework provenance, exact executable modes, per-slice deployment targets,
CodeDirectory flags and exact product-specific entitlement dictionaries, the
Focus Framework's sole Sparkle dependency/RPATH, application icons, forbidden
updater artifacts, and the complete nested signature. Auto packaging cannot
omit `--sparkle-source-root`; a report with null provenance is never a passing
release report. Every `hdiutil` call is bounded, normal detach has one forced
fallback, and a mount that still cannot be proven detached is retained rather
than recursively cleaned.

`autoupdate_release.py` supplies a fail-closed local `prepare-auto` -> `seal` ->
`stage` -> `merge` -> `sign` -> `accept` -> `package` receipt chain for the
Auto output roots. The addendum preserves and binds the historical preparation
receipt; the build seal requires pinned-Ninja no-work evidence for the exact
`chrome` and `chrome/installer/mac:copies` targets in both slices. The
mandatory `accept` receipt binds the current signed app and completed Sparkle
source root to the exact per-slice signing/entitlement matrix and successful
native-arm64 plus Rosetta-x86_64 offline Incognito-storage-isolation launches.
Each proof writes localStorage in Incognito and requires it absent in a normal
second launch using the same fresh profile. `package` cannot
consume a sign receipt directly. It repeats both launches from the exact
read-only-mounted private DMG candidate. That runtime mount receives an
owner-only, descriptor-verified pathname: a same-inode hard link when possible,
or a fsynced read-only byte-for-byte private copy after an allowlisted link
failure. The original and mount input are rebound before and after use. Only
the accepted candidate inode is placed at the final local path after a
last-moment accept/app/Python/driver rebind, then rebound around the sidecar.
It never invokes GN or a compiling Ninja command, publishes to GitHub,
notarizes, or uses a Developer ID, and every stage is a dry run unless
`--execute` is supplied.

The generated signing package is a complete exact allowlist across source,
staging, merge, and pre/post-sign boundaries, including imported Python hashes,
modes, ownership, symlinks, xattrs, ACLs, link counts, and generated
build-properties syntax. Universalization/signing/packaging use Chromium's
full-manifest-validated CIPD Python 3.11.8 under isolated `-I -B` execution
rather than
Apple Python 3.9. An unproven DMG detach retains the candidate; a failure after
durable final placement never deletes that verified DMG and reports a committed
recovery condition instead.

## Update profiles

The user-facing status and installation page is
[`docs/MACOS.md`](../../docs/MACOS.md).

### Preserved manual 1.0.5

Both manual macOS architecture profiles compile with the Chromium application
updater disabled. That app contains no macOS appcast, Sparkle feed, update
helper, or background application-update flow. The Windows Ed25519 appcast is
a Windows-only release mechanism and is not used by this port. Chromium
component updates, where available, are separate from updating
`Focus Browser.app`.

The current public Focus Browser Releases list has no macOS asset, and this
document does not announce one. After an accepted universal DMG is explicitly
published in a future official GitHub Release, updating remains manual:

1. Quit every running Focus Browser window and helper process.
2. Download the new universal DMG only from that official release.
3. Compare its SHA-256 with the checksum published for the same asset.
4. Open the DMG and replace the existing `Focus Browser.app` in Applications.
5. Launch the replacement and verify the displayed Focus Browser version.

There is no silent download, automatic restart, or in-app installation on
that manual profile.

### Separate 1.0.6 Auto prerelease

The updater-capable variant is bundle version `1.0.6.0` (displayed as
`1.0.6`). It uses Sparkle `2.9.4` and only
`https://danilbend.github.io/FocusBrowser/appcast-macos.xml`; ChromiumUpdater,
Keystone, and Google Updater remain disabled. The first Auto build must be
installed manually because `1.0.5` cannot discover Sparkle. Subsequent Auto
versions can use the signed macOS appcast for automatic checks or the manual
check in About.

The intended release channel is the separate non-Latest GitHub prerelease tag
`v1.0.6-macos`. It must not alter stable `v1.0.5`, and the plain `v1.0.6` tag
is reserved for a future coordinated stable release. The feed is published
only after the exact universal DMG, checksum, Sparkle signature, release asset,
and public byte-for-byte verification all pass. This document does not claim
that the prerelease/feed is published and does not invent a final size or hash.

The DMG container itself does not require an Apple account for local use. An
ad-hoc signature satisfies the native-code signature requirement but does not
establish a trusted developer identity. If another Mac receives the app or DMG
with a quarantine attribute, it will not pass the default Gatekeeper
assessment. The user may have to allow it manually in Privacy & Security, and
managed-device policy may prohibit that override. The normal no-manual-override
distribution path requires Developer ID signing and Apple notarization, which
is outside this local-only branch.

Ad-hoc signing must preserve every entitlement required by Chromium and its
nested helpers. "No Developer ID, provisioning, or notarization" never means
removing those entitlements.

Chromium checkouts, built applications, and DMGs are not stored in Git. A macOS
download must not be documented as available until the universal app and its
DMG have passed the applicable manual or full Auto acceptance matrix, their
exact size and SHA-256 have been recorded, and a separate authorized release
has actually published that asset. The local build/package and
`autoupdate_release.py` workflows perform none of those publishing steps.
