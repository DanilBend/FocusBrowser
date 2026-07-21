import { writeFile } from 'node:fs/promises';

const [port, url, outputPath, action, widthArg, heightArg] = process.argv.slice(2);
if (!port || !url || !outputPath) {
  throw new Error('Usage: node capture_focus_page.mjs <port> <url> <output.png>');
}

const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) =>
  response.json(),
);
const target = targets.find((candidate) => candidate.type === 'page');
if (!target?.webSocketDebuggerUrl) {
  throw new Error('No debuggable page target found');
}

const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true });
  socket.addEventListener('error', reject, { once: true });
});

let nextId = 1;
const pending = new Map();
socket.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) {
    return;
  }
  const { resolve, reject } = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) {
    reject(new Error(`${message.error.code}: ${message.error.message}`));
  } else {
    resolve(message.result ?? {});
  }
});

function send(method, params = {}) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', {
  width: Number(widthArg || 1440),
  height: Number(heightArg || 1000),
  deviceScaleFactor: 1,
  mobile: false,
});
if (url !== '-') {
  await send('Page.navigate', { url });
}

if (action === 'click-primary') {
  await send('Runtime.evaluate', {
    expression:
      '(() => { const button = document.querySelector("button.primary"); if (!button) throw new Error("Primary button not found"); button.click(); return button.textContent; })()',
    awaitPromise: true,
    returnByValue: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 750));
}

if (action === 'click-footer-primary') {
  await send('Runtime.evaluate', {
    expression:
      '(() => { const button = document.querySelector("#setup-buttons button.primary"); if (!button) throw new Error("Footer primary button not found"); button.click(); return button.textContent; })()',
    awaitPromise: true,
    returnByValue: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 750));
}

if (action === 'scroll-bottom') {
  await send('Runtime.evaluate', {
    expression: 'window.scrollTo({top: document.documentElement.scrollHeight, behavior: "instant"})',
    returnByValue: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 500));
}

if (action === 'scroll-active-content') {
  await send('Runtime.evaluate', {
    expression:
      '(() => { const content = document.querySelector(".onboarding-page.visible .scrollable-page"); if (!content) throw new Error("Active scrollable content not found"); content.scrollTo({top: Math.min(260, content.scrollHeight - content.clientHeight), behavior: "instant"}); return content.scrollTop; })()',
    awaitPromise: true,
    returnByValue: true,
  });
  await new Promise((resolve) => setTimeout(resolve, 500));
}

let pageInfo = null;
for (let attempt = 0; attempt < 30; attempt++) {
  await new Promise((resolve) => setTimeout(resolve, 250));
  const evaluation = await send('Runtime.evaluate', {
    expression:
      '(() => { const rect = (selector) => { const element = document.querySelector(selector); if (!element) return null; const bounds = element.getBoundingClientRect(); return {x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height}; }; const content = document.querySelector(".onboarding-page.visible .scrollable-page"); return {ready: document.readyState, href: location.href, title: document.title, text: document.body?.innerText?.slice(0, 240) || "", innerHeight, scrollHeight: document.documentElement.scrollHeight, documentScrollY: scrollY, bodyScrollTop: document.body.scrollTop, layout: {shell: rect("#setup-shell"), sidebar: rect("#setup-sidebar"), stage: rect("#setup-stage"), footer: rect("#setup-buttons"), content: content ? {...rect(".onboarding-page.visible .scrollable-page"), scrollTop: content.scrollTop, scrollHeight: content.scrollHeight, clientHeight: content.clientHeight} : null}, buttons: [...document.querySelectorAll("button")].map((button) => { const bounds = button.getBoundingClientRect(); return {text: button.textContent.trim(), x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height}; })}; })()',
    returnByValue: true,
  });
  pageInfo = evaluation.result?.value ?? null;
  if (pageInfo?.ready === 'complete') {
    break;
  }
}

await new Promise((resolve) => setTimeout(resolve, 1000));
const screenshot = await send('Page.captureScreenshot', {
  format: 'png',
  captureBeyondViewport: false,
  fromSurface: true,
});
await writeFile(outputPath, Buffer.from(screenshot.data, 'base64'));
socket.close();

console.log(JSON.stringify({ outputPath, pageInfo }));
