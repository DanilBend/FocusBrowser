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

## Runtime matrix

At minimum, release acceptance requires a native Apple Silicon run and a native
Intel run, plus a macOS 12 launch check and a current-macOS launch check. Rosetta
is a required local x64 smoke on this Apple Silicon build host but does not
replace an Intel Mac. The automated gate therefore catches architecture,
linker, signing, launch, Incognito, and packaging regressions in both slices;
physical Intel and macOS 12 runs remain separate compatibility evidence.

The word "universal" proves that the accepted application contains both native
Mach-O slices; it does not claim testing on every Mac model or macOS release.
The macOS application updater is disabled in both slices. Any future GitHub
Release update is a manual DMG download and replacement, not an appcast or
Sparkle flow.

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
