// Copyright 2026 The Focus Browser Authors
// Browser-owned FocusYoutube component: local initialization, icon state and
// reliable automation that survives service-worker suspension.

'use strict';

importScripts(
    chrome.runtime.getURL('shared/utils.js'),
    chrome.runtime.getURL('shared/main.js'));

const storage = chrome.storage.local;
const schema = globalThis.FocusYoutubeSettings;
const AUTOMATION_KEYS = schema.automationIds;
const AUTOMATION_UPDATE_MESSAGE = 'focusyoutube.updateAutomation';
const AUTOMATION_RECONCILE_MESSAGE = 'focusyoutube.reconcileAutomation';
const TIMED_ALARM_PREFIX = 'focusyoutube-timed:';
const SCHEDULE_ALARM = 'focusyoutube-schedule';

// Explicit UI commands and automatic alarm/schedule work share one queue.
// A command increments its generation as soon as the message is received, so
// any older read-based task can stand down before it commits a stale snapshot.
let automationQueue = Promise.resolve();
let automationCommandGeneration = 0;
let automationStorageGeneration = 0;

function enqueueAutomationTask(task) {
  const run = automationQueue.then(() => task());
  automationQueue = run.catch(error => {
    console.error('FocusYoutube automation task failed:', error);
  });
  return run;
}

function storageGet(keys) {
  return new Promise((resolve, reject) => {
    storage.get(keys, value => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(value);
    });
  });
}

function storageSet(changes) {
  return new Promise((resolve, reject) => {
    storage.set(changes, () => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve();
    });
  });
}

function storageRemove(keys) {
  return new Promise((resolve, reject) => {
    storage.remove(keys, () => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve();
    });
  });
}

function getAllAlarms() {
  return new Promise((resolve, reject) => {
    chrome.alarms.getAll(alarms => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(alarms);
    });
  });
}

function clearAlarm(name) {
  return new Promise((resolve, reject) => {
    chrome.alarms.clear(name, wasCleared => {
      const error = chrome.runtime.lastError;
      if (error) {
        reject(new Error(error.message));
        return;
      }
      resolve(wasCleared);
    });
  });
}

function iconPaths(enabled) {
  const suffix = enabled ? '' : '-off';
  return {
    16: '/images/icon' + suffix + '-16.png',
    32: '/images/icon' + suffix + '-32.png',
    48: '/images/icon' + suffix + '-48.png',
    128: '/images/icon' + suffix + '-128.png',
  };
}

function updateIcon(enabled) {
  chrome.action.setIcon({ path: iconPaths(enabled !== false) });
  chrome.action.setTitle({
    title: enabled === false ?
      'FocusYoutube — защита выключена' :
      'FocusYoutube — режим без отвлечений',
  });
}

function validTimedDeadline(value) {
  const deadline = Number(value);
  return Number.isFinite(deadline) && deadline > 0 ? deadline : 0;
}

function scheduleIsConfigured(settings) {
  return settings.schedule === true &&
      typeof settings.scheduleDays === 'string' &&
      focusScheduleIsValid(settings.scheduleTimes);
}

function scheduledState(settings, now = new Date()) {
  return scheduleIsConfigured(settings) &&
      focusCheckSchedule(settings.scheduleTimes, settings.scheduleDays, now);
}

async function reconcileTimedAlarm(deadline, isCurrent = () => true) {
  const keepName = deadline > 0 ? TIMED_ALARM_PREFIX + deadline : null;
  const alarms = await getAllAlarms();
  if (!isCurrent()) return false;

  const staleNames = alarms
      .filter(alarm => alarm.name.startsWith(TIMED_ALARM_PREFIX) &&
          alarm.name !== keepName)
      .map(alarm => alarm.name);
  await Promise.all(staleNames.map(clearAlarm));
  if (!isCurrent()) return false;

  if (keepName) {
    chrome.alarms.create(keepName, { when: deadline });
  }
  return true;
}

async function reconcileScheduleAlarm(
    settings, now = Date.now(), isCurrent = () => true) {
  if (!isCurrent()) return false;
  if (!scheduleIsConfigured(settings)) {
    await clearAlarm(SCHEDULE_ALARM);
    return isCurrent();
  }
  const nextMinute = now - (now % 60_000) + 60_000;
  if (!isCurrent()) return false;
  chrome.alarms.create(SCHEDULE_ALARM, {
    when: nextMinute,
    periodInMinutes: 1,
  });
  return true;
}

async function completeTimedChange(
    settings, deadline, now = Date.now(), isCurrent = () => true) {
  if (!isCurrent() ||
      validTimedDeadline(settings.nextTimedChange) !== deadline ||
      deadline > now) {
    return false;
  }
  const enabled = scheduleIsConfigured(settings) ?
    scheduledState(settings, new Date(now)) :
    settings.nextTimedValue !== false;

  // There is no await between the final generation check and storage.set().
  // A newer UI command is queued after this task and therefore always wins.
  if (!isCurrent()) return false;
  await storageSet({
    global_enable: enabled,
    nextTimedChange: false,
    nextTimedValue: true,
  });
  await reconcileTimedAlarm(0);
  return true;
}

async function synchronizeAutomation(
    settings, now = Date.now(), isCurrent = () => true) {
  if (!await reconcileScheduleAlarm(settings, now, isCurrent) ||
      !isCurrent()) {
    return false;
  }

  const deadline = validTimedDeadline(settings.nextTimedChange);
  if (deadline > now) {
    return await reconcileTimedAlarm(deadline, isCurrent);
  }
  if (deadline > 0) {
    return await completeTimedChange(settings, deadline, now, isCurrent);
  }
  if (!await reconcileTimedAlarm(0, isCurrent) || !isCurrent()) {
    return false;
  }

  if (scheduleIsConfigured(settings)) {
    const enabled = scheduledState(settings, new Date(now));
    if (settings.global_enable !== enabled) {
      if (!isCurrent()) return false;
      await storageSet({ global_enable: enabled });
    }
  }
  return true;
}

function automaticTaskGuard(commandGeneration, storageGeneration) {
  return () => commandGeneration === automationCommandGeneration &&
      storageGeneration === automationStorageGeneration;
}

function refreshAutomation() {
  const commandGeneration = automationCommandGeneration;
  const storageGeneration = automationStorageGeneration;
  const isCurrent = automaticTaskGuard(
      commandGeneration, storageGeneration);
  enqueueAutomationTask(async () => {
    if (!isCurrent()) return;
    const settings = await storageGet(AUTOMATION_KEYS);
    if (!isCurrent()) return;
    await synchronizeAutomation(settings, Date.now(), isCurrent);
  });
}

function initializeStorage() {
  const commandGeneration = automationCommandGeneration;
  enqueueAutomationTask(async () => {
    const saved = await storageGet(null);
    const { patch, removeKeys } = schema.createMigration(saved);
    if (Object.keys(patch).length) {
      await storageSet(patch);
    }
    if (removeKeys.length) {
      await storageRemove(removeKeys);
    }

    // A command received while migration was pending is already queued behind
    // us. Do not publish icon/schedule state from the pre-command snapshot.
    if (commandGeneration !== automationCommandGeneration) return;
    const current = await storageGet(AUTOMATION_KEYS);
    if (commandGeneration !== automationCommandGeneration) return;
    updateIcon(current.global_enable);
    await synchronizeAutomation(
        current, Date.now(),
        () => commandGeneration === automationCommandGeneration);
  });
}

function validatedAutomationChanges(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const entries = Object.entries(value);
  if (!entries.length) return null;
  const changes = {};
  for (const [id, settingValue] of entries) {
    if (!AUTOMATION_KEYS.includes(id) ||
        !schema.isValidValue(id, settingValue)) {
      return null;
    }
    changes[id] = settingValue;
  }
  return changes;
}

initializeStorage();
chrome.runtime.onInstalled.addListener(() => initializeStorage());
chrome.runtime.onStartup.addListener(() => initializeStorage());

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== AUTOMATION_UPDATE_MESSAGE &&
      message?.type !== AUTOMATION_RECONCILE_MESSAGE) {
    return false;
  }
  if (sender.id !== chrome.runtime.id) {
    sendResponse({ ok: false, error: 'forbidden' });
    return false;
  }

  if (message.type === AUTOMATION_RECONCILE_MESSAGE) {
    const commandGeneration = automationCommandGeneration;
    enqueueAutomationTask(async () => {
      const saved = await storageGet(null);
      const migration = schema.createMigration(saved);
      const automationPatch = Object.fromEntries(
          Object.entries(migration.patch)
              .filter(([id]) => AUTOMATION_KEYS.includes(id)));
      if (Object.keys(automationPatch).length) {
        await storageSet(automationPatch);
      }
      let settings = await storageGet(AUTOMATION_KEYS);
      if (commandGeneration === automationCommandGeneration) {
        await synchronizeAutomation(
            settings, Date.now(),
            () => commandGeneration === automationCommandGeneration);
        settings = await storageGet(AUTOMATION_KEYS);
      }
      return settings;
    }).then(
        settings => sendResponse({ ok: true, settings }),
        error => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  const changes = validatedAutomationChanges(message.changes);
  if (!changes) {
    sendResponse({ ok: false, error: 'invalid automation changes' });
    return false;
  }

  ++automationCommandGeneration;
  enqueueAutomationTask(async () => {
    await storageSet(changes);
    let settings = await storageGet(AUTOMATION_KEYS);
    await synchronizeAutomation(settings);
    settings = await storageGet(AUTOMATION_KEYS);
    updateIcon(settings.global_enable);
    return settings;
  }).then(
      settings => sendResponse({ ok: true, settings }),
      error => sendResponse({ ok: false, error: error.message }));
  return true;
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local') return;
  if (changes.global_enable) {
    updateIcon(changes.global_enable.newValue);
  }
  if (Object.keys(changes).some(key => AUTOMATION_KEYS.includes(key))) {
    ++automationStorageGeneration;
    refreshAutomation();
  }
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === SCHEDULE_ALARM) {
    refreshAutomation();
    return;
  }
  if (!alarm.name.startsWith(TIMED_ALARM_PREFIX)) return;

  const commandGeneration = automationCommandGeneration;
  const storageGeneration = automationStorageGeneration;
  const isCurrent = automaticTaskGuard(
      commandGeneration, storageGeneration);
  enqueueAutomationTask(async () => {
    const deadline = Number(alarm.name.slice(TIMED_ALARM_PREFIX.length));
    if (!Number.isFinite(deadline) || deadline <= 0) {
      await clearAlarm(alarm.name);
      return;
    }
    if (!isCurrent()) return;

    const settings = await storageGet(AUTOMATION_KEYS);
    if (!isCurrent()) return;
    const currentDeadline = validTimedDeadline(settings.nextTimedChange);
    if (currentDeadline !== deadline) {
      await clearAlarm(alarm.name);
      if (isCurrent()) {
        await synchronizeAutomation(settings, Date.now(), isCurrent);
      }
      return;
    }
    if (deadline > Date.now()) {
      await reconcileTimedAlarm(deadline, isCurrent);
      return;
    }
    await completeTimedChange(settings, deadline, Date.now(), isCurrent);
  });
});
