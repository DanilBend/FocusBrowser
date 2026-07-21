# QA scripts

Only source-level verification scripts are stored here. Runtime profiles,
browser databases, screenshots produced during ad-hoc testing and logs are
intentionally excluded from Git.

Run browser tests with a unique disposable `--user-data-dir`. Never point a QA
script at a real browser profile and never terminate unrelated `chrome.exe`
processes by name.
