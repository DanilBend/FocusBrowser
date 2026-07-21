// Copyright 2026 The Focus Browser Authors
// Static integrity checks for the two browser-owned protection components.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(repoRoot, 'build', 'src');
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
  'https://www.youtube.com/*',
  'https://m.youtube.com/*',
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

// Browser integration: both engines stay loaded as non-removable components,
// never enter generic extension surfaces, and use dedicated native controls.
const componentLoader = read('chrome/browser/extensions/component_loader.cc');
assert.match(componentLoader, /AddUBlock\(\)/);
assert.match(componentLoader, /AddFocusYoutube\(\)/);
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
    /bool AdminPolicyIsModifiable[\s\S]*?\n}\n\n}  \/\/ namespace/)?.[0] || '';
assert.match(
    modifiablePolicyBody,
    /Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]*?is_modifiable = false;/);
const remainEnabledBody = managementPolicy.match(
    /bool StandardManagementPolicyProvider::MustRemainEnabled[\s\S]*?\n}/)?.[0] || '';
assert.match(remainEnabledBody, /!AdminPolicyIsModifiable/);
const remainInstalledBody = managementPolicy.match(
    /bool StandardManagementPolicyProvider::MustRemainInstalled[\s\S]*?\n}/)?.[0] || '';
assert.match(remainInstalledBody,
             /Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]*?return true;/);

const extensionPrefs = read('extensions/browser/extension_prefs.cc');
const componentDisableReasonsBody = extensionPrefs.match(
    /void ExtensionPrefs::ClearInapplicableDisableReasonsForComponentExtension[\s\S]*?\n}/)?.[0] || '';
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
    /static inline bool IsUBlockComponent[\s\S]*?\n  }/)?.[0] || '';
assert.match(uBlockComponentHelper, /kUBlockOriginComponentId/);
const extensionUiUtil = read('extensions/browser/ui_util.cc');
const settingsVisibilityBody = extensionUiUtil.match(
    /bool ShouldDisplayInExtensionSettings[\s\S]*?\n}/)?.[0] || '';
assert.match(settingsVisibilityBody, /Manifest::IsUBlockComponent/);
assert.match(settingsVisibilityBody, /focus::kFocusYoutubeComponentId/);
assert.match(settingsVisibilityBody,
             /Manifest::IsUBlockComponent\(extension_id\)[\s\S]*?kFocusYoutubeComponentId[\s\S]*?return false;/);

const toolbarView = read(
    'chrome/browser/ui/views/toolbar/toolbar_view.cc');
const toolbarViewHeader = read(
    'chrome/browser/ui/views/toolbar/toolbar_view.h');
assert.match(toolbarView,
             /focus_block_button_ = AddChildView\(std::make_unique<ToolbarButton>/);
assert.match(toolbarView,
             /focus_youtube_button_ = AddChildView\(std::make_unique<ToolbarButton>/);
assert.match(toolbarView,
             /views::View\* const location_bar_container =[\s\S]*?ReorderChildView\(focus_block_button_,[\s\S]*?GetIndexOf\(location_bar_container\)\.value\(\)\);/,
             'FocusBlock must stay immediately left of the address bar');
assert.match(toolbarViewHeader, /raw_ptr<ToolbarButton> focus_block_button_/);
assert.match(toolbarViewHeader, /raw_ptr<ToolbarButton> focus_youtube_button_/);
assert.match(toolbarView,
             /ShowFocusComponentPopup\(focus::kUBlockOriginComponentId,[\s\S]*?"popup-fenix\.html"/);
assert.match(toolbarView,
             /ShowFocusComponentPopup\(focus::kFocusYoutubeComponentId, "popup\.html"/);
assert.match(toolbarView,
             /prefs::kFocusMotionEnabled[\s\S]*?popup_resource\.append\("\?focusMotion=0"\)[\s\S]*?GetResourceURL\(popup_resource\)/,
             'Focus popup URLs must carry the shared motion preference');
assert.match(toolbarView, /ExtensionPopup::ShowPopup/);
assert.match(toolbarView, /ExtensionPopup::last_popup_for_testing/);
assert.match(toolbarView,
             /host == "www\.youtube\.com" \|\| host == "m\.youtube\.com"/);
assert.match(toolbarView, /committed_url\.SchemeIs\(url::kHttpsScheme\)/);
assert.doesNotMatch(toolbarView, /DomainIs\("youtube\.com"\)/);

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
    /void CustomizeToolbarHandler::PinAction[\s\S]*?\n}/)?.[0] || '';
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
    'extensions/browser/ui_util.cc',
    'extensions/browser/extension_prefs.cc',
    'extensions/common/manifest.h',
  ].map(relative => path.join(overridesRoot, relative)),
];
for (const overrideFile of relevantOverrideFiles) {
  const relative = path.relative(overridesRoot, overrideFile);
  const sourceFile = path.join(sourceRoot, relative);
  assert.ok(fs.existsSync(sourceFile), `Override target is missing: ${relative}`);
  assert.deepEqual(fs.readFileSync(overrideFile), fs.readFileSync(sourceFile),
                   `Override differs from active source: ${relative}`);
}

console.log(`Focus component checks passed: ${jsonFiles.length} JSON files, ` +
            'stable IDs, packaged resources, Russian UI, lifecycle and overrides.');
