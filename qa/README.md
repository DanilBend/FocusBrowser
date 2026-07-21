# QA scripts

Only source-level verification scripts are stored here. Runtime profiles,
browser databases, screenshots produced during ad-hoc testing and logs are
intentionally excluded from Git.

Run browser tests with a unique disposable `--user-data-dir`. Never point a QA
script at a real browser profile and never terminate unrelated `chrome.exe`
processes by name.

Native FocusBlock network smoke test (local fixture, no extension page):

```powershell
node qa/verify_focusblock_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focusblock-runtime-report.json"
```

Built-in text-entry motion static contract:

```powershell
node qa/verify_focus_text_motion.mjs
```

After rebuilding, run the local runtime smoke test with a disposable profile:

```powershell
node qa/verify_focus_text_motion_runtime.mjs build/src/out/Default/chrome.exe "$env:TEMP\focus-text-motion-runtime-report.json"
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
