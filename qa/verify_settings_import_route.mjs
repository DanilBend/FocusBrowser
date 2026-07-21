// Copyright 2026 The Focus Browser Authors
// Static contract checks for the Settings import-data route and backend.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const overrideRoot = path.join(repoRoot, 'source_overrides');
const activeRoot = path.join(repoRoot, 'build', 'src');

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

  assert.match(settingsUi, /make_unique<BraveImportDataHandler>\(\)/);
  assert.match(handler, /"initializeImportDialog"/);
  assert.match(handler, /"importData"/);
  assert.match(importerList, /DetectChromeProfiles\(&profiles\)/);
  assert.match(chromeList, /GetChromeUserDataFolder\(\)/);
  assert.match(chromeList, /chrome\.profile\s*=\s*base::UTF8ToUTF16\(\*name\)/);

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
