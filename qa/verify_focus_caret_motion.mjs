#!/usr/bin/env node

// Static contract checks for the Blink-native Focus caret glide.

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const projectRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)), '..');
const overridesRoot = path.join(projectRoot, 'source_overrides');
const checkoutRoot = path.join(projectRoot, 'build', 'src');
const hasCheckout = fs.existsSync(checkoutRoot);
const activeRoot = hasCheckout ? checkoutRoot : overridesRoot;
const digest = file => crypto.createHash('sha256')
    .update(fs.readFileSync(file)).digest('hex');
const read = relativePath =>
  fs.readFileSync(path.join(activeRoot, relativePath), 'utf8');

const files = [
  'third_party/blink/renderer/core/editing/caret_display_item_client.h',
  'third_party/blink/renderer/core/editing/caret_display_item_client.cc',
];

for (const relativePath of files) {
  const active = path.join(activeRoot, relativePath);
  const override = path.join(overridesRoot, relativePath);
  assert.ok(fs.existsSync(active), `missing active file: ${relativePath}`);
  assert.ok(fs.existsSync(override), `missing override: ${relativePath}`);
  if (hasCheckout) {
    assert.equal(digest(active), digest(override),
                 `active/override drift: ${relativePath}`);
  }
}

const header = read(files[0]);
const implementation = read(files[1]);

for (const required of [
  'animated_local_rect_',
  'focus_caret_motion_from_',
  'focus_caret_motion_to_',
  'focus_caret_motion_start_',
  'ContinueFocusCaretMotion',
]) {
  assert.ok(header.includes(required), `header contract missing: ${required}`);
}

assert.match(
    implementation,
    /kFocusCaretMotionDuration\s*=\s*base::Milliseconds\(90\)/);
assert.match(
    implementation,
    /gfx::CubicBezier curve\(0\.22, 1\.0, 0\.36, 1\.0\)/);
assert.match(implementation, /RequestAnimationFrame/);
assert.match(implementation, /curve\.Solve\(linear_progress\)/);

for (const gate of [
  'caret_shape == CaretShape::kBar',
  'GetFocusTextMotionEnabled()',
  'FocusCaretMotionPrefersReducedMotion',
  'IsInPasswordField(caret_position.GetPosition())',
  '!layout_block_changed',
]) {
  assert.ok(implementation.includes(gate), `safety gate missing: ${gate}`);
}

// Editing routinely replaces an inline PhysicalBoxFragment even when the
// caret remains in the same painter-block coordinate space. That pointer
// churn must not cancel the glide; a changed LayoutBlock is the real
// cross-coordinate-space safety boundary.
assert.doesNotMatch(implementation, /const bool box_fragment_changed/);
assert.match(
    implementation,
    /focus_caret_motion_allowed_\s*&&\s*!layout_block_changed\s*&&\s*!visual_start\.IsEmpty\(\)/);

assert.match(
    implementation,
    /local_rect_\s*=\s*new_local_rect;[\s\S]{0,500}StartFocusCaretMotion\(visual_start, new_local_rect\)/);
assert.match(
    implementation,
    /PaintCaret\([\s\S]{0,400}focus_caret_motion_running_[\s\S]{0,200}animated_local_rect_[\s\S]{0,120}local_rect_/);
assert.match(
    implementation,
    /RecordSelection\([\s\S]{0,300}PhysicalRect drawing_rect = local_rect_/);
assert.match(
    implementation,
    /if \(!active\)[\s\S]{0,100}CancelFocusCaretMotion\(\)/);
assert.doesNotMatch(
    implementation,
    /DispatchEvent|ScriptState|V8[A-Z]|HTMLInputElement::setValue/);

console.log(JSON.stringify({
  ok: true,
  implementation: 'Blink native paint-only caret glide',
  durationMs: 90,
  easing: 'cubic-bezier(.22,1,.36,1)',
  geometry: 'selection, IME, accessibility and events use synchronous local_rect_',
  reducedMotion: true,
  passwordPolicy: 'disabled with Blink IsInPasswordField',
  crossContextPolicy: 'snaps across layout blocks; tolerates inline fragment replacement',
}, null, 2));
