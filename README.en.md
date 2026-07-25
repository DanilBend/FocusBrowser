<p align="right"><a href="README.md">Русский</a> · <strong>English</strong></p>

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

## Focus Browser 1.0.3

Focus Browser keeps the familiar Chromium engine while reducing distractions
and integrating focus tools directly into the browser interface. Version 1.0.3
is based on Chromium 150.0.7871.128 and targets 64-bit Windows 10 and Windows
11.

### Main features

- **FocusBlock** — built-in, non-removable native ad and tracker protection. It
  is enabled by default, can be disabled globally or per site, and its shield
  is located on the right inside the address bar.
- **FocusYoutube** — a stable crossed-out YouTube icon inside the address bar
  and 25 essential local controls. Individual filters are off by default and
  no external settings site is required.
- **Clean new tab** — search, user-selected shortcuts, an add-shortcut button
  and a compact page-customization button.
- **Russian first-run setup** — search and appearance choices, Chrome data
  import, password manager, default-browser flow and optional shortcuts.
- **Smooth motion** — one switch for interface and text-entry motion. Operating
  system reduced-motion settings always take priority.
- **Meditation** — a page in the main menu that opens the selected video
  directly on YouTube, avoiding embedded-player error 153.
- Chromium password manager, direct Chrome Web Store extension installation,
  proprietary media codecs and Widevine integration hooks.
- **Non-disruptive updates** — a discovered update is offered on the next
  launch, with options to update now, remind later or skip that exact version.

## Download and installation

Download the installer or portable archive from
[Releases](https://github.com/DanilBend/FocusBrowser/releases).

The 1.0.3 installer is not Authenticode-signed, so Windows SmartScreen may show
**Unknown publisher**. Verify the SHA-256 values published with each release
before running the installer.

Installing a newer build over an existing Focus Browser installation preserves
the browser profile. Version 1.0.3 includes a next-launch update prompt, but it
offers installation only through a published, Ed25519-signed production
channel at
`https://danilbend.github.io/FocusBrowser/appcast-x64.xml`. If the channel is
temporarily unavailable, use a manual GitHub Releases installation.

## Privacy and external services

- FocusBlock filtering runs locally.
- FocusYoutube settings and filtering run locally and include no analytics,
  payments, donations or remote-font flows.
- The meditation page loads no embedded player. Opening the video uses a normal
  YouTube tab, where YouTube's policies apply.
- Google Chrome account Sync is unavailable to third-party Chromium
  distributions. Focus Browser can locally import bookmarks and history from
  a detected standard Chrome profile; this importer does not transfer Chrome
  passwords or autofill data.
- Widevine hooks are compiled in, but the proprietary CDM is not stored in this
  repository and may be obtained through Chromium's component updater.

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

## Repository layout

- `focus-chromium/` — Chromium configuration, branding and patch set.
- `patches/focus/` — Windows integration and updater patches.
- `source_overrides/` — files overlaid onto a Chromium checkout.
- `installer/` — Focus Browser NSIS installer.
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
