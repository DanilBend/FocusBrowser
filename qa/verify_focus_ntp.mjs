#!/usr/bin/env node

// Runtime contract for the minimal Focus Browser new-tab page. Attach to a
// freshly built browser started with --remote-debugging-port=<port> and a
// disposable profile. This script never launches or terminates a browser.

import assert from 'node:assert/strict';

const [port = '9341'] = process.argv.slice(2);

const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(
    response => response.json());
const target = targets.find(candidate => candidate.type === 'page');
assert.ok(target?.webSocketDebuggerUrl, 'No debuggable page target found');

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
    request.reject(new Error(
        `${message.error.code}: ${message.error.message}`));
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

const evaluate = async expression => {
  const result = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
        result.exceptionDetails.text || 'Runtime evaluation failed');
  }
  return result.result?.value;
};

const pause = milliseconds =>
  new Promise(resolve => setTimeout(resolve, milliseconds));

const waitFor = async (probe, description, attempts = 120) => {
  let lastError = null;
  for (let attempt = 0; attempt < attempts; ++attempt) {
    try {
      const value = await probe();
      if (value) {
        return value;
      }
    } catch (error) {
      lastError = error;
    }
    await pause(250);
  }
  throw new Error(
      `Timed out waiting for ${description}` +
      (lastError ? `: ${lastError.message}` : ''));
};

const ntpParts = `
  const app = document.querySelector('ntp-app');
  const appRoot = app?.shadowRoot;
  const searchbox = appRoot?.querySelector('#focusSearch ntp-searchbox');
  const searchboxRoot = searchbox?.shadowRoot;
  const inputHost = searchboxRoot?.querySelector('#input');
  const inputRoot = inputHost?.shadowRoot;
  const realInput = inputRoot?.querySelector('#input');
`;

await send('Page.enable');
await send('Runtime.enable');

try {
  await send('Page.navigate', {url: 'chrome://new-tab-page/'});

  const initial = await waitFor(() => evaluate(`(() => {
    ${ntpParts}
    const content = appRoot?.querySelector('#content');
    const home = appRoot?.querySelector('#focusHome');
    const search = appRoot?.querySelector('#focusSearch');
    const searchContainer = search?.querySelector('#searchboxContainer');
    const shortcuts = appRoot?.querySelector('#focusShortcuts');
    const mostVisited = shortcuts?.querySelector('#mostVisited');
    if (!app || !appRoot || !content || !home || !search ||
        !searchContainer || !searchboxRoot || !inputHost || !inputRoot ||
        !realInput || !shortcuts || !mostVisited ||
        document.documentElement.getAttribute('lazy-loaded') !== 'true') {
      return null;
    }

    const isVisible = element => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' &&
          style.visibility !== 'hidden' && Number(style.opacity) !== 0;
    };
    const deepQuery = (start, selector) => {
      const roots = [start];
      while (roots.length) {
        const current = roots.pop();
        const match = current.querySelector(selector);
        if (match) {
          return match;
        }
        for (const element of current.querySelectorAll('*')) {
          if (element.shadowRoot) {
            roots.push(element.shadowRoot);
          }
        }
      }
      return null;
    };
    const forbiddenSelectors = [
      '#focusBrand', '#focusMark', '#focusMessage',
      '#focusShortcutsHeading', '#focusMeditationLink', 'ntp-logo',
      'ntp-customize-buttons', '#customizeButtons', '#themeAttribution',
      '#contentBottomSpacer', '#backgroundImageAttribution',
      'ntp-middle-slot-promo', 'ntp-modules', '#modules', '#oneGoogleBar',
      'individual-promos', '#focusTypingMirror',
      '[data-focus-text-motion]',
    ];
    const contentChildren = [...content.children].map(element => element.id);
    const homeChildren = [...home.children].map(element => element.id);
    const searchChildren = [...search.children].map(element => element.id);
    const searchContainerChildren =
        [...searchContainer.children].map(element => element.id);
    const shortcutChildren =
        [...shortcuts.children].map(element => element.id);
    const editableElements = inputRoot.querySelectorAll(
        'input, textarea, [contenteditable="true"]');
    const searchRect = searchbox.getBoundingClientRect();
    const shortcutsRect = mostVisited.getBoundingClientRect();
    const fill = getComputedStyle(realInput)
        .getPropertyValue('-webkit-text-fill-color');

    return {
      href: location.href,
      searchboxPresent: Boolean(searchbox),
      shortcutsPresent: Boolean(mostVisited),
      searchboxVisible: isVisible(searchbox),
      shortcutsVisible: isVisible(mostVisited),
      searchBeforeShortcuts: searchRect.bottom <= shortcutsRect.top + 0.5,
      forbidden: forbiddenSelectors.filter(selector =>
        deepQuery(appRoot, selector)),
      customizePencilPresent: Boolean(deepQuery(appRoot, '#customizeButton')),
      productCopyPresent:
          /Focus Browser|Один экран|Полный фокус/.test(
              appRoot.textContent || ''),
      expectedStructure:
          contentChildren.length === 1 &&
          contentChildren[0] === 'focusHome' &&
          homeChildren.length === 2 &&
          homeChildren[0] === 'focusSearch' &&
          homeChildren[1] === 'focusShortcuts' &&
          searchChildren.length === 1 &&
          searchChildren[0] === 'searchboxContainer' &&
          searchContainerChildren.length === 1 &&
          searchContainerChildren[0] === 'searchbox' &&
          shortcutChildren.length === 1 &&
          shortcutChildren[0] === 'mostVisited',
      soleEditableRealInput:
          editableElements.length === 1 && editableElements[0] === realInput,
      realInputTextVisible:
          fill !== 'transparent' &&
          !/^rgba\\([^)]*,\\s*0(?:\\.0+)?\\)$/.test(fill),
    };
  })()`), 'minimal NTP search and shortcuts');

  const shortcutsDisabled = await evaluate(`(async () => {
    const app = document.querySelector('ntp-app');
    const originalValue = app.shortcutsEnabled_;
    app.shortcutsEnabled_ = false;
    await app.updateComplete;
    const root = app.shadowRoot;
    const content = root.querySelector('#content');
    const home = root.querySelector('#focusHome');
    const state = {
      searchboxPresent: Boolean(root.querySelector(
          '#focusSearch ntp-searchbox')),
      shortcutsPresent: Boolean(root.querySelector('#mostVisited')),
      contentChildren: [...content.children].map(element => element.id),
      homeChildren: [...home.children].map(element => element.id),
    };
    app.shortcutsEnabled_ = originalValue;
    await app.updateComplete;
    state.shortcutsRestored = Boolean(root.querySelector('#mostVisited'));
    return state;
  })()`);

  const prepared = await evaluate(`(() => {
    ${ntpParts}
    if (!realInput) {
      return null;
    }
    const valueSetter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype, 'value')?.set;
    valueSetter?.call(realInput, '');
    realInput.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      composed: true,
      inputType: 'deleteContentBackward',
      data: null,
    }));
    realInput.focus();
    const rect = realInput.getBoundingClientRect();
    return {
      focused: inputRoot.activeElement === realInput,
      value: realInput.value,
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
    };
  })()`);
  assert.ok(prepared?.focused && prepared.value === '',
            'Could not focus the empty NTP search input');

  const typedValue = 'x' + 'e\u0301' + 'z';
  for (const grapheme of ['x', 'e\u0301', 'z']) {
    await send('Input.insertText', {text: grapheme});
    await pause(35);
  }
  const afterTyping = await evaluate(`(() => {
    ${ntpParts}
    const rect = realInput?.getBoundingClientRect();
    return realInput && rect ? {
      value: realInput.value,
      focused: inputRoot.activeElement === realInput,
      caretAtEnd: realInput.selectionStart === realInput.value.length &&
          realInput.selectionEnd === realInput.value.length,
      rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
      // Opening the native suggestion dropdown may legitimately widen the
      // editable area. The text origin/baseline and control height must not
      // move when glyphs are inserted.
      textOriginStable:
          Math.abs(rect.x - ${prepared.rect.x}) <= 0.5 &&
          Math.abs(rect.y - ${prepared.rect.y}) <= 0.5 &&
          Math.abs(rect.height - ${prepared.rect.height}) <= 0.5,
      mirrorAbsent: !inputRoot.querySelector('#focusTypingMirror'),
      overlayAbsent: !document.querySelector('[data-focus-text-motion]'),
    } : null;
  })()`);

  const selected = await evaluate(`(() => {
    ${ntpParts}
    realInput?.focus();
    realInput?.select();
    return realInput?.selectionStart === 0 &&
        realInput?.selectionEnd === realInput?.value.length;
  })()`);
  assert.equal(selected, true, 'Could not select the NTP QA value');
  await send('Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Backspace', code: 'Backspace',
    windowsVirtualKeyCode: 8, nativeVirtualKeyCode: 8,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Backspace', code: 'Backspace',
    windowsVirtualKeyCode: 8, nativeVirtualKeyCode: 8,
  });
  await waitFor(() => evaluate(`(() => {
    ${ntpParts}
    return realInput?.value === '' ? true : null;
  })()`), 'cleared NTP search value');

  const query = 'xqz9c81-focus-browser-ntp-contract';
  await send('Input.insertText', {text: query});
  await pause(250);
  const completeValue = await evaluate(`(() => {
    ${ntpParts}
    return realInput?.value || '';
  })()`);
  await send('Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Enter', code: 'Enter',
    windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Enter', code: 'Enter',
    windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
  });
  const destination = await waitFor(async () => {
    const href = await evaluate('location.href');
    return href !== 'chrome://new-tab-page/' ? href : null;
  }, 'external search navigation');

  const checks = {
    searchboxPresent: initial.searchboxPresent,
    shortcutsPresent: initial.shortcutsPresent,
    searchboxVisible: initial.searchboxVisible,
    shortcutsVisible: initial.shortcutsVisible,
    searchBeforeShortcuts: initial.searchBeforeShortcuts,
    onlySearchAndPinnedShortcuts: initial.expectedStructure,
    forbiddenElementsAbsent: initial.forbidden.length === 0,
    customizePencilAbsent: !initial.customizePencilPresent,
    productCopyAbsent: !initial.productCopyPresent,
    soleVisibleNativeInput:
        initial.soleEditableRealInput && initial.realInputTextVisible,
    shortcutsOffLeavesOnlySearch:
        shortcutsDisabled.searchboxPresent &&
        !shortcutsDisabled.shortcutsPresent &&
        shortcutsDisabled.contentChildren.length === 1 &&
        shortcutsDisabled.contentChildren[0] === 'focusHome' &&
        shortcutsDisabled.homeChildren.length === 1 &&
        shortcutsDisabled.homeChildren[0] === 'focusSearch',
    shortcutsRestored: shortcutsDisabled.shortcutsRestored,
    typingPreservesValueCaretAndGeometry:
        afterTyping?.value === typedValue && afterTyping.focused &&
        afterTyping.caretAtEnd && afterTyping.textOriginStable,
    noSecondMirrorOrPageOverlay:
        afterTyping?.mirrorAbsent && afterTyping.overlayAbsent,
    completeQueryPreserved: completeValue === query,
    searchNavigates:
        destination.includes(query) ||
        destination.includes(encodeURIComponent(query)),
    searchUsesExternalProvider:
        destination.startsWith('https://') &&
        !destination.startsWith('https://focus-browser'),
  };
  assert.ok(Object.values(checks).every(Boolean), JSON.stringify({
    checks,
    initial,
    shortcutsDisabled,
    prepared,
    afterTyping,
    completeValue,
    destination,
  }));

  console.log(JSON.stringify({
    checks,
    initial,
    shortcutsDisabled,
    afterTyping,
    completeValue,
    query,
    destination,
  }));
} finally {
  socket.close();
}
