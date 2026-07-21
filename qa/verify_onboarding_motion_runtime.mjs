import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';

const [chromePath, distPath, outputDirectory] = process.argv.slice(2);
assert.ok(chromePath && distPath && outputDirectory,
          'Usage: node verify_onboarding_motion_runtime.mjs <chrome.exe> <dist> <output-dir>');

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require('playwright');
} catch {
  const modules = process.env.CODEX_NODE_MODULES;
  assert.ok(modules, 'playwright not found; set CODEX_NODE_MODULES');
  playwright = require(path.join(modules, 'playwright'));
}

await fs.mkdir(outputDirectory, {recursive: true});
const sourceRoot = path.resolve(distPath, '..');
const viteEntry = path.join(
    sourceRoot, 'node_modules', 'vite', 'dist', 'node', 'index.js');
const {createServer} = await import(pathToFileURL(viteEntry).href);
const devServer = await createServer({
  root: sourceRoot,
  logLevel: 'error',
  server: {host: '127.0.0.1', port: 0},
});
await devServer.listen();
const previewUrl = devServer.resolvedUrls?.local?.[0];
assert.ok(previewUrl, 'Vite preview URL is unavailable');

const browser = await playwright.chromium.launch({
  executablePath: chromePath,
  headless: true,
  args: [
    '--allow-file-access-from-files',
    '--disable-background-networking',
    '--disable-default-apps',
    '--no-first-run',
  ],
});

const errors = [];
const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
page.on('console', message => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});

try {
  await page.goto(previewUrl, {
    waitUntil: 'networkidle',
  });
  await page.waitForTimeout(750);
  if (await page.locator('#welcome-buttons button.primary').count() === 0) {
    await page.screenshot({
      path: path.join(outputDirectory, '00-load-failure.png'),
    });
    throw new Error(
        `Onboarding did not render: ${await page.locator('body').innerText()}\n` +
        errors.join('\n'));
  }
  await page.locator('#welcome-buttons button.primary').waitFor();
  await page.screenshot({
    path: path.join(outputDirectory, '01-welcome.png'),
    animations: 'allow',
  });

  await page.locator('#welcome-buttons button.primary').click();
  const focusPage = page.locator('#focus-page.visible');
  await focusPage.waitFor();
  await page.waitForTimeout(500);
  const motionToggle = focusPage.getByRole('switch', {
    name: /Плавные анимации/,
  });
  await motionToggle.waitFor();
  assert.equal(await page.locator('html').getAttribute('data-motion'), 'on');
  assert.equal(await motionToggle.getAttribute('aria-checked'), 'true');
  assert.equal(await page.evaluate(
      () => getComputedStyle(document.body, '::after').animationName),
      'focus-water-drift');

  await page.screenshot({
    path: path.join(outputDirectory, '02-focus-settings-motion-on.png'),
    animations: 'allow',
  });

  await motionToggle.click();
  await page.waitForFunction(
      () => document.documentElement.dataset.motion === 'off');
  assert.equal(await page.locator('html').getAttribute('data-motion'), 'off');
  assert.equal(await motionToggle.getAttribute('aria-checked'), 'false');
  assert.equal(await page.evaluate(
      () => getComputedStyle(document.body, '::after').animationName),
      'none');

  await motionToggle.click();
  await page.waitForFunction(
      () => document.documentElement.dataset.motion === 'on');
  assert.equal(await page.locator('html').getAttribute('data-motion'), 'on');
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.waitForFunction(
      () => document.documentElement.dataset.motion === 'off');
  assert.equal(await page.locator('html').getAttribute('data-motion'), 'off');
  assert.equal(await page.evaluate(
      () => getComputedStyle(document.body, '::after').animationName),
      'none');

  await page.emulateMedia({reducedMotion: 'no-preference'});
  await page.waitForFunction(
      () => document.documentElement.dataset.motion === 'on');
  assert.equal(await page.locator('html').getAttribute('data-motion'), 'on');
  assert.deepEqual(errors, [], errors.join('\n'));

  const report = {
    status: 'PASS',
    viewport: {width: 1440, height: 1000},
    motionToggleVisible: true,
    userMotionOffStopsAnimations: true,
    reducedMotionStopsAnimations: true,
    consoleErrors: errors,
  };
  await fs.writeFile(
      path.join(outputDirectory, 'report.json'),
      `${JSON.stringify(report, null, 2)}\n`,
      'utf8');
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
  await devServer.close();
}
