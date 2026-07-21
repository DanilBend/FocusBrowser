// Render exact-size logo crops and a contact sheet through Chrome CDP.

import { writeFile } from 'node:fs/promises';

const [ portArg, outputDir ] = process.argv.slice(2);
const port = Number.parseInt(portArg, 10);
if ( Number.isFinite(port) === false || !outputDir ) {
    throw new Error('Usage: node render_focus_logo_sizes.mjs <port> <output-dir>');
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let page;
for ( let attempt = 0; attempt < 80; attempt += 1 ) {
    try {
        const targets = await fetch(`http://127.0.0.1:${port}/json/list`)
            .then(response => response.json());
        page = targets.find(target =>
            target.type === 'page' &&
            target.url.includes('focus-logo-size-preview.html')
        );
        if ( page ) { break; }
    } catch {
    }
    await delay(100);
}
if ( !page ) { throw new Error('Logo preview page did not appear'); }

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
    width: 1000,
    height: 460,
    deviceScaleFactor: 1,
    mobile: false,
});
await delay(200);

const screenshot = async (path, clip) => {
    const { data } = await send('Page.captureScreenshot', {
        format: 'png',
        fromSurface: true,
        ...(clip ? { clip: { ...clip, scale: 1 } } : {}),
    });
    await writeFile(path, Buffer.from(data, 'base64'));
};

await screenshot(`${outputDir}/focus-logo-sizes-contact.png`);
for ( const size of [ 16, 24, 32, 44, 256 ] ) {
    const response = await send('Runtime.evaluate', {
        expression: `(() => {
            const rect = document.querySelector(
                'figure[data-size="${size}"] svg'
            ).getBoundingClientRect();
            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
        })()`,
        returnByValue: true,
    });
    if ( response.exceptionDetails ) {
        throw new Error(response.exceptionDetails.text);
    }
    await screenshot(
        `${outputDir}/focus-logo-${size}px.png`,
        response.result.value
    );
}

socket.close();
