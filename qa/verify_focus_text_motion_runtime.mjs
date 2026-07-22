#!/usr/bin/env node

// Runtime smoke test for Blink-native Focus text edit motion. It covers single
// insertion, a multi-grapheme paste payload, Backspace, and Delete using a
// disposable profile, a local fixture, DOM/caret probes, and screenshot hashes.
// No page overlay, extension worker, network service, OS clipboard, or
// persistent browser profile is involved.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import crypto from 'node:crypto';
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
const digest = base64 => crypto.createHash('sha256')
    .update(Buffer.from(base64, 'base64')).digest('hex');

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
<title>Focus native text reveal QA</title>
<style>
  * { animation: none !important; transition: none !important; }
  html, body { margin: 0; background: #181a1b; color: white; }
  body { padding: 20px; }
  .qa {
    box-sizing: border-box;
    display: block;
    width: 420px;
    height: 64px;
    margin: 12px;
    border: 0;
    border-radius: 0;
    padding: 10px 14px;
    outline: 0;
    overflow: hidden;
    background: #101112;
    color: #f4f4f4;
    caret-color: transparent;
    font: 42px/44px Consolas, monospace;
    white-space: pre;
  }
  textarea.qa { resize: none; }
  iframe { display: block; width: 470px; height: 100px; border: 0; }
</style>
<input id="input" class="qa" autocomplete="off" spellcheck="false">
<input id="password" type="password" class="qa" autocomplete="new-password">
<textarea id="textarea" class="qa" autocomplete="off" spellcheck="false"></textarea>
<div id="editable" class="qa" contenteditable="true" spellcheck="false"></div>
<div id="openHost"></div>
<div id="closedHost"></div>
<iframe id="frame" src="/frame"></iframe>
<script>
  const prefix = 'Stable';

  const openRoot = document.querySelector('#openHost').attachShadow({mode: 'open'});
  openRoot.innerHTML = '<style>.qa{box-sizing:border-box;display:block;width:420px;height:64px;margin:12px;border:0;border-radius:0;padding:10px 14px;outline:0;overflow:hidden;background:#101112;color:#f4f4f4;caret-color:transparent;font:42px/44px Consolas,monospace;white-space:pre}</style><input id="input" class="qa" autocomplete="off" spellcheck="false">';

  const closedRoot = document.querySelector('#closedHost').attachShadow({mode: 'closed'});
  closedRoot.innerHTML = openRoot.innerHTML;
  const closedInput = closedRoot.querySelector('#input');

  function prepareInput(element, value = prefix, caret = value.length) {
    // Glyph stability is measured here; caret travel has its own runtime QA.
    element.style.caretColor = 'transparent';
    element.value = value;
    element.focus();
    element.setSelectionRange(caret, caret);
  }

  function prepareEditable(element, value = prefix, caret = value.length) {
    // Keep the animated caret out of glyph-only screenshot hashes.
    element.style.caretColor = 'transparent';
    element.textContent = value;
    element.focus();
    const range = document.createRange();
    const text = element.firstChild;
    if (text) {
      range.setStart(text, Math.min(caret, text.length));
    } else {
      range.setStart(element, 0);
    }
    range.collapse(true);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function selectionOffset(element, node, offset) {
    if (!node || (node !== element && !element.contains(node))) {
      return null;
    }
    const range = element.ownerDocument.createRange();
    range.selectNodeContents(element);
    range.setEnd(node, offset);
    return range.toString().length;
  }

  function state(element) {
    const rect = element.getBoundingClientRect();
    if ('value' in element && typeof element.selectionStart === 'number') {
      return {
        value: element.value,
        selectionStart: element.selectionStart,
        selectionEnd: element.selectionEnd,
        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      };
    }
    const selection = element.ownerDocument.getSelection();
    return {
      value: element.textContent,
      selectionStart: selectionOffset(
          element, selection?.anchorNode, selection?.anchorOffset || 0),
      selectionEnd: selectionOffset(
          element, selection?.focusNode, selection?.focusOffset || 0),
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
  }

  function geometry(element, frameElement = null, stablePrefix = prefix) {
    const rect = element.getBoundingClientRect();
    const frameRect = frameElement?.getBoundingClientRect() || {left: 0, top: 0};
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = style.font;
    const prefixWidth = context.measureText(stablePrefix).width;
    const left = frameRect.left + rect.left;
    const top = frameRect.top + rect.top;
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    return {
      full: {
        x: Math.floor(left),
        y: Math.floor(top),
        width: Math.ceil(rect.width),
        height: Math.ceil(rect.height),
      },
      // Exclude the final two pixels to avoid antialiasing from the newly
      // inserted glyph at the range boundary.
      prefix: {
        x: Math.floor(left + paddingLeft),
        y: Math.floor(top + 4),
        width: Math.max(1, Math.floor(prefixWidth - 2)),
        height: Math.max(1, Math.ceil(rect.height - 8)),
      },
    };
  }

  window.qa = {
    prepareInput,
    prepareEditable,
    state,
    geometry,
    openInput: openRoot.querySelector('#input'),
    closedInput,
    prefix,
  };
</script>`;

const frameHtml = `<!doctype html>
<meta charset="utf-8">
<style>
  * { animation: none !important; transition: none !important; }
  html, body { margin: 0; background: #181a1b; }
  .qa { box-sizing:border-box;display:block;width:420px;height:64px;margin:12px;border:0;border-radius:0;padding:10px 14px;outline:0;overflow:hidden;background:#101112;color:#f4f4f4;caret-color:transparent;font:42px/44px Consolas,monospace;white-space:pre; }
</style>
<input id="input" class="qa" autocomplete="off" spellcheck="false">`;

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
    path.join(os.tmpdir(), 'focus-native-text-motion-qa-'));
const devToolsPortFile = path.join(profileDir, 'DevToolsActivePort');
const browser = spawn(chromePath, [
  `--user-data-dir=${profileDir}`,
  '--remote-debugging-port=0',
  '--headless=new',
  '--window-size=900,900',
  '--force-device-scale-factor=1',
  '--hide-scrollbars',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-component-update',
  fixtureUrl,
], {stdio: 'ignore'});

let session = null;
const results = [];

async function evaluate(expression) {
  const result = await session.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || 'Runtime exception');
  }
  return result.result?.value;
}

async function capture(clip) {
  const result = await session.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
    clip: {...clip, scale: 1},
  });
  return digest(result.data);
}

async function sampleInsertion(spec) {
  await evaluate(spec.prepare);
  await delay(35);
  const geometry = await evaluate(spec.geometry);
  await session.send('Input.insertText', {text: 'F'});

  const requestedTimes = [0, 24, 55, 95, 220, 300];
  const started = Date.now();
  const samples = [];
  for (const requestedMs of requestedTimes) {
    await delay(Math.max(0, started + requestedMs - Date.now()));
    const full = await capture(geometry.full);
    const prefix = await capture(geometry.prefix);
    samples.push({elapsedMs: Date.now() - started, full, prefix});
  }

  const fullHashes = samples.map(sample => sample.full);
  const prefixHashes = samples.map(sample => sample.prefix);
  const uniqueFull = new Set(fullHashes).size;
  const uniquePrefix = new Set(prefixHashes).size;
  assert.equal(uniquePrefix, 1,
               `${spec.name}: pre-existing glyph pixels moved or faded`);
  assert.equal(uniqueFull, 1,
               `${spec.name}: committed glyph pixels were not immediately stable`);

  results.push({
    target: spec.name,
    expectedMotion: 'caret-only',
    uniqueFullFrames: uniqueFull,
    stablePrefixFrames: uniquePrefix,
    samples: samples.map(sample => ({
      elapsedMs: sample.elapsedMs,
      full: sample.full.slice(0, 12),
      prefix: sample.prefix.slice(0, 12),
    })),
  });
}

function assertTextState(state, expectedValue, expectedCaret, label) {
  assert.equal(state?.value, expectedValue, `${label}: value was not committed`);
  assert.equal(
      state?.selectionStart, expectedCaret,
      `${label}: selection start did not move immediately`);
  assert.equal(
      state?.selectionEnd, expectedCaret,
      `${label}: selection end did not move immediately`);
}

function assertStableRect(actual, expected, label) {
  for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(
        Math.abs(actual[key] - expected[key]) < 0.01,
        `${label}: field geometry changed (${key})`);
  }
}

async function dispatchEditingKey(key, code, windowsVirtualKeyCode) {
  const event = {key, code, windowsVirtualKeyCode, nativeVirtualKeyCode: windowsVirtualKeyCode};
  await session.send('Input.dispatchKeyEvent', {type: 'rawKeyDown', ...event});
  await session.send('Input.dispatchKeyEvent', {type: 'keyUp', ...event});
}

async function sampleEditMotion({
  name,
  operation,
  prepare,
  state,
  geometry,
  action,
  initialValue,
  initialCaret,
  expectedValue,
  expectedCaret,
  requestedTimes,
}) {
  await evaluate(prepare);
  await delay(35);
  const clip = await evaluate(geometry);
  const before = await evaluate(state);
  assertTextState(before, initialValue, initialCaret, `${name} initial state`);

  const started = Date.now();
  await action();
  const immediate = await evaluate(state);
  const immediateElapsedMs = Date.now() - started;
  assertTextState(
      immediate, expectedValue, expectedCaret, `${name} immediate state`);
  assertStableRect(immediate.rect, before.rect, `${name} immediate state`);

  // Capture several glyph-only frames back-to-back before scheduled sampling
  // adds latency. Any transient opacity, transform, or blur must change a hash.
  const rapidSamples = [];
  for (let index = 0; index < 3; ++index) {
    const full = await capture(clip.full);
    const currentState = await evaluate(state);
    assertTextState(
        currentState, expectedValue, expectedCaret,
        `${name} rapid frame ${index + 1}`);
    assertStableRect(
        currentState.rect, before.rect, `${name} rapid frame ${index + 1}`);
    rapidSamples.push({
      elapsedMs: Date.now() - started,
      full,
      value: currentState.value,
      caret: currentState.selectionEnd,
    });
  }

  const samples = [];
  for (const requestedMs of requestedTimes) {
    await delay(Math.max(0, started + requestedMs - Date.now()));
    const full = await capture(clip.full);
    const prefix = await capture(clip.prefix);
    const currentState = await evaluate(state);
    assertTextState(
        currentState, expectedValue, expectedCaret,
        `${name} frame at ${requestedMs} ms`);
    assertStableRect(
        currentState.rect, before.rect, `${name} frame at ${requestedMs} ms`);
    samples.push({
      elapsedMs: Date.now() - started,
      full,
      prefix,
      value: currentState.value,
      caret: currentState.selectionEnd,
    });
  }

  const fullHashes = [
    ...rapidSamples.map(sample => sample.full),
    ...samples.map(sample => sample.full),
  ];
  const prefixHashes = samples.map(sample => sample.prefix);
  const uniqueFull = new Set(fullHashes).size;
  const uniquePrefix = new Set(prefixHashes).size;
  assert.equal(
      uniquePrefix, 1,
      `${name}: stable prefix moved, faded, or was repainted differently`);
  assert.equal(
      uniqueFull, 1,
      `${name}: committed glyph pixels moved after the edit completed`);

  const finalState = await evaluate(state);
  assertTextState(finalState, expectedValue, expectedCaret, `${name} final state`);
  assertStableRect(finalState.rect, before.rect, `${name} final state`);

  results.push({
    target: name,
    operation,
    expectedMotion: 'caret-only',
    immediateCommitObservedAfterMs: immediateElapsedMs,
    initialValue,
    expectedValue,
    expectedCaret,
    uniqueFullFrames: uniqueFull,
    stablePrefixFrames: uniquePrefix,
    rapidSamples: rapidSamples.map(sample => ({
      elapsedMs: sample.elapsedMs,
      full: sample.full.slice(0, 12),
      value: sample.value,
      caret: sample.caret,
    })),
    samples: samples.map(sample => ({
      elapsedMs: sample.elapsedMs,
      full: sample.full.slice(0, 12),
      prefix: sample.prefix.slice(0, 12),
      value: sample.value,
      caret: sample.caret,
    })),
  });
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
      () => evaluate("document.readyState === 'complete' && !!document.querySelector('#frame').contentDocument?.querySelector('#input')"),
      'fixture and frame document');
  await delay(150);

  assert.equal(await evaluate(
      "document.querySelector('[data-focus-text-motion]') === null"), true,
  'native implementation must not inject a page overlay');

  const specs = [
    {
      name: 'input',
      prepare: "qa.prepareInput(document.querySelector('#input'))",
      geometry: "qa.geometry(document.querySelector('#input'))",
    },
    {
      name: 'textarea',
      prepare: "qa.prepareInput(document.querySelector('#textarea'))",
      geometry: "qa.geometry(document.querySelector('#textarea'))",
    },
    {
      name: 'password input',
      prepare: "qa.prepareInput(document.querySelector('#password'))",
      geometry: "qa.geometry(document.querySelector('#password'))",
    },
    {
      name: 'contenteditable',
      prepare: "qa.prepareEditable(document.querySelector('#editable'))",
      geometry: "qa.geometry(document.querySelector('#editable'))",
    },
    {
      name: 'open shadow input',
      prepare: 'qa.prepareInput(qa.openInput)',
      geometry: 'qa.geometry(qa.openInput)',
    },
    {
      name: 'closed shadow input',
      prepare: 'qa.prepareInput(qa.closedInput)',
      geometry: 'qa.geometry(qa.closedInput)',
    },
    {
      name: 'same-origin iframe input',
      prepare: "qa.prepareInput(document.querySelector('#frame').contentDocument.querySelector('#input'))",
      geometry: "qa.geometry(document.querySelector('#frame').contentDocument.querySelector('#input'), document.querySelector('#frame'))",
    },
  ];

  for (const spec of specs) {
    await sampleInsertion(spec);
  }

  // Exercise the same native editing pipeline with a paste-sized payload and
  // real Backspace/Delete key events. Input.insertText is deliberately used
  // for the paste payload so this isolated test never reads or overwrites the
  // user's real OS clipboard.
  const editableSpecs = [
    {
      name: 'input',
      target: "document.querySelector('#input')",
      prepareFunction: 'prepareInput',
    },
    {
      name: 'textarea',
      target: "document.querySelector('#textarea')",
      prepareFunction: 'prepareInput',
    },
    {
      name: 'contenteditable',
      target: "document.querySelector('#editable')",
      prepareFunction: 'prepareEditable',
    },
  ];
  const expressionFor = (spec, functionName, ...args) =>
    `qa.${functionName}(${spec.target},${args.map(arg => JSON.stringify(arg)).join(',')})`;
  const stateFor = spec => `qa.state(${spec.target})`;
  const geometryFor = (spec, stablePrefix) =>
    `qa.geometry(${spec.target},null,${JSON.stringify(stablePrefix)})`;

  const pastePrefix = 'Stable';
  const pastePayload = 'A\u{1F469}\u200D\u{1F4BB}\u0411';
  const pasteExpected = pastePrefix + pastePayload;
  const pasteTimes = [0, 28, 60, 100, 150, 230, 340, 440];

  const deletionPrefix = 'StableA';
  const deletedGrapheme = '\u{1F469}\u200D\u{1F4BB}';
  const deletionTail = '\u0411Z';
  const deletionInitial = deletionPrefix + deletedGrapheme + deletionTail;
  const deletionExpected = deletionPrefix + deletionTail;
  const deleteTimes = [0, 24, 55, 95, 230, 330];

  for (const spec of editableSpecs) {
    await sampleEditMotion({
      name: `${spec.name} multi-grapheme paste payload`,
      operation: 'multi-grapheme paste payload via native Input.insertText',
      prepare: expressionFor(
          spec, spec.prepareFunction, pastePrefix, pastePrefix.length),
      state: stateFor(spec),
      geometry: geometryFor(spec, pastePrefix),
      action: () => session.send('Input.insertText', {text: pastePayload}),
      initialValue: pastePrefix,
      initialCaret: pastePrefix.length,
      expectedValue: pasteExpected,
      expectedCaret: pasteExpected.length,
      requestedTimes: pasteTimes,
    });

    await sampleEditMotion({
      name: `${spec.name} Backspace grapheme deletion`,
      operation: 'Backspace deletes one Unicode grapheme',
      prepare: expressionFor(
          spec, spec.prepareFunction, deletionInitial,
          deletionPrefix.length + deletedGrapheme.length),
      state: stateFor(spec),
      geometry: geometryFor(spec, deletionPrefix),
      action: () => dispatchEditingKey('Backspace', 'Backspace', 8),
      initialValue: deletionInitial,
      initialCaret: deletionPrefix.length + deletedGrapheme.length,
      expectedValue: deletionExpected,
      expectedCaret: deletionPrefix.length,
      requestedTimes: deleteTimes,
    });

    await sampleEditMotion({
      name: `${spec.name} Delete grapheme deletion`,
      operation: 'Delete removes one Unicode grapheme',
      prepare: expressionFor(
          spec, spec.prepareFunction, deletionInitial, deletionPrefix.length),
      state: stateFor(spec),
      geometry: geometryFor(spec, deletionPrefix),
      action: () => dispatchEditingKey('Delete', 'Delete', 46),
      initialValue: deletionInitial,
      initialCaret: deletionPrefix.length,
      expectedValue: deletionExpected,
      expectedCaret: deletionPrefix.length,
      requestedTimes: deleteTimes,
    });
  }

  await session.send('Emulation.setEmulatedMedia', {
    features: [{name: 'prefers-reduced-motion', value: 'reduce'}],
  });
  await delay(80);
  await sampleInsertion({
    name: 'prefers-reduced-motion',
    prepare: "qa.prepareInput(document.querySelector('#input'))",
    geometry: "qa.geometry(document.querySelector('#input'))",
  });

  const report = {
    ok: true,
    executable: chromePath,
    implementation: 'crisp native glyph paint with separate caret glide',
    fixture: 'local HTTP only; disposable profile',
    invariants: [
      'inserted glyph is sharp and pixel-stable from the first sampled frame',
      'multi-grapheme paste payload is sharp and pixel-stable after commit',
      'Backspace and Delete commit value and caret before animation sampling',
      'Backspace and Delete remove one complete Unicode grapheme',
      'pre-existing prefix stays pixel-stable',
      'field geometry and neighboring glyph pixels stay stable during caret motion',
      'no DOM overlay is injected',
      'prefers-reduced-motion keeps the same crisp committed glyph paint',
    ],
    results,
  };
  if (reportPath) {
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
} finally {
  if (session) {
    try {
      await Promise.race([session.send('Browser.close'), delay(1500)]);
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
  await delay(100);
  fs.rmSync(profileDir, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  });
}
