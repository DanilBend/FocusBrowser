#!/usr/bin/env node

// Runtime QA for the Focus new-tab customization panel. This script attaches
// to an already-running disposable browser and never closes the browser.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const port = Number(process.argv[2] || 9342);
const outputDirectory = path.resolve(process.argv[3] || 'qa');
assert.ok(Number.isInteger(port) && port > 0 && port <= 65535,
          'A valid remote-debugging port is required');
fs.mkdirSync(outputDirectory, {recursive: true});

const endpoint = `http://127.0.0.1:${port}`;
const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitFor(probe, description, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await probe();
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(75);
  }
  const suffix = lastError ? ` Last error: ${lastError.message}` : '';
  throw new Error(`Timed out waiting for ${description}.${suffix}`);
}

class CdpSession {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.webSocketUrl);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, {once: true});
      this.socket.addEventListener('error', reject, {once: true});
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (!pending) {
        return;
      }
      this.pending.delete(message.id);
      if (message.error) {
        pending.reject(new Error(
            `${message.error.code}: ${message.error.message}`));
      } else {
        pending.resolve(message.result ?? {});
      }
    });
    this.socket.addEventListener('close', () => {
      for (const {reject} of this.pending.values()) {
        reject(new Error('DevTools WebSocket closed'));
      }
      this.pending.clear();
    });
  }

  send(method, params = {}) {
    assert.equal(this.socket?.readyState, WebSocket.OPEN,
                 'DevTools WebSocket is not open');
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
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
    throw new Error(response.exceptionDetails.exception?.description ||
                    response.exceptionDetails.text ||
                    'Runtime evaluation failed');
  }
  return response.result?.value;
}

async function listTargets() {
  const response = await fetch(`${endpoint}/json/list`);
  assert.ok(response.ok, `DevTools target list returned ${response.status}`);
  return response.json();
}

async function capture(session, filename) {
  const result = await session.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    fromSurface: true,
  });
  const destination = path.join(outputDirectory, filename);
  fs.writeFileSync(destination, Buffer.from(result.data, 'base64'));
  return destination;
}

const ntpStateExpression = `(() => {
  const app = document.querySelector('ntp-app');
  const root = app?.shadowRoot;
  const home = root?.querySelector('#focusHome');
  const search = root?.querySelector('#focusSearch ntp-searchbox');
  const mostVisited = root?.querySelector('#mostVisited');
  const mostVisitedRoot = mostVisited?.shadowRoot;
  const addShortcut = mostVisitedRoot?.querySelector('#addShortcut');
  if (!app || !root || !home || !search || !mostVisited ||
      !mostVisitedRoot) {
    return null;
  }
  const visible = element => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
        style.visibility !== 'hidden' && Number(style.opacity) !== 0;
  };
  const pinnedTiles = [...mostVisitedRoot.querySelectorAll('.tile')]
      .filter(tile => tile.id !== 'addShortcut')
      .map(tile => ({
        id: tile.id,
        title: tile.getAttribute('aria-label') || tile.textContent?.trim() || '',
        visible: visible(tile),
      }));
  const homeRect = home.getBoundingClientRect();
  return {
    href: location.href,
    innerWidth,
    innerHeight,
    documentWidth: document.documentElement.clientWidth,
    backgroundColor: getComputedStyle(document.documentElement).backgroundColor,
    homeRect: {
      x: homeRect.x,
      y: homeRect.y,
      width: homeRect.width,
      height: homeRect.height,
    },
    searchVisible: visible(search),
    mostVisitedPresent: Boolean(mostVisited),
    addShortcutPresent: Boolean(addShortcut),
    addShortcutVisible: visible(addShortcut),
    pinnedTiles,
  };
})()`;

const panelStateExpression = `(() => {
  const app = document.querySelector('customize-chrome-app');
  const root = app?.shadowRoot;
  const shortcuts = root?.querySelector('customize-chrome-shortcuts');
  const shortcutsRoot = shortcuts?.shadowRoot;
  const toggle = shortcutsRoot?.querySelector('#addShortcutToggle');
  const container = shortcutsRoot?.querySelector('#addShortcutContainer');
  if (!app || !root || !shortcuts || !shortcutsRoot || !toggle ||
      !container) {
    return null;
  }
  const rect = container.getBoundingClientRect();
  return {
    href: location.href,
    checked: toggle.checked,
    title: toggle.title,
    containerText: container.textContent?.replace(/\\s+/g, ' ').trim() || '',
    containerRect: {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    },
  };
})()`;

let ntpSession = null;
let panelSession = null;
let report = null;
let primaryError = null;

try {
  const ntpTarget = await waitFor(async () => {
    const targets = await listTargets();
    return targets.find(target =>
      target.type === 'page' &&
      (target.url.startsWith('chrome://new-tab-page/') ||
       target.url.startsWith('chrome://newtab/')) &&
      target.webSocketDebuggerUrl);
  }, 'an existing Focus new-tab target');

  ntpSession = new CdpSession(ntpTarget.webSocketDebuggerUrl);
  await ntpSession.connect();
  await ntpSession.send('Page.enable');
  await ntpSession.send('Runtime.enable');

  const before = await waitFor(
      () => evaluate(ntpSession, ntpStateExpression), 'the Focus new-tab DOM');
  const screenshots = {};
  screenshots.default = await capture(ntpSession, 'focus-ntp-default.png');

  await ntpSession.send('Emulation.setEmulatedMedia', {
    media: '',
    features: [{name: 'prefers-color-scheme', value: 'dark'}],
  });
  await evaluate(ntpSession, `new Promise(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
  const darkState = await evaluate(ntpSession, ntpStateExpression);
  screenshots.dark = await capture(ntpSession, 'focus-ntp-dark.png');
  await ntpSession.send('Emulation.setEmulatedMedia', {
    media: '',
    features: [],
  });

  const prepared = await evaluate(ntpSession, `(() => {
    const app = document.querySelector('ntp-app');
    const searchbox = app?.shadowRoot?.querySelector(
        '#focusSearch ntp-searchbox');
    const searchRoot = searchbox?.shadowRoot;
    const inputHost = searchRoot?.querySelector('#input');
    const input = inputHost?.shadowRoot?.querySelector('#input');
    if (!input) return null;
    const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value')?.set;
    setter?.call(input, '');
    input.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      composed: true,
      inputType: 'deleteContentBackward',
      data: null,
    }));
    input.focus();
    return true;
  })()`);
  assert.equal(prepared, true, 'Could not prepare the NTP search input');
  await ntpSession.send('Input.insertText', {
    text: 'focus suggestions geometry',
  });

  const suggestions = await waitFor(() => evaluate(ntpSession, `(() => {
    const app = document.querySelector('ntp-app');
    const searchbox = app?.shadowRoot?.querySelector(
        '#focusSearch ntp-searchbox');
    const root = searchbox?.shadowRoot;
    const inputHost = root?.querySelector('#input');
    const input = inputHost?.shadowRoot?.querySelector('#input');
    const list = root?.querySelector('.dropdownContainer');
    const matches = root?.querySelector('#matches');
    const row = matches?.shadowRoot?.querySelector('cr-searchbox-match');
    if (!input || !list || !matches || !row || matches.hidden) return null;
    const activeAnimations = [...list.getAnimations(), ...row.getAnimations()]
        .filter(animation => animation.playState === 'running' ||
            animation.playState === 'pending');
    if (activeAnimations.length) return null;
    const searchboxRect = searchbox.getBoundingClientRect();
    const inputRect = input.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    const rowRect = row.getBoundingClientRect();
    const animationsDisabled =
        getComputedStyle(app)
            .getPropertyValue('--cr-animations-disabled').trim() === '1';
    return {
      animationsDisabled,
      searchboxRect: {
        x: searchboxRect.x, y: searchboxRect.y,
        width: searchboxRect.width, height: searchboxRect.height,
      },
      inputRect: {
        x: inputRect.x, y: inputRect.y,
        width: inputRect.width, height: inputRect.height,
      },
      listRect: {
        x: listRect.x, y: listRect.y,
        width: listRect.width, height: listRect.height,
      },
      rowRect: {
        x: rowRect.x, y: rowRect.y,
        width: rowRect.width, height: rowRect.height,
      },
      listAnimationName: getComputedStyle(list).animationName,
      rowAnimationName: getComputedStyle(row).animationName,
      listBackground: getComputedStyle(list).backgroundColor,
      rowBackground: getComputedStyle(row).backgroundColor,
    };
  })()`), 'the NTP suggestions dropdown');
  screenshots.suggestions =
      await capture(ntpSession, 'focus-ntp-suggestions.png');

  const suggestionsChecks = {
    listBelowInput:
        suggestions.listRect.y >= suggestions.inputRect.y +
            suggestions.inputRect.height - 1,
    listAlignedWithInput:
        Math.abs(suggestions.listRect.x - suggestions.searchboxRect.x) <= 2 &&
        Math.abs(suggestions.listRect.width -
                 suggestions.searchboxRect.width) <= 2,
    firstRowInsideList:
        suggestions.rowRect.x >= suggestions.listRect.x - 1 &&
        suggestions.rowRect.y >= suggestions.listRect.y - 1 &&
        suggestions.rowRect.x + suggestions.rowRect.width <=
            suggestions.listRect.x + suggestions.listRect.width + 1,
    animationsMatchState: suggestions.animationsDisabled ?
        suggestions.listAnimationName === 'none' &&
            suggestions.rowAnimationName === 'none' :
        suggestions.listAnimationName === 'focus-ntp-suggestions-enter' &&
            suggestions.rowAnimationName ===
                'focus-ntp-suggestion-row-enter',
  };

  await evaluate(ntpSession, `(() => {
    const app = document.querySelector('ntp-app');
    const searchbox = app?.shadowRoot?.querySelector(
        '#focusSearch ntp-searchbox');
    const input = searchbox?.shadowRoot?.querySelector('#input')
        ?.shadowRoot?.querySelector('#input');
    if (!input) return false;
    const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value')?.set;
    setter?.call(input, '');
    input.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      composed: true,
      inputType: 'deleteContentBackward',
      data: null,
    }));
    input.blur();
    return true;
  })()`);

  const pencilClicked = await evaluate(ntpSession, `(() => {
    const root = document.querySelector('ntp-app')?.shadowRoot;
    const roots = root ? [root] : [];
    while (roots.length) {
      const current = roots.pop();
      const button = current.querySelector('#customizeButton');
      if (button) {
        button.click();
        return true;
      }
      for (const element of current.querySelectorAll('*')) {
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
    }
    return false;
  })()`);
  assert.equal(pencilClicked, true, 'Could not click the NTP pencil');

  const panelTarget = await waitFor(async () => {
    const targets = await listTargets();
    return targets.find(target =>
      target.url.includes('customize-chrome-side-panel') &&
      target.webSocketDebuggerUrl);
  }, 'the Customize Chrome side-panel target');
  panelSession = new CdpSession(panelTarget.webSocketDebuggerUrl);
  await panelSession.connect();
  await panelSession.send('Page.enable');
  await panelSession.send('Runtime.enable');

  const panelOn = await waitFor(
      () => evaluate(panelSession, panelStateExpression),
      'the Add shortcut panel toggle');
  const open = await waitFor(async () => {
    const state = await evaluate(ntpSession, ntpStateExpression);
    return state && state.innerWidth < before.innerWidth - 100 ? state : null;
  }, 'the NTP viewport to resize for the panel');
  screenshots.panelOn =
      await capture(panelSession, 'focus-customize-panel-shortcut-on.png');
  screenshots.ntpPanelOn =
      await capture(ntpSession, 'focus-ntp-panel-shortcut-on.png');

  assert.equal(panelOn.checked, true,
               'Add shortcut was not enabled before the toggle test');
  const toggledOff = await evaluate(panelSession, `(() => {
    const shortcuts = document.querySelector('customize-chrome-app')
        ?.shadowRoot?.querySelector('customize-chrome-shortcuts');
    const toggle = shortcuts?.shadowRoot?.querySelector('#addShortcutToggle');
    if (!toggle || !toggle.checked) return false;
    toggle.click();
    return true;
  })()`);
  assert.equal(toggledOff, true, 'Could not turn Add shortcut off');
  const panelOff = await waitFor(async () => {
    const state = await evaluate(panelSession, panelStateExpression);
    return state && !state.checked ? state : null;
  }, 'the panel toggle to turn off');
  const off = await waitFor(async () => {
    const state = await evaluate(ntpSession, ntpStateExpression);
    return state && !state.addShortcutVisible ? state : null;
  }, 'the NTP Add shortcut tile to disappear');
  screenshots.panelOff =
      await capture(panelSession, 'focus-customize-panel-shortcut-off.png');
  screenshots.ntpPanelOff =
      await capture(ntpSession, 'focus-ntp-panel-shortcut-off.png');

  const toggledOn = await evaluate(panelSession, `(() => {
    const shortcuts = document.querySelector('customize-chrome-app')
        ?.shadowRoot?.querySelector('customize-chrome-shortcuts');
    const toggle = shortcuts?.shadowRoot?.querySelector('#addShortcutToggle');
    if (!toggle || toggle.checked) return false;
    toggle.click();
    return true;
  })()`);
  assert.equal(toggledOn, true, 'Could not turn Add shortcut on');
  const panelRestored = await waitFor(async () => {
    const state = await evaluate(panelSession, panelStateExpression);
    return state?.checked ? state : null;
  }, 'the panel toggle to turn on');
  const restored = await waitFor(async () => {
    const state = await evaluate(ntpSession, ntpStateExpression);
    return state?.addShortcutVisible ? state : null;
  }, 'the NTP Add shortcut tile to return');

  const tileChecks = {
    russianToggleLabel:
        panelOn.title === 'Добавить ярлык' ||
        panelOn.containerText.includes('Добавить ярлык'),
    offOnlyRemovesAddTile:
        before.searchVisible && off.searchVisible &&
        before.mostVisitedPresent && off.mostVisitedPresent &&
        before.addShortcutVisible && !off.addShortcutVisible &&
        JSON.stringify(before.pinnedTiles) === JSON.stringify(off.pinnedTiles),
    restoredExactly:
        restored.searchVisible === before.searchVisible &&
        restored.mostVisitedPresent === before.mostVisitedPresent &&
        restored.addShortcutPresent === before.addShortcutPresent &&
        restored.addShortcutVisible === before.addShortcutVisible &&
        JSON.stringify(restored.pinnedTiles) ===
            JSON.stringify(before.pinnedTiles) &&
        panelRestored.checked,
  };

  await ntpSession.send('Page.navigate', {url: 'about:blank'});
  const afterNavigation = await waitFor(async () => {
    try {
      const state = await evaluate(ntpSession, `(() => ({
        href: location.href,
        innerWidth,
        innerHeight,
        documentWidth: document.documentElement.clientWidth,
      }))()`);
      return state.href === 'about:blank' &&
          state.innerWidth >= before.innerWidth - 2 ? state : null;
    } catch {
      return null;
    }
  }, 'navigation to close the NTP-opened panel and restore full width');

  const viewportChecks = {
    panelReducedViewport: open.innerWidth < before.innerWidth - 100,
    navigationRestoredViewport:
        afterNavigation.innerWidth >= before.innerWidth - 2,
    documentWidthMatches:
        afterNavigation.documentWidth === afterNavigation.innerWidth,
  };

  const checks = {
    ...suggestionsChecks,
    ...tileChecks,
    ...viewportChecks,
  };
  assert.ok(Object.values(checks).every(Boolean), JSON.stringify({
    checks,
    before,
    darkState,
    suggestions,
    panelOn,
    panelOff,
    open,
    off,
    restored,
    afterNavigation,
  }));

  report = {
    status: 'PASS',
    remoteDebuggingPort: port,
    checks,
    geometry: {before, open, afterNavigation},
    suggestions,
    panel: {on: panelOn, off: panelOff, restored: panelRestored},
    screenshots,
  };
} catch (error) {
  primaryError = error;
  report = {
    status: 'FAIL',
    remoteDebuggingPort: port,
    error: error.stack || String(error),
  };
} finally {
  panelSession?.close();
  ntpSession?.close();
}

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (primaryError) {
  process.exitCode = 1;
}
