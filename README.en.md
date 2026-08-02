<p align="right"><a href="README.md">Русский</a> · <strong>English</strong></p>

<p align="center">
  <img src="focus-chromium/resources/branding/product_logo.svg" width="96" alt="Focus Browser logo">
</p>

<h1 align="center">Focus Browser</h1>

<p align="center">
  A Chromium-based browser for focused work on Windows and macOS.
</p>

<p align="center">
  <a href="https://github.com/DanilBend/FocusBrowser/releases">Download Windows</a>
  · <a href="docs/MACOS.md#english">Focus Browser for macOS</a>
  · <a href="https://github.com/DanilBend/FocusBrowser/issues">Report a bug</a>
  · <a href="docs/RELEASING.md">Windows release guide</a>
</p>

## Focus Browser 1.0.5

Focus Browser uses the Chromium engine while reducing distractions
and integrating focus tools directly into the browser interface. The 1.0.5
source version is based on Chromium 150.0.7871.128 and targets 64-bit Windows 10
and Windows 11. The repository also contains a separate native port for macOS
12 and later: one local universal build for Apple Silicon and 64-bit Intel
Macs.

### Main features

- **FocusBlock** — built-in, non-removable native ad and tracker protection. It
  is enabled by default, can be disabled globally or per site, and its shield
  is located on the right inside the address bar.
- **Distraction-free video** — a built-in address-bar panel with 25 local
  controls for the supported video site. Individual filters are off by default
  and no external settings site is required.
- **Clean new tab** — search, user-selected shortcuts, an add-shortcut button
  and a compact page-customization button.
- **Russian first-run setup** — search and appearance choices, bookmarks and
  history import from a detected supported local profile, password manager,
  default-browser flow and optional shortcuts.
- **Smooth motion** — one switch for interface and text-entry motion. Operating
  system reduced-motion settings always take priority.
- **Meditation** — a page in the main menu that opens the selected video in a
  normal tab without an embedded player.
- Built-in password manager, compatible extension installation, proprietary
  media codecs and DRM integration hooks.
- **Signed updates** — Windows uses its separate WinSparkle channel, while the
  universal macOS build uses Sparkle 2 and a separate Ed25519-signed appcast.
  macOS checks once per day and retains a manual check on the About page.

## Download and installation

### Windows

Download the installer or portable archive from
[Releases](https://github.com/DanilBend/FocusBrowser/releases).

The 1.0.5 installer is not Authenticode-signed, so Windows SmartScreen may show
**Unknown publisher**. Verify the SHA-256 values published with each release
before running the installer.

Installing a newer build over an existing Focus Browser installation preserves
the browser profile. Version 1.0.5 includes a next-launch update prompt, but it
offers installation only through a published, Ed25519-signed production
channel at
`https://danilbend.github.io/FocusBrowser/appcast-x64.xml`. If the channel is
temporarily unavailable, use a manual GitHub Releases installation.

### macOS

The separate macOS port produces an ad-hoc-signed universal DMG for Apple
Silicon and 64-bit Intel Macs running macOS 12 or later. It is not
Apple-notarized.

The new build embeds Sparkle 2.9.4, never ChromiumUpdater or Keystone. The
archive and appcast require a dedicated Ed25519 signature, and the feed is
pinned to `https://danilbend.github.io/FocusBrowser/appcast-macos.xml`. The
previously accepted manual DMG remains a fallback. Because that older build
does not contain Sparkle, the first move to the updater-capable build must be a
manual installation from its specific
[GitHub Release](https://github.com/DanilBend/FocusBrowser/releases). See
[Focus Browser for macOS](docs/MACOS.md#english) for current status and
local-DMG limitations.

## Privacy and external services

- FocusBlock filtering runs locally.
- Settings and filtering for the supported video site run locally and include
  no analytics, payments, donations or remote-font flows.
- The meditation page loads no embedded player. Opening the video uses a normal
  tab, where the destination site's policies apply.
- Account sync is unavailable in this build. Focus Browser can instead import
  bookmarks and history locally from a detected supported profile; this
  importer does not transfer passwords or autofill data.
- DRM support is compiled in, but the proprietary module is not stored in this
  repository and may be obtained through the browser's component updater.
  Chromium component updates are not macOS application updates.

See [PRIVACY.en.md](PRIVACY.en.md) for component-level notes.

## Building on Windows

The full Chromium checkout and compiled output are intentionally excluded from
Git. A clean build requires substantial disk space and memory.

1. Install Visual Studio 2022 with **Desktop development with C++**, Windows
   SDK, Python 3.12+, Git and 7-Zip.
2. Enable Windows long paths.
3. Run from Developer PowerShell for VS 2022:

```powershell
git clone https://github.com/DanilBend/FocusBrowser.git
cd FocusBrowser
python build.py
python package.py --cpu-arch 64bit
```

Output is written under `build/`. Release details are in
[docs/RELEASING.md](docs/RELEASING.md), and the manual checklist is in
[build_support/FOCUS_RELEASE_QA.md](build_support/FOCUS_RELEASE_QA.md).

## Building on macOS

The native macOS port builds separate `arm64` and `x86_64` slices from one
Chromium tree, merges them into a universal application, and creates a local
DMG. It does not touch Windows/Android or publish the result. See
[platform/macos/README.md](platform/macos/README.md) for the contracts and exact
stage order.

## Repository layout

- `focus-chromium/` — Chromium configuration, branding and patch set.
- `patches/focus/` — Windows integration and updater patches.
- `source_overrides/` — files overlaid onto a Chromium checkout.
- `installer/` — Focus Browser NSIS installer.
- `platform/macos/` — native universal build and local-DMG packaging for macOS.
- `build.py`, `package.py` — source preparation, build and packaging.
- `build_support/`, `qa/` — release validation.

## Licensing

Project-specific GPL-covered code is licensed under GPL-3.0; see
[LICENSE](LICENSE). Chromium and imported components retain their original
licenses. Component-specific terms are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Terms and mandatory notices for bundled third-party components remain available
in their license files and in
[Third-party notices](THIRD_PARTY_NOTICES.md).

## Contributing

Read [CONTRIBUTING.en.md](CONTRIBUTING.en.md) before opening a pull request.
Report security issues according to [SECURITY.en.md](SECURITY.en.md), without
publishing sensitive details in a public issue.
