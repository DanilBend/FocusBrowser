// Copyright 2026 The Focus Browser Authors
// Dedicated static QA for the complete built-in FocusYoutube component.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const activeSourceRoot = process.env.FOCUS_ACTIVE_SOURCE_ROOT ?
  path.resolve(process.env.FOCUS_ACTIVE_SOURCE_ROOT) :
  path.join(repoRoot, 'build', 'src');
const activeRoot = path.join(
    activeSourceRoot, 'third_party', 'focus_youtube');
const overrideRoot = path.join(repoRoot, 'source_overrides', 'third_party', 'focus_youtube');

const read = (root, relative) =>
  fs.readFileSync(path.join(root, relative), 'utf8');

function readMirroredFile(file) {
  const bytes = fs.readFileSync(file);
  if (!/\.(?:cc|css|gn|h|html|js|json|py|svg|txt|chromium)$/i.test(file)) {
    return bytes;
  }
  return Buffer.from(bytes.toString('utf8').replace(/\r\n?/g, '\n'), 'utf8');
}

function walk(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap(entry => {
    const absolute = path.join(root, entry.name);
    return entry.isDirectory() ? walk(absolute) : [absolute];
  });
}

const overrideFiles = walk(overrideRoot);
for (const overrideFile of overrideFiles) {
  const relative = path.relative(overrideRoot, overrideFile);
  const activeFile = path.join(activeRoot, relative);
  assert.ok(fs.existsSync(activeFile), 'Нет active-файла: ' + relative);
  assert.deepEqual(
      readMirroredFile(overrideFile),
      readMirroredFile(activeFile),
      'Active и source_overrides различаются: ' + relative);
}

const utf8Decoder = new TextDecoder('utf-8', { fatal: true });
for (const file of [...walk(activeRoot), ...overrideFiles]) {
  if (!/\.(?:js|css|html|json|py|gn|txt|chromium)$/i.test(file)) continue;
  assert.doesNotThrow(
      () => utf8Decoder.decode(fs.readFileSync(file)),
      'Файл не является корректным UTF-8: ' + file);
}

const manifest = JSON.parse(read(activeRoot, 'manifest.json'));
assert.equal(manifest.name, 'FocusYoutube');
assert.equal(manifest.author, 'Focus Browser');
assert.equal(manifest.version, '1.6.9.1');
assert.deepEqual(manifest.permissions, ['storage', 'alarms']);
assert.deepEqual(manifest.host_permissions, [
  'https://youtube.com/*',
  'https://*.youtube.com/*',
]);
assert.equal(manifest.options_ui, undefined,
             'Отдельная страница настроек не должна быть доступна');
assert.equal(manifest.background.service_worker, 'background/events.js');
assert.equal(manifest.web_accessible_resources, undefined);
assert.deepEqual(manifest.content_scripts[0].matches, [
  'https://youtube.com/*',
  'https://*.youtube.com/*',
]);
assert.deepEqual(manifest.content_scripts[0].js, [
  'shared/utils.js',
  'shared/main.js',
  'content-script/main.js',
]);

const extensionDigest = crypto.createHash('sha256')
    .update(Buffer.from(manifest.key, 'base64'))
    .digest()
    .subarray(0, 16);
const extensionId = [...extensionDigest]
    .flatMap(byte => [byte >> 4, byte & 15])
    .map(nibble => String.fromCharCode(97 + nibble))
    .join('');
assert.equal(extensionId, 'jafokmemnknjknbdiklabcnhlpheefbm');

const generator = read(activeRoot, 'generate_file_list.py');
const packaged = new Set([
  ...generator.matchAll(/^\s*"([^"\r\n]+)",?\s*$/gm),
].map(match => match[1]));
assert.equal(packaged.size, 17);
for (const relative of [
  'options/main.html',
  'options/main.js',
  'options/options.css',
]) {
  assert.equal(packaged.has(relative), false,
               'Options page не должна попадать в пакет: ' + relative);
}

function addManifestResource(resources, value) {
  if (typeof value === 'string') resources.add(value.replace(/^\//, ''));
}
const manifestResources = new Set(['manifest.json']);
Object.values(manifest.icons).forEach(value =>
  addManifestResource(manifestResources, value));
Object.values(manifest.action.default_icon).forEach(value =>
  addManifestResource(manifestResources, value));
addManifestResource(manifestResources, manifest.action.default_popup);
addManifestResource(manifestResources, manifest.background.service_worker);
for (const script of manifest.content_scripts) {
  script.js.forEach(value => addManifestResource(manifestResources, value));
  script.css.forEach(value => addManifestResource(manifestResources, value));
}
for (const relative of manifestResources) {
  assert.ok(packaged.has(relative), 'Manifest-ресурс не упакован: ' + relative);
}
for (const relative of packaged) {
  assert.ok(fs.existsSync(path.join(activeRoot, relative)),
            'Упакованный файл отсутствует: ' + relative);
}
for (const size of [16, 32, 48, 128]) {
  const enabledIcon = fs.readFileSync(
      path.join(activeRoot, 'images', `icon-${size}.png`));
  const disabledIcon = fs.readFileSync(
      path.join(activeRoot, 'images', `icon-off-${size}.png`));
  assert.notDeepEqual(
      enabledIcon, disabledIcon,
      `Включённая и выключенная иконки ${size}px должны визуально различаться`);
}

const forbiddenRuntime = new RegExp([
  'mixpanel',
  'stripe',
  'paypal',
  'premium',
  'license_token',
  'session_token',
  'user_email',
  'sendMagicLink',
  'createCheckoutSession',
  'XMLHttpRequest',
  '\\bfetch\\s*\\(',
].join('|'), 'i');
for (const relative of packaged) {
  if (!/\.(?:js|html|css|json)$/.test(relative)) continue;
  const source = read(activeRoot, relative);
  const auditableSource = relative === 'shared/main.js' ?
    source.replace(/const FORBIDDEN_STORAGE_KEYS[\s\S]*?\]\);/, '') : source;
  assert.doesNotMatch(auditableSource, forbiddenRuntime,
                      'Запрещённый remote/account код: ' + relative);
}
for (const stale of [
  'shared/auth.js',
  'shared/license.js',
  'shared/analytics.js',
  'shared/mixpanel.js',
  'options/settings-menu.js',
  'options/feedback/feedback.js',
  'options/donors/donors.js',
  'js/focus-youtube-page.js',
]) {
  assert.equal(fs.existsSync(path.join(activeRoot, stale)), false,
               'Остался старый runtime: ' + stale);
  assert.equal(fs.existsSync(path.join(overrideRoot, stale)), false,
               'Остался старый override: ' + stale);
}

const schemaContext = {};
schemaContext.globalThis = schemaContext;
vm.createContext(schemaContext);
vm.runInContext(read(activeRoot, 'shared/main.js'), schemaContext, {
  filename: 'shared/main.js',
});
const schema = schemaContext.FocusYoutubeSettings;
const ids = [...schema.behaviorIds];
const expectedNativeFeatureKeys = [
  'remove_homepage',
  'remove_entire_sidebar',
  'remove_sidebar',
  'remove_chat',
  'remove_playlist_panel',
  'remove_end_of_video',
  'remove_info_cards',
  'remove_comments',
  'remove_comment_profiles',
  'remove_mixes',
  'remove_merch_shelves',
  'remove_video_metadata',
  'remove_menu_buttons',
  'remove_channel_owner',
  'remove_vid_description',
  'remove_top_header',
  'remove_notif_bell',
  'remove_extra_results',
  'remove_explore_link',
  'remove_explore_section',
  'remove_trending_page',
  'remove_more_section',
  'remove_all_shorts',
  'remove_subscriptions_link',
  'remove_sub_section',
  'remove_subscriptions_page',
  'redirect_to_subs',
  'disable_autoplay',
  'disable_annotations',
];
assert.equal(ids.length, 93);
assert.equal(new Set(ids).size, 93);
assert.equal(Object.keys(schema.behaviorDefaults).length, 93);
assert.ok(Object.values(schema.behaviorDefaults).every(value => value === false));
assert.deepEqual([...schema.nativeBehaviorIds], expectedNativeFeatureKeys);
assert.equal(new Set(schema.nativeBehaviorIds).size, 29);
assert.ok(schema.nativeBehaviorIds.every(id => ids.includes(id)));
assert.equal(schema.defaults.global_enable, true);
assert.equal(schema.defaults.dark_mode, true);
assert.equal(schema.defaults.schedule, false);
assert.equal(schema.defaults.scheduleTimes, '09:00-17:00');
assert.equal(schema.defaults.password, false);
assert.equal(schema.defaults.hashed_password, '');
assert.equal(schema.defaults.focus_youtube_schema_version, 4);
assert.ok(ids.every(id => schema.idToShortId[id] !== undefined),
          'Для экспорта отсутствует short ID');
assert.equal(ids.filter(id => id.startsWith('hide_')).length, 0);

const focusAdditions = [
  'remove_subscriptions_page',
  'remove_trending_page',
  'remove_channel_owner',
  'remove_donation_shelf',
  'remove_top_header',
  'remove_merch_shelves',
  'remove_video_metadata',
  'remove_mixes',
  'remove_playlist_panel',
];
assert.ok(focusAdditions.every(id => ids.includes(id)));
assert.equal(ids.filter(id => !focusAdditions.includes(id)).length, 84);

const oldValues = {
  yt_on: false,
  popup_settings: { dark_mode: false },
};
for (const legacyId of Object.keys(schema.legacyMappings)) {
  oldValues[legacyId] = true;
}
const migrated = schema.createMigration(oldValues);
assert.equal(migrated.settings.global_enable, false);
assert.equal(migrated.settings.dark_mode, false);
for (const targetIds of Object.values(schema.legacyMappings)) {
  for (const targetId of targetIds) {
    assert.equal(
        migrated.settings[targetId],
        expectedNativeFeatureKeys.includes(targetId),
        'Schema v4 неверно обработала legacy key ' + targetId);
  }
}
assert.equal(migrated.settings.schedule, false);
assert.equal(migrated.settings.nextTimedChange, false);
assert.equal(migrated.settings.nextTimedValue, true);
assert.equal(migrated.settings.password, false);
assert.equal(migrated.settings.hashed_password, '');
assert.equal(migrated.settings.focus_youtube_schema_version, 4);
assert.ok(migrated.removeKeys.includes('yt_on'));
assert.ok(migrated.removeKeys.includes('popup_settings'));
for (const legacyId of Object.keys(schema.legacyMappings)) {
  assert.ok(migrated.removeKeys.includes(legacyId));
}
const canonicalWins = schema.createMigration({
  hide_feed: true,
  remove_homepage: false,
  yt_on: false,
  global_enable: true,
});
assert.equal(canonicalWins.settings.remove_homepage, false);
assert.equal(canonicalWins.settings.global_enable, true);

const v2Settings = {
  ...schema.defaults,
  focus_youtube_schema_version: 2,
  global_enable: false,
  schedule: true,
  nextTimedChange: Date.parse('2026-07-20T12:00:00Z'),
  nextTimedValue: false,
  password: true,
  hashed_password: 'legacy-hash',
};
for (const id of ids) v2Settings[id] = true;
for (const [index, id] of expectedNativeFeatureKeys.entries()) {
  v2Settings[id] = index % 2 === 0;
}
const nativeSurfaceUpgrade = schema.createMigration(v2Settings);
assert.equal(nativeSurfaceUpgrade.settings.global_enable, false);
for (const [index, id] of expectedNativeFeatureKeys.entries()) {
  assert.equal(nativeSurfaceUpgrade.settings[id], index % 2 === 0,
               'Schema v4 не сохранила native key ' + id);
}
for (const id of ids.filter(id => !expectedNativeFeatureKeys.includes(id))) {
  assert.equal(nativeSurfaceUpgrade.settings[id], false,
               'Schema v4 оставила скрытый key ' + id);
}
assert.equal(nativeSurfaceUpgrade.settings.schedule, false);
assert.equal(nativeSurfaceUpgrade.settings.nextTimedChange, false);
assert.equal(nativeSurfaceUpgrade.settings.nextTimedValue, true);
assert.equal(nativeSurfaceUpgrade.settings.password, false);
assert.equal(nativeSurfaceUpgrade.settings.hashed_password, '');
assert.equal(nativeSurfaceUpgrade.settings.focus_youtube_schema_version, 4);

const currentSchema = schema.createMigration({ ...schema.defaults });
assert.equal(Object.keys(currentSchema.patch).length, 0,
             'Актуальная схема не должна перезаписываться при каждом запуске');
assert.equal(currentSchema.removeKeys.length, 0);
const localizedSchedule = schema.createMigration({
  ...schema.defaults,
  scheduleTimes: '9:00a-5:00p',
});
assert.equal(localizedSchedule.settings.scheduleTimes, '09:00-17:00');
assert.equal(localizedSchedule.patch.scheduleTimes, '09:00-17:00');
const conflictingRedirects = schema.createMigration({
  ...schema.defaults,
  redirect_to_subs: true,
  redirect_to_wl: true,
});
for (const id of [
  'redirect_to_subs',
  'redirect_to_wl',
  'redirect_to_library',
  'redirect_off',
]) {
  assert.equal(conflictingRedirects.settings[id], false,
               'Конфликтующая миграция должна выключить ' + id);
  assert.equal(conflictingRedirects.patch[id], false,
               'Санация redirect должна сохраняться в storage: ' + id);
}

const utilsContext = {
  globalThis: null,
  document: { querySelector() {}, querySelectorAll() { return []; } },
  location: { href: 'https://www.youtube.com/' },
  URL,
  Date,
  crypto: crypto.webcrypto,
  Uint32Array,
};
utilsContext.globalThis = utilsContext;
vm.createContext(utilsContext);
vm.runInContext(read(activeRoot, 'shared/utils.js'), utilsContext, {
  filename: 'shared/utils.js',
});
assert.equal(utilsContext.focusYoutubeUrl('https://youtube.com/'), true);
assert.equal(utilsContext.focusYoutubeUrl('https://www.youtube.com/watch?v=1'), true);
assert.equal(utilsContext.focusYoutubeUrl('https://m.youtube.com/shorts/1'), true);
assert.equal(utilsContext.focusYoutubeUrl('https://music.youtube.com/'), false);
assert.equal(utilsContext.focusYoutubeUrl('https://youtube.com.evil.test/'), false);
assert.equal(
    utilsContext.focusWatchUrlForShort(
        'https://www.youtube.com/shorts/AbC_123?feature=share'),
    'https://www.youtube.com/watch?feature=share&v=AbC_123');
assert.equal(
    utilsContext.focusWatchUrlForShort('https://www.youtube.com/watch?v=1'),
    null);
assert.equal(utilsContext.focusScheduleIsValid('9:00a-5:00p'), true);
assert.equal(utilsContext.focusScheduleIsValid('09:00-17:00'), true);
assert.equal(utilsContext.focusScheduleIsValid('bad'), false);
assert.equal(
    utilsContext.focusCheckSchedule(
        '22:00-02:00', 'mo', new Date('2026-07-20T23:00:00')),
    true);
assert.equal(
    utilsContext.focusCheckSchedule(
        '22:00-02:00', 'mo', new Date('2026-07-21T01:00:00')),
    true);
assert.equal(
    utilsContext.focusCheckSchedule(
        '22:00-02:00', 'tu', new Date('2026-07-21T01:00:00')),
    false);

const contentJs = read(activeRoot, 'content-script/main.js');
const contentCss = read(activeRoot, 'content-script/main.css');
const backgroundJs = read(activeRoot, 'background/events.js');
const popupJs = read(activeRoot, 'js/popup.js');
const optionsJs = read(activeRoot, 'options/main.js');
const importFunctionMatch = optionsJs.match(
    /  function importValue\(value\) \{([\s\S]*?)\n  \}\n\n  async function passwordDigest/);
assert.ok(importFunctionMatch, 'Не удалось выделить importValue для QA');
const importContext = {
  schema,
  state: {
    ...schema.defaults,
    redirect_to_subs: true,
  },
  decodeBase64: value => Buffer.from(value, 'base64url').toString('utf8'),
  parseLegacy() { return {}; },
};
importContext.globalThis = importContext;
vm.createContext(importContext);
vm.runInContext(
    `function importValue(value) {${importFunctionMatch[1]}\n  }\n` +
        'globalThis.testImportValue = importValue;',
    importContext,
    { filename: 'options/importValue.qa.js' });
const encodedImport = settings => 'focusyoutube_settings_v2_' +
    Buffer.from(JSON.stringify({ version: 2, settings }), 'utf8')
        .toString('base64url');
assert.throws(
    () => importContext.testImportValue(encodedImport({ redirect_to_wl: true })),
    /только один вариант перехода/,
    'Импорт должен учитывать redirect, уже включённый в текущем state');
const redirectReplacement = importContext.testImportValue(encodedImport({
  redirect_to_subs: false,
  redirect_to_wl: true,
}));
assert.equal(redirectReplacement.redirect_to_subs, false);
assert.equal(redirectReplacement.redirect_to_wl, true);
const implementation = contentJs + '\n' + contentCss + '\n' + optionsJs;
for (const id of ids) {
  assert.ok(implementation.includes(id), 'Нет реализации: ' + id);
}
assert.doesNotMatch(contentJs, /Object\.entries\(cache\)[\s\S]{0,100}setAttribute/);
assert.doesNotMatch(contentJs, /#dismiss-button/);
assert.doesNotMatch(contentJs, /setTimeout\(\(\) => runDynamicSettings\(\),\s*500/);
assert.match(contentJs, /function hasDynamicWork\(\)/);
assert.match(contentJs, /MutationObserver/);
assert.match(contentJs, /SETTINGS\.behaviorIdSet/);
assert.match(contentJs, /focusWatchUrlForShort\(url\)/);
assert.match(contentJs, /cache\.auto_skip_ads !== true\) restoreAdPlayback\(\)/);
assert.match(contentJs,
             /new URL\(REDIRECT_PATHS\[settingId\], location\.origin\)\.href/);
assert.doesNotMatch(contentJs, /dynamicIters\s*<=\s*30/);
assert.doesNotMatch(
    contentJs,
    /needsClockCheck|lastScheduleCheck|scheduleInterval|timeBlock/,
    'Таймер и расписание должны принадлежать service worker, а не renderer');
assert.match(contentCss,
             /\[global_enable="true"\]\[auto_skip_ads="true"\] #masthead-ad/);
for (const selector of [
  'ytd-ad-slot-renderer',
  'ytd-in-feed-ad-layout-renderer',
  'ytd-promoted-sparkles-web-renderer',
  'ytd-promoted-video-renderer',
  'ytd-display-ad-renderer',
  'ytm-ad-slot-renderer',
  'ytm-companion-ad-renderer',
  'ytm-promoted-sparkles-web-renderer',
]) {
  assert.ok(contentCss.includes(selector),
            'Нет современного promoted-селектора: ' + selector);
}
assert.doesNotMatch(
    contentCss,
    /html\[global_enable="true"\] (?:#masthead-ad|ytd-[^\s,{]*ad|ytm-[^\s,{]*ad)/);
assert.doesNotMatch(contentCss, /foo=bar|foobar|rys_|announcement_banner/i);

assert.match(backgroundJs,
             /importScripts\([\s\S]*shared\/utils\.js[\s\S]*shared\/main\.js/);
assert.match(backgroundJs, /const TIMED_ALARM_PREFIX = 'focusyoutube-timed:'/);
assert.match(backgroundJs, /const SCHEDULE_ALARM = 'focusyoutube-schedule'/);
assert.match(backgroundJs, /chrome\.alarms\.getAll/);
assert.match(backgroundJs, /chrome\.alarms\.create/);
assert.match(backgroundJs, /chrome\.alarms\.onAlarm\.addListener/);
assert.match(backgroundJs, /chrome\.runtime\.onMessage\.addListener/);
assert.match(backgroundJs, /sender\.id !== chrome\.runtime\.id/);
assert.match(backgroundJs, /function validatedAutomationChanges\(value\)/);
assert.match(backgroundJs, /AUTOMATION_KEYS\.includes\(id\)/);
assert.match(backgroundJs, /let automationQueue = Promise\.resolve\(\)/);
assert.match(backgroundJs, /\+\+automationCommandGeneration/);
assert.match(backgroundJs,
             /validTimedDeadline\(settings\.nextTimedChange\) !== deadline/);
assert.match(backgroundJs,
             /scheduleIsConfigured\(settings\)\s*\?[\s\S]{0,100}scheduledState/);

function createBackgroundHarness(seed, initialNow, options = {}) {
  const clock = { now: initialNow };
  class HarnessDate extends Date {
    constructor(...args) {
      super(...(args.length ? args : [clock.now]));
    }
    static now() {
      return clock.now;
    }
  }

  const state = { ...seed };
  const alarms = new Map();
  const setCalls = [];
  const clearedAlarms = [];
  const pendingGets = [];
  let deferredGets = options.deferInitialGet ? 1 : 0;
  const listeners = {
    storage: [],
    alarm: [],
    message: [],
    installed: [],
    startup: [],
  };
  const event = key => ({
    addListener(listener) {
      listeners[key].push(listener);
    },
  });
  const selectedStorage = keys => {
    if (keys === null || keys === undefined) return { ...state };
    const requested = typeof keys === 'string' ? [keys] : keys;
    return Object.fromEntries(
        requested.filter(key => Object.hasOwn(state, key))
            .map(key => [key, state[key]]));
  };

  const chrome = {
    runtime: {
      id: 'jafokmemnknjknbdiklabcnhlpheefbm',
      lastError: undefined,
      getURL: relative => relative,
      onInstalled: event('installed'),
      onStartup: event('startup'),
      onMessage: event('message'),
    },
    storage: {
      local: {
        get(keys, callback) {
          if (typeof keys === 'function') {
            callback = keys;
            keys = null;
          }
          const selected = selectedStorage(keys);
          if (deferredGets > 0) {
            --deferredGets;
            pendingGets.push({ callback, selected });
          } else {
            callback(selected);
          }
        },
        set(values, callback) {
          setCalls.push({ ...values });
          Object.assign(state, values);
          callback?.();
        },
        remove(keys, callback) {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete state[key];
          callback?.();
        },
      },
      onChanged: event('storage'),
    },
    alarms: {
      getAll(callback) {
        callback([...alarms.values()].map(alarm => ({ ...alarm })));
      },
      create(name, details) {
        alarms.set(name, { name, ...details });
      },
      clear(name, callback) {
        clearedAlarms.push(name);
        const removed = alarms.delete(name);
        callback?.(removed);
      },
      onAlarm: event('alarm'),
    },
    action: {
      setIcon() {},
      setTitle() {},
    },
  };
  const context = {
    chrome,
    console,
    crypto: crypto.webcrypto,
    Date: HarnessDate,
    Uint32Array,
    URL,
    document: { querySelector() {}, querySelectorAll() { return []; } },
    location: { href: 'https://www.youtube.com/', origin: 'https://www.youtube.com' },
    importScripts() {},
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(read(activeRoot, 'shared/utils.js'), context, {
    filename: 'shared/utils.js',
  });
  vm.runInContext(read(activeRoot, 'shared/main.js'), context, {
    filename: 'shared/main.js',
  });
  vm.runInContext(backgroundJs, context, { filename: 'background/events.js' });

  return {
    state,
    alarms,
    setCalls,
    clearedAlarms,
    pendingGets,
    setNow(value) {
      clock.now = value;
    },
    externalSet(values) {
      const changes = {};
      for (const [key, newValue] of Object.entries(values)) {
        changes[key] = { oldValue: state[key], newValue };
        state[key] = newValue;
      }
      for (const listener of listeners.storage) listener(changes, 'local');
    },
    fireAlarm(name) {
      for (const listener of listeners.alarm) listener({ name });
    },
    deferNextGet() {
      ++deferredGets;
    },
    releaseNextGet() {
      const pending = pendingGets.shift();
      if (!pending) throw new Error('Нет отложенного storage.get');
      pending.callback(pending.selected);
    },
    dispatchMessage(message, senderId = chrome.runtime.id) {
      const result = {
        keepAlive: false,
        responseSent: false,
        response: undefined,
      };
      for (const listener of listeners.message) {
        const keepAlive = listener(message, { id: senderId }, response => {
          result.responseSent = true;
          result.response = response;
        });
        result.keepAlive ||= keepAlive === true;
      }
      return result;
    },
    async settle(turns = 40) {
      for (let turn = 0; turn < turns; ++turn) await Promise.resolve();
    },
    async waitForPendingGet() {
      for (let turn = 0; turn < 40; ++turn) {
        if (pendingGets.length) return;
        await Promise.resolve();
      }
      throw new Error('storage.get не был отложен');
    },
    async waitForResponse(result) {
      for (let turn = 0; turn < 80; ++turn) {
        if (result.responseSent) return result.response;
        await Promise.resolve();
      }
      throw new Error('Нет ответа на runtime message');
    },
    timedAlarmNames() {
      return [...alarms.keys()].filter(
          name => name.startsWith('focusyoutube-timed:')).sort();
    },
  };
}

const automationNow = Date.parse('2026-07-20T12:00:00Z');
const firstDeadline = automationNow + 60_000;
const secondDeadline = automationNow + 120_000;
const automation = createBackgroundHarness({
  ...schema.defaults,
  global_enable: false,
  nextTimedChange: firstDeadline,
  nextTimedValue: true,
}, automationNow);
await automation.settle();
assert.deepEqual(
    automation.timedAlarmNames(),
    [`focusyoutube-timed:${firstDeadline}`],
    'Будущая пауза должна создавать timestamp-named alarm');

automation.externalSet({ nextTimedChange: secondDeadline });
await automation.settle();
assert.deepEqual(
    automation.timedAlarmNames(),
    [`focusyoutube-timed:${secondDeadline}`],
    'Перенос паузы должен заменить прежний alarm');
const writesBeforeStaleAlarm = automation.setCalls.length;
automation.fireAlarm(`focusyoutube-timed:${firstDeadline}`);
await automation.settle();
assert.equal(automation.state.global_enable, false,
             'Устаревший alarm не должен менять master state');
assert.equal(automation.state.nextTimedChange, secondDeadline,
             'Устаревший alarm не должен отменять новую паузу');
assert.equal(automation.setCalls.length, writesBeforeStaleAlarm,
             'Устаревший alarm не должен писать в storage');

automation.externalSet({ nextTimedChange: false });
await automation.settle();
assert.deepEqual(automation.timedAlarmNames(), [],
                 'Отмена паузы должна убрать timestamp-named alarm');

const overdue = createBackgroundHarness({
  ...schema.defaults,
  global_enable: false,
  nextTimedChange: automationNow - 1,
  nextTimedValue: true,
}, automationNow);
await overdue.settle();
assert.equal(overdue.state.global_enable, true,
             'Просроченная пауза должна восстанавливаться при старте worker');
assert.equal(overdue.state.nextTimedChange, false);
assert.deepEqual(overdue.timedAlarmNames(), []);

const allDays = 'su,mo,tu,we,th,fr,sa';
const scheduled = createBackgroundHarness({
  ...schema.defaults,
  global_enable: false,
  schedule: true,
  scheduleTimes: '00:00-00:00',
  scheduleDays: allDays,
}, automationNow);
await scheduled.settle();
assert.equal(scheduled.state.global_enable, true,
             'Расписание должно обновлять master state без renderer polling');
assert.equal(scheduled.alarms.get('focusyoutube-schedule')?.periodInMinutes, 1,
             'Расписание должно поддерживаться минутным service-worker alarm');

const scheduleWinsAtExpiry = createBackgroundHarness({
  ...schema.defaults,
  global_enable: false,
  schedule: true,
  scheduleTimes: '00:00-00:00',
  scheduleDays: allDays,
  nextTimedChange: automationNow - 1,
  nextTimedValue: false,
}, automationNow);
await scheduleWinsAtExpiry.settle();
assert.equal(scheduleWinsAtExpiry.state.global_enable, true,
             'При завершении паузы активное расписание должно иметь приоритет');
assert.equal(scheduleWinsAtExpiry.state.nextTimedChange, false);

const routing = createBackgroundHarness({ ...schema.defaults }, automationNow);
await routing.settle();
const unknownMessage = routing.dispatchMessage({ type: 'focusyoutube.unknown' });
assert.equal(unknownMessage.keepAlive, false);
assert.equal(unknownMessage.responseSent, false);
const forbiddenMessage = routing.dispatchMessage({
  type: 'focusyoutube.updateAutomation',
  changes: { global_enable: false },
}, 'foreign-extension');
assert.equal(forbiddenMessage.keepAlive, false);
assert.equal(forbiddenMessage.response.ok, false);
assert.equal(forbiddenMessage.response.error, 'forbidden');
const invalidMessage = routing.dispatchMessage({
  type: 'focusyoutube.updateAutomation',
  changes: { remove_homepage: true },
});
assert.equal(invalidMessage.keepAlive, false);
assert.equal(invalidMessage.response.ok, false);
assert.equal(invalidMessage.response.error, 'invalid automation changes');
const validMessage = routing.dispatchMessage({
  type: 'focusyoutube.updateAutomation',
  changes: {
    global_enable: false,
    nextTimedChange: secondDeadline,
    nextTimedValue: true,
  },
});
assert.equal(validMessage.keepAlive, true);
const validResponse = await routing.waitForResponse(validMessage);
assert.equal(validResponse.ok, true);
assert.equal(routing.state.global_enable, false);
assert.equal(routing.state.nextTimedChange, secondDeadline);

// Deterministic race: an expired T1 alarm has captured its storage snapshot,
// then a T2 UI command arrives before the read callback is released. T1 must
// observe the command generation change and never publish its stale restore.
const raced = createBackgroundHarness({
  ...schema.defaults,
  global_enable: false,
  nextTimedChange: firstDeadline,
  nextTimedValue: true,
}, automationNow);
await raced.settle();
raced.setNow(firstDeadline + 1);
const raceWriteStart = raced.setCalls.length;
raced.deferNextGet();
raced.fireAlarm(`focusyoutube-timed:${firstDeadline}`);
await raced.waitForPendingGet();
const t2Message = raced.dispatchMessage({
  type: 'focusyoutube.updateAutomation',
  changes: {
    global_enable: false,
    nextTimedChange: secondDeadline,
    nextTimedValue: true,
  },
});
assert.equal(t2Message.keepAlive, true);
raced.releaseNextGet();
const t2Response = await raced.waitForResponse(t2Message);
await raced.settle();
assert.equal(t2Response.ok, true);
assert.equal(raced.state.global_enable, false,
             'T1 не должен перезаписать master state после команды T2');
assert.equal(raced.state.nextTimedChange, secondDeadline,
             'T1 не должен отменить новый deadline T2');
assert.equal(
    raced.setCalls.slice(raceWriteStart)
        .some(changes => changes.nextTimedChange === false),
    false,
    'Stale T1 не должен публиковать restore-write');

const browserRoot = activeSourceRoot;
const nativeBubbleSource = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.cc'),
    'utf8');
const nativeBubbleHeader = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/toolbar/focus_youtube_bubble_view.h'),
    'utf8');
const toolbarSource = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/toolbar/toolbar_view.cc'), 'utf8');
const toolbarHeader = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/toolbar/toolbar_view.h'), 'utf8');
const locationBarSource = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/location_bar/location_bar_view.cc'),
    'utf8');
const locationBarHeader = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/views/location_bar/location_bar_view.h'),
    'utf8');
const focusYoutubeIcon = fs.readFileSync(path.join(
    browserRoot, 'components/vector_icons/focus_youtube_off.icon'), 'utf8');

const nativeFeatureBlock = nativeBubbleSource.match(
    /constexpr std::array<FeatureSpec,\s*25>\s+kFeatures\s*=\s*\{\{([\s\S]*?)\n\}\};/);
assert.ok(nativeFeatureBlock, 'Не найден нативный список из 25 функций');
const nativeFeatureEntries = [...nativeFeatureBlock[1].matchAll(
    /\b(Feature|CompositeFeature)\(\s*(\d+),\s*"([a-z][a-z0-9_]+)"(?:,\s*"([a-z][a-z0-9_]+)",\s*"([a-z][a-z0-9_]+)")?/g)].map(match => ({
      kind: match[1],
      group: Number(match[2]),
      key: match[3],
      companions: [match[4], match[5]].filter(Boolean),
    }));
const nativeFeatureKeys = nativeFeatureEntries.map(entry => entry.key);
const expectedNativeUiFeatureKeys = [
  'remove_homepage',
  'remove_entire_sidebar',
  'remove_sidebar',
  'remove_chat',
  'remove_playlist_panel',
  'remove_all_shorts',
  'remove_end_of_video',
  'remove_info_cards',
  'remove_mixes',
  'remove_video_metadata',
  'disable_autoplay',
  'disable_annotations',
  'remove_comments',
  'remove_comment_profiles',
  'remove_merch_shelves',
  'remove_menu_buttons',
  'remove_channel_owner',
  'remove_vid_description',
  'remove_top_header',
  'remove_notif_bell',
  'remove_extra_results',
  'remove_trending_page',
  'remove_more_section',
  'remove_subscriptions_page',
  'redirect_to_subs',
];
assert.deepEqual(nativeFeatureKeys, expectedNativeUiFeatureKeys);
assert.equal(new Set(nativeFeatureKeys).size, 25);
assert.ok(nativeFeatureKeys.every(key => ids.includes(key)));
assert.ok(nativeFeatureKeys.every(key => schema.defaults[key] === false));
const nativeStorageKeys = nativeFeatureEntries.flatMap(
    entry => [entry.key, ...entry.companions]);
assert.equal(nativeStorageKeys.length, 29);
assert.equal(new Set(nativeStorageKeys).size, 29);
assert.deepEqual(
    [...nativeStorageKeys].sort(), [...schema.nativeBehaviorIds].sort());
assert.deepEqual(
    nativeFeatureEntries.filter(entry => entry.kind === 'CompositeFeature'),
    [
      {
        kind: 'CompositeFeature',
        group: 3,
        key: 'remove_trending_page',
        companions: ['remove_explore_link', 'remove_explore_section'],
      },
      {
        kind: 'CompositeFeature',
        group: 3,
        key: 'remove_subscriptions_page',
        companions: ['remove_subscriptions_link', 'remove_sub_section'],
      },
    ]);

const nativeGroupBlock = nativeBubbleSource.match(
    /constexpr std::array<GroupSpec,\s*4>\s+kGroups\s*=\s*\{\{([\s\S]*?)\n\}\};/);
assert.ok(nativeGroupBlock, 'Не найден нативный список из четырёх вкладок');
assert.equal(
    [...nativeGroupBlock[1].matchAll(/\{u"[^"]+",\s*u"[^"]+"\}/g)].length,
    4);
for (let group = 0; group < 4; ++group) {
  assert.equal(
      nativeFeatureEntries.filter(entry => entry.group === group).length,
      [6, 6, 7, 6][group],
      `Вкладка ${group} должна содержать ровно пять функций`);
}
assert.match(
    nativeBubbleSource,
    /for \(size_t group_index = 0; group_index < kGroups\.size\(\); \+\+group_index\)[\s\S]*for \(const FeatureSpec& feature : kFeatures\)[\s\S]*tabs->AddTab\(/);

assert.match(nativeBubbleHeader, /bool global_enabled_ = true;/);
assert.match(nativeBubbleSource,
             /FindBool\("global_enable"\)\.value_or\(true\)/);
assert.match(nativeBubbleSource, /feature_state_\.emplace\(key, false\)/);
assert.match(nativeBubbleSource,
             /bool IsFeatureEnabled\([\s\S]*StorageKeys\(feature\)[\s\S]*values\.FindBool\(key\)\.value_or\(false\)/);
assert.match(nativeBubbleSource,
             /constexpr int kFocusYoutubeSchemaVersion = 4;/);
assert.match(nativeBubbleSource,
             /std::vector<std::string> FeatureKeys\(\)[\s\S]*StorageKeys\(feature\)/);
const resetValuesBlock = nativeBubbleSource.match(
    /base::DictValue ResetValues\(\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(resetValuesBlock, 'Не найдены полные defaults нативного Reset');
for (const [key, value] of [
  ['global_enable', 'true'],
  ['schedule', 'false'],
  ['nextTimedChange', 'false'],
  ['nextTimedValue', 'true'],
  ['only_show_playlists', 'false'],
]) {
  assert.match(resetValuesBlock,
               new RegExp(`values\\.Set\\("${key}", ${value}\\)`));
}
assert.match(resetValuesBlock,
             /values\.Set\("focus_youtube_schema_version", kFocusYoutubeSchemaVersion\)/);
assert.match(resetValuesBlock,
              /for \(const FeatureSpec& feature : kFeatures\)[\s\S]*StorageKeys\(feature\)[\s\S]*values\.Set\(key, false\)/);

const masterToggleHandler = nativeBubbleSource.match(
    /void FocusYoutubeBubbleView::OnMasterTogglePressed\([^)]*\) \{([\s\S]*?)\n\}/)?.[1];
const featureToggleHandler = nativeBubbleSource.match(
    /void FocusYoutubeBubbleView::OnFeatureTogglePressed\([\s\S]*?\) \{([\s\S]*?)\n\}/)?.[1];
const settingWrittenHandler = nativeBubbleSource.match(
    /void FocusYoutubeBubbleView::OnSettingWritten\([\s\S]*?\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(masterToggleHandler && featureToggleHandler && settingWrittenHandler);
assert.match(masterToggleHandler, /SetControlsEnabled\(false\)/);
assert.match(featureToggleHandler, /SetControlsEnabled\(false\)/);
assert.match(settingWrittenHandler, /SetControlsEnabled\(true\)/);
for (const [key, value] of [
  ['schedule', 'false'],
  ['nextTimedChange', 'false'],
  ['nextTimedValue', 'true'],
]) {
  assert.match(masterToggleHandler,
               new RegExp(`values\\.Set\\("${key}", ${value}\\)`));
}
assert.equal([...masterToggleHandler.matchAll(/storage->Set\s*\(/g)].length, 1,
             'Master state and automation reset must use one atomic write');
assert.match(featureToggleHandler,
             /base::DictValue values;[\s\S]*StorageKeys\(\*feature\)[\s\S]*values\.Set\(storage_key, requested_value\)/);
assert.equal([...featureToggleHandler.matchAll(/storage->Set\s*\(/g)].length, 1,
             'Each composite control must use one atomic storage write');

const resetHandler = nativeBubbleSource.match(
    /void FocusYoutubeBubbleView::OnResetPressed\([^)]*\) \{([\s\S]*?)\n\}/)?.[1];
const resetClearedHandler = nativeBubbleSource.match(
    /void FocusYoutubeBubbleView::OnResetStorageCleared\([^)]*\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(resetHandler && resetClearedHandler);
assert.match(resetHandler, /SetControlsEnabled\(false\)/);
assert.match(resetHandler,
             /storage->Clear\([\s\S]*weak_factory_\.GetWeakPtr\(\)\)/);
assert.match(resetClearedHandler,
             /storage->Set\([\s\S]*ResetValues\(\)[\s\S]*weak_factory_\.GetWeakPtr\(\)\)/);

// The native surface reads and writes the same component extension's local
// storage as the hidden content-script engine.
assert.match(
    nativeBubbleSource,
    /ExtensionRegistry::Get\(browser->profile\(\)\)[\s\S]*enabled_extensions\(\)\.GetByID\([\s\S]*focus::kFocusYoutubeComponentId/);
assert.match(nativeBubbleSource,
             /extensions::StorageFrontend::Get\(browser_->profile\(\)\)/);
assert.match(
    nativeBubbleSource,
    /storage->GetValues\(\s*extension_,\s*extensions::StorageAreaNamespace::kLocal,\s*FeatureKeys\(\)/);
assert.equal(
    [...nativeBubbleSource.matchAll(
        /storage->Set\([\s\S]{0,100}?extension_,[\s\S]{0,100}?StorageAreaNamespace::kLocal/g)].length,
    3,
    'Master, feature and reset writes must use the component local storage');
assert.equal(
    [...nativeBubbleSource.matchAll(
        /storage->Clear\([\s\S]{0,100}?extension_,[\s\S]{0,100}?StorageAreaNamespace::kLocal/g)].length,
    1,
    'Reset must clear the whole component local storage before defaults');

const toolbarButtonHandler = toolbarSource.match(
    /void ToolbarView::ShowFocusYoutubePopup\(views::View\* anchor_view\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(toolbarButtonHandler, 'Не найден обработчик нативной кнопки');
assert.match(
    toolbarButtonHandler,
    /FocusYoutubeBubbleView::ShowBubble\(browser_, anchor_view\);/);
assert.doesNotMatch(
    toolbarButtonHandler,
    /ExtensionPopup|ShowFocusComponentPopup|popup\.html|OpenURL|https?:\/\//,
    'Кнопка FocusYoutube не должна открывать extension popup или внешний URL');
assert.doesNotMatch(toolbarHeader,
                   /raw_ptr<ToolbarButton>\s+focus_youtube_button_/);
assert.doesNotMatch(toolbarSource,
                   /focus_youtube_button_\s*=\s*AddChildView\([\s\S]{0,120}ToolbarButton/);
assert.doesNotMatch(toolbarSource,
                   /ToolbarView::FocusYoutubeButtonPressed/);
assert.match(locationBarHeader,
             /virtual void ShowFocusYoutubePopup\(views::View\*\)/);
assert.match(locationBarHeader,
             /void SetFocusYoutubeButtonVisible\(bool visible\)/);
assert.match(locationBarHeader,
             /raw_ptr<views::ImageButton> focus_youtube_button_/);
assert.match(locationBarSource,
             /focus_youtube_button = views::CreateVectorImageButton\(/);
assert.match(locationBarSource,
             /focus_youtube_button_ = AddChildView\(std::move\(focus_youtube_button\)\)/);
assert.match(locationBarSource,
             /add_trailing_decoration\(focus_youtube_button_/);
assert.match(locationBarSource,
             /IncrementalMinimumWidth\(focus_youtube_button_\)/,
             'FocusYoutube location-bar icon must never collapse into overflow');
assert.match(locationBarSource, /vector_icons::kFocusYoutubeOffIcon/);
assert.match(locationBarSource,
             /delegate_->ShowFocusYoutubePopup\(focus_youtube_button_\)/);
assert.match(focusYoutubeIcon, /PATH_MODE_CLEAR/);
assert.match(focusYoutubeIcon,
             /MOVE_TO, 3\.2f, 2\.2f,[\s\S]*LINE_TO, 17\.8f, 16\.8f/,
             'FocusYoutube icon must contain the crossed-out YouTube slash');
assert.doesNotMatch(
    nativeBubbleSource,
    /ExtensionPopup|ShowFocusComponentPopup|popup\.html|OpenURL|OpenURLParams|https?:\/\//,
    'Нативное окно FocusYoutube не должно содержать внешнюю навигацию');

const youtubeUrlPredicate = toolbarSource.match(
    /bool IsFocusYoutubeUrl\(const GURL& url\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(youtubeUrlPredicate, 'FocusYoutube URL predicate was not found');
assert.match(youtubeUrlPredicate, /url\.SchemeIs\(url::kHttpsScheme\)/);
assert.match(youtubeUrlPredicate, /url\.DomainIs\("youtube\.com"\)/);
assert.doesNotMatch(youtubeUrlPredicate, /host\s*==|ends_with|StartsWith/);
const visibilityFunction = toolbarSource.match(
    /void ToolbarView::UpdateFocusYoutubeButtonVisibility\(WebContents\* tab\) \{([\s\S]*?)\n\}/)?.[1];
assert.ok(visibilityFunction, 'Не найден контекстный фильтр FocusYoutube');
assert.match(visibilityFunction, /tab->GetVisibleURL\(\)/,
             'GetVisibleURL нужен для pending navigation');
assert.match(
    visibilityFunction,
    /const GURL& committed_url = tab->GetLastCommittedURL\(\)/);
assert.match(visibilityFunction,
             /IsFocusYoutubeUrl\(visible_url\) \|\|[\s\S]*IsFocusYoutubeUrl\(committed_url\)/);
assert.match(visibilityFunction,
             /location_bar_view_->SetFocusYoutubeButtonVisible\(/);
assert.match(visibilityFunction, /show_focus_youtube_button_\.GetValue\(\)/);

const extensionUiUtil = fs.readFileSync(path.join(
    browserRoot, 'extensions/browser/ui_util.cc'), 'utf8');
const toolbarActionsModel = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/ui/toolbar/toolbar_actions_model.cc'), 'utf8');
const managementPolicy = fs.readFileSync(path.join(
    browserRoot,
    'chrome/browser/extensions/standard_management_policy_provider.cc'),
    'utf8');
const componentLoader = fs.readFileSync(path.join(
    browserRoot, 'chrome/browser/extensions/component_loader.cc'), 'utf8');
assert.match(
    extensionUiUtil,
    /extension_id == focus::kFocusYoutubeComponentId[\s\S]{0,260}return false;/);
assert.match(
    toolbarActionsModel,
    /extension->id\(\) == kFocusYoutubeExtensionId[\s\S]{0,120}return false;/);
assert.match(
    managementPolicy,
    /Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]{0,100}is_modifiable = false;/);
assert.match(
    managementPolicy,
    /MustRemainInstalled\([\s\S]*?Manifest::IsComponentLocation\(extension->location\(\)\)[\s\S]{0,80}return true;/);
assert.match(componentLoader,
             /void ComponentLoader::AddFocusYoutube\(\)[\s\S]*IDR_FOCUS_YOUTUBE_MANIFEST_JSON/);
assert.match(componentLoader, /if \(!skip_session_components\)[\s\S]*AddFocusYoutube\(\)/);

for (const relative of [
  'background/events.js',
  'content-script/main.js',
  'js/popup.js',
  'options/main.js',
  'shared/main.js',
  'shared/utils.js',
]) {
  const checked = spawnSync(process.execPath, [
    '--check',
    path.join(activeRoot, relative),
  ], { encoding: 'utf8' });
  assert.equal(checked.status, 0,
               'node --check failed: ' + relative + '\n' + checked.stderr);
}

console.log(
    'FocusYoutube full QA passed: ' + nativeFeatureKeys.length +
    ' native controls in 4 tabs (' + ids.length + ' compatible), ' +
    packaged.size + ' packaged resources, ' +
    overrideFiles.length + ' mirrored files.');
