# Changelog

<p><a href="CHANGELOG.md">Русский</a> · <strong>English</strong></p>

## 1.0.0 — 2026-07-21

- Initial public Windows x64 release of Focus Browser.
- Full Focus Browser branding with transparent monochrome target icon.
- Native FocusBlock protection powered by `adblock-rust 0.13.2`, with a simple
  Russian control surface and a shield at the right edge inside the address bar.
- Built-in FocusYoutube panel with 20 essential distraction controls.
- Russian first-run setup, detected-profile Chrome bookmark/history import and
  shortcut selection.
- Minimal new-tab page containing only search and shortcuts.
- Optional smooth interface and text-input animations.
- Meditation page in the main menu with direct YouTube playback.
- Chromium password manager, Chrome Web Store installation path, proprietary
  codecs and Widevine integration hooks.
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

The Windows binaries are not Authenticode-signed. Verify the hashes before
installation; SmartScreen may show **Unknown publisher**.
