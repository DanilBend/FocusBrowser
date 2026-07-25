// Copyright 2026 Focus Browser contributors.

import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bundlePath = path.join(root, 'dist', 'focus_ghostery_adblocker.js');
const metadataPath = path.join(
  root,
  'dist',
  'focus_ghostery_adblocker.meta.json',
);
const source = await fs.readFile(bundlePath, 'utf8');
const metadata = JSON.parse(await fs.readFile(metadataPath, 'utf8'));
assert.equal(metadata.upstream.package, '@ghostery/adblocker');
assert.equal(metadata.upstream.version, '2.18.1');
assert.equal(
  metadata.upstream.commit,
  '67ef23276e93ebc5dd4621cc9df2b09ad9f490d7',
);
assert.match(metadata.upstream.integrity, /^sha512-/);
assert.equal(metadata.build.platform, 'browser');
assert.ok(metadata.inputs.length > 0);
const context = vm.createContext({
  ArrayBuffer,
  DataView,
  TextDecoder,
  TextEncoder,
  URL,
  URLSearchParams,
  Uint8Array,
  atob,
  btoa,
  clearTimeout,
  console,
  setTimeout,
});
new vm.Script(source, { filename: bundlePath }).runInContext(context);

const api = context.FocusGhosteryAdblocker;
assert.ok(api, 'bundle did not create FocusGhosteryAdblocker');
assert.equal(api.status().upstreamVersion, '2.18.1');
assert.equal(api.status().upstreamCommit, '67ef23276e93ebc5dd4621cc9df2b09ad9f490d7');
assert.equal(api.status().initialized, false);

api.initializeFromFilterText(`
  ||ads.example^$script
  @@||ads.example/allowed.js$script
  example.com##.ad-slot
`);
assert.equal(api.status().initialized, true);

const blocked = api.matchRawDetails({
  url: 'https://ads.example/banner.js',
  hostname: 'ads.example',
  domain: 'ads.example',
  sourceUrl: 'https://example.com/',
  sourceHostname: 'example.com',
  sourceDomain: 'example.com',
  type: 'script',
});
assert.equal(blocked.validInput, true);
assert.equal(blocked.matched, true);

const allowed = api.matchRawDetails({
  url: 'https://ads.example/allowed.js',
  hostname: 'ads.example',
  domain: 'ads.example',
  sourceUrl: 'https://example.com/',
  sourceHostname: 'example.com',
  sourceDomain: 'example.com',
  type: 'script',
});
assert.equal(allowed.validInput, true);
assert.equal(allowed.matched, false);
assert.equal(allowed.hasException, true);

const cosmetics = api.cosmeticsRawDetails({
  url: 'https://example.com/',
  hostname: 'example.com',
  domain: 'example.com',
  type: 'document',
  classes: ['ad-slot'],
});
assert.equal(cosmetics.validInput, true);
assert.equal(cosmetics.active, true);
assert.match(cosmetics.styles, /\.ad-slot/);
assert.deepEqual(Array.from(cosmetics.scripts), []);
assert.deepEqual(Array.from(cosmetics.extended), []);

const serialized = api.serialize();
assert.ok(serialized instanceof Uint8Array);
assert.ok(serialized.byteLength > 32);
api.reset();
assert.equal(api.status().initialized, false);
api.initializeFromSerialized(serialized);
assert.equal(api.matchRawDetails({
  url: 'https://ads.example/banner.js',
  sourceUrl: 'https://example.com/',
  type: 'script',
}).matched, true);

const invalid = api.matchRawDetails({ type: 'script' });
assert.equal(invalid.validInput, false);
assert.equal(invalid.matched, false);

console.log('Ghostery browser bundle smoke test: PASS');
