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
- The meditation page does not embed or preload an external video service. Its
  button opens the configured video in a normal tab, after which that service
  receives the normal request metadata for the visit.
- Smooth-animation preferences, shortcut selections and onboarding choices are
  stored in the local profile.

## Browser network services

Focus Browser uses networking required for websites, selected search providers,
extension installation and allowed component updates. Third-party account
synchronization is not enabled.

## Diagnostic reports

Do not upload a real profile, cookies, saved passwords, tokens, browsing
databases or unsanitized crash dumps to public issue trackers. Use a disposable
profile when reproducing bugs and follow [SECURITY.en.md](SECURITY.en.md) for vulnerabilities.
