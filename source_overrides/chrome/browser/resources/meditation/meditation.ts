// Copyright 2026 The Focus Browser Authors
// You can use, redistribute, and/or modify this source code under
// the terms of the GPL-3.0 license that can be found in the LICENSE file.

import {loadTimeData} from 'chrome://resources/js/load_time_data.js';

const VIDEO_URL = 'https://www.youtube.com/watch?v=R2K7ZHsnypI';

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
  const loadButton =
      requiredElement<HTMLButtonElement>('load-video');
  const status = requiredElement<HTMLElement>('connection-status');

  loadButton.addEventListener('click', () => {
    status.textContent = 'Видео открыто в новой вкладке YouTube.';
    window.open(VIDEO_URL, '_blank', 'noopener,noreferrer');
  });
});
