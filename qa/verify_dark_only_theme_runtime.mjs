#!/usr/bin/env node

import assert from 'node:assert/strict';
import fs from 'node:fs';

const port = Number(process.argv[2] || 9351);
const preferencesPath = process.argv[3];
const endpoint = `http://127.0.0.1:${port}`;
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

class Session {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, {once: true});
      this.socket.addEventListener('error', reject, {once: true});
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      const request = this.pending.get(message.id);
      if (!request) return;
      this.pending.delete(message.id);
      if (message.error) request.reject(new Error(message.error.message));
      else request.resolve(message.result ?? {});
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  close() {
    if (this.socket.readyState === WebSocket.OPEN) this.socket.close();
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

function rgbLuminance(value) {
  const match = value?.match(/^rgba?\((\d+)[,\s]+(\d+)[,\s]+(\d+)/i);
  assert.ok(match, `Could not parse color: ${value}`);
  return 0.2126 * Number(match[1]) + 0.7152 * Number(match[2]) +
      0.0722 * Number(match[3]);
}

const targets = await fetch(`${endpoint}/json/list`).then(response => response.json());
const panelTarget = targets.find(target =>
    target.type === 'browser_ui' &&
    target.url.includes('customize-chrome-side-panel') &&
    target.webSocketDebuggerUrl);
const ntpTarget = targets.find(target => target.type === 'page' &&
    (target.url.startsWith('chrome://newtab/') ||
     target.url.startsWith('chrome://new-tab-page/')) &&
    target.webSocketDebuggerUrl);
assert.ok(panelTarget, 'The real customize side panel is not open');
assert.ok(ntpTarget, 'A Focus new-tab page is not open');

const panel = new Session(panelTarget.webSocketDebuggerUrl);
const ntp = new Session(ntpTarget.webSocketDebuggerUrl);
await Promise.all([panel.connect(), ntp.connect()]);

try {
  const panelBefore = await evaluate(panel, `(() => {
    const app = document.querySelector('customize-chrome-app');
    if (!app?.shadowRoot) return null;
    const roots = [document];
    for (let i = 0; i < roots.length; ++i) {
      for (const element of roots[i].querySelectorAll('*')) {
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
    }
    const find = selector => roots.flatMap(root =>
      [...root.querySelectorAll(selector)])[0];
    const wallpaper = find('#editThemeButton');
    return {
      background: getComputedStyle(document.documentElement).backgroundColor,
      colorScheme: getComputedStyle(document.documentElement).colorScheme,
      lightDarkSelector: Boolean(find('customize-color-scheme-mode')),
      accentPicker: Boolean(find('cr-theme-color-picker')),
      followDevice: Boolean(find('#followThemeToggle')),
      wallpaperPresent: Boolean(wallpaper),
      wallpaperVisible: Boolean(wallpaper && !wallpaper.hidden &&
          getComputedStyle(wallpaper).display !== 'none'),
      wallpaperLabel: wallpaper?.innerText?.trim() || '',
    };
  })()`);
  assert.ok(panelBefore, 'Customize side panel did not finish loading');
  assert.ok(rgbLuminance(panelBefore.background) < 128, panelBefore.background);
  assert.equal(panelBefore.lightDarkSelector, false);
  assert.equal(panelBefore.accentPicker, false);
  assert.equal(panelBefore.followDevice, false);
  assert.equal(panelBefore.wallpaperPresent, true);
  assert.equal(panelBefore.wallpaperVisible, true);
  assert.ok(panelBefore.wallpaperLabel.length > 0);

  const ntpBefore = await evaluate(ntp, `(() => ({
    background: getComputedStyle(document.documentElement).backgroundColor,
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
  }))()`);
  assert.ok(rgbLuminance(ntpBefore.background) < 128, ntpBefore.background);

  const legacyLightAttempt = await evaluate(panel, `(async () => {
    const module = await import(
        'chrome://resources/cr_components/customize_color_scheme_mode/browser_proxy.js');
    await module.CustomizeColorSchemeModeBrowserProxy.getInstance()
        .handler.setColorSchemeMode(1);
    await new Promise(resolve => setTimeout(resolve, 300));
    return getComputedStyle(document.documentElement).backgroundColor;
  })()`);
  assert.ok(rgbLuminance(legacyLightAttempt) < 128, legacyLightAttempt);

  let storedColorScheme = null;
  if (preferencesPath) {
    for (let attempt = 0; attempt < 40; ++attempt) {
      try {
        const preferences = JSON.parse(fs.readFileSync(preferencesPath, 'utf8'));
        storedColorScheme = preferences?.browser?.theme?.color_scheme2;
        if (storedColorScheme === 2) break;
      } catch {}
      await delay(50);
    }
    assert.equal(storedColorScheme, 2,
                 'Legacy Light request changed the stored dark scheme');
  }

  const report = {
    panelBefore,
    ntpBefore,
    legacyLightAttempt,
    storedColorScheme,
  };
  console.log(JSON.stringify(report, null, 2));
} finally {
  panel.close();
  ntp.close();
}
