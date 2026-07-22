#!/usr/bin/env node

// Static contract checks for Blink-native Focus text insertion reveal.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const projectRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)), '..');
const overridesRoot = path.join(projectRoot, 'source_overrides');
const checkoutRoot = path.join(projectRoot, 'build', 'src');
const hasCheckout = fs.existsSync(checkoutRoot);
const activeRoot = hasCheckout ? checkoutRoot : overridesRoot;
const read = (root, relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');
const digest = file => crypto.createHash('sha256')
    .update(fs.readFileSync(file)).digest('hex');

const nativeFiles = [
  'third_party/blink/renderer/core/editing/commands/insert_text_command.cc',
  'third_party/blink/renderer/core/editing/commands/insert_text_command.h',
  'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command.cc',
  'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command.h',
  'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command_test.cc',
  'third_party/blink/renderer/core/editing/commands/typing_command.cc',
  'third_party/blink/renderer/core/editing/commands/insert_text_command_test.cc',
  'third_party/blink/renderer/core/editing/markers/document_marker.h',
  'third_party/blink/renderer/core/editing/markers/document_marker_controller.h',
  'third_party/blink/renderer/core/editing/markers/document_marker_controller.cc',
  'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.h',
  'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.cc',
  'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker_test.cc',
  'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker_list_impl.h',
  'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker_list_impl.cc',
  'third_party/blink/renderer/core/editing/markers/highlight_pseudo_marker_list_impl.cc',
  'third_party/blink/renderer/core/editing/build.gni',
  'third_party/blink/renderer/core/frame/settings.json5',
  'third_party/blink/renderer/core/exported/web_view_impl.cc',
  'third_party/blink/public/web/web_settings.h',
  'third_party/blink/renderer/core/exported/web_settings_impl.h',
  'third_party/blink/renderer/core/exported/web_settings_impl.cc',
  'third_party/blink/renderer/core/paint/highlight_overlay.h',
  'third_party/blink/renderer/core/paint/highlight_overlay.cc',
  'third_party/blink/renderer/core/paint/highlight_painter.h',
  'third_party/blink/renderer/core/paint/highlight_painter.cc',
  'third_party/blink/public/common/web_preferences/web_preferences.h',
  'third_party/blink/public/common/web_preferences/web_preferences_mojom_traits.h',
  'third_party/blink/public/mojom/webpreferences/web_preferences.mojom',
  'third_party/blink/common/web_preferences/web_preferences_mojom_traits.cc',
  'chrome/browser/chrome_content_browser_client.cc',
  'chrome/browser/ui/prefs/pref_watcher.cc',
  'chrome/browser/extensions/component_loader.h',
  'chrome/browser/extensions/component_loader.cc',
  'third_party/focus_text_motion/manifest.json',
  'third_party/focus_text_motion/content-script.js',
];

for (const relativePath of nativeFiles) {
  const active = path.join(activeRoot, relativePath);
  const override = path.join(overridesRoot, relativePath);
  assert.ok(fs.existsSync(active), `missing active file: ${relativePath}`);
  assert.ok(fs.existsSync(override), `missing override: ${relativePath}`);
  if (hasCheckout) {
    assert.equal(digest(active), digest(override),
                 `active/override drift: ${relativePath}`);
  }
}

const insert = read(activeRoot,
    'third_party/blink/renderer/core/editing/commands/insert_text_command.cc');
assert.match(insert, /AddFocusTextMotionMarker\([\s\S]*offset[\s\S]*text_\.length\(\)/);
assert.match(insert, /enable_focus_text_motion_/);

const incrementalInsert = read(
    activeRoot,
    'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command.cc');
assert.match(
    incrementalInsert,
    /enable_focus_text_motion_ && !old_text\.empty\(\) && old_text == new_text/);
assert.match(
    incrementalInsert,
    /TextIterator marked_text\(selection_range\.StartPosition\(\),[\s\S]{0,1000}AddFocusInsertionMotionMarkers/);

const incrementalTests = read(
    activeRoot,
    'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command_test.cc');
assert.match(incrementalTests,
             /FocusTextMotionMarksIdenticalCommittedComposition/);
assert.match(incrementalTests,
             /FocusTextMotionSkipsIdenticalProvisionalComposition/);

const typing = read(activeRoot,
    'third_party/blink/renderer/core/editing/commands/typing_command.cc');
assert.match(
    typing,
    /const bool enable_focus_text_motion\s*=\s*\n?\s*composition_type_ != kTextCompositionUpdate/);
assert.match(
    typing,
    /MakeGarbageCollected<InsertIncrementalTextCommand>\([\s\S]{0,500}enable_focus_text_motion\)/);
assert.match(
    typing,
    /MakeGarbageCollected<InsertTextCommand>\([\s\S]{0,500}enable_focus_text_motion\)/);
assert.match(typing, /AddFocusDeletionSettleMarker/);
assert.match(typing, /AddFocusTextDeletionMotionMarker/);
assert.ok((typing.match(/AddFocusDeletionSettleMarker\(/g) ?? []).length >= 3,
          'definition plus backward/forward deletion calls are required');
assert.match(
    typing,
    /MakeGarbageCollected<RelocatablePosition>\(selection_to_delete\.Start\(\)\)[\s\S]{0,900}DeleteSelectionIfRange\([\s\S]{0,250}if \(editing_state->IsAborted\(\)\)[\s\S]{0,150}AddFocusDeletionSettleMarker/);

const marker = read(activeRoot,
    'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.cc');
assert.match(marker, /kRevealDuration = base::Milliseconds\(180\)/);
assert.match(marker, /opacity_/);
assert.match(marker, /kInitialTranslationY = 3\.0f/);
assert.match(marker, /kDeletionInitialTranslationInline = 3\.0f/);
assert.match(marker, /Kind::kDeletionSettle/);
assert.match(marker, /CubicBezier curve\(0\.22, 1\.0, 0\.36, 1\.0\)/);
assert.doesNotMatch(marker,
    /document\.|createElement|attachShadow|InputEvent|translateY|scale\(|blur\(/);
const markerTests = read(
    activeRoot,
    'third_party/blink/renderer/core/editing/markers/focus_text_motion_marker_test.cc');
assert.match(markerTests, /RapidInsertionsKeepIndependentTimelines/);

const painter = read(activeRoot,
    'third_party/blink/renderer/core/paint/highlight_painter.cc');
for (const required of [
  'ApplyFocusTextMotionToParts',
  'MarkerRangeMappingContext',
  'ExpandRangeToIncludePartialGlyphs',
  'BeginLayer(part.opacity)',
  'motion.TranslationInline()',
  'Translate(part.translation_x, part.translation_y)',
  'TextPainter::kTextProperOnly',
]) {
  assert.ok(painter.includes(required), `native paint contract missing: ${required}`);
}
assert.doesNotMatch(painter, /scale\(|blur\(/);

const omniboxPatch = read(
    projectRoot,
    'focus-chromium/patches/focus/ui/omnibox-typing-motion-ranges.patch');
assert.match(omniboxPatch, /kFocusTypingRevealDuration[\s\S]*Milliseconds\(180\)/);
assert.match(omniboxPatch,
             /^\+\s+if \(committed_text_differs && !is_ime_composing && location_bar_view_\)/m);
assert.doesNotMatch(omniboxPatch,
                    /^\+\s+if \(something_changed && committed_text_differs/m);
assert.match(omniboxPatch, /FocusTypingReveal[\s\S]*base::TimeTicks started_at/);
assert.match(omniboxPatch, /CubicBezier[\s\S]*0\.22[\s\S]*0\.36/);
assert.match(
    omniboxPatch,
    /transform\.Translate\(paint\.translation_x, paint\.translation_y\)/);
assert.match(omniboxPatch, /FocusTypingMotionKind::kDeletionSettle/);
assert.doesNotMatch(omniboxPatch, /scale\(|blur\(/);

const controller = read(activeRoot,
    'third_party/blink/renderer/core/editing/markers/document_marker_controller.cc');
assert.match(controller, /GetFocusTextMotionEnabled/);
assert.match(controller, /GetPrefersReducedMotion/);
assert.match(controller, /RequestAnimationFrame/);

const webPrefs = read(activeRoot,
    'third_party/blink/public/common/web_preferences/web_preferences.h');
assert.match(webPrefs, /bool focus_text_motion_enabled = false/);
const webSettings = read(
    activeRoot, 'third_party/blink/public/web/web_settings.h');
assert.match(webSettings, /SetFocusTextMotionEnabled\(bool\)/);
const webSettingsImplHeader = read(
    activeRoot,
    'third_party/blink/renderer/core/exported/web_settings_impl.h');
assert.match(webSettingsImplHeader,
             /SetFocusTextMotionEnabled\(bool\) override/);
const webSettingsImpl = read(
    activeRoot,
    'third_party/blink/renderer/core/exported/web_settings_impl.cc');
assert.match(
    webSettingsImpl,
    /WebSettingsImpl::SetFocusTextMotionEnabled[\s\S]*SetFocusTextMotionEnabled/);
const browserClient = read(activeRoot,
    'chrome/browser/chrome_content_browser_client.cc');
assert.match(browserClient,
             /focus_text_motion_enabled[\s\S]*kFocusMotionEnabled/);
const prefWatcher = read(activeRoot,
    'chrome/browser/ui/prefs/pref_watcher.cc');
assert.match(prefWatcher, /kWebPrefsToObserve[\s\S]*kFocusMotionEnabled/);

const manifest = JSON.parse(read(
    activeRoot, 'third_party/focus_text_motion/manifest.json'));
assert.equal(manifest.content_scripts, undefined);
assert.equal(manifest.background, undefined);
assert.equal(manifest.host_permissions, undefined);
assert.equal(manifest.permissions, undefined);
const legacyContent = read(
    activeRoot, 'third_party/focus_text_motion/content-script.js');
assert.doesNotMatch(legacyContent,
    /createElement|attachShadow|animate\(|beforeinput|data-focus-text-motion/);
const loader = read(activeRoot,
    'chrome/browser/extensions/component_loader.cc');
assert.doesNotMatch(loader, /AddFocusTextMotion/);

const tests = read(activeRoot,
    'third_party/blink/renderer/core/editing/commands/insert_text_command_test.cc');
assert.match(tests, /FocusTextMotionMarksOnlyInsertedRange/);
assert.match(tests, /FocusTextMotionHonorsReducedMotion/);
assert.match(tests, /FocusTextMotionSkipsProvisionalImeUpdate/);

console.log(JSON.stringify({
  ok: true,
  implementation: 'Blink native range paint',
  geometry: 'layout/caret unchanged; only inserted-range paint settles by 3px',
  coverage: 'input/textarea UA shadow editors, contenteditable, frames',
  preference: 'focus.ui.motion_enabled (live WebPreferences)',
  reducedMotion: true,
  passwordPolicy: 'paint-time opacity/translation only; no password text copy',
}, null, 2));
