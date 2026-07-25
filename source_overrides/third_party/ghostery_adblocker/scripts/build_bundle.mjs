// Copyright 2026 Focus Browser contributors.

import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { build, version as esbuildVersion } from 'esbuild';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const upstreamMetadata = JSON.parse(
  await fs.readFile(path.join(root, 'UPSTREAM.json'), 'utf8'),
);
const installedPackage = JSON.parse(
  await fs.readFile(
    path.join(root, 'node_modules', '@ghostery', 'adblocker', 'package.json'),
    'utf8',
  ),
);

if (installedPackage.version !== upstreamMetadata.version) {
  throw new Error(
    `locked Ghostery version mismatch: expected ${upstreamMetadata.version}, ` +
      `got ${installedPackage.version}`,
  );
}

const outputDir = path.join(root, 'dist');
const outputFile = path.join(outputDir, 'focus_ghostery_adblocker.js');
await fs.mkdir(outputDir, { recursive: true });

const buildResult = await build({
  entryPoints: [path.join(root, 'src', 'focus_ghostery_adblocker.js')],
  outfile: outputFile,
  bundle: true,
  charset: 'utf8',
  format: 'iife',
  globalName: 'FocusGhosteryAdblocker',
  legalComments: 'inline',
  logLevel: 'info',
  minify: false,
  platform: 'browser',
  sourcemap: false,
  target: ['chrome120'],
  treeShaking: true,
  metafile: true,
  banner: {
    js:
      `/*! Focus Browser adapter for @ghostery/adblocker ${upstreamMetadata.version}; ` +
      `upstream ${upstreamMetadata.commit}; MPL-2.0; see LICENSE and NOTICE. */`,
  },
});

const bundle = await fs.readFile(outputFile);
const digest = crypto.createHash('sha256').update(bundle).digest('hex');
await fs.writeFile(
  path.join(outputDir, 'focus_ghostery_adblocker.sha256'),
  `${digest}  focus_ghostery_adblocker.js\n`,
  'utf8',
);

const normalizePath = (inputPath) => {
  const relativePath = path.isAbsolute(inputPath)
    ? path.relative(root, inputPath)
    : inputPath;
  return relativePath.replaceAll('\\', '/');
};
const inputs = Object.entries(buildResult.metafile.inputs)
  .map(([inputPath, details]) => ({
    path: normalizePath(inputPath),
    bytes: details.bytes,
  }))
  .sort((left, right) => {
    if (left.path < right.path) {
      return -1;
    }
    return left.path > right.path ? 1 : 0;
  });
const metadata = {
  schema_version: 1,
  upstream: {
    package: upstreamMetadata.name,
    version: upstreamMetadata.version,
    tag: upstreamMetadata.tag,
    commit: upstreamMetadata.commit,
    integrity: upstreamMetadata.npmIntegrity,
  },
  build: {
    tool: 'esbuild',
    version: esbuildVersion,
    format: 'iife',
    platform: 'browser',
    target: 'chrome120',
    minified: false,
  },
  output: {
    file: 'focus_ghostery_adblocker.js',
    bytes: bundle.length,
    sha256: digest,
  },
  inputs,
};
await fs.writeFile(
  path.join(outputDir, 'focus_ghostery_adblocker.meta.json'),
  `${JSON.stringify(metadata, null, 2)}\n`,
  'utf8',
);
console.log(`Ghostery bundle SHA-256: ${digest}`);
