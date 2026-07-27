#!/usr/bin/env node

// Runtime QA for the browser-native FocusBlock URLLoaderFactory proxy.
// Uses only a disposable profile, a local HTTP fixture and the DevTools
// protocol exposed by the executable passed on the command line.

import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import {createServer} from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..');
const defaultChrome = path.join(
    projectRoot, 'build', 'src', 'out', 'Default', 'chrome.exe');
const chromePath = path.resolve(process.argv[2] || defaultChrome);
const reportPath = process.argv[3] ? path.resolve(process.argv[3]) : null;

assert.ok(fs.existsSync(chromePath), [
  'Focus Browser executable was not found.',
  'Usage: node qa/verify_focusblock_runtime.mjs <chrome.exe> [report.json]',
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
  const suffix = lastError ? ` Last error: ${lastError.message}` : '';
  throw new Error(`Timed out waiting for ${description}.${suffix}`);
}

class CdpSession {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.socket = null;
    this.nextId = 1;
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
      if (message.id && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) {
          pending.reject(new Error(
              `${message.error.code}: ${message.error.message}`));
        } else {
          pending.resolve(message.result ?? {});
        }
        return;
      }
      if (!message.method) {
        return;
      }
      for (const listener of this.listeners.get(message.method) ?? []) {
        listener(message.params ?? {});
      }
    });
    this.socket.addEventListener('close', () => {
      for (const {reject} of this.pending.values()) {
        reject(new Error('DevTools WebSocket closed'));
      }
      this.pending.clear();
    });
  }

  on(method, listener) {
    if (!this.listeners.has(method)) {
      this.listeners.set(method, []);
    }
    this.listeners.get(method).push(listener);
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
  if (browserSession?.socket?.readyState === WebSocket.OPEN) {
    try {
      await Promise.race([
        browserSession.send('Browser.close'),
        delay(2000),
      ]);
    } catch {
      // Fall through to exact-owned-PID cleanup below.
    }
  }
  if (await waitForChildExit(child, 8000)) {
    return;
  }
  // The child uses a unique disposable user-data-dir, so only its process tree
  // belongs to this harness. Never terminate unrelated chrome.exe processes.
  const killer = spawn(
      'taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
      {stdio: 'ignore', windowsHide: true});
  await waitForChildExit(killer, 5000);
  await waitForChildExit(child, 5000);
}

function fixtureHtml() {
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>FocusBlock native runtime fixture</title>
<body>
<main id="fixture-ready">FocusBlock runtime fixture</main>
<script>
  window.__focusBlockFixture = {
    executed: [],
    runScript(url, label) {
      return new Promise(resolve => {
        const script = document.createElement('script');
        let settled = false;
        const finish = outcome => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          script.remove();
          resolve({label, outcome, url});
        };
        const timer = setTimeout(() => finish('timeout'), 12000);
        script.async = true;
        script.src = url;
        script.onload = () => finish('loaded');
        script.onerror = () => finish('error');
        document.head.append(script);
      });
    },
  };
</script>
<script src="${adOrigin}/cold-ad.js?phase=cold-start"></script>
</body>
</html>`;
}

const hits = {
  total: 0,
  byRoute: {},
  requests: [],
};

const server = createServer((request, response) => {
  const host = request.headers.host || '';
  const url = new URL(request.url || '/', `http://${host || 'localhost'}`);
  hits.total += 1;
  hits.byRoute[url.pathname] = (hits.byRoute[url.pathname] || 0) + 1;
  hits.requests.push({host, path: url.pathname, search: url.search});

  const javascript = source => {
    response.writeHead(200, {
      'Content-Type': 'application/javascript; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    response.end(source);
  };

  if (url.pathname === '/') {
    response.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    response.end(fixtureHtml());
    return;
  }
  if (url.pathname === '/allowed.js') {
    javascript(`window.__focusBlockFixture.executed.push('allowed-direct');`);
    return;
  }
  if (url.pathname === '/redirect-allowed') {
    response.writeHead(302, {
      Location: `/allowed-after-redirect.js${url.search}`,
      'Cache-Control': 'no-store',
    });
    response.end();
    return;
  }
  if (url.pathname === '/allowed-after-redirect.js') {
    javascript(`window.__focusBlockFixture.executed.push('allowed-redirect');`);
    return;
  }
  if (url.pathname === '/redirect-ad') {
    const location =
        `http://ad.doubleclick.net:${server.address().port}` +
        `/redirect-ad.js${url.search}`;
    response.writeHead(302, {Location: location, 'Cache-Control': 'no-store'});
    response.end();
    return;
  }
  if (url.pathname === '/cold-ad.js' ||
      url.pathname === '/direct-ad.js' ||
      url.pathname === '/redirect-ad.js') {
    javascript(`window.__focusBlockFixture.executed.push(` +
               `${JSON.stringify(url.pathname)});`);
    return;
  }
  response.writeHead(404, {'Content-Type': 'text/plain; charset=utf-8'});
  response.end('not found');
});

await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});

const fixturePort = server.address().port;
const fixtureOrigin = `http://127.0.0.1:${fixturePort}`;
const adOrigin = `http://ad.doubleclick.net:${fixturePort}`;
const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focusblock-native-runtime-'));
const stdoutChunks = [];
const stderrChunks = [];
const headed = process.env.FOCUS_QA_HEADED === '1';
const launchArguments = [
  ...(headed ? ['--window-size=1280,900'] : [
    '--headless=new',
    '--disable-gpu',
  ]),
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-default-apps',
  '--disable-dns-prefetch',
  '--disable-sync',
  '--disable-features=HttpsUpgrades,HttpsFirstBalancedModeAutoEnable',
  '--no-default-browser-check',
  '--no-first-run',
  '--no-proxy-server',
  '--noerrdialogs',
  '--remote-debugging-port=0',
  '--remote-allow-origins=*',
  '--host-resolver-rules=MAP ad.doubleclick.net 127.0.0.1',
  `--user-data-dir=${profileDir}`,
  `${fixtureOrigin}/`,
];
const browser = spawn(chromePath, launchArguments, {
  cwd: path.dirname(chromePath),
  stdio: ['ignore', 'pipe', 'pipe'],
  windowsHide: true,
});

const captureOutput = (chunks, chunk) => {
  if (chunks.reduce((total, item) => total + item.length, 0) < 1024 * 1024) {
    chunks.push(Buffer.from(chunk));
  }
};
browser.stdout.on('data', chunk => captureOutput(stdoutChunks, chunk));
browser.stderr.on('data', chunk => captureOutput(stderrChunks, chunk));

let browserSession = null;
let pageSession = null;
let report = null;
let primaryError = null;

try {
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  const debugPort = await waitFor(() => {
    assert.equal(browser.exitCode, null,
                 `Focus Browser exited during startup (${browser.exitCode})`);
    if (!fs.existsSync(portFile)) {
      return null;
    }
    const value = Number(fs.readFileSync(portFile, 'utf8').split(/\r?\n/)[0]);
    return Number.isInteger(value) && value > 0 ? value : null;
  }, 'DevToolsActivePort', 45000);

  const version = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
    return response.ok ? response.json() : null;
  }, 'DevTools version endpoint', 15000);
  browserSession = new CdpSession(version.webSocketDebuggerUrl);
  await browserSession.connect();

  const targets = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${debugPort}/json/list`);
    const entries = await response.json();
    return entries.some(entry => entry.type === 'page') ? entries : null;
  }, 'a page target');
  const target = targets.find(entry => entry.type === 'page');
  assert.ok(target?.webSocketDebuggerUrl, 'No debuggable page target found');

  pageSession = new CdpSession(target.webSocketDebuggerUrl);
  await pageSession.connect();
  const requestUrls = new Map();
  const networkEvents = [];
  pageSession.on('Network.requestWillBeSent', event => {
    const previousUrl = requestUrls.get(event.requestId) || null;
    requestUrls.set(event.requestId, event.request.url);
    networkEvents.push({
      kind: 'request',
      requestId: event.requestId,
      url: event.request.url,
      previousUrl,
      redirected: Boolean(event.redirectResponse),
      redirectStatus: event.redirectResponse?.status ?? null,
      type: event.type,
    });
  });
  pageSession.on('Network.loadingFailed', event => {
    networkEvents.push({
      kind: 'failed',
      requestId: event.requestId,
      url: requestUrls.get(event.requestId) || null,
      errorText: event.errorText,
      blockedReason: event.blockedReason || null,
      type: event.type,
      canceled: Boolean(event.canceled),
    });
  });
  pageSession.on('Network.loadingFinished', event => {
    networkEvents.push({
      kind: 'finished',
      requestId: event.requestId,
      url: requestUrls.get(event.requestId) || null,
      encodedDataLength: event.encodedDataLength,
    });
  });
  await pageSession.send('Network.enable');
  await pageSession.send('Page.enable');
  await pageSession.send('Runtime.enable');
  await pageSession.send('Page.navigate', {url: `${fixtureOrigin}/`});
  await waitFor(() => evaluate(pageSession,
      `Boolean(window.__focusBlockFixture && ` +
      `document.querySelector('#fixture-ready'))`), 'fixture page');

  let sequence = 0;
  const runProbe = async (baseUrl, label) => {
    const token = `${label}-${Date.now()}-${++sequence}`;
    const separator = baseUrl.includes('?') ? '&' : '?';
    const url = `${baseUrl}${separator}token=${encodeURIComponent(token)}`;
    const result = await evaluate(pageSession,
        `window.__focusBlockFixture.runScript(` +
        `${JSON.stringify(url)}, ${JSON.stringify(label)})`);
    await delay(100);
    const events = networkEvents.filter(event =>
      typeof event.url === 'string' && event.url.includes(token));
    return {token, url, result, events};
  };

  // The native engine builds its bundled lists asynchronously. Poll a known
  // EasyList host until the proxy itself reports ERR_BLOCKED_BY_CLIENT, rather
  // than mistaking DNS, HSTS or TLS failures for ad blocking.
  let readiness = null;
  const readinessDeadline = Date.now() + 45000;
  for (let attempt = 1; Date.now() < readinessDeadline; ++attempt) {
    const probe = await runProbe(`${adOrigin}/direct-ad.js`, 'engine-ready');
    const blocked = probe.events.find(event =>
      event.kind === 'failed' &&
      event.errorText === 'net::ERR_BLOCKED_BY_CLIENT');
    if (blocked) {
      readiness = {attempts: attempt, event: blocked};
      break;
    }
    await delay(400);
  }
  assert.ok(readiness,
            'FocusBlock engine did not report ERR_BLOCKED_BY_CLIENT');

  const beforeRoutes = {...hits.byRoute};
  const allowedDirect = await runProbe(
      `${fixtureOrigin}/allowed.js`, 'allowed-direct');
  const allowedRedirect = await runProbe(
      `${fixtureOrigin}/redirect-allowed`, 'allowed-redirect');
  const directDoubleclick = await runProbe(
      `${adOrigin}/direct-ad.js`, 'blocked-direct-doubleclick');
  const redirectDoubleclick = await runProbe(
      `${fixtureOrigin}/redirect-ad`, 'blocked-redirect-doubleclick');

  const routeDelta = route =>
    (hits.byRoute[route] || 0) - (beforeRoutes[route] || 0);
  const blockedFailure = probe => probe.events.find(event =>
    event.kind === 'failed' &&
    event.errorText === 'net::ERR_BLOCKED_BY_CLIENT');
  const executed = await evaluate(
      pageSession, '[...window.__focusBlockFixture.executed]');

  assert.equal(allowedDirect.result.outcome, 'loaded',
               'Same-origin direct script did not load');
  assert.equal(routeDelta('/allowed.js'), 1,
               'Allowed direct fixture did not reach the server once');
  assert.equal(allowedRedirect.result.outcome, 'loaded',
               'Same-origin redirected script did not load');
  assert.equal(routeDelta('/redirect-allowed'), 1,
               'Allowed redirect endpoint was not reached once');
  assert.equal(routeDelta('/allowed-after-redirect.js'), 1,
               'Allowed redirect target was not reached once');
  assert.ok(executed.includes('allowed-direct'));
  assert.ok(executed.includes('allowed-redirect'));

  assert.equal(directDoubleclick.result.outcome, 'error',
               'Direct doubleclick script unexpectedly loaded');
  assert.ok(blockedFailure(directDoubleclick),
            'Direct doubleclick request lacked ERR_BLOCKED_BY_CLIENT');
  assert.equal(routeDelta('/direct-ad.js'), 0,
               'Blocked direct doubleclick target reached the server');

  assert.equal(redirectDoubleclick.result.outcome, 'error',
               'Redirected doubleclick script unexpectedly loaded');
  assert.ok(redirectDoubleclick.events.some(event =>
    event.kind === 'request' &&
    new URL(event.url).hostname === '127.0.0.1' &&
    new URL(event.url).pathname === '/redirect-ad'),
    'Redirect start was not visible in the network request chain');
  // The proxy intentionally rejects a disallowed redirect before forwarding
  // OnReceiveRedirect to the renderer. CDP therefore need not expose the
  // advertising target URL; the network error and zero target hits are the
  // security contract.
  assert.ok(blockedFailure(redirectDoubleclick),
            'Redirected doubleclick request lacked ERR_BLOCKED_BY_CLIENT');
  assert.equal(routeDelta('/redirect-ad'), 1,
               'Redirect endpoint was not reached once');
  assert.equal(routeDelta('/redirect-ad.js'), 0,
               'Blocked redirect target reached the server');
  assert.equal(hits.byRoute['/cold-ad.js'] || 0, 0,
               'Cold-start advertising request reached the network');

  const blockedObserved = [directDoubleclick, redirectDoubleclick]
      .filter(probe => Boolean(blockedFailure(probe))).length;
  report = {
    status: 'PASS',
    executable: chromePath,
    mode: headed ? 'headed' : 'headless',
    profileKind: 'unique-temporary',
    fixtureOrigin,
    engineReadiness: readiness,
    checks: {
      coldStart: {
        targetHits: hits.byRoute['/cold-ad.js'] || 0,
      },
      allowedDirect: {
        outcome: allowedDirect.result.outcome,
        serverHits: routeDelta('/allowed.js'),
      },
      allowedRedirect: {
        outcome: allowedRedirect.result.outcome,
        redirectHits: routeDelta('/redirect-allowed'),
        targetHits: routeDelta('/allowed-after-redirect.js'),
      },
      directDoubleclick: {
        outcome: directDoubleclick.result.outcome,
        targetHits: routeDelta('/direct-ad.js'),
        failure: blockedFailure(directDoubleclick),
      },
      redirectDoubleclick: {
        outcome: redirectDoubleclick.result.outcome,
        redirectHits: routeDelta('/redirect-ad'),
        targetHits: routeDelta('/redirect-ad.js'),
        failure: blockedFailure(redirectDoubleclick),
      },
    },
    nativeCounters: {
      cdpReadable: false,
      reason: 'Native session/site counters are exposed to the Views bubble, ' +
          'not to a CDP or WebUI endpoint.',
      blockedRequestsObserved: blockedObserved,
      minimumExpectedSessionIncrement: blockedObserved,
    },
  };
} catch (error) {
  primaryError = error;
  report = {
    status: 'FAIL',
    executable: chromePath,
    mode: headed ? 'headed' : 'headless',
    fixtureOrigin,
    error: error.stack || String(error),
    recentServerRequests: hits.requests.slice(-30),
    browserStderr: Buffer.concat(stderrChunks).toString('utf8').slice(-12000),
  };
} finally {
  pageSession?.close();
  await stopOwnedBrowser(browser, browserSession);
  browserSession?.close();
  await new Promise(resolve => server.close(resolve));
  try {
    // mkdtempSync created this exact direct child of the OS temp directory.
    assert.equal(path.dirname(profileDir), path.resolve(os.tmpdir()));
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
