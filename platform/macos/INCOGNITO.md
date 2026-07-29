# Native Incognito contract for macOS

This port uses Chromium's native Off-The-Record profile. It does not emulate
private browsing in Swift, WebKit, or a second wrapper process.

## Source-level invariants

- File > New Incognito Window and `Command-Shift-N` dispatch
  `IDC_NEW_INCOGNITO_WINDOW` to `NewIncognitoWindow(profile())`.
- Incognito policy still controls whether the command is available; Guest mode
  does not expose the command.
- The default `Command-Shift-N` accelerator is locked on this macOS port so the
  custom-shortcut feature cannot remove it or assign it to another command.
- The runtime flag that could make `BrowserWidget` report a private window as
  regular is removed by the final macOS patch.
- A private/guest avatar marker remains visible even when the regular-profile
  avatar preference or `show-avatar-button=never` would hide it.
- `custom-ntp` cannot replace a macOS private new-tab page with an external,
  local, or internal override.
- FocusBlock requests its own OTR service instance. FocusYoutube and uBlock are
  enabled for private windows under Chromium's component-extension semantics;
  their storage separation is intentionally a runtime acceptance item.
- The macOS-only private NTP disclosure has reviewed RU and EN text. It states
  what the browser profile does not retain after all private windows close and
  warns that sites, networks, providers, and managed-device administrators may
  still observe activity.

The planner hashes the nine specifically reviewed common patches, twelve
selected final overlays, and the final four-target macOS patch. Its
authoritative body manifests additionally cover all 321 planned common patches
and all 2531 included regular overlay files; any body, count, symlink, or
special-file drift fails closed. The 59-patch and 72-overlay private-mode
subsets remain useful diagnostics, not claims that a heuristic can identify
every indirect behavior change. A pure-Python unified-diff parser rejects
malformed hunk counts before a patch is accepted.

Comment-free source sentinels check that the pinned upstream tree still
contains the expected OTR/history/session/storage control points. They are
static intent evidence only: the report explicitly does not turn them into
semantic or runtime proof.

## Runtime acceptance after a Chromium checkout exists

1. Build clean native arm64 and x86_64 apps from Chromium `150.0.7871.128`,
   merge them with that checkout's pinned universalizer, and sign the universal
   bundle with the reviewed local workflow.
2. Open a private window from both the File menu and `Command-Shift-N` in RU and
   EN. Verify policy states enabled, disabled, and forced.
3. Attempt to hide the avatar, reassign `Command-Shift-N`, enable the removed
   theme override, and configure external/local `custom-ntp`; the private
   identity and native NTP must remain.
4. Verify history is not recorded, Session Restore does not restore private
   tabs, and all OTR storage partitions are destroyed after the final private
   window closes.
5. Exercise FocusBlock, FocusYoutube, and uBlock in both regular and private
   windows. Confirm intended behavior and explicitly inspect whether extension
   storage is shared or separated.
6. Repeat native acceptance on Apple Silicon and Intel, with at least macOS 12
   and a current macOS host represented. Rosetta is supplemental, not a native
   Intel substitute.
7. Capture RU/EN private NTP and toolbar screenshots and inspect contrast,
   clipping, text scaling, keyboard access, and VoiceOver.

Until those checks run against a real app, the report remains
`runtime_verified: false`. The increased private storage quota is an
anti-detection choice with higher memory-pressure/availability risk; it is not
evidence that storage is ephemeral.
