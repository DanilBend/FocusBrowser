<a id="russian"></a>

# Focus Browser для macOS

<p align="right"><strong>Русский</strong> · <a href="#english">English</a></p>

> **Статус:** прежняя ручная DMG принята локально; новая universal-сборка со
> Sparkle версии 1.0.6 проходит отдельную приёмку перед публикацией.

Focus Browser для macOS — нативная Chromium/Views-сборка, а не мобильный порт
или WebKit-обёртка. Один universal DMG предназначен для Apple Silicon и
64-битных Intel Mac с macOS 12 Monterey или новее.

## Статус загрузки

| Поле | Значение |
| --- | --- |
| GitHub Release с автообновлением | **ожидает финальной приёмки** |
| Целевой DMG с автообновлением | `FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg` |
| Локальный принятый DMG | `FocusBrowser-macOS-1.0.5-universal.dmg` |
| Размер | `278 163 247` байт |
| SHA-256 | `5f5e3755276fbd8a622c9d759e801a0e17c9c876114501d538912fe29a1d2eb0` |

Размер и SHA-256 выше относятся к локально принятому 31 июля 2026 года DMG.
Он ещё не загружен в GitHub. Пока отдельный Release фактически не опубликован,
официальной загрузки macOS нет. Не используйте кнопку `releases/latest` как
ссылку на macOS: сейчас она ведёт на Windows-выпуск.

Приёмка резервной ручной сборки прошла на Apple Silicon Mac: приложение и смонтированный
только для чтения DMG запускались как нативно в `arm64`, так и через Rosetta в
`x86_64`, каждый раз с новым профилем и в Incognito. Проверены ad-hoc подпись,
RU/EN, FocusBlock, FocusYoutube и отсутствие компонентов автообновления. Это
относится только к указанной выше резервной DMG, а не к новой Sparkle-сборке.

После публикации файл нужно скачивать только из официального списка
[GitHub Releases](https://github.com/DanilBend/FocusBrowser/releases) и сверять
с размером и SHA-256, указанными для того же выпуска.

## Установка

1. Скачайте universal DMG из конкретного официального GitHub Release.
2. До открытия сверьте размер файла и SHA-256 с опубликованными значениями.
3. Откройте DMG и перетащите `Focus Browser.app` в `Applications`.
4. Запустите установленное приложение и проверьте версию на странице About.

Локальная сборка подписана ad-hoc и не нотарифицирована Apple. Такая подпись
проверяет целостность кода, но не подтверждает личность разработчика.
Gatekeeper может потребовать ручное разрешение в Privacy & Security, а политика
управляемого Mac может полностью запретить запуск. Разрешайте запуск только для
файла, полученного из официального Release и совпавшего по SHA-256.

## Обновления приложения

Новая macOS-сборка использует Sparkle 2.9.4. ChromiumUpdater, Keystone и Google
Updater остаются отключены. Браузер проверяет отдельный macOS-feed раз в сутки,
может безопасно установить найденную версию и также поддерживает ручную
проверку на странице «О браузере»:

`https://danilbend.github.io/FocusBrowser/appcast-macos.xml`

DMG-пакет и сам appcast обязаны иметь корректную Ed25519-подпись выделенным
macOS-ключом. Закрытый ключ хранится только в macOS Keychain и не находится в
репозитории или приложении. Feed не публикуется раньше проверенного GitHub
Release, а системное профилирование и JavaScript Sparkle отключены.

Первая сборка с автообновлением имеет отдельную macOS-версию `1.0.6.0`
(показывается как `1.0.6`). Версии Windows и Android этим не изменяются.
Планируемый канал — отдельный неизменяемый prerelease `v1.0.6-macos`, который
не помечается как Latest. Текущий стабильный Windows-выпуск остаётся
`v1.0.5`, а простой тег `v1.0.6` зарезервирован для будущего согласованного
стабильного выпуска.

Прежняя DMG `FocusBrowser-macOS-1.0.5-universal.dmg` не умеет обновляться сама.
Для первого перехода с неё установите новую Sparkle-сборку вручную из
конкретного Release. Ручная замена приложения из DMG остаётся резервным способом
на случай недоступности feed.

Windows использует другой ключ и `appcast-x64.xml`; эти каналы не смешиваются.
Обновления компонентов Chromium также не являются обновлением самого
`Focus Browser.app`.

## Совместимость и границы

- macOS 12 Monterey или новее;
- Apple Silicon `arm64`;
- 64-битные Intel Mac `x86_64`;
- один universal DMG с обоими срезами;
- только macOS: версий для iPhone, iPad, iOS и iPadOS нет.

Universal-бинарник подтверждает наличие обеих архитектур, но не означает, что
выпуск протестирован на каждой модели Mac и каждой версии macOS. Фактическая
матрица проверок должна быть указана в примечаниях конкретного выпуска.

Технические сведения: [локальный DMG](../platform/macos/LOCAL-DMG.md),
[universal-совместимость](../platform/macos/UNIVERSAL.md) и
[macOS build pipeline](../platform/macos/README.md),
[контракт автообновления](../platform/macos/AUTOUPDATE.md).

<a id="english"></a>

# Focus Browser for macOS

<p align="right"><a href="#russian">Русский</a> · <strong>English</strong></p>

> **Status:** the previous manual DMG passed local acceptance; the new
> Sparkle-enabled 1.0.6 universal build is undergoing separate release
> acceptance.

Focus Browser for macOS is a native Chromium/Views build, not a mobile port or
WebKit wrapper. One universal DMG targets Apple Silicon and 64-bit Intel Macs
running macOS 12 Monterey or later.

## Download status

| Field | Value |
| --- | --- |
| Auto-update GitHub Release | **awaiting final acceptance** |
| Target auto-update DMG | `FocusBrowser-macOS-1.0.6-universal-autoupdate.dmg` |
| Locally accepted DMG | `FocusBrowser-macOS-1.0.5-universal.dmg` |
| Size | `278,163,247` bytes |
| SHA-256 | `5f5e3755276fbd8a622c9d759e801a0e17c9c876114501d538912fe29a1d2eb0` |

The size and SHA-256 above belong to the locally accepted DMG from July 31,
2026. It has not been uploaded to GitHub. Until a separate Release has actually
been published, there is no official macOS download. Do not use
`releases/latest` as a macOS link: it currently points to the Windows release.

The fallback manual build passed local acceptance on an Apple Silicon Mac. Both the signed application
and its read-only mounted DMG ran natively as `arm64` and through Rosetta as
`x86_64`, each with a fresh Incognito profile. The gate also verified ad-hoc
signing, RU/EN resources, FocusBlock, FocusYoutube, and the absence of automatic
application-update components. That last statement applies only to the fallback
DMG above, not the new Sparkle build.

After publication, download only from the official
[GitHub Releases](https://github.com/DanilBend/FocusBrowser/releases) list and
compare the file with the size and SHA-256 stated for that same release.

## Installation

1. Download the universal DMG from its specific official GitHub Release.
2. Before opening it, verify the file size and SHA-256 against the published
   values.
3. Open the DMG and drag `Focus Browser.app` to `Applications`.
4. Launch the installed app and verify its version on the About page.

The local build is ad-hoc signed and is not Apple-notarized. This signature
checks code integrity but does not establish a developer identity. Gatekeeper
may require manual approval in Privacy & Security, and managed-Mac policy may
prohibit execution entirely. Approve only a file obtained from the official
Release whose SHA-256 matches.

## Application updates

The new macOS build uses Sparkle 2.9.4. ChromiumUpdater, Keystone, and Google
Updater remain disabled. The browser checks a dedicated macOS feed once per
day, can safely install a discovered version, and retains a manual check on the
About page:

`https://danilbend.github.io/FocusBrowser/appcast-macos.xml`

The DMG payload and appcast must carry valid Ed25519 signatures from the dedicated
macOS key. Its private key remains only in macOS Keychain and is never stored
in the repository or application. The feed is published after the verified
GitHub Release; Sparkle system profiling and JavaScript are disabled.

The first updater-capable build has a separate macOS version of `1.0.6.0`
(displayed as `1.0.6`). This does not change the Windows or Android versions.
Its intended channel is the separate immutable prerelease `v1.0.6-macos`,
which is not marked Latest. The current stable Windows release remains
`v1.0.5`, while plain `v1.0.6` is reserved for a future coordinated stable
release.

The previous `FocusBrowser-macOS-1.0.5-universal.dmg` cannot update itself. The
first move from that build requires a manual installation of the new
Sparkle-enabled build from its specific Release. Manual DMG replacement remains
the fallback if the feed is unavailable.

Windows uses a different key and `appcast-x64.xml`; the channels are never
mixed. Chromium component updates are also not updates to
`Focus Browser.app` itself.

## Compatibility and scope

- macOS 12 Monterey or later;
- Apple Silicon `arm64`;
- 64-bit Intel Macs `x86_64`;
- one universal DMG containing both slices;
- macOS only: there are no iPhone, iPad, iOS, or iPadOS builds.

A universal binary proves that both architectures are present; it does not
claim testing on every Mac model or macOS release. The actual test matrix must
be stated in the notes for each specific release.

Technical details: [local DMG](../platform/macos/LOCAL-DMG.md),
[universal compatibility](../platform/macos/UNIVERSAL.md), and the
[macOS build pipeline](../platform/macos/README.md), plus the
[automatic-update contract](../platform/macos/AUTOUPDATE.md).
