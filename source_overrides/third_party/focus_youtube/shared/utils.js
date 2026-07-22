// Copyright 2026 The Focus Browser Authors

'use strict';

function focusQs(query, root = document) {
  return root.querySelector(query);
}

function focusQsa(query, root = document) {
  return Array.from(root.querySelectorAll(query));
}

// Compatibility aliases retained for the original Unhook behavior engine.
const qs = focusQs;
const qsa = focusQsa;

function focusYoutubeUrl(urlValue = location.href) {
  try {
    const url = new URL(urlValue);
    return url.protocol === 'https:' &&
        (url.hostname === 'youtube.com' ||
         url.hostname === 'www.youtube.com' ||
         url.hostname === 'm.youtube.com');
  } catch {
    return false;
  }
}

function focusYoutubePage(urlValue = location.href) {
  if (!focusYoutubeUrl(urlValue)) return 'other';
  const url = new URL(urlValue);
  if (url.pathname === '/' || url.pathname === '') return 'home';
  if (url.pathname === '/results') return 'results';
  if (url.pathname.startsWith('/shorts/')) return 'shorts';
  if (url.pathname === '/watch') return 'video';
  if (url.pathname === '/feed/subscriptions') return 'subscriptions';
  if (url.pathname === '/feed/trending' ||
      url.pathname === '/feed/explore') return 'trending';
  if (url.pathname.startsWith('/@') ||
      url.pathname.startsWith('/channel/')) return 'channel';
  return 'other';
}

function focusWatchUrlForShort(urlValue = location.href) {
  if (!focusYoutubeUrl(urlValue)) return null;
  const url = new URL(urlValue);
  const match = url.pathname.match(/^\/shorts\/([^/]+)\/?$/);
  if (!match) return null;
  url.pathname = '/watch';
  url.searchParams.set('v', match[1]);
  url.hash = '';
  return url.href;
}

const FOCUS_DAYS = Object.freeze(['su', 'mo', 'tu', 'we', 'th', 'fr', 'sa']);

function focusParseClock(value, now) {
  const text = String(value || '').trim().toLowerCase();
  const twelveHour = text.match(/^([1-9]|1[0-2]):([0-5]\d)(a|p)$/);
  const twentyFourHour = text.match(/^([01]?\d|2[0-3]):([0-5]\d)$/);
  let hours;
  let minutes;
  if (twelveHour) {
    hours = Number(twelveHour[1]) % 12;
    if (twelveHour[3] === 'p') hours += 12;
    minutes = Number(twelveHour[2]);
  } else if (twentyFourHour) {
    hours = Number(twentyFourHour[1]);
    minutes = Number(twentyFourHour[2]);
  } else {
    return null;
  }
  const result = new Date(now);
  result.setHours(hours, minutes, 0, 0);
  return result;
}

function focusScheduleIsValid(times) {
  return String(times || '').split(',').every(range => {
    const [start, end, extra] = range.trim().split('-');
    if (extra !== undefined || !start || !end) return false;
    const now = new Date();
    return Boolean(focusParseClock(start, now) &&
                   focusParseClock(end, now));
  });
}

function focusCheckSchedule(times, days, now = new Date()) {
  if (!focusScheduleIsValid(times) || typeof days !== 'string') return false;
  const enabledDays = new Set(
      days.split(',').map(day => day.trim().toLowerCase()));
  const currentDay = now.getDay();
  const previousDay = (currentDay + 6) % 7;
  const currentMinutes = now.getHours() * 60 + now.getMinutes();

  return times.split(',').some(range => {
    const [startText, endText] = range.trim().split('-');
    const start = focusParseClock(startText, now);
    const end = focusParseClock(endText, now);
    if (!start || !end) return false;
    const startMinutes = start.getHours() * 60 + start.getMinutes();
    const endMinutes = end.getHours() * 60 + end.getMinutes();
    if (startMinutes === endMinutes) {
      return enabledDays.has(FOCUS_DAYS[currentDay]);
    }
    if (startMinutes < endMinutes) {
      return enabledDays.has(FOCUS_DAYS[currentDay]) &&
          currentMinutes >= startMinutes && currentMinutes < endMinutes;
    }
    if (currentMinutes >= startMinutes) {
      return enabledDays.has(FOCUS_DAYS[currentDay]);
    }
    return currentMinutes < endMinutes &&
        enabledDays.has(FOCUS_DAYS[previousDay]);
  });
}

function focusGenerateCode(length = 8) {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const values = new Uint32Array(length);
  crypto.getRandomValues(values);
  return Array.from(values, value => alphabet[value % alphabet.length]).join('');
}
