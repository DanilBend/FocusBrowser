// Release-only runtime QA for the built Focus Browser setup and settings UI.
// Uses one disposable profile and only the executable passed on the command line.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';

const chromePath = path.resolve(process.argv[2] || '');
assert.ok(fs.existsSync(chromePath),
          'Usage: node qa/release-final-setup-runtime.mjs <chrome.exe>');

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname
    .replace(/^\/(?:[A-Za-z]:)/, match => match.slice(1))), '..');
const qaRoot = path.join(repoRoot, 'qa');
const artifact = suffix =>
  path.join(qaRoot, `release-final-setup-${suffix}`);
const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-release-final-setup-'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

const browser = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-sync',
  '--no-first-run',
  '--no-default-browser-check',
  '--noerrdialogs',
  '--lang=ru',
  '--remote-debugging-port=0',
  `--user-data-dir=${profileDir}`,
  'about:blank',
], {stdio: 'ignore', windowsHide: true});

async function waitForPort() {
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  for (let attempt = 0; attempt < 240; ++attempt) {
    if (fs.existsSync(portFile)) {
      const port = Number(fs.readFileSync(portFile, 'utf8').split(/\r?\n/)[0]);
      if (port) return port;
    }
    assert.equal(browser.exitCode, null, 'QA browser exited during startup');
    await delay(50);
  }
  throw new Error('DevToolsActivePort timeout');
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once: true});
    socket.addEventListener('error', reject, {once: true});
  });
  let nextId = 0;
  const pending = new Map();
  const listeners = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const callback = pending.get(message.id);
      if (!callback) return;
      pending.delete(message.id);
      if (message.error) callback.reject(new Error(message.error.message));
      else callback.resolve(message.result || {});
      return;
    }
    for (const listener of listeners.get(message.method) || []) {
      listener(message.params || {});
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
  const on = (method, listener) => {
    const entries = listeners.get(method) || [];
    entries.push(listener);
    listeners.set(method, entries);
  };
  return {socket, send, on};
}

async function evaluate(page, expression) {
  const response = await page.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ||
                    response.exceptionDetails.text);
  }
  return response.result?.value;
}

async function waitFor(page, expression, attempts = 200) {
  for (let attempt = 0; attempt < attempts; ++attempt) {
    const result = await evaluate(page, expression).catch(() => null);
    if (result) return result;
    await delay(100);
  }
  throw new Error(`Timed out waiting for: ${expression.slice(0, 120)}`);
}

async function screenshot(page, suffix) {
  // The welcome copy is intentionally staggered through 1.14 seconds. Capture
  // the settled UI rather than a valid-but-blurred intermediate animation.
  // Page transitions use a 50 ms delay followed by a 300 ms animation.
  // Leave one additional frame budget so screenshots never capture the
  // animation's clipped/blurred boundary frame on a busy release machine.
  await delay(suffix === '00-welcome' ? 1300 : 1200);
  const result = await page.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    fromSurface: true,
  });
  const output = artifact(`${suffix}.png`);
  fs.writeFileSync(output, Buffer.from(result.data, 'base64'));
  return path.basename(output);
}

const activeSetupExpression = `(() => {
  const active = document.querySelector('.onboarding-page.visible');
  if (!active) return null;
  const rect = active.getBoundingClientRect();
  return {
    id: active.id,
    text: (active.innerText || '').trim(),
    href: location.href,
    lang: document.documentElement.lang,
    dataMotion: document.documentElement.dataset.motion || '',
    animation: getComputedStyle(document.body, '::after').animationName,
    visible: rect.width > 0 && rect.height > 0 &&
        getComputedStyle(active).visibility !== 'hidden',
    progress: document.querySelector('#setup-progress')?.textContent?.trim() || '',
    error: document.querySelector('#setup-error')?.textContent?.trim() || '',
  };
})()`;

const deepHelpers = `
  const deepAll = () => {
    const result = [];
    const visit = root => {
      for (const element of root.querySelectorAll('*')) {
        result.push(element);
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(document);
    return result;
  };
`;

let browserControl;
let page;
let browserPid = 0;
const report = {
  status: 'RUNNING',
  executable: chromePath,
  profileKind: 'unique-temporary',
  setup: {pages: [], motion: {}, shortcuts: {}},
  settings: {appearance: {}, defaultBrowser: {}},
  artifacts: [],
  runtimeExceptions: [],
};

try {
  const port = await waitForPort();
  const version = await fetch(`http://127.0.0.1:${port}/json/version`)
      .then(response => response.json());
  browserControl = await connect(version.webSocketDebuggerUrl);
  const processInfo = await browserControl.send('SystemInfo.getProcessInfo');
  browserPid = processInfo.processInfo.find(item => item.type === 'browser')?.id;
  assert.ok(Number.isInteger(browserPid) && browserPid > 0);

  // In a normal window BrowserNavigator rewrites focus://setup to the internal
  // WebUI. Headless startup ignores that alias, so create the resolved target.
  const target = await fetch(
      `http://127.0.0.1:${port}/json/new?${encodeURIComponent('chrome://setup/')}`,
      {method: 'PUT'}).then(response => response.json());
  page = await connect(target.webSocketDebuggerUrl);
  page.on('Runtime.exceptionThrown', event => {
    report.runtimeExceptions.push(
        event.exceptionDetails?.exception?.description ||
        event.exceptionDetails?.text || 'Unknown runtime exception');
  });
  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  });

  const expectedPages = [
    ['welcome-page', '00-welcome'],
    ['focus-page', '01-focus'],
    ['appearance-page', '02-appearance'],
    ['search-engines-page', '03-search'],
    ['data-import-page', '04-import'],
    ['password-manager-page', '05-passwords'],
    ['default-browser-page', '06-default-browser'],
    ['shortcuts-page', '07-shortcuts'],
  ];

  let state = await waitFor(page, activeSetupExpression);
  for (let index = 0; index < expectedPages.length; ++index) {
    const [expectedId, shotName] = expectedPages[index];
    state = await waitFor(page,
        `(() => { const value = ${activeSetupExpression}; ` +
        `return value?.id === ${JSON.stringify(expectedId)} ? value : null; })()`);
    assert.equal(state.id, expectedId);
    assert.equal(state.visible, true);
    assert.match(state.text, /[А-Яа-яЁё]/,
                 `${expectedId} has no Russian UI text`);
    assert.doesNotMatch(state.text, /[\u0402-\u040f\u0452-\u045f]/,
                        `${expectedId} contains mojibake`);
    assert.equal(state.error, '', `${expectedId}: ${state.error}`);
    report.setup.pages.push({
      id: state.id,
      href: state.href,
      lang: state.lang,
      progress: state.progress,
      textSample: state.text.slice(0, 180),
    });
    report.artifacts.push(await screenshot(page, shotName));

    if (expectedId === 'focus-page') {
      const motionOn = await evaluate(page, `(() => {
        const button = [...document.querySelectorAll(
            '#focus-page.visible button[role="switch"]')]
            .find(item => /Плавные анимации/i.test(item.innerText));
        return button ? {
          checked: button.getAttribute('aria-checked'),
          dataMotion: document.documentElement.dataset.motion,
          animation: getComputedStyle(document.body, '::after').animationName,
        } : null;
      })()`);
      assert.deepEqual(motionOn, {
        checked: 'true', dataMotion: 'on', animation: 'focus-water-drift'});
      await evaluate(page, `(() => {
        const button = [...document.querySelectorAll(
            '#focus-page.visible button[role="switch"]')]
            .find(item => /Плавные анимации/i.test(item.innerText));
        button.click(); return true;
      })()`);
      const motionOff = await waitFor(page, `(() => {
        const button = [...document.querySelectorAll(
            '#focus-page.visible button[role="switch"]')]
            .find(item => /Плавные анимации/i.test(item.innerText));
        if (document.documentElement.dataset.motion !== 'off') return null;
        return {
          checked: button?.getAttribute('aria-checked'),
          dataMotion: document.documentElement.dataset.motion,
          animation: getComputedStyle(document.body, '::after').animationName,
        };
      })()`);
      assert.deepEqual(motionOff,
                       {checked: 'false', dataMotion: 'off', animation: 'none'});
      report.artifacts.push(await screenshot(page, '01b-focus-motion-off'));
      await evaluate(page, `(() => {
        const button = [...document.querySelectorAll(
            '#focus-page.visible button[role="switch"]')]
            .find(item => /Плавные анимации/i.test(item.innerText));
        button.click(); return true;
      })()`);
      await waitFor(page,
          `document.documentElement.dataset.motion === 'on'`);
      report.setup.motion = {on: motionOn, off: motionOff, restored: true};
    }

    if (expectedId === 'default-browser-page') {
      // Keep onboarding itself from requesting the OS default-browser UI.
      await evaluate(page, `(() => {
        const choices = [...document.querySelectorAll(
            '#default-browser-page.visible button.big[aria-pressed]')];
        const no = choices.find(item => /Не сейчас/i.test(item.innerText)) ||
            choices[1];
        no?.click();
        return choices.map(item => item.getAttribute('aria-pressed'));
      })()`);
    }

    if (expectedId === 'password-manager-page') {
      // Headless Windows can report the OS handoff as unavailable even though
      // the page itself is valid. Exercise the same live-state listener used
      // by production, only inside this disposable renderer, so the complete
      // seven-step UI can be visually covered.
      const exposed = await evaluate(page, `(() => {
        if (!globalThis.cr?.webUIListenerCallback) return false;
        cr.webUIListenerCallback('browser-default-state-changed', {
          isDefault: false,
          canBeDefault: true,
          isDisabledByPolicy: false,
          isUnknownError: false,
          canPin: false,
        });
        return true;
      })()`);
      assert.equal(exposed, true,
                   'Default-browser WebUI listener is unavailable');
      await delay(100);
    }

    if (expectedId === 'shortcuts-page') {
      const shortcuts = await evaluate(page, `(() => {
        const rows = [...document.querySelectorAll(
            '#shortcuts-page.visible button.shortcut')];
        return {
          count: rows.length,
          selected: rows.filter(row => row.getAttribute('aria-pressed') === 'true')
              .length,
          fallbackCount: document.querySelectorAll(
              '#shortcuts-page.visible .logo-fallback').length,
          logos: rows.map(row => {
            const image = row.querySelector('.mark img');
            return {
              name: row.querySelector('.name')?.textContent?.trim(),
              loaded: Boolean(image?.complete && image.naturalWidth > 0 &&
                              image.naturalHeight > 0),
              width: image?.naturalWidth || 0,
              height: image?.naturalHeight || 0,
              src: image?.src || '',
            };
          }),
        };
      })()`);
      assert.equal(shortcuts.count, 16);
      assert.equal(shortcuts.selected, 0);
      assert.equal(shortcuts.fallbackCount, 0);
      assert.ok(shortcuts.logos.every(item => item.loaded),
                JSON.stringify(shortcuts.logos.filter(item => !item.loaded)));
      report.setup.shortcuts = shortcuts;
      break;
    }

    const nextSelector = index === 0 ?
      '#welcome-buttons button.primary:not(:disabled)' :
      '#setup-buttons button.primary:not(:disabled)';
    await waitFor(page, `Boolean(document.querySelector(${JSON.stringify(nextSelector)}))`);
    await evaluate(page,
        `document.querySelector(${JSON.stringify(nextSelector)}).click()`);
    await delay(350);
  }

  // Appearance settings: locate the Focus pref through every open shadow root.
  await page.send('Page.navigate', {url: 'chrome://settings/appearance'});
  const appearance = await waitFor(page, `(() => {
    ${deepHelpers}
    const toggle = deepAll().find(element =>
      element.localName === 'settings-toggle-button' &&
      element.pref?.key === 'focus.ui.motion_enabled');
    if (!toggle) return null;
    return {
      href: location.href,
      checked: Boolean(toggle.checked),
      prefValue: Boolean(toggle.pref?.value),
      label: toggle.label || '',
    };
  })()`);
  assert.match(appearance.label, /Плавные анимации/i);
  assert.equal(appearance.checked, true);
  assert.equal(appearance.prefValue, true);
  report.artifacts.push(await screenshot(page, '08-settings-appearance-on'));
  const appearanceOff = await evaluate(page, `(() => {
    ${deepHelpers}
    const toggle = deepAll().find(element =>
      element.localName === 'settings-toggle-button' &&
      element.pref?.key === 'focus.ui.motion_enabled');
    toggle.click();
    return true;
  })()`);
  assert.equal(appearanceOff, true);
  const offState = await waitFor(page, `(() => {
    ${deepHelpers}
    const toggle = deepAll().find(element =>
      element.localName === 'settings-toggle-button' &&
      element.pref?.key === 'focus.ui.motion_enabled');
    if (!toggle || toggle.checked || toggle.pref?.value) return null;
    return {checked: Boolean(toggle.checked), prefValue: Boolean(toggle.pref.value)};
  })()`);
  report.artifacts.push(await screenshot(page, '08b-settings-appearance-off'));
  await evaluate(page, `(() => {
    ${deepHelpers}
    deepAll().find(element => element.localName === 'settings-toggle-button' &&
      element.pref?.key === 'focus.ui.motion_enabled').click();
    return true;
  })()`);
  const restored = await waitFor(page, `(() => {
    ${deepHelpers}
    const toggle = deepAll().find(element =>
      element.localName === 'settings-toggle-button' &&
      element.pref?.key === 'focus.ui.motion_enabled');
    return toggle?.checked && toggle.pref?.value;
  })()`);
  assert.equal(restored, true);
  report.settings.appearance = {initial: appearance, off: offState, restored};

  // Default-browser regression: one real button click, then only prove that
  // the same browser process and WebUI target remain alive. Do not interact
  // with the Windows picker which this request may open.
  await page.send('Page.navigate', {url: 'chrome://settings/defaultBrowser'});
  const nativeDefaultState = await waitFor(page, `(() => {
    ${deepHelpers}
    const host = deepAll().find(element =>
        element.localName === 'settings-default-browser-page');
    if (!host) return null;
    const button = host.shadowRoot?.querySelector('cr-button');
    if (!button && !host.isDefault_ && !host.isSecondaryInstall_ &&
        !host.isUnknownError_) return null;
    return {
      href: location.href,
      maySet: Boolean(host.maySetDefaultBrowser_),
      isDefault: Boolean(host.isDefault_),
      secondary: Boolean(host.isSecondaryInstall_),
      unknownError: Boolean(host.isUnknownError_),
      buttonText: button?.innerText?.trim() || '',
    };
  })()`);
  if (!nativeDefaultState.maySet) {
    // This machine already reports Focus Browser as default. Reveal the
    // not-default branch through the page's real listener so the regression
    // click still reaches the native handler without changing Windows state.
    const exposed = await evaluate(page, `(() => {
      if (!globalThis.cr?.webUIListenerCallback) return false;
      cr.webUIListenerCallback('browser-default-state-changed', {
        isDefault: false,
        canBeDefault: true,
        isDisabledByPolicy: false,
        isUnknownError: false,
        canPin: false,
      });
      return true;
    })()`);
    assert.equal(exposed, true);
  }
  const defaultBefore = await waitFor(page, `(() => {
    ${deepHelpers}
    const host = deepAll().find(element =>
        element.localName === 'settings-default-browser-page');
    const button = host?.shadowRoot?.querySelector('cr-button');
    if (!host?.maySetDefaultBrowser_ || !button) return null;
    return {
      href: location.href,
      maySet: true,
      isDefault: Boolean(host.isDefault_),
      secondary: Boolean(host.isSecondaryInstall_),
      unknownError: Boolean(host.isUnknownError_),
      buttonText: button.innerText.trim(),
    };
  })()`);
  assert.match(defaultBefore.buttonText, /по умолчанию|основн/i);
  report.artifacts.push(await screenshot(page, '09-settings-default-before'));
  const clicked = await evaluate(page, `(() => {
    ${deepHelpers}
    const host = deepAll().find(element =>
        element.localName === 'settings-default-browser-page');
    host.canPin_ = false;
    const button = host.shadowRoot.querySelector('cr-button');
    button.click();
    return {clicked: true, text: button.innerText.trim()};
  })()`);
  await delay(1800);
  const alivePage = await evaluate(page, `({
    href: location.href,
    readyState: document.readyState,
    title: document.title,
  })`);
  const aliveProcesses = await browserControl.send('SystemInfo.getProcessInfo');
  const alivePid = aliveProcesses.processInfo.find(
      item => item.type === 'browser')?.id;
  assert.equal(alivePid, browserPid);
  assert.equal(alivePage.readyState, 'complete');
  assert.match(alivePage.href, /^chrome:\/\/settings\/defaultBrowser/);
  report.artifacts.push(await screenshot(page, '10-settings-default-after'));
  report.settings.defaultBrowser = {
    nativeState: nativeDefaultState,
    conditionalButtonExposedForRegression:
        !nativeDefaultState.maySet,
    before: defaultBefore,
    clickedOnce: clicked.clicked,
    browserPidBefore: browserPid,
    browserPidAfter: alivePid,
    pageAfter: alivePage,
    processAndPageAlive: true,
    systemSelectionPerformed: false,
  };

  assert.deepEqual(report.runtimeExceptions, [],
                   report.runtimeExceptions.join('\n'));
  report.status = 'PASS';
  fs.writeFileSync(artifact('report.json'),
                   `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify(report));
} catch (error) {
  report.status = 'FAIL';
  report.error = error.stack || String(error);
  fs.writeFileSync(artifact('report.json'),
                   `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  throw error;
} finally {
  if (browserControl?.socket.readyState === WebSocket.OPEN) {
    try {
      await Promise.race([browserControl.send('Browser.close'), delay(1500)]);
    } catch {}
    await delay(350);
  }
  if (page?.socket.readyState === WebSocket.OPEN) page.socket.close();
  if (browserControl?.socket.readyState === WebSocket.OPEN) {
    browserControl.socket.close();
  }
  if (browserPid || browser.exitCode === null) {
    spawnSync('taskkill', [
      '/PID', String(browserPid || browser.pid), '/T', '/F',
    ], {stdio: 'ignore', windowsHide: true});
  }
  const tempRoot = path.resolve(os.tmpdir());
  const resolvedProfile = path.resolve(profileDir);
  if (resolvedProfile.startsWith(`${tempRoot}${path.sep}`)) {
    await delay(250);
    fs.rmSync(resolvedProfile, {
      recursive: true, force: true, maxRetries: 10, retryDelay: 100,
    });
  }
}
