// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/markers/focus_text_motion_marker_list_impl.h"

namespace blink {

DocumentMarker::MarkerType FocusTextMotionMarkerListImpl::MarkerType() const {
  return DocumentMarker::kFocusTextMotion;
}

void FocusTextMotionMarkerListImpl::MergeOverlappingMarkers() {
  // Timing is per insertion. Merging would make a newly typed character inherit
  // an older character's progress.
  NOTREACHED();
}

}  // namespace blink
