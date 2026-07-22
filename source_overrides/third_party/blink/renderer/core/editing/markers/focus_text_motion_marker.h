// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_H_

#include <optional>

#include "base/time/time.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/editing/markers/document_marker.h"
#include "third_party/blink/renderer/platform/wtf/casting.h"

namespace blink {

// A short-lived, browser-owned marker for text inserted by an editing command.
// It carries only transient paint state: the inserted text remains in Blink's
// normal shaping and layout pipeline and is never copied into an overlay.
class CORE_EXPORT FocusTextMotionMarker final : public DocumentMarker {
 public:
  enum class Kind {
    kInsertion,
    kDeletionSettle,
  };

  FocusTextMotionMarker(unsigned start_offset,
                        unsigned end_offset,
                        Kind kind = Kind::kInsertion,
                        base::TimeDelta start_delay = base::TimeDelta());
  FocusTextMotionMarker(const FocusTextMotionMarker&) = delete;
  FocusTextMotionMarker& operator=(const FocusTextMotionMarker&) = delete;

  MarkerType GetType() const final;

  float Opacity() const { return opacity_; }
  float TranslationInline() const { return translation_inline_; }
  float TranslationY() const { return translation_y_; }

  // Advances this marker using the document animation-frame clock. Returns
  // true once the reveal is complete.
  bool UpdateOpacity(base::TimeTicks tick);

 private:
  const Kind kind_;
  const base::TimeDelta start_delay_;
  std::optional<base::TimeTicks> animation_start_;
  float opacity_ = 0.12f;
  float translation_inline_ = 0.0f;
  float translation_y_ = 3.0f;
};

template <>
struct DowncastTraits<FocusTextMotionMarker> {
  static bool AllowFrom(const DocumentMarker& marker) {
    return marker.GetType() == DocumentMarker::kFocusTextMotion;
  }
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_H_
