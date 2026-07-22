# Privacy notes

<p><a href="PRIVACY.md">Русский</a> · <strong>English</strong></p>

Focus Browser is designed to keep its Focus-specific controls local, but it is
still a web browser and network behavior depends on the pages and services you
use.

## Focus-specific components

- FocusBlock evaluates its bundled rules and user settings locally. Updating
  external filter lists, when enabled by the user, contacts the list hosts.
- FocusYoutube stores its switches in the local browser profile. The packaged
  component contains no analytics, payment, donation, remote-font or external
  settings flows.
- The meditation page does not embed or preload YouTube. Pressing its video
  button opens YouTube in a normal tab, after which YouTube receives the normal
  request metadata for that visit.
- Smooth-animation preferences, shortcut selections and onboarding choices are
  stored in the local profile.

## Chromium services

Focus Browser inherits networking required for normal Chromium functionality,
including websites, selected search providers, extension installation and
allowed component updates. Google Chrome account Sync is not enabled.

## Diagnostic reports

Do not upload a real profile, cookies, saved passwords, tokens, browsing
databases or unsanitized crash dumps to GitHub issues. Use a disposable profile
when reproducing bugs and follow [SECURITY.en.md](SECURITY.en.md) for vulnerabilities.
