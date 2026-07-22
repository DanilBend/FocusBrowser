// Copyright 2026 The Focus Browser Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

import {loadTimeData} from 'chrome://resources/js/load_time_data.js';

const VIDEO_URL = 'https://www.youtube.com/watch?v=R2K7ZHsnypI';

const applicationLocale = globalThis.navigator?.language ||
    loadTimeData.getString('applicationLocale') || 'en-US';
const normalizedLocale = applicationLocale.toLowerCase();
const useRussianUi = normalizedLocale === 'ru' ||
    normalizedLocale.startsWith('ru-') || normalizedLocale.startsWith('ru_');

document.documentElement.lang = applicationLocale;
if (useRussianUi) {
  document.title = 'Медитация · Focus Browser';
}

const RUSSIAN_TEXT: Readonly<Record<string, string>> = {
  'section-name': 'Медитация',
  'eyebrow': 'Пауза для ясного ума',
  'page-title-first': 'Остановитесь.',
  'page-title-second': 'Верните внимание.',
  'hero-copy':
      'Несколько спокойных минут помогают убрать лишний шум и снова сосредоточиться на главном.',
  'card-label': 'Практика дыхания',
  'video-title':
      'Учу вас дышать: успех, власть, сила. Измени свою жизнь навсегда. Мориарти.',
  'video-description':
      'Устройтесь удобно, уберите уведомления и оставьте себе это время без переключений.',
  'breathing-label': 'вдох',
  'privacy-title': 'Начните медитацию, когда будете готовы',
  'privacy-description':
      'Focus Browser откроет оригинальное видео в YouTube. Так оно воспроизводится без ошибки встроенного плеера; сервис сможет получить ваш IP-адрес и сведения об устройстве.',
  'load-video-label': 'Открыть видео и начать',
  'youtube-link-label': 'Открыть на YouTube',
  'footer-guidance': 'Дышите медленнее. Делайте только одно дело.',
  'footer-privacy': 'Без фоновой загрузки видео',
};

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const updateMotionState = () => {
  document.documentElement.dataset['motion'] =
      loadTimeData.getBoolean('focusMotionEnabled') && !reducedMotion.matches ?
      'on' :
      'off';
};

updateMotionState();
reducedMotion.addEventListener('change', updateMotionState);

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: ${id}`);
  }
  return element as T;
}

document.addEventListener('DOMContentLoaded', () => {
  if (useRussianUi) {
    for (const [id, value] of Object.entries(RUSSIAN_TEXT)) {
      requiredElement(id).textContent = value;
    }
  }

  const loadButton =
      requiredElement<HTMLButtonElement>('load-video');
  const status = requiredElement<HTMLElement>('connection-status');

  loadButton.addEventListener('click', () => {
    status.textContent = useRussianUi ?
        'Видео открыто в новой вкладке YouTube.' :
        'The video opened in a new YouTube tab.';
    window.open(VIDEO_URL, '_blank', 'noopener,noreferrer');
  });
});
