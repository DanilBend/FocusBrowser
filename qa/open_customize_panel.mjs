#!/usr/bin/env node

import assert from 'node:assert/strict';

const port = Number(process.argv[2] || 9351);
const endpoint = `http://127.0.0.1:${port}`;
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

const targets = await fetch(`${endpoint}/json/list`).then(response => response.json());
const ntp = targets.find(target => target.type === 'page' &&
    (target.url.startsWith('chrome://newtab/') ||
     target.url.startsWith('chrome://new-tab-page/')) &&
    target.webSocketDebuggerUrl);
assert.ok(ntp, 'No Focus new-tab target is open');

const socket = new WebSocket(ntp.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, {once: true});
  socket.addEventListener('error', reject, {once: true});
});

let nextId = 1;
const pending = new Map();
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.error) request.reject(new Error(message.error.message));
  else request.resolve(message.result ?? {});
});
const send = (method, params = {}) => {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    socket.send(JSON.stringify({id, method, params}));
  });
};

let clicked = false;
for (let attempt = 0; attempt < 100 && !clicked; ++attempt) {
  const response = await send('Runtime.evaluate', {
    expression: `(() => {
      const first = document.querySelector('ntp-app')?.shadowRoot;
      if (!first) return false;
      const roots = [first];
      while (roots.length) {
        const root = roots.pop();
        const button = root.querySelector('#customizeButton');
        if (button) {
          button.click();
          return true;
        }
        for (const element of root.querySelectorAll('*')) {
          if (element.shadowRoot) roots.push(element.shadowRoot);
        }
      }
      return false;
    })()`,
    returnByValue: true,
  });
  clicked = response.result?.value === true;
  if (!clicked) await delay(100);
}
socket.close();
assert.equal(clicked, true, 'Could not click the Focus customize button');

let panel = null;
for (let attempt = 0; attempt < 100 && !panel; ++attempt) {
  const current = await fetch(`${endpoint}/json/list`).then(response => response.json());
  panel = current.find(target =>
      target.url.includes('customize-chrome-side-panel') &&
      target.type !== 'page' && target.webSocketDebuggerUrl);
  if (!panel) await delay(100);
}
assert.ok(panel, 'Customize side-panel target did not open');
console.log(JSON.stringify({clicked, targetId: panel.id, url: panel.url}));
