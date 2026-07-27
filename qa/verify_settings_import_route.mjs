// Copyright 2026 The Focus Browser Authors
// Static contract checks for the Settings import-data route and backend.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const overrideRoot = path.join(repoRoot, 'source_overrides');
const activeRoot = process.env.FOCUS_ACTIVE_SOURCE_ROOT ?
  path.resolve(process.env.FOCUS_ACTIVE_SOURCE_ROOT) :
  path.join(repoRoot, 'build', 'src');

const read = (root, relativePath) =>
  fs.readFileSync(path.join(root, relativePath), 'utf8');

const routeRelative = 'chrome/browser/resources/settings/route.ts';
const menuRelative =
    'chrome/browser/resources/settings/settings_menu/settings_menu.html';
const proxyRelative =
    'chrome/browser/resources/settings/people_page/import_data_browser_proxy.ts';
const peopleRelative =
    'chrome/browser/resources/settings/people_page/people_page.ts';
const peopleIndexRelative =
    'chrome/browser/resources/settings/people_page/people_page_index.ts';
const importGuardPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/stabilize-import-data-handler.patch');
const chromeImporterGuardPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/stabilize-chrome-importer.patch');
const importQaOverridePatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/add-import-qa-source-override.patch');
const importQaHardeningPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/harden-import-qa-source-override.patch');
const importQaIsolationPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/isolate-import-qa-profile-discovery.patch');
const focusImportRenamePatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/rename-focus-import-product-layer.patch');
const focusImportInternalsPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/core/rename-focus-import-internals.patch');
const settingsBuildOverride = read(
    overrideRoot, 'chrome/browser/ui/BUILD.gn');
const generatedResourcesOverride = read(
    overrideRoot, 'chrome/app/generated_resources.grd');
const settingsUiOverride = read(
    overrideRoot, 'chrome/browser/ui/webui/settings/settings_ui.cc');
const defaultBrowserHandler = read(
    overrideRoot,
    'chrome/browser/ui/webui/settings/settings_default_browser_handler.cc');
const patchSeries = read(repoRoot, 'focus-chromium/patches/series')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line && !line.startsWith('#'));
const keyboardShortcutsPatch = read(
    repoRoot,
    'focus-chromium/patches/focus/settings/custom-keyboard-shortcuts-page.patch');
const realImportRuntimeQa = read(
    repoRoot, 'qa/verify_settings_import_real_runtime.mjs');

const keyboardPatchIndex = patchSeries.indexOf(
    'focus/settings/custom-keyboard-shortcuts-page.patch');
const importRenameIndex = patchSeries.indexOf(
    'focus/core/rename-focus-import-product-layer.patch');
const stabilizeChromeImporterIndex = patchSeries.indexOf(
    'focus/core/stabilize-chrome-importer.patch');
const importQaOverrideIndex = patchSeries.indexOf(
    'focus/core/add-import-qa-source-override.patch');
const importQaHardeningIndex = patchSeries.indexOf(
    'focus/core/harden-import-qa-source-override.patch');
const importQaIsolationIndex = patchSeries.indexOf(
    'focus/core/isolate-import-qa-profile-discovery.patch');
assert.ok(keyboardPatchIndex >= 0 && importRenameIndex > keyboardPatchIndex,
          'import rename must remain after earlier settings patches');
assert.ok(stabilizeChromeImporterIndex >= 0,
          'the hardened Chrome importer patch is missing from the series');
assert.equal(
    importQaOverrideIndex, stabilizeChromeImporterIndex + 1,
    'the QA-only source override must immediately follow importer hardening');
assert.equal(
    importQaHardeningIndex, importQaOverrideIndex + 1,
    'QA source containment must immediately follow the source override');
assert.equal(
    importQaIsolationIndex, importQaHardeningIndex + 1,
    'outer QA discovery isolation must immediately follow containment');
assert.match(
    keyboardShortcutsPatch,
    /^ #include "chrome\/browser\/ui\/webui\/settings\/brave_import_data_handler\.h"$/m,
    'the early settings patch must match the pre-rename importer include');
const qaHookGuardIndex = realImportRuntimeQa.indexOf(
    'assert.ok(qaHookSources.length > 0');
const qaOwnedRootIndex = realImportRuntimeQa.indexOf('const ownedRoot =');
const qaBinaryGuardIndex = realImportRuntimeQa.indexOf(
    'assert.ok(fileContainsAsciiToken(chromeLibraryPath, qaHookBuildMarker)');
const qaOuterGuardIndex = realImportRuntimeQa.indexOf(
    'const isolatedDiscoveryBranch = qaHookContract.match(');
const qaBrowserSpawnIndex = realImportRuntimeQa.indexOf(
    'const browser = spawn(executablePath');
assert.ok(qaHookGuardIndex >= 0 && qaBinaryGuardIndex >= 0 &&
              qaOuterGuardIndex >= 0 &&
              qaOwnedRootIndex > qaBinaryGuardIndex &&
              qaOwnedRootIndex > qaHookGuardIndex &&
              qaOwnedRootIndex > qaOuterGuardIndex &&
              qaBrowserSpawnIndex > qaHookGuardIndex,
          'real import QA must fail closed before creating profiles or launching');
assert.match(realImportRuntimeQa, /Refusing to run:/);
assert.match(realImportRuntimeQa, /real browser profile/);
assert.match(realImportRuntimeQa, /chrome\.dll/);
assert.match(realImportRuntimeQa, /FOCUS_IMPORT_QA_HOOK_V3/);
assert.match(realImportRuntimeQa, /Traversal QA Source/);

function patchPostimage(source) {
  if (!source.includes('diff --git ')) return source;
  return source.split(/\r?\n/)
      .filter(line => !line.startsWith('diff --git ') &&
          !line.startsWith('--- ') && !line.startsWith('+++ ') &&
          !line.startsWith('@@ ') && !line.startsWith('-'))
      .map(line => line.startsWith('+') || line.startsWith(' ') ?
        line.slice(1) : line)
      .join('\n');
}

function assertImportQaOverrideContract(source, label) {
  const postimage = patchPostimage(source);
  for (const token of [
    'FOCUS_IMPORT_QA_CHROME_USER_DATA_DIR',
    'FOCUS_IMPORT_QA_HOOK_V3',
    'FocusImportQaOverrideState',
    'kUnset',
    'kValid',
    'kInvalid',
    'base::Environment::Create()',
    'base::NormalizeFilePath(requested_source',
    'base::NormalizeFilePath(requested_target',
    'base::GetTempDir(&temp_dir)',
    'focus-real-import-qa-',
    'chrome-source',
    'User Data',
    'focus-target',
    'source.AppendASCII("Local State")',
    'require_focus_qa_containment',
    'relative_profile.ReferencesParent()',
    'relative_profile.GetComponents()',
    'base::NormalizeFilePath(profile_path, &normalized_profile)',
    'user_data_importer::TYPE_CHROME, true',
    'FocusImportQaOverrideIsPresent()',
    'HasVar(',
  ]) {
    assert.ok(postimage.includes(token), `${label}: missing ${token}`);
  }
  assert.match(
      postimage,
      /HasSwitch\(switches::kUserDataDir\)[\s\S]*DirectoryExists\(requested_source\)[\s\S]*DirectoryExists\(requested_target\)/,
      `${label}: source and destination ownership guards are incomplete`);
  assert.match(
      postimage,
      /qa_override\.state != FocusImportQaOverrideState::kUnset[\s\S]*qa_override\.state == FocusImportQaOverrideState::kValid[\s\S]*AddChromeToProfiles\([\s\S]*return;/,
      `${label}: invalid overrides must return before normal profile discovery`);
}

function assertImportQaOuterIsolation(source, label) {
  const postimage = patchPostimage(source);
  assert.match(
      postimage,
      /bool FocusImportQaOverrideIsPresent\(\) \{\s*return base::Environment::Create\(\)->HasVar\(\s*kFocusImportQaChromeUserDataDir\);\s*\}/,
      `${label}: exact environment-presence guard is missing`);
  const branch = postimage.match(
      /#if BUILDFLAG\(IS_WIN\)\s+if \(FocusImportQaOverrideIsPresent\(\)\) \{([\s\S]*?)\}\s+else if \(shell_integration::IsFirefoxDefaultBrowser\(\)\)/)?.[1];
  assert.ok(branch, `${label}: outer QA-only discovery branch is missing`);
  assert.match(branch, /DetectChromeProfiles\(&profiles\);/);
  assert.doesNotMatch(
      branch,
      /Detect(?:Firefox|Zen|BuiltinWindows|IE|Edge|Safari)Profiles|Is(?:Firefox|IE)DefaultBrowser|Get(?:Firefox|Zen)Details|EdgeImporterCanImport/,
      `${label}: QA branch may inspect a real non-Chrome profile`);
}

assertImportQaOverrideContract(
    `${importQaOverridePatch}\n${importQaHardeningPatch}\n${importQaIsolationPatch}`,
    'QA override patches');
assertImportQaOuterIsolation(importQaIsolationPatch, 'QA isolation patch');

assert.doesNotMatch(importGuardPatch, /^\+.*\bCHECK(?:_[A-Z]+)?\(/m,
                    'import WebUI must reject bad input without crashing');
assert.match(importGuardPatch, /FindBool[\s\S]*value_or\(false\)/);
assert.match(importGuardPatch,
             /import-data-status-changed[\s\S]*kImportStatusFailed/);
assert.match(importGuardPatch, /!importer_list_loaded_\s*\|\|\s*!importer_list_/);
assert.match(chromeImporterGuardPatch, /entry\.Set\("id", "Default"\)/);
assert.match(chromeImporterGuardPatch, /base::StringToInt64/);
assert.match(chromeImporterGuardPatch, /if \(!bookmark_url\.is_valid\(\)\)/);
assert.doesNotMatch(chromeImporterGuardPatch, /^\+.*std::stoll/m,
                    'malformed bookmark timestamps must not throw');
assert.match(focusImportRenamePatch, /FocusImportDataHandler/);
assert.match(focusImportRenamePatch, /FocusImporterObserver/);
assert.match(focusImportRenamePatch,
             /rename to chrome\/browser\/ui\/webui\/settings\/focus_import_data_handler\.cc/);
assert.match(focusImportRenamePatch,
             /rename to chrome\/browser\/ui\/webui\/settings\/focus_importer_observer\.cc/);
for (const path of [
  'focus_external_process_importer_client.cc',
  'focus_external_process_importer_host.cc',
  'focus_in_process_importer_bridge.cc',
  'focus_external_process_importer_bridge.cc',
  'focus_profile_import_impl.cc',
  'focus_full_disk_access_confirm_dialog_delegate_mac.mm',
]) {
  assert.match(focusImportInternalsPatch, new RegExp(`rename to .*${path}`),
               `internal importer rename is missing: ${path}`);
}
assert.match(focusImportInternalsPatch, /FocusExternalProcessImporterHost/);
assert.match(focusImportInternalsPatch, /FocusProfileImportImpl/);
assert.match(focusImportInternalsPatch,
             /kSearchPromotionStoreUrl[\s\S]*^\+\s+""};/m);
assert.match(settingsBuildOverride, /focus_import_data_handler\.cc/);
assert.match(settingsBuildOverride, /focus_importer_observer\.cc/);
assert.match(settingsBuildOverride,
             /focus_full_disk_access_confirm_dialog_delegate_mac\.mm/);
assert.doesNotMatch(settingsBuildOverride,
                    /brave_(?:import_data_handler|importer_observer)/i);
assert.match(settingsUiOverride, /make_unique<FocusImportDataHandler>\(\)/);
assert.doesNotMatch(settingsUiOverride,
                    /BraveImportDataHandler|brave_import_data_handler/);
assert.doesNotMatch(generatedResourcesOverride, /IDS_SEARCH_PROMOTION_IPH_/);
assert.doesNotMatch(defaultBrowserHandler, /CHECK_EQ\(args\.size\(\),\s*1U\)/);
assert.match(defaultBrowserHandler, /if \(args\.size\(\) != 1u \|\| !args\[0\]\.is_string\(\)\)/);
assert.match(defaultBrowserHandler, /if \(!manager\)/);

const webUiRoots = [{label: 'override', root: overrideRoot}];
if (fs.existsSync(activeRoot)) {
  webUiRoots.push({label: 'active', root: activeRoot});
}

for (const {label, root} of webUiRoots) {
  const route = read(root, routeRelative);
  const menu = read(root, menuRelative);
  const proxy = read(root, proxyRelative);
  const people = read(root, peopleRelative);
  const peopleIndex = read(root, peopleIndexRelative);

  const routePath = route.match(
      /r\.IMPORT_DATA\s*=\s*r\.PEOPLE\.createChild\(['"]([^'"]+)['"]\)/)?.[1];
  const menuPath = menu.match(
      /id="importData"\s+href="([^"]+)"/)?.[1];

  assert.equal(routePath, '/importData', `${label}: wrong IMPORT_DATA route`);
  assert.equal(menuPath, routePath,
               `${label}: sidebar href does not resolve to IMPORT_DATA`);
  assert.doesNotMatch(menu, /href="\/people\/importData"/,
                      `${label}: stale, non-existent import route`);
  assert.match(route, /r\.IMPORT_DATA\.isNavigableDialog\s*=\s*true/);
  assert.match(people, /getCurrentRoute\(\)\s*===\s*routes\.IMPORT_DATA/);
  assert.match(peopleIndex, /case routes\.IMPORT_DATA:/);
  assert.match(proxy, /extensions:\s*boolean;/,
               `${label}: BrowserProfile must match backend fields`);
}

if (fs.existsSync(activeRoot)) {
  const settingsUi = read(
      activeRoot, 'chrome/browser/ui/webui/settings/settings_ui.cc');
  const handler = read(
      activeRoot, 'chrome/browser/ui/webui/settings/import_data_handler.cc');
  const importerList = read(
      activeRoot, 'chrome/browser/importer/importer_list.cc');
  const chromeList = read(
      activeRoot, 'chrome/browser/importer/chrome_importer_list.cc');
  const chromeUtils = read(
      activeRoot, 'chrome/common/importer/chrome_importer_utils.cc');
  const chromeImporter = read(
      activeRoot, 'chrome/utility/importer/chrome_importer.cc');

  assert.match(settingsUi, /make_unique<FocusImportDataHandler>\(\)/);
  assert.match(handler, /"initializeImportDialog"/);
  assert.match(handler, /"importData"/);
  assert.match(importerList, /DetectChromeProfiles\(&profiles\)/);
  assert.match(chromeList, /GetChromeUserDataFolder\(\)/);
  assert.match(chromeList, /chrome\.profile\s*=\s*base::UTF8ToUTF16\(\*name\)/);
  assertImportQaOverrideContract(chromeList, 'active importer list');
  assertImportQaOuterIsolation(
      `${chromeList}\n${importerList}`, 'active importer worker');

  // Chrome profile data is advertised only when the corresponding files and
  // importer implementation exist. Password/autofill are deliberately masked
  // until a safe decrypting importer exists; showing a checkbox that silently
  // imports nothing would be a false success.
  assert.match(chromeUtils,
               /PathExists\(bookmarks\)[\s\S]*user_data_importer::FAVORITES/);
  assert.match(chromeUtils,
               /PathExists\(history\)[\s\S]*user_data_importer::HISTORY/);
  assert.match(importerList,
               /services_supported\s*&=\s*~\(user_data_importer::PASSWORDS[\s\S]*user_data_importer::AUTOFILL_FORM_DATA\)/);
  assert.match(chromeImporter,
               /items\s*&\s*user_data_importer::HISTORY[\s\S]*ImportHistory\(\)/);
  assert.match(chromeImporter,
               /items\s*&\s*user_data_importer::FAVORITES[\s\S]*ImportBookmarks\(\)/);
  assert.doesNotMatch(chromeImporter, /std::stoll/);
  assert.match(chromeImporter, /base::StringToInt64/);
  assert.match(chromeImporter, /if \(!bookmark_url\.is_valid\(\)\)/);
  assert.match(chromeUtils, /entry\.Set\("id", "Default"\)/);

  for (const field of [
    'history', 'favorites', 'passwords', 'search', 'autofillFormData',
    'extensions',
  ]) {
    assert.match(handler, new RegExp(
        `browser_profile\\.Set\\(\\s*"${field}"`),
                 `backend profile field missing: ${field}`);
  }
}

console.log('Settings import-data route and backend contract verified.');
