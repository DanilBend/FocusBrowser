// Copyright 2026 The Focus Browser Authors

'use strict';

(() => {
  if (globalThis.__focusTextMotionLoaded === true) {
    return;
  }
  globalThis.__focusTextMotionLoaded = true;

  const STORAGE_KEY = 'motion_enabled';
  const MAX_GRAPHEMES = 16;
  const MAX_ACTIVE_GLYPHS = 48;
  const ANIMATION_MS = 340;
  const INPUT_TYPES = new Set([
    'text', 'search', 'email', 'url', 'tel', 'number', 'password',
  ]);

  const pendingInsertions = new WeakMap();
  const composingEditors = new WeakSet();
  const suppressNextInput = new WeakSet();
  const activeGlyphs = [];
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const forcedColors = matchMedia('(forced-colors: active)');
  const segmenter = typeof Intl.Segmenter === 'function' ?
    new Intl.Segmenter(undefined, {granularity: 'grapheme'}) : null;

  let preferenceEnabled = false;
  let overlayHost = null;
  let overlayLayer = null;

  function motionAllowed() {
    return preferenceEnabled && !reducedMotion.matches &&
        !forcedColors.matches;
  }

  function graphemes(text) {
    if (typeof text !== 'string' || text.length === 0) {
      return [];
    }
    const values = segmenter ?
      Array.from(segmenter.segment(text), item => item.segment) :
      Array.from(text);
    return values.slice(-MAX_GRAPHEMES);
  }

  function capInsertionText(text) {
    if (typeof text !== 'string' || text.length === 0) {
      return '';
    }
    // Bound transient event data before it can outlive the input event. The
    // animation itself applies the stricter grapheme cap in graphemes().
    return text.slice(-256);
  }

  function isInsertion(inputType) {
    return typeof inputType === 'string' &&
        inputType.startsWith('insert');
  }

  function setImportantStyles(element, properties) {
    for (const [name, value] of Object.entries(properties)) {
      element.style.setProperty(name, value, 'important');
    }
  }

  function ensureOverlayLayer() {
    if (overlayHost?.isConnected && overlayLayer) {
      return overlayLayer;
    }
    const root = document.documentElement;
    if (!root) {
      return null;
    }

    overlayHost = document.createElement('focus-text-motion-layer');
    overlayHost.setAttribute('aria-hidden', 'true');
    overlayHost.setAttribute('data-focus-text-motion', '');
    setImportantStyles(overlayHost, {
      all: 'initial',
      display: 'block',
      position: 'fixed',
      inset: '0',
      width: '100%',
      height: '100%',
      'pointer-events': 'none',
      'z-index': '2147483647',
      contain: 'layout style',
      visibility: 'visible',
      opacity: '1',
      transform: 'none',
      filter: 'none',
      overflow: 'visible',
    });

    const shadow = overlayHost.attachShadow({mode: 'closed'});
    overlayLayer = document.createElement('div');
    overlayLayer.setAttribute('aria-hidden', 'true');
    setImportantStyles(overlayLayer, {
      display: 'block',
      position: 'fixed',
      inset: '0',
      width: '100%',
      height: '100%',
      'pointer-events': 'none',
      overflow: 'visible',
      visibility: 'visible',
      opacity: '1',
    });
    shadow.appendChild(overlayLayer);
    root.appendChild(overlayHost);
    updateActiveCount();
    return overlayLayer;
  }

  function updateActiveCount() {
    if (overlayHost) {
      overlayHost.setAttribute(
          'data-focus-motion-active', String(activeGlyphs.length));
    }
  }

  function cleanupGlyph(record) {
    record.maskAnimation?.cancel();
    record.glyphAnimation?.cancel();
    record.mask.remove();
    record.glyph.remove();
    const index = activeGlyphs.indexOf(record);
    if (index !== -1) {
      activeGlyphs.splice(index, 1);
    }
    updateActiveCount();
  }

  function cancelAnimations() {
    for (const record of [...activeGlyphs]) {
      cleanupGlyph(record);
    }
  }

  function setPreference(enabled) {
    preferenceEnabled = enabled === true;
    if (!motionAllowed()) {
      cancelAnimations();
    }
  }

  function elementFromNode(node) {
    if (node instanceof Element) {
      return node;
    }
    return node?.parentElement || null;
  }

  function editableTarget(node) {
    const element = elementFromNode(node);
    if (!element) {
      return null;
    }
    if (element instanceof HTMLInputElement) {
      return INPUT_TYPES.has(element.type.toLowerCase()) ? element : null;
    }
    if (element instanceof HTMLTextAreaElement) {
      return element;
    }
    const editable = element.closest('[contenteditable]');
    return editable?.isContentEditable ? editable : null;
  }

  function editableEventTarget(event) {
    const path = typeof event.composedPath === 'function' ?
      event.composedPath() : [];
    return editableTarget(path[0] || event.target);
  }

  function isPassword(target) {
    return target instanceof HTMLInputElement &&
        target.type.toLowerCase() === 'password';
  }

  function safeSelectionStart(control) {
    try {
      return typeof control.selectionStart === 'number' ?
        control.selectionStart : null;
    } catch {
      return null;
    }
  }

  function opaqueBackground(target) {
    let element = target;
    while (element instanceof Element) {
      const color = getComputedStyle(element).backgroundColor;
      if (color && color !== 'transparent' &&
          !/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color)) {
        return color;
      }
      element = element.parentElement;
    }
    const rootColor = getComputedStyle(document.documentElement)
                          .backgroundColor;
    if (rootColor && rootColor !== 'transparent' &&
        !/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(rootColor)) {
      return rootColor;
    }
    return 'Canvas';
  }

  function copyTextStyle(from, to) {
    const properties = [
      'fontFamily', 'fontSize', 'fontStyle', 'fontWeight', 'fontStretch',
      'fontVariant', 'fontKerning', 'fontFeatureSettings',
      'fontVariationSettings', 'letterSpacing', 'lineHeight', 'textAlign',
      'textIndent', 'textTransform', 'direction', 'writingMode', 'tabSize',
      'wordBreak', 'overflowWrap', 'paddingTop', 'paddingRight',
      'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderRightWidth',
      'borderBottomWidth', 'borderLeftWidth', 'borderTopStyle',
      'borderRightStyle', 'borderBottomStyle', 'borderLeftStyle',
    ];
    for (const property of properties) {
      to.style[property] = from[property];
    }
  }

  function formControlMeasurements(target, insertedText, password) {
    const bounds = target.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) {
      return [];
    }
    const computed = getComputedStyle(target);
    const caret = safeSelectionStart(target);
    const glyphs = password ? ['\u2022'] : graphemes(insertedText);
    if (glyphs.length === 0) {
      return [];
    }

    let prefix;
    if (password) {
      // Never read a password value or InputEvent.data. Only the numeric caret
      // position is used to place one generic bullet for the completed edit.
      prefix = '\u2022'.repeat(Math.max(0, (caret ?? 1) - 1));
    } else {
      const value = target.value;
      const end = caret ?? value.length;
      const insertedLength = glyphs.join('').length;
      prefix = value.slice(0, Math.max(0, end - insertedLength));
    }

    const mirror = document.createElement('div');
    copyTextStyle(computed, mirror);
    Object.assign(mirror.style, {
      position: 'fixed',
      left: `${bounds.left}px`,
      top: `${bounds.top}px`,
      width: `${bounds.width}px`,
      height: `${bounds.height}px`,
      boxSizing: computed.boxSizing,
      whiteSpace: target instanceof HTMLTextAreaElement ? 'pre-wrap' : 'pre',
      overflow: 'hidden',
      visibility: 'hidden',
      pointerEvents: 'none',
      color: computed.color,
    });
    mirror.appendChild(document.createTextNode(prefix));
    const spans = glyphs.map(glyph => {
      const span = document.createElement('span');
      span.textContent = glyph;
      mirror.appendChild(span);
      return span;
    });
    document.documentElement.appendChild(mirror);
    mirror.scrollLeft = target.scrollLeft;
    mirror.scrollTop = target.scrollTop;

    const measurements = spans.map((span, index) => ({
      glyph: glyphs[index],
      rect: span.getBoundingClientRect(),
      computed,
    }));
    mirror.remove();
    return measurements;
  }

  function caretFallbackMeasurements(target, glyphs, computed) {
    const selection = target.ownerDocument.getSelection();
    let caretRect = null;
    if (selection?.rangeCount && selection.isCollapsed) {
      const range = selection.getRangeAt(0).cloneRange();
      const rects = range.getClientRects();
      caretRect = rects.length ? rects[0] : range.getBoundingClientRect();
    }
    if (!caretRect || (caretRect.width === 0 && caretRect.height === 0)) {
      caretRect = target.getBoundingClientRect();
    }

    const probe = document.createElement('canvas');
    const context = probe.getContext('2d');
    if (context) {
      context.font = computed.font;
    }
    const fallbackWidth = Math.max(1, parseFloat(computed.fontSize) * .6);
    const widths = glyphs.map(glyph => Math.max(
        1, context ? context.measureText(glyph).width : fallbackWidth));
    const totalWidth = widths.reduce((sum, width) => sum + width, 0);
    let x = computed.direction === 'rtl' ? caretRect.right :
      caretRect.left - totalWidth;
    const height = caretRect.height || parseFloat(computed.lineHeight) ||
        parseFloat(computed.fontSize) * 1.2;

    return glyphs.map((glyph, index) => {
      const width = widths[index];
      const rect = new DOMRect(x, caretRect.top, width, height);
      x += width;
      return {glyph, rect, computed};
    });
  }

  function editableMeasurements(target, insertedText) {
    const glyphs = graphemes(insertedText);
    if (glyphs.length === 0) {
      return [];
    }
    const selection = target.ownerDocument.getSelection();
    const focusNode = selection?.focusNode;
    const focusOffset = selection?.focusOffset ?? 0;
    const styleElement = elementFromNode(focusNode) || target;
    const computed = getComputedStyle(styleElement);
    const insertedLength = glyphs.join('').length;

    if (selection?.isCollapsed && focusNode?.nodeType === Node.TEXT_NODE &&
        target.contains(focusNode) && focusOffset >= insertedLength) {
      let offset = focusOffset - insertedLength;
      return glyphs.map(glyph => {
        const range = document.createRange();
        range.setStart(focusNode, offset);
        offset += glyph.length;
        range.setEnd(focusNode, offset);
        const rects = range.getClientRects();
        const rect = rects.length ? rects[0] : range.getBoundingClientRect();
        return {glyph, rect, computed};
      });
    }
    return caretFallbackMeasurements(target, glyphs, computed);
  }

  function animateMeasurement(target, measurement, delay, password) {
    const layer = ensureOverlayLayer();
    const {glyph, rect, computed} = measurement;
    if (!layer || !rect || rect.width < 0 || rect.height <= 0 ||
        /^\s*$/u.test(glyph)) {
      return;
    }

    while (activeGlyphs.length >= MAX_ACTIVE_GLYPHS) {
      cleanupGlyph(activeGlyphs[0]);
    }

    const mask = document.createElement('span');
    const glyphView = document.createElement('span');
    for (const view of [mask, glyphView]) {
      Object.assign(view.style, {
        position: 'fixed',
        left: `${rect.left}px`,
        top: `${rect.top}px`,
        width: `${Math.max(1, rect.width)}px`,
        height: `${rect.height}px`,
        margin: '0',
        padding: '0',
        border: '0',
        pointerEvents: 'none',
        boxSizing: 'border-box',
        whiteSpace: 'pre',
        overflow: 'visible',
      });
    }
    mask.style.background = opaqueBackground(target);
    glyphView.textContent = glyph;
    glyphView.style.color = computed.color;
    glyphView.style.font = computed.font;
    glyphView.style.fontKerning = computed.fontKerning;
    glyphView.style.fontFeatureSettings = computed.fontFeatureSettings;
    glyphView.style.fontVariationSettings = computed.fontVariationSettings;
    glyphView.style.letterSpacing = computed.letterSpacing;
    glyphView.style.lineHeight = computed.lineHeight;
    glyphView.style.textTransform = computed.textTransform;
    glyphView.style.direction = computed.direction;
    layer.append(mask, glyphView);

    const record = {
      mask,
      glyph: glyphView,
      passwordTarget: password ? target : null,
      maskAnimation: null,
      glyphAnimation: null,
    };
    activeGlyphs.push(record);
    updateActiveCount();

    record.maskAnimation = mask.animate([
      {opacity: 1, offset: 0},
      {opacity: 1, offset: 0.2},
      {opacity: 0, offset: 0.88},
      {opacity: 0, offset: 1},
    ], {
      duration: ANIMATION_MS,
      delay,
      easing: 'cubic-bezier(.2,.8,.2,1)',
      fill: 'both',
    });
    record.glyphAnimation = glyphView.animate([
      {opacity: 0, transform: 'translateY(.18em) scale(.96)',
        filter: 'blur(1.5px)', offset: 0},
      {opacity: 1, transform: 'translateY(0) scale(1)',
        filter: 'blur(0)', offset: 0.62},
      {opacity: 0, transform: 'translateY(0) scale(1)',
        filter: 'blur(0)', offset: 1},
    ], {
      duration: ANIMATION_MS,
      delay,
      easing: 'cubic-bezier(.16,1,.3,1)',
      fill: 'both',
    });
    record.glyphAnimation.finished.then(
        () => cleanupGlyph(record), () => cleanupGlyph(record));
  }

  function animateInsertion(target, insertedText, password) {
    if (!motionAllowed() || !target?.isConnected) {
      return;
    }
    if (password) {
      // Rapid typing may deliver several password input events before the
      // previous generic marker finishes. Keep exactly one indistinguishable
      // bullet per password control so neither the character nor the burst
      // length is reflected by the animation overlay.
      for (const record of [...activeGlyphs]) {
        cleanupGlyph(record);
      }
    }
    const measurements = target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ?
      formControlMeasurements(target, insertedText, password) :
      editableMeasurements(target, insertedText);
    measurements.forEach((measurement, index) => {
      animateMeasurement(target, measurement, index * 24, password);
    });
  }

  function fallbackInsertedText(target) {
    if (target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement) {
      const caret = safeSelectionStart(target) ?? target.value.length;
      return graphemes(target.value.slice(0, caret)).at(-1) || '';
    }
    const selection = target.ownerDocument.getSelection();
    if (selection?.isCollapsed &&
        selection.focusNode?.nodeType === Node.TEXT_NODE) {
      const text = selection.focusNode.data.slice(0, selection.focusOffset);
      return graphemes(text).at(-1) || '';
    }
    return '';
  }

  function scheduleAnimation(target, text, password) {
    queueMicrotask(() => {
      if (password) {
        animateInsertion(target, '', true);
        return;
      }
      animateInsertion(target, text || fallbackInsertedText(target), false);
    });
  }

  document.addEventListener('beforeinput', event => {
    const target = editableEventTarget(event);
    if (!target || !isInsertion(event.inputType) ||
        event.isComposing || composingEditors.has(target)) {
      return;
    }
    if (isPassword(target)) {
      // The marker deliberately contains no text from the password event.
      pendingInsertions.set(target, {password: true});
      return;
    }
    let text = typeof event.data === 'string' ? event.data : '';
    if (!text && event.dataTransfer) {
      text = event.dataTransfer.getData('text/plain');
    }
    pendingInsertions.set(
        target, {password: false, text: capInsertionText(text)});
  }, true);

  document.addEventListener('input', event => {
    const target = editableEventTarget(event);
    if (!target) {
      return;
    }
    if (suppressNextInput.has(target)) {
      suppressNextInput.delete(target);
      pendingInsertions.delete(target);
      return;
    }
    if (event.isComposing || composingEditors.has(target) ||
        !isInsertion(event.inputType)) {
      return;
    }
    const pending = pendingInsertions.get(target);
    pendingInsertions.delete(target);
    const password = isPassword(target);
    scheduleAnimation(target, password ? '' : pending?.text || '', password);
  }, true);

  document.addEventListener('compositionstart', event => {
    const target = editableEventTarget(event);
    if (target) {
      composingEditors.add(target);
      pendingInsertions.delete(target);
    }
  }, true);

  document.addEventListener('compositionend', event => {
    const target = editableEventTarget(event);
    if (!target) {
      return;
    }
    composingEditors.delete(target);
    suppressNextInput.add(target);
    setTimeout(() => suppressNextInput.delete(target), 0);
    if (isPassword(target)) {
      scheduleAnimation(target, '', true);
      return;
    }
    const text = typeof event.data === 'string' ?
      capInsertionText(event.data) : '';
    scheduleAnimation(target, text, false);
  }, true);

  document.addEventListener('focusout', event => {
    const target = editableEventTarget(event);
    if (target) {
      pendingInsertions.delete(target);
      composingEditors.delete(target);
      suppressNextInput.delete(target);
    }
  }, true);

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === 'local' && changes[STORAGE_KEY]) {
      setPreference(changes[STORAGE_KEY].newValue === true);
    }
  });
  chrome.storage.local.get([STORAGE_KEY], values => {
    setPreference(values[STORAGE_KEY] === true);
  });
  chrome.runtime.sendMessage(
      {type: 'focus-text-motion.get-state'}, response => {
        void chrome.runtime.lastError;
        if (typeof response?.enabled === 'boolean') {
          setPreference(response.enabled);
        }
      });

  reducedMotion.addEventListener('change', () => {
    if (!motionAllowed()) {
      cancelAnimations();
    }
  });
  forcedColors.addEventListener('change', () => {
    if (!motionAllowed()) {
      cancelAnimations();
    }
  });
  addEventListener('scroll', cancelAnimations, {capture: true, passive: true});
  addEventListener('resize', cancelAnimations, {passive: true});
  globalThis.visualViewport?.addEventListener(
      'resize', cancelAnimations, {passive: true});
  globalThis.visualViewport?.addEventListener(
      'scroll', cancelAnimations, {passive: true});
})();
