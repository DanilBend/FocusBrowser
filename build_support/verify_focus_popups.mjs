// Copyright 2026 The Focus Browser Authors
// Headless behavioral smoke test for the browser-owned Focus protection UIs.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const browserPath = process.argv[2];
assert.ok(browserPath && fs.existsSync(browserPath),
          'Usage: node verify_focus_popups.mjs <path-to-chrome.exe>');

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), 'focus-popup-test-'));
const browser = spawn(browserPath, [
  '--headless=new',
  '--disable-gpu',
  '--allow-file-access-from-files',
  '--no-first-run',
  '--no-default-browser-check',
  `--user-data-dir=${profileDir}`,
  '--remote-debugging-port=0',
  'about:blank',
], { stdio: 'ignore', windowsHide: true });

const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitForDevToolsPort() {
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (fs.existsSync(portFile)) {
      const [port] = fs.readFileSync(portFile, 'utf8').trim().split(/\r?\n/);
      if (port) return Number(port);
    }
    assert.equal(browser.exitCode, null, 'Browser exited before DevTools started');
    await delay(50);
  }
  throw new Error('Timed out waiting for DevToolsActivePort');
}

async function connectWebSocket(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  let nextId = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id) return;
    const callbacks = pending.get(message.id);
    if (!callbacks) return;
    pending.delete(message.id);
    if (message.error) {
      callbacks.reject(new Error(message.error.message));
    } else {
      callbacks.resolve(message.result);
    }
  });

  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { socket, send };
}

async function connectPage(port) {
  const target = await fetch(`http://127.0.0.1:${port}/json/new`, {
    method: 'PUT',
  }).then(response => response.json());
  return connectWebSocket(target.webSocketDebuggerUrl);
}

async function connectBrowser(port) {
  const version = await fetch(`http://127.0.0.1:${port}/json/version`)
      .then(response => response.json());
  return connectWebSocket(version.webSocketDebuggerUrl);
}

async function waitUntilReady(send) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const result = await send('Runtime.evaluate', {
      expression: 'document.readyState',
      returnByValue: true,
    });
    if (result.result.value === 'complete') {
      await delay(100);
      return;
    }
    await delay(50);
  }
  throw new Error('Page did not finish loading');
}

async function evaluate(send, expression) {
  const response = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ||
                    response.exceptionDetails.text);
  }
  return response.result.value;
}

const storageStub = String.raw`
  (() => {
    const state = {};
    const writes = [];
    const storage = {
      local: {
        get(_keys, callback) {
          queueMicrotask(() => callback(structuredClone(state)));
        },
        set(values, callback) {
          Object.assign(state, structuredClone(values));
          writes.push(structuredClone(values));
          if (callback) queueMicrotask(callback);
        },
        remove(keys, callback) {
          for (const key of Array.isArray(keys) ? keys : [keys]) {
            delete state[key];
          }
          if (callback) queueMicrotask(callback);
        },
      },
      onChanged: { addListener() {} },
    };
    globalThis.chrome ||= {};
    const focusBlockState = {
      appName: 'FocusBlock',
      appVersion: '1.0',
      tabId: 7,
      tabTitle: 'Тестовая страница',
      pageURL: 'https://focusbrowser.ru/',
      rawURL: 'https://focusbrowser.ru/',
      pageHostname: 'focusbrowser.ru',
      pageDomain: 'focusbrowser.ru',
      cnameMap: [],
      hostnameDict: {
        'focusbrowser.ru': {
          domain: 'focusbrowser.ru',
          counts: {
            allowed: { any: 72, script: 4, frame: 1 },
            blocked: { any: 28, script: 2, frame: 0 },
          },
        },
      },
      firewallRules: {},
      pageCounts: { allowed: { any: 72 }, blocked: { any: 28 } },
      globalAllowedRequestCount: 5400,
      globalBlockedRequestCount: 12486,
      popupBlockedCount: 0,
      largeMediaCount: 0,
      remoteFontCount: 0,
      contentLastModified: 1,
      focusGlobalEnabled: true,
      netFilteringSwitch: true,
      noPopups: false,
      noLargeMedia: false,
      noCosmeticFiltering: false,
      noRemoteFonts: false,
      noScripting: false,
      canElementPicker: true,
      userFiltersAreEnabled: true,
      advancedUserEnabled: false,
      matrixIsDirty: false,
      hasUnprocessedRequest: false,
      firewallPaneMinimized: true,
      popupPanelSections: 0,
      popupPanelDisabledSections: 0,
      popupPanelLockedSections: 0,
      popupPanelOrientation: 'portrait',
      popupPanelHeightMode: 0,
      fontSize: 'unset',
      tooltipsDisabled: false,
      colorBlindFriendly: false,
      godMode: false,
    };
    const cloneFocusBlockState = () => structuredClone(focusBlockState);
    const localValues = new Map();
    const focusBlockMessage = (channel, message) => {
      if (channel === 'dom' && message?.what === 'uiStyles') {
        return { uiTheme: 'dark', uiStyles: 'unset' };
      }
      if (channel === 'vapi' && message?.what === 'localStorage') {
        const [key, value] = message.args || [];
        if (message.fn === 'getItemAsync') return localValues.get(key) ?? null;
        if (message.fn === 'setItem') localValues.set(key, value);
        if (message.fn === 'removeItem') localValues.delete(key);
        if (message.fn === 'clear') localValues.clear();
        return undefined;
      }
      if (channel !== 'popupPanel') return undefined;
      switch (message?.what) {
      case 'getPopupData':
        return cloneFocusBlockState();
      case 'toggleFocusGlobalFiltering':
        focusBlockState.focusGlobalEnabled = message.state === true;
        return cloneFocusBlockState();
      case 'toggleNetFiltering':
        focusBlockState.netFilteringSwitch = message.state === true;
        return cloneFocusBlockState();
      case 'toggleHostnameSwitch': {
        const propertyById = {
          'no-popups': 'noPopups',
          'no-large-media': 'noLargeMedia',
          'no-cosmetic-filtering': 'noCosmeticFiltering',
          'no-remote-fonts': 'noRemoteFonts',
          'no-scripting': 'noScripting',
        };
        focusBlockState[propertyById[message.name]] = message.state === true;
        return cloneFocusBlockState();
      }
      case 'getHiddenElementCount':
      case 'getScriptCount':
        return 0;
      case 'hasPopupContentChanged':
        return false;
      default:
        return cloneFocusBlockState();
      }
    };
    const portListeners = new Set();
    const disconnectListeners = new Set();
    const mockPort = {
      onMessage: {
        addListener(listener) { portListeners.add(listener); },
        removeListener(listener) { portListeners.delete(listener); },
      },
      onDisconnect: {
        addListener(listener) { disconnectListeners.add(listener); },
        removeListener(listener) { disconnectListeners.delete(listener); },
      },
      postMessage(envelope) {
        queueMicrotask(() => {
          const response = focusBlockMessage(envelope.channel, envelope.msg);
          for (const listener of portListeners) {
            listener({ msgId: envelope.msgId, msg: response });
          }
        });
      },
      disconnect() {},
    };
    Object.defineProperty(globalThis.chrome, 'storage', {
      configurable: true,
      value: storage,
    });
    Object.defineProperty(globalThis.chrome, 'runtime', {
      configurable: true,
      value: {
        id: 'jafokmemnknjknbdiklabcnhlpheefbm',
        lastError: undefined,
        connect() { return mockPort; },
        getManifest() { return { name: 'FocusBlock', version: '1.72.2.2' }; },
        getURL(resource = '') {
          return 'chrome-extension://blockjmkbacgjkknlgpkjjiijinjdanf/' + resource;
        },
        sendMessage(message, callback) {
          queueMicrotask(() => {
            const schema = globalThis.FocusYoutubeSettings;
            if (message?.type === 'focusyoutube.reconcileAutomation') {
              const migration = schema.createMigration(state);
              const automationPatch = Object.fromEntries(
                  Object.entries(migration.patch).filter(([id]) =>
                    schema.automationIds.includes(id)));
              Object.assign(state, structuredClone(automationPatch));
              if (Object.keys(automationPatch).length) {
                writes.push(structuredClone(automationPatch));
              }
            } else if (message?.type === 'focusyoutube.updateAutomation') {
              Object.assign(state, structuredClone(message.changes));
              writes.push(structuredClone(message.changes));
            } else {
              callback?.({ ok: false, error: 'unknown message' });
              return;
            }
            const settings = Object.fromEntries(schema.automationIds.map(id =>
              [id, state[id]]));
            callback?.({ ok: true, settings });
          });
        },
      },
    });
    Object.defineProperty(globalThis.chrome, 'i18n', {
      configurable: true,
      value: {
        getMessage(key) {
          const messages = {
            '@@ui_locale': 'ru',
            popupBlockedStats: '{{count}} ({{percent}}%)',
            popupHitDomainCount: '{{count}} из {{total}}',
            popupPowerSwitchInfo1: 'Выключить защиту на этом сайте',
            popupPowerSwitchInfo2: 'Включить защиту на этом сайте',
            popupTipNoPopups1: 'Блокировать всплывающие окна',
            popupTipNoPopups2: 'Не блокировать всплывающие окна',
            popupTipNoLargeMedia1: 'Блокировать крупные медиа',
            popupTipNoLargeMedia2: 'Не блокировать крупные медиа',
            popupTipNoCosmeticFiltering1: 'Не скрывать элементы рекламы',
            popupTipNoCosmeticFiltering2: 'Скрывать элементы рекламы',
            popupTipNoRemoteFonts1: 'Блокировать удалённые шрифты',
            popupTipNoRemoteFonts2: 'Не блокировать удалённые шрифты',
            popupTipNoScripting1: 'Отключить JavaScript',
            popupTipNoScripting2: 'Включить JavaScript',
          };
          return messages[key] || '';
        },
      },
    });
    globalThis.__focusPopupTest = {
      state,
      writes,
      focusBlockState,
    };
  })();
`;

let page;
let browserControl;
let browserProcessId;
try {
  const port = await waitForDevToolsPort();
  browserControl = await connectBrowser(port);
  const processInfo = await browserControl.send('SystemInfo.getProcessInfo');
  browserProcessId = processInfo.processInfo.find(
      process => process.type === 'browser')?.id;
  assert.ok(Number.isInteger(browserProcessId) && browserProcessId > 0,
            'DevTools did not report the test browser PID');
  page = await connectPage(port);
  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Page.addScriptToEvaluateOnNewDocument', {
    source: storageStub,
  });

  const focusYoutubeUrl = pathToFileURL(path.join(
      repoRoot, 'build', 'src', 'third_party', 'focus_youtube',
      'popup.html')).href;
  await page.send('Page.navigate', { url: focusYoutubeUrl });
  await waitUntilReady(page.send);

  const youtubeResult = await evaluate(page.send, String.raw`
    (async () => {
      const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
      const visibleControls = [
        ...document.querySelectorAll('#controls input[type="checkbox"]'),
      ];
      const initial = {
        masterChecked: document.getElementById('global_enable').checked,
        visibleControlCount: visibleControls.length,
        visibleControlsOff: visibleControls.every(input => !input.checked),
        storedBehaviorCount: globalThis.FocusYoutubeSettings.behaviorIds
            .filter(id => globalThis.__focusPopupTest.state[id] === false)
            .length,
        bodyBackground: getComputedStyle(document.body).backgroundColor,
        text: document.body.innerText,
        selectedText: document.getElementById('enabledCount').textContent,
        resetDisabled: document.getElementById('resetAll').disabled,
        masterText: document.getElementById('masterState').textContent,
        masterTag: document.getElementById('global_enable').tagName,
        resetTag: document.getElementById('resetAll').tagName,
      };

      document.getElementById('remove_homepage').click();
      await sleep(20);
      document.getElementById('remove_sidebar').click();
      await sleep(20);
      const selectedAfterChanges =
          document.getElementById('enabledCount').textContent;
      const resetEnabledAfterChanges =
          !document.getElementById('resetAll').disabled;

      document.getElementById('global_enable').click();
      await sleep(20);
      const switchedOff =
          !document.getElementById('global_enable').checked;
      const switchedOffText =
          document.getElementById('masterState').textContent;
      document.getElementById('global_enable').click();
      await sleep(20);

      document.getElementById('resetAll').click();
      await sleep(20);
      const resetState = {
        allVisibleControlsOff: visibleControls.every(input => !input.checked),
        selectedText: document.getElementById('enabledCount').textContent,
        resetDisabled: document.getElementById('resetAll').disabled,
      };

      return {
        initial,
        selectedAfterChanges,
        resetEnabledAfterChanges,
        switchedOff,
        switchedOffText,
        switchedBackOn: document.getElementById('global_enable').checked,
        resetState,
        stored: structuredClone(globalThis.__focusPopupTest.state),
        writeCount: globalThis.__focusPopupTest.writes.length,
      };
    })()
  `);

  assert.equal(youtubeResult.initial.masterChecked, true);
  assert.equal(youtubeResult.initial.visibleControlCount, 24);
  assert.equal(youtubeResult.initial.visibleControlsOff, true);
  assert.equal(youtubeResult.initial.storedBehaviorCount, 93);
  assert.equal(youtubeResult.initial.bodyBackground, 'rgb(8, 8, 8)');
  assert.match(youtubeResult.initial.text, /Скрывать рекомендации на главной/);
  assert.doesNotMatch(youtubeResult.initial.text, /Hide Home Feed/);
  assert.equal(youtubeResult.initial.selectedText, 'Включено 0 из 24');
  assert.equal(youtubeResult.initial.resetDisabled, true);
  assert.equal(youtubeResult.initial.masterText, 'FocusYoutube включён');
  assert.equal(youtubeResult.initial.masterTag, 'INPUT');
  assert.equal(youtubeResult.initial.resetTag, 'BUTTON');
  assert.equal(youtubeResult.selectedAfterChanges, 'Включено 2 из 24');
  assert.equal(youtubeResult.resetEnabledAfterChanges, true);
  assert.equal(youtubeResult.switchedOff, true);
  assert.equal(youtubeResult.switchedOffText, 'FocusYoutube выключен');
  assert.equal(youtubeResult.switchedBackOn, true);
  assert.equal(youtubeResult.resetState.allVisibleControlsOff, true);
  assert.equal(youtubeResult.resetState.selectedText, 'Включено 0 из 24');
  assert.equal(youtubeResult.resetState.resetDisabled, true);
  assert.equal(youtubeResult.stored.remove_homepage, false);
  assert.equal(youtubeResult.stored.remove_sidebar, false);
  assert.equal(youtubeResult.stored.global_enable, true);
  assert.ok(youtubeResult.writeCount >= 5);

  const focusBlockUrl = pathToFileURL(path.join(
      repoRoot, 'build', 'src', 'third_party', 'ublock',
      'popup-fenix.html')).href;
  // Extension popups start auto-sizing from a deliberately small viewport.
  // Keep the browser-owned panel at its fixed 400-DIP product width even
  // under a fractional Windows scale factor.
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: 90,
    height: 600,
    deviceScaleFactor: 1.25,
    mobile: false,
  });
  await page.send('Page.navigate', { url: focusBlockUrl });
  await waitUntilReady(page.send);
  const blockResult = await evaluate(page.send, String.raw`
    (async () => {
      const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
      const globalSwitch = document.getElementById('focusGlobalSwitch');
      const siteSwitch = document.getElementById('switch');
      const siteOption = document.getElementById('no-popups');
      const initial = {
        text: document.getElementById('main').innerText,
        globalOn: !document.body.classList.contains('focusGlobalOff'),
        globalAria: globalSwitch.getAttribute('aria-checked'),
        siteOn: !document.body.classList.contains('off'),
        siteAria: siteSwitch.getAttribute('aria-checked'),
        pageStat: document.querySelector('[data-focus-stat="page"] + span')
            .textContent,
        totalStat: document.querySelector('[data-focus-stat="total"] + span')
            .textContent,
        bodyColor: getComputedStyle(document.body).backgroundColor,
        mainMaxHeight: getComputedStyle(document.getElementById('main')).maxHeight,
        markBackground: getComputedStyle(document.querySelector('.focusMark'))
            .backgroundColor,
        waterAnimation: getComputedStyle(
            document.querySelector('.masterCard'), '::before').animationName,
        waterDuration: getComputedStyle(
            document.querySelector('.masterCard'), '::before').animationDuration,
        hasLogoTile: document.querySelector('.focusMark rect') !== null,
        optionCount: document.querySelectorAll('#extraTools .hnSwitch').length,
        externalLinkCount: document.querySelectorAll('a[href^="http"]').length,
        settingsHref: document.querySelector('a[href="dashboard.html"]')?.
            getAttribute('href'),
        viewportWidth: window.innerWidth,
        intrinsicWidth: Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth,
            document.getElementById('main').scrollWidth),
      };

      globalSwitch.click();
      await sleep(50);
      const globalOff = {
        bodyState: document.body.classList.contains('focusGlobalOff'),
        aria: globalSwitch.getAttribute('aria-checked'),
        backend: globalThis.__focusPopupTest.focusBlockState.focusGlobalEnabled,
        visibleText: document.querySelector(
            '#focusProtection .focusGlobalOff').textContent,
      };
      globalSwitch.click();
      await sleep(50);

      siteSwitch.click();
      await sleep(30);
      const siteOff = {
        bodyState: document.body.classList.contains('off'),
        aria: siteSwitch.getAttribute('aria-checked'),
        backend: globalThis.__focusPopupTest.focusBlockState.netFilteringSwitch,
      };
      siteSwitch.click();
      await sleep(30);

      document.getElementById('focusSiteOptions').open = true;
      siteOption.click();
      await sleep(30);
      const siteOptionOn = {
        classState: siteOption.classList.contains('on'),
        aria: siteOption.getAttribute('aria-pressed'),
        backend: globalThis.__focusPopupTest.focusBlockState.noPopups,
      };

      return {
        initial,
        globalOff,
        globalBackOn: !document.body.classList.contains('focusGlobalOff'),
        siteOff,
        siteBackOn: !document.body.classList.contains('off'),
        siteOptionOn,
      };
    })()
  `);
  assert.match(blockResult.initial.text, /FocusBlock включён/);
  assert.match(blockResult.initial.text, /Встроенная защита браузера/);
  assert.match(blockResult.initial.text, /ВСТРОЕНО/);
  assert.match(blockResult.initial.text, /На этой странице/);
  assert.match(blockResult.initial.text, /Всего заблокировано/);
  assert.equal(blockResult.initial.globalOn, true);
  assert.equal(blockResult.initial.globalAria, 'true');
  assert.equal(blockResult.initial.siteOn, true);
  assert.equal(blockResult.initial.siteAria, 'true');
  assert.match(blockResult.initial.pageStat, /^28/);
  assert.match(blockResult.initial.totalStat, /12[\s ]?486/);
  assert.equal(blockResult.initial.bodyColor, 'rgb(8, 8, 8)');
  assert.equal(blockResult.initial.mainMaxHeight, '600px');
  assert.equal(blockResult.initial.markBackground, 'rgba(0, 0, 0, 0)');
  assert.equal(blockResult.initial.waterAnimation, 'focusWater');
  assert.equal(blockResult.initial.waterDuration, '10s');
  assert.equal(blockResult.initial.hasLogoTile, false);
  assert.equal(blockResult.initial.optionCount, 5);
  assert.equal(blockResult.initial.externalLinkCount, 0);
  assert.equal(blockResult.initial.settingsHref, 'dashboard.html');
  assert.equal(blockResult.initial.viewportWidth, 90);
  assert.ok(blockResult.initial.intrinsicWidth >= 400,
            `FocusBlock popup collapsed to ${blockResult.initial.intrinsicWidth}px ` +
            'instead of advertising its 400px intrinsic desktop width');
  assert.deepEqual(blockResult.globalOff, {
    bodyState: true,
    aria: 'false',
    backend: false,
    visibleText: 'FocusBlock выключен',
  });
  assert.equal(blockResult.globalBackOn, true);
  assert.deepEqual(blockResult.siteOff, {
    bodyState: true,
    aria: 'false',
    backend: false,
  });
  assert.equal(blockResult.siteBackOn, true);
  assert.deepEqual(blockResult.siteOptionOn, {
    classState: true,
    aria: 'true',
    backend: true,
  });

  await page.send('Page.navigate', { url: `${focusBlockUrl}?focusMotion=0` });
  await waitUntilReady(page.send);
  const blockMotionResult = await evaluate(page.send, String.raw`
    (() => ({
      dataset: document.documentElement.dataset.motion,
      transition: getComputedStyle(
          document.querySelector('.switchTrack')).transitionDuration,
      scrollBehavior: getComputedStyle(
          document.querySelector('.popupContent')).scrollBehavior,
      waterAnimation: getComputedStyle(
          document.querySelector('.masterCard'), '::before').animationName,
    }))()
  `);
  assert.equal(blockMotionResult.dataset, 'off');
  assert.equal(blockMotionResult.transition, '0s');
  assert.equal(blockMotionResult.scrollBehavior, 'auto');
  assert.equal(blockMotionResult.waterAnimation, 'none');

  console.log('Focus popup behavior passed: defaults, toggles, persistence, ' +
              'Russian text, shared design tokens and reduced motion.');
} finally {
  if (browserControl?.socket.readyState === WebSocket.OPEN) {
    // Close the real browser process through DevTools first. Installed Chromium
    // can relaunch after its short-lived bootstrap process exits, so relying on
    // the PID returned by spawn() alone can leave the relaunched process alive.
    try {
      await Promise.race([
        browserControl.send('Browser.close'),
        delay(2000),
      ]);
    } catch {
      // The WebSocket is allowed to close before Browser.close replies.
    }
    await delay(500);
  }
  if (page?.socket.readyState === WebSocket.OPEN) page.socket.close();
  if (browserControl?.socket.readyState === WebSocket.OPEN) {
    browserControl.socket.close();
  }
  // Browser.close is normally sufficient. taskkill is a bounded fallback for
  // vendor builds that relaunch their bootstrap process and keep the real
  // headless browser alive. The PID comes from this DevTools instance itself.
  if (browserProcessId || browser.exitCode === null) {
    spawnSync('taskkill', [
      '/PID', String(browserProcessId || browser.pid), '/T', '/F',
    ], {
      stdio: 'ignore',
      windowsHide: true,
    });
    await delay(500);
  }
  const resolvedTemp = path.resolve(os.tmpdir());
  const resolvedProfile = path.resolve(profileDir);
  if (resolvedProfile.startsWith(`${resolvedTemp}${path.sep}`)) {
    // taskkill may return a fraction before Windows releases Chromium's
    // profile handles. Give the OS a moment, then use Node's bounded Windows
    // retry support so a successful UI check is not reported as a false
    // negative because of that shutdown race.
    await delay(250);
    fs.rmSync(resolvedProfile, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
}
