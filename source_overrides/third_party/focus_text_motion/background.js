// Copyright 2026 The Focus Browser Authors

'use strict';

const FOCUS_MOTION_PREF = 'focus.ui.motion_enabled';
const STORAGE_KEY = 'motion_enabled';

function readMotionPreference(callback) {
  chrome.settingsPrivate.getPref(FOCUS_MOTION_PREF, pref => {
    callback(pref?.value === true);
  });
}

function synchronizeMotionPreference() {
  readMotionPreference(enabled => {
    chrome.storage.local.set({[STORAGE_KEY]: enabled});
  });
}

chrome.settingsPrivate.onPrefsChanged.addListener(changedPrefs => {
  const motionPref = changedPrefs.find(pref => pref.key === FOCUS_MOTION_PREF);
  if (motionPref) {
    chrome.storage.local.set({[STORAGE_KEY]: motionPref.value === true});
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'focus-text-motion.get-state') {
    return false;
  }
  readMotionPreference(enabled => sendResponse({enabled}));
  return true;
});

chrome.runtime.onInstalled.addListener(synchronizeMotionPreference);
chrome.runtime.onStartup.addListener(synchronizeMotionPreference);
synchronizeMotionPreference();
