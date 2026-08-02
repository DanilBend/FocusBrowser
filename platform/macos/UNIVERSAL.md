# Universal Focus Browser for supported Macs

This target is macOS-only. It produces no iPhone, iPad, iOS, or iPadOS app.

The compatibility target is Chromium 150's supported Mac range, not every Mac
ever manufactured:

- macOS 12 Monterey or later;
- Intel `x86_64` Macs capable of running macOS 12;
- Apple Silicon `arm64` Macs;
- one local universal application and ad-hoc-signed DMG after both native
  slices pass acceptance.

PowerPC, 32-bit Intel, and macOS 11 or older are outside the Chromium 150
runtime contract. Shipping an older Chromium to reach those systems would
withhold current browser security fixes and is not part of this port.

Both GN profiles pin `mac_deployment_target="12.0"`,
`mac_min_system_version="12.0"`, and `use_system_xcode=true`. They differ only
where an architecture-specific value is required. The current Xcode 27 SDK
advertises both `x86_64` and `arm64` and a minimum macOS deployment target of
12.0. The planner verifies Xcode 27.0 build `27A5228h` and SDK 27.0 build
`26A5388f` directly from plist evidence without changing `xcode-select` or
executing Xcode. Chromium 150's upstream official SDK is 26.5 build `25F70`.
Tool and SDK identity validation is not runtime proof; each completed slice and
the final DMG must still pass the acceptance matrix below.

## Build and merge contract

Chromium does not use one all-encompassing universal GN configuration. Build
the exact same patched Chromium 150.0.7871.128 tree twice:

1. `target_cpu="arm64"` into `out/FocusMacArm64`;
2. `target_cpu="x64"` into `out/FocusMacX64`;
3. merge the two unsigned parallel app trees, x64 first and arm64 second, with
   the exact checkout's `chrome/installer/mac/universalizer.py` into
   `out/FocusMacUniversal/Focus Browser.app`;
4. apply the reviewed nested ad-hoc signing workflow while preserving the
   Chromium helper entitlements;
5. verify the complete bundle and signing matrix, then require a native arm64
   and Rosetta x86_64 offline-Incognito smoke before packaging;
6. create one drag-and-drop DMG from the accepted universal app with
   `package_local_dmg.py --require-universal` in a private candidate directory,
   mount and runtime-test that exact candidate read-only, then atomically
   place the accepted inode at the still-absent local final path.

Do not replace Chromium's universalizer with a recursive hand-written `lipo`
loop. It handles parallel bundle trees, property-list differences, symlinks,
permissions, and Mach-O alignment rules expected by Chromium.

Those paths describe the preserved updater-free/manual build, including the
historical `1.0.5` artifact. The `1.0.6` Auto build uses separate
`out/FocusMacArm64Auto` and `out/FocusMacX64Auto` thin trees and the staged
`autoupdate_release.py` `prepare-auto` -> `seal` -> `stage` -> `merge` ->
`sign` -> `accept` -> `package`
receipt chain. It must not reuse the manual output roots or receipts. Package
is unreachable until `accept` records the full Sparkle-provenance release gate,
exact per-slice signing/entitlement matrix, and passing native-arm64 plus
Rosetta-x86_64 offline Incognito-write/normal-read localStorage isolation
smokes. The addendum preserves the historical preparation receipt, while the
seal requires both completed pinned-Ninja graphs to report no work for the
exact `chrome` and `chrome/installer/mac:copies` targets. The same
x64-first/arm64-second
universalization and nested-signature acceptance rules apply.

## Runtime matrix

At minimum, release acceptance requires a native Apple Silicon run and a native
Intel run, plus a macOS 12 launch check and a current-macOS launch check. Rosetta
is a required local x64 smoke on this Apple Silicon build host but does not
replace an Intel Mac. The automated gate therefore catches architecture,
linker, signing, launch, Incognito, and packaging regressions in both slices;
physical Intel and macOS 12 runs remain separate compatibility evidence.

The word "universal" proves that the accepted application contains both native
Mach-O slices; it does not claim testing on every Mac model or macOS release.

There are now two deliberately distinct update profiles:

- the preserved `1.0.5` manual profile disables the application updater in
  both slices; updating that artifact means downloading and replacing the DMG;
- the `1.0.6.0` Auto profile embeds pinned Sparkle `2.9.4` in both slices and
  reads only
  `https://danilbend.github.io/FocusBrowser/appcast-macos.xml`. Its first
  installation is manual, after which Sparkle can perform the configured
  automatic and About-page checks.

The Auto candidate requires `autoupdate_contract.py` plus the complete
`package_local_dmg.py --require-universal --require-autoupdate` source,
staging, mounted-DMG, provenance, architecture, and deep/strict-signature
gates. It remains ad-hoc signed and non-notarized. Its prospective release is
the separate, non-Latest prerelease `v1.0.6-macos`; stable `v1.0.5` is not
mutated and the plain `v1.0.6` tag remains reserved for a future coordinated
stable release. This document records neither a final DMG size/hash nor a
published release or feed.

The Auto chain executes Chromium's universalizer and signer with the pinned
CIPD CPython 3.11.8 in isolated `-I -B` mode, authenticates its complete CIPD
manifest/runtime tree, and verifies the entire generated signing package before
and after every copy/execution boundary. The exact DMG candidate repeats both
runtime smokes from a read-only mount opened through an owner-only,
descriptor-verified pathname. The runtime gate prefers a same-inode hard link
and uses a fsynced read-only byte-for-byte private copy only when an allowlisted
filesystem limitation prevents linking; the packaging helper's earlier mount
uses its own same-inode private hard link. The original and mount inputs are
rebound before and after use. The accept receipt, app, Python tree, package
driver and candidate are rebound immediately before durable placement, and
final inode/size/hash are rebound around receipt commit. A detach that cannot
be proven retains the private candidate, and a post-commit failure never
removes the verified final DMG.

The exact upstream references are:

- Chromium 150
  [`build/config/mac/mac_sdk.gni`](https://chromium.googlesource.com/chromium/src/+/refs/tags/150.0.7871.128/build/config/mac/mac_sdk.gni),
  which pins both minimum values to 12.0 and is pinned locally to SHA-256
  `faab8ecd3da90f31bff07d03b847731e4e8d310a10d212720e320edafd946542`;
- Chromium's
  [`docs/mac_arm64.md`](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_arm64.md),
  which specifies separate x86_64 and arm64 builds followed by
  `chrome/installer/mac/universalizer.py`. The exact Chromium 150 universalizer
  is pinned locally to SHA-256
  `c514adedd2dbd04532d3ddd95ded3ec1bd129ba81570b1f68ddad2a21bed7ab0`.
