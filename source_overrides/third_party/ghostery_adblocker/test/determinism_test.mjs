// Copyright 2026 Focus Browser contributors.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bundlePath = path.join(root, 'dist', 'focus_ghostery_adblocker.js');
const metadataPath = path.join(
  root,
  'dist',
  'focus_ghostery_adblocker.meta.json',
);
const buildScript = path.join(root, 'scripts', 'build_bundle.mjs');

function buildOnce() {
  const result = spawnSync(process.execPath, [buildScript], {
    cwd: root,
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
}

function digest(bytes) {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

buildOnce();
const first = await fs.readFile(bundlePath);
const firstMetadata = await fs.readFile(metadataPath);
buildOnce();
const second = await fs.readFile(bundlePath);
const secondMetadata = await fs.readFile(metadataPath);
assert.ok(first.equals(second), 'two clean bundle invocations produced different bytes');
assert.ok(
  firstMetadata.equals(secondMetadata),
  'two clean bundle invocations produced different metadata',
);
console.log(`Ghostery deterministic bundle test: PASS (${digest(second)})`);
