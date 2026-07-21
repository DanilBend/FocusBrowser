#!/usr/bin/env node

// Static contract checks for the built-in Focus Text Motion component.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const projectRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)), '..');
const overridesRoot = path.join(projectRoot, 'source_overrides');
const activeRoot = path.join(projectRoot, 'build', 'src');

const read = (root, relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');
const digest = file => crypto.createHash('sha256')
    .update(fs.readFileSync(file))
    .digest('hex');

const pairedFiles = [
  'third_party/focus_text_motion/BUILD.gn',
  'third_party/focus_text_motion/generate_file_list.py',
  'third_party/focus_text_motion/manifest.json',
  'third_party/focus_text_motion/background.js',
  'third_party/focus_text_motion/content-script.js',
  'components/focus_services/extension_ids.h',
  'chrome/browser/extensions/BUILD.gn',
  'chrome/browser/extensions/component_loader.h',
  'chrome/browser/extensions/component_loader.cc',
  'chrome/browser/extensions/component_extensions_allowlist/allowlist.cc',
  'chrome/browser/extensions/chrome_component_extension_resource_manager.cc',
  'chrome/browser/ui/toolbar/toolbar_actions_model.cc',
  'chrome/chrome_paks.gni',
  'extensions/browser/ui_util.cc',
  'tools/gritsettings/resource_ids.spec',
];

for (const relativePath of pairedFiles) {
  const active = path.join(activeRoot, relativePath);
  const override = path.join(overridesRoot, relativePath);
  assert.ok(fs.existsSync(active), `missing active file: ${relativePath}`);
  assert.ok(fs.existsSync(override), `missing override file: ${relativePath}`);
  assert.equal(
      digest(active), digest(override),
      `active/override drift: ${relativePath}`);
}

const manifest = JSON.parse(
    read(activeRoot, 'third_party/focus_text_motion/manifest.json'));
assert.equal(manifest.manifest_version, 3);
assert.equal(manifest.incognito, 'split');
assert.deepEqual(manifest.permissions.sort(), ['settingsPrivate', 'storage']);
assert.deepEqual(
    manifest.host_permissions,
    ['http://*/*', 'https://*/*']);
assert.equal(manifest.background?.service_worker, 'background.js');
assert.equal(manifest.content_scripts?.length, 1);
assert.deepEqual(
    manifest.content_scripts[0].matches,
    ['http://*/*', 'https://*/*']);
assert.deepEqual(manifest.content_scripts[0].js, ['content-script.js']);
assert.equal(manifest.content_scripts[0].all_frames, true);
assert.equal(manifest.content_scripts[0].match_origin_as_fallback, true);
assert.equal(manifest.content_scripts[0].run_at, 'document_start');
for (const forbidden of [
  'action', 'browser_action', 'page_action', 'options_page', 'options_ui',
]) {
  assert.equal(manifest[forbidden], undefined,
               `built-in component exposes forbidden ${forbidden}`);
}

const publicKey = Buffer.from(manifest.key, 'base64');
const extensionId = crypto.createHash('sha256')
    .update(publicKey)
    .digest()
    .subarray(0, 16)
    .toString('hex')
    .replace(/[0-9a-f]/g, nibble =>
      String.fromCharCode('a'.charCodeAt(0) + Number.parseInt(nibble, 16)));
assert.equal(extensionId, 'ajekofejbbjbbkdfnlghakcilbfdmofc');

const background = read(
    activeRoot, 'third_party/focus_text_motion/background.js');
assert.match(background, /focus\.ui\.motion_enabled/);
assert.match(background, /chrome\.settingsPrivate\.getPref/);
assert.match(background, /chrome\.settingsPrivate\.onPrefsChanged/);
assert.match(background, /chrome\.storage\.local\.set/);
assert.match(background, /focus-text-motion\.get-state/);
assert.doesNotMatch(background, /console\.|fetch\(|XMLHttpRequest|WebSocket/);

const content = read(
    activeRoot, 'third_party/focus_text_motion/content-script.js');
for (const required of [
  "'text'", "'search'", "'email'", "'url'", "'tel'", "'number'",
  "'password'", '[contenteditable]', 'Intl.Segmenter', 'beforeinput',
  'compositionstart', 'compositionend', 'isComposing', 'event.composedPath()',
  'document.createRange()', "document.createElement('canvas')",
  "document.createElement('focus-text-motion-layer')", "mode: 'closed'",
  'mask.animate', 'glyphView.animate', 'prefers-reduced-motion: reduce',
  'forced-colors: active', 'chrome.storage.onChanged',
  'data-focus-text-motion', 'data-focus-motion-active',
  'MAX_GRAPHEMES = 16', 'MAX_ACTIVE_GLYPHS = 48',
  "style.setProperty(name, value, 'important')",
]) {
  assert.ok(content.includes(required), `content contract missing: ${required}`);
}
assert.match(content, /if \(password\) \{[\s\S]*?\} else \{\s*const value = target\.value/);
const beforeInputStart = content.indexOf(
    "document.addEventListener('beforeinput'");
const beforeInputEnd = content.indexOf(
    "document.addEventListener('input'", beforeInputStart);
const beforeInput = content.slice(beforeInputStart, beforeInputEnd);
assert.ok(beforeInput.indexOf('if (isPassword(target))') !== -1);
assert.ok(beforeInput.indexOf('if (isPassword(target))') <
              beforeInput.indexOf('event.data'),
          'password guard must precede all InputEvent.data reads');
assert.match(beforeInput, /pendingInsertions\.set\(target, \{password: true\}\)/);
assert.doesNotMatch(content, /target\.value\s*=|\.execCommand\(|innerHTML\s*=/);
assert.doesNotMatch(content, /console\.|fetch\(|XMLHttpRequest|WebSocket/);

const extensionIds = read(
    activeRoot, 'components/focus_services/extension_ids.h');
assert.match(extensionIds,
             /kFocusTextMotionComponentId\[\][\s\S]*ajekofejbbjbbkdfnlghakcilbfdmofc/);

const componentLoader = read(
    activeRoot, 'chrome/browser/extensions/component_loader.cc');
assert.match(componentLoader,
             /void ComponentLoader::AddFocusTextMotion\(\)[\s\S]*IDR_FOCUS_TEXT_MOTION_MANIFEST_JSON/);
assert.match(componentLoader,
             /AddDefaultComponentExtensions\([\s\S]*AddFocusTextMotion\(\)/);

const componentLoaderHeader = read(
    activeRoot, 'chrome/browser/extensions/component_loader.h');
assert.match(componentLoaderHeader, /void AddFocusTextMotion\(\);/);

const allowlist = read(
    activeRoot,
    'chrome/browser/extensions/component_extensions_allowlist/allowlist.cc');
assert.match(allowlist, /focus::kFocusTextMotionComponentId/);
assert.match(allowlist, /IDR_FOCUS_TEXT_MOTION_MANIFEST_JSON/);

const resourceManager = read(
    activeRoot,
    'chrome/browser/extensions/chrome_component_extension_resource_manager.cc');
assert.match(resourceManager, /kFocusTextMotionResources/);

const browserExtensionsBuild = read(
    activeRoot, 'chrome/browser/extensions/BUILD.gn');
assert.match(browserExtensionsBuild, /\/\/third_party\/focus_text_motion/);
const chromePaks = read(activeRoot, 'chrome/chrome_paks.gni');
assert.match(chromePaks, /focus_text_motion_resources\.pak/);
assert.match(chromePaks, /\/\/third_party\/focus_text_motion/);
const resourceIds = read(activeRoot, 'tools/gritsettings/resource_ids.spec');
assert.match(resourceIds,
             /third_party\/focus_text_motion\/focus_text_motion_resources\.grd/);

const extensionsUi = read(activeRoot, 'extensions/browser/ui_util.cc');
assert.match(extensionsUi, /kFocusTextMotionComponentId/);
const toolbarActions = read(
    activeRoot, 'chrome/browser/ui/toolbar/toolbar_actions_model.cc');
assert.match(toolbarActions, /kFocusTextMotionExtensionId/);

const prefNames = read(activeRoot, 'components/focus_services/pref_names.h');
assert.match(prefNames,
             /kFocusMotionEnabled\[\][\s\S]*focus\.ui\.motion_enabled/);
const browserPrefs = read(
    activeRoot, 'chrome/browser/ui/browser_ui_prefs.cc');
assert.match(browserPrefs,
             /RegisterBooleanPref\(prefs::kFocusMotionEnabled, true\)/);
const prefsUtil = read(
    activeRoot,
    'chrome/browser/extensions/api/settings_private/prefs_util.cc');
assert.match(prefsUtil, /kFocusMotionEnabled[\s\S]*PrefType::kBoolean/);

const permissionFeatures = read(
    activeRoot, 'chrome/common/extensions/api/_permission_features.json');
assert.match(permissionFeatures,
             /"settingsPrivate"[\s\S]*?"location": "component"[\s\S]*?"platforms": \[[^\]]*"win"/);
const brandingBuildflagsPath = path.join(
    activeRoot, 'out', 'Default', 'gen', 'build', 'branding_buildflags.h');
if (fs.existsSync(brandingBuildflagsPath)) {
  assert.match(fs.readFileSync(brandingBuildflagsPath, 'utf8'),
               /GOOGLE_CHROME_BRANDING\(\) \(0\)/);
}

const runtimeQa = fs.readFileSync(
    path.join(projectRoot, 'qa', 'verify_focus_text_motion_runtime.mjs'),
    'utf8');
assert.match(runtimeQa,
             /settingsPrivate\.setPref[\s\S]*native pref=false[\s\S]*native pref=true/);
assert.match(runtimeQa, /taskkill[\s\S]*\/PID[\s\S]*\/T[\s\S]*\/F/);
assert.match(runtimeQa,
             /getBoundingClientRect\(\)[\s\S]*viewportWidth[\s\S]*viewportHeight/);

console.log(JSON.stringify({
  ok: true,
  componentId: extensionId,
  frames: 'all http/https frames',
  motionPreference: 'focus.ui.motion_enabled (default true, live)',
  passwordPolicy: 'generic bullet only',
}, null, 2));
