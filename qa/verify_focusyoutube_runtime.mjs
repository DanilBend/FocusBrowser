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

function verifyNativeBubbleReadinessContract() {
  const sourceCandidates = [
    path.join(projectRoot, 'source_overrides', 'chrome', 'browser', 'ui',
        'views', 'toolbar', 'focus_youtube_bubble_view.cc'),
    path.join(projectRoot, 'build', 'src', 'chrome', 'browser', 'ui', 'views',
        'toolbar', 'focus_youtube_bubble_view.cc'),
  ];
  const sourcePath = sourceCandidates.find(candidate => fs.existsSync(candidate));
  assert.ok(sourcePath, 'focus_youtube_bubble_view.cc was not found');
  const source = fs.readFileSync(sourcePath, 'utf8');
  const attempts = Number(source.match(
      /kSettingsLoadMaxAttempts\s*=\s*(\d+)/)?.[1]);
  const retryDelayMs = Number(source.match(
      /kSettingsLoadRetryDelay\s*=\s*base::Milliseconds\((\d+)\)/)?.[1]);
  assert.ok(attempts >= 10 && attempts <= 100,
      'native bubble must use a bounded component-readiness retry budget');
  assert.ok(retryDelayMs >= 20 && retryDelayMs <= 250,
      'native bubble retry delay must remain responsive and non-busy');
  assert.match(source,
      /extension_\s*=\s*GetFocusYoutubeExtension\(browser_\)/,
      'each settings attempt must reacquire the component extension');
  assert.match(source,
      /void FocusYoutubeBubbleView::ScheduleSettingsLoadRetry\(\)/);
  assert.match(source, /PostDelayedTask\([\s\S]*?weak_factory_\.GetWeakPtr\(\)/,
      'closing the bubble must cancel pending readiness retries');
  assert.match(source,
      /if \(!storage \|\| !extension_\) \{\s*ScheduleSettingsLoadRetry\(\)/,
      'missing component/storage must stay in loading and retry');
  assert.match(source,
      /if \(!success\) \{\s*ScheduleSettingsLoadRetry\(\)/,
      'transient storage reads must retry before becoming unavailable');
  assert.doesNotMatch(source,
      /SetCloseOnMainFrameOriginNavigation\(true\)/,
      'the pending NTP -> YouTube commit must not close a just-opened bubble');
  assert.match(source,
      /DidFinishNavigation\([\s\S]*?IsFocusYoutubeUrl\(navigation_handle->GetURL\(\)\)/,
      'the bubble must remain open on supported YouTube commits');
  assert.match(source,
      /TabbedPane::Orientation::kHorizontal,\s*views::TabbedPane::TabStripStyle::kBorder/,
      'horizontal FocusYoutube tabs must use the supported border style');
  assert.doesNotMatch(source,
      /TabbedPane::Orientation::kHorizontal,\s*views::TabbedPane::TabStripStyle::kHighlight/,
      'Chromium rejects horizontal highlight tabs at runtime');
  return {sourcePath, attempts, retryDelayMs};
}

const toolbarPath = verifyExactHostPredicate();
const bubbleReadinessContract = verifyNativeBubbleReadinessContract();
if (staticOnly) {
  console.log(JSON.stringify({
    ok: true,
    mode: 'static-only',
    schemaVersion: schema.defaults.focus_youtube_schema_version,
    behaviorCount: behaviorIds.length,
    nativeBehaviorCount: nativeBehaviorIds.length,
    hiddenBehaviorCount: hiddenBehaviorIds.length,
    toolbarSource: toolbarPath,
    bubbleReadinessContract,
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

async function verifyImmediateNativeBubbleReadiness() {
  // Keep the apex host offline and deterministic. Warm the native browser
  // window on about:blank first, then dispatch the YouTube navigation through
  // a second invocation using the same profile. Invoking a Views control while
  // the cold-start error page is still replacing the toolbar can make Windows
  // UI Automation block on a stale element and report a successful Invoke
  // without delivering the callback. A warmed window still verifies the
  // important product contract: the button must react to the pending visible
  // URL itself, before any network redirect to www.
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
    'about:blank',
  ], {
    stdio: 'ignore',
    // UI Automation must inspect the real top-level browser window. Passing
    // windowsHide here sets SW_HIDE for GUI processes as well and makes a
    // correct toolbar impossible to observe.
    windowsHide: false,
  });

  const automationScript = String.raw`
Add-Type -AssemblyName UIAutomationClient
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$browserProcessId = [int]$env:FOCUS_QA_BROWSER_PID
$timeoutMs = 15000
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$windowSeenAt = $null
$windowReadyAt = $null
$navigationDispatchedAt = $null
$navigationLauncherProcessId = $null
$navigationLauncherError = $null
$buttonSeenAt = $null
$buttonInvokedAt = $null
$buttonName = $null
$maximumWindowCount = 0
$bubbleElementSeen = $false
$masterToggleEnabled = $false
$seenButtonNames = [System.Collections.Generic.HashSet[string]]::new()
$enabledFeatureNames = [System.Collections.Generic.HashSet[string]]::new()
$focusTabNamesSeen = [System.Collections.Generic.HashSet[string]]::new()
$focusTabNames = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase)
foreach ($name in @(
    'Feed', 'Player', 'Interface', 'Search',
    'Лента', 'Плеер', 'Интерфейс', 'Поиск')) {
  [void]$focusTabNames.Add($name)
}
$processCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
  $browserProcessId)
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Button)
$editCondition = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
  [System.Windows.Automation.ControlType]::Edit)

function Get-FocusWindows {
  return [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
    [System.Windows.Automation.TreeScope]::Children, $processCondition)
}

function Get-FocusElements {
  $elements = [System.Collections.Generic.List[
      System.Windows.Automation.AutomationElement]]::new()
  $windows = Get-FocusWindows
  foreach ($window in $windows) {
    [void]$elements.Add($window)
    $descendants = $window.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $descendants) {
      [void]$elements.Add($element)
    }
  }
  return $elements
}

function Collect-FocusYoutubeBubbleState {
  $bubbleRoots = [System.Collections.Generic.List[
      System.Windows.Automation.AutomationElement]]::new()
  foreach ($element in (Get-FocusElements)) {
    try {
      if ($element.Current.Name -eq 'FocusYoutube' -and
          $element.Current.ControlType -ne
              [System.Windows.Automation.ControlType]::Button) {
        $script:bubbleElementSeen = $true
        [void]$bubbleRoots.Add($element)
      }
    } catch {}
  }

  $focusTabs = [System.Collections.Generic.List[
      System.Windows.Automation.AutomationElement]]::new()
  foreach ($bubbleRoot in $bubbleRoots) {
    $elements = $bubbleRoot.FindAll(
      [System.Windows.Automation.TreeScope]::Descendants,
      [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($element in $elements) {
      try {
        $name = $element.Current.Name
        if ($focusTabNames.Contains($name)) {
          [void]$focusTabs.Add($element)
          [void]$focusTabNamesSeen.Add($name)
        }

        $togglePattern = $null
        if (-not $element.TryGetCurrentPattern(
            [System.Windows.Automation.TogglePattern]::Pattern,
            [ref]$togglePattern) -or -not $element.Current.IsEnabled) {
          continue
        }
        if ($name -like '*FocusYoutube*') {
          $script:masterToggleEnabled = $true
        } elseif (-not [string]::IsNullOrWhiteSpace($name)) {
          # Count only controls inside the native FocusYoutube dialog. The
          # browser frame itself can expose unrelated enabled TogglePatterns
          # (for example toolbar customisation state) and must not inflate the
          # expected twenty feature controls.
          [void]$enabledFeatureNames.Add($name)
        }
      } catch {
        # A Views subtree can be replaced while a tab is selected. The next
        # 20 ms probe reads the fresh automation elements.
      }
    }
  }
  return $focusTabs
}

# Wait for a stable, interactive omnibox before dispatching the navigation.
# This separates toolbar readiness from cold-process startup without weakening
# the bounded pending-URL assertion below.
while ($clock.ElapsedMilliseconds -lt $timeoutMs -and
       $null -eq $windowReadyAt) {
  $windows = Get-FocusWindows
  $maximumWindowCount = [Math]::Max($maximumWindowCount, $windows.Count)
  if ($windows.Count -gt 0 -and $null -eq $windowSeenAt) {
    $windowSeenAt = $clock.ElapsedMilliseconds
  }
  foreach ($window in $windows) {
    try {
      $edits = $window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $editCondition)
      foreach ($edit in $edits) {
        if ($edit.Current.IsEnabled -and -not $edit.Current.IsOffscreen) {
          $windowReadyAt = $clock.ElapsedMilliseconds
          break
        }
      }
    } catch {}
    if ($null -ne $windowReadyAt) { break }
  }
  if ($null -eq $windowReadyAt) { Start-Sleep -Milliseconds 20 }
}

if ($null -eq $windowReadyAt) {
  [pscustomobject]@{
    ok = $false
    phase = 'browser-warmup'
    processId = $browserProcessId
    windowSeenMs = $windowSeenAt
    maximumWindowCount = $maximumWindowCount
  } | ConvertTo-Json -Compress
  exit 1
}

# Give Views one event-loop turn after UIA first observes the omnibox, then ask
# the already-running profile to navigate. The short-lived launcher owns no
# browser window; all subsequent UIA reads remain scoped to browserProcessId.
Start-Sleep -Milliseconds 100
$navigationDispatchedAt = $clock.ElapsedMilliseconds
try {
  $launcher = Start-Process -FilePath $env:FOCUS_QA_CHROME_PATH -ArgumentList @(
    "--user-data-dir=$($env:FOCUS_QA_PROFILE_DIR)",
    '--new-tab',
    'https://youtube.com/'
  ) -PassThru
  $navigationLauncherProcessId = $launcher.Id
} catch {
  $navigationLauncherError = $_.Exception.Message
}

if ($null -ne $navigationLauncherError) {
  [pscustomobject]@{
    ok = $false
    phase = 'navigation-dispatch'
    processId = $browserProcessId
    windowSeenMs = $windowSeenAt
    windowReadyMs = $windowReadyAt
    navigationDispatchedMs = $navigationDispatchedAt
    navigationLauncherError = $navigationLauncherError
  } | ConvertTo-Json -Compress
  exit 1
}

while ($clock.ElapsedMilliseconds -lt $timeoutMs) {
  $windows = Get-FocusWindows
  $maximumWindowCount = [Math]::Max($maximumWindowCount, $windows.Count)
  if ($windows.Count -gt 0 -and $null -eq $windowSeenAt) {
    $windowSeenAt = $clock.ElapsedMilliseconds
  }

  if ($null -eq $buttonInvokedAt) {
    foreach ($window in $windows) {
      $buttons = $window.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)
      foreach ($button in $buttons) {
        try {
          $name = $button.Current.Name
          if (-not [string]::IsNullOrWhiteSpace($name)) {
            [void]$seenButtonNames.Add($name)
          }
          if ($name -notlike '*FocusYoutube*' -or
              $button.Current.IsOffscreen -or -not $button.Current.IsEnabled) {
            continue
          }
          $invokePattern = $null
          if (-not $button.TryGetCurrentPattern(
              [System.Windows.Automation.InvokePattern]::Pattern,
              [ref]$invokePattern)) {
            throw 'FocusYoutube toolbar button has no InvokePattern'
          }
          $buttonSeenAt = $clock.ElapsedMilliseconds
          $buttonName = $name
          $invokePattern.Invoke()
          $buttonInvokedAt = $clock.ElapsedMilliseconds
          break
        } catch {
          # Retry a transiently stale toolbar element until the bounded probe
          # deadline; permanent failures are included in the final evidence.
        }
      }
      if ($null -ne $buttonInvokedAt) { break }
    }
  }

  if ($null -ne $buttonInvokedAt) {
    $focusTabs = Collect-FocusYoutubeBubbleState
    foreach ($tab in $focusTabs) {
      try {
        $selectionPattern = $null
        if ($tab.TryGetCurrentPattern(
            [System.Windows.Automation.SelectionItemPattern]::Pattern,
            [ref]$selectionPattern)) {
          $selectionPattern.Select()
        } else {
          $invokePattern = $null
          if ($tab.TryGetCurrentPattern(
              [System.Windows.Automation.InvokePattern]::Pattern,
              [ref]$invokePattern)) {
            $invokePattern.Invoke()
          }
        }
        Start-Sleep -Milliseconds 15
        Collect-FocusYoutubeBubbleState | Out-Null
      } catch {}
    }

    if ($focusTabNamesSeen.Count -eq 4 -and $masterToggleEnabled -and
        $enabledFeatureNames.Count -eq 20) {
      $readyAt = $clock.ElapsedMilliseconds
      [pscustomobject]@{
        ok = $true
        processId = $browserProcessId
        accessibleName = $buttonName
        windowSeenMs = [int64]$windowSeenAt
        windowReadyMs = [int64]$windowReadyAt
        navigationDispatchedMs = [int64]$navigationDispatchedAt
        navigationLauncherProcessId = $navigationLauncherProcessId
        buttonSeenMs = [int64]$buttonSeenAt
        buttonInvokedMs = [int64]$buttonInvokedAt
        bubbleReadyMs = [int64]$readyAt
        deltaFromWindowMs = [int64]($buttonSeenAt - $windowSeenAt)
        deltaFromNavigationMs = [int64](
          $buttonSeenAt - $navigationDispatchedAt)
        deltaFromInvokeMs = [int64]($readyAt - $buttonInvokedAt)
        bubbleElementSeen = $bubbleElementSeen
        tabCount = $focusTabNamesSeen.Count
        masterToggleEnabled = $masterToggleEnabled
        enabledFeatureToggleCount = $enabledFeatureNames.Count
      } | ConvertTo-Json -Compress
      exit 0
    }
  }
  Start-Sleep -Milliseconds 20
}
[pscustomobject]@{
  ok = $false
  processId = $browserProcessId
  windowSeenMs = $windowSeenAt
  windowReadyMs = $windowReadyAt
  navigationDispatchedMs = $navigationDispatchedAt
  navigationLauncherProcessId = $navigationLauncherProcessId
  navigationLauncherError = $navigationLauncherError
  buttonSeenMs = $buttonSeenAt
  buttonInvokedMs = $buttonInvokedAt
  maximumWindowCount = $maximumWindowCount
  bubbleElementSeen = $bubbleElementSeen
  tabCount = $focusTabNamesSeen.Count
  masterToggleEnabled = $masterToggleEnabled
  enabledFeatureToggleCount = $enabledFeatureNames.Count
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
            FOCUS_QA_CHROME_PATH: chromePath,
            FOCUS_QA_PROFILE_DIR: profileDir,
          },
        });
    assert.equal(result.status, 0, [
      'FocusYoutube immediate native-bubble UIA probe failed.',
      result.error?.message || '',
      result.stdout || '',
      result.stderr || '',
    ].filter(Boolean).join('\n'));
    const line = result.stdout.trim().split(/\r?\n/).at(-1);
    const measurement = JSON.parse(line);
    assert.equal(measurement.ok, true);
    assert.match(measurement.accessibleName, /FocusYoutube/);
    assert.ok(measurement.deltaFromNavigationMs <= 1500,
        `FocusYoutube button appeared ${measurement.deltaFromNavigationMs}ms ` +
        'after the pending YouTube navigation');
    assert.equal(measurement.tabCount, 4,
        'FocusYoutube bubble did not expose all four native sections');
    assert.equal(measurement.masterToggleEnabled, true,
        'FocusYoutube master toggle remained disabled');
    assert.equal(measurement.enabledFeatureToggleCount, nativeBehaviorIds.length,
        'FocusYoutube bubble did not enable all 20 native feature toggles');
    assert.ok(measurement.deltaFromInvokeMs <= 2500,
        `FocusYoutube bubble needed ${measurement.deltaFromInvokeMs}ms ` +
        'after immediate toolbar invocation');
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
  const immediateBubbleReadiness =
      await verifyImmediateNativeBubbleReadiness();

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
    nativeBubbleReadiness: {
      runtimeProbe: 'Windows UI Automation on the exact spawned browser PID',
      apexHostButtonVisibleImmediately: true,
      toolbarButtonInvokedAsSoonAsVisible: true,
      allNativeFeatureTogglesEnabledPromptly: true,
      ...immediateBubbleReadiness,
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
