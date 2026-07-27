#!/usr/bin/env node

// End-to-end QA for Chrome bookmark import. Both the source Chrome profile
// and the destination Focus profile are created under a uniquely owned
// temporary root and are removed after the report is written. The test build
// must support FOCUS_IMPORT_QA_CHROME_USER_DATA_DIR as a source-root override.
// The harness verifies both the active source contract and a marker embedded
// in the sibling chrome.dll before it launches anything; otherwise it fails
// closed without allowing the importer to inspect a real browser profile.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';

const projectRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)), '..');

const executableArgument = process.argv[2];
assert.ok(executableArgument,
          'Usage: node qa/verify_settings_import_real_runtime.mjs ' +
          '<chrome.exe> [report.json]');

const executablePath = path.resolve(executableArgument);
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;
assert.ok(fs.statSync(executablePath).isFile(),
          'Focus Browser executable is missing: ' + executablePath);

const qaHookBuildMarker = 'FOCUS_IMPORT_QA_HOOK_V3';
const chromeLibraryPath = path.join(path.dirname(executablePath), 'chrome.dll');
assert.ok(fs.existsSync(chromeLibraryPath) &&
              fs.statSync(chromeLibraryPath).isFile(),
          'Refusing to run: sibling chrome.dll is missing');

function fileContainsAsciiToken(filePath, token) {
  const needle = Buffer.from(token, 'ascii');
  const chunk = Buffer.allocUnsafe(1024 * 1024);
  let carry = Buffer.alloc(0);
  const descriptor = fs.openSync(filePath, 'r');
  try {
    while (true) {
      const bytesRead = fs.readSync(
          descriptor, chunk, 0, chunk.length, null);
      if (bytesRead === 0) return false;
      const window = Buffer.concat([carry, chunk.subarray(0, bytesRead)]);
      if (window.includes(needle)) return true;
      const overlap = Math.min(needle.length - 1, window.length);
      carry = Buffer.from(window.subarray(window.length - overlap));
    }
  } finally {
    fs.closeSync(descriptor);
  }
}

assert.ok(fileContainsAsciiToken(chromeLibraryPath, qaHookBuildMarker), [
  `Refusing to run: ${qaHookBuildMarker} is absent from chrome.dll.`,
  'The executable may be stale and could inspect a real browser profile.',
].join(' '));

const activeSourceRoot = path.resolve(
    process.env.FOCUS_ACTIVE_SOURCE_ROOT ||
    path.join(projectRoot, 'build', 'src'));
const qaHookName = 'FOCUS_IMPORT_QA_CHROME_USER_DATA_DIR';
const importerSourceRoots = [
  path.join(activeSourceRoot, 'chrome', 'browser', 'importer'),
  path.join(activeSourceRoot, 'chrome', 'common', 'importer'),
  path.join(activeSourceRoot, 'chrome', 'utility', 'importer'),
];

function sourceFilesBelow(root) {
  if (!fs.existsSync(root)) return [];
  const files = [];
  const pending = [root];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, {withFileTypes: true})) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(entryPath);
      } else if (/\.(?:cc|h|mm)$/.test(entry.name)) {
        files.push(entryPath);
      }
    }
  }
  return files;
}

const qaHookSources = importerSourceRoots
    .flatMap(sourceFilesBelow)
    .filter(sourcePath =>
      fs.readFileSync(sourcePath, 'utf8').includes(qaHookName));
assert.ok(qaHookSources.length > 0, [
  `Refusing to run: ${qaHookName} is not implemented in the active source`,
  `tree (${activeSourceRoot}). Running without this hook could inspect the`,
  'user\'s real browser profile. Build an explicit QA-only hook first.',
].join(' '));

const qaHookContract = qaHookSources
    .map(sourcePath => fs.readFileSync(sourcePath, 'utf8'))
    .concat(fs.readFileSync(
        path.join(activeSourceRoot, 'chrome', 'browser', 'importer',
                  'importer_list.cc'),
        'utf8'))
    .join('\n');
for (const token of [
  'FocusImportQaOverrideState',
  qaHookBuildMarker,
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
  assert.ok(qaHookContract.includes(token),
            `Refusing to run: QA source hook is missing ${token}`);
}
assert.match(
    qaHookContract,
    /HasSwitch\(switches::kUserDataDir\)[\s\S]*DirectoryExists\(requested_source\)[\s\S]*DirectoryExists\(requested_target\)/,
    'Refusing to run: QA source/destination ownership guards are incomplete');
assert.match(
    qaHookContract,
    /qa_override\.state != FocusImportQaOverrideState::kUnset[\s\S]*qa_override\.state == FocusImportQaOverrideState::kValid[\s\S]*AddChromeToProfiles\([\s\S]*return;/,
    'Refusing to run: invalid QA overrides do not fail closed');
const isolatedDiscoveryBranch = qaHookContract.match(
    /#if BUILDFLAG\(IS_WIN\)\s+if \(FocusImportQaOverrideIsPresent\(\)\) \{([\s\S]*?)\}\s+else if \(shell_integration::IsFirefoxDefaultBrowser\(\)\)/)?.[1];
assert.ok(isolatedDiscoveryBranch,
          'Refusing to run: outer QA profile discovery isolation is missing');
assert.match(
    qaHookContract,
    /bool FocusImportQaOverrideIsPresent\(\) \{\s*return base::Environment::Create\(\)->HasVar\(\s*kFocusImportQaChromeUserDataDir\);\s*\}/,
    'Refusing to run: exact QA environment-presence guard is missing');
assert.match(isolatedDiscoveryBranch, /DetectChromeProfiles\(&profiles\);/);
assert.doesNotMatch(
    isolatedDiscoveryBranch,
    /Detect(?:Firefox|Zen|BuiltinWindows|IE|Edge|Safari)Profiles|Is(?:Firefox|IE)DefaultBrowser|Get(?:Firefox|Zen)Details|EdgeImporterCanImport/,
    'Refusing to run: QA isolation can inspect a non-disposable profile');

const ownedRoot = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-real-import-qa-'));
const chromeUserDataDir = path.join(ownedRoot, 'chrome-source', 'User Data');
const chromeProfileDir = path.join(chromeUserDataDir, 'Default');
const escapedProfileId = '..\\..\\escaped-profile';
const escapedProfileDir = path.join(ownedRoot, 'escaped-profile');
const focusUserDataDir = path.join(ownedRoot, 'focus-target');
const portFile = path.join(focusUserDataDir, 'DevToolsActivePort');
fs.mkdirSync(chromeProfileDir, {recursive: true});
fs.mkdirSync(escapedProfileDir, {recursive: true});
fs.mkdirSync(focusUserDataDir, {recursive: true});

const runId = randomUUID();
const urls = {
  first: `https://focus-import-qa.invalid/${runId}/first`,
  mixed: `https://focus-import-qa.invalid/${runId}/mixed`,
  retry: `https://focus-import-qa.invalid/${runId}/retry`,
  escaped: `https://focus-import-qa.invalid/${runId}/escaped-profile`,
  invalid: 'not a valid bookmark url',
};

const windowsEpochMicros = '13366012800000000';
const bookmarkNode = (name, url, overrides = {}) => ({
  date_added: windowsEpochMicros,
  guid: randomUUID(),
  id: String(Math.floor(Math.random() * 1000000) + 10),
  name,
  type: 'url',
  url,
  ...overrides,
});

function writeLocalState() {
  fs.writeFileSync(path.join(chromeUserDataDir, 'Local State'), JSON.stringify({
    profile: {
      last_active_profiles: ['Default', escapedProfileId],
      info_cache: {
        Default: {
          active_time: 1,
          avatar_icon: 'chrome://theme/IDR_PROFILE_AVATAR_0',
          name: 'Focus QA Source',
        },
        [escapedProfileId]: {
          active_time: 1,
          avatar_icon: 'chrome://theme/IDR_PROFILE_AVATAR_0',
          name: 'Traversal QA Source',
        },
      },
    },
  }), 'utf8');
}

function writeBookmarks(children, profileDirectory = chromeProfileDir) {
  const folder = (id, name, items) => ({
    children: items,
    date_added: windowsEpochMicros,
    date_modified: '0',
    guid: randomUUID(),
    id,
    name,
    type: 'folder',
  });
  const document = {
    checksum: '',
    roots: {
      bookmark_bar: folder('1', 'Bookmarks bar', children),
      other: folder('2', 'Other bookmarks', []),
      synced: folder('3', 'Mobile bookmarks', []),
    },
    version: 1,
  };
  fs.writeFileSync(
      path.join(profileDirectory, 'Bookmarks'),
      JSON.stringify(document, null, 2), 'utf8');
}

writeLocalState();
writeBookmarks([bookmarkNode('QA first import', urls.first)]);
writeBookmarks(
    [bookmarkNode('Traversal must stay hidden', urls.escaped)],
    escapedProfileDir);

const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

const browser = spawn(executablePath, [
  '--headless=new',
  '--disable-gpu',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-sync',
  '--disable-search-engine-choice-screen',
  '--no-first-run',
  '--no-default-browser-check',
  '--noerrdialogs',
  '--remote-debugging-port=0',
  '--remote-allow-origins=*',
  '--window-size=1400,900',
  '--force-device-scale-factor=1',
  '--user-data-dir=' + focusUserDataDir,
  'about:blank',
], {
  cwd: path.dirname(executablePath),
  env: {
    ...process.env,
    FOCUS_IMPORT_QA_CHROME_USER_DATA_DIR: chromeUserDataDir,
  },
  stdio: 'ignore',
  windowsHide: true,
});

class CdpSession {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.socket = null;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.webSocketUrl);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, {once: true});
      this.socket.addEventListener('error', reject, {once: true});
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) {
          return;
        }
        this.pending.delete(message.id);
        if (message.error) {
          pending.reject(new Error(message.error.message));
        } else {
          pending.resolve(message.result || {});
        }
        return;
      }
      for (const listener of this.listeners.get(message.method) || []) {
        listener(message.params || {});
      }
    });
    this.socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        pending.reject(new Error('DevTools WebSocket closed'));
      }
      this.pending.clear();
    });
  }

  send(method, params = {}) {
    assert.equal(this.socket?.readyState, WebSocket.OPEN,
                 'DevTools WebSocket is not open');
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  on(method, listener) {
    const entries = this.listeners.get(method) || [];
    entries.push(listener);
    this.listeners.set(method, entries);
  }

  close() {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.close();
    }
  }
}

async function waitForPort() {
  const deadline = Date.now() + 45000;
  while (Date.now() < deadline) {
    if (fs.existsSync(portFile)) {
      const port = Number(
          fs.readFileSync(portFile, 'utf8').split(/\r?\n/, 1)[0]);
      if (Number.isInteger(port) && port > 0) {
        return port;
      }
    }
    if (browser.exitCode !== null) {
      throw new Error('Focus Browser exited during startup: ' +
                      browser.exitCode);
    }
    await delay(50);
  }
  throw new Error('Timed out waiting for DevToolsActivePort');
}

async function evaluate(session, expression) {
  const response = await session.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(
        response.exceptionDetails.exception?.description ||
        response.exceptionDetails.text || 'Runtime evaluation failed');
  }
  return response.result?.value;
}

async function waitForValue(session, expression, description,
                            timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    assert.equal(browser.exitCode, null,
                 'Browser exited while waiting for ' + description);
    try {
      const value = await evaluate(session, expression);
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(100);
  }
  throw new Error(
      'Timed out waiting for ' + description +
      (lastError ? ': ' + lastError.message : ''));
}

const dialogProbeSource = `(() => {
  const visit = root => {
    for (const element of root.querySelectorAll('*')) {
      if (element.localName === 'settings-import-data-dialog') return element;
      if (element.shadowRoot) {
        const found = visit(element.shadowRoot);
        if (found) return found;
      }
    }
    return null;
  };
  const dialog = visit(document);
  if (!dialog || !Array.isArray(dialog.browserProfiles_)) return null;
  const source = dialog.browserProfiles_.find(profile =>
    profile.name === 'Google Chrome' &&
    profile.profileName === 'Focus QA Source');
  if (!source || !source.favorites) return null;
  return {
    sourceIndex: source.index,
    sourceName: source.name,
    profileName: source.profileName,
    favorites: source.favorites,
    status: dialog.importStatus_,
    profiles: dialog.browserProfiles_.map(profile => ({
      name: profile.name,
      profileName: profile.profileName ?? '',
      history: Boolean(profile.history),
      favorites: Boolean(profile.favorites),
      passwords: Boolean(profile.passwords),
      search: Boolean(profile.search),
      autofillFormData: Boolean(profile.autofillFormData),
      extensions: Boolean(profile.extensions),
    })),
  };
})()`;

const statusProbeSource = terminalStatus => `(() =>
  window.__focusImportQaStatuses?.includes(
      ${JSON.stringify(terminalStatus)}) ?
    ${JSON.stringify(terminalStatus)} : null
)()`;

async function openImportDialog(page) {
  await page.send('Page.navigate', {url: 'chrome://settings/importData'});
  const source = await waitForValue(
      page, dialogProbeSource, 'the isolated Chrome source profile');
  assert.equal(source.profiles.length, 2,
               'QA import dialog exposed an unexpected real browser profile');
  const chromeSources = source.profiles.filter(profile =>
    profile.name === 'Google Chrome' &&
    profile.profileName === 'Focus QA Source');
  assert.equal(chromeSources.length, 1,
               'QA import dialog must expose exactly one disposable Chrome source');
  const interactiveSources = source.profiles.filter(profile =>
    profile.profileName === '' && profile.favorites &&
    !profile.history && !profile.passwords && !profile.search &&
    !profile.autofillFormData && !profile.extensions);
  assert.equal(interactiveSources.length, 1,
               'QA import dialog must expose exactly one bookmark-file source');
  assert.equal(
      source.profiles.some(
          profile => profile.profileName === 'Traversal QA Source'), false,
      'A traversal profile escaped the disposable Chrome source root');
  await evaluate(page, `(async () => {
    window.__focusImportQaStatuses = [];
    const {addWebUiListener} = await import('chrome://resources/js/cr.js');
    addWebUiListener('import-data-status-changed', status => {
      window.__focusImportQaStatuses.push(status);
    });
    return true;
  })()`);
  return source;
}

async function assertBrowserAlive(browserControl, expectedPid, label) {
  const processInfo = await browserControl.send('SystemInfo.getProcessInfo');
  const actualPid = processInfo.processInfo.find(
      process => process.type === 'browser')?.id || 0;
  assert.equal(actualPid, expectedPid, label + ': browser process changed');
}

async function sendMalformedImport(page, browserControl, browserPid, label) {
  await openImportDialog(page);
  await evaluate(page, `(() => {
    chrome.send('importData', ['not-an-index']);
    return true;
  })()`);
  const status = await waitForValue(
      page, statusProbeSource('failed'), label + ' failure status');
  assert.equal(status, 'failed');
  await assertBrowserAlive(browserControl, browserPid, label);
  return status;
}

async function importBookmarks(page, browserControl, browserPid, label) {
  const source = await openImportDialog(page);
  const startResult = await evaluate(page, `(() => {
    chrome.send('importData', [${Number(source.sourceIndex)}, {
      import_dialog_autofill_form_data: false,
      import_dialog_bookmarks: true,
      import_dialog_extensions: false,
      import_dialog_history: false,
      import_dialog_saved_passwords: false,
      import_dialog_search_engine: false,
    }]);
    return true;
  })()`);
  assert.equal(startResult, true);
  const status = await waitForValue(
      page, statusProbeSource('succeeded'), label + ' success status', 90000);
  assert.equal(status, 'succeeded');
  await assertBrowserAlive(browserControl, browserPid, label);
  return {source, status};
}

function collectBookmarkUrls(value, result = []) {
  if (Array.isArray(value)) {
    for (const entry of value) collectBookmarkUrls(entry, result);
  } else if (value && typeof value === 'object') {
    if (typeof value.url === 'string') result.push(value.url);
    for (const child of Object.values(value)) {
      collectBookmarkUrls(child, result);
    }
  }
  return result;
}

const runtimeExceptions = [];
const report = {
  status: 'RUNNING',
  executable: executablePath,
  isolation: {
    source: 'unique disposable Chrome profile',
    destination: 'unique disposable Focus profile',
    ownedRoot,
    userProfilesAccessed: false,
  },
  runId,
  scenarios: [],
  runtimeExceptions,
};

let browserControl = null;
let page = null;
let browserPid = 0;
let primaryError = null;

function persistReport() {
  if (!reportPath) return;
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n');
}

try {
  const port = await waitForPort();
  const version = await (await fetch(
      `http://127.0.0.1:${port}/json/version`)).json();
  browserControl = new CdpSession(version.webSocketDebuggerUrl);
  await browserControl.connect();
  const processInfo = await browserControl.send('SystemInfo.getProcessInfo');
  browserPid = processInfo.processInfo.find(
      process => process.type === 'browser')?.id || 0;
  assert.ok(browserPid > 0, 'Could not resolve the QA browser PID');

  const targets = await (await fetch(
      `http://127.0.0.1:${port}/json/list`)).json();
  const pageTarget = targets.find(target => target.type === 'page');
  assert.ok(pageTarget?.webSocketDebuggerUrl, 'No page target exists');
  page = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await page.connect();
  page.on('Runtime.exceptionThrown', event => runtimeExceptions.push(
      event.exceptionDetails?.exception?.description ||
      event.exceptionDetails?.text || 'Unknown runtime exception'));
  await page.send('Page.enable');
  await page.send('Runtime.enable');

  report.scenarios.push({
    name: 'malformed request before first import',
    status: await sendMalformedImport(
        page, browserControl, browserPid, 'initial malformed request'),
    browserAlive: true,
  });

  report.scenarios.push({
    name: 'valid bookmark import after an error',
    ...(await importBookmarks(
        page, browserControl, browserPid, 'first valid import')),
    browserAlive: true,
  });

  writeBookmarks([
    'damaged non-object child',
    {type: 'url', name: 'Missing URL'},
    bookmarkNode('Invalid URL', urls.invalid),
    bookmarkNode('Broken date but valid URL', urls.mixed, {
      date_added: 'not-a-number',
    }),
    {
      type: 'folder',
      name: 'Malformed folder',
      date_added: '-9223372036854775809',
      children: [null, 7, {type: 'url', name: 'Nested missing URL'}],
    },
  ]);
  report.scenarios.push({
    name: 'mixed valid and damaged bookmark nodes after success',
    ...(await importBookmarks(
        page, browserControl, browserPid, 'mixed damaged import')),
    browserAlive: true,
  });

  report.scenarios.push({
    name: 'malformed request after successful imports',
    status: await sendMalformedImport(
        page, browserControl, browserPid, 'repeated malformed request'),
    browserAlive: true,
  });

  writeBookmarks([bookmarkNode('QA retry import', urls.retry)]);
  report.scenarios.push({
    name: 'valid bookmark import after repeated error',
    ...(await importBookmarks(
        page, browserControl, browserPid, 'retry valid import')),
    browserAlive: true,
  });

  assert.deepEqual(runtimeExceptions, [],
                   'Renderer exceptions occurred during import QA');
  report.browserAliveAfterAllImports = true;
} catch (error) {
  primaryError = error;
  report.status = 'FAIL';
  report.error = error.stack || String(error);
} finally {
  if (browserControl?.socket?.readyState === WebSocket.OPEN) {
    try {
      await Promise.race([browserControl.send('Browser.close'), delay(3000)]);
    } catch {
      // The exact-PID fallback below owns cleanup.
    }
  }
  page?.close();
  browserControl?.close();
  const gracefulDeadline = Date.now() + 15000;
  while (browser.exitCode === null && Date.now() < gracefulDeadline) {
    await delay(100);
  }
  report.gracefulShutdown = browser.exitCode !== null;

  const ownedPids = new Set([browserPid, browser.pid].filter(pid => pid > 0));
  for (const pid of ownedPids) {
    try {
      process.kill(pid, 0);
    } catch {
      continue;
    }
    spawnSync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], {
      stdio: 'ignore',
      windowsHide: true,
    });
  }

  try {
    const destinationBookmarksPath =
        path.join(focusUserDataDir, 'Default', 'Bookmarks');
    assert.ok(fs.existsSync(destinationBookmarksPath),
              'Focus destination Bookmarks file was not created');
    const destinationBookmarks = JSON.parse(
        fs.readFileSync(destinationBookmarksPath, 'utf8'));
    const importedUrls = collectBookmarkUrls(destinationBookmarks);
    report.result = {
      destinationBookmarksCreated: true,
      importedUrls: importedUrls.filter(url => url.includes(runId)),
      expectedValidUrls: [urls.first, urls.mixed, urls.retry],
      invalidUrlWasImported: importedUrls.includes(urls.invalid),
      escapedProfileWasImported: importedUrls.includes(urls.escaped),
    };
    assert.ok(importedUrls.includes(urls.first),
              'First valid bookmark did not reach the Focus profile');
    assert.ok(importedUrls.includes(urls.mixed),
              'Valid bookmark beside damaged nodes was not imported');
    assert.ok(importedUrls.includes(urls.retry),
              'Retry bookmark did not reach the Focus profile');
    assert.equal(importedUrls.includes(urls.invalid), false,
                 'Invalid bookmark URL was imported');
    assert.equal(importedUrls.includes(urls.escaped), false,
                 'A traversal profile bookmark was imported');
  } catch (error) {
    if (!primaryError) primaryError = error;
    report.status = 'FAIL';
    report.error = primaryError.stack || String(primaryError);
  }

  const tempRoot = path.resolve(os.tmpdir());
  const resolvedOwnedRoot = path.resolve(ownedRoot);
  const relativeOwnedRoot = path.relative(tempRoot, resolvedOwnedRoot);
  if (path.basename(resolvedOwnedRoot).startsWith('focus-real-import-qa-') &&
      relativeOwnedRoot && !relativeOwnedRoot.startsWith('..') &&
      !path.isAbsolute(relativeOwnedRoot)) {
    fs.rmSync(resolvedOwnedRoot, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
    report.isolation.cleaned = true;
  }
}

if (!primaryError) report.status = 'PASS';
persistReport();

if (primaryError) {
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify(report, null, 2));
}
