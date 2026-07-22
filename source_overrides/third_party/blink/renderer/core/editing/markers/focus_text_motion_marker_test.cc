// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.h"

#include "testing/gtest/include/gtest/gtest.h"

namespace blink {

TEST(FocusTextMotionMarkerTest, CommittedGlyphInkIsAlwaysCrisp) {
  auto* insertion = MakeGarbageCollected<FocusTextMotionMarker>(3, 4);
  auto* deletion = MakeGarbageCollected<FocusTextMotionMarker>(
      4, 5, FocusTextMotionMarker::Kind::kDeletionSettle);

  for (auto* marker : {insertion, deletion}) {
    EXPECT_EQ(DocumentMarker::kFocusTextMotion, marker->GetType());
    EXPECT_FLOAT_EQ(1.0f, marker->Opacity());
    EXPECT_FLOAT_EQ(0.0f, marker->TranslationInline());
    EXPECT_FLOAT_EQ(0.0f, marker->TranslationY());
    EXPECT_TRUE(marker->UpdateOpacity(base::TimeTicks() + base::Seconds(1)));
  }
}

TEST(FocusTextMotionMarkerTest, PasteDelayNeverDelaysCommittedInk) {
  auto* marker = MakeGarbageCollected<FocusTextMotionMarker>(
      4, 6, FocusTextMotionMarker::Kind::kInsertion, base::Milliseconds(48));

  EXPECT_FLOAT_EQ(1.0f, marker->Opacity());
  EXPECT_FLOAT_EQ(0.0f, marker->TranslationInline());
  EXPECT_FLOAT_EQ(0.0f, marker->TranslationY());
  EXPECT_TRUE(marker->UpdateOpacity(base::TimeTicks() + base::Seconds(1)));
}

}  // namespace blink
