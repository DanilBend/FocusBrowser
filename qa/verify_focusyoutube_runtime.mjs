#!/usr/bin/env node

// Runtime migration smoke test for the built-in FocusYoutube component.
// The test owns one unique disposable profile and only ever terminates the
// exact browser process tree that it spawned.

import assert from 'node:assert/strict';
import {spawn, spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDir, '..');
const staticOnly = process.argv.includes('--static-only');
const positionalArgs = process.argv.slice(2).filter(arg => arg !== '--static-only');
const chromePath = path.resolve(
    positionalArgs[0] ||
    path.join(projectRoot, 'build', 'src', 'out', 'Default', 'chrome.exe'));
const reportPath = positionalArgs[1] ? path.resolve(positionalArgs[1]) : null;
const extensionId = 'jafokmemnknjknbdiklabcnhlpheefbm';
const extensionPage = `chrome-extension://${extensionId}/popup.html?runtime-qa=1`;

if (!staticOnly) {
  assert.ok(fs.existsSync(chromePath), [
    'Focus Browser executable was not found.',
    'Usage: node qa/verify_focusyoutube_runtime.mjs <chrome.exe> [report.json]',
    'Static audit: node qa/verify_focusyoutube_runtime.mjs --static-only',
  ].join('\n'));
}

const schemaPathCandidates = [
  path.join(projectRoot, 'source_overrides', 'third_party', 'focus_youtube',
      'shared', 'main.js'),
  path.join(projectRoot, 'build', 'src', 'third_party', 'focus_youtube',
      'shared', 'main.js'),
];
const schemaPath = schemaPathCandidates.find(candidate => fs.existsSync(candidate));
assert.ok(schemaPath, 'FocusYoutube shared/main.js was not found');

const schemaContext = {};
schemaContext.globalThis = schemaContext;
vm.createContext(schemaContext);
vm.runInContext(fs.readFileSync(schemaPath, 'utf8'), schemaContext, {
  filename: schemaPath,
});
const schema = schemaContext.FocusYoutubeSettings;
const behaviorIds = [...schema.behaviorIds];
const nativeBehaviorIds = [...schema.nativeBehaviorIds];
const hiddenBehaviorIds = behaviorIds.filter(
    id => !nativeBehaviorIds.includes(id));
assert.equal(behaviorIds.length, 93);
assert.equal(nativeBehaviorIds.length, 20);
assert.equal(hiddenBehaviorIds.length, 73);

function verifyExactHostPredicate() {
  const candidates = [
    path.join(projectRoot, 'source_overrides', 'chrome', 'browser', 'ui',
        'views', 'toolbar', 'toolbar_view.cc'),
    path.join(projectRoot, 'build', 'src', 'chrome', 'browser', 'ui', 'views',
        'toolbar', 'toolbar_view.cc'),
  ];
  const toolbarPath = candidates.find(candidate => fs.existsSync(candidate));
  assert.ok(toolbarPath, 'toolbar_view.cc was not found for visibility audit');
  const source = fs.readFileSync(toolbarPath, 'utf8');
  const body = source.match(
      /void ToolbarView::UpdateFocusYoutubeButtonVisibility\(WebContents\* tab\) \{([\s\S]*?)\n\}/)?.[1];
  assert.ok(body, 'FocusYoutube visibility function was not found');
  assert.match(body, /tab->GetVisibleURL\(\)/);
  assert.match(body, /context_url\.SchemeIs\(url::kHttpsScheme\)/);
  assert.deepEqual(
      [...body.matchAll(/host == "([^"]+)"/g)].map(match => match[1]),
      ['youtube.com', 'www.youtube.com', 'm.youtube.com']);
  assert.doesNotMatch(body, /DomainIs|ends_with|StartsWith/);
  return toolbarPath;
}

const toolbarPath = verifyExactHostPredicate();
if (staticOnly) {
  console.log(JSON.stringify({
    ok: true,
    mode: 'static-only',
    schemaVersion: schema.defaults.focus_youtube_schema_version,
    behaviorCount: behaviorIds.length,
    nativeBehaviorCount: nativeBehaviorIds.length,
    hiddenBehaviorCount: hiddenBehaviorIds.length,
    toolbarSource: toolbarPath,
    exactHosts: ['youtube.com', 'www.youtube.com', 'm.youtube.com'],
  }, null, 2));
  process.exit(0);
}
const delay = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

async function waitFor(probe, description, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await probe();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(50);
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
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result ?? {});
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

async function evaluate(session, expression) {
  const result = await session.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
        result.exceptionDetails.exception?.description ||
        result.exceptionDetails.text || 'Runtime exception');
  }
  return result.result?.value;
}

async function killExactProcessTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (child.pid) {
    const cleanup = spawnSync(
        'taskkill', ['/PID', String(child.pid), '/T', '/F'], {
          stdio: 'ignore',
          windowsHide: true,
        });
    if (cleanup.error) child.kill('SIGKILL');
  }
  await Promise.race([
    new Promise(resolve => child.once('exit', resolve)),
    delay(3000),
  ]);
}

async function verifyImmediateButtonVisibility() {
  // Keep the apex host offline and deterministic. The toolbar must react to
  // the pending visible URL itself, before any network redirect to www.
  // Use a fresh profile instead of reusing the just-closed migration profile:
  // Windows can keep a renderer descendant alive for a moment after
  // Browser.close, which would otherwise turn this spawn into a short-lived
  // launcher with no top-level window of its own.
  const profileDir = fs.mkdtempSync(
      path.join(os.tmpdir(), 'focusyoutube-button-qa-'));
  const child = spawn(chromePath, [
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--host-resolver-rules=MAP youtube.com 127.0.0.1',
    '--window-size=1000,700',
    'https://youtube.com/',
  ], {
    stdio: 'ignore',
    // UI Automation must inspect the real top-level browser window. Passing
    // windowsHide here sets SW_HIDE for GUI processes as well and makes a
    // correct toolbar impossible to observe.
    windowsHide: false,
  });

  const automationScript = String.raw`
Add-Type -AssemblyName UIAutomationClient
$browserProcessId = [int]$env:FOCUS_QA_BROWSER_PID
$timeoutMs = 15000
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$windowSeenAt = $null
$maximumWindowCount = 0
$seenButtonNames = [System.Collections.Generic.HashSet[string]]::new()
$processCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
  $browserProcessId)
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Button)
while ($clock.ElapsedMilliseconds -lt $timeoutMs) {
  $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
    [System.Windows.Automation.TreeScope]::Children, $processCondition)
  $maximumWindowCount = [Math]::Max($maximumWindowCount, $windows.Count)
  if ($windows.Count -gt 0 -and $null -eq $windowSeenAt) {
    $windowSeenAt = $clock.ElapsedMilliseconds
  }
  foreach ($window in $windows) {
    $buttons = $window.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
    foreach ($button in $buttons) {
      $name = $button.Current.Name
      if (-not [string]::IsNullOrWhiteSpace($name)) {
        [void]$seenButtonNames.Add($name)
      }
      if ($name -like '*FocusYoutube*' -and
          -not $button.Current.IsOffscreen -and $button.Current.IsEnabled) {
        [pscustomobject]@{
          ok = $true
          processId = $browserProcessId
          accessibleName = $name
          windowSeenMs = [int64]$windowSeenAt
          buttonSeenMs = [int64]$clock.ElapsedMilliseconds
          deltaFromWindowMs = [int64]($clock.ElapsedMilliseconds - $windowSeenAt)
        } | ConvertTo-Json -Compress
        exit 0
      }
    }
  }
  Start-Sleep -Milliseconds 20
}
[pscustomobject]@{
  ok = $false
  processId = $browserProcessId
  windowSeenMs = $windowSeenAt
  maximumWindowCount = $maximumWindowCount
  buttonNames = @($seenButtonNames)
} | ConvertTo-Json -Compress
exit 1
`;

  try {
    const result = spawnSync(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', automationScript], {
          encoding: 'utf8',
          timeout: 20000,
          windowsHide: true,
          env: {
            ...process.env,
            FOCUS_QA_BROWSER_PID: String(child.pid),
          },
        });
    assert.equal(result.status, 0, [
      'FocusYoutube immediate native-button UIA probe failed.',
      result.error?.message || '',
      result.stdout || '',
      result.stderr || '',
    ].filter(Boolean).join('\n'));
    const line = result.stdout.trim().split(/\r?\n/).at(-1);
    const measurement = JSON.parse(line);
    assert.equal(measurement.ok, true);
    assert.match(measurement.accessibleName, /FocusYoutube/);
    assert.ok(measurement.deltaFromWindowMs <= 1500,
        `FocusYoutube button appeared ${measurement.deltaFromWindowMs}ms ` +
        'after the browser window');
    return measurement;
  } finally {
    await killExactProcessTree(child);
    const resolvedProfile = path.resolve(profileDir);
    if (path.dirname(resolvedProfile) === path.resolve(os.tmpdir()) &&
        path.basename(resolvedProfile).startsWith('focusyoutube-button-qa-')) {
      fs.rmSync(resolvedProfile, {recursive: true, force: true});
    }
  }
}

async function startBrowser(profileDir) {
  const portFile = path.join(profileDir, 'DevToolsActivePort');
  fs.rmSync(portFile, {force: true});
  const child = spawn(chromePath, [
    `--user-data-dir=${profileDir}`,
    '--remote-debugging-port=0',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-features=Translate',
    '--window-size=1000,700',
    extensionPage,
  ], {
    stdio: 'ignore',
    windowsHide: true,
  });

  try {
    const port = await waitFor(() => {
      if (child.exitCode !== null || child.signalCode !== null) {
        throw new Error(`browser exited early with code ${child.exitCode}`);
      }
      if (!fs.existsSync(portFile)) return null;
      return fs.readFileSync(portFile, 'utf8').trim().split(/\r?\n/)[0];
    }, 'DevToolsActivePort');
    const version = await waitFor(async () => {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      return response.ok ? await response.json() : null;
    }, 'browser DevTools endpoint');
    const target = await waitFor(async () => {
      const response = await fetch(`http://127.0.0.1:${port}/json`);
      const targets = await response.json();
      return targets.find(candidate =>
        candidate.type === 'page' && candidate.url.startsWith(extensionPage));
    }, 'FocusYoutube extension page');
    const page = new CdpSession(target.webSocketDebuggerUrl);
    const browser = new CdpSession(version.webSocketDebuggerUrl);
    await page.connect();
    await browser.connect();
    await page.send('Runtime.enable');
    await waitFor(
        () => evaluate(page, "document.readyState === 'complete'"),
        'FocusYoutube extension document');
    return {child, page, browser, port};
  } catch (error) {
    await killExactProcessTree(child);
    throw error;
  }
}

async function closeBrowser(instance) {
  if (!instance) return;
  try {
    await Promise.race([
      instance.browser.send('Browser.close'),
      delay(1500),
    ]);
  } catch {
    // The exact process-tree fallback below handles an already closed socket.
  }
  instance.page.close();
  instance.browser.close();
  await Promise.race([
    new Promise(resolve => instance.child.once('exit', resolve)),
    delay(3000),
  ]);
  await killExactProcessTree(instance.child);
}

const storageGetAll = page => evaluate(page, `new Promise((resolve, reject) =>
  chrome.storage.local.get(null, value => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve(value);
  }))`);

const storageReplace = (page, value) => evaluate(page, `new Promise(
    (resolve, reject) => chrome.storage.local.clear(() => {
      const clearError = chrome.runtime.lastError;
      if (clearError) { reject(new Error(clearError.message)); return; }
      chrome.storage.local.set(${JSON.stringify(value)}, () => {
        const setError = chrome.runtime.lastError;
        if (setError) reject(new Error(setError.message)); else resolve(true);
      });
    }))`);

const alarmsGetAll = page => evaluate(page, `new Promise((resolve, reject) =>
  chrome.alarms.getAll(value => {
    const error = chrome.runtime.lastError;
    if (error) reject(new Error(error.message)); else resolve(value);
  }))`);

const profileDir = fs.mkdtempSync(
    path.join(os.tmpdir(), 'focusyoutube-v3-runtime-qa-'));
const nativeSeed = Object.fromEntries(
    nativeBehaviorIds.map((id, index) => [id, index % 2 === 0]));
const seed = {
  ...Object.fromEntries(behaviorIds.map(id => [id, true])),
  ...nativeSeed,
  global_enable: false,
  dark_mode: false,
  schedule: true,
  scheduleTimes: 'invalid-qa-schedule',
  scheduleDays: 'mo,tu,we,th,fr',
  nextTimedChange: Date.now() + 7 * 24 * 60 * 60 * 1000,
  nextTimedValue: false,
  password: true,
  hashed_password: 'qa-legacy-password-hash',
  focus_youtube_schema_version: 2,
  yt_on: true,
  popup_settings: {dark_mode: true},
  hide_feed: true,
  session_token: 'must-be-removed',
  license_token: 'must-be-removed',
  user_email: 'must-be-removed@example.test',
  log_enabled: true,
  log_prompt_answered: true,
};

let firstRun = null;
let secondRun = null;
try {
  firstRun = await startBrowser(profileDir);
  await waitFor(async () => {
    const current = await storageGetAll(firstRun.page);
    return current.focus_youtube_schema_version === 3 &&
        behaviorIds.every(id => typeof current[id] === 'boolean');
  }, 'initial FocusYoutube schema');
  // Let both the component-load and onInstalled/onStartup initialization
  // tasks settle before deliberately replacing storage with the old schema.
  await delay(1000);

  assert.equal(await storageReplace(firstRun.page, seed), true);
  const seeded = await waitFor(async () => {
    const current = await storageGetAll(firstRun.page);
    return current.focus_youtube_schema_version === 2 &&
        current.hashed_password === seed.hashed_password ? current : null;
  }, 'persisted pre-v3 seed');
  assert.equal(seeded.global_enable, false);
  assert.equal(seeded.schedule, true);
  assert.equal(seeded.nextTimedValue, false);
  assert.ok(hiddenBehaviorIds.every(id => seeded[id] === true));
  await delay(500);
  const stableSeed = await storageGetAll(firstRun.page);
  assert.equal(stableSeed.focus_youtube_schema_version, 2,
      'pre-v3 seed was migrated before the intentional browser restart');
  assert.equal(stableSeed.hashed_password, seed.hashed_password);
  await closeBrowser(firstRun);
  firstRun = null;

  secondRun = await startBrowser(profileDir);
  const migrated = await waitFor(async () => {
    const current = await storageGetAll(secondRun.page);
    if (current.focus_youtube_schema_version !== 3) return null;
    if (Object.keys(nativeSeed).some(id => current[id] !== nativeSeed[id])) {
      return null;
    }
    if (hiddenBehaviorIds.some(id => current[id] !== false)) return null;
    if (current.schedule !== false || current.nextTimedChange !== false ||
        current.nextTimedValue !== true || current.password !== false ||
        current.hashed_password !== '') {
      return null;
    }
    return current;
  }, 'schema v3 migration');

  assert.equal(migrated.global_enable, false,
      'canonical global_enable must survive migration');
  assert.equal(migrated.dark_mode, false,
      'canonical dark_mode must win over legacy popup_settings');
  assert.equal(
      behaviorIds.filter(id =>
        Object.prototype.hasOwnProperty.call(migrated, id)).length,
      93, 'all behavior keys must exist after migration');
  assert.ok(nativeBehaviorIds.every(id => migrated[id] === nativeSeed[id]));
  assert.ok(hiddenBehaviorIds.every(id => migrated[id] === false));
  for (const removed of [
    'yt_on', 'popup_settings', 'hide_feed', 'session_token', 'license_token',
    'user_email', 'log_enabled', 'log_prompt_answered',
  ]) {
    assert.equal(Object.prototype.hasOwnProperty.call(migrated, removed), false,
        `legacy/forbidden key was not removed: ${removed}`);
  }

  await waitFor(async () => {
    const alarms = await alarmsGetAll(secondRun.page);
    return alarms.every(alarm =>
      alarm.name !== 'focusyoutube-schedule' &&
      !alarm.name.startsWith('focusyoutube-timed:')) ? alarms : null;
  }, 'cleared FocusYoutube automation alarms', 5000);
  const alarms = await alarmsGetAll(secondRun.page);

  // The native button owns its own availability. Close the storage probe and
  // prove that the apex URL exposes the toolbar control immediately, without
  // waiting for YouTube's redirect, content scripts, migration or a timer.
  await closeBrowser(secondRun);
  secondRun = null;
  const immediateVisibility =
      await verifyImmediateButtonVisibility();

  const report = {
    ok: true,
    executable: chromePath,
    profile: 'unique disposable profile removed after test',
    cleanup: 'Browser.close followed by exact spawned PID tree fallback',
    migration: {
      fromSchema: 2,
      toSchema: migrated.focus_youtube_schema_version,
      globalPreserved: migrated.global_enable,
      nativeBehaviorCount: nativeBehaviorIds.length,
      nativeValuesPreserved: nativeBehaviorIds,
      hiddenBehaviorCount: hiddenBehaviorIds.length,
      hiddenValuesForcedFalse: hiddenBehaviorIds.length,
      scheduleDisabled: migrated.schedule === false,
      timerCleared: migrated.nextTimedChange === false,
      passwordCleared:
          migrated.password === false && migrated.hashed_password === '',
      automationAlarmsRemaining: alarms
          .filter(alarm => alarm.name.startsWith('focusyoutube-'))
          .map(alarm => alarm.name),
    },
    buttonVisibility: {
      runtimeProbe: 'Windows UI Automation on the exact spawned browser PID',
      apexHostVisibleImmediately: true,
      ...immediateVisibility,
      source: toolbarPath,
      scheme: 'https only',
      exactHosts: ['youtube.com', 'www.youtube.com', 'm.youtube.com'],
      rejectsLookalikesByExactEquality: true,
    },
  };
  if (reportPath) {
    fs.mkdirSync(path.dirname(reportPath), {recursive: true});
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
} finally {
  await closeBrowser(secondRun);
  await closeBrowser(firstRun);
  await delay(250);
  fs.rmSync(profileDir, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  });
}
