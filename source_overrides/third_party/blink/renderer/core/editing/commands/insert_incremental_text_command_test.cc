// Copyright 2017 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/editing/commands/insert_incremental_text_command.h"

#include "third_party/blink/renderer/core/dom/text.h"
#include "third_party/blink/renderer/core/editing/frame_selection.h"
#include "third_party/blink/renderer/core/editing/markers/document_marker_controller.h"
#include "third_party/blink/renderer/core/editing/selection_template.h"
#include "third_party/blink/renderer/core/editing/testing/editing_test_base.h"
#include "third_party/blink/renderer/core/frame/settings.h"
#include "third_party/blink/renderer/platform/heap/garbage_collected.h"

namespace blink {

class InsertIncrementalTextCommandTest : public EditingTestBase {};

TEST_F(InsertIncrementalTextCommandTest,
       FocusTextMotionMarksIdenticalCommittedComposition) {
  GetDocument().GetSettings()->SetFocusTextMotionEnabled(true);
  SetBodyContent("<div id=sample contenteditable>abXYcd</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  auto* const text = To<Text>(sample->firstChild());
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(text, 2))
                               .Extend(Position(text, 4))
                               .Build(),
                           SetSelectionOptions());

  MakeGarbageCollected<InsertIncrementalTextCommand>(
      GetDocument(), "XY", EditCommand::PasswordEchoBehavior::kDoNotEcho,
      InsertTextCommand::kRebalanceAllWhitespaces,
      /*enable_focus_text_motion=*/true)
      ->Apply();

  const DocumentMarkerVector markers = GetDocument().Markers().MarkersFor(
      *text, DocumentMarker::MarkerTypes::FocusTextMotion());
  ASSERT_EQ(2u, markers.size());
  EXPECT_EQ(2u, markers[0]->StartOffset());
  EXPECT_EQ(3u, markers[0]->EndOffset());
  EXPECT_EQ(3u, markers[1]->StartOffset());
  EXPECT_EQ(4u, markers[1]->EndOffset());
  EXPECT_EQ("abXYcd", text->data());
}

TEST_F(InsertIncrementalTextCommandTest,
       FocusTextMotionSkipsIdenticalProvisionalComposition) {
  GetDocument().GetSettings()->SetFocusTextMotionEnabled(true);
  SetBodyContent("<div id=sample contenteditable>abXYcd</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  auto* const text = To<Text>(sample->firstChild());
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(text, 2))
                               .Extend(Position(text, 4))
                               .Build(),
                           SetSelectionOptions());

  MakeGarbageCollected<InsertIncrementalTextCommand>(
      GetDocument(), "XY", EditCommand::PasswordEchoBehavior::kDoNotEcho,
      InsertTextCommand::kRebalanceAllWhitespaces,
      /*enable_focus_text_motion=*/false)
      ->Apply();

  EXPECT_TRUE(
      GetDocument()
          .Markers()
          .MarkersFor(*text, DocumentMarker::MarkerTypes::FocusTextMotion())
          .empty());
  EXPECT_EQ("abXYcd", text->data());
}

// http://crbug.com/706166
TEST_F(InsertIncrementalTextCommandTest, SurrogatePairsReplace) {
  SetBodyContent("<div id=sample contenteditable><a>a</a>b&#x1F63A;</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  const String new_text(Vector<UChar>{0xD83D, 0xDE38});  // U+1F638
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(sample->lastChild(), 1))
                               .Extend(Position(sample->lastChild(), 3))
                               .Build(),
                           SetSelectionOptions());
  CompositeEditCommand* const command =
      MakeGarbageCollected<InsertIncrementalTextCommand>(
          GetDocument(), new_text,
          EditCommand::PasswordEchoBehavior::kDoNotEcho);
  command->Apply();

  EXPECT_EQ(String(Vector<UChar>{'b', 0xD83D, 0xDE38}),
            sample->lastChild()->nodeValue())
      << "Replace 'U+D83D U+DE3A (U+1F63A) with 'U+D83D U+DE38'(U+1F638)";
}

TEST_F(InsertIncrementalTextCommandTest, SurrogatePairsNoReplace) {
  SetBodyContent("<div id=sample contenteditable><a>a</a>b&#x1F63A;</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  const String new_text(Vector<UChar>{0xD83D, 0xDE3A});  // U+1F63A
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(sample->lastChild(), 1))
                               .Extend(Position(sample->lastChild(), 3))
                               .Build(),
                           SetSelectionOptions());
  CompositeEditCommand* const command =
      MakeGarbageCollected<InsertIncrementalTextCommand>(
          GetDocument(), new_text,
          EditCommand::PasswordEchoBehavior::kDoNotEcho);
  command->Apply();

  EXPECT_EQ(String(Vector<UChar>{'b', 0xD83D, 0xDE3A}),
            sample->lastChild()->nodeValue())
      << "Replace 'U+D83D U+DE3A(U+1F63A) with 'U+D83D U+DE3A'(U+1F63A)";
}

// http://crbug.com/706166
TEST_F(InsertIncrementalTextCommandTest, SurrogatePairsTwo) {
  SetBodyContent(
      "<div id=sample contenteditable><a>a</a>b&#x1F63A;&#x1F63A;</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  const String new_text(Vector<UChar>{0xD83D, 0xDE38});  // U+1F638
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(sample->lastChild(), 1))
                               .Extend(Position(sample->lastChild(), 5))
                               .Build(),
                           SetSelectionOptions());
  CompositeEditCommand* const command =
      MakeGarbageCollected<InsertIncrementalTextCommand>(
          GetDocument(), new_text,
          EditCommand::PasswordEchoBehavior::kDoNotEcho);
  command->Apply();

  EXPECT_EQ(String(Vector<UChar>{'b', 0xD83D, 0xDE38}),
            sample->lastChild()->nodeValue())
      << "Replace 'U+1F63A U+1F63A with U+1F638";
}

TEST_F(InsertIncrementalTextCommandTest,
       SurrogatePairsReplaceWithPreceedingNonEditableText) {
  SetBodyContent(
      "<div id=sample contenteditable><span "
      "contenteditable='false'>•</span>&#x1F63A;&#x1F638;</div>");
  Element* const sample = GetDocument().getElementById(AtomicString("sample"));
  const String new_text(Vector<UChar>{0xD83D, 0xDE38});  // U+1F638
  Selection().SetSelection(SelectionInDOMTree::Builder()
                               .Collapse(Position(sample->lastChild(), 2))
                               .Extend(Position(sample->lastChild(), 4))
                               .Build(),
                           SetSelectionOptions());
  CompositeEditCommand* const command =
      MakeGarbageCollected<InsertIncrementalTextCommand>(
          GetDocument(), new_text,
          EditCommand::PasswordEchoBehavior::kDoNotEcho);
  command->Apply();

  EXPECT_EQ(String(Vector<UChar>{0xD83D, 0xDE3A, 0xD83D, 0xDE38}),
            sample->lastChild()->nodeValue())
      << "Replace U+1F638 with U+1F638";
}

}  // namespace blink
