// Copyright 2026 The Focus Browser Authors

'use strict';

(() => {
  if (new URLSearchParams(location.search).get('focusMotion') === '0') {
    document.documentElement.dataset.motion = 'off';
  }
  const schema = globalThis.FocusYoutubeSettings;
  const storage = chrome.storage.local;
  const popup = document.querySelector('main.popup');
  const VISIBLE_GROUPS = Object.freeze([
    {
      title: 'Лента и рекомендации',
      ids: Object.freeze([
        'remove_homepage',
        'remove_sidebar',
        'remove_end_of_video',
        'remove_all_shorts',
        'search_engine_mode',
        'disable_play_on_hover',
      ]),
    },
    {
      title: 'Плеер',
      ids: Object.freeze([
        'disable_autoplay',
        'auto_skip_ads',
        'remove_info_cards',
        'remove_overlay_suggestions',
        'remove_play_next_button',
        'remove_chat',
        'enable_theater',
        'disable_ambient_mode',
      ]),
    },
    {
      title: 'Интерфейс',
      ids: Object.freeze([
        'remove_comments',
        'remove_left_nav_bar',
        'remove_notif_bell',
        'remove_menu_buttons',
        'grayscale_mode',
      ]),
    },
    {
      title: 'Поиск и каналы',
      ids: Object.freeze([
        'remove_search_suggestions',
        'remove_search_promoted',
        'remove_shorts_results',
        'disable_channel_autoplay',
        'remove_channel_for_you',
      ]),
    },
  ]);
  const visibleIds = Object.freeze(
      VISIBLE_GROUPS.flatMap(group => group.ids));
  const entries = new Map(
      schema.sections.flatMap(section => section.options)
          .map(entry => [entry.id, entry]));
  let state = { ...schema.defaults };
  let initialized = false;

  if (visibleIds.length !== 24 ||
      new Set(visibleIds).size !== visibleIds.length ||
      visibleIds.some(id => !entries.has(id))) {
    throw new Error('Некорректный набор основных функций FocusYoutube');
  }

  popup.inert = true;
  popup.setAttribute('aria-busy', 'true');

  function announce(message) {
    document.getElementById('statusMessage').textContent = message;
  }

  function sendAutomationMessage(type, changes = undefined) {
    chrome.runtime.sendMessage({ type, ...(changes ? { changes } : {}) },
        response => {
      const error = chrome.runtime.lastError;
      if (error || response?.ok !== true) {
        storage.get(schema.automationIds, current => {
          Object.assign(state, current);
          announce('Не удалось изменить состояние FocusYoutube');
          render();
        });
        return;
      }
      Object.assign(state, response.settings);
      render();
    });
  }

  function persistChanges(changes) {
    const automation = {};
    const regular = {};
    for (const [id, value] of Object.entries(changes)) {
      (schema.automationIds.includes(id) ? automation : regular)[id] = value;
    }
    if (Object.keys(regular).length) storage.set(regular);
    if (Object.keys(automation).length) {
      sendAutomationMessage('focusyoutube.updateAutomation', automation);
    }
  }

  function persistMigration(changes) {
    const regular = Object.fromEntries(Object.entries(changes)
        .filter(([id]) => !schema.automationIds.includes(id)));
    if (Object.keys(regular).length) storage.set(regular);
    if (Object.keys(changes).some(id => schema.automationIds.includes(id))) {
      sendAutomationMessage('focusyoutube.reconcileAutomation');
    }
  }

  function createSwitch(id) {
    const entry = entries.get(id);
    const row = document.createElement('div');
    row.className = 'controlRow';

    const copy = document.createElement('label');
    copy.className = 'controlCopy';
    copy.htmlFor = id;
    const title = document.createElement('strong');
    title.textContent = entry.label;
    copy.appendChild(title);
    if (entry.description) {
      const hint = document.createElement('small');
      hint.textContent = entry.description;
      copy.appendChild(hint);
    }

    const switchLabel = document.createElement('label');
    switchLabel.className = 'switch';
    const input = document.createElement('input');
    input.id = id;
    input.type = 'checkbox';
    input.setAttribute('role', 'switch');
    input.setAttribute('aria-label', entry.label);
    const track = document.createElement('span');
    track.setAttribute('aria-hidden', 'true');
    switchLabel.append(input, track);
    row.append(copy, switchLabel);

    input.addEventListener('change', () => {
      if (!initialized) {
        input.checked = state[id] === true;
        return;
      }
      const changes = schema.applyEffects(state, id, input.checked);
      Object.assign(state, changes);
      persistChanges(changes);
      announce(entry.label + (input.checked ? ': включено' : ': выключено'));
      render();
    });
    return row;
  }

  function buildControls() {
    const container = document.getElementById('controls');
    for (const group of VISIBLE_GROUPS) {
      const section = document.createElement('section');
      section.className = 'controlGroup';
      const heading = document.createElement('h3');
      heading.className = 'groupTitle';
      heading.textContent = group.title;
      section.appendChild(heading);
      for (const id of group.ids) section.appendChild(createSwitch(id));
      container.appendChild(section);
    }
  }

  function visibleActiveCount() {
    return visibleIds.reduce(
        (count, id) => count + Number(state[id] === true), 0);
  }

  function behaviorActiveCount() {
    return schema.behaviorIds.reduce(
        (count, id) => count + Number(state[id] === true), 0);
  }

  function render() {
    const master = document.getElementById('global_enable');
    master.checked = state.global_enable === true;
    master.disabled = !initialized;
    document.getElementById('masterState').textContent = master.checked ?
      'FocusYoutube включён' : 'FocusYoutube выключен';
    document.getElementById('masterHint').textContent = master.checked ?
      'Включайте только то, что действительно помогает.' :
      'Выбранные настройки сохранены, но временно не применяются.';

    for (const id of visibleIds) {
      const input = document.getElementById(id);
      input.checked = state[id] === true;
      input.disabled = !initialized;
    }
    document.getElementById('enabledCount').textContent =
        'Включено ' + visibleActiveCount() + ' из ' + visibleIds.length;
    document.getElementById('resetAll').disabled =
        !initialized || behaviorActiveCount() === 0;
  }

  function initialize() {
    storage.get(null, saved => {
      const migration = schema.createMigration(saved);
      state = migration.settings;
      if (Object.keys(migration.patch).length) {
        persistMigration(migration.patch);
      }
      if (migration.removeKeys.length) storage.remove(migration.removeKeys);
      initialized = true;
      render();
      popup.removeAttribute('aria-busy');
      popup.inert = false;
    });
  }

  buildControls();

  document.getElementById('global_enable').addEventListener('change', event => {
    if (!initialized) {
      event.currentTarget.checked = state.global_enable === true;
      return;
    }
    const changes = {
      global_enable: event.currentTarget.checked,
      nextTimedChange: false,
      nextTimedValue: true,
    };
    Object.assign(state, changes);
    persistChanges(changes);
    announce(changes.global_enable ?
      'FocusYoutube включён' : 'FocusYoutube выключен');
    render();
  });

  document.getElementById('resetAll').addEventListener('click', () => {
    if (!initialized) return;
    const changes = { ...schema.behaviorDefaults };
    Object.assign(state, changes);
    persistChanges(changes);
    announce('Все фильтры выключены');
    render();
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local') return;
    for (const [id, change] of Object.entries(changes)) {
      if (id in state) state[id] = change.newValue;
    }
    render();
  });

  initialize();
})();
