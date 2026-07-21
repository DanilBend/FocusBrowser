#!/usr/bin/env node

// Runtime smoke test for the built-in Focus Text Motion component. It uses a
// disposable profile and a local fixture only.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import {createServer} from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..');
const chromePath = path.resolve(
    process.argv[2] ||
    path.join(projectRoot, 'build', 'src', 'out', 'Default', 'chrome.exe'));
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;

assert.ok(fs.existsSync(chromePath), [
  'Focus Browser executable was not found.',
  'Usage: node qa/verify_focus_text_motion_runtime.mjs <chrome.exe> [report.json]',
].join('\n'));

const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitFor(probe, description, timeoutMs = 30000) {
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
    await delay(20);
  }
  throw new Error(
      `Timed out waiting for ${description}` +
      (lastError ? `: ${lastError.message}` : ''));
}

class CdpSession {
  constructor(url) {
    this.url = url;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.url);
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
        pending.reject(new Error(message.error.message));
      } else {
        pending.resolve(message.result ?? {});
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
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, {resolve, reject});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }

  close() {
    this.socket?.close();
  }
}

const fixtureHtml = `<!doctype html>
<meta charset="utf-8">
<title>Focus Text Motion QA</title>
<style>
  body { font: 18px/1.4 system-ui; padding: 20px; }
  input, textarea, [contenteditable] {
    display: block; width: 460px; min-height: 32px; margin: 12px;
  }
  iframe { width: 520px; height: 100px; }
</style>
<input id="text" type="text">
<input id="search" type="search">
<input id="email" type="email">
<input id="url" type="url">
<input id="tel" type="tel">
<input id="number" type="number">
<input id="password" type="password">
<textarea id="textarea"></textarea>
<div id="editable" contenteditable="true"></div>
<div id="ime" contenteditable="true"></div>
<iframe id="frame" src="/frame"></iframe>`;

const frameHtml = `<!doctype html><meta charset="utf-8">
<input id="frameInput" type="text" style="font:18px system-ui;width:420px">`;

const server = createServer((request, response) => {
  response.writeHead(200, {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(request.url === '/frame' ? frameHtml : fixtureHtml);
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

const address = server.address();
assert.ok(address && typeof address !== 'string');
const fixtureUrl = `http://127.0.0.1:${address.port}/`;
const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-text-motion-qa-'));
const devToolsPortFile = path.join(profileDir, 'DevToolsActivePort');
const browser = spawn(chromePath, [
  `--user-data-dir=${profileDir}`,
  '--remote-debugging-port=0',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-component-update',
  fixtureUrl,
], {stdio: 'ignore'});

let session = null;
let workerSession = null;
let originalMotionPreference = null;
const results = [];

async function evaluateIn(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || 'Runtime exception');
  }
  return result.result?.value;
}

const evaluate = expression => evaluateIn(session, expression);

async function readMotionPreference() {
  return workerEvaluate(`new Promise(resolve =>
    chrome.settingsPrivate.getPref(
        'focus.ui.motion_enabled', pref => resolve(pref?.value === true)))`);
}

async function setMotionPreference(enabled) {
  const success = await workerEvaluate(`new Promise(resolve =>
    chrome.settingsPrivate.setPref(
        'focus.ui.motion_enabled', ${enabled}, '', resolve))`);
  assert.equal(success, true, `failed to set motion preference to ${enabled}`);
  await waitFor(
      async () => (await readMotionPreference()) === enabled,
      `native motion preference=${enabled}`, 3000);
}

const workerEvaluate = expression => evaluateIn(workerSession, expression);

const mainActive = () => evaluate(`Number(
  document.querySelector('[data-focus-text-motion]')?.
      getAttribute('data-focus-motion-active') || 0)`);
const frameActive = () => evaluate(`Number(
  document.querySelector('#frame').contentDocument.
      querySelector('[data-focus-text-motion]')?.
      getAttribute('data-focus-motion-active') || 0)`);

async function typeAndObserve(selector, text, activeProbe = mainActive) {
  await evaluate(`(() => {
    const target = ${selector};
    target.focus();
    if ('value' in target) target.value = '';
    else target.textContent = '';
  })()`);
  await session.send('Input.insertText', {text});
  const count = await waitFor(
      async () => (await activeProbe()) > 0 ? await activeProbe() : 0,
      `glyph animation for ${selector}`, 3000);
  results.push({target: selector, activeGlyphs: count});
}

try {
  await waitFor(
      () => fs.existsSync(devToolsPortFile) &&
        fs.readFileSync(devToolsPortFile, 'utf8').trim(),
      'DevToolsActivePort');
  const [port] = fs.readFileSync(devToolsPortFile, 'utf8')
      .trim().split(/\r?\n/);
  const pageTarget = await waitFor(async () => {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    return targets.find(target =>
      target.type === 'page' && target.url.startsWith(fixtureUrl));
  }, 'local fixture page');

  session = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await session.connect();
  await session.send('Runtime.enable');
  await session.send('Page.enable');
  await waitFor(
      () => evaluate("document.readyState === 'complete'"),
      'fixture document');
  await delay(500);

  const workerTarget = await waitFor(async () => {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    return targets.find(target =>
      target.type === 'service_worker' &&
      target.url ===
          'chrome-extension://ajekofejbbjbbkdfnlghakcilbfdmofc/background.js');
  }, 'Focus Text Motion service worker', 5000);
  workerSession = new CdpSession(workerTarget.webSocketDebuggerUrl);
  await workerSession.connect();
  await workerSession.send('Runtime.enable');
  originalMotionPreference = await readMotionPreference();
  assert.equal(originalMotionPreference, true,
               'fresh profile motion preference must default to true');

  for (const [selector, text] of [
    ["document.querySelector('#text')", 'Focus'],
    ["document.querySelector('#search')", 'search'],
    ["document.querySelector('#email')", 'a@b.co'],
    ["document.querySelector('#url')", 'https://focus.test'],
    ["document.querySelector('#tel')", '+373'],
    ["document.querySelector('#number')", '42'],
    ["document.querySelector('#textarea')", 'water'],
    ["document.querySelector('#editable')", 'flow'],
  ]) {
    await typeAndObserve(selector, text);
    if (selector === "document.querySelector('#text')") {
      const overlayGeometry = await evaluate(`(() => {
        const host = document.querySelector('[data-focus-text-motion]');
        const rect = host.getBoundingClientRect();
        const style = getComputedStyle(host);
        return {
          display: style.display,
          visibility: style.visibility,
          width: rect.width,
          height: rect.height,
          viewportWidth: innerWidth,
          viewportHeight: innerHeight,
        };
      })()`);
      assert.notEqual(overlayGeometry.display, 'none');
      assert.equal(overlayGeometry.visibility, 'visible');
      assert.ok(overlayGeometry.width >= overlayGeometry.viewportWidth - 1);
      assert.ok(overlayGeometry.height >= overlayGeometry.viewportHeight - 1);
      results.push({target: 'overlay viewport', ...overlayGeometry});
    }
    await delay(380);
  }

  await typeAndObserve(
      "document.querySelector('#password')", 'private');
  const passwordResult = results.at(-1);
  assert.equal(passwordResult.activeGlyphs, 1,
               'password must animate one generic bullet only');
  assert.equal(await evaluate(`document.querySelector(
      '[data-focus-text-motion]').shadowRoot === null`), true,
  'animation overlay must use a closed shadow root');
  await delay(380);

  await typeAndObserve(
      "document.querySelector('#frame').contentDocument.querySelector('#frameInput')",
      'frame', frameActive);
  await delay(380);

  await evaluate(`(() => {
    const target = document.querySelector('#ime');
    target.focus();
    target.dispatchEvent(new CompositionEvent(
        'compositionstart', {bubbles: true, data: ''}));
    target.textContent = 'Ж';
    const range = document.createRange();
    range.selectNodeContents(target);
    range.collapse(false);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    target.dispatchEvent(new CompositionEvent(
        'compositionend', {bubbles: true, data: 'Ж'}));
  })()`);
  const imeCount = await waitFor(
      async () => (await mainActive()) > 0 ? await mainActive() : 0,
      'IME commit glyph animation', 3000);
  results.push({target: 'IME composition commit', activeGlyphs: imeCount});
  await delay(380);

  await setMotionPreference(false);
  await delay(100);
  assert.equal(await mainActive(), 0);
  await evaluate(`(() => {
    const target = document.querySelector('#text');
    target.value = '';
    target.focus();
  })()`);
  await session.send('Input.insertText', {text: 'disabled'});
  await delay(120);
  assert.equal(await mainActive(), 0,
               'native pref=false must suppress glyph animation live');
  results.push({target: 'native pref=false (live)', activeGlyphs: 0});

  await setMotionPreference(true);
  await delay(100);
  await evaluate(`(() => {
    const target = document.querySelector('#text');
    target.value = '';
    target.focus();
  })()`);
  await session.send('Input.insertText', {text: 'enabled'});
  const liveEnabledCount = await waitFor(
      async () => (await mainActive()) > 0 ? await mainActive() : 0,
      'native pref=true live glyph animation', 3000);
  results.push({
    target: 'native pref=true (live)',
    activeGlyphs: liveEnabledCount,
  });
  await delay(380);

  await session.send('Emulation.setEmulatedMedia', {
    features: [{name: 'prefers-reduced-motion', value: 'reduce'}],
  });
  await delay(100);
  await evaluate("document.querySelector('#text').focus()");
  await session.send('Input.insertText', {text: 'x'});
  await delay(100);
  assert.equal(await mainActive(), 0,
               'reduced-motion must suppress glyph animation');
  results.push({target: 'prefers-reduced-motion', activeGlyphs: 0});

  const report = {
    ok: true,
    executable: chromePath,
    fixture: 'local HTTP only',
    nativePreferenceLiveToggle: 'false => 0, true => animated',
    cleanup: 'Browser.close then exact spawned PID tree fallback',
    results,
  };
  if (reportPath) {
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
} finally {
  if (workerSession && typeof originalMotionPreference === 'boolean') {
    try {
      await setMotionPreference(originalMotionPreference);
    } catch {
      // The disposable profile is removed below even if the worker exited.
    }
  }
  workerSession?.close();
  if (session) {
    try {
      await Promise.race([
        session.send('Browser.close'),
        delay(1500),
      ]);
    } catch {
      // Fall through to the exact-PID tree cleanup below.
    }
    session.close();
  }
  await Promise.race([
    new Promise(resolve => browser.once('exit', resolve)),
    delay(3000),
  ]);
  if (browser.exitCode === null && browser.signalCode === null && browser.pid) {
    const cleanup = spawnSync(
        'taskkill', ['/PID', String(browser.pid), '/T', '/F'], {
      stdio: 'ignore',
    });
    if (cleanup.error) {
      browser.kill('SIGKILL');
    }
  }
  await new Promise(resolve => server.close(resolve));
  await delay(200);
  fs.rmSync(profileDir, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  });
}
