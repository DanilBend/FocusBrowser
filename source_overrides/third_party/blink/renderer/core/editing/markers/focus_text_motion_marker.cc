// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.h"

#include <algorithm>

#include "ui/gfx/geometry/cubic_bezier.h"

namespace blink {

namespace {

// This is the original Focus Browser settle: the new grapheme starts three
// physical CSS pixels below its final baseline and eases into place. Neither
// the line box nor any other grapheme moves.
constexpr base::TimeDelta kRevealDuration = base::Milliseconds(180);
constexpr float kInitialOpacity = 0.12f;
constexpr float kInitialTranslationY = 3.0f;
constexpr float kDeletionInitialTranslationInline = 3.0f;

double RevealProgress(double progress) {
  // Matches cubic-bezier(0.22, 1, 0.36, 1) from the original NTP animation.
  static const gfx::CubicBezier curve(0.22, 1.0, 0.36, 1.0);
  return curve.Solve(progress);
}

float RevealOpacity(double eased) {
  return static_cast<float>(kInitialOpacity + (1.0f - kInitialOpacity) * eased);
}

}  // namespace

FocusTextMotionMarker::FocusTextMotionMarker(unsigned start_offset,
                                             unsigned end_offset,
                                             Kind kind,
                                             base::TimeDelta start_delay)
    : DocumentMarker(start_offset, end_offset),
      kind_(kind),
      start_delay_(start_delay) {
  if (kind_ == Kind::kDeletionSettle) {
    opacity_ = 1.0f;
    translation_inline_ = kDeletionInitialTranslationInline;
    translation_y_ = 0.0f;
  }
}

DocumentMarker::MarkerType FocusTextMotionMarker::GetType() const {
  return DocumentMarker::kFocusTextMotion;
}

bool FocusTextMotionMarker::UpdateOpacity(base::TimeTicks tick) {
  if (!animation_start_) {
    animation_start_ = tick + start_delay_;
  }
  if (tick < *animation_start_) {
    return false;
  }
  const double progress =
      std::clamp((tick - *animation_start_) / kRevealDuration, 0.0, 1.0);
  const double eased = RevealProgress(progress);
  if (kind_ == Kind::kDeletionSettle) {
    translation_inline_ =
        static_cast<float>(kDeletionInitialTranslationInline * (1.0 - eased));
  } else {
    opacity_ = RevealOpacity(eased);
    translation_y_ = static_cast<float>(kInitialTranslationY * (1.0 - eased));
  }
  return progress >= 1.0;
}

}  // namespace blink
