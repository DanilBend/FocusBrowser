#!/usr/bin/env node

// Runtime-only QA for the Settings import-data route. The script launches
// exactly the Focus Browser executable supplied by the caller, uses a unique
// disposable profile, and never presses the import button.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const chromeArgument = process.argv[2];
assert.ok(chromeArgument,
          'Usage: node qa/verify_settings_import_runtime.mjs ' +
          '<chrome.exe> [report.json]');

const chromePath = path.resolve(chromeArgument);
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;
assert.ok(fs.existsSync(chromePath),
          'Focus Browser executable does not exist: ' + chromePath);
assert.ok(fs.statSync(chromePath).isFile(),
          'Focus Browser executable is not a file: ' + chromePath);

const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-settings-import-qa-'));
const portFile = path.join(profileDir, 'DevToolsActivePort');
const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

const browser = spawn(chromePath, [
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
  '--window-size=1600,1000',
  '--force-device-scale-factor=1',
  '--user-data-dir=' + profileDir,
  'about:blank',
], {
  cwd: path.dirname(chromePath),
  stdio: 'ignore',
  windowsHide: true,
});

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
      throw new Error(
          'Focus Browser exited during startup with code ' +
          browser.exitCode);
    }
    await delay(50);
  }
  throw new Error('Timed out waiting for DevToolsActivePort');
}

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
          pending.reject(
              new Error(message.error.code + ': ' + message.error.message));
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
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('DevTools WebSocket is not open'));
    }
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

async function evaluate(session, expression) {
  const response = await session.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(
        response.exceptionDetails.exception?.description ||
        response.exceptionDetails.text ||
        'Runtime evaluation failed');
  }
  return response.result?.value;
}

async function waitForValue(session, expression, description,
                            timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (browser.exitCode !== null) {
      throw new Error(
          'Focus Browser exited while waiting for ' + description +
          ' (code ' + browser.exitCode + ')');
    }
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

function pageReadyProbe() {
  const deepAll = () => {
    const elements = [];
    const visit = root => {
      for (const element of root.querySelectorAll('*')) {
        elements.push(element);
        if (element.shadowRoot) {
          visit(element.shadowRoot);
        }
      }
    };
    visit(document);
    return elements;
  };
  const elements = deepAll();
  const importDialog = elements.find(
      element => element.localName === 'settings-import-data-dialog');
  const sourceSelect = importDialog?.shadowRoot?.querySelector(
      '#browserSelect');
  const crDialog = importDialog?.shadowRoot?.querySelector('#dialog');
  return location.pathname === '/importData' &&
      document.readyState === 'complete' &&
      Array.isArray(importDialog?.browserProfiles_) &&
      importDialog.browserProfiles_.length > 0 &&
      sourceSelect?.options.length === importDialog.browserProfiles_.length &&
      crDialog?.open === true;
}

async function probeImportPage() {
  const deepAll = () => {
    const elements = [];
    const visit = root => {
      for (const element of root.querySelectorAll('*')) {
        elements.push(element);
        if (element.shadowRoot) {
          visit(element.shadowRoot);
        }
      }
    };
    visit(document);
    return elements;
  };
  const visible = element => {
    if (!element || element.hidden) {
      return false;
    }
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
        rect.width > 0 && rect.height > 0;
  };
  const elements = deepAll();
  const importLink = elements.find(element => element.id === 'importData');
  const appearanceLink = elements.find(element => element.id === 'appearance');
  const menu = importLink?.parentElement;
  const importDialog = elements.find(
      element => element.localName === 'settings-import-data-dialog');
  const shadow = importDialog?.shadowRoot;
  const crDialog = shadow?.querySelector('#dialog');
  const nativeDialog = crDialog?.getNative?.() ||
      crDialog?.shadowRoot?.querySelector('dialog');
  const sourceSelect = shadow?.querySelector('#browserSelect');
  const titleElement = shadow?.querySelector('[slot="title"]');
  const bodyElement = shadow?.querySelector('[slot="body"]');
  const importButton = shadow?.querySelector('#import');
  const cancelButton = shadow?.querySelector('#cancel');
  if (!importLink || !appearanceLink || !menu || !importDialog || !shadow ||
      !crDialog || !nativeDialog || !sourceSelect || !titleElement ||
      !bodyElement || !importButton || !cancelButton) {
    throw new Error('Settings import UI disappeared during inspection');
  }

  // Chromium exposes the public Settings module through settings.js; route.js
  // and router.js are implementation resources and are not independently
  // addressable in a release build.
  const settingsModule = await import('chrome://settings/settings.js');
  const currentRoute = settingsModule.Router.getInstance().getCurrentRoute();
  const route = {
    pathname: location.pathname,
    href: location.href,
    currentPath: currentRoute?.path || '',
    importDataPath: settingsModule.routes.IMPORT_DATA?.path || '',
    isImportDataObject: currentRoute === settingsModule.routes.IMPORT_DATA,
  };

  const profiles = importDialog.browserProfiles_;
  const originalIndex = sourceSelect.selectedIndex;
  const sourceStates = [];
  for (let index = 0; index < profiles.length; ++index) {
    sourceSelect.selectedIndex = index;
    sourceSelect.dispatchEvent(new Event('change', {
      bubbles: true,
      composed: true,
    }));
    await new Promise(resolve => requestAnimationFrame(
        () => requestAnimationFrame(resolve)));

    const selectedProfile = importDialog.selected_ || profiles[index];
    const historyControl = shadow.querySelector('#importDialogHistory');
    const bookmarksControl = shadow.querySelector(
        '#importDialogBookmarks');
    sourceStates.push({
      optionIndex: index,
      optionValue: sourceSelect.options[index]?.value || '',
      optionText: sourceSelect.options[index]?.textContent?.trim() || '',
      selectedProfile: {
        name: String(selectedProfile?.name || ''),
        index: Number(selectedProfile?.index),
        profileName: String(selectedProfile?.profileName || ''),
        history: Boolean(selectedProfile?.history),
        favorites: Boolean(selectedProfile?.favorites),
        passwords: Boolean(selectedProfile?.passwords),
        search: Boolean(selectedProfile?.search),
        autofillFormData: Boolean(selectedProfile?.autofillFormData),
        extensions: Boolean(selectedProfile?.extensions),
      },
      controls: {
        history: {
          present: Boolean(historyControl),
          hidden: Boolean(historyControl?.hidden),
          visible: visible(historyControl),
          prefKey: historyControl?.pref?.key || '',
        },
        bookmarks: {
          present: Boolean(bookmarksControl),
          hidden: Boolean(bookmarksControl?.hidden),
          visible: visible(bookmarksControl),
          prefKey: bookmarksControl?.pref?.key || '',
        },
      },
    });
  }
  sourceSelect.selectedIndex = originalIndex;
  sourceSelect.dispatchEvent(new Event('change', {
    bubbles: true,
    composed: true,
  }));
  await new Promise(resolve => requestAnimationFrame(resolve));

  const nativeRect = nativeDialog.getBoundingClientRect();
  return {
    readyState: document.readyState,
    title: document.title,
    bodyTextLength: (document.body?.innerText || '').trim().length,
    route,
    menu: {
      selectedPath: String(menu.selected || ''),
      importHref: importLink.getAttribute('href') || '',
      importSelected: importLink.hasAttribute('selected'),
      importVisible: visible(importLink),
      appearanceHref: appearanceLink.getAttribute('href') || '',
      appearanceSelected: appearanceLink.hasAttribute('selected'),
    },
    dialog: {
      hostConnected: importDialog.isConnected,
      customElementDefined:
          customElements.get('settings-import-data-dialog') !== undefined,
      open: Boolean(crDialog.open && nativeDialog.open),
      visible: visible(nativeDialog),
      width: nativeRect.width,
      height: nativeRect.height,
      title: titleElement.innerText.trim(),
      bodyTextLength: bodyElement.innerText.trim().length,
      importButtonPresent: Boolean(importButton),
      importButtonVisible: visible(importButton),
      cancelButtonPresent: Boolean(cancelButton),
      status: String(importDialog.importStatus_ || ''),
    },
    selector: {
      visible: visible(sourceSelect),
      disabled: sourceSelect.disabled,
      optionCount: sourceSelect.options.length,
      options: Array.from(sourceSelect.options, option => ({
        value: option.value,
        text: option.textContent?.trim() || '',
      })),
      profileCount: profiles.length,
    },
    sourceStates,
  };
}

const readyExpression = '(' + pageReadyProbe.toString() + ')()';
const probeExpression = '(' + probeImportPage.toString() + ')()';
const runtimeExceptions = [];
const report = {
  status: 'RUNNING',
  executable: chromePath,
  profileKind: 'unique-disposable',
  profileDir,
  targetUrl: 'chrome://settings/importData',
  realImportAttempted: false,
  cleanup: 'Browser.close, then exact owned PID tree fallback',
  runtimeExceptions,
};

let browserControl = null;
let page = null;
let browserPid = 0;
let primaryError = null;

function persistReport() {
  if (reportPath) {
    fs.mkdirSync(path.dirname(reportPath), {recursive: true});
    fs.writeFileSync(
        reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
  }
}

try {
  const port = await waitForPort();
  report.devToolsPort = port;

  const versionResponse = await fetch(
      'http://127.0.0.1:' + port + '/json/version');
  assert.equal(versionResponse.ok, true, 'DevTools version endpoint failed');
  const version = await versionResponse.json();
  browserControl = new CdpSession(version.webSocketDebuggerUrl);
  await browserControl.connect();

  const processInfo = await browserControl.send('SystemInfo.getProcessInfo');
  browserPid = processInfo.processInfo.find(
      process => process.type === 'browser')?.id || 0;
  assert.ok(Number.isInteger(browserPid) && browserPid > 0,
            'Could not resolve the exact QA browser PID');
  report.browserPid = browserPid;

  const targetsResponse = await fetch(
      'http://127.0.0.1:' + port + '/json/list');
  assert.equal(targetsResponse.ok, true, 'DevTools target list failed');
  const targets = await targetsResponse.json();
  const pageTarget = targets.find(target => target.type === 'page');
  assert.ok(pageTarget?.webSocketDebuggerUrl,
            'No debuggable QA page target was created');

  page = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await page.connect();
  page.on('Runtime.exceptionThrown', event => {
    runtimeExceptions.push(
        event.exceptionDetails?.exception?.description ||
        event.exceptionDetails?.text ||
        'Unknown runtime exception');
  });
  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: 1600,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await page.send('Page.navigate', {
    url: 'chrome://settings/importData',
  });

  await waitForValue(
      page, readyExpression,
      'the rendered Settings import-data dialog and backend source list');
  const snapshot = await evaluate(page, probeExpression);
  report.snapshot = snapshot;

  assert.equal(snapshot.readyState, 'complete');
  assert.ok(snapshot.title.length > 0, 'Settings page has no title');
  assert.equal(snapshot.route.pathname, '/importData');
  assert.equal(snapshot.route.currentPath, '/importData');
  assert.equal(snapshot.route.importDataPath, '/importData');
  assert.equal(snapshot.route.isImportDataObject, true,
               'Router current route is not routes.IMPORT_DATA');

  assert.equal(snapshot.menu.selectedPath, '/importData');
  assert.equal(snapshot.menu.importHref, '/importData');
  assert.equal(snapshot.menu.importSelected, true,
               'Import sidebar item is not selected');
  assert.equal(snapshot.menu.importVisible, true,
               'Import sidebar item is not visible');
  assert.equal(snapshot.menu.appearanceHref, '/appearance');
  assert.equal(snapshot.menu.appearanceSelected, false,
               'Appearance sidebar item is incorrectly selected');

  assert.equal(snapshot.dialog.hostConnected, true);
  assert.equal(snapshot.dialog.customElementDefined, true);
  assert.equal(snapshot.dialog.open, true, 'Import dialog is not open');
  assert.equal(snapshot.dialog.visible, true, 'Import dialog is not visible');
  assert.ok(snapshot.dialog.width > 100 && snapshot.dialog.height > 100,
            'Import dialog has no rendered surface');
  assert.ok(snapshot.dialog.title.length > 0,
            'Import dialog title is blank');
  assert.ok(snapshot.dialog.bodyTextLength > 0,
            'Import dialog body is blank');
  assert.equal(snapshot.dialog.importButtonPresent, true);
  assert.equal(snapshot.dialog.importButtonVisible, true);
  assert.equal(snapshot.dialog.cancelButtonPresent, true);
  assert.equal(snapshot.dialog.status, 'initial',
               'Import unexpectedly left its initial state');

  assert.equal(snapshot.selector.visible, true,
               'Import source selector is not visible');
  assert.equal(snapshot.selector.disabled, false,
               'Import source selector is disabled');
  assert.ok(snapshot.selector.optionCount > 0,
            'Import source selector has no options');
  assert.equal(snapshot.selector.optionCount, snapshot.selector.profileCount);
  assert.ok(snapshot.selector.options.every(option => option.text.length > 0),
            'Import source selector contains a blank option');
  assert.equal(snapshot.sourceStates.length, snapshot.selector.profileCount);

  for (const source of snapshot.sourceStates) {
    const label = source.optionText || source.selectedProfile.name ||
        String(source.optionIndex);
    assert.equal(source.controls.history.present, true,
                 label + ': history control is missing');
    assert.equal(source.controls.bookmarks.present, true,
                 label + ': bookmarks control is missing');
    assert.equal(source.controls.history.prefKey, 'import_dialog_history',
                 label + ': history pref contract changed');
    assert.equal(
        source.controls.bookmarks.prefKey, 'import_dialog_bookmarks',
        label + ': bookmarks pref contract changed');
    assert.equal(
        source.controls.history.visible, source.selectedProfile.history,
        label + ': history visibility differs from backend support');
    assert.equal(
        source.controls.history.hidden, !source.selectedProfile.history,
        label + ': history hidden state differs from backend support');
    assert.equal(
        source.controls.bookmarks.visible, source.selectedProfile.favorites,
        label + ': bookmarks visibility differs from backend support');
    assert.equal(
        source.controls.bookmarks.hidden, !source.selectedProfile.favorites,
        label + ': bookmarks hidden state differs from backend support');
  }

  const chromeProfiles = snapshot.sourceStates.filter(source =>
    /chrome/i.test(
        source.selectedProfile.name + ' ' +
        source.selectedProfile.profileName));
  report.chromeProfile = chromeProfiles.length > 0 ? {
    status: 'detected',
    count: chromeProfiles.length,
    profiles: chromeProfiles.map(source => ({
      name: source.selectedProfile.name,
      profileName: source.selectedProfile.profileName,
      historySupported: source.selectedProfile.history,
      bookmarksSupported: source.selectedProfile.favorites,
    })),
  } : {
    status: 'not-detected',
    count: 0,
    note: 'No Chrome profile was advertised by the backend; page ' +
        'functionality and the file-import fallback were still verified.',
  };

  const aliveInfo = await browserControl.send('SystemInfo.getProcessInfo');
  const alivePid = aliveInfo.processInfo.find(
      process => process.type === 'browser')?.id || 0;
  assert.equal(alivePid, browserPid,
               'QA browser process changed or exited during inspection');
  assert.deepEqual(runtimeExceptions, [],
                   'Settings runtime exceptions: ' +
                   runtimeExceptions.join('\n'));
  assert.equal(report.realImportAttempted, false);

  report.status = 'PASS';
  report.browserAliveAfterInspection = true;
  persistReport();
} catch (error) {
  primaryError = error;
  report.status = 'FAIL';
  report.error = error.stack || String(error);
  persistReport();
} finally {
  if (browserControl?.socket?.readyState === WebSocket.OPEN) {
    try {
      await Promise.race([
        browserControl.send('Browser.close'),
        delay(2000),
      ]);
    } catch {
      // The exact-PID fallback below owns cleanup after a CDP failure.
    }
  }
  page?.close();
  browserControl?.close();
  await delay(300);

  const ownedPids = new Set();
  if (browserPid > 0) {
    ownedPids.add(browserPid);
  }
  if (browser.exitCode === null && browser.pid > 0) {
    ownedPids.add(browser.pid);
  }
  for (const pid of ownedPids) {
    try {
      process.kill(pid, 0);
    } catch {
      continue;
    }
    spawnSync('taskkill.exe', [
      '/PID', String(pid), '/T', '/F',
    ], {
      stdio: 'ignore',
      windowsHide: true,
    });
  }

  const tempRoot = path.resolve(os.tmpdir());
  const resolvedProfile = path.resolve(profileDir);
  const relativeProfile = path.relative(tempRoot, resolvedProfile);
  if (path.basename(resolvedProfile).startsWith(
          'focus-settings-import-qa-') &&
      relativeProfile && !relativeProfile.startsWith('..') &&
      !path.isAbsolute(relativeProfile)) {
    await delay(200);
    fs.rmSync(resolvedProfile, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
}

if (primaryError) {
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify(report, null, 2));
}
