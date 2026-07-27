# QA scripts

Only source-level verification scripts are stored here. Runtime profiles,
browser databases, screenshots produced during ad-hoc testing and logs are
intentionally excluded from Git.

Run browser tests with a unique disposable `--user-data-dir`. Never point a QA
script at a real browser profile and never terminate unrelated `chrome.exe`
processes by name.

Real bookmark-import regression QA is fail-closed. It refuses to launch unless
the active test source tree contains the explicit QA-only source-profile hook:

```powershell
$env:FOCUS_ACTIVE_SOURCE_ROOT = (Resolve-Path build/src).Path
node qa/verify_settings_import_real_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-import-runtime-report.json"
```

Do not bypass that refusal or run the test against a normal browser profile.

Native FocusBlock network smoke test (local fixture, no extension page):

```powershell
node qa/verify_focusblock_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focusblock-runtime-report.json"
```

Focus new-tab entry-transition proof against an already-running disposable
browser. Start that browser yourself with `--remote-debugging-port=<port>`;
the script creates and closes only its own temporary tab:

```powershell
node qa/verify_focus_new_tab_transition.mjs 9341
```

Automatic password-save bubble crash regression (generated QA credentials,
local login form, Password Manager Internals proof and disposable profile):

```powershell
node qa/verify_password_bubble_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-password-bubble-runtime-report.json"
```

Built-in text-entry motion static contract:

```powershell
node qa/verify_focus_text_motion.mjs
```

Blink-native caret glide static contract:

```powershell
node qa/verify_focus_caret_motion.mjs
```

Blink-native caret glide runtime proof. It isolates the bright caret against
transparent text, checks a no-DOM-change ArrowRight control, then samples
typing, paste-sized insertion, Backspace and Delete in input, textarea and
contenteditable fields:

```powershell
node qa/verify_focus_caret_motion_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-caret-motion-runtime-report.json"
```

After rebuilding, run the local runtime smoke test with a disposable profile.
It checks single-character insertion, a multi-grapheme paste payload,
Backspace/Delete, immediate value and caret commits, pixel-stable prefixes,
stable field geometry, and final paint settling:

```powershell
node qa/verify_focus_text_motion_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-text-motion-runtime-report.json"
```

IME composition guard runtime proof. It uses CDP composition updates in
input, textarea and contenteditable fields, requires a real compositionend on
commit, verifies provisional text is still, and checks reduced motion:

```powershell
node qa/verify_focus_ime_motion_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-ime-motion-runtime-report.json"
```

Native omnibox glyph-settle smoke test (real Windows keyboard input and
screen-pixel sampling, always with a unique disposable profile):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File qa/verify_focus_omnibox_text_motion_runtime.ps1 -BrowserPath build/src/out/Default/chrome.exe -EvidenceDirectory "$env:TEMP\focus-omnibox-motion"
```

Final Windows package and installer/update gate (x64):

```powershell
python package.py --cpu-arch 64bit
powershell -NoProfile -ExecutionPolicy Bypass -File qa\verify_installer_update_smoke.ps1 -Mode Artifacts
powershell -NoProfile -ExecutionPolicy Bypass -File qa\verify_installer_update_smoke.ps1 -Mode All -AllowInstall
```

The install mode refuses to run without `-AllowInstall`, aborts if an installed
Focus Browser process is already active, uses only unique disposable profiles,
and never terminates a process merely because it is named `chrome.exe`.
