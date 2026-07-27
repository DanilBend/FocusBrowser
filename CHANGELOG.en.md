# Changelog

<p><a href="CHANGELOG.md">Русский</a> · <strong>English</strong></p>

## 1.0.4 — 2026-07-27

- The browser interface now uses one consistent dark theme: light and system
  appearance controls are removed, including for existing profiles.
- Wallpaper selection remains available in the compact new-tab customization
  panel.
- Increased the usable size of the product icon on the taskbar, desktop and in
  the installer; the mark remains crisp at system sizes from 16 through 256 px.
- Strengthened bookmark and history import stability: first-run setup and
  browser settings now use one consistent native import route.
- Fixed regressions in the FocusYoutube address-bar button, with repeatable
  coverage for startup, navigation and restored tabs.
- Advanced the installer and updater to the monotonic `1.0.4.0` version. An
  update is accepted only after mandatory feed-signature and artifact-metadata
  validation.
- Expanded pre-release checks for version metadata, installation, profile
  preservation and reinstall behavior. Publication remains blocked until all
  quality gates pass.

### Windows x64 artifacts

- `FocusBrowser_1.0.4_x64-installer.exe` — SHA-256
  `1F54D5486E763214C3A319BD6CCB48E55D875DA237AF5458F5A1BF3BFFDF112E`
- `FocusBrowser_1.0.4_x64-mini-installer.exe` — SHA-256
  `45F2B8A496FFE2478C360C3AE56679553EFCD52C78EE541812573EC8E4A5B5FA`
- `FocusBrowser_1.0.4_x64-windows.zip` — SHA-256
  `550D5E77FCFA30E1C56911C76549F34C507FC55BBC8EB0A7D80EC58A36B5EAE5`

## 1.0.3 — 2026-07-25

- Fixed FocusYoutube visibility: its icon now appears automatically after
  opening, navigating to, or restoring a supported page.
- The icon remains visible after address-bar state updates; toggling
  FocusYoutube off and back on in toolbar settings is no longer required.
- Added regression coverage for stable icon visibility after navigation
  commits.

## 1.0.2 — 2026-07-25

- FocusBlock's native engine was updated while retaining its interface, global
  and per-site controls, and blocked-request counters.
- FocusYoutube now has a stable native crossed-out video icon inside the
  address bar and 25 essential controls.
- Added a native next-launch prompt for discovered updates: **Update now**,
  **Remind later**, or skip that exact version. The production feed is published
  separately as part of the release process.
- Restored **Add shortcut** and **Customize this page** on the new tab without
  adding unrelated elements to the main surface.
- Expanded regression QA for the blocking engine, FocusYoutube readiness,
  new-tab geometry and update-prompt lifetime.

### Windows x64 artifacts

- `FocusBrowser_1.0.2_x64-installer.exe` — SHA-256
  `E58E0579E46641A5E88E7FA92556189EA21815B1B5EE6221EBF1C102321B0B5C`
- `FocusBrowser_1.0.2_x64-mini-installer.exe` — SHA-256
  `A4ADF9D049182F4D2C282CF57BEFCB926B58F1C3A74C1A99329C61A4D0C47E2E`
- `FocusBrowser_1.0.2_x64-windows.zip` — SHA-256
  `53574E9546AA9A846868850C1DF62FAF760655664A7BD38A8660C972513AE9EC`

## 1.0.1 — 2026-07-22

- Text entry now follows a crisp document-editor model: each glyph is painted
  at its final position immediately, without a translucent layer, fractional
  offset or blur; Backspace/Delete no longer shifts a neighbouring glyph.
- Added a smooth 110 ms caret glide without delaying the committed text, DOM,
  IME or accessibility state.
- Fixed IME handling: provisional composition text no longer animates
  repeatedly, while committed text receives one correct paint update.
- Fixed a critical crash after successful sign-in on mail, video and other
  sites: the native save-password prompt no longer closes the browser.
- The smooth-motion preference and the operating system reduced-motion setting
  immediately disable glyph and caret motion.
- Fixed a FocusYoutube startup race: its button appears immediately on supported
  video pages, while the native panel waits safely for the built-in component
  and remains open during the initial navigation.
- Made the repository and its templates bilingual: updated the README,
  changelog, contributing, privacy and security documentation, plus Issue and
  Pull Request templates in Russian and English.
- Expanded automated and visual QA for typing, paste, deletion, caret motion,
  IME, the address bar and FocusYoutube readiness.
- Strengthened installer and update validation: repeat installation preserves
  the browser profile, verifies Focus Browser branding and does not terminate
  unrelated processes.

### Windows x64 artifacts

- `FocusBrowser_1.0.1_x64-installer.exe` — SHA-256
  `F8F98C1F4FFB8E43F9E1DD05BE6B2D940F1A6F34EE5F3BAF00D800DD36597A91`
- `FocusBrowser_1.0.1_x64-mini-installer.exe` — SHA-256
  `B4EFEC7E4448E9D27F946E3043E81483C1B813AA4EB62F0A14D48861008C09B1`
- `FocusBrowser_1.0.1_x64-windows.zip` — SHA-256
  `FBF87CF2520AB375B6C7313456679ADAB069B42C71021046FD7B73D1D5FE5941`

## 1.0.0 — 2026-07-21

- Initial public Windows x64 release of Focus Browser.
- Full Focus Browser branding with transparent monochrome target icon.
- Native FocusBlock protection with a simple Russian control surface and a
  shield at the right edge inside the address bar.
- Built-in FocusYoutube panel with 20 essential distraction controls.
- Russian first-run setup, bookmark/history import from a detected standard
  profile of a compatible browser, and shortcut selection.
- Minimal new-tab page containing only search and shortcuts.
- Optional smooth interface and text-input animations.
- Meditation page in the main menu with direct opening of the configured
  external video.
- Built-in password manager, extension installation from a compatible catalog,
  proprietary codecs and protected-media integration hooks.
- Installer/update path preserves an existing Focus Browser profile.

### Windows x64 artifacts

- `FocusBrowser_1.0_x64-installer.exe` — SHA-256
  `7EB762A29A0403ABACA7EA74694F7F05486A2A16616EE808661F10C719B0D311`
- `FocusBrowser_1.0_x64-mini-installer.exe` — SHA-256
  `3CB26E0D10CB20547F3BB5B699C13B240A8AFA0B9CD3ADA356C94F1AA7F1783E`
- `FocusBrowser_1.0_x64-windows.zip` — SHA-256
  `4A6C3CA0B550A4DE2EFFAB43322C006059D8A16A3E6A7320185BB58C891BF4FF`

### Source-build dependency

- `onboarding-page-202607132006-focus1.tar.gz` — SHA-256
  `DDB5F5E375412DC987581103D8C64A59144097A084AB3C49166A95AFEEA230D7`

This archive is used only by `build.py`; it is not an installer. It is
published in the separate build-dependencies prerelease so the main Focus
Browser 1.0 download list contains only end-user packages and checksums.

The Windows binaries do not have a publisher signature. Verify the hashes before
installation; the system may show an **Unknown publisher** warning.
