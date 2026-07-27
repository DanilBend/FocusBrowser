import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = relative => readFile(path.join(repoRoot, relative), 'utf8');

const onboardingRoot =
    'source_overrides/components/focus_onboarding';
const localeModule = await import(pathToFileURL(path.join(
    repoRoot, onboardingRoot, 'src/lib/locale.ts')));

for (const locale of ['ru', 'ru-RU', 'RU_ru']) {
  assert.equal(localeModule.isRussianUiLocale(locale), true, locale);
}
for (const locale of ['en-US', 'de', 'tr', 'uk', '']) {
  assert.equal(localeModule.isRussianUiLocale(locale), false, locale);
}

const russianJson = await read(`${onboardingRoot}/src/lib/strings.ru.json`);
assert.equal(russianJson.charCodeAt(0) === 0xFEFF, false, 'RU JSON has a BOM');
const russian = JSON.parse(russianJson);
assert.equal(russian.pageTitle, 'Настройка Focus Browser');
assert.equal(russian.navigationStep, 'Шаг');

const generated = await read(`${onboardingRoot}/src/lib/strings.ts`);
assert.match(generated, /import russianStrings from '\.\/strings\.ru\.json';/);
assert.match(generated, /isRussianUiLocale\(getUiLocale\(\)\)/);
assert.match(generated, /getString\('applicationLocale'\)/);
assert.match(generated,
             /return globalThis\.navigator\?\.language \|\| applicationLocale/);
assert.match(generated, /"pageTitle": "Focus Browser Setup"/);
const generatedKeys = new Set(
    [...generated.matchAll(/^ \| "([^"]+)";?$/gm)].map(match => match[1]));
assert.deepEqual(new Set(Object.keys(russian)), generatedKeys);

const onboardingSource =
    await read(`${onboardingRoot}/focus_onboarding_strings.grdp`);
assert.doesNotMatch(onboardingSource, /[А-Яа-яЁё]/);
assert.match(onboardingSource, />\s*Focus Browser Setup\s*</);

const localePatch = await read(
    'focus-chromium/patches/focus/core/windows-first-run-locale.patch');
assert.match(localePatch, /^-\s*if \(GoogleUpdateSettings::GetLanguage/m);
assert.doesNotMatch(localePatch, /^\+.*(?:SetString|kApplicationLocale|kLang)/m);
const userLanguageCall = localePatch.indexOf(
    'GetUserPreferredUILanguageList(&user_languages)');
const fallbackLanguageCall = localePatch.indexOf(
    'GetThreadPreferredUILanguageList(&fallback_languages)');
assert.notEqual(userLanguageCall, -1);
assert.notEqual(fallbackLanguageCall, -1);
assert.ok(userLanguageCall < fallbackLanguageCall,
          'Windows user language order must precede merged fallbacks');
assert.match(localePatch,
             /CHECK\(got_user_languages \|\| got_fallback_languages\)/);
assert.match(localePatch,
             /std::ranges::find\(ascii_languages, ascii_language\)/);
assert.doesNotMatch(
    localePatch, /^diff --git a\/ui\/base\/l10n\/l10n_util\.cc/m);

const onboardingLocalePatch = await read(
    'focus-chromium/patches/focus/core/onboarding-application-locale.patch');
const resolvedLocalePattern =
    /l10n_util::GetApplicationLocale\(std::string\(\), false\)/;
assert.match(onboardingLocalePatch, resolvedLocalePattern);
assert.doesNotMatch(onboardingLocalePatch,
                    /GetLoadedLocale|g_browser_process/);

const ntpPatch = await read(
    'focus-chromium/patches/focus/ui/focus-new-tab-page.patch');
assert.doesNotMatch(ntpPatch, /[А-Яа-яЁё]/);
assert.match(ntpPatch, /aria-label="\$i18n\{title\}"/);
assert.match(ntpPatch, /aria-label="\$i18n\{searchBoxHint\}"/);
assert.match(ntpPatch, /aria-label="\$i18n\{addLinkTitle\}"/);
assert.match(
    ntpPatch,
    /^\+.*placeholder-text="\$i18n\{searchBoxPlaceholder\}"/m);

const settings = await read('source_overrides/chrome/app/settings_strings.grdp');
assert.match(settings, />\s*Smooth interface animations\s*</);
assert.doesNotMatch(settings, /[А-Яа-яЁё]/);

const bubble = await read(
    'source_overrides/chrome/browser/ui/views/location_bar/focus_block_bubble_view.cc');
assert.match(bubble, resolvedLocalePattern);
assert.doesNotMatch(bubble, /GetLoadedLocale|g_browser_process/);
assert.match(bubble, /Protection across the browser/);
assert.match(bubble, /Защита во всём браузере/);

const shieldPatch = await read(
    'patches/focus/windows/focusblock-location-bar-shield.patch');
assert.match(shieldPatch, resolvedLocalePattern);
assert.doesNotMatch(shieldPatch, /GetLoadedLocale|g_browser_process/);
assert.match(shieldPatch, /FocusBlock — ad protection/);
assert.match(shieldPatch, /FocusBlock — защита от рекламы/);

const meditation = await read(
    'source_overrides/chrome/browser/resources/meditation/meditation.html');
assert.match(meditation, /<html lang="en">/);
assert.match(meditation, /Meditation · Focus Browser/);
const meditationScript = await read(
    'source_overrides/chrome/browser/resources/meditation/meditation.ts');
assert.match(
    meditationScript,
    /applicationLocale = globalThis\.navigator\?\.language \|\|/);
const meditationUi = await read(
    'source_overrides/chrome/browser/ui/webui/meditation/meditation_ui.cc');
assert.match(meditationUi, resolvedLocalePattern);
assert.doesNotMatch(meditationUi, /GetLoadedLocale|g_browser_process/);

console.log(`Focus localization QA passed (${generatedKeys.size} onboarding strings).`);
