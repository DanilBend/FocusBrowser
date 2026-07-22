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

// NTP/WebUI takes the same Blink-native insertion path as every website. A
// second mirrored input would double-animate and can move the visible text or
// caret, so the searchbox must remain a single native input.
for (const source of [
  ntpUi,
  ntpSearchbox,
  sharedSearchboxInput,
  sharedSearchboxInputHtml,
  sharedSearchboxInputCss,
]) {
  assert.doesNotMatch(
      source,
      /focusTyping|focus-typing|focusMotionEnabled|focus-motion-enabled/);
}
assert.doesNotMatch(sharedSearchboxInputHtml, /focusTypingMirror/);
assert.doesNotMatch(
    sharedSearchboxInputCss,
    /-webkit-text-fill-color:\s*transparent|transform\s*:|translate\s*:/);

const focusPatchSeries = read(repoRoot, 'focus-chromium/patches/series');
assert.doesNotMatch(focusPatchSeries, /ntp-typing-motion\.patch/);
assert.match(focusPatchSeries, /omnibox-typing-motion-ranges\.patch/);
assert.doesNotMatch(focusPatchSeries, /omnibox-typing-opacity-ranges\.patch/);

const ntpAppCss = read(
    activeRoot,
    'chrome/browser/resources/new_tab_page/app.css');
assert.doesNotMatch(
    ntpAppCss,
    /#searchboxContainer \{[\s\S]*border-radius: calc\(0\.5 \* var\(--cr-searchbox-height\)\)/);

const motionRangesPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/ui/omnibox-typing-motion-ranges.patch');
assert.match(motionRangesPatch, /std::vector<FocusTypingReveal>/);
assert.match(motionRangesPatch, /focus_typing_repaint_timer_/);
assert.match(motionRangesPatch, /kFocusTypingInitialTranslationY/);
assert.match(motionRangesPatch, /kFocusDeletionInitialTranslationInline/);
assert.match(motionRangesPatch, /FocusTypingMotionKind::kDeletionSettle/);
assert.match(
    motionRangesPatch,
    /transform\.Translate\(paint\.translation_x, paint\.translation_y\)/);
assert.doesNotMatch(motionRangesPatch, /^\+.*(?:blur\(|scale\()/m);

const locationBarView = read(
    activeRoot,
    'chrome/browser/ui/views/location_bar/location_bar_view.cc');
assert.match(locationBarView, /prefs::kFocusMotionEnabled/);
assert.match(locationBarView, /gfx::Animation::ShouldRenderRichAnimation\(\)/);
assert.match(locationBarView, /gfx::Animation::PrefersReducedMotion\(\)/);
assert.match(locationBarView, /PreferredContrast::kMore/);
assert.doesNotMatch(locationBarView, /typing_animation_/);
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
    /OnFocusMotionPreferenceChanged\(\)[\s\S]*CancelFocusTypingReveals\(\)[\s\S]*hover_animation_\.Reset/);

const locationBarHeader = read(
    activeRoot,
    'chrome/browser/ui/views/location_bar/location_bar_view.h');
assert.doesNotMatch(locationBarHeader, /typing_animation_/);
assert.match(locationBarHeader, /bool ShouldAnimateFocusMotion\(\) const/);

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
    /if \(committed_text_differs && !is_ime_composing &&\s*location_bar_view_\)/);
assert.doesNotMatch(
    omniboxView,
    /something_changed && committed_text_differs/,
    'ordinary physical typing must not be gated on the model UI return value');
assert.match(
    omniboxView,
    /kFocusTypingRevealDuration\s*=\s*\n?\s*base::Milliseconds\(180\)/);
assert.match(omniboxView, /UpdateFocusTypingRevealsForEdit\(/);
assert.match(omniboxView, /AddFocusTypingReveal\(gfx::Range\(prefix, new_suffix\)\)/);
assert.match(omniboxView, /ExpandRangeToGraphemeBoundary\(inserted_range\)/);
assert.match(omniboxView, /focus_typing_reveals_/);
assert.match(omniboxView, /reveal\.started_at/);
assert.match(omniboxView, /suffix_shift/);
assert.match(omniboxView, /focus_typing_repaint_timer_\.Start\(/);
assert.match(omniboxView, /kFocusTypingInitialOpacity = 0\.12/);
assert.match(omniboxView, /kFocusTypingInitialTranslationY = 3\.0f/);
assert.match(omniboxView, /CubicBezier curve\(0\.22, 1\.0, 0\.36, 1\.0\)/);
assert.match(
    omniboxView,
    /SK_AlphaTRANSPARENT[\s\S]*Textfield::OnPaint\(canvas\);[\s\S]*EmphasizeURLComponents\(\)[\s\S]*GetSubstringBounds\(paint\.range\)[\s\S]*ClipRect\(glyph_bounds\)[\s\S]*transform\.Translate\(paint\.translation_x, paint\.translation_y\)/);
assert.match(omniboxView, /FocusTypingMotionKind::kDeletionSettle/);
assert.doesNotMatch(omniboxView, /blur\(|\.Scale\(|typing_animation_/);

const omniboxViewHeader = read(
    activeRoot,
    'chrome/browser/ui/views/omnibox/omnibox_view_views.h');
assert.match(omniboxViewHeader, /focus_typing_text_before_change_/);
assert.match(omniboxViewHeader, /focus_typing_text_before_composition_/);
assert.match(omniboxViewHeader, /has_focus_typing_composition_baseline_/);
assert.match(omniboxViewHeader, /struct FocusTypingReveal/);
assert.match(omniboxViewHeader, /std::vector<FocusTypingReveal> focus_typing_reveals_/);
assert.match(omniboxViewHeader, /base::RepeatingTimer focus_typing_repaint_timer_/);

console.log('Focus motion contract verified.');
