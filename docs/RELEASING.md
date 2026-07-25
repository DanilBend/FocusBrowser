# Релизы Focus Browser для Windows

Этот документ — runbook для текущего открытого репозитория
`DanilBend/FocusBrowser` и выпуска версии 1.0.3. Он ничего не публикует и не
содержит рабочих ключей.

> Важно: текущий `.github/workflows/main.yml` нельзя включать для production-
> релизов без отдельного аудита, перечисленного ниже. Сначала должен пройти
> тестовый выпуск с отдельным тестовым ключом.

## 1. Что защищает каждый вид подписи

- Ed25519-подпись WinSparkle подтверждает байты скачанного обновления уже
  установленному браузеру. Это основной механизм доверия и обязательный
  атрибут каждого enclosure, предлагаемого production appcast.
- Authenticode подтверждает издателя Windows и влияет на диалоги UAC и
  репутацию SmartScreen. Для штатного автообновления она не обязательна.
- HTTPS, SHA-256 и неизменяемый GitHub Release усиливают цепочку поставки, но
  не заменяют Ed25519. Они также не дают Windows идентичность издателя.

В system-level сценарии elevated helper повторно проверяет Ed25519 и сразу
принимает payload при успехе. Только если эта повторная проверка не прошла и при
сборке задан непустой `WINSPARKLE_AUTHENTICODE_ORG`, helper использует
Authenticode как опциональный fallback: `WinVerifyTrust` должен подтвердить
цепочку, а поле `Subject Organization (O)` сертификата должно целиком совпасть с
настроенным значением без учёта регистра ASCII. Это не поиск подстроки и не
сравнение display name. Если переменная пуста, fallback отключён.

Этот fallback существует только во втором, elevated-helper слое. Первичная
проверка скачанного enclosure самим WinSparkle всё равно требует корректную
Ed25519-подпись. Поэтому не требуется, чтобы один payload одновременно прошёл
обе проверки, но appcast без Ed25519 production-клиенту публиковать нельзя.

Ed25519 не устраняет предупреждение Windows «Неизвестный издатель». Без
доверенного Authenticode-сертификата SmartScreen может предупреждать или
блокировать новый файл, пока у файла/издателя не появится репутация.

## 2. Однократная настройка GitHub

### 2.1. Репозиторий

1. Используйте публичный репозиторий `DanilBend/FocusBrowser`.
2. Сохраните существующую историю Git. Если репозиторий уже инициализирован,
   не выполняйте повторно `git init`.
3. Проверьте лицензию GPL-3.0 и лицензии импортированного кода перед первым
   публичным push.
4. Добавьте remote и отправьте ветку:

   ```powershell
   git remote -v
   git remote add origin https://github.com/DanilBend/FocusBrowser.git
   git push -u origin main
   ```

5. В ruleset для `main` потребуйте pull request, успешные проверки и запретите
   force-push/delete. Для release-тегов `v*` запретите перемещение и удаление.
6. В Actions задайте минимальные права по умолчанию: `contents: read`.
   Только job публикации получает `contents: write`; публикация Pages — только
   `pages: write` и `id-token: write`.
7. Не разрешайте workflow из fork/PR получать release-секреты. Все сторонние
   Actions должны быть закреплены на полном commit SHA после проверки исходника.

### 2.2. Защищённые окружения

Текущий `github-pages` environment разрешает deployment только из `main` и не
содержит секретов. `publish-appcast.yml` проверяет уже опубликованные immutable
assets публичным ключом.

Если позже появится полностью автоматизированный signing/publish job, создайте
для него отдельное окружение `release`, добавьте независимого required reviewer,
запретите self-review и administrator bypass и разрешите только защищённые
`main`/`v*`. Не подключайте приватный Ed25519-ключ к Pages job.

### 2.3. Variables и secrets

| Имя | Где хранить | Содержимое | Секрет |
| --- | --- | --- | --- |
| `WINSPARKLE_ED_KEY` | Repository variable | Канонический Base64 публичного Ed25519-ключа, ровно 32 байта после декодирования | нет |
| `WINSPARKLE_APPCAST_URL` | Repository variable только для common-feed policy; всегда process env build job | Абсолютный HTTPS URL feed текущей сборки | нет |
| `WINSPARKLE_APPCAST_URL_X64` | Repository variable | Policy-вариант: `https://danilbend.github.io/FocusBrowser/appcast-x64.xml` | нет |
| `WINSPARKLE_APPCAST_URL_ARM64` | Repository variable | Policy-вариант: `https://danilbend.github.io/FocusBrowser/appcast-arm64.xml` | нет |
| `WINSPARKLE_AUTHENTICODE_ORG` | Repository variable | Точное поле `Subject O` сертификата; сравнение целой строки без учёта регистра ASCII; не задавать без fallback | нет |
| приватный Ed25519 seed | Только зашифрованные offline-копии вне GitHub и checkout | Файл, созданный `winsparkle-tool generate-key` | да |

Исходный код сейчас ожидает переменную процесса
`WINSPARKLE_APPCAST_URL`. Для двух архитектур будущий workflow должен выбрать
соответствующую GitHub variable и передать её под ожидаемым именем в build job:

```yaml
env:
  WINSPARKLE_ED_KEY: ${{ vars.WINSPARKLE_ED_KEY }}
  WINSPARKLE_APPCAST_URL: ${{ vars.WINSPARKLE_APPCAST_URL_X64 }}
```

Для ARM64 используется `vars.WINSPARKLE_APPCAST_URL_ARM64`. Это выбранная ниже
release-policy с двумя feed, а не ограничение парсера WinSparkle. При этой
policy не задавайте repository variable с неоднозначным общим URL: каждый build
job должен явно отобразить свой архитектурный URL в process env
`WINSPARKLE_APPCAST_URL`. Никогда не добавляйте приватный ключ в repository
variable, исходники, артефакт сборки, кэш или лог.

### 2.4. Неизменяемые релизы

До публикации 1.0.3 откройте repository `Settings`, прокрутите страницу до
раздела `Releases` и включите `Enable release immutability`. Настройка действует
только на будущие релизы. После публикации GitHub запрещает перемещение тега и
изменение/удаление assets и автоматически создаёт release attestation.

Именно поэтому каждый выпуск сначала создаётся как draft: загрузите и проверьте
все файлы в draft, а публикацию выполняйте последним действием.

## 3. Бесплатный Ed25519-ключ WinSparkle

### 3.1. Воспроизводимое получение `winsparkle-tool`

Зависимость браузера зафиксирована в `downloads.ini` как WinSparkle `0.9.3` с
SHA-256 исходного архива
`76cde0f62222a6fe516d494a847916b562f0daaf935485e38381cdbacb063f55`.
Однако GitHub source archive не содержит содержимое upstream submodules, а
локальный GN-файл объявляет библиотеку `//third_party/winsparkle`, но не CLI-
target. Не выдумывайте команду `autoninja winsparkle-tool`.

Для release-процесса используйте официальный binary archive той же версии,
закреплённый URL и проверенный SHA-256. `winsparkle-tool.exe` в этом архиве сам
не имеет Authenticode-подписи, поэтому запуск до успешной hash-проверки запрещён:

```powershell
$toolVersion = '0.9.3'
$toolUrl = 'https://github.com/vslavik/winsparkle/releases/download/' +
  "v$toolVersion/WinSparkle-$toolVersion.zip"
$expectedSha256 =
  '745985f41d2ab26b2d5a1cf87d76e4ed851039db19038e50610eb25ea0b73772'
$releaseTemp = if ($env:RUNNER_TEMP) {
  $env:RUNNER_TEMP
} else {
  Join-Path ([IO.Path]::GetTempPath()) 'focus-release-tools'
}
$toolRoot = Join-Path $releaseTemp "winsparkle-tool-$toolVersion"
$toolZip = Join-Path $toolRoot "WinSparkle-$toolVersion.zip"

New-Item -ItemType Directory -Path $toolRoot -Force | Out-Null
Invoke-WebRequest -UseBasicParsing -Uri $toolUrl -OutFile $toolZip
$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $toolZip).Hash.ToLowerInvariant()
if ($actualSha256 -cne $expectedSha256) {
  throw "winsparkle-tool archive SHA-256 mismatch: $actualSha256"
}

Expand-Archive -LiteralPath $toolZip -DestinationPath $toolRoot -Force
$tool = Join-Path $toolRoot "WinSparkle-$toolVersion\bin\winsparkle-tool.exe"
$reportedVersion = ((& $tool --version) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $reportedVersion -cne $toolVersion) {
  throw "Unexpected winsparkle-tool version: $reportedVersion"
}
```

В workflow версия, URL и hash должны меняться только одним reviewed PR вместе с
обновлением `[winsparkle]` в `downloads.ini`. Не используйте `latest`, случайный
NuGet/архив, файл из предыдущего CI artifact или инструмент без hash lock.

### 3.2. Генерация

Ключ создаётся один раз на чистой доверенной машине проверенным инструментом из
предыдущего раздела:

```powershell
& $tool generate-key --file .\winsparkle-private.key
& $tool public-key --private-key-file .\winsparkle-private.key
```

Первая команда сохраняет 32-байтовый seed в Base64. Вторая выводит публичный
ключ. Скопируйте только публичный ключ в `WINSPARKLE_ED_KEY`.

Не вставляйте значение приватного ключа в командную строку, GitHub variable,
GitHub secret, исходники, workflow или release asset. Текущий
`publish-appcast.yml` принципиально не получает приватный ключ: подпись
создаётся локально до загрузки draft-релиза, а GitHub Actions повторно проверяет
её только публичным ключом.

### 3.3. Хранение

- Держите две зашифрованные offline-копии в разных физических местах.
- Доступ должен быть только у release-maintainers с 2FA.
- В PR и обычных CI-сборках используйте отдельный тестовый ключ или полностью
  отключённый updater.
- Не кэшируйте и не загружайте каталог, содержащий key-файл.
- Потеря ключа означает, что уже установленные версии не смогут доверять
  обновлениям с новым ключом. Компрометация ключа требует немедленной остановки
  feed и ручного перехода на новый доверенный ключ.

## 4. Версия 1.0.3 и имена артефактов

Внутренняя версия должна монотонно увеличиваться. Для текущего стабильного
релиза используйте техническую версию `1.0.3.0` и тег `v1.0.3`.

Короткая строка `1.0.3` относится только к presentation-слою упаковки:
`package.py` использует её в именах assets, а NSIS — в тексте мастера и
строковых version-resource полях wrapper. Установленная версия, каталог версии
в `Application`, Windows uninstall `Version`/`DisplayVersion`, страница About,
`--product-version` и сравнение версий WinSparkle используют полную строку
`1.0.3.0`. Поэтому в appcast обязательно ставьте
`sparkle:version="1.0.3.0"`; `sparkle:shortVersionString="1.0.3"` — только подпись
для пользователя. Не переиспользуйте технический номер или тег после
публикации.

Текущий `package.py` формирует для каждой архитектуры:

| Архитектура | Ручная установка | Payload автообновления | Архив |
| --- | --- | --- | --- |
| x64 | `FocusBrowser_1.0.3_x64-installer.exe` | `FocusBrowser_1.0.3_x64-mini-installer.exe` | `FocusBrowser_1.0.3_x64-windows.zip` |
| ARM64 | `FocusBrowser_1.0.3_arm64-installer.exe` | `FocusBrowser_1.0.3_arm64-mini-installer.exe` | `FocusBrowser_1.0.3_arm64-windows.zip` |

В Release также приложите:

- `SHA256SUMS.txt` со всеми публичными файлами;
- `appcast-x64.xml` и `appcast-arm64.xml` как аудит-копии;
- release notes;
- при готовности — SBOM и provenance/attestation.

Appcast должен указывать на `mini-installer.exe`, потому что это payload
встроенного updater. Если реализация позже перейдёт на другой installer,
подписывайте и указывайте один и тот же итоговый файл байт-в-байт.

## 5. Appcast для x64 и ARM64

Парсер WinSparkle `0.9.3` умеет выбирать enclosure по точным маркерам
`sparkle:os="windows-x64"`, `sparkle:os="windows-arm64"` и
`sparkle:os="windows-x86"`. Маркер `windows` является generic fallback для
любой Windows-архитектуры, поэтому его нельзя ставить на архитектурный payload.
Внутри одного `<item>` допустимо несколько enclosure: сначала выбирается точное
совпадение архитектуры, затем generic fallback.

Следовательно, технически поддерживаются обе схемы:

- один общий appcast URL для обеих сборок и два enclosure в каждом item;
- отдельный appcast URL на архитектуру.

Для 1.0 проектная release-policy выбирает отдельные feed: так проще независимо
проверять и поэтапно открывать x64/ARM64, а ошибка в одном feed не отправит
другой архитектуре неверный payload. Это policy, а не ограничение парсера.
Сборки компилируются с разными стабильными URL:

- `https://danilbend.github.io/FocusBrowser/appcast-x64.xml`;
- `https://danilbend.github.io/FocusBrowser/appcast-arm64.xml`.

Рекомендуемый хостинг — GitHub Pages, публикуемый Actions после release-
approval. Не используйте `releases/latest/download/appcast.xml`: стабильный и
pre-release каналы имеют разные правила. При policy с отдельными feed URL должен
однозначно соответствовать архитектуре. Feed публикуется только после того, как
неизменяемые assets уже доступны.

Минимальный `appcast-x64.xml` для 1.0.3:

```xml
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"
     xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Focus Browser updates (x64)</title>
    <link>https://github.com/DanilBend/FocusBrowser/releases</link>
    <description>Stable updates for Focus Browser x64</description>
    <language>ru</language>
    <item>
      <title>Focus Browser 1.0.3</title>
      <pubDate>Sat, 25 Jul 2026 12:00:00 +0000</pubDate>
      <link>https://github.com/DanilBend/FocusBrowser/releases/tag/v1.0.3</link>
      <enclosure
        url="https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.3/FocusBrowser_1.0.3_x64-mini-installer.exe"
        sparkle:version="1.0.3.0"
        sparkle:shortVersionString="1.0.3"
        sparkle:os="windows-x64"
        sparkle:edSignature="&lt;BASE64_ED25519_SIGNATURE&gt;"
        length="&lt;BYTE_LENGTH&gt;"
        type="application/octet-stream" />
    </item>
  </channel>
</rss>
```

Для ARM64 замените заголовок, URL, имя файла и поставьте
`sparkle:os="windows-arm64"`. Если позднее будет принята policy общего feed,
обе сборки должны компилироваться с одним URL, а один item должен содержать два
enclosure с разными URL, подписями, размерами и точными OS-маркерами:

```xml
<sparkle:version>1.0.3.0</sparkle:version>
<sparkle:shortVersionString>1.0.3</sparkle:shortVersionString>
<enclosure url="https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.3/FocusBrowser_1.0.3_x64-mini-installer.exe"
           sparkle:os="windows-x64"
           sparkle:edSignature="&lt;X64_BASE64_ED25519_SIGNATURE&gt;"
           length="&lt;X64_BYTE_LENGTH&gt;"
           type="application/octet-stream" />
<enclosure url="https://github.com/DanilBend/FocusBrowser/releases/download/v1.0.3/FocusBrowser_1.0.3_arm64-mini-installer.exe"
           sparkle:os="windows-arm64"
           sparkle:edSignature="&lt;ARM64_BASE64_ED25519_SIGNATURE&gt;"
           length="&lt;ARM64_BYTE_LENGTH&gt;"
           type="application/octet-stream" />
```

`<BYTE_LENGTH>` — точный размер финального файла в байтах. Команда
`sign --verbose` печатает в stdout не весь XML и не только подпись, а ровно
фрагмент из двух атрибутов:

```text
sparkle:edSignature="<BASE64_ED25519_SIGNATURE>" length="<BYTE_LENGTH>"
```

Без `--verbose` команда печатает только Base64-подпись. В CI проверяйте exit
code и формат stdout, затем переносите оба значения в атрибуты того enclosure,
файл которого был подписан:

```powershell
$payload = (Resolve-Path .\FocusBrowser_1.0.3_x64-mini-installer.exe).Path
$fragment = ((& $tool sign --verbose `
  --private-key-file .\winsparkle-private.key $payload) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or
    $fragment -notmatch '^sparkle:edSignature="([A-Za-z0-9+/]{86}==)" length="([0-9]+)"$') {
  throw "Unexpected winsparkle-tool sign output"
}
$signature = $Matches[1]
$signedLength = [int64]$Matches[2]
if ($signedLength -ne (Get-Item -LiteralPath $payload).Length) {
  throw "Signed length does not match payload length"
}
```

Для production используйте проверяемый локальный генератор из репозитория,
который дополнительно проверяет версию, имя payload, PE x64, расположение ключа
вне репозитория, подпись и итоговый XML:

```powershell
.\build_support\prepare_winsparkle_appcast.ps1 `
  -ToolPath $tool `
  -PrivateKeyPath 'D:\offline\winsparkle-private.key' `
  -PublicKey $env:WINSPARKLE_ED_KEY `
  -PayloadPath .\build\FocusBrowser_1.0.3_x64-mini-installer.exe `
  -OutputPath .\build\appcast-x64.xml `
  -Version 1.0.3.0 `
  -ShortVersion 1.0.3 `
  -ReleaseTag v1.0.3
```

Локальная проверка до публикации:

```powershell
& $tool verify `
  --public-key '<BASE64_ED25519_PUBLIC_KEY>' `
  --signature '<BASE64_ED25519_SIGNATURE>' `
  .\FocusBrowser_1.0.3_x64-mini-installer.exe
```

Угловые скобки — placeholders; их нельзя оставлять в production XML. Проверьте
XML-парсером, скачайте каждый enclosure по опубликованному HTTPS URL и повторите
`verify` именно над скачанным файлом.

## 6. Неизменяемый release: правильный порядок

1. Зафиксируйте commit, версии зависимостей и номер `1.0.3.0`.
2. Соберите x64 с production public key и
   `https://danilbend.github.io/FocusBrowser/appcast-x64.xml`. ARM64 выпускается
   позже отдельным бинарником и отдельным feed, а не универсальным enclosure.
3. Выполните тесты на чистой Windows x64, включая user-level и system-level
   установку.
4. Если есть Authenticode, сначала подпишите внутренние PE-файлы, затем
   упакуйте installer, подпишите финальные installer-файлы и получите timestamp.
5. Только после всех модификаций Authenticode подпишите финальный
   `mini-installer.exe` через Ed25519. Любое последующее изменение файла делает
   Ed25519-подпись недействительной.
6. Вычислите SHA-256 и точный размер каждого финального asset.
7. Сгенерируйте `appcast-x64.xml` локальным helper-скриптом и повторно проверьте
   Ed25519-подпись.
8. Создайте GitHub Release `v1.0.3` как draft.
9. Загрузите в draft x64 assets, checksums, точную аудит-копию
   `appcast-x64.xml` и release notes. Скачайте их обратно и сравните SHA-256.
10. Опубликуйте draft. Включённая immutable releases policy блокирует изменение
    тега и assets и создаёт release attestation.
11. Запустите `publish-appcast.yml` из `main`. Он без приватного ключа публично
    скачает immutable mini-installer и аудит-копию appcast, сверит metadata,
    SHA-256, PE x64, XML и Ed25519, запретит rollback и только затем развернёт
    точные проверенные байты на GitHub Pages.
12. На чистой машине с установленной `1.0.2.0` (release display `1.0.2`)
    выполните ручную проверку обновления. Для следующих выпусков дополнительно
    проверяйте обновление с предыдущей stable-версии.

Если после публикации найдена ошибка, не заменяйте asset. Выпустите новую
версию/тег, затем переведите feed на неё. Сначала draft и все assets, публикация
— последним действием.

## 7. Разделение build/release и production appcast

Текущий `.github/workflows/main.yml` остаётся заготовкой, а `do-release` должен
быть выключен до отдельного аудита. Перед его будущим включением:

- замените все жёстко заданные внешние repository/description URL на
  `DanilBend/FocusBrowser` или `${{ github.repository }}`;
- удалите либо сделайте условными существующие Azure Artifact Signing steps и
  соответствующие секреты, если используется unsigned-сборка или SignPath;
- добавьте защищённое окружение к signing/publish jobs;
- оставьте `permissions: contents: read` глобально и выдавайте `contents: write`
  только publish job;
- закрепите сторонние Actions на полном commit SHA;
- генерируйте Ed25519-подпись и appcast только после финальной упаковки и
  возможной Authenticode-подписи;
- разделите x64/ARM64 feed variables и запретите публикацию при пустом или
  невалидном ключе/URL;
- не запускайте signing job из `pull_request`, fork или произвольной ветки;
- задайте короткий retention для внутренних unsigned-артефактов и не публикуйте
  их как Release assets;
- публикуйте Release сначала как draft, а production appcast — последним;
- добавьте smoke-test: загрузить enclosure, проверить размер, SHA-256,
  Ed25519-подпись и архитектуру PE-файла.

Production-развёртывание feed выполняет отдельный
`.github/workflows/publish-appcast.yml`: он запускается только из `main`,
ограничен веточной policy окружения `github-pages`, требует stable immutable
release и не имеет доступа к приватному ключу.

## 8. Обновление без потери профиля

Release 1.0 фиксирует install identity. В последующих версиях нельзя менять:

- company path `FocusBrowser`;
- product path и имя приложения `Focus Browser`;
- base app id `FocusBrowser`;
- ProgID-префиксы `FocusHTM` и `FocusPDF`;
- URL scheme `focus`;
- WinSparkle company/app identity `Focus Browser` / `Focus Browser` и вытекающий
  registry path `HKCU\Software\Focus Browser\Focus Browser\WinSparkle`;
- Active Setup GUID, Toast/Elevator/Tracing CLSID/IID и связанные component IDs
  в `patches/focus/windows/change-branding.patch`.

`win_sparkle_set_app_details()` получает полную техническую версию, например
`1.0.0.0`, но первые два аргумента образуют постоянную registry identity. Не
меняйте их и не вызывайте `win_sparkle_set_registry_path()` с новым путём без
явной миграции прежних значений updater.

Определение NSIS `PRODUCT_GUID` сейчас нигде в installer script не используется:
оно не участвует ни в обнаружении установки, ни в сохранении профиля, ни в
WinSparkle state. Не рассматривайте этот неиспользуемый define как upgrade-
идентификатор. Если он позже будет реально подключён к registry/MSI-механизму,
его роль и миграция должны быть спроектированы и протестированы отдельно.

По умолчанию профиль находится под
`%LOCALAPPDATA%\FocusBrowser\Focus Browser\User Data`. Installer должен
обновлять существующую установку на месте. Запрещены удаление `User Data`,
предварительный uninstall и флаги очистки профиля.

Перед каждым стабильным релизом проверьте матрицу:

| Исходная установка | Обновление | Что проверить |
| --- | --- | --- |
| previous stable, user-level x64 | новая x64 | bookmarks, history, cookies, passwords, extensions, settings, session |
| previous stable, system-level x64 | новая x64 | то же плюс права/UAC и default-browser registration |
| previous stable, user-level ARM64 | новая ARM64 | то же плюс отсутствие x64 payload |
| previous stable, system-level ARM64 | новая ARM64 | то же плюс helper/update flow |

Дополнительно проверьте несколько профилей, гостевой режим, восстановление
вкладок и встроенные компоненты. До major-изменений делайте backup `User Data`.
Downgrade профиля не поддерживается: откат выполняется только с резервной копией.

Сохранённые пароли на Windows защищены DPAPI и могут быть привязаны к учётной
записи/машине. Простое копирование каталога профиля не является полноценной
синхронизацией или гарантированным переносом паролей.

## 9. Бесплатный Authenticode через SignPath — опционально

SignPath принимает заявки open-source проектов на бесплатную подпись, но
соответствие требованиям и одобрение не гарантированы.

Если проект принят:

1. Подключите GitHub как trusted build system по официальной инструкции.
2. Ограничьте подписываемые артефакты конкретным workflow, commit/tag и
   protected environment.
3. Не выгружайте сертификат или его private key в репозиторий/GitHub secrets.
4. Подписывайте внутренние PE и финальные installers до Ed25519-подписи payload.
5. Оставьте WinSparkle Ed25519 включённым: SignPath не заменяет подпись
   автообновлений.
6. Если включаете helper fallback, запишите в `WINSPARKLE_AUTHENTICODE_ORG`
   точное значение `Subject Organization (O)` выданного сертификата. Различие
   регистра допустимо, различие пробелов, пунктуации или полного текста — нет.

Если SignPath не одобрит проект и платного сертификата нет, релиз технически
можно распространять и обновлять по Ed25519, но Windows покажет неизвестного
издателя, а SmartScreen может предупреждать. SHA-256, HTTPS и GitHub attestation
не убирают это системное ограничение. Не обещайте пользователям обратное и не
советуйте отключать защиту Windows.

## 10. Аварийные сценарии

### Потерян приватный Ed25519-ключ

Старые установки не примут update, подписанный новым ключом. Остановите feed,
создайте новый key pair и распространяйте ручной installer с новым публичным
ключом. Возобновляйте автообновления только после достаточного перехода клиентов.

### Ключ скомпрометирован

Немедленно остановите production feed, отзовите доступ release-maintainers и
проведите расследование. Не используйте старый ключ для «переходного» релиза:
атакующий тоже может им подписывать. Нужен доверенный ручной канал доставки
сборки с новым публичным ключом; Authenticode заметно улучшает этот сценарий.

### Опубликован плохой immutable release

Не удаляйте и не заменяйте assets. Исправьте код, увеличьте версию, создайте
новый draft release и после проверки переведите appcast на новый URL.

### Appcast опубликован раньше assets

Уберите/заморозьте feed до готовности assets, затем повторите полный download и
signature smoke-test. Клиентам нельзя предлагать URL, который ещё не доступен.

## 11. Финальный чек-лист 1.0.3

- [ ] В исходниках, артефактах, metadata и UI используется только актуальное имя.
- [ ] Production private key существует только в зашифрованных offline-копиях.
- [ ] Окружение `github-pages` разрешает deployment только из `main`.
- [ ] Pinned `winsparkle-tool` archive совпал с зафиксированным SHA-256.
- [ ] About/installed/WinSparkle и `sparkle:version` используют `1.0.3.0`;
      `1.0.3` осталась только presentation-версией упаковки.
- [ ] x64 собран с правильным appcast URL; будущий ARM64 получит отдельный feed.
- [ ] Enclosure помечен `windows-x64`, а не generic `windows`.
- [ ] WinSparkle registry identity осталась
      `HKCU\Software\Focus Browser\Focus Browser\WinSparkle`.
- [ ] `WINSPARKLE_AUTHENTICODE_ORG` либо не задана, либо точно равна `Subject O`
      сертификата без учёта регистра.
- [ ] Все installers проверены на чистой Windows.
- [ ] Upgrade сохраняет профиль, пароли, расширения и сессию.
- [ ] Финальные payload подписаны Ed25519 после последнего изменения байтов.
- [ ] SHA-256, размеры и Ed25519 проверены после обратного скачивания.
- [ ] Release создан как draft и содержит все assets до публикации.
- [ ] Immutable releases включены.
- [ ] Appcast опубликован после Release и проверен установленным клиентом.
- [ ] Ограничение SmartScreen честно описано в release notes/download page.

## Официальные источники

- [WinSparkle: signing updates](https://github.com/vslavik/winsparkle#signing-updates)
- [WinSparkle 0.9.3 official release](https://github.com/vslavik/winsparkle/releases/tag/v0.9.3)
- [WinSparkle: EdDSA migration](https://github.com/vslavik/winsparkle/wiki/Upgrading-from-DSA-to-EdDSA-signatures)
- [GitHub: immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- [GitHub: enable release immutability](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)
- [GitHub: environments and deployment protection](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [GitHub: using secrets in Actions](https://docs.github.com/en/actions/reference/security/secrets)
- [GitHub: secure use of Actions](https://docs.github.com/en/actions/reference/security/secure-use?learn=getting_started&learnProduct=actions)
- [SignPath open-source program](https://signpath.io/solutions/open-source-community)
- [SignPath GitHub trusted build](https://docs.signpath.io/trusted-build-systems/github)
