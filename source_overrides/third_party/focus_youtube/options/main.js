// Copyright 2026 The Focus Browser Authors

'use strict';

(() => {
  const schema = globalThis.FocusYoutubeSettings;
  const storage = chrome.storage.local;
  const app = document.getElementById('app');
  const settingsList = document.getElementById('settingsList');
  const entries = new Map(
      schema.sections.flatMap(section => section.options)
          .map(entry => [entry.id, entry]));
  let state = { ...schema.defaults };
  let activeOnly = false;
  let transferMode = 'export';
  let guardMode = null;
  let guardCode = '';
  let guardDeadline = 0;
  let guardTimer = null;
  let initialized = false;
  let sessionUnlocked = false;
  let toastTimer = null;
  app.inert = true;
  app.setAttribute('aria-busy', 'true');

  function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 2200);
  }

  function sendAutomationMessage(type, changes = undefined) {
    chrome.runtime.sendMessage({ type, ...(changes ? { changes } : {}) },
        response => {
      const error = chrome.runtime.lastError;
      if (error || response?.ok !== true) {
        storage.get(schema.automationIds, current => {
          Object.assign(state, current);
          showToast('Не удалось изменить автоматические настройки');
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

  function createSwitch(id, labelText) {
    const label = document.createElement('label');
    label.className = 'switch';
    const input = document.createElement('input');
    input.id = 'setting-' + id;
    input.type = 'checkbox';
    input.dataset.settingId = id;
    input.setAttribute('role', 'switch');
    input.setAttribute('aria-label', labelText);
    const track = document.createElement('span');
    track.setAttribute('aria-hidden', 'true');
    label.append(input, track);
    return label;
  }

  function buildSettings() {
    const nav = document.getElementById('categoryNav');
    schema.sections.forEach((section, sectionIndex) => {
      const navButton = document.createElement('button');
      navButton.type = 'button';
      navButton.className = 'categoryButton';
      navButton.textContent = section.title;
      navButton.dataset.target = section.id;
      navButton.addEventListener('click', () => {
        document.getElementById('section-' + section.id)
            .scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      nav.appendChild(navButton);

      const sectionNode = document.createElement('section');
      sectionNode.className = 'settingsSection';
      sectionNode.id = 'section-' + section.id;
      sectionNode.dataset.section = section.id;

      const header = document.createElement('div');
      header.className = 'sectionTitle';
      const index = document.createElement('span');
      index.className = 'index';
      index.textContent = String(sectionIndex + 1).padStart(2, '0');
      const copy = document.createElement('div');
      const title = document.createElement('h2');
      title.textContent = section.title;
      const description = document.createElement('p');
      description.textContent = section.description;
      copy.append(title, description);
      header.append(index, copy);
      sectionNode.appendChild(header);

      for (const entry of section.options) {
        const row = document.createElement('div');
        row.className = 'optionRow';
        row.dataset.settingRow = entry.id;
        row.dataset.search = (
          section.title + ' ' + entry.label + ' ' + entry.description
        ).toLocaleLowerCase('ru');

        const optionCopy = document.createElement('div');
        optionCopy.className = 'optionCopy';
        const label = document.createElement('label');
        label.htmlFor = 'setting-' + entry.id;
        label.textContent = entry.label;
        optionCopy.appendChild(label);
        if (entry.description) {
          const hint = document.createElement('small');
          hint.textContent = entry.description;
          optionCopy.appendChild(hint);
        }
        const toggle = createSwitch(entry.id, entry.label);
        toggle.querySelector('input').addEventListener('change', event => {
          const changes = schema.applyEffects(
              state, entry.id, event.currentTarget.checked);
          writeChanges(changes);
        });
        row.append(optionCopy, toggle);
        sectionNode.appendChild(row);
      }
      settingsList.appendChild(sectionNode);
    });
  }

  function writeChanges(changes) {
    if (!initialized || !sessionUnlocked) {
      showToast('Сначала разблокируйте настройки');
      return false;
    }
    const valid = {};
    for (const [id, value] of Object.entries(changes)) {
      if (schema.isValidValue(id, value)) valid[id] = value;
    }
    Object.assign(state, valid);
    if (Object.keys(valid).length) persistChanges(valid);
    render();
    return Object.keys(valid).length > 0;
  }

  function render() {
    document.documentElement.dataset.theme =
        state.dark_mode === false ? 'light' : 'dark';
    const master = document.getElementById('global_enable');
    master.checked = state.global_enable === true;
    document.getElementById('masterLabel').textContent = master.checked ?
      'Защита включена' : 'Защита выключена';

    for (const id of schema.behaviorIds) {
      const input = document.getElementById('setting-' + id);
      if (input) input.checked = state[id] === true;
    }
    const count = schema.behaviorIds.reduce(
        (sum, id) => sum + Number(state[id] === true), 0);
    document.getElementById('activeCount').textContent = String(count);
    document.getElementById('totalCount').textContent =
        String(schema.behaviorIds.length);

    document.getElementById('schedule').checked = state.schedule === true;
    document.getElementById('scheduleTimes').value = state.scheduleTimes;
    const days = new Set(String(state.scheduleDays).split(','));
    document.querySelectorAll('.days input').forEach(input => {
      input.checked = days.has(input.value);
    });

    const passwordEnabled = state.password === true &&
        /^sha256\$[^$]+\$[0-9a-f]{64}$/i.test(
            String(state.hashed_password));
    document.getElementById('passwordAction').textContent = passwordEnabled ?
      'Убрать пароль' : 'Установить пароль';
    document.getElementById('passwordConfirm').hidden = passwordEnabled;
    document.getElementById('passwordInput').placeholder = passwordEnabled ?
      'Текущий пароль' : 'Новый пароль';
    applyFilter();
  }

  function applyFilter() {
    const query = document.getElementById('settingsSearch')
        .value.trim().toLocaleLowerCase('ru');
    document.querySelectorAll('[data-setting-row]').forEach(row => {
      const id = row.dataset.settingRow;
      const matchesText = !query || row.dataset.search.includes(query);
      const matchesActive = !activeOnly || state[id] === true;
      row.classList.toggle('filteredOut', !matchesText || !matchesActive);
    });
    document.querySelectorAll('[data-section]').forEach(section => {
      const visible = [...section.querySelectorAll('[data-setting-row]')]
          .some(row => !row.classList.contains('filteredOut'));
      section.classList.toggle('filteredOut', !visible);
    });
    document.getElementById('showActive').hidden = activeOnly;
    document.getElementById('showAll').hidden = !activeOnly;
  }

  function initializeStorage(callback) {
    storage.get(null, saved => {
      const migration = schema.createMigration(saved);
      state = migration.settings;
      if (state.password === true &&
          !/^sha256\$[^$]+\$[0-9a-f]{64}$/i.test(
              String(state.hashed_password))) {
        state.password = false;
        state.hashed_password = '';
        migration.patch.password = false;
        migration.patch.hashed_password = '';
      }
      if (Object.keys(migration.patch).length) {
        persistMigration(migration.patch);
      }
      if (migration.removeKeys.length) {
        storage.remove(migration.removeKeys);
      }
      callback();
    });
  }

  function encodeBase64(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = '';
    bytes.forEach(byte => {
      binary += String.fromCharCode(byte);
    });
    return btoa(binary).replaceAll('+', '-').replaceAll('/', '_')
        .replace(/=+$/, '');
  }

  function decodeBase64(value) {
    const normalized = value.replaceAll('-', '+').replaceAll('_', '/');
    const binary = atob(normalized +
        '='.repeat((4 - normalized.length % 4) % 4));
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  }

  function exportValue() {
    const excluded = new Set([
      'password', 'hashed_password', 'focus_youtube_schema_version',
    ]);
    const settings = {};
    for (const [id, value] of Object.entries(state)) {
      if (!excluded.has(id) && schema.isValidValue(id, value)) {
        settings[id] = value;
      }
    }
    return 'focusyoutube_settings_v2_' +
        encodeBase64(JSON.stringify({ version: 2, settings }));
  }

  function parseLegacy(value) {
    const prefix = 'focusyoutube_settings_';
    if (!value.startsWith(prefix)) throw new Error('Неверный формат');
    const body = value.slice(prefix.length);
    const result = {};
    for (const pair of body.split(',')) {
      const separator = pair.indexOf(':');
      if (separator < 1) throw new Error('Повреждённые настройки');
      const shortId = pair.slice(0, separator);
      const id = schema.shortIdToId[shortId];
      if (!id) continue;
      let raw = pair.slice(separator + 1);
      let parsed;
      if (raw === 't' || raw === 'f') {
        parsed = raw === 't';
      } else {
        parsed = raw.replaceAll('<d1>', ':').replaceAll('<d2>', ',');
      }
      if (schema.isValidValue(id, parsed) &&
          id !== 'password' && id !== 'hashed_password') {
        result[id] = parsed;
      }
    }
    return result;
  }

  function importValue(value) {
    const v2Prefix = 'focusyoutube_settings_v2_';
    let candidate;
    if (value.startsWith(v2Prefix)) {
      const payload = JSON.parse(decodeBase64(value.slice(v2Prefix.length)));
      if (payload.version !== 2 ||
          !payload.settings ||
          typeof payload.settings !== 'object' ||
          Array.isArray(payload.settings)) {
        throw new Error('Неподдерживаемая версия');
      }
      candidate = payload.settings;
    } else {
      candidate = parseLegacy(value);
    }

    const changes = {};
    for (const [id, settingValue] of Object.entries(candidate)) {
      if (id === 'password' || id === 'hashed_password') continue;
      if (!schema.isValidValue(id, settingValue)) {
        throw new Error('Неверное значение: ' + id);
      }
      changes[id] = settingValue;
    }
    if (!Object.keys(changes).length) {
      throw new Error('Нет подходящих настроек');
    }
    const redirectIds = [
      'redirect_to_subs',
      'redirect_to_wl',
      'redirect_to_library',
      'redirect_off',
    ];
    const resultingRedirects = redirectIds.filter(id =>
      (Object.prototype.hasOwnProperty.call(changes, id) ?
        changes[id] : state[id]) === true);
    if (resultingRedirects.length > 1) {
      throw new Error('В импорте можно выбрать только один вариант перехода');
    }
    return changes;
  }

  async function passwordDigest(password, salt) {
    const data = new TextEncoder().encode(salt + '\u0000' + password);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return [...new Uint8Array(digest)]
        .map(byte => byte.toString(16).padStart(2, '0')).join('');
  }

  async function createPasswordValue(password) {
    const salt = focusGenerateCode(16);
    return 'sha256$' + salt + '$' + await passwordDigest(password, salt);
  }

  async function verifyPassword(password) {
    const parts = String(state.hashed_password).split('$');
    if (parts.length !== 3 || parts[0] !== 'sha256') return false;
    return await passwordDigest(password, parts[1]) === parts[2];
  }

  function unlockGuard() {
    if (guardTimer !== null) {
      clearInterval(guardTimer);
      guardTimer = null;
    }
    guardDeadline = 0;
    guardMode = null;
    sessionUnlocked = true;
    document.getElementById('guard').hidden = true;
    app.inert = false;
    document.getElementById('settingsSearch').focus();
  }

  function showGuard(mode) {
    if (guardTimer !== null) {
      clearInterval(guardTimer);
      guardTimer = null;
    }
    guardMode = mode;
    guardDeadline = 0;
    sessionUnlocked = false;
    app.inert = true;
    const guard = document.getElementById('guard');
    const message = document.getElementById('guardMessage');
    const code = document.getElementById('guardCode');
    const input = document.getElementById('guardInput');
    const button = document.getElementById('guardButton');
    code.hidden = true;
    input.hidden = true;
    input.value = '';
    input.type = 'text';
    button.disabled = false;
    button.textContent = 'Продолжить';

    if (mode === 'password') {
      message.textContent = 'Введите локальный пароль FocusYoutube.';
      input.type = 'password';
      input.hidden = false;
    } else if (mode === 'code') {
      guardCode = focusGenerateCode(8);
      message.textContent = 'Перепечатайте код, чтобы открыть настройки.';
      code.textContent = guardCode;
      code.hidden = false;
      input.hidden = false;
    } else {
      message.textContent =
          'Небольшая пауза помогает не менять правила импульсивно.';
      button.disabled = true;
      guardDeadline = Date.now() + 10_000;
      const updateTimer = () => {
        const remaining = Math.max(
            0, Math.ceil((guardDeadline - Date.now()) / 1000));
        if (remaining <= 0) {
          clearInterval(guardTimer);
          guardTimer = null;
          button.disabled = false;
          button.textContent = 'Продолжить';
        } else {
          button.textContent = 'Подождите ' + remaining + ' с';
        }
      };
      updateTimer();
      guardTimer = setInterval(updateTimer, 250);
    }
    guard.hidden = false;
    if (!input.hidden) input.focus();
  }

  function applyInitialGuard() {
    if (state.password &&
        /^sha256\$[^$]+\$[0-9a-f]{64}$/i.test(
            String(state.hashed_password))) {
      showGuard('password');
      return true;
    } else if (state.menu_timer) {
      showGuard('timer');
      return true;
    } else if (state.lock_code) {
      showGuard('code');
      return true;
    }
    return false;
  }

  buildSettings();

  document.getElementById('global_enable').addEventListener('change', event => {
    writeChanges({
      global_enable: event.currentTarget.checked,
      nextTimedChange: false,
      nextTimedValue: true,
    });
  });

  document.getElementById('themeToggle').addEventListener('click', () => {
    writeChanges({ dark_mode: state.dark_mode === false });
  });

  document.getElementById('settingsSearch').addEventListener('input', applyFilter);
  document.getElementById('showActive').addEventListener('click', () => {
    activeOnly = true;
    applyFilter();
  });
  document.getElementById('showAll').addEventListener('click', () => {
    activeOnly = false;
    applyFilter();
  });

  document.getElementById('schedule').addEventListener('change', event => {
    writeChanges({ schedule: event.currentTarget.checked });
  });

  document.getElementById('scheduleTimes').addEventListener('change', event => {
    const value = event.currentTarget.value.trim();
    const valid = focusScheduleIsValid(value);
    event.currentTarget.setAttribute('aria-invalid', String(!valid));
    if (valid) {
      writeChanges({ scheduleTimes: value });
    } else {
      showToast('Проверьте формат интервалов');
    }
  });

  document.querySelectorAll('.days input').forEach(input => {
    input.addEventListener('change', () => {
      const days = [...document.querySelectorAll('.days input:checked')]
          .map(day => day.value).join(',');
      writeChanges({ scheduleDays: days });
    });
  });

  document.getElementById('pause25').addEventListener('click', () => {
    writeChanges({
      global_enable: false,
      nextTimedChange: Date.now() + 25 * 60 * 1000,
      nextTimedValue: true,
    });
    showToast('FocusYoutube приостановлен на 25 минут');
  });

  document.getElementById('resumeNow').addEventListener('click', () => {
    writeChanges({
      global_enable: true,
      nextTimedChange: false,
      nextTimedValue: true,
    });
    showToast('FocusYoutube включён');
  });

  document.getElementById('resetSettings').addEventListener('click', () => {
    if (!confirm('Выключить все 93 фильтра?')) return;
    writeChanges({
      ...schema.behaviorDefaults,
      global_enable: true,
      schedule: false,
      nextTimedChange: false,
      nextTimedValue: true,
    });
    showToast('Все фильтры выключены');
  });

  const transferDialog = document.getElementById('transferDialog');
  document.getElementById('exportSettings').addEventListener('click', () => {
    transferMode = 'export';
    document.getElementById('transferTitle').textContent = 'Экспорт настроек';
    document.getElementById('transferHelp').textContent =
        'Скопируйте строку и сохраните её в надёжном месте.';
    document.getElementById('transferValue').value = exportValue();
    document.getElementById('transferSubmit').textContent = 'Копировать';
    transferDialog.showModal();
    document.getElementById('transferValue').select();
  });

  document.getElementById('importSettings').addEventListener('click', () => {
    transferMode = 'import';
    document.getElementById('transferTitle').textContent = 'Импорт настроек';
    document.getElementById('transferHelp').textContent =
        'Вставьте строку FocusYoutube. Пароль никогда не импортируется.';
    document.getElementById('transferValue').value = '';
    document.getElementById('transferSubmit').textContent = 'Импортировать';
    transferDialog.showModal();
  });

  document.getElementById('transferSubmit').addEventListener('click', async () => {
    const value = document.getElementById('transferValue').value.trim();
    try {
      if (transferMode === 'export') {
        await navigator.clipboard.writeText(value);
        showToast('Настройки скопированы');
      } else {
        writeChanges(importValue(value));
        showToast('Настройки импортированы');
      }
      transferDialog.close();
    } catch (error) {
      showToast(error.message || 'Не удалось перенести настройки');
    }
  });

  document.getElementById('passwordAction').addEventListener('click', async () => {
    const password = document.getElementById('passwordInput').value;
    const confirmValue = document.getElementById('passwordConfirm').value;
    const enabled = state.password === true &&
        /^sha256\$[^$]+\$[0-9a-f]{64}$/i.test(
            String(state.hashed_password));

    if (enabled) {
      if (!await verifyPassword(password)) {
        showToast('Неверный пароль');
        return;
      }
      writeChanges({ password: false, hashed_password: '' });
      showToast('Пароль удалён');
    } else {
      if (password.length < 6) {
        showToast('Минимум 6 символов');
        return;
      }
      if (password !== confirmValue) {
        showToast('Пароли не совпадают');
        return;
      }
      writeChanges({
        password: true,
        hashed_password: await createPasswordValue(password),
      });
      showToast('Пароль установлен');
    }
    document.getElementById('passwordInput').value = '';
    document.getElementById('passwordConfirm').value = '';
  });

  document.getElementById('guardForm').addEventListener('submit', async event => {
    event.preventDefault();
    const input = document.getElementById('guardInput');
    if (guardMode === 'timer') {
      if (Date.now() < guardDeadline) {
        showToast('Дождитесь окончания таймера');
        return;
      }
      unlockGuard();
    } else if (guardMode === 'code') {
      if (input.value.trim().toUpperCase() === guardCode) {
        unlockGuard();
      } else {
        showToast('Код не совпадает');
      }
    } else if (guardMode === 'password') {
      if (await verifyPassword(input.value)) {
        unlockGuard();
      } else {
        showToast('Неверный пароль');
      }
    }
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'local') return;
    for (const [id, change] of Object.entries(changes)) {
      if (id in state) state[id] = change.newValue;
    }
    render();
  });

  initializeStorage(() => {
    initialized = true;
    app.removeAttribute('aria-busy');
    render();
    if (!applyInitialGuard()) {
      sessionUnlocked = true;
      app.inert = false;
    }
  });
})();
