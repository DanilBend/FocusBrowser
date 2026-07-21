// Copyright 2026 The Focus Browser Authors
// Static contract checks for the unified Focus motion preference.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const activeRoot = path.join(repoRoot, 'build', 'src');
const overridesRoot = path.join(repoRoot, 'source_overrides');

const read = (root, relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');

const digest = file => crypto.createHash('sha256')
    .update(fs.readFileSync(file))
    .digest('hex');

const pairedFiles = [
  'components/focus_services/pref_names.h',
  'chrome/browser/ui/browser_ui_prefs.cc',
  'chrome/browser/ui/views/toolbar/toolbar_view.cc',
  'chrome/browser/extensions/api/settings_private/prefs_util.cc',
  'chrome/browser/ui/webui/onboarding/onboarding_handler.cc',
  'components/focus_onboarding/BUILD.gn',
  'components/focus_onboarding/focus_onboarding_strings.grdp',
  'components/focus_onboarding/src/App.svelte',
  'components/focus_onboarding/src/app.css',
  'components/focus_onboarding/src/components/PageNavigation.svelte',
  'components/focus_onboarding/src/lib/browser/focus.ts',
  'components/focus_onboarding/src/lib/browser/prefs.ts',
  'components/focus_onboarding/src/lib/motion.ts',
  'components/focus_onboarding/src/lib/onboarding-flow.ts',
  'components/focus_onboarding/src/lib/strings.ts',
  'components/focus_onboarding/src/pages/FocusSetup.svelte',
  'components/focus_onboarding/src/pages/Welcome.svelte',
  'chrome/app/settings_strings.grdp',
  'chrome/browser/resources/settings/appearance_page/appearance_page.html',
  'chrome/browser/ui/webui/settings/settings_localized_strings_provider.cc',
  'chrome/browser/resources/meditation/meditation.css',
  'chrome/browser/resources/meditation/meditation.ts',
  'chrome/browser/ui/webui/meditation/BUILD.gn',
  'chrome/browser/ui/webui/meditation/meditation_ui.cc',
  'third_party/focus_youtube/css/popup.css',
  'third_party/focus_youtube/js/popup.js',
  'third_party/ublock/css/popup-fenix.css',
  'third_party/ublock/js/popup-fenix.js',
];

for (const relativePath of pairedFiles) {
  const active = path.join(activeRoot, relativePath);
  const override = path.join(overridesRoot, relativePath);
  assert.ok(fs.existsSync(active), `missing active file: ${relativePath}`);
  assert.ok(fs.existsSync(override), `missing override file: ${relativePath}`);
  assert.equal(digest(active), digest(override), `active/override drift: ${relativePath}`);
}

const prefNames = read(activeRoot, 'components/focus_services/pref_names.h');
assert.match(prefNames,
             /kFocusMotionEnabled\[\][\s\S]*"focus\.ui\.motion_enabled"/);

const browserPrefs = read(activeRoot, 'chrome/browser/ui/browser_ui_prefs.cc');
assert.match(browserPrefs,
             /RegisterBooleanPref\(prefs::kFocusMotionEnabled, true\)/);

const toolbarView = read(
    activeRoot,
    'chrome/browser/ui/views/toolbar/toolbar_view.cc');
assert.match(toolbarView, /FocusBlockBubbleView::ShowBubble/);
assert.match(toolbarView, /FocusYoutubeBubbleView::ShowBubble/);
assert.doesNotMatch(toolbarView, /popup_resource\.append\("\?focusMotion=0"\)/);

const prefsUtil = read(
    activeRoot,
    'chrome/browser/extensions/api/settings_private/prefs_util.cc');
assert.match(prefsUtil, /kFocusMotionEnabled[\s\S]*PrefType::kBoolean/);

const onboardingHandler = read(
    activeRoot,
    'chrome/browser/ui/webui/onboarding/onboarding_handler.cc');
assert.match(onboardingHandler, /FindBool\("smoothAnimations"\)/);
assert.match(onboardingHandler,
             /SetBoolean\(prefs::kFocusMotionEnabled, \*smooth_animations\)/);

const onboardingPrefs = read(
    activeRoot,
    'components/focus_onboarding/src/lib/browser/prefs.ts');
assert.match(onboardingPrefs, /'ui\.motion_enabled': boolean/);

const motionModule = read(
    activeRoot,
    'components/focus_onboarding/src/lib/motion.ts');
assert.match(motionModule, /prefers-reduced-motion: reduce/);
assert.match(motionModule, /prefs\["ui\.motion_enabled"\]/);
assert.match(motionModule, /dataset\.motion/);

const focusSetup = read(
    activeRoot,
    'components/focus_onboarding/src/pages/FocusSetup.svelte');
assert.match(focusSetup, /aria-checked=\{\$smoothAnimations\}/);
assert.match(focusSetup, /s\.focus\.motionTitle/);

const onboardingCss = read(
    activeRoot,
    'components/focus_onboarding/src/app.css');
for (const required of [
  'html[data-motion="off"] *',
  '@media (prefers-reduced-motion: reduce)',
  'animation: none !important',
  'transition: none !important',
  '@keyframes focus-water-drift',
]) {
  assert.ok(onboardingCss.includes(required), `onboarding motion CSS missing: ${required}`);
}

const appearance = read(
    activeRoot,
    'chrome/browser/resources/settings/appearance_page/appearance_page.html');
assert.match(appearance, /prefs\.focus\.ui\.motion_enabled/);
assert.match(appearance, /\$i18n\{focusMotionDescription\}/);

const meditationUi = read(
    activeRoot,
    'chrome/browser/ui/webui/meditation/meditation_ui.cc');
assert.match(meditationUi, /AddBoolean\([\s\S]*"focusMotionEnabled"/);
assert.match(meditationUi, /GetBoolean\(prefs::kFocusMotionEnabled\)/);

const meditationTs = read(
    activeRoot,
    'chrome/browser/resources/meditation/meditation.ts');
assert.match(meditationTs, /getBoolean\('focusMotionEnabled'\)/);
assert.match(meditationTs, /prefers-reduced-motion: reduce/);

const meditationCss = read(
    activeRoot,
    'chrome/browser/resources/meditation/meditation.css');
assert.match(meditationCss, /@keyframes meditation-water/);
assert.match(meditationCss,
             /html\[data-motion=["']off["']\][\s\S]*transition: none !important/);

const focusYoutubeJs = read(
    activeRoot,
    'third_party/focus_youtube/js/popup.js');
assert.match(focusYoutubeJs, /get\('focusMotion'\) === '0'/);

const focusYoutubeCss = read(
    activeRoot,
    'third_party/focus_youtube/css/popup.css');
assert.match(focusYoutubeCss,
             /html\[data-motion="off"\][\s\S]*transition: none !important/);

const focusBlockJs = read(activeRoot, 'third_party/ublock/js/popup-fenix.js');
assert.match(focusBlockJs, /get\('focusMotion'\) === '0'/);

const focusBlockCss = read(activeRoot, 'third_party/ublock/css/popup-fenix.css');
assert.match(focusBlockCss,
             /:root\[data-motion="off"\][\s\S]*transition: none !important/);
assert.match(focusBlockCss, /@media \(prefers-reduced-motion: reduce\)/);

const settingsStrings = read(activeRoot, 'chrome/app/settings_strings.grdp');
assert.match(settingsStrings,
             /IDS_SETTINGS_FOCUS_MOTION[^]*Smooth interface animations/);
assert.match(settingsStrings,
             /IDS_SETTINGS_FOCUS_MOTION_DESCRIPTION[^]*Calm, fluid transitions/);
assert.doesNotMatch(settingsStrings, /[А-Яа-яЁё]/);

const russianTranslations = JSON.parse(read(
    repoRoot, 'focus-chromium/i18n/translations/ru.json'));
const russianTranslationByName = new Map(
    russianTranslations.map(entry => [entry.name, entry]));
assert.equal(
    russianTranslationByName.get('IDS_SETTINGS_FOCUS_MOTION')?.message,
    'Плавные анимации интерфейса');
assert.equal(
    russianTranslationByName.get('IDS_SETTINGS_FOCUS_MOTION_DESCRIPTION')
        ?.message,
    'Спокойные плавные переходы в Focus Browser. Системное уменьшение движения всегда имеет приоритет.');

const ntpUi = read(
    activeRoot,
    'chrome/browser/ui/webui/new_tab_page/new_tab_page_ui.cc');
assert.match(ntpUi, /#include "components\/focus_services\/pref_names\.h"/);
assert.match(
    ntpUi,
    /AddBoolean\(\s*"focusMotionEnabled",[\s\S]*GetBoolean\(prefs::kFocusMotionEnabled\)/);
assert.match(
    ntpUi,
    /pref_change_registrar_\.Add\([\s\S]*prefs::kFocusMotionEnabled[\s\S]*OnFocusMotionEnabledChanged/);
assert.match(
    ntpUi,
    /OnFocusMotionEnabledChanged\(\)[\s\S]*focus-motion-enabled-changed/);

const ntpSearchbox = read(
    activeRoot,
    'chrome/browser/resources/new_tab_page/ntp_searchbox.ts');
const sharedSearchboxInput = read(
    activeRoot,
    'ui/webui/resources/cr_components/searchbox/searchbox_input.ts');
const sharedSearchboxInputHtml = read(
    activeRoot,
    'ui/webui/resources/cr_components/searchbox/searchbox_input.html.ts');
const sharedSearchboxInputCss = read(
    activeRoot,
    'ui/webui/resources/cr_components/searchbox/searchbox_input.css');
assert.match(ntpSearchbox, /getBoolean\('focusMotionEnabled'\)/);
assert.match(ntpSearchbox, /focus-motion-enabled-changed/);
assert.match(ntpSearchbox, /prefers-reduced-motion: reduce/);
assert.match(ntpSearchbox, /forced-colors: active/);
assert.match(
    ntpSearchbox,
    /onSearchboxInputTextUpdated\(e, \/\*is_composing=\*\/ false\)/);
assert.match(ntpSearchbox, /focusTypingCommittedValue_/);
assert.match(ntpSearchbox, /focusTypingCompositionStartValue_/);
assert.match(ntpSearchbox, /compositionstart/);
assert.match(ntpSearchbox, /compositionend/);
assert.match(ntpSearchbox, /requestAnimationFrame/);
assert.match(
    ntpSearchbox,
    /e\.detail\.isComposing[\s\S]*e\.detail\.value === this\.focusTypingCommittedValue_/);
assert.match(
    ntpSearchbox,
    /const previousValue = this\.focusTypingCommittedValue_[\s\S]*revealFocusTypingInsertion\(previousValue, currentValue\)/);
assert.match(
    ntpSearchbox,
    /focusReducedMotionQuery_\.addEventListener[\s\S]*focusForcedColorsQuery_\.addEventListener/);
assert.match(
    ntpSearchbox,
    /focusReducedMotionQuery_\.removeEventListener[\s\S]*focusForcedColorsQuery_\.removeEventListener/);
assert.doesNotMatch(ntpSearchbox, /(?:container|input)\.animate\(/);
assert.doesNotMatch(ntpSearchbox, /boxShadow|focus-typing-water/);

assert.match(sharedSearchboxInput, /new Intl\.Segmenter\(undefined, \{granularity: 'grapheme'\}\)/);
assert.match(
    sharedSearchboxInput,
    /revealFocusTypingInsertion\(previousValue: string, currentValue: string\)/);
assert.match(sharedSearchboxInput, /prefixLength[\s\S]*suffixLength/);
assert.match(sharedSearchboxInput, /currentGraphemes\.length > 512/);
assert.match(sharedSearchboxInput, /insertedElements\.slice\(0, 24\)/);
assert.equal(
    (sharedSearchboxInput.match(/element\.animate\(/g) ?? []).length,
    1,
    'NTP typing reveal must animate only inserted grapheme elements');
assert.match(
    sharedSearchboxInput,
    /opacity: 0\.12, transform: 'translateY\(3px\)'[\s\S]*duration: 180/);
assert.match(sharedSearchboxInput, /Math\.min\(index, 10\) \* 12/);
assert.match(
    sharedSearchboxInput,
    /animation\.id = 'focus-typing-grapheme-reveal'/);
assert.match(sharedSearchboxInput, /input\.selectionStart !== input\.selectionEnd/);
assert.match(sharedSearchboxInput, /input\.scrollLeft[\s\S]*input\.scrollTop/);
assert.match(sharedSearchboxInputHtml, /id="focusTypingMirror" aria-hidden="true" hidden/);
assert.match(sharedSearchboxInputHtml, /@compositionstart="\$\{this\.onFocusTypingCompositionstart_\}"/);
assert.match(sharedSearchboxInputHtml, /@select="\$\{this\.onFocusTypingInputSelect_\}"/);
assert.match(
    sharedSearchboxInputCss,
    /:host\(\[focus-typing-reveal-active\]\) #input[\s\S]*-webkit-text-fill-color: transparent/);
assert.match(sharedSearchboxInputCss, /prefers-reduced-motion: reduce/);
assert.match(sharedSearchboxInputCss, /forced-colors: active/);
assert.doesNotMatch(sharedSearchboxInputCss, /box-shadow/);

const ntpAppCss = read(
    activeRoot,
    'chrome/browser/resources/new_tab_page/app.css');
assert.doesNotMatch(
    ntpAppCss,
    /#searchboxContainer \{[\s\S]*border-radius: calc\(0\.5 \* var\(--cr-searchbox-height\)\)/);

const ntpMotionPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/ui/ntp-typing-motion.patch');
assert.match(ntpMotionPatch, /ui\/webui\/resources\/cr_components\/searchbox\/searchbox_input\.ts/);
assert.match(ntpMotionPatch, /focus-typing-grapheme-reveal/);
assert.doesNotMatch(ntpMotionPatch, /boxShadow|focus-typing-water/);

const locationBarView = read(
    activeRoot,
    'chrome/browser/ui/views/location_bar/location_bar_view.cc');
assert.match(locationBarView, /prefs::kFocusMotionEnabled/);
assert.match(locationBarView, /typing_animation_\.SetSlideDuration\(base::Milliseconds\(210\)\)/);
assert.match(locationBarView, /typing_animation_\.Reset\(\);[\s\S]*typing_animation_\.Show\(\)/);
assert.match(locationBarView, /IsOmniboxTypingAnimationRunning\(\) const/);
assert.match(locationBarView, /GetOmniboxTypingAnimationValue\(\) const/);
assert.match(locationBarView, /gfx::Animation::ShouldRenderRichAnimation\(\)/);
assert.match(locationBarView, /gfx::Animation::PrefersReducedMotion\(\)/);
assert.match(locationBarView, /PreferredContrast::kMore/);
assert.doesNotMatch(
    locationBarView,
    /std::sin\(typing_animation_\.GetCurrentValue\(\)/);
const refreshBackgroundStart = locationBarView.indexOf(
    'void LocationBarView::RefreshBackground()');
const refreshBackgroundEnd = locationBarView.indexOf(
    '\nvoid LocationBarView::', refreshBackgroundStart + 1);
assert.ok(refreshBackgroundStart >= 0 && refreshBackgroundEnd > refreshBackgroundStart);
assert.doesNotMatch(
    locationBarView.slice(refreshBackgroundStart, refreshBackgroundEnd),
    /typing_animation_/,
    'typing reveal must not pulse the omnibox background');
assert.match(
    locationBarView,
    /bool LocationBarView::ShouldAnimateFocusMotion\(\) const[\s\S]*kFocusMotionEnabled[\s\S]*ShouldRenderRichAnimation[\s\S]*PrefersReducedMotion[\s\S]*PreferredContrast::kMore/);
assert.match(
    locationBarView,
    /OnOmniboxHovered\(bool is_hovering\)[\s\S]*!ShouldAnimateFocusMotion\(\)[\s\S]*hover_animation_\.Reset\(should_show_hover \? 1\.0 : 0\.0\)/);
assert.match(
    locationBarView,
    /OnFocusMotionPreferenceChanged\(\)[\s\S]*typing_animation_\.Reset\(\)[\s\S]*hover_animation_\.Reset/);

const locationBarHeader = read(
    activeRoot,
    'chrome/browser/ui/views/location_bar/location_bar_view.h');
assert.equal(
    (locationBarHeader.match(/gfx::SlideAnimation typing_animation_\{this\}/g) ?? []).length,
    1,
    'omnibox typing pulse must reuse one native animation instance');
assert.match(locationBarHeader, /IsOmniboxTypingAnimationRunning\(\) const/);
assert.match(locationBarHeader, /GetOmniboxTypingAnimationValue\(\) const/);

const omniboxView = read(
    activeRoot,
    'chrome/browser/ui/views/omnibox/omnibox_view_views.cc');
assert.match(
    omniboxView,
    /focus_typing_text_before_change_ = state_before_change_\.text/);
assert.match(
    omniboxView,
    /!ime_composing_before_change_ && is_ime_composing[\s\S]*focus_typing_text_before_composition_/);
assert.match(
    omniboxView,
    /ime_composing_before_change_ && !is_ime_composing[\s\S]*focus_typing_baseline = composition_baseline[\s\S]*new_state\.text != focus_typing_baseline/);
assert.match(
    omniboxView,
    /focus_typing_baseline = focus_typing_text_before_change_[\s\S]*new_state\.text != focus_typing_baseline/);
assert.match(
    omniboxView,
    /something_changed && committed_text_differs && !is_ime_composing/);
assert.match(omniboxView, /location_bar_view_->OnOmniboxInputEdited\(\)/);
assert.match(omniboxView, /focus_typing_inserted_range_/);
assert.match(omniboxView, /ExpandRangeToGraphemeBoundary\(reveal_range\)/);
assert.match(
    omniboxView,
    /std::clamp\(0\.18 \+ 0\.82 \* progress[\s\S]*SkColorSetA\(/);
assert.match(
    omniboxView,
    /Textfield::OnPaint\(canvas\);[\s\S]*if \(reveal_inserted_text\)[\s\S]*EmphasizeURLComponents\(\)/);

const omniboxViewHeader = read(
    activeRoot,
    'chrome/browser/ui/views/omnibox/omnibox_view_views.h');
assert.match(omniboxViewHeader, /focus_typing_text_before_change_/);
assert.match(omniboxViewHeader, /focus_typing_text_before_composition_/);
assert.match(omniboxViewHeader, /has_focus_typing_composition_baseline_/);
assert.match(omniboxViewHeader, /gfx::Range focus_typing_inserted_range_/);

console.log('Focus motion contract verified.');
