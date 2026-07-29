# Universal Focus Browser for supported Macs

The compatibility target is Chromium 150's supported Mac range, not every Mac
ever manufactured:

- macOS 12 Monterey or later;
- Intel `x86_64` Macs capable of running macOS 12;
- Apple Silicon `arm64` Macs;
- one universal application and DMG after both native slices pass acceptance.

PowerPC, 32-bit Intel, and macOS 11 or older are outside the Chromium 150
runtime contract. Shipping an older Chromium to reach those systems would
withhold current browser security fixes and is not part of this port.

Both GN profiles pin `mac_deployment_target="12.0"`,
`mac_min_system_version="12.0"`, and `use_system_xcode=true`. They differ only
where an architecture-specific value is required. The current Xcode 27 SDK
advertises both `x86_64` and `arm64` and a minimum macOS deployment target of
12.0. The planner verifies Xcode 27.0 build `27A5228h` and SDK 27.0 build
`26A5388f` directly from plist evidence without changing `xcode-select` or
executing Xcode. Chromium 150's upstream official SDK is 26.5 build `25F70`, so
the installed newer beta is identity-validated but not build-proven.

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
5. verify the complete bundle, both main executable slices, native launch, and
   the Incognito/FocusBlock/FocusYoutube acceptance matrix before packaging;
6. create one drag-and-drop DMG from the accepted universal app with
   `package_local_dmg.py --require-universal`.

Do not replace Chromium's universalizer with a recursive hand-written `lipo`
loop. It handles parallel bundle trees, property-list differences, symlinks,
permissions, and Mach-O alignment rules expected by Chromium.

## Runtime matrix

At minimum, release acceptance requires a native Apple Silicon run and a native
Intel run, plus a macOS 12 launch check and a current-macOS launch check. Rosetta
is useful for an extra x64 smoke test on Apple Silicon but does not replace an
Intel Mac. Until these runs exist, compatibility remains planned rather than
runtime-verified.

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
