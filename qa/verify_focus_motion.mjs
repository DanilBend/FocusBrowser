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
assert.match(toolbarView, /GetBoolean\([\s\S]*prefs::kFocusMotionEnabled/);
assert.match(toolbarView, /popup_resource\.append\("\?focusMotion=0"\)/);

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
             /IDS_SETTINGS_FOCUS_MOTION[^]*Плавные анимации интерфейса/);
assert.match(settingsStrings,
             /IDS_SETTINGS_FOCUS_MOTION_DESCRIPTION[^]*Спокойные плавные переходы/);
assert.doesNotMatch(settingsStrings,
                    /Smooth interface animations|Use calm, fluid transitions/);

for (const ntpRoot of [
  path.join(activeRoot, 'chrome', 'browser', 'resources', 'new_tab_page'),
  path.join(activeRoot, 'chrome', 'browser', 'resources', 'new_tab_page_third_party'),
]) {
  if (!fs.existsSync(ntpRoot)) continue;
  const pending = [ntpRoot];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, {withFileTypes: true})) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(absolute);
      } else if (/\.(css|html|js|ts)$/.test(entry.name)) {
        const contents = fs.readFileSync(absolute, 'utf8');
        assert.doesNotMatch(
            contents,
            /focus\.ui\.motion_enabled|focusMotionEnabled|focus-water-drift/,
            `motion feature leaked into NTP: ${absolute}`);
      }
    }
  }
}

console.log('Focus motion contract verified.');
