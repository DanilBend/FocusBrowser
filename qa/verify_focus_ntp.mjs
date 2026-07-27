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
    const exceptionDescription =
        result.exceptionDetails.exception?.description ||
        result.exceptionDetails.exception?.value || '';
    throw new Error(
        exceptionDescription || result.exceptionDetails.text ||
        'Runtime evaluation failed');
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
    const mostVisitedRoot = mostVisited?.shadowRoot;
    if (!app || !appRoot || !content || !home || !search ||
        !searchContainer || !searchboxRoot || !inputHost || !inputRoot ||
        !realInput || !shortcuts || !mostVisited || !mostVisitedRoot ||
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
    const parseRgb = value => {
      const match = value.match(
          /^rgba?\\(\\s*([\\d.]+)[,\\s]+([\\d.]+)[,\\s]+([\\d.]+)/i);
      return match ? match.slice(1, 4).map(Number) : null;
    };
    const isNeutral = value => {
      const rgb = parseRgb(value);
      return Boolean(rgb && Math.max(...rgb) - Math.min(...rgb) <= 8);
    };
    const isDark = value => {
      const rgb = parseRgb(value);
      return Boolean(rgb && rgb.reduce((sum, channel) => sum + channel, 0) /
          rgb.length <= 80);
    };
    const px = value => Number.parseFloat(value) || 0;
    const forbiddenSelectors = [
      '#focusBrand', '#focusMark', '#focusMessage',
      '#focusShortcutsHeading', '#focusMeditationLink', 'ntp-logo',
      '#themeAttribution',
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
    const addShortcut = deepQuery(appRoot, '#addShortcut');
    const customizeButton = deepQuery(appRoot, '#customizeButton');
    const searchSurface = searchboxRoot.querySelector('#inputWrapper');
    const voiceSearchButton = searchboxRoot.querySelector('#voiceSearchButton');
    const lensSearchButton = searchboxRoot.querySelector('#lensSearchButton');
    const visiblePinnedTiles = [...mostVisitedRoot.querySelectorAll('.tile')]
        .filter(isVisible);
    const addShortcutRect = addShortcut?.getBoundingClientRect();
    const searchSurfaceStyle = searchSurface && getComputedStyle(searchSurface);
    const customizeStyle =
        customizeButton && getComputedStyle(customizeButton);
    const searchCenterOffset =
        Math.abs(searchRect.left + searchRect.width / 2 - innerWidth / 2);
    const addShortcutCenterOffset = addShortcutRect ?
        Math.abs(addShortcutRect.left + addShortcutRect.width / 2 -
                 innerWidth / 2) :
        null;
    const homeStyle = getComputedStyle(home);
    const homeAnimationFrames = home.getAnimations().flatMap(animation => {
      const effect = animation.effect;
      return effect instanceof KeyframeEffect ? effect.getKeyframes() : [];
    });
    const homeAnimationOpacitySafe = homeAnimationFrames.every(frame => {
      if (frame.opacity === undefined) {
        return true;
      }
      const opacity = Number.parseFloat(String(frame.opacity));
      return Number.isFinite(opacity) && opacity >= 1;
    });

    return {
      href: location.href,
      searchboxPresent: Boolean(searchbox),
      shortcutsPresent: Boolean(mostVisited),
      searchboxVisible: isVisible(searchbox),
      shortcutsVisible: isVisible(mostVisited),
      searchBeforeShortcuts: searchRect.bottom <= shortcutsRect.top + 0.5,
      forbidden: forbiddenSelectors.filter(selector =>
        deepQuery(appRoot, selector)),
      addShortcutPresent: Boolean(addShortcut),
      addShortcutVisible: Boolean(addShortcut && isVisible(addShortcut)),
      addShortcutLabel:
          addShortcut?.getAttribute('aria-label') ||
          addShortcut?.getAttribute('title') || '',
      visiblePinnedTileCount: visiblePinnedTiles.length,
      searchCenterOffset,
      addShortcutCenterOffset,
      homeComputedOpacity: homeStyle.opacity,
      homeAnimationName: homeStyle.animationName,
      homeTransform: homeStyle.transform,
      homeAnimationFrameCount: homeAnimationFrames.length,
      homeAnimationOpacitySafe,
      loneShortcutCentered:
          visiblePinnedTiles.length === 0 && addShortcutRect &&
          addShortcutCenterOffset <= 1.5,
      searchSurfaceModernMonochrome: Boolean(
          searchSurfaceStyle && isVisible(searchSurface) &&
          isNeutral(searchSurfaceStyle.backgroundColor) &&
          isDark(searchSurfaceStyle.backgroundColor) &&
          px(searchSurfaceStyle.borderRadius) >= 20 &&
          px(searchSurfaceStyle.borderTopWidth) >= 0.75 &&
          searchSurfaceStyle.boxShadow !== 'none' &&
          searchCenterOffset <= 1.5),
      searchSurfaceStyle: searchSurfaceStyle ? {
        backgroundColor: searchSurfaceStyle.backgroundColor,
        borderRadius: searchSurfaceStyle.borderRadius,
        borderTopWidth: searchSurfaceStyle.borderTopWidth,
        boxShadow: searchSurfaceStyle.boxShadow,
      } : null,
      customizePencilPresent: Boolean(customizeButton),
      customizePencilVisible:
          Boolean(customizeButton && isVisible(customizeButton)),
      customizePencilLabel: customizeButton?.getAttribute('title') || '',
      customizePencilModernMonochrome: Boolean(
          customizeButton && customizeStyle && isVisible(customizeButton) &&
          customizeButton.getBoundingClientRect().width >= 40 &&
          customizeButton.getBoundingClientRect().height >= 40 &&
          isNeutral(customizeStyle.backgroundColor) &&
          isDark(customizeStyle.backgroundColor) &&
          isNeutral(customizeStyle.color) &&
          px(customizeStyle.borderRadius) >= 20),
      customizePencilStyle: customizeStyle ? {
        backgroundColor: customizeStyle.backgroundColor,
        borderRadius: customizeStyle.borderRadius,
        color: customizeStyle.color,
        height: customizeButton.getBoundingClientRect().height,
        width: customizeButton.getBoundingClientRect().width,
      } : null,
      voiceSearchPresentAndVisible:
          Boolean(voiceSearchButton && isVisible(voiceSearchButton)),
      voiceSearchLocalized:
          Boolean(voiceSearchButton?.getAttribute('title')),
      lensSearchPresentAndVisible:
          Boolean(lensSearchButton && isVisible(lensSearchButton)),
      lensSearchLocalized:
          Boolean(lensSearchButton?.getAttribute('title')),
      productCopyPresent:
          /Focus Browser|Один экран|Полный фокус/.test(
              appRoot.textContent || ''),
      expectedStructure:
          contentChildren.length === 2 &&
          contentChildren[0] === 'focusHome' &&
          contentChildren[1] === 'customizeButtons' &&
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

  const overlayEvents = await evaluate(`(async () => {
    const app = document.querySelector('ntp-app');
    const appRoot = app?.shadowRoot;
    const searchbox = appRoot?.querySelector('#focusSearch ntp-searchbox');
    if (!app || !appRoot || !searchbox) {
      return null;
    }

    searchbox.dispatchEvent(new Event('open-lens-search', {
      bubbles: true,
      composed: true,
    }));
    await app.updateComplete;
    const lensOpened = app.showLensUploadDialog_ === true &&
        Boolean(appRoot.querySelector('ntp-lens-upload-dialog'));
    app.showLensUploadDialog_ = false;
    await app.updateComplete;
    const lensClosed = app.showLensUploadDialog_ === false &&
        !appRoot.querySelector('ntp-lens-upload-dialog');

    searchbox.dispatchEvent(new Event('open-voice-search', {
      bubbles: true,
      composed: true,
    }));
    await app.updateComplete;
    const voiceOpened = app.showVoiceSearchOverlay_ === true &&
        searchbox.inVoiceSearchMode === true &&
        Boolean(appRoot.querySelector('ntp-voice-search-overlay'));
    app.showVoiceSearchOverlay_ = false;
    app.hasVoiceSearchError = false;
    await app.updateComplete;
    const voiceClosed = app.showVoiceSearchOverlay_ === false &&
        searchbox.inVoiceSearchMode === false &&
        !appRoot.querySelector('ntp-voice-search-overlay');

    return {lensOpened, lensClosed, voiceOpened, voiceClosed};
  })()`);

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
  await waitFor(() => evaluate(`(() => {
    ${ntpParts}
    const dropdown = searchboxRoot?.querySelector('#matches');
    return searchbox?.hasAttribute('dropdown-is-visible') &&
        !dropdown?.hidden &&
        dropdown?.shadowRoot?.querySelector('cr-searchbox-match') ? true : null;
  })()`), 'animated NTP search suggestions');
  const afterTyping = await evaluate(`(() => {
    ${ntpParts}
    const rect = realInput?.getBoundingClientRect();
    const dropdownContainer =
        searchboxRoot?.querySelector('.dropdownContainer');
    const dropdown = searchboxRoot?.querySelector('#matches');
    const dropdownRoot = dropdown?.shadowRoot;
    const dropdownContent = dropdownRoot?.querySelector('#content');
    const firstMatch = dropdownRoot?.querySelector('cr-searchbox-match');
    const searchContainer = appRoot?.querySelector('#searchboxContainer');
    const paletteStyle = searchContainer && getComputedStyle(searchContainer);
    const focusAnimationsDisabled =
        getComputedStyle(app).getPropertyValue('--cr-animations-disabled')
            .trim() === '1';
    const reducedMotion =
        matchMedia('(prefers-reduced-motion: reduce)').matches;
    const motionExpected = !focusAnimationsDisabled && !reducedMotion;
    const listAnimationName = dropdownContainer ?
        getComputedStyle(dropdownContainer).animationName : '';
    const rowAnimationName = firstMatch ?
        getComputedStyle(firstMatch).animationName : '';
    const parseColor = value => {
      const match = value.match(
          /rgba?\\(\\s*([\\d.]+)[,\\s]+([\\d.]+)[,\\s]+([\\d.]+)(?:[,\\s/]+([\\d.]+))?/i);
      return match ? {
        rgb: match.slice(1, 4).map(Number),
        alpha: match[4] === undefined ? 1 : Number(match[4]),
      } : null;
    };
    const palette = paletteStyle ? {
      background: parseColor(paletteStyle.getPropertyValue(
          '--color-searchbox-results-background')),
      hovered: parseColor(paletteStyle.getPropertyValue(
          '--color-searchbox-results-background-hovered')),
      selected: parseColor(paletteStyle.getPropertyValue(
          '--color-searchbox-results-background-selected')),
      indicator: parseColor(paletteStyle.getPropertyValue(
          '--color-searchbox-results-focus-indicator')),
      border: parseColor(paletteStyle.getPropertyValue(
          '--cr-searchbox-border')),
      glow: parseColor(paletteStyle.getPropertyValue(
          '--focus-searchbox-glow')),
    } : null;
    const luminance = color => color.rgb.reduce(
        (sum, channel) => sum + channel, 0) / color.rgb.length;
    const neutral = color =>
      Math.max(...color.rgb) - Math.min(...color.rgb) <= 4;
    const paletteIsDark = palette && luminance(palette.background) < 128;
    const hoverDelta = palette ?
        Math.abs(luminance(palette.hovered) -
                 luminance(palette.background)) : Infinity;
    const selectedDelta = palette ?
        Math.abs(luminance(palette.selected) -
                 luminance(palette.background)) : Infinity;
    const paletteDirectionCorrect = palette && (paletteIsDark ?
        luminance(palette.background) < luminance(palette.hovered) &&
            luminance(palette.hovered) < luminance(palette.selected) :
        luminance(palette.background) > luminance(palette.hovered) &&
            luminance(palette.hovered) > luminance(palette.selected));
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
      suggestionsOpen:
          searchbox.hasAttribute('dropdown-is-visible') &&
          !dropdown?.hidden && Boolean(dropdownContent && firstMatch),
      motionExpected,
      listAnimationName,
      rowAnimationName,
      suggestionPalette: palette ? {
        ...palette,
        hoverDelta,
        selectedDelta,
        paletteIsDark,
      } : null,
      suggestionPaletteDistinct: Boolean(
          palette && Object.values(palette).every(neutral) &&
          paletteDirectionCorrect && hoverDelta >= 6 && hoverDelta <= 10 &&
          selectedDelta >= 12 && selectedDelta <= 18 &&
          palette.indicator.alpha >= 0.35 &&
          palette.indicator.alpha <= 0.42 && palette.border.alpha <= 0.12 &&
          palette.glow.alpha >= 0.35 && palette.glow.alpha <= 0.42),
      suggestionAnimationsRespectMotion: motionExpected ?
          listAnimationName.includes('focus-ntp-suggestions-enter') &&
          rowAnimationName.includes('focus-ntp-suggestion-row-enter') :
          listAnimationName === 'none' && rowAnimationName === 'none',
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
    addShortcutPresentAndVisible:
        initial.addShortcutPresent && initial.addShortcutVisible,
    addShortcutLocalized: initial.addShortcutLabel.length > 0,
    loneShortcutCentered: initial.loneShortcutCentered,
    searchBeforeShortcuts: initial.searchBeforeShortcuts,
    searchSurfaceModernMonochrome:
        initial.searchSurfaceModernMonochrome,
    onlySearchAndPinnedShortcuts: initial.expectedStructure,
    homeNeverFadesOnEntry:
        Number.parseFloat(initial.homeComputedOpacity) >= 1 &&
        initial.homeAnimationOpacitySafe &&
        initial.homeAnimationName === 'none' &&
        initial.homeTransform === 'none' &&
        initial.homeAnimationFrameCount === 0,
    forbiddenElementsAbsent: initial.forbidden.length === 0,
    compactCustomizationPresentAndVisible:
        initial.customizePencilPresent && initial.customizePencilVisible,
    customizationLocalized: initial.customizePencilLabel.length > 0,
    customizationModernMonochrome:
        initial.customizePencilModernMonochrome,
    voiceSearchPresentVisibleAndLocalized:
        initial.voiceSearchPresentAndVisible && initial.voiceSearchLocalized,
    lensSearchPresentVisibleAndLocalized:
        initial.lensSearchPresentAndVisible && initial.lensSearchLocalized,
    lensOverlayEventOpensAndResets:
        overlayEvents?.lensOpened && overlayEvents.lensClosed,
    voiceOverlayEventOpensAndResets:
        overlayEvents?.voiceOpened && overlayEvents.voiceClosed,
    productCopyAbsent: !initial.productCopyPresent,
    soleVisibleNativeInput:
        initial.soleEditableRealInput && initial.realInputTextVisible,
    shortcutsOffLeavesOnlySearch:
        shortcutsDisabled.searchboxPresent &&
        !shortcutsDisabled.shortcutsPresent &&
        shortcutsDisabled.contentChildren.length === 2 &&
        shortcutsDisabled.contentChildren[0] === 'focusHome' &&
        shortcutsDisabled.contentChildren[1] === 'customizeButtons' &&
        shortcutsDisabled.homeChildren.length === 1 &&
        shortcutsDisabled.homeChildren[0] === 'focusSearch',
    shortcutsRestored: shortcutsDisabled.shortcutsRestored,
    typingPreservesValueCaretAndGeometry:
        afterTyping?.value === typedValue && afterTyping.focused &&
        afterTyping.caretAtEnd && afterTyping.suggestionsOpen &&
        afterTyping.textOriginStable,
    suggestionAnimationsRespectMotion:
        afterTyping?.suggestionAnimationsRespectMotion,
    suggestionPaletteDistinct: afterTyping?.suggestionPaletteDistinct,
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
    overlayEvents,
    shortcutsDisabled,
    prepared,
    afterTyping,
    completeValue,
    destination,
  }));

  console.log(JSON.stringify({
    checks,
    initial,
    overlayEvents,
    shortcutsDisabled,
    afterTyping,
    completeValue,
    query,
    destination,
  }));
} finally {
  socket.close();
}
