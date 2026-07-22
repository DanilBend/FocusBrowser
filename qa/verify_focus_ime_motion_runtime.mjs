#!/usr/bin/env node

// Runtime proof for the Blink-native Focus text-motion IME guard. Provisional
// composition updates must paint once and remain still; committing the active
// composition must use the normal Focus glyph-settle path. The fixture is
// local-only, caret paint is transparent, and the browser uses an owned
// disposable profile.

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
  'Usage: node qa/verify_focus_ime_motion_runtime.mjs <chrome.exe> [report.json]',
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
<title>Focus IME motion guard QA</title>
<style>
  * { animation: none !important; transition: none !important; }
  html, body { margin: 0; background: #181a1b; color: white; }
  body { padding: 20px; }
  .qa {
    box-sizing: border-box;
    display: block;
    width: 700px;
    height: 78px;
    margin: 14px;
    border: 0;
    border-radius: 0;
    padding: 10px 16px;
    outline: 0;
    overflow: hidden;
    background: #08090a;
    color: #f8f8f8;
    caret-color: transparent !important;
    text-shadow: none !important;
    font: 48px/56px "Segoe UI", "Yu Gothic UI", sans-serif;
    white-space: pre;
  }
  textarea.qa { resize: none; }
</style>
<input id="input" class="qa" autocomplete="off" spellcheck="false">
<textarea id="textarea" class="qa" autocomplete="off" spellcheck="false"></textarea>
<div id="editable" class="qa" contenteditable="true" spellcheck="false"></div>
<script>
  const records = new WeakMap();
  const composing = new WeakMap();

  function valueOf(element) {
    return 'value' in element ? element.value : element.textContent;
  }

  function record(element, event) {
    if (event.type === 'compositionstart') {
      composing.set(element, true);
    } else if (event.type === 'compositionend') {
      composing.set(element, false);
    }
    const entries = records.get(element) || [];
    entries.push({
      type: event.type,
      data: typeof event.data === 'string' ? event.data : null,
      inputType: typeof event.inputType === 'string' ? event.inputType : null,
      isComposing: typeof event.isComposing === 'boolean'
        ? event.isComposing
        : null,
      value: valueOf(element),
    });
    records.set(element, entries);
  }

  for (const element of document.querySelectorAll('.qa')) {
    records.set(element, []);
    composing.set(element, false);
    for (const type of [
      'compositionstart', 'compositionupdate', 'compositionend',
      'beforeinput', 'input',
    ]) {
      element.addEventListener(type, event => record(element, event));
    }
  }

  function prepareInput(element, value, caret) {
    element.value = value;
    element.focus();
    element.setSelectionRange(caret, caret);
    records.set(element, []);
    composing.set(element, false);
  }

  function prepareEditable(element, value, caret) {
    element.textContent = value;
    element.focus();
    const range = document.createRange();
    range.setStart(element.firstChild, caret);
    range.collapse(true);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    records.set(element, []);
    composing.set(element, false);
  }

  function selectionOffset(element, node, offset) {
    if (!node || (node !== element && !element.contains(node))) {
      return null;
    }
    const range = document.createRange();
    range.selectNodeContents(element);
    range.setEnd(node, offset);
    return range.toString().length;
  }

  function state(element) {
    const rect = element.getBoundingClientRect();
    const base = {
      value: valueOf(element),
      composing: composing.get(element) === true,
      events: [...(records.get(element) || [])],
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
    if ('value' in element && typeof element.selectionStart === 'number') {
      return {
        ...base,
        selectionStart: element.selectionStart,
        selectionEnd: element.selectionEnd,
      };
    }
    const selection = getSelection();
    return {
      ...base,
      selectionStart: selectionOffset(
          element, selection.anchorNode, selection.anchorOffset),
      selectionEnd: selectionOffset(
          element, selection.focusNode, selection.focusOffset),
    };
  }

  function geometry(element, stablePrefix) {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = style.font;
    const prefixWidth = context.measureText(stablePrefix).width;
    const paddingLeft = parseFloat(style.paddingLeft) || 0;
    return {
      full: {
        x: Math.floor(rect.x),
        y: Math.floor(rect.y),
        width: Math.ceil(rect.width),
        height: Math.ceil(rect.height),
      },
      prefix: {
        x: Math.floor(rect.x + paddingLeft),
        y: Math.floor(rect.y + 4),
        width: Math.max(1, Math.floor(prefixWidth - 3)),
        height: Math.max(1, Math.ceil(rect.height - 8)),
      },
    };
  }

  window.qa = {prepareInput, prepareEditable, state, geometry};
</script>`;

const server = createServer((request, response) => {
  response.writeHead(200, {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'no-store',
  });
  response.end(fixtureHtml);
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

const address = server.address();
assert.ok(address && typeof address !== 'string');
const fixtureUrl = `http://127.0.0.1:${address.port}/`;
const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-native-ime-motion-qa-'));
const devToolsPortFile = path.join(profileDir, 'DevToolsActivePort');
const browser = spawn(chromePath, [
  `--user-data-dir=${profileDir}`,
  '--remote-debugging-port=0',
  '--headless=new',
  '--window-size=900,500',
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
const failures = [];
const controls = [];
const reducedMotion = [];

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

async function waitTwoAnimationFrames() {
  await evaluate(`new Promise(resolve => requestAnimationFrame(
      () => requestAnimationFrame(() => resolve(true))))`);
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

function assertStableRect(actual, expected, label) {
  for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(Math.abs(actual[key] - expected[key]) < 0.01,
              `${label}: field geometry changed (${key})`);
  }
}

function assertValue(state, expected, label) {
  assert.equal(state?.value, expected, `${label}: value mismatch`);
}

function compactEvents(events) {
  return events.map(event => ({
    type: event.type,
    data: event.data,
    inputType: event.inputType,
    isComposing: event.isComposing,
    value: event.value,
  }));
}

async function sampleIme(spec) {
  const prefix = 'Stable';
  const firstComposition = '\u5019';
  const committedText = '\u5019\u88dc';
  const firstValue = prefix + firstComposition;
  const finalValue = prefix + committedText;
  const prepare =
      `qa.${spec.prepare}(${spec.target},${JSON.stringify(prefix)},${prefix.length})`;
  const stateExpression = `qa.state(${spec.target})`;
  const geometryExpression =
      `qa.geometry(${spec.target},${JSON.stringify(prefix)})`;

  await evaluate(prepare);
  await delay(50);
  const geometry = await evaluate(geometryExpression);
  const initial = await evaluate(stateExpression);
  assertValue(initial, prefix, `${spec.name} initial state`);

  await session.send('Input.imeSetComposition', {
    text: firstComposition,
    selectionStart: firstComposition.length,
    selectionEnd: firstComposition.length,
  });
  await waitTwoAnimationFrames();
  await delay(120);
  const firstSettled = await capture(geometry.full);
  await delay(80);
  const firstSettledConfirm = await capture(geometry.full);
  assert.equal(
      firstSettled, firstSettledConfirm,
      `${spec.name}: first provisional composition did not settle`);
  const afterFirst = await evaluate(stateExpression);
  assertValue(afterFirst, firstValue, `${spec.name} first composition`);
  assert.equal(afterFirst.composing, true,
               `${spec.name}: compositionstart was not active`);
  assert.equal(
      afterFirst.events.filter(event => event.type === 'compositionend').length,
      0, `${spec.name}: provisional composition ended unexpectedly`);

  const provisionalStarted = Date.now();
  await session.send('Input.imeSetComposition', {
    text: committedText,
    selectionStart: committedText.length,
    selectionEnd: committedText.length,
  });
  const provisionalImmediate = await evaluate(stateExpression);
  assertValue(
      provisionalImmediate, finalValue,
      `${spec.name} second provisional composition`);
  assert.equal(provisionalImmediate.composing, true,
               `${spec.name}: second provisional update ended composition`);
  assertStableRect(
      provisionalImmediate.rect, initial.rect,
      `${spec.name} second provisional composition`);

  // Two compositor frames establish the first fully-painted provisional
  // update. Every later frame must be byte-identical: the candidate text may
  // change once, but the Focus reveal must not keep moving it while composing.
  await waitTwoAnimationFrames();
  const provisionalSamples = [];
  for (const requestedMs of [0, 24, 55, 100, 180, 260]) {
    await delay(Math.max(0, provisionalStarted + requestedMs - Date.now()));
    const full = await capture(geometry.full);
    const prefixHash = await capture(geometry.prefix);
    const state = await evaluate(stateExpression);
    assertValue(state, finalValue, `${spec.name} provisional frame`);
    assert.equal(state.composing, true,
                 `${spec.name}: composition ended during provisional sampling`);
    assertStableRect(state.rect, initial.rect,
                     `${spec.name} provisional frame`);
    provisionalSamples.push({
      elapsedMs: Date.now() - provisionalStarted,
      full,
      prefix: prefixHash,
    });
  }
  const provisionalFullHashes = provisionalSamples.map(sample => sample.full);
  const provisionalPrefixHashes = provisionalSamples.map(sample => sample.prefix);
  assert.equal(
      new Set(provisionalFullHashes).size, 1,
      `${spec.name}: provisional IME update produced glyph-motion frames`);
  assert.equal(
      new Set(provisionalPrefixHashes).size, 1,
      `${spec.name}: stable prefix changed during provisional IME update`);
  assert.notEqual(
      firstSettled, provisionalFullHashes[0],
      `${spec.name}: second provisional update did not change painted text`);

  const eventsBeforeCommit = provisionalImmediate.events.length;
  const commitStarted = Date.now();
  // CDP documents Input.insertText as the path used for IME text. When an
  // active composition exists Chromium commits it; event evidence below is
  // mandatory so an ordinary non-IME insertion cannot masquerade as a pass.
  await session.send('Input.insertText', {text: committedText});
  const commitImmediate = await evaluate(stateExpression);
  const commitImmediateElapsedMs = Date.now() - commitStarted;
  assertValue(commitImmediate, finalValue, `${spec.name} committed composition`);
  assert.equal(commitImmediate.composing, false,
               `${spec.name}: Input.insertText did not end active composition`);
  assert.equal(commitImmediate.selectionStart, finalValue.length,
               `${spec.name}: committed selection start mismatch`);
  assert.equal(commitImmediate.selectionEnd, finalValue.length,
               `${spec.name}: committed selection end mismatch`);
  assertStableRect(
      commitImmediate.rect, initial.rect, `${spec.name} committed composition`);

  const commitEvents = commitImmediate.events.slice(eventsBeforeCommit);
  const compositionEnds = commitEvents.filter(
      event => event.type === 'compositionend');
  assert.equal(
      compositionEnds.length, 1,
      `${spec.name}: CDP commit path did not dispatch one compositionend; ` +
      `events=${JSON.stringify(compactEvents(commitEvents))}`);
  assert.equal(
      compositionEnds[0].data, committedText,
      `${spec.name}: compositionend data did not match committed text`);

  const rapidCommitSamples = [];
  for (let index = 0; index < 3; ++index) {
    const full = await capture(geometry.full);
    const state = await evaluate(stateExpression);
    assertValue(state, finalValue, `${spec.name} rapid commit frame`);
    assert.equal(state.composing, false,
                 `${spec.name}: composition restarted after commit`);
    rapidCommitSamples.push({elapsedMs: Date.now() - commitStarted, full});
  }

  const commitSamples = [];
  for (const requestedMs of [0, 28, 60, 100, 160, 240, 340]) {
    await delay(Math.max(0, commitStarted + requestedMs - Date.now()));
    const full = await capture(geometry.full);
    const prefixHash = await capture(geometry.prefix);
    const state = await evaluate(stateExpression);
    assertValue(state, finalValue, `${spec.name} commit frame`);
    assert.equal(state.composing, false,
                 `${spec.name}: composition restarted during commit settle`);
    assertStableRect(state.rect, initial.rect, `${spec.name} commit frame`);
    commitSamples.push({
      elapsedMs: Date.now() - commitStarted,
      full,
      prefix: prefixHash,
    });
  }

  const commitFullHashes = [
    ...rapidCommitSamples.map(sample => sample.full),
    ...commitSamples.map(sample => sample.full),
  ];
  const commitPrefixHashes = commitSamples.map(sample => sample.prefix);
  const uniqueCommitFrames = new Set(commitFullHashes).size;
  assert.equal(
      new Set(commitPrefixHashes).size, 1,
      `${spec.name}: stable prefix moved during committed Focus settle`);
  assert.equal(
      uniqueCommitFrames, 1,
      `${spec.name}: committed IME glyph pixels were not immediately stable`);

  const finalState = await evaluate(stateExpression);
  assertValue(finalState, finalValue, `${spec.name} final state`);
  assert.equal(finalState.composing, false,
               `${spec.name}: final state remained composing`);

  const caseResult = {
    target: spec.name,
    ok: uniqueCommitFrames === 1,
    provisional: {
      ok: true,
      firstValue,
      updatedValue: finalValue,
      activeComposition: true,
      uniqueFramesAfterFirstSettledUpdate:
          new Set(provisionalFullHashes).size,
      stablePrefixFrames: new Set(provisionalPrefixHashes).size,
      samples: provisionalSamples.map(sample => ({
        elapsedMs: sample.elapsedMs,
        full: sample.full.slice(0, 12),
        prefix: sample.prefix.slice(0, 12),
      })),
    },
    commit: {
      ok: uniqueCommitFrames === 1,
      mechanism: 'Input.insertText while Input.imeSetComposition is active',
      compositionEndObserved: true,
      compositionEndData: compositionEnds[0].data,
      immediateCommitObservedAfterMs: commitImmediateElapsedMs,
      finalValue,
      uniqueCommittedGlyphFrames: uniqueCommitFrames,
      stablePrefixFrames: new Set(commitPrefixHashes).size,
      finalStable: commitFullHashes.at(-1) === commitFullHashes.at(-2),
      events: compactEvents(commitEvents),
      rapidSamples: rapidCommitSamples.map(sample => ({
        elapsedMs: sample.elapsedMs,
        full: sample.full.slice(0, 12),
      })),
      samples: commitSamples.map(sample => ({
        elapsedMs: sample.elapsedMs,
        full: sample.full.slice(0, 12),
        prefix: sample.prefix.slice(0, 12),
      })),
    },
  };
  if (uniqueCommitFrames !== 1) {
    const error = new Error(
        `${spec.name}: committed IME text was not pixel-stable; ` +
        `uniqueFrames=${uniqueCommitFrames}`);
    error.partialResult = caseResult;
    throw error;
  }
  return caseResult;
}

async function samplePlainInsertControl(spec) {
  const prefix = 'Stable';
  const insertedText = '\u5019\u88dc';
  const finalValue = prefix + insertedText;
  const prepare =
      `qa.${spec.prepare}(${spec.target},${JSON.stringify(prefix)},${prefix.length})`;
  const stateExpression = `qa.state(${spec.target})`;
  const geometryExpression =
      `qa.geometry(${spec.target},${JSON.stringify(prefix)})`;
  await evaluate(prepare);
  await delay(50);
  const geometry = await evaluate(geometryExpression);

  const started = Date.now();
  await session.send('Input.insertText', {text: insertedText});
  const immediate = await evaluate(stateExpression);
  assertValue(immediate, finalValue, `${spec.name} ordinary-insert control`);
  assert.equal(immediate.composing, false,
               `${spec.name}: ordinary-insert control became composing`);

  const rapid = [];
  for (let index = 0; index < 3; ++index) {
    rapid.push({elapsedMs: Date.now() - started, full: await capture(geometry.full)});
  }
  const samples = [];
  for (const requestedMs of [0, 28, 60, 100, 220, 320]) {
    await delay(Math.max(0, started + requestedMs - Date.now()));
    samples.push({
      elapsedMs: Date.now() - started,
      full: await capture(geometry.full),
      prefix: await capture(geometry.prefix),
    });
  }
  const fullHashes = [
    ...rapid.map(sample => sample.full),
    ...samples.map(sample => sample.full),
  ];
  const prefixHashes = samples.map(sample => sample.prefix);
  const uniqueFrames = new Set(fullHashes).size;
  assert.equal(uniqueFrames, 1,
               `${spec.name}: ordinary insert glyph pixels were not stable`);
  assert.equal(new Set(prefixHashes).size, 1,
               `${spec.name}: ordinary insert control moved stable prefix`);
  assert.equal(fullHashes.at(-1), fullHashes.at(-2),
               `${spec.name}: ordinary insert control did not stabilize`);
  return {
    target: spec.name,
    ok: true,
    purpose: 'verifies ordinary committed glyph pixels are immediately stable',
    uniqueCommittedGlyphFrames: uniqueFrames,
    finalStable: fullHashes.at(-1) === fullHashes.at(-2),
  };
}

async function sampleReducedIme(spec) {
  const prefix = 'Stable';
  const committedText = '\u5019\u88dc';
  const finalValue = prefix + committedText;
  const prepare =
      `qa.${spec.prepare}(${spec.target},${JSON.stringify(prefix)},${prefix.length})`;
  const stateExpression = `qa.state(${spec.target})`;
  const geometryExpression =
      `qa.geometry(${spec.target},${JSON.stringify(prefix)})`;
  await evaluate(prepare);
  await delay(50);
  const geometry = await evaluate(geometryExpression);

  await session.send('Input.imeSetComposition', {
    text: committedText,
    selectionStart: committedText.length,
    selectionEnd: committedText.length,
  });
  const composingState = await evaluate(stateExpression);
  assertValue(composingState, finalValue,
              `${spec.name} reduced-motion composition`);
  assert.equal(composingState.composing, true,
               `${spec.name}: reduced-motion composition was not active`);
  const eventCount = composingState.events.length;

  await session.send('Input.insertText', {text: committedText});
  const committed = await evaluate(stateExpression);
  assertValue(committed, finalValue, `${spec.name} reduced-motion commit`);
  assert.equal(committed.composing, false,
               `${spec.name}: reduced-motion commit remained composing`);
  const commitEvents = committed.events.slice(eventCount);
  const compositionEnds = commitEvents.filter(
      event => event.type === 'compositionend');
  assert.equal(compositionEnds.length, 1,
               `${spec.name}: reduced-motion commit lacked compositionend`);
  assert.equal(compositionEnds[0].data, committedText,
               `${spec.name}: reduced-motion compositionend data mismatch`);

  await waitTwoAnimationFrames();
  const samples = [];
  const started = Date.now();
  for (const requestedMs of [0, 40, 100, 220]) {
    await delay(Math.max(0, started + requestedMs - Date.now()));
    samples.push({
      elapsedMs: Date.now() - started,
      full: await capture(geometry.full),
      prefix: await capture(geometry.prefix),
    });
  }
  const uniqueFull = new Set(samples.map(sample => sample.full)).size;
  const uniquePrefix = new Set(samples.map(sample => sample.prefix)).size;
  assert.equal(uniqueFull, 1,
               `${spec.name}: reduced motion did not suppress IME commit settle`);
  assert.equal(uniquePrefix, 1,
               `${spec.name}: reduced-motion commit moved stable prefix`);
  return {
    target: spec.name,
    ok: true,
    compositionEndObserved: true,
    uniqueFramesAfterFirstSettledCommit: uniqueFull,
    stablePrefixFrames: uniquePrefix,
    finalValue,
  };
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
  }, 'local IME fixture page');

  session = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await session.connect();
  await session.send('Runtime.enable');
  await session.send('Page.enable');
  await waitFor(
      () => evaluate("document.readyState === 'complete' && !!window.qa"),
      'IME fixture readiness');
  await delay(120);

  const specs = [
    {
      name: 'input',
      target: "document.querySelector('#input')",
      prepare: 'prepareInput',
    },
    {
      name: 'textarea',
      target: "document.querySelector('#textarea')",
      prepare: 'prepareInput',
    },
    {
      name: 'contenteditable',
      target: "document.querySelector('#editable')",
      prepare: 'prepareEditable',
    },
  ];

  for (const spec of specs) {
    try {
      results.push(await sampleIme(spec));
    } catch (error) {
      if (error.partialResult) {
        results.push(error.partialResult);
      }
      let state = null;
      try {
        state = await evaluate(`qa.state(${spec.target})`);
      } catch {
        // Preserve the original failure when the page itself became invalid.
      }
      failures.push({
        target: spec.name,
        error: error.message,
        state,
        limitation: error.message.includes('compositionend') ||
                error.message.includes('did not end active composition')
          ? 'CDP Input.insertText did not expose an honest active-composition commit for this target'
          : null,
      });
    }
  }

  // A same-fixture ordinary insertion control is especially important for
  // contenteditable: if IME commit is static but this control moves, the
  // failure is in the composition-confirm command path rather than screenshot
  // timing or CJK glyph rasterization.
  const contenteditableSpec = specs.find(spec => spec.name === 'contenteditable');
  try {
    controls.push(await samplePlainInsertControl(contenteditableSpec));
  } catch (error) {
    failures.push({
      target: 'contenteditable ordinary-insert control',
      error: error.message,
      limitation: 'same-fixture control could not validate pixel sampling',
    });
  }

  await session.send('Emulation.setEmulatedMedia', {
    features: [{name: 'prefers-reduced-motion', value: 'reduce'}],
  });
  assert.equal(
      await evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),
      true, 'CDP reduced-motion emulation was not applied');
  for (const spec of specs) {
    try {
      reducedMotion.push(await sampleReducedIme(spec));
    } catch (error) {
      failures.push({
        target: `${spec.name} reduced-motion IME commit`,
        error: error.message,
        limitation: null,
      });
    }
  }

  const report = {
    ok: failures.length === 0,
    executable: chromePath,
    ownedProcess: {
      rootPid: browser.pid,
      userDataDir: profileDir,
      cleanup: 'Browser.close followed only by exact owned PID-tree fallback',
    },
    implementation: 'crisp native glyph paint with provisional IME guard and separate caret glide',
    fixture: 'local HTTP; transparent caret; disposable profile',
    commitSemantics:
        'Input.insertText is accepted only when one matching compositionend proves the active Input.imeSetComposition was committed',
    invariants: [
      'provisional composition remains active across imeSetComposition updates',
      'after its first settled frame a provisional update has no later glyph-motion frames',
      'stable prefix and field geometry do not move',
      'commit dispatches compositionend with the committed text',
      'committed text is sharp and pixel-stable from its first sampled frame',
      'prefers-reduced-motion preserves the same crisp committed IME paint',
      'input, textarea and contenteditable finish with the exact committed value',
    ],
    results,
    controls,
    reducedMotion,
    failures,
  };
  if (reportPath) {
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
  if (!report.ok) {
    process.exitCode = 1;
  }
} finally {
  if (session) {
    try {
      await Promise.race([session.send('Browser.close'), delay(1500)]);
    } catch {
      // Fall through to exact-owned-PID cleanup.
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
