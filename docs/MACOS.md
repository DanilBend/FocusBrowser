<a id="russian"></a>

# Focus Browser для macOS

<p align="right"><strong>Русский</strong> · <a href="#english">English</a></p>

> **Статус:** сборка для macOS пока не опубликована. В текущем публичном
> списке GitHub Releases нет файлов для macOS.

Focus Browser для macOS — нативная Chromium/Views-сборка, а не мобильный порт
или WebKit-обёртка. Один universal DMG предназначен для Apple Silicon и
64-битных Intel Mac с macOS 12 Monterey или новее.

## Статус загрузки

| Поле | Значение |
| --- | --- |
| GitHub Release | **не опубликован** |
| Локальный принятый DMG | `FocusBrowser-macOS-1.0.5-universal.dmg` |
| Размер | `278 163 247` байт |
| SHA-256 | `5f5e3755276fbd8a622c9d759e801a0e17c9c876114501d538912fe29a1d2eb0` |

Размер и SHA-256 выше относятся к локально принятому 31 июля 2026 года DMG.
Он ещё не загружен в GitHub. Пока отдельный Release фактически не опубликован,
официальной загрузки macOS нет. Не используйте кнопку `releases/latest` как
ссылку на macOS: сейчас она ведёт на Windows-выпуск.

Локальная приёмка прошла на Apple Silicon Mac: приложение и смонтированный
только для чтения DMG запускались как нативно в `arm64`, так и через Rosetta в
`x86_64`, каждый раз с новым профилем и в Incognito. Проверены ad-hoc подпись,
RU/EN, FocusBlock, FocusYoutube и отсутствие компонентов автообновления.

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

## Ручные обновления

Автоматического обновления приложения в macOS-сборке нет. В ней отключён
Chromium app updater, отсутствуют appcast, Sparkle, update helper, фоновая
загрузка и автоматический перезапуск.

Чтобы обновить Focus Browser после появления нового macOS-выпуска:

1. Закройте все окна Focus Browser и дождитесь завершения его helper-процессов.
2. Скачайте новый universal DMG из конкретного официального GitHub Release.
3. Сверьте размер и SHA-256 нового файла.
4. Откройте DMG и вручную замените существующий `Focus Browser.app`.
5. После запуска проверьте отображаемую версию браузера.

Windows-сборки используют отдельный подписанный Ed25519 WinSparkle-канал. Он
не используется macOS-портом. Обновления отдельных
компонентов Chromium, если они доступны, также не являются обновлением самого
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
[macOS build pipeline](../platform/macos/README.md).

<a id="english"></a>

# Focus Browser for macOS

<p align="right"><a href="#russian">Русский</a> · <strong>English</strong></p>

> **Status:** the macOS build is not published yet. The current public GitHub
> Releases list contains no macOS assets.

Focus Browser for macOS is a native Chromium/Views build, not a mobile port or
WebKit wrapper. One universal DMG targets Apple Silicon and 64-bit Intel Macs
running macOS 12 Monterey or later.

## Download status

| Field | Value |
| --- | --- |
| GitHub Release | **not published** |
| Locally accepted DMG | `FocusBrowser-macOS-1.0.5-universal.dmg` |
| Size | `278,163,247` bytes |
| SHA-256 | `5f5e3755276fbd8a622c9d759e801a0e17c9c876114501d538912fe29a1d2eb0` |

The size and SHA-256 above belong to the locally accepted DMG from July 31,
2026. It has not been uploaded to GitHub. Until a separate Release has actually
been published, there is no official macOS download. Do not use
`releases/latest` as a macOS link: it currently points to the Windows release.

Local acceptance passed on an Apple Silicon Mac. Both the signed application
and its read-only mounted DMG ran natively as `arm64` and through Rosetta as
`x86_64`, each with a fresh Incognito profile. The gate also verified ad-hoc
signing, RU/EN resources, FocusBlock, FocusYoutube, and the absence of automatic
application-update components.

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

## Manual updates

The macOS build has no automatic application updates. The Chromium app updater
is disabled, with no appcast, Sparkle, update helper, background download, or
automatic restart.

After a newer macOS release becomes available:

1. Quit all Focus Browser windows and wait for its helper processes to exit.
2. Download the new universal DMG from its specific official GitHub Release.
3. Verify the new file's size and SHA-256.
4. Open the DMG and manually replace the existing `Focus Browser.app`.
5. Launch it and verify the displayed browser version.

Windows builds use their separate Ed25519-signed WinSparkle channel. That
channel is not used by the macOS port. Chromium component updates, where
available, are also not updates to `Focus Browser.app` itself.

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
[macOS build pipeline](../platform/macos/README.md).
