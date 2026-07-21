#!/usr/bin/env node

// Runtime contract for the Focus Browser new tab page. Attach to a freshly
// built browser started with --remote-debugging-port=<port> and a disposable
// profile. This script never launches or terminates a browser process.

const [port = '9341'] = process.argv.slice(2);

const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(
    response => response.json());
const target = targets.find(candidate => candidate.type === 'page');
if (!target?.webSocketDebuggerUrl) {
  throw new Error('No debuggable page target found');
}

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
socket.addEventListener('close', () => {
  for (const request of pending.values()) {
    request.reject(new Error('DevTools WebSocket closed'));
  }
  pending.clear();
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
  for (let attempt = 0; attempt < attempts; attempt++) {
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

const motionParts = `
  const app = document.querySelector('ntp-app');
  const appRoot = app?.shadowRoot;
  const searchbox = appRoot?.querySelector('#focusSearch ntp-searchbox');
  const searchboxRoot = searchbox?.shadowRoot;
  const inputHost = searchboxRoot?.querySelector('#input');
  const inputRoot = inputHost?.shadowRoot;
  const realInput = inputRoot?.querySelector('#input');
  const mirror = inputRoot?.querySelector('#focusTypingMirror');
  const mirrorText = inputRoot?.querySelector('#focusTypingMirrorText');
`;

const transparentColorFunction = `
  const isTransparentColor = color =>
    color === 'transparent' ||
    /^rgba\\([^)]*,\\s*0(?:\\.0+)?\\)$/.test(color);
`;

await send('Page.enable');
await send('Runtime.enable');

try {
  await send('Page.navigate', {url: 'chrome://new-tab-page/'});

  const initial = await waitFor(() => evaluate(`(() => {
    ${motionParts}
    const content = appRoot?.querySelector('#content');
    const home = appRoot?.querySelector('#focusHome');
    const search = appRoot?.querySelector('#focusSearch');
    const searchContainer = search?.querySelector('#searchboxContainer');
    const shortcuts = appRoot?.querySelector('#focusShortcuts');
    const mostVisited = shortcuts?.querySelector('#mostVisited');
    if (!app || !appRoot || !content || !home || !search ||
        !searchContainer || !searchboxRoot || !inputHost || !inputRoot ||
        !realInput || !mirror || !mirrorText || !shortcuts || !mostVisited ||
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
      'individual-promos',
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
    ${transparentColorFunction}
    const initialFill = getComputedStyle(realInput)
                            .getPropertyValue('-webkit-text-fill-color');

    return {
      href: location.href,
      lazyLoaded: true,
      searchboxPresent: Boolean(searchbox),
      shortcutsPresent: Boolean(mostVisited),
      searchboxVisible: isVisible(searchbox),
      shortcutsVisible: isVisible(mostVisited),
      searchBeforeShortcuts: searchRect.bottom <= shortcutsRect.top + 0.5,
      forbidden: forbiddenSelectors.filter(selector =>
        appRoot.querySelector(selector)),
      customizePencilPresent: Boolean(deepQuery(appRoot, '#customizeButton')),
      productCopyPresent:
          /Focus Browser|\u041e\u0434\u0438\u043d \u044d\u043a\u0440\u0430\u043d|\u041f\u043e\u043b\u043d\u044b\u0439 \u0444\u043e\u043a\u0443\u0441/.test(
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
      mirrorPresent: true,
      mirrorInitiallyHidden: mirror.hidden,
      mirrorAriaHidden: mirror.getAttribute('aria-hidden') === 'true',
      mirrorInitiallyEmpty: mirrorText.childElementCount === 0,
      soleEditableRealInput:
          editableElements.length === 1 && editableElements[0] === realInput,
      realInputInitiallyVisible: !isTransparentColor(initialFill),
      contentChildren,
      homeChildren,
      searchChildren,
      searchContainerChildren,
      shortcutChildren,
    };
  })()`), 'lazy NTP search, shortcuts and mirror');

  const shortcutsDisabled = await evaluate(`(async () => {
    const app = document.querySelector('ntp-app');
    const originalValue = app.shortcutsEnabled_;
    app.shortcutsEnabled_ = false;
    await app.updateComplete;
    const root = app.shadowRoot;
    const content = root.querySelector('#content');
    const home = root.querySelector('#focusHome');
    const forbiddenSelectors = [
      '#focusBrand', '#focusMark', '#focusMessage',
      '#focusShortcutsHeading', '#focusMeditationLink', 'ntp-logo',
      'ntp-customize-buttons', '#customizeButtons', '#themeAttribution',
      '#contentBottomSpacer', '#backgroundImageAttribution',
      'ntp-middle-slot-promo', 'ntp-modules', '#modules', '#oneGoogleBar',
      'individual-promos',
    ];
    const state = {
      searchboxPresent: Boolean(root.querySelector(
          '#focusSearch ntp-searchbox')),
      shortcutsPresent: Boolean(root.querySelector('#mostVisited')),
      forbidden: forbiddenSelectors.filter(selector => root.querySelector(
          selector)),
      productCopyPresent:
          /Focus Browser|\u041e\u0434\u0438\u043d \u044d\u043a\u0440\u0430\u043d|\u041f\u043e\u043b\u043d\u044b\u0439 \u0444\u043e\u043a\u0443\u0441/.test(
              root.textContent || ''),
      contentChildren: [...content.children].map(element => element.id),
      homeChildren: [...home.children].map(element => element.id),
    };
    app.shortcutsEnabled_ = originalValue;
    await app.updateComplete;
    state.shortcutsRestored = Boolean(root.querySelector('#mostVisited'));
    return state;
  })()`);

  const reducedChar = 'x';
  const insertedGrapheme = 'e\u0301';
  const rapidChar = 'z';
  const motionValue = reducedChar + insertedGrapheme + rapidChar;
  const query = 'xqz9c81-focus-browser-ntp-contract';
  const focusedInput = await evaluate(`(() => {
    ${motionParts}
    if (!realInput || !mirror || !mirrorText) {
      return false;
    }
    // The omnibox can transfer text into the NTP searchbox while the page is
    // becoming active. Normalize the isolated test input before exercising
    // grapheme animation so the contract is independent of startup focus.
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
    return inputRoot.activeElement === realInput && realInput.value === '';
  })()`);
  if (!focusedInput) {
    throw new Error('Could not focus the empty NTP search input');
  }

  await send('Emulation.setEmulatedMedia', {
    media: '',
    features: [{name: 'prefers-reduced-motion', value: 'reduce'}],
  });
  await pause(100);
  await send('Input.insertText', {text: reducedChar});
  await pause(50);

  const reducedMotion = await evaluate(`(() => {
    ${motionParts}
    if (!inputHost || !realInput || !mirror || !mirrorText) {
      return null;
    }
    const animations = mirror.getAnimations({subtree: true}).filter(
        animation => animation.id === 'focus-typing-grapheme-reveal');
    ${transparentColorFunction}
    const fill = getComputedStyle(realInput)
                     .getPropertyValue('-webkit-text-fill-color');
    return {
      animationCount: animations.length,
      revealActive: inputHost.hasAttribute('focus-typing-reveal-active'),
      mirrorHidden: mirror.hidden,
      mirrorChildCount: mirrorText.childElementCount,
      realInputTextVisible: !isTransparentColor(fill),
      value: realInput.value,
    };
  })()`);

  await send('Emulation.setEmulatedMedia', {media: '', features: []});
  await pause(100);
  await send('Input.insertText', {text: insertedGrapheme});
  await pause(10);

  const firstReveal = await evaluate(`(() => {
    ${motionParts}
    if (!inputHost || !realInput || !mirror || !mirrorText) {
      return null;
    }
    const animations = mirror.getAnimations({subtree: true}).filter(
        animation => animation.id === 'focus-typing-grapheme-reveal');
    const insertedSpan = mirrorText.lastElementChild;
    const animation = animations.find(candidate =>
      candidate.effect?.target === insertedSpan) || null;
    inputHost.__focusQaFirstAnimation = animation;
    inputHost.__focusQaFirstTarget = insertedSpan;
    const inputRect = realInput.getBoundingClientRect();
    const mirrorRect = mirror.getBoundingClientRect();
    const style = getComputedStyle(realInput);
    const fill = style.getPropertyValue('-webkit-text-fill-color');
    const caret = style.getPropertyValue('caret-color');
    ${transparentColorFunction}
    return {
      animationCount: animations.length,
      animationId: animation?.id || '',
      playState: animation?.playState || '',
      targetIsInsertedSpan:
          animation?.effect?.target === insertedSpan &&
          insertedSpan?.textContent === ${JSON.stringify(insertedGrapheme)},
      targetConnected: Boolean(insertedSpan?.isConnected),
      revealActive: inputHost.hasAttribute('focus-typing-reveal-active'),
      mirrorHidden: mirror.hidden,
      mirrorAriaHidden: mirror.getAttribute('aria-hidden') === 'true',
      mirrorMatchesValue: mirrorText.textContent === realInput.value,
      mirrorSpanCount: mirrorText.childElementCount,
      mirrorMatchesInputGeometry:
          Math.abs(mirrorRect.left - inputRect.left) <= 1 &&
          Math.abs(mirrorRect.top - inputRect.top) <= 1 &&
          Math.abs(mirrorRect.width - inputRect.width) <= 1 &&
          Math.abs(mirrorRect.height - inputRect.height) <= 1,
      realInputTransparent: isTransparentColor(fill),
      caretVisible: !isTransparentColor(caret),
      realInputFocused: realInput.matches(':focus'),
      value: realInput.value,
    };
  })()`);

  await send('Input.insertText', {text: rapidChar});
  await pause(10);

  const overlappingReveal = await evaluate(`(() => {
    ${motionParts}
    if (!inputHost || !realInput || !mirror || !mirrorText) {
      return null;
    }
    const animations = mirror.getAnimations({subtree: true}).filter(
        animation => animation.id === 'focus-typing-grapheme-reveal');
    const firstAnimation = inputHost.__focusQaFirstAnimation;
    const firstTarget = inputHost.__focusQaFirstTarget;
    const newestSpan = mirrorText.lastElementChild;
    const newestAnimation = animations.find(candidate =>
      candidate !== firstAnimation && candidate.effect?.target === newestSpan);
    return {
      animationCount: animations.length,
      preservesFirstAnimation: animations.includes(firstAnimation),
      firstAnimationStillRunning:
          firstAnimation?.playState === 'running' ||
          firstAnimation?.playState === 'pending',
      firstTargetStillConnected: Boolean(firstTarget?.isConnected),
      distinctNewestAnimation: Boolean(
          newestAnimation && newestAnimation !== firstAnimation),
      newestAnimationId: newestAnimation?.id || '',
      newestAnimationRunning:
          newestAnimation?.playState === 'running' ||
          newestAnimation?.playState === 'pending',
      newestTargetsInsertedSpan:
          newestAnimation?.effect?.target === newestSpan &&
          newestSpan?.textContent === ${JSON.stringify(rapidChar)},
      mirrorMatchesValue: mirrorText.textContent === realInput.value,
      mirrorSpanCount: mirrorText.childElementCount,
      revealActive: inputHost.hasAttribute('focus-typing-reveal-active'),
      value: realInput.value,
    };
  })()`);

  await pause(450);
  const afterFinish = await evaluate(`(() => {
    ${motionParts}
    if (!inputHost || !realInput || !mirror || !mirrorText) {
      return null;
    }
    const animations = mirror.getAnimations({subtree: true}).filter(
        animation => animation.id === 'focus-typing-grapheme-reveal');
    const fill = getComputedStyle(realInput)
                     .getPropertyValue('-webkit-text-fill-color');
    ${transparentColorFunction}
    return {
      animationCount: animations.length,
      revealActive: inputHost.hasAttribute('focus-typing-reveal-active'),
      mirrorHidden: mirror.hidden,
      mirrorEmpty: mirrorText.childElementCount === 0 &&
          mirrorText.textContent === '',
      realInputTextVisible: !isTransparentColor(fill),
      realInputFocused: realInput.matches(':focus'),
      value: realInput.value,
    };
  })()`);

  const selectedMotionValue = await evaluate(`(() => {
    ${motionParts}
    if (!realInput) {
      return false;
    }
    realInput.focus();
    realInput.select();
    return realInput.selectionStart === 0 &&
        realInput.selectionEnd === realInput.value.length;
  })()`);
  if (!selectedMotionValue) {
    throw new Error('Could not select the completed grapheme QA value');
  }
  await send('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Backspace',
    code: 'Backspace',
    windowsVirtualKeyCode: 8,
    nativeVirtualKeyCode: 8,
  });
  await send('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Backspace',
    code: 'Backspace',
    windowsVirtualKeyCode: 8,
    nativeVirtualKeyCode: 8,
  });
  const clearedValue = await waitFor(() => evaluate(`(() => {
    ${motionParts}
    return realInput?.value === '' ? true : null;
  })()`), 'cleared NTP search value');

  await send('Input.insertText', {text: query});
  await pause(450);
  const completeValue = await evaluate(`(() => {
    ${motionParts}
    return realInput?.value || '';
  })()`);

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
  }, 'external search navigation');

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
    mirrorAndRealInputContract:
        initial.mirrorPresent && initial.mirrorInitiallyHidden &&
        initial.mirrorAriaHidden && initial.mirrorInitiallyEmpty &&
        initial.soleEditableRealInput && initial.realInputInitiallyVisible,
    shortcutsOffLeavesOnlySearch:
        shortcutsDisabled.searchboxPresent &&
        !shortcutsDisabled.shortcutsPresent &&
        shortcutsDisabled.forbidden.length === 0 &&
        !shortcutsDisabled.productCopyPresent &&
        shortcutsDisabled.contentChildren.length === 1 &&
        shortcutsDisabled.contentChildren[0] === 'focusHome' &&
        shortcutsDisabled.homeChildren.length === 1 &&
        shortcutsDisabled.homeChildren[0] === 'focusSearch',
    shortcutsRestored: shortcutsDisabled.shortcutsRestored,
    reducedMotionSkipsGraphemeReveal:
        reducedMotion?.animationCount === 0 &&
        !reducedMotion.revealActive && reducedMotion.mirrorHidden &&
        reducedMotion.mirrorChildCount === 0 &&
        reducedMotion.realInputTextVisible &&
        reducedMotion.value === reducedChar,
    insertionRunsPerGraphemeReveal:
        firstReveal?.animationCount === 1 &&
        firstReveal.animationId === 'focus-typing-grapheme-reveal' &&
        (firstReveal.playState === 'running' ||
         firstReveal.playState === 'pending') &&
        firstReveal.targetIsInsertedSpan && firstReveal.targetConnected &&
        firstReveal.revealActive && !firstReveal.mirrorHidden &&
        firstReveal.mirrorAriaHidden && firstReveal.mirrorMatchesValue &&
        firstReveal.mirrorSpanCount === 2 &&
        firstReveal.mirrorMatchesInputGeometry &&
        firstReveal.realInputTransparent && firstReveal.caretVisible &&
        firstReveal.realInputFocused &&
        firstReveal.value === reducedChar + insertedGrapheme,
    rapidTypingPreservesOverlappingAnimations:
        overlappingReveal?.animationCount >= 2 &&
        overlappingReveal.preservesFirstAnimation &&
        overlappingReveal.firstAnimationStillRunning &&
        overlappingReveal.firstTargetStillConnected &&
        overlappingReveal.distinctNewestAnimation &&
        overlappingReveal.newestAnimationId ===
            'focus-typing-grapheme-reveal' &&
        overlappingReveal.newestAnimationRunning &&
        overlappingReveal.newestTargetsInsertedSpan &&
        overlappingReveal.mirrorMatchesValue &&
        overlappingReveal.mirrorSpanCount === 3 &&
        overlappingReveal.revealActive &&
        overlappingReveal.value === motionValue,
    finishRestoresRealTextAndHidesMirror:
        afterFinish?.animationCount === 0 && !afterFinish.revealActive &&
        afterFinish.mirrorHidden && afterFinish.mirrorEmpty &&
        afterFinish.realInputTextVisible && afterFinish.realInputFocused &&
        afterFinish.value === motionValue,
    combiningSequenceIsSingleGraphemeSpan:
        firstReveal?.targetIsInsertedSpan &&
        firstReveal.mirrorSpanCount === 2 &&
        firstReveal.value.length > firstReveal.mirrorSpanCount,
    completeQueryPreserved: clearedValue && completeValue === query,
    searchNavigates:
        destination.includes(query) ||
        destination.includes(encodeURIComponent(query)),
    searchUsesExternalProvider:
        destination.startsWith('https://') &&
        !destination.startsWith('https://focus-browser'),
  };

  if (Object.values(checks).some(value => !value)) {
    throw new Error(JSON.stringify({
      checks,
      initial,
      shortcutsDisabled,
      reducedMotion,
      firstReveal,
      overlappingReveal,
      afterFinish,
      clearedValue,
      completeValue,
      query,
      destination,
    }));
  }

  console.log(JSON.stringify({
    checks,
    initial,
    shortcutsDisabled,
    reducedMotion,
    firstReveal,
    overlappingReveal,
    afterFinish,
    clearedValue,
    completeValue,
    insertedGrapheme,
    query,
    destination,
  }));
} finally {
  socket.close();
}
