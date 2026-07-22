#!/usr/bin/env node

// Regression smoke test for the automatic password-save bubble. The harness
// opens Password Manager Internals before submitting a local login form so the
// native AskUserOrSavePassword and Show password prompt signals prove that the
// browser reached the same path used after a successful Google sign-in. Only
// generated QA credentials, a unique disposable profile, and a local HTTP
// server are used. Cleanup is restricted to the exact process tree spawned by
// this script.

import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
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
  'Usage: node qa/verify_password_bubble_runtime.mjs <chrome.exe> [report.json]',
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
    await delay(100);
  }
  throw new Error(
      `Timed out waiting for ${description}` +
      (lastError ? `: ${lastError.message}` : ''));
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
      for (const pending of this.pending.values()) {
        pending.reject(new Error('DevTools WebSocket closed'));
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

async function waitForChildExit(child, timeoutMs) {
  if (!child || child.exitCode !== null) {
    return true;
  }
  return Promise.race([
    new Promise(resolve => child.once('exit', () => resolve(true))),
    delay(timeoutMs).then(() => false),
  ]);
}

async function stopOwnedBrowser(child, browserSession) {
  if (!child || child.exitCode !== null) {
    return;
  }
  if (browserSession) {
    await Promise.race([
      browserSession.send('Browser.close').catch(() => null),
      delay(2000),
    ]);
  }
  if (await waitForChildExit(child, 8000)) {
    return;
  }
  // This PID was launched with a unique QA profile, so only this exact process
  // tree belongs to the harness. Never terminate chrome.exe processes by name.
  const killer = spawn(
      'taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
      {stdio: 'ignore', windowsHide: true});
  await waitForChildExit(killer, 5000);
  await waitForChildExit(child, 5000);
}

function collectCrashDumps(root) {
  const records = [];
  const visit = directory => {
    let entries;
    try {
      entries = fs.readdirSync(directory, {withFileTypes: true});
    } catch {
      return;
    }
    for (const entry of entries) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        visit(absolute);
        continue;
      }
      if (!entry.isFile() || path.extname(entry.name).toLowerCase() !== '.dmp') {
        continue;
      }
      try {
        const stat = fs.statSync(absolute);
        records.push({
          relativePath: path.relative(root, absolute),
          size: stat.size,
          modifiedMs: stat.mtimeMs,
        });
      } catch {
        // A report can be atomically moved while Crashpad finishes it. The
        // next inventory pass will see the final path.
      }
    }
  };
  visit(root);
  return records.sort((left, right) =>
    left.relativePath.localeCompare(right.relativePath));
}

function crashKey(record) {
  return `${record.relativePath}\0${record.size}\0${record.modifiedMs}`;
}

async function getJson(url) {
  const response = await fetch(url);
  assert.ok(response.ok, `${url} returned HTTP ${response.status}`);
  return response.json();
}

async function targetForId(debugPort, targetId) {
  return waitFor(async () => {
    const targets = await getJson(`http://127.0.0.1:${debugPort}/json/list`);
    const target = targets.find(entry => entry.id === targetId);
    return target?.webSocketDebuggerUrl ? target : null;
  }, `DevTools target ${targetId}`, 15000);
}

const runToken = crypto.randomBytes(8).toString('hex');
const qaUsername = `focus-qa-${runToken}@focus.invalid`;
const qaPassword = `Focus-QA-${runToken}-Only!`;
const fixtureState = {
  requests: [],
  loginPosts: 0,
  expectedUsername: false,
  expectedPassword: false,
  welcomeLoads: 0,
};

function loginHtml() {
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Focus password bubble QA</title>
<main>
  <h1>Disposable login fixture</h1>
  <form id="login-form" action="/authenticate" method="post" autocomplete="on">
    <label>Username
      <input id="username" name="username" type="email" autocomplete="username">
    </label>
    <label>Password
      <input id="password" name="password" type="password"
             autocomplete="current-password">
    </label>
    <button id="submit-login" type="submit">Sign in</button>
  </form>
</main>
</html>`;
}

function welcomeHtml() {
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Focus password bubble QA complete</title>
<main id="login-complete">Local QA login accepted</main>
</html>`;
}

const server = createServer((request, response) => {
  const url = new URL(request.url || '/', 'http://focus-login.test');
  fixtureState.requests.push({method: request.method, path: url.pathname});

  const html = body => {
    response.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    response.end(body);
  };

  if (request.method === 'GET' && url.pathname === '/login') {
    html(loginHtml());
    return;
  }
  if (request.method === 'POST' && url.pathname === '/authenticate') {
    let body = '';
    request.setEncoding('utf8');
    request.on('data', chunk => {
      body += chunk;
      if (body.length > 16384) {
        request.destroy();
      }
    });
    request.on('end', () => {
      const form = new URLSearchParams(body);
      fixtureState.loginPosts += 1;
      fixtureState.expectedUsername = form.get('username') === qaUsername;
      fixtureState.expectedPassword = form.get('password') === qaPassword;
      response.writeHead(303, {
        Location: '/welcome',
        'Cache-Control': 'no-store',
      });
      response.end();
    });
    return;
  }
  if (request.method === 'GET' && url.pathname === '/welcome') {
    fixtureState.welcomeLoads += 1;
    html(welcomeHtml());
    return;
  }
  if (url.pathname === '/favicon.ico') {
    response.writeHead(204, {'Cache-Control': 'no-store'});
    response.end();
    return;
  }
  response.writeHead(404, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  response.end('not found');
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

const fixturePort = server.address().port;
const fixtureOrigin = `http://focus-login.test:${fixturePort}`;
const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focus-password-bubble-runtime-'));
const crashInventoryBefore = collectCrashDumps(profileDir);
const stdoutChunks = [];
const stderrChunks = [];
let stdoutBytes = 0;
let stderrBytes = 0;
const launchArguments = [
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-default-apps',
  '--disable-dns-prefetch',
  '--disable-sync',
  '--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable',
  '--enable-logging=stderr',
  '--no-default-browser-check',
  '--no-first-run',
  '--no-proxy-server',
  '--noerrdialogs',
  '--remote-debugging-port=0',
  '--remote-allow-origins=*',
  '--window-size=1000,760',
  '--host-resolver-rules=MAP focus-login.test 127.0.0.1',
  `--user-data-dir=${profileDir}`,
  'about:blank',
];
const browser = spawn(chromePath, launchArguments, {
  cwd: path.dirname(chromePath),
  stdio: ['ignore', 'pipe', 'pipe'],
  windowsHide: true,
});
let spawnError = null;
browser.once('error', error => {
  spawnError = error;
});

const captureOutput = (chunks, chunk, byteCount) => {
  if (byteCount < 1024 * 1024) {
    chunks.push(Buffer.from(chunk));
  }
  return byteCount + chunk.length;
};
browser.stdout.on('data', chunk => {
  stdoutBytes = captureOutput(stdoutChunks, chunk, stdoutBytes);
});
browser.stderr.on('data', chunk => {
  stderrBytes = captureOutput(stderrChunks, chunk, stderrBytes);
});

let browserSession = null;
let internalsSession = null;
let loginSession = null;
let report = null;
let primaryError = null;
const startedAt = Date.now();

const assertBrowserAlive = async () => {
  assert.ifError(spawnError);
  assert.equal(browser.exitCode, null,
               `Focus Browser exited unexpectedly (${browser.exitCode})`);
  assert.ok(browserSession, 'Browser DevTools session was not created');
  const version = await browserSession.send('Browser.getVersion');
  assert.match(version.product || '', /Chrome|Chromium|Focus/i);
  return version;
};

try {
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  const debugPort = await waitFor(() => {
    assert.ifError(spawnError);
    assert.equal(browser.exitCode, null,
                 `Focus Browser exited during startup (${browser.exitCode})`);
    if (!fs.existsSync(portFile)) {
      return null;
    }
    const value = Number(fs.readFileSync(portFile, 'utf8').split(/\r?\n/)[0]);
    return Number.isInteger(value) && value > 0 ? value : null;
  }, 'DevToolsActivePort', 45000);

  const version = await waitFor(
      () => getJson(`http://127.0.0.1:${debugPort}/json/version`),
      'DevTools version endpoint', 15000);
  browserSession = new CdpSession(version.webSocketDebuggerUrl);
  await browserSession.connect();

  const internalsTargetId = (await browserSession.send('Target.createTarget', {
    url: 'chrome://password-manager-internals/',
    background: true,
  })).targetId;
  const internalsTarget = await targetForId(debugPort, internalsTargetId);
  internalsSession = new CdpSession(internalsTarget.webSocketDebuggerUrl);
  await internalsSession.connect();
  await internalsSession.send('Runtime.enable');
  await waitFor(() => evaluate(internalsSession, `
    document.title === 'Password Manager Internals' &&
    document.querySelector('#CurrentlyRecording')?.checked === true
  `), 'Password Manager Internals log receiver', 20000);

  const loginTargetId = (await browserSession.send('Target.createTarget', {
    url: `${fixtureOrigin}/login`,
    background: false,
  })).targetId;
  const loginTarget = await targetForId(debugPort, loginTargetId);
  loginSession = new CdpSession(loginTarget.webSocketDebuggerUrl);
  await loginSession.connect();
  await loginSession.send('Page.enable');
  await loginSession.send('Runtime.enable');
  await loginSession.send('Page.bringToFront');
  await waitFor(() => evaluate(loginSession, `
    document.readyState === 'complete' &&
    Boolean(document.querySelector('#login-form'))
  `), 'local login form', 20000);

  const typeInto = async (selector, text) => {
    const focused = await evaluate(loginSession, `(() => {
      const input = document.querySelector(${JSON.stringify(selector)});
      if (!input) return false;
      input.focus();
      input.select();
      return document.activeElement === input;
    })()`);
    assert.equal(focused, true, `Could not focus ${selector}`);
    await loginSession.send('Input.insertText', {text});
    const valueMatches = await evaluate(loginSession, `
      document.querySelector(${JSON.stringify(selector)})?.value ===
          ${JSON.stringify(text)}
    `);
    assert.equal(valueMatches, true, `CDP did not enter ${selector}`);
  };

  await typeInto('#username', qaUsername);
  await typeInto('#password', qaPassword);

  const submitPoint = await evaluate(loginSession, `(() => {
    const button = document.querySelector('#submit-login');
    if (!button) return null;
    const rect = button.getBoundingClientRect();
    return {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2};
  })()`);
  assert.ok(submitPoint && Number.isFinite(submitPoint.x) &&
            Number.isFinite(submitPoint.y), 'Submit button has no click point');
  await loginSession.send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: submitPoint.x,
    y: submitPoint.y,
    button: 'left',
    clickCount: 1,
  });
  await loginSession.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: submitPoint.x,
    y: submitPoint.y,
    button: 'left',
    clickCount: 1,
  });

  await waitFor(() =>
    fixtureState.loginPosts === 1 &&
    fixtureState.expectedUsername &&
    fixtureState.expectedPassword &&
    fixtureState.welcomeLoads >= 1,
    'successful local credential submission', 20000);
  await waitFor(() => evaluate(loginSession, `
    document.readyState === 'complete' &&
    Boolean(document.querySelector('#login-complete'))
  `), 'successful login landing page', 20000);

  const passwordSignals = await waitFor(async () => {
    await assertBrowserAlive();
    const signals = await evaluate(internalsSession, `(() => {
      const text = document.querySelector('#log-entries')?.innerText || '';
      return {
        askUserOrSavePassword:
            text.includes('PasswordManager::AskUserOrSavePassword'),
        showPasswordPrompt: text.includes('Show password prompt'),
        renderedLogNodes:
            document.querySelector('#log-entries')?.childElementCount || 0,
      };
    })()`);
    return signals.askUserOrSavePassword && signals.showPasswordPrompt ?
        signals : null;
  }, 'automatic password-save prompt signals', 20000);

  // Keep exercising both the browser-level and renderer-level CDP channels
  // after the prompt opens. The original regression terminated the browser
  // process synchronously while creating this bubble.
  const stabilityStartedAt = Date.now();
  while (Date.now() - stabilityStartedAt < 3000) {
    await assertBrowserAlive();
    const landingPageAlive = await evaluate(loginSession,
        `Boolean(document.querySelector('#login-complete'))`);
    assert.equal(landingPageAlive, true,
                 'Login renderer stopped responding after password prompt');
    await delay(100);
  }

  report = {
    status: 'PASS',
    executable: chromePath,
    profileKind: 'unique-temporary',
    credentials: 'generated-test-only',
    fixtureOrigin,
    browserPid: browser.pid,
    checks: {
      localCredentialPost: true,
      successfulLandingPage: true,
      askUserOrSavePassword: passwordSignals.askUserOrSavePassword,
      showPasswordPrompt: passwordSignals.showPasswordPrompt,
      passwordManagerLogNodes: passwordSignals.renderedLogNodes,
      browserProcessAliveAfterPrompt: browser.exitCode === null,
      browserCdpAliveAfterPrompt: true,
      pageCdpAliveAfterPrompt: true,
      stabilityWindowMs: Date.now() - stabilityStartedAt,
    },
    crashDumpsCreated: 0,
    elapsedMs: Date.now() - startedAt,
  };
} catch (error) {
  primaryError = error;
  report = {
    status: 'FAIL',
    executable: chromePath,
    profileKind: 'unique-temporary',
    credentials: 'generated-test-only',
    fixtureOrigin,
    browserPid: browser.pid,
    browserExitCode: browser.exitCode,
    fixture: {
      loginPosts: fixtureState.loginPosts,
      expectedUsername: fixtureState.expectedUsername,
      expectedPassword: fixtureState.expectedPassword,
      welcomeLoads: fixtureState.welcomeLoads,
      recentRequests: fixtureState.requests.slice(-20),
    },
    error: error.stack || String(error),
  };
} finally {
  loginSession?.close();
  internalsSession?.close();
  await stopOwnedBrowser(browser, browserSession);
  browserSession?.close();
  await new Promise(resolve => server.close(resolve));

  const crashInventoryAfter = collectCrashDumps(profileDir);
  const previousCrashKeys = new Set(crashInventoryBefore.map(crashKey));
  const newCrashDumps = crashInventoryAfter.filter(
      record => !previousCrashKeys.has(crashKey(record)));
  report.crashDumpsCreated = newCrashDumps.length;
  if (newCrashDumps.length > 0) {
    const crashError = new Error(
        `Disposable profile contains ${newCrashDumps.length} new crash dump(s)`);
    if (!primaryError) {
      primaryError = crashError;
      report.status = 'FAIL';
      report.error = crashError.stack;
    }
    report.newCrashDumps = newCrashDumps;
  }

  if (primaryError) {
    const redact = value => value
        .replaceAll(qaUsername, '[QA_USERNAME]')
        .replaceAll(qaPassword, '[QA_PASSWORD]');
    report.browserStdout = redact(
        Buffer.concat(stdoutChunks).toString('utf8').slice(-12000));
    report.browserStderr = redact(
        Buffer.concat(stderrChunks).toString('utf8').slice(-12000));
  }

  try {
    // mkdtempSync created this exact direct child of the OS temp directory.
    // Validate both the parent and prefix before recursive removal.
    assert.equal(path.dirname(profileDir), path.resolve(os.tmpdir()));
    assert.match(path.basename(profileDir),
                 /^focus-password-bubble-runtime-[A-Za-z0-9_-]+$/);
    fs.rmSync(profileDir, {recursive: true, force: true, maxRetries: 3});
  } catch (cleanupError) {
    report.profileCleanupWarning = cleanupError.message;
  }
}

const serialized = `${JSON.stringify(report, null, 2)}\n`;
if (reportPath) {
  fs.mkdirSync(path.dirname(reportPath), {recursive: true});
  fs.writeFileSync(reportPath, serialized, 'utf8');
}
process.stdout.write(serialized);
if (primaryError) {
  process.exitCode = 1;
}
