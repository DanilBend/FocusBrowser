import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {access, mkdir, readFile, writeFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import path from 'node:path';

const EXPECTED_VIDEO_URL =
    'https://www.youtube.com/watch?v=R2K7ZHsnypI';
const VIEWPORT = {width: 2048, height: 1152};
const root = path.resolve(import.meta.dirname, '..');
const defaultChrome = path.join(
    root, 'build', 'src', 'out', 'Default', 'chrome.exe');
const activeMenuPath = path.join(
    root, 'build', 'src', 'chrome', 'browser', 'ui', 'toolbar',
    'app_menu_model.cc');
const overrideMenuPath = path.join(
    root, 'source_overrides', 'chrome', 'browser', 'ui', 'toolbar',
    'app_menu_model.cc');

const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
const chromePath = path.resolve(process.argv[2] || defaultChrome);
const outputDir = path.resolve(
    process.argv[3] || path.join(import.meta.dirname,
                                `runtime-meditation-${timestamp}`));
const uiLocale = process.argv[4] || 'ru';
const useRussianUi = /^ru(?:[-_]|$)/i.test(uiLocale);
const expectedMeditation = useRussianUi ? {
  htmlLang: 'ru',
  title: 'Медитация · Focus Browser',
  sectionText: 'Медитация',
  pageTitleParts: ['Остановитесь.', 'Верните внимание.'],
  buttonText: 'Открыть видео и начать',
} : {
  htmlLang: 'en-US',
  title: 'Meditation · Focus Browser',
  sectionText: 'Meditation',
  pageTitleParts: ['Pause.', 'Bring your attention back.'],
  buttonText: 'Open video and begin',
};
const profileDir = path.join(outputDir, 'profile');
const stdoutPath = path.join(outputDir, 'chrome.stdout.log');
const stderrPath = path.join(outputDir, 'chrome.stderr.log');

const delay = milliseconds =>
    new Promise(resolve => setTimeout(resolve, milliseconds));

async function getFreePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  if (!address || typeof address === 'string') {
    server.close();
    throw new Error('Could not allocate a local DevTools port');
  }
  await new Promise(resolve => server.close(resolve));
  return address.port;
}

async function waitFor(probe, description, options = {}) {
  const timeoutMs = options.timeoutMs ?? 30000;
  const intervalMs = options.intervalMs ?? 200;
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
    await delay(intervalMs);
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
    this.eventHandlers = new Map();
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
          pending.reject(
              new Error(`${message.error.code}: ${message.error.message}`));
        } else {
          pending.resolve(message.result ?? {});
        }
        return;
      }
      if (!message.method) {
        return;
      }
      for (const handler of this.eventHandlers.get(message.method) ?? []) {
        handler(message.params ?? {});
      }
    });
    this.socket.addEventListener('close', () => {
      for (const {reject} of this.pending.values()) {
        reject(new Error('DevTools WebSocket closed'));
      }
      this.pending.clear();
    });
  }

  on(method, handler) {
    if (!this.eventHandlers.has(method)) {
      this.eventHandlers.set(method, []);
    }
    this.eventHandlers.get(method).push(handler);
  }

  send(method, params = {}) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('DevTools WebSocket is not open'));
    }
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
    const detail = response.exceptionDetails.exception?.description ||
        response.exceptionDetails.text || 'Runtime evaluation failed';
    throw new Error(detail);
  }
  return response.result?.value;
}

async function capture(session, outputPath) {
  const result = await session.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
    fromSurface: true,
  });
  await writeFile(outputPath, Buffer.from(result.data, 'base64'));
}

async function clickAt(session, point) {
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    throw new Error('Could not resolve a clickable element center');
  }
  await session.send('Input.dispatchMouseEvent', {
    type: 'mouseMoved',
    x: point.x,
    y: point.y,
  });
  await session.send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: point.x,
    y: point.y,
    button: 'left',
    buttons: 1,
    clickCount: 1,
  });
  await session.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: point.x,
    y: point.y,
    button: 'left',
    buttons: 0,
    clickCount: 1,
  });
}

async function waitForChildExit(child, timeoutMs) {
  if (child.exitCode !== null) {
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

  // The user-data-dir is unique, so this tree contains only this QA launch.
  const killer = spawn(
      'taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
      {stdio: 'ignore', windowsHide: true});
  await waitForChildExit(killer, 5000);
  await waitForChildExit(child, 5000);
}

await access(chromePath);
await mkdir(profileDir, {recursive: true});

const port = await getFreePort();
const stdoutStream = createWriteStream(stdoutPath, {flags: 'wx'});
const stderrStream = createWriteStream(stderrPath, {flags: 'wx'});
const args = [
  `--user-data-dir=${profileDir}`,
  `--remote-debugging-port=${port}`,
  '--remote-allow-origins=*',
  '--headless=new',
  `--window-size=${VIEWPORT.width},${VIEWPORT.height}`,
  '--force-device-scale-factor=1',
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-background-networking',
  '--disable-component-update',
  '--disable-sync',
  '--disable-search-engine-choice-screen',
  `--lang=${uiLocale}`,
  'chrome://new-tab-page/',
];

let browser = null;
let browserSession = null;
let pageSession = null;
let report = null;
let primaryError = null;
let appMenuStatic = null;
const runtimeExceptions = [];
const consoleMessages = [];

try {
  const [activeMenu, overrideMenu] = await Promise.all([
    readFile(activeMenuPath, 'utf8'),
    readFile(overrideMenuPath, 'utf8'),
  ]);
  const menuEntryPattern =
      /IDC_OPEN_MEDITATION,\s*use_russian_ui\s*\?\s*u"Медитация"\s*:\s*u"Meditation"/m;
  const sha256 = value =>
      createHash('sha256').update(value, 'utf8').digest('hex');
  appMenuStatic = {
    activePath: activeMenuPath,
    overridePath: overrideMenuPath,
    activeSha256: sha256(activeMenu),
    overrideSha256: sha256(overrideMenu),
    checks: {
      activeEntryPresent: menuEntryPattern.test(activeMenu),
      overrideEntryPresent: menuEntryPattern.test(overrideMenu),
      activeOverrideParity: activeMenu === overrideMenu,
    },
  };
  if (Object.values(appMenuStatic.checks).some(value => !value)) {
    throw new Error(
        `App menu meditation contract failed: ${JSON.stringify(appMenuStatic)}`);
  }

  browser = spawn(chromePath, args, {
    cwd: path.dirname(chromePath),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  browser.stdout.pipe(stdoutStream);
  browser.stderr.pipe(stderrStream);

  const version = await waitFor(async () => {
    if (browser.exitCode !== null) {
      throw new Error(`Focus Browser exited early with ${browser.exitCode}`);
    }
    const response = await fetch(`http://127.0.0.1:${port}/json/version`);
    return response.ok ? response.json() : null;
  }, 'Focus Browser DevTools endpoint', {timeoutMs: 45000});

  browserSession = new CdpSession(version.webSocketDebuggerUrl);
  await browserSession.connect();

  const targetHistory = [];
  const recordTarget = ({targetInfo}) => {
    if (targetInfo) {
      targetHistory.push({
        targetId: targetInfo.targetId,
        type: targetInfo.type,
        url: targetInfo.url,
      });
    }
  };
  browserSession.on('Target.targetCreated', recordTarget);
  browserSession.on('Target.targetInfoChanged', recordTarget);
  await browserSession.send('Target.setDiscoverTargets', {discover: true});

  const initialTargets = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await response.json();
    return targets.some(target => target.type === 'page') ? targets : null;
  }, 'an initial page target');
  const pageTarget = initialTargets.find(target =>
    target.type === 'page' &&
    (target.url.includes('new-tab-page') || target.url === 'about:blank')) ||
      initialTargets.find(target => target.type === 'page');
  if (!pageTarget?.webSocketDebuggerUrl) {
    throw new Error('No debuggable Focus Browser page target found');
  }
  const initialTargetIds = new Set(initialTargets.map(target => target.id));

  pageSession = new CdpSession(pageTarget.webSocketDebuggerUrl);
  await pageSession.connect();
  pageSession.on('Runtime.exceptionThrown', ({exceptionDetails}) => {
    runtimeExceptions.push(
        exceptionDetails?.exception?.description ||
        exceptionDetails?.text || 'Unknown runtime exception');
  });
  pageSession.on('Runtime.consoleAPICalled', ({type, args: values = []}) => {
    consoleMessages.push({
      type,
      values: values.map(value => value.value ?? value.description ?? ''),
    });
  });
  await pageSession.send('Page.enable');
  await pageSession.send('Runtime.enable');
  await pageSession.send('Emulation.setDeviceMetricsOverride', {
    ...VIEWPORT,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await pageSession.send('Page.navigate', {url: 'chrome://new-tab-page/'});

  const ntp = await waitFor(() => evaluate(pageSession, `(() => {
    const app = document.querySelector('ntp-app');
    const root = app?.shadowRoot;
    const content = root?.querySelector('#content');
    const home = root?.querySelector('#focusHome');
    const searchbox = root?.querySelector('#focusSearch ntp-searchbox');
    const mostVisited = root?.querySelector('#focusShortcuts #mostVisited');
    if (!app || !root || !content || !home || !searchbox?.shadowRoot ||
        !mostVisited ||
        document.documentElement.getAttribute('lazy-loaded') !== 'true') {
      return null;
    }
    const forbiddenSelectors = [
      '#focusBrand', '#focusMark', '#focusMessage', '#focusShortcutsHeading',
      '#focusMeditationLink', 'ntp-logo', 'ntp-customize-buttons',
      '#customizeButtons', '#themeAttribution', '#contentBottomSpacer',
      '#backgroundImageAttribution', 'ntp-middle-slot-promo', 'ntp-modules',
      '#modules', '#oneGoogleBar', 'individual-promos',
    ];
    const contentChildren = [...content.children].map(element => element.id);
    const homeChildren = [...home.children].map(element => element.id);
    return {
      href: location.href,
      title: document.title,
      viewport: {width: innerWidth, height: innerHeight},
      scrolling: {
        documentClientHeight: document.documentElement.clientHeight,
        documentScrollHeight: document.documentElement.scrollHeight,
        contentClientHeight: content.clientHeight,
        contentScrollHeight: content.scrollHeight,
      },
      searchboxPresent: Boolean(searchbox),
      shortcutsPresent: Boolean(mostVisited),
      forbidden: forbiddenSelectors.filter(selector => root.querySelector(selector)),
      productCopyPresent:
          /Focus Browser|Один экран|Полный фокус/.test(root.textContent || ''),
      expectedStructure:
          contentChildren.length === 1 && contentChildren[0] === 'focusHome' &&
          homeChildren.length === 2 && homeChildren[0] === 'focusSearch' &&
          homeChildren[1] === 'focusShortcuts',
      contentChildren,
      homeChildren,
    };
  })()`), 'the clean Focus Browser new tab page');

  const ntpChecks = {
    viewportIs2048x1152:
        ntp.viewport.width === VIEWPORT.width &&
        ntp.viewport.height === VIEWPORT.height,
    searchboxPresent: ntp.searchboxPresent,
    shortcutsPresent: ntp.shortcutsPresent,
    onlySearchAndPinnedShortcuts: ntp.expectedStructure,
    forbiddenElementsAbsent: ntp.forbidden.length === 0,
    productCopyAbsent: !ntp.productCopyPresent,
    noUnwantedScrollbar:
        ntp.scrolling.documentScrollHeight <=
            ntp.scrolling.documentClientHeight + 1 &&
        ntp.scrolling.contentScrollHeight <=
            ntp.scrolling.contentClientHeight + 1,
  };
  if (Object.values(ntpChecks).some(value => !value)) {
    throw new Error(`Clean NTP contract failed: ${JSON.stringify({
      ntpChecks, ntp,
    })}`);
  }

  const ntpScreenshot = path.join(
      outputDir, '01-clean-new-tab-2048x1152.png');
  await capture(pageSession, ntpScreenshot);

  await pageSession.send('Page.navigate', {url: 'chrome://meditation/'});
  const meditation = await waitFor(() => evaluate(pageSession, `(async () => {
    if (location.hostname !== 'meditation' ||
        document.readyState !== 'complete') return null;
    const loadButton = document.querySelector('#load-video');
    const privacyGate = document.querySelector('#privacy-gate');
    if (!loadButton || !privacyGate) return null;
    const rect = loadButton.getBoundingClientRect();
    const style = getComputedStyle(loadButton);
    const bodyText = document.body?.innerText || '';
    return {
      href: location.href,
      host: location.hostname,
      scheme: location.protocol,
      title: document.title,
      htmlLang: document.documentElement.lang,
      navigatorLanguage: navigator.language,
      navigatorLanguages: [...navigator.languages],
      loadTimeApplicationLocale:
          globalThis.loadTimeData?.getString?.('applicationLocale') || '',
      scripts: [...document.scripts].map(script => script.src),
      viewport: {width: innerWidth, height: innerHeight},
      pageTitle: document.querySelector('#page-title')?.innerText || '',
      sectionText: document.querySelector('.section-name')?.innerText || '',
      buttonText: loadButton.innerText.trim(),
      buttonVisible: rect.width > 0 && rect.height > 0 &&
          style.display !== 'none' && style.visibility !== 'hidden',
      privacyGateVisible: !privacyGate.hidden &&
          getComputedStyle(privacyGate).display !== 'none',
      secondaryHref:
          document.querySelector('.secondary-action')?.getAttribute('href') || '',
      iframeCount: document.querySelectorAll('iframe').length,
      hasError153: /(?:Ошибка|Error)\\s*153/i.test(bodyText),
      bodyText: bodyText.replace(/\\s+/g, ' ').trim().slice(0, 1200),
      clickPoint: {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2},
    };
  })()`), 'the Focus Browser meditation page');

  const meditationChecks = {
    pageResolvedToMeditation:
        meditation.host === 'meditation' &&
        ['chrome:', 'focus:'].includes(meditation.scheme),
    viewportIs2048x1152:
        meditation.viewport.width === VIEWPORT.width &&
        meditation.viewport.height === VIEWPORT.height,
    localizedDocument:
        (useRussianUi ? /^ru(?:[-_]|$)/i.test(meditation.htmlLang) :
                        /^en(?:[-_]|$)/i.test(meditation.htmlLang)) &&
        meditation.title === expectedMeditation.title &&
        meditation.sectionText.includes(expectedMeditation.sectionText) &&
        expectedMeditation.pageTitleParts.every(
            part => meditation.pageTitle.includes(part)),
    loadButtonVisible:
        meditation.buttonVisible && meditation.privacyGateVisible &&
        meditation.buttonText === expectedMeditation.buttonText,
    directFallbackExact: meditation.secondaryHref === EXPECTED_VIDEO_URL,
    noEmbeddedPlayer: meditation.iframeCount === 0,
    noError153: !meditation.hasError153,
  };
  if (Object.values(meditationChecks).some(value => !value)) {
    throw new Error(`Meditation page contract failed: ${JSON.stringify({
      meditationChecks, meditation,
    })}`);
  }

  const meditationScreenshot = path.join(
      outputDir, '02-meditation-page-2048x1152.png');
  // Let the entry transition settle so release evidence reflects the final UI.
  await delay(650);
  await capture(pageSession, meditationScreenshot);

  await evaluate(pageSession, `(async () => {
    const button = document.querySelector('#load-video');
    if (!button) throw new Error('Meditation load button disappeared');
    button.scrollIntoView({
      block: 'center',
      inline: 'center',
      behavior: 'instant',
    });
    await new Promise(resolve => requestAnimationFrame(() =>
      requestAnimationFrame(resolve)));
    return {scrollX, scrollY};
  })()`);
  const videoButtonAfterScroll = await waitFor(
      () => evaluate(pageSession, `(() => {
        const button = document.querySelector('#load-video');
        if (!button) return null;
        const rect = button.getBoundingClientRect();
        const style = getComputedStyle(button);
        const inViewport = rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' && style.visibility !== 'hidden' &&
            rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight &&
            rect.right <= innerWidth;
        if (!inViewport) return null;
        return {
          rect: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
          },
          viewport: {width: innerWidth, height: innerHeight},
          clickPoint: {
            x: rect.x + rect.width / 2,
            y: rect.y + rect.height / 2,
          },
        };
      })()`),
      'the meditation load button inside the viewport');

  const historyStart = targetHistory.length;
  await clickAt(pageSession, videoButtonAfterScroll.clickPoint);

  const videoOpen = await waitFor(async () => {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`);
    const targets = await response.json();
    const history = targetHistory.slice(historyStart);
    const exactHistoryEntry = history.find(
        target => target.url === EXPECTED_VIDEO_URL);
    const newPageTargets = targets.filter(target =>
      target.type === 'page' && !initialTargetIds.has(target.id));
    const exactTarget = newPageTargets.find(
        target => target.url === EXPECTED_VIDEO_URL);
    const youtubeTarget = newPageTargets.find(target =>
      target.url.includes('youtube.com') &&
      target.url.includes('R2K7ZHsnypI'));
    if (!exactHistoryEntry && !exactTarget) {
      return null;
    }
    return {
      exactUrlObserved: true,
      exactHistoryEntry: exactHistoryEntry || null,
      currentTarget: exactTarget || youtubeTarget || null,
      newPageTargets: newPageTargets.map(target => ({
        id: target.id,
        title: target.title,
        url: target.url,
      })),
      targetHistory: history,
    };
  }, 'the exact YouTube video URL in a new tab', {timeoutMs: 20000});

  const afterOpen = await waitFor(() => evaluate(pageSession, `(() => {
    const bodyText = document.body?.innerText || '';
    return {
      href: location.href,
      status: document.querySelector('#connection-status')?.innerText.trim() || '',
      iframeCount: document.querySelectorAll('iframe').length,
      hasError153: /(?:Ошибка|Error)\\s*153/i.test(bodyText),
    };
  })()`), 'the meditation post-click state');

  const videoChecks = {
    exactYouTubeUrlObserved: videoOpen.exactUrlObserved,
    statusUpdated:
        afterOpen.status === 'Видео открыто в новой вкладке YouTube.',
    remainsOnMeditationPage:
        new URL(afterOpen.href).hostname === 'meditation',
    stillNoEmbeddedPlayer: afterOpen.iframeCount === 0,
    stillNoError153: !afterOpen.hasError153,
  };
  if (Object.values(videoChecks).some(value => !value)) {
    throw new Error(`Meditation video action failed: ${JSON.stringify({
      videoChecks, videoOpen, afterOpen,
    })}`);
  }

  await evaluate(pageSession, `(() => {
    document.querySelector('#connection-status')?.scrollIntoView({
      block: 'center',
      behavior: 'instant',
    });
    return {scrollX, scrollY};
  })()`);
  await delay(250);
  const afterOpenScreenshot = path.join(
      outputDir, '03-meditation-video-opened-2048x1152.png');
  await capture(pageSession, afterOpenScreenshot);

  report = {
    passed: true,
    chromePath,
    browserPid: browser.pid,
    devToolsPort: port,
    profileDir,
    viewport: VIEWPORT,
    uiLocale,
    expectedVideoUrl: EXPECTED_VIDEO_URL,
    checks: {
      appMenu: appMenuStatic.checks,
      ntp: ntpChecks,
      meditation: meditationChecks,
      video: videoChecks,
    },
    evidence: {
      appMenu: appMenuStatic,
      ntp,
      meditation,
      videoButtonAfterScroll,
      afterOpen,
      videoOpen,
      screenshots: [ntpScreenshot, meditationScreenshot, afterOpenScreenshot],
      stdoutPath,
      stderrPath,
    },
  };
  await writeFile(
      path.join(outputDir, 'report.json'),
      `${JSON.stringify(report, null, 2)}\n`, 'utf8');
} catch (error) {
  primaryError = error;
  report = {
    passed: false,
    chromePath,
    browserPid: browser?.pid ?? null,
    devToolsPort: port,
    profileDir,
    checks: {appMenu: appMenuStatic?.checks ?? null},
    evidence: {appMenu: appMenuStatic, runtimeExceptions, consoleMessages},
    error: error.stack || String(error),
    stdoutPath,
    stderrPath,
  };
  await writeFile(
      path.join(outputDir, 'report.json'),
      `${JSON.stringify(report, null, 2)}\n`, 'utf8').catch(() => null);
} finally {
  pageSession?.close();
  await stopOwnedBrowser(browser, browserSession);
  browserSession?.close();
  stdoutStream.end();
  stderrStream.end();
}

if (primaryError) {
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify(report, null, 2));
}
