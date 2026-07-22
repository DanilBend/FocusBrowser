# Contributing

<p><a href="CONTRIBUTING.md">Русский</a> · <strong>English</strong></p>

Thanks for helping improve Focus Browser.

## Before opening a pull request

1. Keep changes focused and do not commit `build/`, browser profiles, logs,
   archives or compiled binaries.
2. Preserve third-party copyright and license notices.
3. Do not add private keys, tokens, cookies, profile data or machine-specific
   paths.
4. Run the lightweight checks:

```powershell
python build_support/test_release_configuration.py
node .github/actions/stage/release-version.test.js
```

For browser UI changes, also run the relevant scripts under `qa/` with a
disposable `--user-data-dir` and attach screenshots to the pull request.

## Pull requests

Describe what changed, why it changed, how it affects users and exactly which
checks were run. Large unrelated rewrites should be split into separate pull
requests.

## Licensing

By contributing GPL-covered project code, you agree that your contribution is
available under GPL-3.0. Do not contribute third-party material unless its
license is compatible and the required notices are included. The
Unhook-derived FocusYoutube directory is governed separately; changes to that
material require permission consistent with its component terms.
