<p align="center">
  <img src="focus-chromium/resources/branding/product_logo.svg" width="96" alt="Focus Browser logo">
</p>

<h1 align="center">Focus Browser</h1>

<p align="center">
  A Chromium-based Windows browser built for focused work.
</p>

<p align="center">
  <a href="https://github.com/DanilBend/FocusBrowser/releases/latest">Download</a>
  · <a href="https://github.com/DanilBend/FocusBrowser/issues">Report a bug</a>
  · <a href="docs/RELEASING.md">Release guide</a>
</p>

## Focus Browser 1.0

Focus Browser keeps the familiar Chromium engine while reducing distractions
and integrating the controls that are normally spread across extensions.
Version 1.0 is based on Chromium 150.0.7871.128 and targets 64-bit Windows 10
and Windows 11.

### Main features

- **FocusBlock** — built-in, non-removable content blocking based on uBlock
  Origin. It is enabled by default, can be disabled globally or per site, and
  has a compact browser-native control immediately to the left of the address
  bar.
- **FocusYoutube** — 24 essential YouTube distraction controls in one local
  panel. Individual filters are off by default and no external settings site
  is required.
- **Clean new tab** — search and selected shortcuts only.
- **Russian first-run setup** — search provider, Chrome data import, password
  manager, address-bar appearance, default browser and optional shortcuts.
- **Smooth interface motion** — subtle transitions and ambient water-like
  motion, with switches in first-run setup and Settings. The operating
  system's reduced-motion preference always takes priority.
- **Meditation page** — available from the main menu and opens the selected
  YouTube video directly, avoiding embedded-player error 153.
- Chromium password manager, Chrome Web Store extension installation,
  proprietary media codecs and Widevine integration hooks.

## Download and installation

Download the installer or portable archive from
[Releases](https://github.com/DanilBend/FocusBrowser/releases).

The 1.0 installer is not Authenticode-signed. Windows SmartScreen may therefore
show **Unknown publisher**. Verify the SHA-256 values published with each
release before running the installer.

Installing a newer build over an existing Focus Browser installation preserves
the browser profile. Production automatic updates are not enabled in the 1.0
binary; install a newer GitHub release manually until a signed WinSparkle feed
is configured.

## Privacy and external services

- FocusBlock filtering runs locally.
- FocusYoutube settings and filtering run locally and do not include analytics,
  payment, donation or remote-font flows.
- The meditation page loads no embedded player; selecting the video opens
  YouTube in a normal tab, where YouTube receives the usual connection data.
- Google Chrome account Sync is not enabled because Google does not issue
  Chrome Sync credentials to third-party Chromium distributions. One-click
  local import from Chrome is available.
- Widevine support is compiled in, but the proprietary CDM is not distributed
  in this repository and may need to be downloaded by Chromium's component
  updater.

See [PRIVACY.md](PRIVACY.md) for the component-by-component notes.

## Building on Windows

The full Chromium checkout and compiled output are intentionally not stored in
Git. A clean build requires significant disk space and memory.

1. Install Visual Studio 2022 with the Desktop development with C++ workload,
   Windows SDK, Python 3.12+, Git and 7-Zip.
2. Enable Windows long paths.
3. Clone this repository and run from a Developer PowerShell for VS 2022:

```powershell
git clone https://github.com/DanilBend/FocusBrowser.git
cd FocusBrowser
python build.py
python package.py --cpu-arch 64bit
```

Build output is written under `build/`. Detailed release and updater notes are
in [docs/RELEASING.md](docs/RELEASING.md), and the manual release checklist is
in [build_support/FOCUS_RELEASE_QA.md](build_support/FOCUS_RELEASE_QA.md).

## Repository layout

- `focus-chromium/` — vendored Chromium configuration, branding and patch set.
- `patches/focus/` — Windows integration and updater patches.
- `source_overrides/` — files overlaid onto a Chromium checkout.
- `installer/` — Focus Browser NSIS installer.
- `build.py` and `package.py` — source preparation, build and packaging tools.
- `build_support/` and `qa/` — release validation scripts.

## Licensing

Project-specific GPL-covered code is licensed under GPL-3.0; see
[LICENSE](LICENSE). Chromium and imported projects retain their original
licenses. Important component-specific terms are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

In particular, FocusBlock is derived from uBlock Origin and remains GPL-3.0.
FocusYoutube contains material derived from Unhook and is distributed by the
project owner under separate written permission; it is **not** relicensed under
this repository's GPL-3.0 terms.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Security
issues should be reported according to [SECURITY.md](SECURITY.md), not through a
public issue.
