const [port = '9341'] = process.argv.slice(2);

const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(
  response => response.json(),
);
const target = targets.find(candidate => candidate.type === 'page');
if (!target?.webSocketDebuggerUrl) {
  throw new Error('No debuggable page target found');
}

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) {
    return;
  }
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) {
    reject(new Error(`${message.error.code}: ${message.error.message}`));
  } else {
    resolve(message.result ?? {});
  }
});

const send = (method, params = {}) => {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
};

const evaluate = async expression => {
  const result = await send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || 'Runtime evaluation failed');
  }
  return result.result?.value;
};

const waitFor = async (probe, attempts = 120) => {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const value = await probe().catch(() => null);
    if (value) {
      return value;
    }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error('Timed out waiting for the NTP contract');
};

await send('Page.enable');
await send('Runtime.enable');
await send('Page.navigate', { url: 'chrome://new-tab-page/' });

const initial = await waitFor(() => evaluate(`(() => {
  const app = document.querySelector('ntp-app');
  const root = app?.shadowRoot;
  const content = root?.querySelector('#content');
  const home = root?.querySelector('#focusHome');
  const search = root?.querySelector('#focusSearch');
  const searchContainer = search?.querySelector('#searchboxContainer');
  const searchbox = searchContainer?.querySelector('ntp-searchbox');
  const shortcuts = root?.querySelector('#focusShortcuts');
  const mostVisited = shortcuts?.querySelector('#mostVisited');
  if (!app || !root || !content || !home || !search || !searchContainer ||
      !searchbox?.shadowRoot || !shortcuts || !mostVisited ||
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
      if (match) return match;
      for (const element of current.querySelectorAll('*')) {
        if (element.shadowRoot) roots.push(element.shadowRoot);
      }
    }
    return null;
  };
  const forbiddenSelectors = [
    '#focusBrand', '#focusMark', '#focusMessage', '#focusShortcutsHeading',
    '#focusMeditationLink', 'ntp-logo', 'ntp-customize-buttons',
    '#customizeButtons', '#themeAttribution', '#contentBottomSpacer',
    '#backgroundImageAttribution', 'ntp-middle-slot-promo', 'ntp-modules',
    '#modules', '#oneGoogleBar', 'individual-promos',
  ];
  const forbidden = forbiddenSelectors.filter(selector =>
    root.querySelector(selector));
  const contentChildren = [...content.children].map(element => element.id);
  const homeChildren = [...home.children].map(element => element.id);
  const searchChildren = [...search.children].map(element => element.id);
  const searchContainerChildren =
      [...searchContainer.children].map(element => element.id);
  const shortcutChildren = [...shortcuts.children].map(element => element.id);
  const searchRect = searchbox.getBoundingClientRect();
  const shortcutsRect = mostVisited.getBoundingClientRect();
  return {
    href: location.href,
    lazyLoaded: true,
    searchboxPresent: Boolean(searchbox),
    shortcutsPresent: Boolean(mostVisited),
    searchboxVisible: isVisible(searchbox),
    shortcutsVisible: isVisible(mostVisited),
    searchBeforeShortcuts: searchRect.bottom <= shortcutsRect.top + 0.5,
    forbidden,
    customizePencilPresent: Boolean(deepQuery(root, '#customizeButton')),
    productCopyPresent:
        /Focus Browser|Один экран|Полный фокус/.test(root.textContent || ''),
    expectedStructure:
        contentChildren.length === 1 && contentChildren[0] === 'focusHome' &&
        homeChildren.length === 2 && homeChildren[0] === 'focusSearch' &&
        homeChildren[1] === 'focusShortcuts' &&
        searchChildren.length === 1 &&
        searchChildren[0] === 'searchboxContainer' &&
        searchContainerChildren.length === 1 &&
        searchContainerChildren[0] === 'searchbox' &&
        shortcutChildren.length === 1 &&
        shortcutChildren[0] === 'mostVisited',
    contentChildren,
    homeChildren,
    searchChildren,
    searchContainerChildren,
    shortcutChildren,
  };
})()`));

const shortcutsDisabled = await evaluate(`(async () => {
  const app = document.querySelector('ntp-app');
  const originalValue = app.shortcutsEnabled_;
  app.shortcutsEnabled_ = false;
  await app.updateComplete;
  const root = app.shadowRoot;
  const content = root.querySelector('#content');
  const home = root.querySelector('#focusHome');
  const searchbox = root.querySelector('#focusSearch ntp-searchbox');
  const forbiddenSelectors = [
    '#focusBrand', '#focusMark', '#focusMessage', '#focusShortcutsHeading',
    '#focusMeditationLink', 'ntp-logo', 'ntp-customize-buttons',
    '#customizeButtons', '#themeAttribution', '#contentBottomSpacer',
    '#backgroundImageAttribution', 'ntp-middle-slot-promo', 'ntp-modules',
    '#modules', '#oneGoogleBar', 'individual-promos',
  ];
  const state = {
    searchboxPresent: Boolean(searchbox),
    shortcutsPresent: Boolean(root.querySelector('#mostVisited')),
    forbidden: forbiddenSelectors.filter(selector => root.querySelector(selector)),
    productCopyPresent:
        /Focus Browser|Один экран|Полный фокус/.test(root.textContent || ''),
    contentChildren: [...content.children].map(element => element.id),
    homeChildren: [...home.children].map(element => element.id),
  };
  app.shortcutsEnabled_ = originalValue;
  await app.updateComplete;
  state.shortcutsRestored = Boolean(root.querySelector('#mostVisited'));
  return state;
})()`);

const query = 'focus-browser-ntp-contract-9c81';
const focusedInput = await evaluate(`(() => {
  const findInput = root => {
    for (const element of root.querySelectorAll('*')) {
      if (element instanceof HTMLInputElement) return element;
      if (element.shadowRoot) {
        const nested = findInput(element.shadowRoot);
        if (nested) return nested;
      }
    }
    return null;
  };
  const searchbox = document.querySelector('ntp-app')?.shadowRoot?.querySelector(
      '#focusSearch ntp-searchbox');
  const input = searchbox?.shadowRoot ? findInput(searchbox.shadowRoot) : null;
  if (!input) return false;
  input.focus();
  return Boolean(input);
})()`);

if (!focusedInput) {
  throw new Error('Could not focus the NTP search input');
}

await send('Input.insertText', { text: query });
await new Promise(resolve => setTimeout(resolve, 500));
await send('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
});
await send('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'Enter',
  code: 'Enter',
  windowsVirtualKeyCode: 13,
  nativeVirtualKeyCode: 13,
});

const destination = await waitFor(async () => {
  const href = await evaluate('location.href');
  return href !== 'chrome://new-tab-page/' ? href : null;
});

const checks = {
  lazyRendered: initial.lazyLoaded,
  searchboxPresent: initial.searchboxPresent,
  shortcutsPresent: initial.shortcutsPresent,
  searchboxVisible: initial.searchboxVisible,
  shortcutsVisible: initial.shortcutsVisible,
  searchBeforeShortcuts: initial.searchBeforeShortcuts,
  onlySearchAndPinnedShortcuts: initial.expectedStructure,
  forbiddenElementsAbsent: initial.forbidden.length === 0,
  customizePencilAbsent: !initial.customizePencilPresent,
  productCopyAbsent: !initial.productCopyPresent,
  shortcutsOffLeavesOnlySearch:
    shortcutsDisabled.searchboxPresent && !shortcutsDisabled.shortcutsPresent &&
    shortcutsDisabled.forbidden.length === 0 &&
    !shortcutsDisabled.productCopyPresent &&
    shortcutsDisabled.contentChildren.length === 1 &&
    shortcutsDisabled.contentChildren[0] === 'focusHome' &&
    shortcutsDisabled.homeChildren.length === 1 &&
    shortcutsDisabled.homeChildren[0] === 'focusSearch',
  shortcutsRestored: shortcutsDisabled.shortcutsRestored,
  searchNavigates: destination.includes(query),
  searchUsesExternalProvider:
    destination.startsWith('https://') &&
    !destination.startsWith('https://focus-browser'),
};

socket.close();
if (Object.values(checks).some(value => !value)) {
  throw new Error(JSON.stringify({ checks, initial, shortcutsDisabled, destination }));
}

console.log(JSON.stringify({ checks, initial, shortcutsDisabled, destination }));
