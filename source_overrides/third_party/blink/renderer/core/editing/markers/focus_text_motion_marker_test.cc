// Copyright 2026 The Focus Browser Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/markers/focus_text_motion_marker.h"

#include "testing/gtest/include/gtest/gtest.h"

namespace blink {

TEST(FocusTextMotionMarkerTest, SettlesOnlyTransientPaintState) {
  auto* marker = MakeGarbageCollected<FocusTextMotionMarker>(3, 4);
  EXPECT_EQ(DocumentMarker::kFocusTextMotion, marker->GetType());
  EXPECT_EQ(3u, marker->StartOffset());
  EXPECT_EQ(4u, marker->EndOffset());
  EXPECT_GT(marker->Opacity(), 0.0f);
  EXPECT_LT(marker->Opacity(), 1.0f);
  EXPECT_FLOAT_EQ(3.0f, marker->TranslationY());

  const base::TimeTicks start = base::TimeTicks() + base::Seconds(1);
  EXPECT_FALSE(marker->UpdateOpacity(start));
  const float initial = marker->Opacity();
  const float initial_translation = marker->TranslationY();
  EXPECT_FALSE(marker->UpdateOpacity(start + base::Milliseconds(60)));
  EXPECT_GT(marker->Opacity(), initial);
  EXPECT_LT(marker->TranslationY(), initial_translation);
  EXPECT_FALSE(marker->UpdateOpacity(start + base::Milliseconds(179)));
  EXPECT_TRUE(marker->UpdateOpacity(start + base::Milliseconds(180)));
  EXPECT_FLOAT_EQ(1.0f, marker->Opacity());
  EXPECT_FLOAT_EQ(0.0f, marker->TranslationY());
}

TEST(FocusTextMotionMarkerTest, RapidInsertionsKeepIndependentTimelines) {
  auto* first = MakeGarbageCollected<FocusTextMotionMarker>(0, 1);
  auto* second = MakeGarbageCollected<FocusTextMotionMarker>(1, 2);
  const base::TimeTicks start = base::TimeTicks() + base::Seconds(1);

  EXPECT_FALSE(first->UpdateOpacity(start));
  EXPECT_FALSE(first->UpdateOpacity(start + base::Milliseconds(70)));
  const float first_before_second_insert = first->Opacity();

  // A later insertion starts from its own initial opacity. Advancing it must
  // not reset or complete the older glyph's reveal.
  EXPECT_FALSE(second->UpdateOpacity(start + base::Milliseconds(70)));
  EXPECT_LT(second->Opacity(), first_before_second_insert);
  EXPECT_FALSE(first->UpdateOpacity(start + base::Milliseconds(110)));
  EXPECT_FALSE(second->UpdateOpacity(start + base::Milliseconds(110)));
  EXPECT_GT(first->Opacity(), second->Opacity());
  EXPECT_GT(first->Opacity(), first_before_second_insert);

  EXPECT_TRUE(first->UpdateOpacity(start + base::Milliseconds(180)));
  EXPECT_FALSE(second->UpdateOpacity(start + base::Milliseconds(180)));
  EXPECT_TRUE(second->UpdateOpacity(start + base::Milliseconds(250)));
}

TEST(FocusTextMotionMarkerTest, PasteStaggerKeepsInitialSettleUntilDelay) {
  auto* marker = MakeGarbageCollected<FocusTextMotionMarker>(
      4, 6, FocusTextMotionMarker::Kind::kInsertion, base::Milliseconds(48));
  const base::TimeTicks start = base::TimeTicks() + base::Seconds(1);

  EXPECT_FALSE(marker->UpdateOpacity(start));
  EXPECT_FLOAT_EQ(0.12f, marker->Opacity());
  EXPECT_FLOAT_EQ(3.0f, marker->TranslationY());
  EXPECT_FALSE(marker->UpdateOpacity(start + base::Milliseconds(47)));
  EXPECT_FLOAT_EQ(0.12f, marker->Opacity());
  EXPECT_FLOAT_EQ(3.0f, marker->TranslationY());
  EXPECT_FALSE(marker->UpdateOpacity(start + base::Milliseconds(49)));
  EXPECT_GT(marker->Opacity(), 0.12f);
  EXPECT_LT(marker->TranslationY(), 3.0f);
  EXPECT_TRUE(marker->UpdateOpacity(start + base::Milliseconds(228)));
}

TEST(FocusTextMotionMarkerTest, DeletionSettlesInlineWithoutGhostText) {
  auto* marker = MakeGarbageCollected<FocusTextMotionMarker>(
      2, 3, FocusTextMotionMarker::Kind::kDeletionSettle);
  const base::TimeTicks start = base::TimeTicks() + base::Seconds(1);

  EXPECT_FLOAT_EQ(1.0f, marker->Opacity());
  EXPECT_FLOAT_EQ(3.0f, marker->TranslationInline());
  EXPECT_FLOAT_EQ(0.0f, marker->TranslationY());
  EXPECT_FALSE(marker->UpdateOpacity(start));
  EXPECT_FALSE(marker->UpdateOpacity(start + base::Milliseconds(90)));
  EXPECT_FLOAT_EQ(1.0f, marker->Opacity());
  EXPECT_GT(marker->TranslationInline(), 0.0f);
  EXPECT_LT(marker->TranslationInline(), 3.0f);
  EXPECT_TRUE(marker->UpdateOpacity(start + base::Milliseconds(180)));
  EXPECT_FLOAT_EQ(0.0f, marker->TranslationInline());
}

}  // namespace blink
