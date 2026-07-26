<p align="right"><strong>Русский</strong> · <a href="README.en.md">English</a></p>

<p align="center">
  <img src="focus-chromium/resources/branding/product_logo.svg" width="96" alt="Логотип Focus Browser">
</p>

<h1 align="center">Focus Browser</h1>

<p align="center">
  Браузер на базе Chromium для сфокусированной работы в Windows.
</p>

<p align="center">
  <a href="https://github.com/DanilBend/FocusBrowser/releases/latest">Скачать</a>
  · <a href="https://github.com/DanilBend/FocusBrowser/issues">Сообщить об ошибке</a>
  · <a href="docs/RELEASING.md">Инструкция по выпуску</a>
</p>

## Focus Browser 1.0.3

Focus Browser сохраняет привычный движок Chromium, уменьшает количество
отвлекающих элементов и переносит основные инструменты концентрации прямо в
интерфейс браузера. Версия 1.0.3 основана на Chromium 150.0.7871.128 и рассчитана
на 64-битные Windows 10 и Windows 11.

### Основные возможности

- **FocusBlock** — встроенная неудаляемая нативная защита от рекламы и
  трекеров. По умолчанию она включена, может быть отключена глобально или для
  текущего сайта, а её щит расположен справа внутри адресной строки.
- **FocusYoutube** — стабильный перечёркнутый значок YouTube внутри адресной
  строки и 25 основных локальных настроек. Все отдельные фильтры изначально
  выключены; переход на внешний сайт для настройки не требуется.
- **Чистая новая вкладка** — поиск, выбранные пользователем ярлыки, кнопка
  добавления ярлыка и компактная кнопка настройки страницы.
- **Русская первоначальная настройка** — выбор поиска и внешнего вида, импорт
  закладок и истории из обнаруженного стандартного профиля Chrome, менеджер
  паролей, назначение браузером по умолчанию и ярлыки.
- **Плавные анимации** — единая настройка движения интерфейса и ввода текста,
  которую можно отключить. Системное уменьшение движения всегда имеет
  приоритет.
- **Медитация** — отдельная страница из главного меню, открывающая выбранное
  видео непосредственно на YouTube без ошибки встроенного проигрывателя 153.
- Встроенный менеджер паролей Chromium, установка расширений напрямую из
  Chrome Web Store, проприетарные медиакодеки и точки интеграции Widevine.
- **Обновления без прерывания работы** — найденное обновление предлагается при
  следующем запуске с вариантами обновить сейчас, напомнить позже или пропустить
  конкретную версию.

## Сравнение с другими браузерами

Focus Browser не пытается заменить каждый специализированный инструмент. Его
главное отличие — защита от рекламы, управление отвлекающими элементами YouTube
и спокойная рабочая среда собраны в одном браузере и доступны без набора
сторонних расширений.

| Возможность | **Focus Browser 1.0.3** | Helium | Zen Browser | Firefox | Brave | Chrome | Edge |
|---|---|---|---|---|---|---|---|
| Полная блокировка рекламы и трекеров без расширения | **✅ FocusBlock, включён по умолчанию** | ✅ Встроена и включена по умолчанию | ◐ Защита от трекеров, не полный блокировщик рекламы | ◐ Enhanced Tracking Protection, не полный блокировщик рекламы | ✅ Shields включён по умолчанию | ◐ Блокирует только навязчивую рекламу | ◐ Tracking Prevention, не полный блокировщик рекламы |
| Управление отвлекающими элементами YouTube на компьютере | **✅ 25 локальных переключателей в панели адресной строки** | — Отдельная панель не заявлена | — Отдельная панель не заявлена | — Отдельная панель не заявлена | ◐ Есть несколько YouTube-фильтров; часть настроек доступна только на мобильных устройствах | — Отдельная панель не заявлена | — Отдельная панель не заявлена |
| Инструменты спокойного интерфейса | **✅ Чистая новая вкладка, плавный ввод текста, медитация** | Минималистичные макеты и Frameless Mode | Compact Mode, вертикальные вкладки и Workspaces | Reader View, боковая панель и Focus Timer | Speedreader и вертикальные вкладки | Reading Mode и вертикальные вкладки | Immersive Reader и вертикальные вкладки |
| Экосистема расширений | **Chrome Web Store** | Chromium extensions | Firefox Add-ons | Firefox Add-ons | Chrome Web Store | Chrome Web Store | Edge Add-ons и Chrome Web Store |
| Облачная синхронизация данных браузера | **— Локальный импорт закладок и истории из Chrome** | — Пока отсутствует | ◐ Mozilla Account; раскладка окон Zen синхронизируется только на одном устройстве | ✅ Firefox Sync | ✅ Brave Sync | ✅ Google Sync | ✅ Microsoft Sync |
| Исходный код браузерного проекта | **✅ Открыт** | ✅ Открыт | ✅ Открыт | ✅ Открыт | ✅ Открыт | ◐ Открыт Chromium, но не весь Chrome | ◐ Открыт Chromium, но не весь Edge |
| Основная доступность | **Windows 10/11, x64** | Desktop, beta | Desktop, beta | Desktop и mobile | Desktop и mobile | Desktop и mobile | Desktop и mobile |

**Главное преимущество Focus Browser:** FocusBlock и 25 настроек FocusYoutube
работают как единая часть браузера. Среди перечисленных продуктов только Brave
также документирует специальные YouTube-фильтры, но набор и доступность зависят
от платформы; Focus Browser предоставляет единую локальную панель на Windows.

Обозначения: ✅ — сопоставимая возможность встроена; ◐ — частичная возможность
или другой охват; — — сопоставимая встроенная возможность не найдена в
официальной документации. Сравнение составлено по официальным публичным
материалам на 26 июля 2026 года. Скорость и потребление памяти намеренно не
ранжируются без единого воспроизводимого теста.

<details>
<summary>Официальные источники сравнения</summary>

- **Helium:** [возможности и FAQ](https://helium.computer/).
- **Zen Browser:** [защита](https://docs.zen-browser.app/security),
  [Compact Mode](https://docs.zen-browser.app/user-manual/compact-mode),
  [расширения](https://docs.zen-browser.app/user-manual/extensions) и
  [синхронизация окон](https://docs.zen-browser.app/user-manual/window-sync).
- **Firefox:** [защита от отслеживания](https://support.mozilla.org/en-US/kb/enhanced-tracking-protection-firefox-desktop),
  [боковая панель и вертикальные вкладки](https://support.mozilla.org/en-US/kb/use-sidebar-access-tools-and-vertical-tabs),
  [Focus Timer](https://www.firefox.com/en-US/features/focus/) и
  [Firefox Sync](https://support.mozilla.org/en-US/kb/sync).
- **Brave:** [основные возможности](https://brave.com/features/) и
  [YouTube-фильтры](https://brave.com/youtube-ad-blocker/).
- **Chrome:** [фильтрация навязчивой рекламы](https://support.google.com/chrome/answer/7632919),
  [Reading Mode](https://support.google.com/chrome/answer/14218344),
  [управление вкладками](https://support.google.com/chrome/answer/2391819),
  [синхронизация](https://support.google.com/chrome/answer/165139) и
  [различия Chrome и Chromium](https://chromium.googlesource.com/chromium/src/+/main/docs/chromium_browser_vs_google_chrome.md).
- **Edge:** [защита от отслеживания](https://support.microsoft.com/en-US/edge/learn-about-tracking-prevention-in-microsoft-edge),
  [Immersive Reader](https://support.microsoft.com/en-US/edge/use-immersive-reader-in-microsoft-edge),
  [вертикальные вкладки](https://www.microsoft.com/en-us/edge/features/vertical-tabs) и
  [синхронизация](https://support.microsoft.com/en-US/edge/sign-in-to-sync-microsoft-edge-across-devices).

</details>

Названия сторонних продуктов принадлежат их правообладателям.

## Скачивание и установка

Установщик и портативный архив доступны в разделе
[Releases](https://github.com/DanilBend/FocusBrowser/releases).

Установщик версии 1.0.3 пока не подписан Authenticode, поэтому Windows SmartScreen
может показать предупреждение **«Неизвестный издатель»**. Перед запуском
сверяйте SHA-256 с контрольными суммами, опубликованными вместе с выпуском.

Установка новой сборки поверх существующего Focus Browser сохраняет профиль.
В версии 1.0.3 встроен диалог обновления при следующем запуске, однако он
предлагает установку только из опубликованного и подписанного Ed25519
production-канала
`https://danilbend.github.io/FocusBrowser/appcast-x64.xml`. Если канал временно
недоступен, используйте ручную установку из GitHub Releases.

## Конфиденциальность и внешние сервисы

- FocusBlock фильтрует запросы локально.
- Настройки и фильтры FocusYoutube работают локально и не содержат аналитики,
  платежей, пожертвований или удалённых шрифтов.
- Страница медитации не загружает встроенный проигрыватель. После нажатия видео
  открывается в обычной вкладке YouTube, где действуют правила YouTube.
- Синхронизация аккаунта Google Chrome недоступна сторонним сборкам Chromium.
  Вместо неё предусмотрен локальный импорт закладок и истории из обнаруженного
  стандартного профиля Chrome. Пароли и данные автозаполнения этим импортёром
  не переносятся.
- Поддержка Widevine включена при сборке, но проприетарный CDM не хранится в
  репозитории и может загружаться отдельным компонентом Chromium.

Подробнее: [PRIVACY.md](PRIVACY.md).

## Сборка в Windows

Полный checkout Chromium и скомпилированные файлы намеренно не хранятся в Git.
Для чистой сборки требуется значительный объём диска и оперативной памяти.

1. Установите Visual Studio 2022 с workload **Desktop development with C++**,
   Windows SDK, Python 3.12+, Git и 7-Zip.
2. Включите поддержку длинных путей Windows.
3. Выполните в Developer PowerShell for VS 2022:

```powershell
git clone https://github.com/DanilBend/FocusBrowser.git
cd FocusBrowser
python build.py
python package.py --cpu-arch 64bit
```

Результат появится в `build/`. Подробности выпуска и обновления находятся в
[docs/RELEASING.md](docs/RELEASING.md), ручной чек-лист — в
[build_support/FOCUS_RELEASE_QA.md](build_support/FOCUS_RELEASE_QA.md).

## Структура репозитория

- `focus-chromium/` — конфигурация, брендинг и набор патчей Chromium.
- `patches/focus/` — интеграция с Windows и патчи обновления.
- `source_overrides/` — файлы, накладываемые поверх checkout Chromium.
- `installer/` — NSIS-установщик Focus Browser.
- `build.py`, `package.py` — подготовка исходников, сборка и упаковка.
- `build_support/`, `qa/` — автоматические проверки выпуска.

## Лицензии

Проектный код, охватываемый GPL, распространяется по GPL-3.0 — см.
[LICENSE](LICENSE). Chromium и импортированные компоненты сохраняют свои
исходные лицензии. Условия для отдельных компонентов перечислены в
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Доступен также
[информационный русский перевод](THIRD_PARTY_NOTICES.ru.md), который не заменяет
английский оригинал.

Условия и обязательные уведомления для встроенных сторонних компонентов
сохранены в файлах лицензий и
[уведомлениях о стороннем ПО](THIRD_PARTY_NOTICES.md).

## Участие в разработке

Перед pull request прочитайте [CONTRIBUTING.md](CONTRIBUTING.md). Сообщения об
уязвимостях отправляйте по инструкции [SECURITY.md](SECURITY.md), не раскрывая
чувствительные детали в публичном Issue.
