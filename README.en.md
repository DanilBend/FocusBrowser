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

## Browser comparison

Focus Browser does not try to replace every specialized tool. Its main
difference is that ad protection, YouTube distraction controls, and a calm work
environment are delivered together without requiring a stack of extensions.

| Capability | **Focus Browser 1.0.3** | Helium | Zen Browser | Firefox | Brave | Chrome | Edge |
|---|---|---|---|---|---|---|---|
| Full ad and tracker blocking without an extension | **✅ FocusBlock, on by default** | ✅ Built in and on by default | ◐ Tracking protection, not a full ad blocker | ◐ Enhanced Tracking Protection, not a full ad blocker | ✅ Shields is on by default | ◐ Blocks intrusive ads only | ◐ Tracking Prevention, not a full ad blocker |
| Desktop YouTube distraction controls | **✅ 25 local toggles in an address-bar panel** | — No dedicated panel documented | — No dedicated panel documented | — No dedicated panel documented | ◐ Several YouTube filters; some controls are mobile-only | — No dedicated panel documented | — No dedicated panel documented |
| Calm-interface tools | **✅ Clean new tab, smooth text entry, meditation** | Minimal layouts and Frameless Mode | Compact Mode, vertical tabs, and Workspaces | Reader View, sidebar, and Focus Timer | Speedreader and vertical tabs | Reading Mode and vertical tabs | Immersive Reader and vertical tabs |
| Extension ecosystem | **Chrome Web Store** | Chromium extensions | Firefox Add-ons | Firefox Add-ons | Chrome Web Store | Chrome Web Store | Edge Add-ons and Chrome Web Store |
| Browser-data cloud sync | **— Local Chrome bookmark and history import** | — Not available yet | ◐ Mozilla Account; Zen window layout sync is currently same-device only | ✅ Firefox Sync | ✅ Brave Sync | ✅ Google Sync | ✅ Microsoft Sync |
| Browser project source | **✅ Open** | ✅ Open | ✅ Open | ✅ Open | ✅ Open | ◐ Chromium is open, but Chrome as a whole is not | ◐ Chromium is open, but Edge as a whole is not |
| Primary availability | **Windows 10/11, x64** | Desktop, beta | Desktop, beta | Desktop and mobile | Desktop and mobile | Desktop and mobile | Desktop and mobile |

**Focus Browser's clearest advantage:** FocusBlock and 25 FocusYoutube controls
behave as one part of the browser. Of the products listed, Brave also documents
special YouTube filters, but their set and availability vary by platform;
Focus Browser provides one local Windows panel.

Legend: ✅ — a comparable capability is built in; ◐ — partial capability or a
different scope; — — no comparable built-in capability was found in official
product documentation. This comparison uses official public documentation
checked on July 26, 2026. Speed and memory use are intentionally not ranked
without one reproducible cross-browser benchmark.

<details>
<summary>Official comparison sources</summary>

- **Helium:** [features and FAQ](https://helium.computer/).
- **Zen Browser:** [protection](https://docs.zen-browser.app/security),
  [Compact Mode](https://docs.zen-browser.app/user-manual/compact-mode),
  [extensions](https://docs.zen-browser.app/user-manual/extensions), and
  [window sync](https://docs.zen-browser.app/user-manual/window-sync).
- **Firefox:** [tracking protection](https://support.mozilla.org/en-US/kb/enhanced-tracking-protection-firefox-desktop),
  [sidebar and vertical tabs](https://support.mozilla.org/en-US/kb/use-sidebar-access-tools-and-vertical-tabs),
  [Focus Timer](https://www.firefox.com/en-US/features/focus/), and
  [Firefox Sync](https://support.mozilla.org/en-US/kb/sync).
- **Brave:** [core features](https://brave.com/features/) and
  [YouTube filters](https://brave.com/youtube-ad-blocker/).
- **Chrome:** [intrusive-ad filtering](https://support.google.com/chrome/answer/7632919),
  [Reading Mode](https://support.google.com/chrome/answer/14218344),
  [tab management](https://support.google.com/chrome/answer/2391819),
  [sync](https://support.google.com/chrome/answer/165139), and
  [Chrome versus Chromium](https://chromium.googlesource.com/chromium/src/+/main/docs/chromium_browser_vs_google_chrome.md).
- **Edge:** [tracking prevention](https://support.microsoft.com/en-US/edge/learn-about-tracking-prevention-in-microsoft-edge),
  [Immersive Reader](https://support.microsoft.com/en-US/edge/use-immersive-reader-in-microsoft-edge),
  [vertical tabs](https://www.microsoft.com/en-us/edge/features/vertical-tabs), and
  [sync](https://support.microsoft.com/en-US/edge/sign-in-to-sync-microsoft-edge-across-devices).

</details>

Third-party product names belong to their respective owners.

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
