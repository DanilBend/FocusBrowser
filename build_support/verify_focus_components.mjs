// Copyright 2026 The Focus Browser Authors
// Static integrity checks for the browser-owned Focus components.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = process.env.FOCUS_ACTIVE_SOURCE_ROOT ?
  path.resolve(process.env.FOCUS_ACTIVE_SOURCE_ROOT) :
  path.join(repoRoot, 'build', 'src');
const overridesRoot = path.join(repoRoot, 'source_overrides');

function walk(root, predicate = () => true) {
  const result = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const absolute = path.join(root, entry.name);
    if (entry.isDirectory()) {
      result.push(...walk(absolute, predicate));
    } else if (predicate(absolute)) {
      result.push(absolute);
    }
  }
  return result;
}

function read(relativePath) {
  return fs.readFileSync(path.join(sourceRoot, relativePath), 'utf8');
}

function readMirroredFile(file) {
  const bytes = fs.readFileSync(file);
  if (!/\.(?:cc|css|gn|gni|grd|grdp|h|html|icon|js|json|mojom|py|svelte|svg|ts|txt|chromium)$/i.test(file)) {
    return bytes;
  }
  return Buffer.from(bytes.toString('utf8').replace(/\r\n?/g, '\n'), 'utf8');
}

function parseJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    throw new Error(`Invalid JSON: ${file}\n${error.message}`);
  }
}

function extensionIdFromKey(base64Key) {
  const digest = crypto
      .createHash('sha256')
      .update(Buffer.from(base64Key, 'base64'))
      .digest()
      .subarray(0, 16);
  return [...digest]
      .flatMap(byte => [byte >> 4, byte & 0x0f])
      .map(nibble => String.fromCharCode('a'.charCodeAt(0) + nibble))
      .join('');
}

function manifestResources(manifest) {
  const resources = new Set(['manifest.json']);
  const add = value => {
    if (typeof value === 'string') resources.add(value.replace(/^\//, ''));
  };

  add(manifest.action?.default_popup);
  if (typeof manifest.action?.default_icon === 'string') {
    add(manifest.action.default_icon);
  } else {
    Object.values(manifest.action?.default_icon || {}).forEach(add);
  }
  Object.values(manifest.icons || {}).forEach(add);
  add(manifest.background?.service_worker);
  add(manifest.background?.page);
  add(manifest.options_page);
  add(manifest.options_ui?.page);
  for (const contentScript of manifest.content_scripts || []) {
    (contentScript.css || []).forEach(add);
    (contentScript.js || []).forEach(add);
  }
  for (const entry of manifest.web_accessible_resources || []) {
    if (typeof entry === 'string') {
      add(entry);
    } else {
      (entry.resources || []).forEach(add);
    }
  }
  return resources;
}

// Every checked-in JSON file must remain parseable after branding edits.
const jsonFiles = [
  ...walk(path.join(sourceRoot, 'third_party', 'focus_youtube'),
          file => file.endsWith('.json')),
  ...walk(path.join(sourceRoot, 'third_party', 'focus_text_motion'),
          file => file.endsWith('.json')),
  ...walk(path.join(sourceRoot, 'third_party', 'ublock'),
          file => file.endsWith('.json')),
];
for (const file of jsonFiles) parseJson(file);

const focusYoutubeRoot = path.join(sourceRoot, 'third_party', 'focus_youtube');
const focusYoutubeManifest = parseJson(path.join(focusYoutubeRoot, 'manifest.json'));
assert.equal(extensionIdFromKey(focusYoutubeManifest.key),
             'jafokmemnknjknbdiklabcnhlpheefbm');
assert.equal(focusYoutubeManifest.version, '1.6.9.1');
assert.match(focusYoutubeManifest.description, /[А-Яа-яЁё]/);
assert.equal(focusYoutubeManifest.author, 'Focus Browser');
assert.match(focusYoutubeManifest.action.default_title,
             /режим без отвлечений/);
assert.equal(focusYoutubeManifest.options_page, undefined);
assert.equal(focusYoutubeManifest.options_ui, undefined);
assert.deepEqual(focusYoutubeManifest.permissions, ['storage', 'alarms']);
assert.deepEqual(focusYoutubeManifest.host_permissions, [
  'https://youtube.com/*',
  'https://*.youtube.com/*',
]);
assert.deepEqual(focusYoutubeManifest.content_scripts[0].matches,
                 focusYoutubeManifest.host_permissions);

const fileListScript = fs.readFileSync(
    path.join(focusYoutubeRoot, 'generate_file_list.py'), 'utf8');
const packagedFocusYoutubePaths = new Set([
  ...fileListScript.matchAll(/^\s*"([^"\r\n]+)",?\s*$/gm),
].map(match => match[1]));
assert.equal(packagedFocusYoutubePaths.size, 17);
for (const optionsResource of [
  'options/main.html',
  'options/main.js',
  'options/options.css',
]) {
  assert.equal(packagedFocusYoutubePaths.has(optionsResource), false,
               `FocusYoutube options page is packaged: ${optionsResource}`);
}
for (const resource of manifestResources(focusYoutubeManifest)) {
  assert.ok(fs.existsSync(path.join(focusYoutubeRoot, resource)),
            `FocusYoutube manifest resource is missing: ${resource}`);
  assert.ok(packagedFocusYoutubePaths.has(resource),
            `FocusYoutube resource is not packaged: ${resource}`);
}
for (const resource of packagedFocusYoutubePaths) {
  assert.ok(fs.existsSync(path.join(focusYoutubeRoot, resource)),
            `FocusYoutube packaged resource is missing: ${resource}`);
}
for (const forbiddenSurface of [
  'premium/',
  'paywall/',
  'feedback/',
  'donors/',
  'shared/auth.js',
  'shared/license.js',
  'shared/analytics.js',
]) {
  assert.ok(
      [...packagedFocusYoutubePaths]
          .every(resource => !resource.startsWith(forbiddenSurface)),
      `Remote/account surface must not be packaged: ${forbiddenSurface}`);
}

const focusYoutubePopup = fs.readFileSync(
    path.join(focusYoutubeRoot, 'popup.html'), 'utf8');
assert.match(focusYoutubePopup, /lang="ru"/);
assert.match(focusYoutubePopup, /Спокойный YouTube без лишнего/);
assert.match(focusYoutubePopup, /id="global_enable"[^>]*role="switch"/);
assert.match(focusYoutubePopup,
             /<button id="resetAll"[^>]*type="button"[^>]*disabled/);
assert.match(focusYoutubePopup, /id="masterState" aria-live="polite"/);
assert.match(focusYoutubePopup, /id="enabledCount" aria-live="polite"/);
assert.match(focusYoutubePopup, /Включено 0 из 24/);
assert.match(focusYoutubePopup, /Настройки применяются сразу на YouTube/);
assert.doesNotMatch(focusYoutubePopup, /id="openOptions"|Все 93 настройки/);
assert.doesNotMatch(focusYoutubePopup, /<(?:a|form)\b/i);
assert.doesNotMatch(
    focusYoutubePopup,
    />(?:Hide|Disable|Redirect|Turn on|Turn off|FocusYoutube is off)[^<]*</);
assert.doesNotMatch(focusYoutubePopup, /Unhook|extension|расширени[ея]/i);

const schemaContext = {};
schemaContext.globalThis = schemaContext;
vm.createContext(schemaContext);
vm.runInContext(
    fs.readFileSync(path.join(focusYoutubeRoot, 'shared', 'main.js'), 'utf8'),
    schemaContext,
    { filename: 'shared/main.js' });
const focusYoutubeSchema = schemaContext.FocusYoutubeSettings;
assert.equal(focusYoutubeSchema.behaviorIds.length, 93);
assert.equal(new Set(focusYoutubeSchema.behaviorIds).size, 93);
assert.equal(focusYoutubeSchema.defaults.focus_youtube_schema_version, 4);
assert.equal(focusYoutubeSchema.nativeBehaviorIds.length, 29);
assert.equal(new Set(focusYoutubeSchema.nativeBehaviorIds).size, 29);
assert.ok(Object.values(focusYoutubeSchema.behaviorDefaults)
    .every(value => value === false));
assert.equal(focusYoutubeSchema.defaults.global_enable, true);
assert.equal(focusYoutubeSchema.defaults.dark_mode, true);
assert.ok(focusYoutubeSchema.behaviorIds.every(id => !id.startsWith('hide_')));

const focusYoutubePopupTheme = fs.readFileSync(
    path.join(focusYoutubeRoot, 'css', 'popup.css'), 'utf8');
assert.match(focusYoutubePopupTheme, /width: 400px/);
assert.match(focusYoutubePopupTheme, /height: 600px/);
assert.match(focusYoutubePopupTheme, /input:focus-visible/);
assert.match(focusYoutubePopupTheme, /outline: 2px solid var\(--text\)/);
assert.match(focusYoutubePopupTheme, /prefers-reduced-motion: reduce/);
assert.match(focusYoutubePopupTheme, /forced-colors: active/);
const popupHexColors = [
  ...focusYoutubePopupTheme.matchAll(/#([0-9a-f]{6})\b/gi),
].map(match => match[1]);
assert.ok(popupHexColors.length > 0);
for (const color of popupHexColors) {
  assert.equal(color.slice(0, 2).toLowerCase(),
               color.slice(2, 4).toLowerCase(),
               `FocusYoutube popup color #${color} is not monochrome`);
  assert.equal(color.slice(2, 4).toLowerCase(),
               color.slice(4, 6).toLowerCase(),
               `FocusYoutube popup color #${color} is not monochrome`);
}

const focusYoutubeImplementation = [
  fs.readFileSync(
      path.join(focusYoutubeRoot, 'background', 'events.js'), 'utf8'),
  fs.readFileSync(
      path.join(focusYoutubeRoot, 'content-script', 'main.css'), 'utf8'),
  fs.readFileSync(
      path.join(focusYoutubeRoot, 'content-script', 'main.js'), 'utf8'),
  fs.readFileSync(path.join(focusYoutubeRoot, 'options', 'main.js'), 'utf8'),
].join('\n');
for (const control of focusYoutubeSchema.behaviorIds) {
  assert.ok(focusYoutubeImplementation.includes(control),
            `FocusYoutube control has no implementation: ${control}`);
}
assert.match(focusYoutubeImplementation, /MutationObserver/);
assert.doesNotMatch(focusYoutubeImplementation,
                    /\bfetch\s*\(|XMLHttpRequest|mixpanel|stripe|paypal/i);

const focusYoutubePopupScript = fs.readFileSync(
    path.join(focusYoutubeRoot, 'js', 'popup.js'), 'utf8');
assert.match(focusYoutubePopupScript, /resetAll/);
assert.match(focusYoutubePopupScript, /visibleIds\.length !== 24/);
assert.doesNotMatch(
    focusYoutubePopupScript,
    /openOptionsPage|window\.open|chrome\.tabs|location\.(?:href|assign|replace)/);

const focusTextMotionRoot = path.join(
    sourceRoot, 'third_party', 'focus_text_motion');
const focusTextMotionManifest = parseJson(
    path.join(focusTextMotionRoot, 'manifest.json'));
assert.equal(extensionIdFromKey(focusTextMotionManifest.key),
             'ajekofejbbjbbkdfnlghakcilbfdmofc');
assert.equal(focusTextMotionManifest.manifest_version, 3);
assert.equal(focusTextMotionManifest.name, 'Focus Text Motion');
assert.equal(focusTextMotionManifest.short_name, 'Focus Motion');
assert.equal(focusTextMotionManifest.version, '1.0.0');
assert.equal(focusTextMotionManifest.incognito, 'split');
assert.equal(focusTextMotionManifest.background, undefined);
assert.equal(focusTextMotionManifest.permissions, undefined);
assert.equal(focusTextMotionManifest.host_permissions, undefined);
assert.equal(focusTextMotionManifest.content_scripts, undefined);
assert.equal(focusTextMotionManifest.action, undefined);
assert.equal(focusTextMotionManifest.options, undefined);
assert.equal(focusTextMotionManifest.options_page, undefined);
assert.equal(focusTextMotionManifest.options_ui, undefined);
assert.deepEqual([...manifestResources(focusTextMotionManifest)],
                 ['manifest.json']);

const focusTextMotionContent = fs.readFileSync(
    path.join(focusTextMotionRoot, 'content-script.js'), 'utf8');
assert.match(focusTextMotionContent, /Intentionally empty/);
assert.match(focusTextMotionContent, /implemented in Blink's native/);
assert.match(focusTextMotionContent, /never installs a DOM overlay/);
const focusTextMotionExecutableContent = focusTextMotionContent
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .trim();
assert.equal(focusTextMotionExecutableContent, '');

function readFocusTextMotionNative(relativePath) {
  const override = fs.readFileSync(
      path.join(overridesRoot, relativePath), 'utf8');
  assert.equal(read(relativePath), override,
               `Native text-motion override differs: ${relativePath}`);
  return override;
}

const focusTextMotionBlinkMarker = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'markers',
    'focus_text_motion_marker.cc'));
assert.match(
    focusTextMotionBlinkMarker,
    /UpdateOpacity\(base::TimeTicks\)\s*\{[\s\S]*?return true;\s*\}/);
assert.doesNotMatch(
    focusTextMotionBlinkMarker,
    /kRevealDuration|kInitialOpacity|kInitialTranslationY|kDeletionInitialTranslationInline|CubicBezier|curve\.Solve|opacity_|translation_inline_|translation_y_/);

const focusTextMotionBlinkMarkerHeader = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'markers',
    'focus_text_motion_marker.h'));
assert.match(focusTextMotionBlinkMarkerHeader,
             /float Opacity\(\) const \{ return 1\.0f; \}/);
assert.match(focusTextMotionBlinkMarkerHeader,
             /float TranslationInline\(\) const \{ return 0\.0f; \}/);
assert.match(focusTextMotionBlinkMarkerHeader,
             /float TranslationY\(\) const \{ return 0\.0f; \}/);
assert.doesNotMatch(
    focusTextMotionBlinkMarkerHeader,
    /animation_start_|start_delay_|kind_|float opacity_|float translation_inline_|float translation_y_/);

const focusTextMotionHighlightPart = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'paint',
    'highlight_overlay.h'));
assert.match(
    focusTextMotionHighlightPart,
    /struct CORE_EXPORT HighlightPart[\s\S]*float opacity = 1\.0f;[\s\S]*float translation_y = 0\.0f;[\s\S]*float translation_x = 0\.0f;/);

const focusTextMotionHighlightPainter = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'paint',
    'highlight_painter.cc'));
assert.match(focusTextMotionHighlightPainter, /motion\.TranslationY\(\)/);
assert.match(focusTextMotionHighlightPainter, /motion\.TranslationInline\(\)/);
assert.match(focusTextMotionHighlightPainter, /HighlightPart split = part/);
for (const crispPaintContract of [
  'float opacity = 1.0f;',
  'float translation_inline = 0.0f;',
  'float translation_y = 0.0f;',
  'split.opacity = opacity;',
  'TextPainter::kTextProperOnly',
]) {
  assert.ok(focusTextMotionHighlightPainter.includes(crispPaintContract),
            `Missing crisp text-paint contract: ${crispPaintContract}`);
}
assert.doesNotMatch(
    focusTextMotionHighlightPainter,
    /BeginLayer\(part\.opacity\)|Translate\(part\.translation_x, part\.translation_y\)/);

const focusInsertText = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_text_command.cc'));
const focusInsertTextHeader = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_text_command.h'));
const focusInsertIncrementalText = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_incremental_text_command.cc'));
const focusInsertIncrementalTextHeader = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_incremental_text_command.h'));
const focusInsertIncrementalTextTests = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_incremental_text_command_test.cc'));
const focusTypingCommand = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'typing_command.cc'));
const focusInsertTextTests = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing', 'commands',
    'insert_text_command_test.cc'));
assert.match(focusInsertText, /enable_focus_text_motion_/);
assert.match(focusInsertTextHeader, /bool enable_focus_text_motion = true/);
assert.match(focusInsertIncrementalText, /enable_focus_text_motion/);
assert.match(focusInsertIncrementalTextHeader,
             /bool enable_focus_text_motion = true/);
assert.match(
    focusInsertIncrementalText,
    /enable_focus_text_motion_ && !old_text\.empty\(\) && old_text == new_text/);
assert.match(
    focusInsertIncrementalText,
    /TextIterator marked_text\(selection_range\.StartPosition\(\),[\s\S]{0,1000}AddFocusInsertionMotionMarkers/);
assert.match(focusInsertIncrementalTextTests,
             /FocusTextMotionMarksIdenticalCommittedComposition/);
assert.match(focusInsertIncrementalTextTests,
             /FocusTextMotionSkipsIdenticalProvisionalComposition/);
assert.match(
    focusTypingCommand,
    /const bool enable_focus_text_motion\s*=\s*\n?\s*composition_type_ != kTextCompositionUpdate/);
assert.match(focusTypingCommand, /AddFocusTextDeletionMotionMarker/);
assert.ok(
    (focusTypingCommand.match(/AddFocusDeletionSettleMarker\(/g) ?? []).length >=
        3,
    'definition plus backward/forward deletion calls are required');
assert.match(focusInsertTextTests, /FocusTextMotionSkipsProvisionalImeUpdate/);

const focusCaretMotionHeader = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing',
    'caret_display_item_client.h'));
const focusCaretMotion = readFocusTextMotionNative(path.join(
    'third_party', 'blink', 'renderer', 'core', 'editing',
    'caret_display_item_client.cc'));
assert.match(focusCaretMotionHeader, /animated_local_rect_/);
assert.match(focusCaretMotion,
             /kFocusCaretMotionDuration = base::Milliseconds\(110\)/);
assert.match(focusCaretMotion,
             /gfx::CubicBezier curve\(0\.0, 0\.8, 0\.2, 1\.0\)/);
assert.match(focusCaretMotion, /GetFocusTextMotionEnabled\(\)/);
assert.match(focusCaretMotion, /FocusCaretMotionPrefersReducedMotion/);
assert.match(focusCaretMotion,
             /IsInPasswordField\(caret_position\.GetPosition\(\)\)/);
assert.doesNotMatch(focusCaretMotion, /const bool box_fragment_changed/);
assert.match(
    focusCaretMotion,
    /local_rect_\s*=\s*new_local_rect;[\s\S]{0,500}StartFocusCaretMotion\(visual_start, new_local_rect\)/);
assert.match(
    focusCaretMotion,
    /RecordSelection\([\s\S]{0,300}PhysicalRect drawing_rect = local_rect_/);

const focusOmniboxMotionPatch = fs.readFileSync(path.join(
    repoRoot, 'focus-chromium', 'patches', 'focus', 'ui',
    'omnibox-typing-motion-ranges.patch'), 'utf8');
function assertFocusOmniboxMotion(source) {
  assert.match(
      source,
      /bool OmniboxViewViews::ShouldAnimateCaretMotion\(\) const\s*\{[\s\S]*?location_bar_view_ && !popup_window_mode_[\s\S]*?location_bar_view_->ShouldAnimateFocusMotion\(\);[\s\S]*?\}/);
  assert.doesNotMatch(
      source,
      /FocusTypingReveal|FocusTypingPaint|focus_typing_|RepeatingTimer|SaveLayerAlpha|SK_AlphaTRANSPARENT|transform\.Translate|kFocusTyping|base::Milliseconds\(180\)|CubicBezier/);
}
assertFocusOmniboxMotion(focusOmniboxMotionPatch);

const focusOmniboxMotionSourcePath = path.join(
    sourceRoot, 'chrome', 'browser', 'ui', 'views', 'omnibox',
    'omnibox_view_views.cc');
if (fs.existsSync(focusOmniboxMotionSourcePath)) {
  const focusOmniboxMotionSource = fs.readFileSync(
      focusOmniboxMotionSourcePath, 'utf8');
  assertFocusOmniboxMotion(focusOmniboxMotionSource);
  assert.match(focusOmniboxMotionSource, /Textfield::OnPaint\(canvas\)/);
}

const focusBlockRoot = path.join(sourceRoot, 'third_party', 'ublock');
const focusBlockManifest = parseJson(path.join(focusBlockRoot, 'manifest.json'));
assert.equal(extensionIdFromKey(focusBlockManifest.key),
             'blockjmkbacgjkknlgpkjjiijinjdanf');
assert.equal(focusBlockManifest.version, '1.72.2.2');
assert.equal(focusBlockManifest.name, 'FocusBlock');

const russianMessages = parseJson(
    path.join(focusBlockRoot, '_locales', 'ru', 'messages.json'));
assert.equal(russianMessages.extName.message, 'FocusBlock');
assert.match(russianMessages.extShortDesc.message, /[А-Яа-яЁё]/);
assert.match(russianMessages.popupPowerSwitchInfo.message, /отключить\/включить/);
assert.equal(russianMessages.focusBlockTagline.message,
             'Встроенная защита рекламы');
assert.equal(russianMessages.focusBlockGlobalOn.message, 'Защита включена');
assert.equal(russianMessages.focusBlockGlobalOff.message, 'Защита выключена');

const focusBlockPopup = fs.readFileSync(
    path.join(focusBlockRoot, 'popup-fenix.html'), 'utf8');
assert.match(focusBlockPopup, /<html[^>]*lang="ru"/);
assert.match(focusBlockPopup, /id="focusBrand"/);
assert.match(focusBlockPopup, /id="focusGlobalSwitch"/);
assert.match(focusBlockPopup,
             /id="focusGlobalSwitch"[^>]*role="switch"[^>]*aria-pressed="true"/);
assert.match(focusBlockPopup,
             /id="switch"[^>]*role="switch"[^>]*aria-pressed="true"/);
assert.match(focusBlockPopup, /class="focusMark"/);
assert.match(focusBlockPopup, /class="focusTargetOuter"/);
assert.match(focusBlockPopup, /class="focusTargetInner"/);
assert.match(focusBlockPopup, /class="focusTargetTicks"/);
assert.match(focusBlockPopup, /class="focusTargetDot"/);
assert.doesNotMatch(focusBlockPopup, /M16\.5 10c5\.2-2\.4/);
assert.doesNotMatch(focusBlockPopup,
                    /<rect[^>]*(?:fill="#?0{3,6}"|class="(?:tile|background)")/i);
assert.match(focusBlockPopup, /id="focusProtection"/);
assert.match(focusBlockPopup, /id="focusSiteOptions"/);
assert.match(focusBlockPopup, /data-focus-stat="page"/);
assert.match(focusBlockPopup, /data-focus-stat="total"/);
assert.match(focusBlockPopup, /На этой странице/);
assert.match(focusBlockPopup, /Всего заблокировано/);
assert.match(focusBlockPopup, /Убрать элемент/);
assert.match(focusBlockPopup, /href="dashboard\.html"/);
assert.match(focusBlockPopup, /Не скрывать элементы рекламы/);
assert.doesNotMatch(focusBlockPopup, /id="power-off-path"/);
assert.doesNotMatch(focusBlockPopup, /href="https?:\/\//i);
for (const controlId of [
  'switch',
  'no-popups',
  'no-large-media',
  'no-cosmetic-filtering',
  'no-remote-fonts',
  'no-scripting',
  'gotoPick',
]) {
  assert.match(focusBlockPopup, new RegExp(`id="${controlId}"[^>]*>`));
}
const focusBlockPopupTheme = fs.readFileSync(
    path.join(focusBlockRoot, 'css', 'popup-fenix.css'), 'utf8');
for (const token of [
  '--bg: #080808',
  '--surface: #121212',
  '--surface-raised: #1b1b1b',
  '--border: #2d2d2d',
  '--border-strong: #464646',
]) {
  assert.match(focusBlockPopupTheme, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}
assert.match(focusBlockPopupTheme, /font: 14px\/1\.4 "Segoe UI"/);
assert.match(focusBlockPopupTheme, /border-radius: 18px/);
assert.match(focusBlockPopupTheme, /\.focusMark \.focusTargetDot/);
assert.match(focusBlockPopupTheme, /max-height: 600px/);
assert.match(focusBlockPopupTheme, /width: 400px/);
assert.match(focusBlockPopupTheme, /height: 600px/);
assert.match(focusBlockPopupTheme, /:root\[data-motion="off"\]/);
assert.match(focusBlockPopupTheme, /@keyframes focusWater/);
assert.match(focusBlockPopupTheme,
             /\.masterCard::before[\s\S]*?animation: focusWater 10s/);
assert.match(focusBlockPopupTheme,
             /\.optionList \.tool[\s\S]*?transition: background-color 180ms ease/);
assert.match(focusBlockPopupTheme, /prefers-reduced-motion: reduce/);
assert.match(focusBlockPopupTheme, /forced-colors: active/);
const focusBlockTheme = fs.readFileSync(
    path.join(focusBlockRoot, 'css', 'themes', 'default.css'), 'utf8');
assert.match(focusBlockTheme, /Focus Browser monochrome product theme/);
const focusBlockBackground = read('third_party/ublock/js/background.js');
assert.match(focusBlockBackground, /uiTheme: 'dark'/);
assert.match(focusBlockBackground, /focusGlobalEnabled: true/);
const focusBlockStart = read('third_party/ublock/js/start.js');
assert.match(focusBlockStart, /'focusGlobalEnabled': true/);
assert.match(focusBlockStart,
             /focusGlobalEnabled = fetched\.focusGlobalEnabled !== false/);
const focusBlockCore = read('third_party/ublock/js/ublock.js');
assert.match(focusBlockCore,
             /focusGlobalEnabled === false \) \{ return false; \}/);
const focusBlockMessaging = read('third_party/ublock/js/messaging.js');
assert.match(focusBlockMessaging, /case 'toggleFocusGlobalFiltering'/);
assert.match(focusBlockMessaging,
             /vAPI\.storage\.set\(\{ focusGlobalEnabled: enabled \}\)/);
assert.match(focusBlockMessaging, /case 'toggleNetFiltering'/);
const focusBlockPopupScript = read('third_party/ublock/js/popup-fenix.js');
assert.match(focusBlockPopupScript, /toggleFocusGlobalSwitch/);
assert.match(focusBlockPopupScript, /toggleNetFilteringSwitch/);
assert.match(focusBlockPopupScript,
             /URLSearchParams\(self\.location\.search\)[\s\S]*?focusMotion[\s\S]*?dataset\.motion = 'off'/);
assert.match(focusBlockPopupScript, /\[data-focus-stat="page"\] \+ span/);
assert.match(focusBlockPopupScript, /\[data-focus-stat="total"\] \+ span/);

// Browser integration: FocusBlock is a native engine (the legacy component is
// not loaded), while FocusYoutube remains a non-removable component with its
// dedicated native control.
const componentLoader = read('chrome/browser/extensions/component_loader.cc');
const defaultComponentExtensionsBody = componentLoader.match(
    /void ComponentLoader::AddDefaultComponentExtensions\([\s\S]*?void ComponentLoader::AddDefaultComponentExtensionsForKioskMode/)?.[0] || '';
assert.doesNotMatch(defaultComponentExtensionsBody, /AddUBlock\(\)/,
                    'Legacy FocusBlock component extension must not load');
assert.match(defaultComponentExtensionsBody, /AddFocusYoutube\(\)/);

const focusBlockBuild = read('chrome/browser/focus_block/BUILD.gn');
assert.match(focusBlockBuild, /focus_block_url_loader_factory\.cc/);
assert.doesNotMatch(focusBlockBuild, /focus_block_url_loader_throttle/);
assert.ok(!fs.existsSync(path.join(
    sourceRoot,
    'chrome/browser/focus_block/focus_block_url_loader_throttle.cc')));
assert.ok(!fs.existsSync(path.join(
    sourceRoot,
    'chrome/browser/focus_block/focus_block_url_loader_throttle.h')));

const chromeContentBrowserClient = read(
    'chrome/browser/chrome_content_browser_client.cc');
assert.match(
    chromeContentBrowserClient,
    /focus_block::MaybeProxyURLLoaderFactory\([\s\S]*?MaybeProxyNetworkBoundRequest\(/,
    'FocusBlock must intercept before the final network-bound factory');
assert.doesNotMatch(
    chromeContentBrowserClient, /focus_block_url_loader_throttle/);

const focusBlockProxy = read(
    'chrome/browser/focus_block/focus_block_url_loader_factory.cc');
assert.match(focusBlockProxy, /class FocusBlockProxyingURLLoaderFactory/);
assert.match(focusBlockProxy, /class FocusBlockURLLoader/);
assert.match(
    focusBlockProxy,
    /OnReceiveRedirect\([\s\S]*?CheckRequest\([\s\S]*?OnReceiveRedirectDecision/,
    'Redirect targets must be checked before reaching the renderer');
assert.match(
    focusBlockProxy,
    /OnFollowRedirectDecision[\s\S]*?ERR_BLOCKED_BY_CLIENT/,
    'Caller-supplied redirect targets must also wait for Ghostery');
assert.match(focusBlockProxy, /net::ERR_BLOCKED_BY_CLIENT/);
assert.match(focusBlockProxy, /client_receiver_\.set_disconnect_handler/);
assert.match(focusBlockProxy, /GetOutermostMainFrame\(\)/);
assert.doesNotMatch(
    focusBlockProxy, /WebContents::GetLastCommittedURL|web_contents_/,
    'Network policy must use a stable factory snapshot, not the active tab');

const focusBlockService = read(
    'chrome/browser/focus_block/focus_block_service.cc');
assert.match(
    focusBlockService,
    /ShouldBlock\([\s\S]*?top_level_url[\s\S]*?source_url/);
assert.match(focusBlockService, /blocked_count_by_site_/);
assert.match(focusBlockService, /GetCosmeticResourcesForUrl/);
assert.match(focusBlockService, /EnsureGhosteryV8Initialized/);
assert.match(focusBlockService, /CreateSingleThreadTaskRunner/);
assert.match(focusBlockService, /FocusBlockGhosteryEngine::Match/);
assert.match(focusBlockService, /FocusBlockGhosteryEngine::GetCosmetics/);
assert.doesNotMatch(focusBlockService, /rust::|engine_from_filter_set/);
assert.match(focusBlockBuild, /third_party\/ghostery_adblocker:resources/);
assert.doesNotMatch(focusBlockBuild, /components\/focus_block:native_engine/);
const focusBlockGhosteryEngine = read(
    'chrome/browser/focus_block/focus_block_ghostery_engine.cc');
assert.match(focusBlockGhosteryEngine, /gin::IsolateHolder/);
assert.match(focusBlockGhosteryEngine, /matchRawDetails/);
assert.match(focusBlockGhosteryEngine, /cosmeticsRawDetails/);
assert.match(focusBlockGhosteryEngine, /TextEncoder/);
const ghosteryMetadata = JSON.parse(read(
    'third_party/ghostery_adblocker/UPSTREAM.json'));
assert.equal(ghosteryMetadata.version, '2.18.1');
assert.equal(
    ghosteryMetadata.commit,
    '67ef23276e93ebc5dd4621cc9df2b09ad9f490d7');
assert.match(read('chrome/chrome_paks.gni'),
             /third_party\/ghostery_adblocker\/resources\/ghostery_adblocker_resources\.pak/);
assert.match(read('chrome/browser/extensions/BUILD.gn'),
             /\/\/components\/focus_services/);
assert.match(read('extensions/browser/BUILD.gn'),
             /\/\/components\/focus_services/);
const allowlist = read(
    'chrome/browser/extensions/component_extensions_allowlist/allowlist.cc');
assert.match(allowlist, /kUBlockOriginComponentId/);
assert.match(allowlist, /kFocusYoutubeComponentId/);

const managementPolicy = read(
    'chrome/browser/extensions/standard_management_policy_provider.cc');
const modifiablePolicyBody = managementPolicy.match(
    /bool AdminPolicyIsModifiable[\s\S]*?\r?\n}\r?\n\r?\n}  \/\/ namespace/)?.[0] || '';
assert.match(
    modifiablePolicyBody,
    /Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]*?is_modifiable = false;/);
const remainEnabledBody = managementPolicy.match(
    /bool StandardManagementPolicyProvider::MustRemainEnabled[\s\S]*?\r?\n}/)?.[0] || '';
assert.match(remainEnabledBody, /!AdminPolicyIsModifiable/);
const remainInstalledBody = managementPolicy.match(
    /bool StandardManagementPolicyProvider::MustRemainInstalled[\s\S]*?\r?\n}/)?.[0] || '';
assert.match(remainInstalledBody,
             /Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]*?return true;/);

const extensionPrefs = read('extensions/browser/extension_prefs.cc');
const componentDisableReasonsBody = extensionPrefs.match(
    /void ExtensionPrefs::ClearInapplicableDisableReasonsForComponentExtension[\s\S]*?\r?\n}/)?.[0] || '';
assert.doesNotMatch(componentDisableReasonsBody, /kUBlockOriginComponentId/);
assert.doesNotMatch(componentDisableReasonsBody, /kFocusYoutubeComponentId/);
assert.doesNotMatch(componentDisableReasonsBody, /DISABLE_USER_ACTION/);
const pinnedPrefRegistration = extensionPrefs.match(
    /RegisterListPref\(pref_names::kPinnedExtensions,[\s\S]*?\);/)?.[0] || '';
assert.doesNotMatch(pinnedPrefRegistration, /kUBlockOriginComponentId/);
assert.doesNotMatch(pinnedPrefRegistration, /kFocusYoutubeComponentId/);

const toolbarModel = read('chrome/browser/ui/toolbar/toolbar_actions_model.cc');
const shouldAddExtensionBody = toolbarModel.match(
    /bool ToolbarActionsModel::ShouldAddExtension[\s\S]*?if \(extension->id\(\) == kFocusBlockExtensionId[\s\S]*?return false;\s*}/)?.[0] || '';
assert.match(shouldAddExtensionBody, /kFocusBlockExtensionId/);
assert.match(shouldAddExtensionBody, /kFocusYoutubeExtensionId/);
assert.match(shouldAddExtensionBody, /return false/);
assert.doesNotMatch(toolbarModel, /focus_default_pin_initialized/);
assert.doesNotMatch(toolbarModel, /pinned\.push_back\(kFocus/);

const manifestHeader = read('extensions/common/manifest.h');
const uBlockComponentHelper = manifestHeader.match(
    /static inline bool IsUBlockComponent[\s\S]*?\r?\n  }/)?.[0] || '';
assert.match(uBlockComponentHelper, /kUBlockOriginComponentId/);
const extensionUiUtil = read('extensions/browser/ui_util.cc');
const settingsVisibilityBody = extensionUiUtil.match(
    /bool ShouldDisplayInExtensionSettings[\s\S]*?\r?\n}/)?.[0] || '';
assert.match(settingsVisibilityBody, /Manifest::IsUBlockComponent/);
assert.match(settingsVisibilityBody, /focus::kFocusYoutubeComponentId/);
assert.match(settingsVisibilityBody,
             /Manifest::IsUBlockComponent\(extension_id\)[\s\S]*?kFocusYoutubeComponentId[\s\S]*?return false;/);

const toolbarView = read(
    'chrome/browser/ui/views/toolbar/toolbar_view.cc');
const toolbarViewHeader = read(
    'chrome/browser/ui/views/toolbar/toolbar_view.h');
const locationBarView = read(
    'chrome/browser/ui/views/location_bar/location_bar_view.cc');
const locationBarViewHeader = read(
    'chrome/browser/ui/views/location_bar/location_bar_view.h');
const focusBlockBubble = read(
    'chrome/browser/ui/views/location_bar/focus_block_bubble_view.cc');
const focusYoutubeBubble = read(
    'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc');
const focusYoutubeBubbleHeader = read(
    'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h');
const focusYoutubeIcon = read(
    'components/vector_icons/focus_youtube_off.icon');
assert.doesNotMatch(toolbarView,
                    /focus_block_button_ = AddChildView\(std::make_unique<ToolbarButton>/);
assert.doesNotMatch(toolbarView,
                    /focus_youtube_button_ = AddChildView\(std::make_unique<ToolbarButton>/);
assert.doesNotMatch(toolbarView, /ReorderChildView\(focus_block_button_/);
assert.doesNotMatch(toolbarViewHeader,
                    /raw_ptr<ToolbarButton> focus_block_button_/);
assert.match(locationBarView,
             /focus_block_button_ = AddChildView\(std::move\(focus_block_button\)\)/);
assert.match(locationBarView,
             /add_trailing_decoration\(focus_block_button_[\s\S]*?add_trailing_decoration\(page_action_icon_container_/,
             'FocusBlock must be the first decoration at the address-bar right edge');
assert.match(locationBarView,
             /IncrementalMinimumWidth\(focus_block_button_\)/,
             'Narrow omnibox layouts must reserve space for FocusBlock');
assert.match(locationBarViewHeader,
             /raw_ptr<views::ImageButton> focus_block_button_/);
assert.match(locationBarViewHeader,
             /void SetFocusBlockButtonVisible\(bool visible\)/);
assert.match(locationBarViewHeader,
             /virtual void ShowFocusBlockPopup\(views::View\*\)/);
assert.doesNotMatch(toolbarViewHeader,
                    /raw_ptr<ToolbarButton> focus_youtube_button_/);
assert.match(locationBarView,
             /focus_youtube_button = views::CreateVectorImageButton\(/);
assert.match(locationBarView,
             /focus_youtube_button_ = AddChildView\(std::move\(focus_youtube_button\)\)/);
assert.match(locationBarView,
             /add_trailing_decoration\(focus_youtube_button_/,
             'FocusYoutube must live inside the address field');
assert.match(locationBarView,
             /IncrementalMinimumWidth\(focus_youtube_button_\)/,
             'Narrow omnibox layouts must reserve space for FocusYoutube');
assert.match(locationBarView,
             /vector_icons::kFocusYoutubeOffIcon/,
             'FocusYoutube must use the crossed-out YouTube icon');
assert.match(locationBarView,
             /delegate_->ShowFocusYoutubePopup\(focus_youtube_button_\)/);
assert.match(locationBarViewHeader,
             /raw_ptr<views::ImageButton> focus_youtube_button_/);
assert.match(locationBarViewHeader,
             /void SetFocusYoutubeButtonVisible\(bool visible\)/);
assert.match(locationBarViewHeader,
             /virtual void ShowFocusYoutubePopup\(views::View\*\)/);
assert.match(focusYoutubeIcon,
             /MOVE_TO, 3\.2f, 2\.2f,[\s\S]*LINE_TO, 17\.8f, 16\.8f/);
assert.match(toolbarView,
             /FocusBlockBubbleView::ShowBubble\(browser_, anchor_view\)/,
             'FocusBlock must open its native Views bubble');
assert.doesNotMatch(toolbarView,
                    /ShowFocusComponentPopup\(focus::kUBlockOriginComponentId/);
assert.match(toolbarView,
             /FocusYoutubeBubbleView::ShowBubble\(browser_, anchor_view\)/,
             'FocusYoutube must open its native Views bubble');
assert.doesNotMatch(toolbarView,
                    /ShowFocusComponentPopup\(focus::kFocusYoutubeComponentId/);
assert.doesNotMatch(toolbarView,
                    /ExtensionPopup::(?:ShowPopup|last_popup_for_testing)/);
assert.match(focusYoutubeBubbleHeader,
             /class FocusYoutubeBubbleView : public LocationBarBubbleDelegateView/);
assert.match(focusYoutubeBubble,
             /FocusYoutubeBubbleView::ShowBubble\(Browser\* browser,/);
assert.match(focusYoutubeBubble,
             /constexpr std::array<FeatureSpec, 25> kFeatures/);
assert.match(focusYoutubeBubble,
             /constexpr int kFocusYoutubeSchemaVersion = 4/);
assert.match(focusYoutubeBubble,
             /CompositeFeature\(3, "remove_trending_page", "remove_explore_link",[\s\S]*?"remove_explore_section"/);
assert.match(focusYoutubeBubble,
             /CompositeFeature\(3, "remove_subscriptions_page",[\s\S]*?"remove_subscriptions_link", "remove_sub_section"/);
const focusYoutubeFeatureWrite = focusYoutubeBubble.match(
    /void FocusYoutubeBubbleView::OnFeatureTogglePressed\([\s\S]*?\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(focusYoutubeFeatureWrite);
assert.match(focusYoutubeFeatureWrite,
             /base::DictValue values;[\s\S]*StorageKeys\(\*feature\)[\s\S]*values\.Set\(storage_key, requested_value\)/);
assert.equal(
    [...focusYoutubeFeatureWrite.matchAll(/storage->Set\s*\(/g)].length, 1,
    'Composite FocusYoutube controls must use one atomic storage write');
assert.match(focusYoutubeBubble,
             /constexpr std::array<GroupSpec, 4> kGroups/);
assert.match(focusYoutubeBubble,
             /storage->Clear\(extension_, extensions::StorageAreaNamespace::kLocal/);
assert.match(focusYoutubeBubble,
             /storage->Set\(extension_, extensions::StorageAreaNamespace::kLocal,[\s\S]*?ResetValues\(\)/);
assert.doesNotMatch(focusYoutubeBubble, /https?:\/\//,
                    'Native FocusYoutube bubble must not link outside the browser');
assert.match(focusBlockBubble,
             /FocusBlockServiceFactory::GetForProfile/);
assert.match(focusBlockBubble,
             /service_->SetEnabled\(global_toggle_->GetIsOn\(\)\)/);
assert.match(focusBlockBubble,
             /service_->SetEnabledForUrl\(page_url_, site_toggle_->GetIsOn\(\)\)/);
assert.match(focusBlockBubble, /service_->engine_ready\(\)/);
assert.match(focusBlockBubble,
             /u"Защита во всём браузере"/);
assert.match(focusBlockBubble,
             /u"Защита на этом сайте"/);
assert.match(focusBlockBubble,
             /GetBlockedCountForUrl\(page_url_\)/);
assert.match(focusBlockBubble,
             /blocked_count_session\(\)/);
assert.match(focusBlockBubble,
             /EasyList \+ EasyPrivacy[\s\S]*?Ghostery 2\.18\.1/);
assert.doesNotMatch(focusBlockBubble, /https?:\/\//,
                    'Native FocusBlock bubble must not link outside the browser');
assert.match(toolbarView,
             /bool IsFocusYoutubeUrl\(const GURL& url\) \{[\s\S]*?url\.SchemeIs\(url::kHttpsScheme\)[\s\S]*?url\.DomainIs\("youtube\.com"\)/);
assert.doesNotMatch(toolbarView,
                    /bool IsFocusYoutubeUrl[\s\S]{0,240}(?:host\s*==|ends_with|StartsWith)/);
assert.match(toolbarView,
             /bool IsFocusYoutubeTab\(WebContents\* tab\) \{[\s\S]*?GetVisibleURL\(\)[\s\S]*?GetLastCommittedURL\(\)[\s\S]*?GetVisibleEntry\(\)[\s\S]*?GetVirtualURL\(\)[\s\S]*?GetPendingEntry\(\)[\s\S]*?GetVirtualURL\(\)/);
assert.match(toolbarView,
             /UpdateFocusYoutubeButtonVisibility\(WebContents\* tab\) \{[\s\S]*?IsFocusYoutubeTab\(tab\)/);
assert.match(toolbarView,
             /location_bar_view_->SetFocusYoutubeButtonVisible\(/);

// Default-browser handoff must fail closed. The native handler is shared by
// Settings and onboarding, so neither stale policy state nor malformed WebUI
// arguments may terminate the browser process.
const defaultBrowserHandler = read(
    'chrome/browser/ui/webui/settings/settings_default_browser_handler.cc');
assert.doesNotMatch(
    defaultBrowserHandler,
    /CHECK\(!DefaultBrowserIsDisabledByPolicy\(\)\)/);
assert.match(
    defaultBrowserHandler,
    /if \(!pref \|\| !pref->GetValue\(\) \|\|\s*!pref->GetValue\(\)->is_bool\(\)\)/);
assert.match(
    defaultBrowserHandler,
    /void DefaultBrowserHandler::SetAsDefaultBrowser[\s\S]*?AllowJavascript\(\);[\s\S]*?if \(DefaultBrowserIsDisabledByPolicy\(\) \|\|\s*!default_browser_controller_\)[\s\S]*?UNKNOWN_DEFAULT[\s\S]*?return;/);
assert.match(
    defaultBrowserHandler,
    /!args\.empty\(\) && args\[0\]\.is_bool\(\) && args\[0\]\.GetBool\(\)/);
assert.match(
    defaultBrowserHandler,
    /!did_user_interact_ && default_browser_controller_/);

const onboardingDefaultBrowser = read(
    'components/focus_onboarding/src/lib/browser/is-default.ts');
assert.match(onboardingDefaultBrowser,
             /"loading" \| "ready" \| "error"/);
assert.match(onboardingDefaultBrowser,
             /const _canBeDefaultBrowser = writable\(false\)/);
assert.match(onboardingDefaultBrowser,
             /writable<DefaultBrowserAvailabilityState>\("loading"\)/);
assert.match(onboardingDefaultBrowser,
             /requestDefaultBrowserState\(\)[\s\S]*?\.catch\(/);
assert.match(onboardingDefaultBrowser,
             /browser-default-state-changed[\s\S]*?applyDefaultBrowserState\(state\)/);
assert.match(onboardingDefaultBrowser,
             /if \(defaultBrowserRequestStarted\) \{\s*return true;/);
assert.match(onboardingDefaultBrowser,
             /get\(_availabilityState\) !== "ready"[\s\S]*?!get\(_canBeDefaultBrowser\)[\s\S]*?get\(_defaultBrowser\)/);
assert.doesNotMatch(onboardingDefaultBrowser,
                    /export let canBeDefaultBrowser = true/);

const onboardingFlow = read(
    'components/focus_onboarding/src/lib/onboarding-flow.ts');
assert.match(onboardingFlow,
             /get\(defaultBrowserAvailabilityState\) !== "ready"/);
assert.match(onboardingFlow, /!get\(canBeDefaultBrowser\)/);
const onboardingNavigation = read(
    'components/focus_onboarding/src/components/PageNavigation.svelte');
assert.match(onboardingNavigation,
             /\$userChoseFocusAsDefault && !askToBeDefault\(\)/);

const toolbarViewBrowserTest = read(
    'chrome/browser/ui/views/toolbar/toolbar_view_browsertest.cc');
const removedFocusModePattern =
    /FocusModeHudView|focus_button_|focus_mode_hud|focus_mode_timer_|focus_mode_end_time_|StartFocusMode|StopFocusMode|ToggleFocusMode|SetFocusModeNotificationsBlocked|FocusSessionShowsHud|FocusSessionPreserves/;
assert.doesNotMatch(toolbarView, removedFocusModePattern);
assert.doesNotMatch(toolbarViewHeader, removedFocusModePattern);
assert.doesNotMatch(toolbarViewBrowserTest, removedFocusModePattern);
const notificationDisplayService = read(
    'chrome/browser/notifications/notification_display_service_impl.cc');
const notificationDisplayServiceHeader = read(
    'chrome/browser/notifications/notification_display_service_impl.h');
assert.doesNotMatch(notificationDisplayService, removedFocusModePattern);
assert.doesNotMatch(notificationDisplayServiceHeader, removedFocusModePattern);
const productStrings = read('chrome/app/chromium_strings.grd');
assert.doesNotMatch(productStrings, /with Focus Mode/);

const focusPrefNames = read('components/focus_services/pref_names.h');
assert.match(focusPrefNames, /kShowFocusBlockButton/);
assert.match(focusPrefNames, /kShowFocusYoutubeButton/);
const browserUiPrefs = read('chrome/browser/ui/browser_ui_prefs.cc');
assert.match(browserUiPrefs,
             /RegisterBooleanPref\(prefs::kShowFocusBlockButton, true\)/);
assert.match(browserUiPrefs,
             /RegisterBooleanPref\(prefs::kShowFocusYoutubeButton, true\)/);
assert.match(toolbarView, /prefs::kShowFocusBlockButton/);
assert.match(toolbarView, /prefs::kShowFocusYoutubeButton/);
assert.match(toolbarView, /show_focus_block_button_\.GetValue\(\)/);
assert.match(toolbarView, /show_focus_youtube_button_\.GetValue\(\)/);

const customizeToolbarMojom = read(
    'chrome/browser/ui/webui/side_panel/customize_chrome/' +
    'customize_toolbar/customize_toolbar.mojom');
assert.match(customizeToolbarMojom, /kFocusBlock/);
assert.match(customizeToolbarMojom, /kFocusYoutube/);
const customizeToolbarHandler = read(
    'chrome/browser/ui/webui/side_panel/customize_chrome/' +
    'customize_toolbar/customize_toolbar_handler.cc');
assert.match(customizeToolbarHandler,
             /ActionId::kFocusBlock[\s\S]*?prefs::kShowFocusBlockButton/);
assert.match(customizeToolbarHandler,
             /ActionId::kFocusYoutube[\s\S]*?prefs::kShowFocusYoutubeButton/);
assert.match(customizeToolbarHandler,
             /OnBrowserOwnedActionPinnedChanged/);
const customizePinBody = customizeToolbarHandler.match(
    /void CustomizeToolbarHandler::PinAction[\s\S]*?\r?\n}/)?.[0] || '';
assert.match(customizePinBody,
             /ActionId::kFocusBlock[\s\S]*?SetBoolean\(prefs::kShowFocusBlockButton, pin\)/);
assert.match(customizePinBody,
             /ActionId::kFocusYoutube[\s\S]*?SetBoolean\(prefs::kShowFocusYoutubeButton, pin\)/);
const pinnedToolbarModel = read(
    'chrome/browser/ui/toolbar/pinned_toolbar/' +
    'pinned_toolbar_actions_model.cc');
assert.match(pinnedToolbarModel,
             /ClearPref\(prefs::kShowFocusBlockButton\)/);
assert.match(pinnedToolbarModel,
             /ClearPref\(prefs::kShowFocusYoutubeButton\)/);
assert.match(pinnedToolbarModel,
             /prefs::kShowFocusBlockButton,[\s\S]*?prefs::kShowFocusYoutubeButton/);

const extensionContextMenu = read(
    'chrome/browser/extensions/extension_context_menu_model.cc');
assert.match(extensionContextMenu,
             /can_uninstall_extension = !is_component_/);

// Relevant overrides are the reproducible source of truth for a fresh source
// tree. Other features may be edited concurrently by independent build agents.
const relevantOverrideFiles = [
  ...walk(path.join(overridesRoot, 'third_party', 'focus_text_motion')),
  ...walk(path.join(overridesRoot, 'third_party', 'focus_youtube')),
  ...walk(path.join(overridesRoot, 'third_party', 'ublock')),
  ...[
    'chrome/browser/extensions/BUILD.gn',
    'chrome/browser/extensions/chrome_component_extension_resource_manager.cc',
    'chrome/browser/extensions/component_extensions_allowlist/allowlist.cc',
    'chrome/browser/extensions/component_loader.cc',
    'chrome/browser/extensions/component_loader.h',
    'chrome/browser/extensions/standard_management_policy_provider.cc',
    'chrome/browser/ui/browser_ui_prefs.cc',
    'chrome/browser/ui/toolbar/toolbar_actions_model.cc',
    'chrome/browser/ui/toolbar/pinned_toolbar/pinned_toolbar_actions_model.cc',
    'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc',
    'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h',
    'chrome/browser/ui/views/toolbar/toolbar_view.cc',
    'chrome/browser/ui/views/toolbar/toolbar_view.h',
    'chrome/browser/ui/views/toolbar/toolbar_view_browsertest.cc',
    'chrome/browser/ui/webui/settings/settings_default_browser_handler.cc',
    'chrome/browser/ui/webui/side_panel/customize_chrome/customize_toolbar/customize_toolbar.mojom',
    'chrome/browser/ui/webui/side_panel/customize_chrome/customize_toolbar/customize_toolbar_handler.cc',
    'chrome/browser/ui/webui/side_panel/customize_chrome/customize_toolbar/customize_toolbar_handler.h',
    'components/focus_services/extension_ids.h',
    'components/focus_services/pref_names.h',
    'components/focus_onboarding/src/components/PageNavigation.svelte',
    'components/focus_onboarding/src/lib/browser/is-default.ts',
    'components/focus_onboarding/src/lib/onboarding-flow.ts',
    'chrome/chrome_paks.gni',
    'extensions/browser/ui_util.cc',
    'extensions/browser/extension_prefs.cc',
    'extensions/common/manifest.h',
    'third_party/blink/renderer/core/editing/caret_display_item_client.cc',
    'third_party/blink/renderer/core/editing/caret_display_item_client.h',
    'third_party/blink/renderer/core/editing/commands/insert_incremental_text_command_test.cc',
    'tools/gritsettings/resource_ids.spec',
  ].map(relative => path.join(overridesRoot, relative)),
];
for (const overrideFile of relevantOverrideFiles) {
  const relative = path.relative(overridesRoot, overrideFile);
  const sourceFile = path.join(sourceRoot, relative);
  assert.ok(fs.existsSync(sourceFile), `Override target is missing: ${relative}`);
  assert.deepEqual(readMirroredFile(overrideFile), readMirroredFile(sourceFile),
                   `Override differs from active source: ${relative}`);
}

console.log(`Focus component checks passed: ${jsonFiles.length} JSON files, ` +
            'stable IDs, native UI, text motion, lifecycle and overrides.');
