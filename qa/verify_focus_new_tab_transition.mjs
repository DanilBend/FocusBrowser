#!/usr/bin/env node

// Runtime contract for the Focus Browser new-tab entry transition. Attach to
// an already-running disposable browser with --remote-debugging-port=<port>.
// This script creates and closes only its own temporary target; it never
// launches or terminates the browser.

import assert from 'node:assert/strict';

const [portArgument = '9341'] = process.argv.slice(2);
const port = Number(portArgument);
assert.ok(Number.isInteger(port) && port > 0 && port <= 65535, [
  'A valid remote-debugging port is required.',
  'Usage: node qa/verify_focus_new_tab_transition.mjs <port>',
].join('\n'));

const endpoint = `http://127.0.0.1:${port}`;
const sampleWindowMs = 650;
const hardDeadlineMs = 30000;
const hardDeadlineAt = Date.now() + hardDeadlineMs;

const remainingHardDeadlineMs = () =>
  Math.max(0, hardDeadlineAt - Date.now());

const withDeadline = (promise, description, timeoutMs = 5000) => {
  const boundedTimeoutMs = Math.min(timeoutMs, remainingHardDeadlineMs());
  if (boundedTimeoutMs <= 0) {
    return Promise.reject(new Error(
        `Hard ${hardDeadlineMs} ms QA deadline reached during ${description}`));
  }
  let timer;
  return Promise.race([
    promise,
    new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error(
          `Timed out after ${boundedTimeoutMs} ms during ${description}`)),
      boundedTimeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
};

const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitFor(probe, description, timeoutMs = 10000) {
  const deadline = Math.min(Date.now() + timeoutMs, hardDeadlineAt);
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await withDeadline(
          Promise.resolve().then(probe), `${description} probe`, 2000);
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await delay(50);
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
    await withDeadline(new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, {once: true});
      this.socket.addEventListener('error', reject, {once: true});
    }), 'opening a DevTools WebSocket');
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

  send(method, params = {}, timeoutMs = 5000) {
    assert.equal(this.socket?.readyState, WebSocket.OPEN,
                 'DevTools WebSocket is not open');
    const id = this.nextId++;
    const boundedTimeoutMs = Math.min(timeoutMs, remainingHardDeadlineMs());
    if (boundedTimeoutMs <= 0) {
      return Promise.reject(new Error(
          `Hard ${hardDeadlineMs} ms QA deadline reached before ${method}`));
    }
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(
            `Timed out after ${boundedTimeoutMs} ms waiting for ${method}`));
      }, boundedTimeoutMs);
      this.pending.set(id, {
        resolve: value => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: error => {
          clearTimeout(timer);
          reject(error);
        },
      });
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
    awaitPromise: false,
    returnByValue: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ||
                    response.exceptionDetails.text ||
                    'Runtime evaluation failed');
  }
  return response.result?.value;
}

const samplerSource = `(() => {
  const sampleWindowMs = ${sampleWindowMs};
  const timeoutMs = 10000;
  const navigationStart = performance.now();
  const samples = [];
  let appearedAt = null;

  window.__focusNewTabTransitionQaResult = null;
  const finish = (status, now) => {
    window.__focusNewTabTransitionQaResult = {
      status,
      href: location.href,
      appearedAt,
      sampledDurationMs: appearedAt === null ? 0 : now - appearedAt,
      samples,
    };
  };

  const effectiveBackground = app => {
    for (const element of [app, document.body, document.documentElement]) {
      if (!element) {
        continue;
      }
      const color = getComputedStyle(element).backgroundColor;
      if (color && color !== 'transparent' &&
          color !== 'rgba(0, 0, 0, 0)') {
        return color;
      }
    }
    return '';
  };

  const sample = now => {
    const app = document.querySelector('ntp-app');
    const home = app?.shadowRoot?.querySelector('#focusHome');
    const style = home ? getComputedStyle(home) : null;
    if (home) {
      appearedAt ??= now;
    }
    samples.push({
      elapsedNavigationMs: now - navigationStart,
      elapsedMs: appearedAt === null ? null : now - appearedAt,
      homePresent: Boolean(home),
      backgroundColor: effectiveBackground(app),
      opacity: style?.opacity ?? null,
      animationName: style?.animationName ?? null,
      transform: style?.transform ?? null,
      animationsDisabled: app ?
          getComputedStyle(app)
              .getPropertyValue('--cr-animations-disabled').trim() === '1' :
          null,
    });
    if (appearedAt !== null && now - appearedAt >= sampleWindowMs) {
      finish('complete', now);
      return;
    }

    if (now - navigationStart >= timeoutMs) {
      finish('timeout', now);
      return;
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
})();`;

const parseCssRgb = value => {
  const match = String(value).match(
      /^rgba?\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)/);
  return match ? match.slice(1, 4).map(Number) : null;
};

let browserSession = null;
let pageSession = null;
let targetId = null;
let primaryError = null;
let report = null;
let cleanupWarning = null;

try {
  const version = await fetch(`${endpoint}/json/version`).then(response => {
    assert.ok(response.ok, `DevTools endpoint returned ${response.status}`);
    return response.json();
  });
  assert.ok(version.webSocketDebuggerUrl,
            'Browser DevTools WebSocket URL is unavailable');

  browserSession = new CdpSession(version.webSocketDebuggerUrl);
  await browserSession.connect();

  const created = await browserSession.send('Target.createTarget', {
    url: 'about:blank',
    background: true,
  });
  targetId = created.targetId;
  assert.ok(targetId, 'DevTools did not create a temporary page target');

  const target = await waitFor(async () => {
    const response = await fetch(`${endpoint}/json/list`);
    if (!response.ok) {
      return null;
    }
    const targets = await response.json();
    return targets.find(candidate =>
      candidate.id === targetId && candidate.type === 'page' &&
      candidate.webSocketDebuggerUrl);
  }, 'the temporary page target');

  pageSession = new CdpSession(target.webSocketDebuggerUrl);
  await pageSession.connect();
  await pageSession.send('Page.enable');
  await pageSession.send('Runtime.enable');
  await pageSession.send('Page.addScriptToEvaluateOnNewDocument', {
    source: samplerSource,
  });
  await pageSession.send('Page.navigate', {url: 'chrome://new-tab-page/'});

  await waitFor(() => evaluate(pageSession, `(() =>
    location.href.startsWith('chrome://new-tab-page/') &&
    Boolean(document.querySelector('ntp-app')))()`),
  'the Focus new-tab document');
  const samplerInstalled = await evaluate(pageSession, `(() => {
    if (typeof window.__focusNewTabTransitionQaResult !== 'undefined') {
      return true;
    }
    ${samplerSource}
    return true;
  })()`);
  assert.equal(samplerInstalled, true,
               'Failed to install the Focus transition sampler');

  const transition = await waitFor(() => evaluate(pageSession, `(() => {
    if (!location.href.startsWith('chrome://new-tab-page/')) {
      return null;
    }
    return window.__focusNewTabTransitionQaResult;
  })()`), 'the Focus new-tab transition samples', 15000);

  const checks = {
    navigatedToNewTab:
        transition.href.startsWith('chrome://new-tab-page/'),
    focusHomeAppeared:
        transition.status === 'complete' && transition.appearedAt !== null,
    sampledForFullWindow:
        transition.sampledDurationMs >= sampleWindowMs - 25 &&
        transition.samples.length >= 2,
    opacityAlwaysOne: transition.samples.every(sample =>
      Number.isFinite(Number(sample.opacity)) &&
      Math.abs(Number(sample.opacity) - 1) <= Number.EPSILON),
    fullPageMotionAbsent: transition.samples.every(sample =>
      sample.animationName === 'none' && sample.transform === 'none'),
    opaqueBackgroundAlwaysPresent: transition.samples.every(sample =>
      Boolean(parseCssRgb(sample.backgroundColor))),
    noNearWhiteBackgroundFrame: transition.samples.every(sample => {
      const rgb = parseCssRgb(sample.backgroundColor);
      return rgb && rgb.reduce((sum, channel) => sum + channel, 0) / 3 < 220;
    }),
  };
  assert.ok(Object.values(checks).every(Boolean), JSON.stringify({
    checks,
    transition,
  }));

  report = {
    status: 'PASS',
    remoteDebuggingPort: port,
    targetKind: 'owned-temporary',
    checks,
    transition,
  };
} catch (error) {
  primaryError = error;
  report = {
    status: 'FAIL',
    remoteDebuggingPort: port,
    targetKind: 'owned-temporary',
    error: error.stack || String(error),
  };
} finally {
  pageSession?.close();
  if (targetId && browserSession) {
    try {
      const closed =
          await browserSession.send('Target.closeTarget', {targetId});
      if (closed.success !== true) {
        cleanupWarning = 'DevTools did not confirm temporary target closure';
      }
    } catch (error) {
      cleanupWarning = error.message;
    }
  }
  browserSession?.close();
}

if (cleanupWarning) {
  report.cleanupWarning = cleanupWarning;
}
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (primaryError || cleanupWarning) {
  process.exitCode = 1;
}
