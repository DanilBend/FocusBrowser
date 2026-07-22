// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_LIST_IMPL_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_LIST_IMPL_H_

#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/editing/markers/highlight_pseudo_marker_list_impl.h"

namespace blink {

// Uses the overlapping marker editor because successive editing operations can
// replace a range before the preceding character reveal has completed.
class CORE_EXPORT FocusTextMotionMarkerListImpl final
    : public HighlightPseudoMarkerListImpl {
 public:
  FocusTextMotionMarkerListImpl() = default;
  FocusTextMotionMarkerListImpl(const FocusTextMotionMarkerListImpl&) = delete;
  FocusTextMotionMarkerListImpl& operator=(
      const FocusTextMotionMarkerListImpl&) = delete;

  DocumentMarker::MarkerType MarkerType() const final;
  void MergeOverlappingMarkers() final;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_EDITING_MARKERS_FOCUS_TEXT_MOTION_MARKER_LIST_IMPL_H_
