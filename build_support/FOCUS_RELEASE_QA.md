# Focus Browser 1.0.2 — Windows release QA

This checklist keeps every unrelated browser profile and process out of scope.
Never terminate every `chrome.exe`; identify Focus Browser by its full
executable path. All browser feature tests use a disposable `--user-data-dir`.

## 1. Read-only artifact gate

Before building, run the source-only release checks. They validate PE version
templates, the patch mirror, stable release metadata, optional signing guards,
embedded component versions, and safe updater defaults without invoking Ninja:

```powershell
$repo = (Resolve-Path '.').Path
$qaPython = (Get-Command python.exe -ErrorAction Stop).Source
$qaNode = Join-Path $repo 'build\src\third_party\node\win\node.exe'

& $qaPython (Join-Path $repo 'build_support\test_release_configuration.py')
if ($LASTEXITCODE -ne 0) { throw 'Focus release source checks failed' }

& $qaNode (Join-Path $repo '.github\actions\stage\release-version.test.js')
if ($LASTEXITCODE -ne 0) { throw 'Focus release-version checks failed' }
```

Run after `chrome`, `setup`, `mini_installer`, and the NSIS wrapper have been
built and packaging has finished:

```powershell
$repo = (Resolve-Path '.').Path
$qaNode = Join-Path $repo 'build\src\third_party\node\win\node.exe'
$qaBrowser = Join-Path $repo 'build\src\out\Default\chrome.exe'
$qaInstaller = Join-Path $repo 'build\FocusBrowser_1.0.2_x64-installer.exe'
$qaVerifier = Join-Path $repo 'build_support\verify_focus_release.ps1'
$qaPowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

& $qaPowerShell -NoProfile -ExecutionPolicy Bypass -File $qaVerifier `
    -Mode Artifacts -BrowserPath $qaBrowser -InstallerPath $qaInstaller
if ($LASTEXITCODE -ne 0) { throw 'Focus Browser artifact gate failed' }

& $qaNode (Join-Path $repo 'build_support\verify_focus_popups.mjs') $qaBrowser
if ($LASTEXITCODE -ne 0) { throw 'Focus popup smoke test failed' }

& $qaNode (Join-Path $repo 'qa\verify_focusyoutube_full.mjs')
if ($LASTEXITCODE -ne 0) { throw 'FocusYoutube full contract failed' }
```

The PowerShell verifier is read-only in `Artifacts` mode. It checks Focus
branding and exact PE FileVersion/ProductVersion `1.0.2.0` for `chrome.exe`,
`chrome.dll`, `setup.exe`, and `mini_installer.exe`, plus packaging payloads,
SHA-256 hashes, monochrome embedded icon, signature state, and static
browser-owned component integration. An
unsigned installer is reported as a warning, not a failure; it will still show
`Unknown publisher`/SmartScreen until an Authenticode solution is added.

## 2. Disposable first-run profile

Do not pass `--no-first-run`; the point is to exercise the real first launch.

```powershell
$qaRoot = Join-Path $env:TEMP `
    ('FocusBrowser-QA-1.0.2-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$qaProfile = Join-Path $qaRoot 'User Data'
$qaLog = Join-Path $qaRoot 'focus-browser.log'
New-Item -ItemType Directory -Path $qaProfile -Force | Out-Null

$qaLaunchArgs = @(
    "--user-data-dir=$qaProfile",
    '--lang=ru',
    '--no-default-browser-check',
    '--enable-logging',
    "--log-file=$qaLog",
    '--v=0'
)
Start-Process -FilePath $qaBrowser -ArgumentList $qaLaunchArgs
```

First-run/onboarding checks:

- [ ] The first tab is `focus://setup`; no old setup page appears.
- [ ] The welcome mark is the white concentric Focus target on black. There is
  no old letter, star, arrow, or previous product name/logo.
- [ ] The detailed setup path contains Focus defaults, appearance, search,
  import, password manager, default browser, and shortcuts pages.
- [ ] Appearance offers the three address-bar styles (full, centered, minimal)
  and the smooth-animation switch. There is no obsolete tab-layout step.
  Selections apply without a crash.
- [ ] Newly entered graphemes appear immediately in their final position without
  a translucent duplicate, blur, or fractional vertical shift in the new-tab
  search, native address bar, and editable fields on HTTP/HTTPS pages. Paste,
  Backspace/Delete, and IME composition preserve committed text and selection;
  the caret glides to its committed position without delaying DOM, IME, or
  accessibility state. Disabling smooth animations stops caret motion
  immediately.
- [ ] Search-engine and browser-import rows have visible, non-broken logos.
- [ ] If Google Chrome is detected, the one-click Chrome import card is shown.
  It offers the Chromium importer categories implemented by this build
  (bookmarks and history). A real import is expected only when a standard
  Chrome profile is detected. Chrome password and autofill imports are
  deliberately disabled in this path. Do not inspect or print imported values;
  the entire disposable QA profile is deleted afterward.
- [ ] The Focus Password Manager page opens
  `chrome://password-manager/passwords`; its import button opens
  `chrome://password-manager/settings`.
- [ ] Choose **No** on the default-browser onboarding page during isolated QA,
  so this phase cannot change Windows defaults.
- [ ] Shortcut categories are Social, Coding, AI, and Productivity. All 16
  company logos render. Initially zero are selected and the final button says
  **Пропустить**. Selecting an item changes it to **Начать**.
- [ ] Select YouTube and Codex, finish onboarding, and verify their real logos
  appear as shortcuts on the new tab page.
- [ ] The new tab exposes localized **Добавить ярлык** and
  **Настроить эту страницу** controls. Opening search suggestions does not move
  the search field or caret, and no logo, meditation card, or Focus Mode control
  is added to the main surface.
- [ ] The new-tab favicon/logo is the monochrome Focus target, not the old
  letter/star/arrow.

Repeat the first-run check with a second disposable profile and select no
shortcuts. Press **Пропустить** and verify no preset shortcuts are silently
added.

## 3. Built-in FocusBlock and FocusYoutube

Checks:

- [ ] FocusBlock has a browser-owned shield at the far right *inside* the
  address field on a fresh profile. Verify normal, centered, compact, minimal
  and vertical toolbar layouts, including a deliberately narrow window; the
  shield must stay in the field and must open its popup from that same anchor.
  The crossed-out FocusYoutube icon appears only on supported YouTube URLs,
  remains stable across pending/committed navigation, and opens its popup from
  the same address-field anchor. Both use black/white Focus styling with Russian
  text.
- [ ] FocusBlock opens a simple native panel, not an extension popup. It shows
  engine state, the browser-wide switch, the current-site switch, blocked
  counts for the site/session and current engine attribution; it contains no
  links to external settings sites.
- [ ] Both controls can be hidden from toolbar customization; restart and
  confirm the hidden state persists. They can be restored from toolbar
  customization, not from the generic extensions menu.
- [ ] Neither offers **Удалить из Focus Browser**. `chrome://extensions` does
  not expose them as ordinary removable extensions.
- [ ] FocusBlock protection is globally enabled by default. Switch it off,
  reload a page, restart the browser, and confirm the off state persists; turn
  it back on before finishing QA.
- [ ] FocusBlock's per-site power control still works independently of the
  global switch and its state persists after reload.
- [ ] FocusYoutube itself is enabled by default, but all 25 distraction controls
  are off. Enable **Скрывать рекомендации на главной**, reload YouTube,
  verify the feed disappears, then disable it and verify the feed returns.
- [ ] Disable and re-enable the whole FocusYoutube module. It must not crash the
  tab or lose the individual control state.
- [ ] On a local direct/redirect fixture, FocusBlock allows an ordinary request
  and blocks a matching advertising/tracker request before it reaches the
  target. Repeat after a browser restart to catch engine-startup regressions.

For a network behavior check, use a disposable profile only. A public adblock
test page and YouTube may change independently of Focus Browser, so failures
must be distinguished from offline/network failures.

## 4. Meditation, passwords, extension store, and DRM

- [ ] Open `focus://meditation`. It renders without an iframe/player error.
- [ ] Press the video button. A normal YouTube tab opens at exactly
  `https://www.youtube.com/watch?v=R2K7ZHsnypI`; error 153 does not appear.
- [ ] `chrome://password-manager/passwords` and
  `chrome://password-manager/settings` work and are Focus branded. Onboarding
  imports only bookmarks and history from a detected standard Chrome profile;
  Chrome password and autofill imports remain disabled.
- [ ] Install one harmless test extension from the Chrome Web Store into the
  disposable profile. It downloads from Google's Chrome Web Store/update
  endpoints and appears as an ordinary removable extension; FocusBlock and
  FocusYoutube remain browser-owned.
- [ ] `chrome://components` contains **Widevine Content Decryption Module**.
  Use **Check for update**, then test licensed playback on a Widevine demo.
  Record `component present`, `download succeeded`, and `playback succeeded` as
  three separate results.

Widevine delivery is network/licensing dependent and no proprietary CDM is
bundled. Google Chrome account Sync is also not a release-pass criterion for a
third-party Chromium fork without Google-approved private API credentials.

## 5. Installer and default-browser crash regression

Before installation, close Focus Browser windows only. Leave unrelated browser
processes alone. Inspect executable paths instead of killing a shared process
name:

```powershell
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Select-Object ProcessId, ExecutablePath, CommandLine |
    Format-Table -AutoSize
```

Snapshot critical Focus profile files while Focus Browser is closed. The
snapshot contains only sizes, timestamps, paths, and SHA-256 hashes—never
password/history contents:

```powershell
$qaUpgradeRoot = Join-Path $env:TEMP 'FocusBrowser-QA-Upgrade-1.0.2'
New-Item -ItemType Directory -Path $qaUpgradeRoot -Force | Out-Null
$qaBefore = Join-Path $qaUpgradeRoot 'before.json'
$qaAfter = Join-Path $qaUpgradeRoot 'after.json'

& $qaPowerShell -NoProfile -ExecutionPolicy Bypass -File $qaVerifier `
    -Mode Snapshot -SnapshotPath $qaBefore
if ($LASTEXITCODE -ne 0) { throw 'Could not snapshot Focus user data' }
```

Run a silent current-user install/update and verify the wrapper-normalized exit
code. Do not add `/SYSTEM` for this test:

```powershell
$qaInstallerLog = Join-Path $qaUpgradeRoot 'installer.log'
$qaInstallerProcess = Start-Process -FilePath $qaInstaller -Wait -PassThru `
    -ArgumentList @('/S', '/VERBOSE-LOGGING', "/LOG-FILE=$qaInstallerLog")
if ($qaInstallerProcess.ExitCode -ne 0) {
    throw "Installer failed with exit code $($qaInstallerProcess.ExitCode)"
}

& $qaPowerShell -NoProfile -ExecutionPolicy Bypass -File $qaVerifier `
    -Mode Compare -BaselineSnapshot $qaBefore -SnapshotPath $qaAfter
if ($LASTEXITCODE -ne 0) { throw 'Upgrade modified or removed browser data' }

& $qaPowerShell -NoProfile -ExecutionPolicy Bypass -File $qaVerifier -Mode Registry
if ($LASTEXITCODE -ne 0) { throw 'Windows browser registration is incomplete' }
```

Installer checks:

- [ ] First install/update returns exit code 0; a second silent run also returns
  0 and behaves as repair/up-to-date, not as an error.
- [ ] Installed executable is
  `%LOCALAPPDATA%\FocusBrowser\Focus Browser\Application\chrome.exe` and its
  FileVersion and ProductVersion are exactly `1.0.2.0`.
- [ ] `RegisteredApplications`, `StartMenuInternet`, `FocusHTM*`, and
  `FocusPDF*` registry entries point to that executable.
- [ ] No new `chrome.exe*.dmp`, `setup.exe*.dmp`, or installer crash dump was
  created. No stale `focus_browser` payload remains under an `ns*.tmp` wrapper
  directory.
- [ ] Existing bookmarks/history/password databases survive byte-for-byte while
  the browser is closed. After launch, the same profile opens normally.

Default-browser crash regression:

- [ ] Open `chrome://settings/defaultBrowser` in the installed Focus Browser.
- [ ] Record the browser PID, click **Сделать браузером по умолчанию**, and
  confirm Windows Default Apps opens while the same Focus process remains alive.
- [ ] There are no assertion dialogs, three/four error windows, or crash dump.
- [ ] It is enough to confirm Windows can display the Focus Browser association
  page. Do not actually change the user's current default unless explicitly
  requested. If manually selected, HTTPS/HTTP UserChoice should resolve to a
  `FocusHTM*` ProgID.

## 6. Next-launch update prompt

Use a disposable profile and a staging appcast signed by the same Ed25519 key
as the test build. This checklist does not imply that the production Pages feed
has already been published.

- [ ] Discovery records an available version without interrupting the current
  session. After a normal exit, the next browser launch shows one native Focus
  prompt.
- [ ] **Обновить сейчас** starts download only for an enclosure with a valid
  signature; an unsigned or missing enclosure is never offered.
- [ ] **Напомнить позже** and closing the dialog suppress it for the current
  session and allow it on a later launch.
- [ ] Skipping a version suppresses exactly that version; a newer signed version
  supersedes the skip.
- [ ] An unavailable or empty feed produces no prompt, startup crash, or stale
  offer.

## 7. Cleanup and evidence

Exit the disposable Focus Browser from its menu and wait for only processes
whose command line contains the exact `$qaProfile` path to close. Verify the
resolved cleanup target is inside `$env:TEMP` before deleting it; never delete a
default browser profile or terminate all `chrome.exe` processes.

Keep these release artifacts:

- artifact verifier output and SHA-256 hashes;
- NSIS installer log and exit code;
- before/after upgrade snapshot comparison;
- screenshots of the onboarding target logo, shortcut logos, clean new tab,
  both built-in popups, meditation page, animation switch, and Windows Default
  Apps handoff;
- DRM component/update/playback result with network status noted separately.
