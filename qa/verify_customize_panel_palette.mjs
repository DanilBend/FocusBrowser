#!/usr/bin/env node

// Computed-color contract for the Focus customization panel. Attach to a
// browser that was started with --remote-debugging-port=<port> and has the NTP
// customization panel open. This script never launches or closes a browser.

import assert from 'node:assert/strict';

const [port = '9341'] = process.argv.slice(2);
const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(
    response => response.json());
const target = targets.find(candidate =>
  candidate.type === 'browser_ui' &&
  candidate.url?.includes('customize-chrome-side-panel')) ??
    targets.find(candidate =>
      candidate.url?.includes('customize-chrome-side-panel'));
assert.ok(
    target?.webSocketDebuggerUrl,
    'Open the NTP customization panel before running this check');

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, {once: true});
  socket.addEventListener('error', reject, {once: true});
});

let nextId = 1;
const pending = new Map();
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  const request = pending.get(message.id);
  if (!request) {
    return;
  }
  pending.delete(message.id);
  if (message.error) {
    request.reject(new Error(message.error.message));
  } else {
    request.resolve(message.result ?? {});
  }
});

const send = (method, params = {}) => {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
};

try {
  await send('Runtime.enable');
  const {result, exceptionDetails} = await send('Runtime.evaluate', {
    expression: `(() => {
      const app = document.querySelector('customize-chrome-app');
      if (!app?.shadowRoot) {
        return null;
      }

      const roots = [document];
      for (let index = 0; index < roots.length; ++index) {
        for (const element of roots[index].querySelectorAll('*')) {
          if (element.shadowRoot) {
            roots.push(element.shadowRoot);
          }
        }
      }

      const selectors = [
        'button', 'cr-button', 'cr-checkbox', 'cr-radio-button', 'cr-toggle',
        '[role="button"]', '[role="checkbox"]', '[role="radio"]',
        '[role="switch"]', '.option', 'customize-chrome-check-mark-wrapper',
        '#cornerNewTabPageTile', '#foreground', '#background',
      ].join(',');
      const excludedSelectors = [
        '.collection', 'cr-theme-color-picker',
        'customize-chrome-wallpaper-search-tile',
      ].join(',');
      const properties = [
        'backgroundColor', 'borderTopColor', 'borderRightColor',
        'borderBottomColor', 'borderLeftColor', 'color', 'fill',
        'outlineColor', 'stroke',
      ];

      const crossesExcludedAncestor = element => {
        let current = element;
        while (current) {
          if (current.matches?.(excludedSelectors)) {
            return true;
          }
          if (current.parentElement) {
            current = current.parentElement;
            continue;
          }
          const root = current.getRootNode?.();
          current = root instanceof ShadowRoot ? root.host : null;
        }
        return false;
      };
      const parseColor = value => {
        const match = value.match(
            /^rgba?\\(\\s*([\\d.]+)[,\\s]+([\\d.]+)[,\\s]+([\\d.]+)(?:[,\\s/]+([\\d.]+))?/i);
        if (!match) {
          return null;
        }
        return {
          channels: match.slice(1, 4).map(Number),
          alpha: match[4] === undefined ? 1 : Number(match[4]),
        };
      };
      const describe = element => {
        const id = element.id ? '#' + element.id : '';
        const classes = [...element.classList].slice(0, 2)
            .map(name => '.' + name).join('');
        return element.localName + id + classes;
      };

      const candidates = [...new Set(roots.flatMap(root =>
        [...root.querySelectorAll(selectors)]))]
          .filter(element => !crossesExcludedAncestor(element));
      const violations = [];
      for (const element of candidates) {
        const style = getComputedStyle(element);
        for (const property of properties) {
          const value = style[property];
          const parsed = parseColor(value);
          if (!parsed || parsed.alpha <= 0.05) {
            continue;
          }
          const chroma = Math.max(...parsed.channels) -
              Math.min(...parsed.channels);
          if (chroma > 12) {
            violations.push({
              element: describe(element), property, value, chroma,
            });
          }
        }
      }

      const find = selector => roots.flatMap(root =>
        [...root.querySelectorAll(selector)])[0];
      const forbiddenThemeControls = [
        'customize-color-scheme-mode',
        'cr-theme-color-picker',
        '#followThemeToggle',
      ].filter(selector => Boolean(find(selector)));
      const wallpaperButton = find('#editThemeButton');
      const appearance = find('customize-chrome-appearance');
      const editButtonsContainer = find('#editButtonsContainer');
      return {
        candidateCount: candidates.length,
        forbiddenThemeControls,
        wallpaperButtonPresent: Boolean(wallpaperButton),
        wallpaperButtonHidden: wallpaperButton?.hidden ?? null,
        wallpaperButtonDisplay:
            wallpaperButton ? getComputedStyle(wallpaperButton).display : null,
        wallpaperButtonVisible: Boolean(
            wallpaperButton && !wallpaperButton.hidden &&
            getComputedStyle(wallpaperButton).display !== 'none'),
        wallpaperButtonText: wallpaperButton?.innerText?.trim() ?? '',
        editButtonsContainerHidden: editButtonsContainer?.hidden ?? null,
        appearanceHidden: appearance?.hidden ?? null,
        showEditTheme: appearance?.showEditTheme_ ?? null,
        violations,
      };
    })()`,
    returnByValue: true,
  });
  if (exceptionDetails) {
    throw new Error(exceptionDetails.text || 'Runtime evaluation failed');
  }
  const report = result?.value;
  assert.ok(report, 'Customization panel did not finish loading');
  assert.ok(report.candidateCount > 0, 'No panel controls were inspected');
  assert.deepEqual(
      report.forbiddenThemeControls, [],
      'Light/system or accent theme controls are still exposed');
  assert.ok(
      report.wallpaperButtonPresent,
      `Wallpaper button is missing: ${JSON.stringify(report)}`);
  assert.ok(
      report.wallpaperButtonVisible,
      `Wallpaper button is hidden: ${JSON.stringify(report)}`);
  assert.ok(
      report.wallpaperButtonText,
      `Wallpaper button has no label: ${JSON.stringify(report)}`);
  assert.deepEqual(report.violations, [], JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally {
  socket.close();
}
