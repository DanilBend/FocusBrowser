// Render the FocusBlock popup directly from the source tree through Chrome's
// DevTools protocol. This is intentionally independent of extension APIs so
// that visual checks do not require rebuilding Chromium first.

import { writeFile } from 'node:fs/promises';

const [ portArg, compactPath, expandedPath, widthArg ] = process.argv.slice(2);
const port = Number.parseInt(portArg, 10);
const viewportWidth = Number.parseInt(widthArg || '400', 10);

if (
    Number.isFinite(port) === false ||
    Number.isFinite(viewportWidth) === false ||
    !compactPath ||
    !expandedPath
) {
    throw new Error(
        'Usage: node render_focusblock_preview.mjs <port> <compact.png> <expanded.png>'
    );
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

let page;
for ( let attempt = 0; attempt < 80; attempt += 1 ) {
    try {
        const targets = await fetch(`http://127.0.0.1:${port}/json/list`)
            .then(response => response.json());
        page = targets.find(target =>
            target.type === 'page' && target.url.includes('popup-fenix.html')
        );
        if ( page ) { break; }
    } catch {
    }
    await delay(100);
}

if ( !page ) {
    throw new Error('FocusBlock preview page did not appear');
}

const socket = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
});

let sequence = 0;
const pending = new Map();
socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if ( !message.id ) { return; }
    const callback = pending.get(message.id);
    if ( !callback ) { return; }
    pending.delete(message.id);
    if ( message.error ) {
        callback.reject(new Error(message.error.message));
    } else {
        callback.resolve(message.result);
    }
});

const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++sequence;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
});

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', {
    width: viewportWidth,
    height: 600,
    deviceScaleFactor: 1,
    mobile: false,
});

const previewState = String.raw`
    document.documentElement.classList.add('desktop');
    document.documentElement.dataset.motion = 'off';
    document.body.classList.remove('off', 'focusGlobalOff');
    const hostname = document.querySelector('#hostname');
    hostname.children[0].textContent = '';
    hostname.children[1].textContent = 'focusbrowser.ru';
    const setStat = (key, value) => {
        const label = document.querySelector('[data-focus-stat="' + key + '"]');
        if ( label && label.nextElementSibling ) {
            label.nextElementSibling.textContent = value;
        }
    };
    setStat('page', '28 (41%)');
    setStat('total', '12 486');
    document.querySelector('#focusDomainsValue').textContent = '6 из 14';
    document.querySelector('#version').textContent = '1.0';
    document.querySelector('#gotoPick').classList.add('canPick');
    document.querySelector('#no-popups').classList.add('on');
    document.querySelector('#no-popups').setAttribute('aria-pressed', 'true');
    document.querySelector('#focusSiteOptions').open = false;
    window.scrollTo(0, 0);
`;

const evaluated = await send('Runtime.evaluate', {
    expression: previewState,
    returnByValue: true,
});
if ( evaluated.exceptionDetails ) {
    throw new Error(evaluated.exceptionDetails.text);
}

await delay(250);

const capture = async outputPath => {
    const { data } = await send('Page.captureScreenshot', {
        format: 'png',
        fromSurface: true,
    });
    await writeFile(outputPath, Buffer.from(data, 'base64'));
};

await capture(compactPath);

await send('Runtime.evaluate', {
    expression: `
        document.querySelector('#focusSiteOptions').open = true;
        window.scrollTo(0, 0);
    `,
});
await delay(200);
await capture(expandedPath);

socket.close();
