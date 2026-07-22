// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.h"

namespace blink {

FocusTextMotionMarker::FocusTextMotionMarker(unsigned start_offset,
                                             unsigned end_offset,
                                             Kind,
                                             base::TimeDelta)
    : DocumentMarker(start_offset, end_offset) {}

DocumentMarker::MarkerType FocusTextMotionMarker::GetType() const {
  return DocumentMarker::kFocusTextMotion;
}

bool FocusTextMotionMarker::UpdateOpacity(base::TimeTicks) {
  // Text is committed and painted sharply on the first frame. Returning true
  // removes this compatibility marker without scheduling a glyph animation.
  return true;
}

}  // namespace blink
