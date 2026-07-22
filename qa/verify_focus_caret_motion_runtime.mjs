#!/usr/bin/env node

// Visible runtime smoke test for the Blink-native, paint-only Focus caret
// glide. Text ink is transparent and the caret is bright magenta, so glyph
// insertion/deletion paint cannot be mistaken for caret motion. The fixture is
// local-only and the browser always runs with an owned disposable profile.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import {createServer} from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {inflateSync} from 'node:zlib';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..');
const chromePath = path.resolve(
    process.argv[2] ||
    path.join(projectRoot, 'build', 'src', 'out', 'Default', 'chrome.exe'));
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;

assert.ok(fs.existsSync(chromePath), [
  'Focus Browser executable was not found.',
  'Usage: node qa/verify_focus_caret_motion_runtime.mjs <chrome.exe> [report.json]',
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

function paethPredictor(left, up, upperLeft) {
  const estimate = left + up - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) {
    return left;
  }
  return upDistance <= upperLeftDistance ? up : upperLeft;
}

// Minimal decoder for the non-interlaced 8-bit RGB/RGBA PNGs emitted by CDP.
// Keeping it here avoids external packages and makes the pixel proof portable.
function decodePng(base64) {
  const png = Buffer.from(base64, 'base64');
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  assert.ok(png.subarray(0, 8).equals(signature), 'CDP returned an invalid PNG');

  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = -1;
  let interlace = -1;
  const idat = [];
  for (let offset = 8; offset < png.length;) {
    const length = png.readUInt32BE(offset);
    const type = png.toString('ascii', offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      bitDepth = data[8];
      colorType = data[9];
      interlace = data[12];
    } else if (type === 'IDAT') {
      idat.push(data);
    } else if (type === 'IEND') {
      break;
    }
    offset += length + 12;
  }

  assert.equal(bitDepth, 8, 'caret QA expects an 8-bit CDP PNG');
  assert.ok(colorType === 2 || colorType === 6,
            `unsupported CDP PNG color type: ${colorType}`);
  assert.equal(interlace, 0, 'caret QA expects a non-interlaced CDP PNG');
  assert.ok(width > 0 && height > 0 && idat.length > 0,
            'CDP PNG was missing image data');

  const bytesPerPixel = colorType === 6 ? 4 : 3;
  const stride = width * bytesPerPixel;
  const raw = inflateSync(Buffer.concat(idat));
  assert.equal(raw.length, height * (stride + 1),
               'unexpected CDP PNG scanline length');
  const pixels = Buffer.alloc(height * stride);

  let rawOffset = 0;
  for (let y = 0; y < height; ++y) {
    const filter = raw[rawOffset++];
    const rowOffset = y * stride;
    const previousRowOffset = (y - 1) * stride;
    for (let x = 0; x < stride; ++x) {
      const encoded = raw[rawOffset++];
      const left = x >= bytesPerPixel ? pixels[rowOffset + x - bytesPerPixel] : 0;
      const up = y > 0 ? pixels[previousRowOffset + x] : 0;
      const upperLeft = y > 0 && x >= bytesPerPixel
        ? pixels[previousRowOffset + x - bytesPerPixel]
        : 0;
      let predictor = 0;
      if (filter === 1) {
        predictor = left;
      } else if (filter === 2) {
        predictor = up;
      } else if (filter === 3) {
        predictor = Math.floor((left + up) / 2);
      } else if (filter === 4) {
        predictor = paethPredictor(left, up, upperLeft);
      } else {
        assert.equal(filter, 0, `unsupported PNG filter: ${filter}`);
      }
      pixels[rowOffset + x] = (encoded + predictor) & 0xff;
    }
  }
  return {width, height, colorType, bytesPerPixel, pixels};
}

function locateMagentaCaret(image, label) {
  let minimumX = image.width;
  let minimumY = image.height;
  let maximumX = -1;
  let maximumY = -1;
  let weightedX = 0;
  let weightedY = 0;
  let totalWeight = 0;
  let pixelCount = 0;
  for (let y = 0; y < image.height; ++y) {
    for (let x = 0; x < image.width; ++x) {
      const offset = (y * image.width + x) * image.bytesPerPixel;
      const red = image.pixels[offset];
      const green = image.pixels[offset + 1];
      const blue = image.pixels[offset + 2];
      const alpha = image.bytesPerPixel === 4 ? image.pixels[offset + 3] : 255;
      if (red < 150 || blue < 150 || green > 130 || alpha < 100) {
        continue;
      }
      const weight = Math.max(1, Math.min(red, blue) - green);
      minimumX = Math.min(minimumX, x);
      minimumY = Math.min(minimumY, y);
      maximumX = Math.max(maximumX, x);
      maximumY = Math.max(maximumY, y);
      weightedX += (x + 0.5) * weight;
      weightedY += (y + 0.5) * weight;
      totalWeight += weight;
      ++pixelCount;
    }
  }
  assert.ok(pixelCount >= 8, `${label}: visible magenta caret was not isolated`);
  return {
    x: weightedX / totalWeight,
    y: weightedY / totalWeight,
    pixelCount,
    bounds: {
      x: minimumX,
      y: minimumY,
      width: maximumX - minimumX + 1,
      height: maximumY - minimumY + 1,
    },
  };
}

const fixtureHtml = `<!doctype html>
<meta charset="utf-8">
<title>Focus native caret glide QA</title>
<style>
  * { animation: none !important; transition: none !important; }
  html, body { margin: 0; background: #111; }
  body { padding: 18px; }
  .caret-qa {
    box-sizing: border-box;
    display: block;
    width: 760px;
    height: 82px;
    margin: 12px;
    border: 0;
    border-radius: 0;
    padding: 14px 18px;
    outline: 0;
    overflow: hidden;
    background: #050607;
    color: transparent !important;
    -webkit-text-fill-color: transparent !important;
    caret-color: #ff00ff !important;
    text-shadow: none !important;
    font: 48px/52px Consolas, monospace;
    white-space: pre;
  }
  textarea.caret-qa { resize: none; }
</style>
<input id="input" class="caret-qa" autocomplete="off" spellcheck="false">
<textarea id="textarea" class="caret-qa" autocomplete="off" spellcheck="false"></textarea>
<div id="editable" class="caret-qa" contenteditable="true" spellcheck="false"></div>
<script>
  function prepareInput(element, value, caret) {
    element.value = value;
    element.focus();
    element.setSelectionRange(caret, caret);
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
    if ('value' in element && typeof element.selectionStart === 'number') {
      return {
        value: element.value,
        caret: element.selectionEnd,
        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      };
    }
    const selection = getSelection();
    return {
      value: element.textContent,
      caret: selectionOffset(element, selection.focusNode, selection.focusOffset),
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
  }

  function clip(element) {
    const rect = element.getBoundingClientRect();
    return {
      x: Math.floor(rect.x),
      y: Math.floor(rect.y),
      width: Math.ceil(rect.width),
      height: Math.ceil(rect.height),
    };
  }

  window.qa = {prepareInput, prepareEditable, state, clip};
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
    path.join(os.tmpdir(), 'focus-native-caret-motion-qa-'));
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

async function captureCaret(clip, label) {
  const result = await session.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
    clip: {...clip, scale: 1},
  });
  return locateMagentaCaret(decodePng(result.data), label);
}

async function dispatchEditingKey(key, code, windowsVirtualKeyCode, text = '') {
  const event = {
    key,
    code,
    windowsVirtualKeyCode,
    nativeVirtualKeyCode: windowsVirtualKeyCode,
  };
  await session.send('Input.dispatchKeyEvent', {
    type: text ? 'keyDown' : 'rawKeyDown',
    ...event,
    ...(text ? {text, unmodifiedText: text} : {}),
  });
  await session.send('Input.dispatchKeyEvent', {type: 'keyUp', ...event});
}

function assertState(state, expectedValue, expectedCaret, label) {
  assert.equal(state?.value, expectedValue, `${label}: value mismatch`);
  assert.equal(state?.caret, expectedCaret, `${label}: caret offset mismatch`);
}

function assertStableRect(actual, expected, label) {
  for (const key of ['x', 'y', 'width', 'height']) {
    assert.ok(Math.abs(actual[key] - expected[key]) < 0.01,
              `${label}: field geometry changed (${key})`);
  }
}

async function sampleCaretGlide({
  name,
  prepare,
  stateExpression,
  clipExpression,
  action,
  initialValue,
  initialCaret,
  expectedValue,
  expectedCaret,
}) {
  await evaluate(prepare);
  // Let the programmatic fixture setup settle before recording the true start
  // endpoint. The actual edit below resets the caret blink phase.
  await delay(140);
  const clip = await evaluate(clipExpression);
  const initialState = await evaluate(stateExpression);
  assertState(initialState, initialValue, initialCaret, `${name} initial state`);
  const startFrame = await captureCaret(clip, `${name} start endpoint`);
  const startConfirm = await captureCaret(clip, `${name} confirmed start endpoint`);
  assert.ok(Math.abs(startFrame.x - startConfirm.x) <= 0.5,
            `${name}: start endpoint was not stable`);

  const started = Date.now();
  await action();
  const immediateState = await evaluate(stateExpression);
  const immediateCommitObservedAfterMs = Date.now() - started;
  assertState(immediateState, expectedValue, expectedCaret, `${name} immediate state`);
  assertStableRect(immediateState.rect, initialState.rect, `${name} immediate state`);

  const rapidFrames = [];
  for (let index = 0; index < 6; ++index) {
    const caret = await captureCaret(clip, `${name} rapid frame ${index + 1}`);
    const state = await evaluate(stateExpression);
    assertState(state, expectedValue, expectedCaret, `${name} rapid frame ${index + 1}`);
    assertStableRect(state.rect, initialState.rect, `${name} rapid frame ${index + 1}`);
    rapidFrames.push({elapsedMs: Date.now() - started, ...caret});
  }

  await delay(Math.max(0, started + 150 - Date.now()));
  const finalFrame = await captureCaret(clip, `${name} final endpoint`);
  await delay(Math.max(0, started + 230 - Date.now()));
  const finalConfirm = await captureCaret(clip, `${name} confirmed final endpoint`);
  const finalState = await evaluate(stateExpression);
  assertState(finalState, expectedValue, expectedCaret, `${name} final state`);
  assertStableRect(finalState.rect, initialState.rect, `${name} final state`);

  const startX = (startFrame.x + startConfirm.x) / 2;
  const finalX = (finalFrame.x + finalConfirm.x) / 2;
  const lowX = Math.min(startX, finalX);
  const highX = Math.max(startX, finalX);
  assert.ok(highX - lowX >= 8,
            `${name}: endpoints were too close to prove a glide`);
  assert.ok(Math.abs(finalFrame.x - finalConfirm.x) <= 0.5,
            `${name}: final caret endpoint did not stabilize`);

  const intermediate = rapidFrames.filter(
      frame => frame.x > lowX + 0.75 && frame.x < highX - 0.75);
  const stayedWithinEndpoints = rapidFrames.every(
      frame => frame.x >= lowX - 1.5 && frame.x <= highX + 1.5);
  const uniqueRapidPositions =
      new Set(rapidFrames.map(frame => frame.x.toFixed(2))).size;

  results.push({
    target: name,
    ok: intermediate.length >= 1 && stayedWithinEndpoints &&
        uniqueRapidPositions >= 2,
    immediateCommitObservedAfterMs,
    initialValue,
    expectedValue,
    initialCaret,
    expectedCaret,
    start: startFrame,
    startConfirm,
    rapidFrames,
    final: finalFrame,
    finalConfirm,
    intermediatePositions: intermediate.map(frame => frame.x),
    endpointDistance: highX - lowX,
    stayedWithinEndpoints,
    uniqueRapidPositions,
  });

  assert.ok(intermediate.length >= 1,
            `${name}: no intermediate caret position appeared between endpoints ` +
            JSON.stringify({startX, finalX, rapidFrames}));
  assert.ok(stayedWithinEndpoints,
            `${name}: caret paint overshot its endpoints`);
  assert.ok(uniqueRapidPositions >= 2,
            `${name}: rapid caret frames did not move`);
}

async function runCase(options) {
  try {
    await sampleCaretGlide(options);
  } catch (error) {
    failures.push({target: options.name, error: error.message});
  }
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
  }, 'local caret fixture page');

  session = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await session.connect();
  await session.send('Runtime.enable');
  await session.send('Page.enable');
  await waitFor(
      () => evaluate("document.readyState === 'complete' && !!window.qa"),
      'caret fixture readiness');
  await delay(100);

  const targets = [
    {name: 'input', expression: "document.querySelector('#input')", prepare: 'prepareInput'},
    {name: 'textarea', expression: "document.querySelector('#textarea')", prepare: 'prepareInput'},
    {name: 'contenteditable', expression: "document.querySelector('#editable')", prepare: 'prepareEditable'},
  ];
  const expressionFor = (target, functionName, ...args) =>
    `qa.${functionName}(${target.expression},${args.map(value => JSON.stringify(value)).join(',')})`;

  for (const target of targets) {
    const stateExpression = `qa.state(${target.expression})`;
    const clipExpression = `qa.clip(${target.expression})`;

    await runCase({
      name: `${target.name} ArrowRight control`,
      prepare: expressionFor(target, target.prepare, 'MMMM', 1),
      stateExpression,
      clipExpression,
      action: () => dispatchEditingKey('ArrowRight', 'ArrowRight', 39),
      initialValue: 'MMMM',
      initialCaret: 1,
      expectedValue: 'MMMM',
      expectedCaret: 2,
    });

    await runCase({
      name: `${target.name} typed character`,
      prepare: expressionFor(target, target.prepare, 'MMMM', 1),
      stateExpression,
      clipExpression,
      action: () => dispatchEditingKey('x', 'KeyX', 88, 'x'),
      initialValue: 'MMMM',
      initialCaret: 1,
      expectedValue: 'MxMMM',
      expectedCaret: 2,
    });

    const pastePayload = 'A\u{1F469}\u200D\u{1F4BB}\u0411';
    await runCase({
      name: `${target.name} multi-grapheme paste payload`,
      prepare: expressionFor(target, target.prepare, 'MMMM', 1),
      stateExpression,
      clipExpression,
      // Input.insertText exercises the native paste-sized insertion path
      // without touching the user's OS clipboard.
      action: () => session.send('Input.insertText', {text: pastePayload}),
      initialValue: 'MMMM',
      initialCaret: 1,
      expectedValue: `M${pastePayload}MMM`,
      expectedCaret: 1 + pastePayload.length,
    });

    await runCase({
      name: `${target.name} Backspace`,
      prepare: expressionFor(target, target.prepare, 'MMMM', 2),
      stateExpression,
      clipExpression,
      action: () => dispatchEditingKey('Backspace', 'Backspace', 8),
      initialValue: 'MMMM',
      initialCaret: 2,
      expectedValue: 'MMM',
      expectedCaret: 1,
    });

    await runCase({
      name: `${target.name} Delete`,
      // Forward Delete normally leaves the caret at the same visual x. Right
      // alignment makes the shorter post-delete line move that caret by one
      // glyph while preserving its logical offset, so the paint glide has
      // distinct endpoints that can be sampled deterministically.
      prepare: `(()=>{const element=${target.expression};` +
          `element.style.textAlign='right';` +
          `qa.${target.prepare}(element,"MMMM",1);})()`,
      stateExpression,
      clipExpression,
      action: () => dispatchEditingKey('Delete', 'Delete', 46),
      initialValue: 'MMMM',
      initialCaret: 1,
      expectedValue: 'MMM',
      expectedCaret: 1,
    });
  }

  const report = {
    ok: failures.length === 0,
    executable: chromePath,
    ownedProcess: {
      rootPid: browser.pid,
      userDataDir: profileDir,
      cleanup: 'Browser.close followed only by exact owned PID-tree fallback',
    },
    implementation: 'Blink native paint-only caret glide',
    fixture: 'local HTTP, transparent text, magenta caret, disposable profile',
    isolation: 'glyph ink is transparent; OS clipboard is not accessed',
    invariants: [
      'DOM value and logical caret offset commit before animation sampling',
      'at least one painted caret position lies strictly between endpoints',
      'rapid caret paint stays within endpoints and does not overshoot',
      'final endpoint is stable while field geometry never changes',
      'ArrowRight is a no-DOM-change positive control for pixel sampling',
      'input, textarea and contenteditable cover typing, paste-sized insertion, Backspace and Delete',
    ],
    policyVerification: {
      reducedMotion: 'qa/verify_focus_caret_motion.mjs static gate',
      passwordFields: 'qa/verify_focus_caret_motion.mjs asserts IsInPasswordField gate',
    },
    results,
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
