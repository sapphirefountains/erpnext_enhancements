"""Chaptered video: a clickable contents list inside a lesson video.

The vertical mirrors in-video **checkpoints** deliberately — a standalone doctype
keyed by `(lesson, block_key)`, authored on the canvas, serialized at publish into
`published_content_json`, read by `video.js` — and stops mirroring at exactly one
place, which is the place worth writing a test about.

**Chapters are public; checkpoints are not.** A checkpoint's options and per-option
explanations live at `permlevel: 1` and only a *count* ever reaches the browser,
because shipping them hands over the answer. A chapter is a label and a number: it
has to reach the browser to be clickable, and there is nothing in it to protect.

**Seeking to a chapter earns no watch coverage, and nobody had to make that true.**
`video.js` credits a media span only when the media advance is consistent with
elapsed wall time × rate, so a forward seek credits nothing — the same property that
makes `currentTime = 3600` in the console worthless. A coverage-gated course
therefore cannot be passed by clicking through the contents, and jumping *back* to
re-hear a sentence costs nothing either, because the per-second bitmap is idempotent.

Run: python -m unittest erpnext_enhancements.tests.test_training_video_chapters
"""

import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DOCTYPE_DIR = APP / "training" / "doctype" / "training_video_chapter"
AUTHOR_API = APP / "api" / "training_author.py"
VIDEO_JS = APP / "public" / "js" / "training" / "video.js"
PLAYER_CSS = APP / "public" / "css" / "training" / "player.css"
CANVAS_JS = APP / "training" / "page" / "training_canvas" / "training_canvas.js"


def read(path):
    return path.read_text(encoding="utf-8")


def doctype():
    return json.loads(read(DOCTYPE_DIR / "training_video_chapter.json"))


def js_code(path):
    """JS with comments stripped, for the assertions about absence."""
    src = re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)
    keep = [line for line in src.splitlines() if not line.strip().startswith("//")]
    return chr(10).join(keep)


class TestTheDoctype(unittest.TestCase):
    def test_it_exists_and_is_in_the_training_module(self):
        d = doctype()
        self.assertEqual(d["doctype"], "DocType")
        self.assertEqual(d["module"], "Training")
        self.assertEqual(d["name"], "Training Video Chapter")

    def test_it_is_not_a_child_table(self):
        """Keyed by (lesson, block_key) like a checkpoint, and for the same reason:
        `Training Content Block` is itself a child of `Training Lesson`, and Frappe
        has no grandchild tables."""
        self.assertNotEqual(doctype().get("istable"), 1)
        names = {f["fieldname"] for f in doctype()["fields"]}
        self.assertIn("lesson", names)
        self.assertIn("block_key", names)

    def test_it_keys_on_block_key_and_not_an_index(self):
        """An `idx` would move a chapter onto a different video the first time
        somebody reordered a lesson's blocks. Every join in this module is on a
        stable `*_key` for that reason."""
        fields = {f["fieldname"]: f for f in doctype()["fields"]}
        self.assertEqual(fields["block_key"]["fieldtype"], "Data")
        self.assertEqual(fields["block_key"].get("reqd"), 1)

    def test_the_timestamp_and_title_are_required(self):
        fields = {f["fieldname"]: f for f in doctype()["fields"]}
        self.assertEqual(fields["at_seconds"].get("reqd"), 1)
        self.assertEqual(fields["title"].get("reqd"), 1)

    def test_no_learner_role_can_write_it(self):
        """A learner reads chapters off the published payload, never off the
        doctype — the same arrangement as every other content doctype here."""
        roles = {p["role"] for p in doctype()["permissions"]}
        self.assertEqual(roles, {"System Manager", "Training Manager", "Training Author"})

    def test_the_controller_refuses_a_duplicate_timestamp(self):
        """Two chapters at the same second render as two adjacent entries in an
        arbitrary order that changes between reads — the author sees one order and
        the learner another, and neither is wrong."""
        source = read(DOCTYPE_DIR / "training_video_chapter.py")
        self.assertIn("_refuse_a_duplicate_timestamp", source)
        self.assertIn("frappe.throw", source)


class TestThePublishedPayload(unittest.TestCase):
    def test_split_lesson_attaches_them_to_the_block(self):
        source = read(AUTHOR_API)
        self.assertIn('"Training Video Chapter"', source)
        self.assertIn('payload["chapters"] = rows', source)

    def test_they_go_in_public_and_not_in_the_answer_key(self):
        """The one place the checkpoint mirror stops. A chapter carries nothing to
        protect; a `key` half for it would be an empty ceremony that later invites
        somebody to put something real in it."""
        source = read(AUTHOR_API)
        start = source.index("chapters_by_block = {}")
        end = source.index('payload["chapters"] = rows') + 40
        block = source[start:end]
        self.assertNotIn("key[", block)
        self.assertIn('public["blocks"]', block)

    def test_the_key_is_omitted_when_there_are_none(self):
        """Absent rather than empty: an empty list on every block would put the word
        `chapters` on every block that will never have any."""
        source = read(AUTHOR_API)
        self.assertRegex(source, r"if rows:\s*\n\s*payload\[\"chapters\"\] = rows")

    def test_they_are_ordered_by_the_server(self):
        """So the player never sorts, and cannot disagree with the author."""
        source = read(AUTHOR_API)
        start = source.index('"Training Video Chapter"')
        self.assertIn('order_by="at_seconds asc"', source[start : start + 400])


class TestTheBuilderPayload(unittest.TestCase):
    def test_the_canvas_is_sent_its_chapters(self):
        source = read(AUTHOR_API)
        self.assertIn("_builder_video_chapters", source)
        self.assertIn('"video_chapters": video_chapters or []', source)

    def test_it_is_called_video_chapters_and_not_chapters(self):
        """`Training Chapter` groups LESSONS and is a different idea with the same
        English word. The canvas already holds `this.chapters` for it, so a payload
        key called `chapters` would land a list of timestamps on top of the lesson
        grouping."""
        source = read(AUTHOR_API)
        self.assertIn('"video_chapters"', source)
        canvas = js_code(CANVAS_JS)
        self.assertIn("lesson.video_chapters", canvas)

    def test_it_carries_the_optimistic_lock(self):
        """`modified` goes back on every save, same as a checkpoint's, so a second
        author's edit is rejected rather than silently overwritten."""
        source = read(AUTHOR_API)
        start = source.index("def _builder_video_chapters(")
        self.assertIn('"modified"', source[start : start + 1200])


class TestTheLearnerRuntime(unittest.TestCase):
    def test_video_js_renders_the_list(self):
        code = js_code(VIDEO_JS)
        self.assertIn("tr-video-chapters", code)
        self.assertIn("tr-video-chapter-go", code)

    def test_it_seeks_by_setting_current_time(self):
        code = js_code(VIDEO_JS)
        self.assertIn("video.currentTime = at", code)

    def test_it_survives_a_seek_the_browser_refuses(self):
        """A video that has not loaded its metadata refuses a seek, and an error
        thrown here would take the whole block render down with it."""
        code = js_code(VIDEO_JS)
        start = code.index("video.currentTime = at")
        self.assertIn("catch", code[start - 200 : start + 400])

    def test_mount_passes_them_through(self):
        code = js_code(VIDEO_JS)
        self.assertIn("chapters: block.chapters", code)

    def test_it_draws_nothing_when_there_are_none(self):
        """Most blocks have none, and an empty bordered list is worse than nothing."""
        code = js_code(VIDEO_JS)
        self.assertIn("if (chapters && chapters.length)", code)

    def test_no_endpoint_was_added_for_this(self):
        """Chapters ride the payload `get_lesson` already sends. A new whitelisted
        read would be a new surface to permission, rate-limit and keep POST-only,
        for data that was already in flight."""
        api = read(APP / "api" / "training.py")
        self.assertNotIn("def get_chapters", api)
        self.assertNotIn("Training Video Chapter", api)


class TestTheClassContract(unittest.TestCase):
    """Every `tr-*` class the scripts emit needs a rule. Asserted in full by
    `test_training_player_css_contract`; named here so a chapter-shaped failure
    says so."""

    def test_every_new_class_is_styled(self):
        css = read(PLAYER_CSS)
        for name in (
            "tr-video-chapters",
            "tr-video-chapter",
            "tr-video-chapter-go",
            "tr-video-chapter-at",
            "tr-video-chapter-title",
        ):
            with self.subTest(name):
                self.assertIn(f".{name}", css)


class TestTheAuthoringSurface(unittest.TestCase):
    def test_the_editor_is_offered_only_on_a_video_block(self):
        """An External Embed is a cross-origin iframe with no `currentTime` and no
        seek, so a chapter on one could never work. The honest place to say so is by
        not offering the editor — which falls out of it living in `video_editor`."""
        code = js_code(CANVAS_JS)
        call_at = code.index("this.chapters_editor(lesson, block)")
        video_at = code.index(chr(9) + "video_editor(lesson, block) {")
        # The call sits inside video_editor's body, which starts before it and is the
        # only branch that reaches it.
        self.assertLess(video_at, call_at, "the editor is called outside video_editor")
        self.assertIn(chr(9) + "chapters_editor(lesson, block) {", code)

    def test_it_repaints_only_its_own_list(self):
        """A full re-render tears down the rich-text controls, so a save landing a
        second after the author started typing in a block would eat it. The same
        reason `after_checkpoint_write` repaints pins rather than the sheet."""
        code = js_code(CANVAS_JS)
        start = code.index("write_chapter(lesson, row)")
        body = code[start : start + 1800]
        self.assertNotIn("render_sheet", body)

    def test_it_saves_through_the_doctype_not_the_block_table(self):
        """`save_draft_version` replaces the block child table by position and
        refuses anything outside BLOCK_ALLOWED_FIELDS, so a chapter sent that way
        would be dropped silently and come back in `rejected`."""
        code = js_code(CANVAS_JS)
        self.assertIn('doctype: "Training Video Chapter"', code)

    def test_it_refuses_to_key_a_chapter_on_an_unsaved_block(self):
        """A block created this session has no stable `block_key` until the save
        returns, and a chapter keyed on a transient one is stranded on the next
        load."""
        code = js_code(CANVAS_JS)
        # The DEFINITION, not the call site. `code.index("chapters_editor(...)")`
        # finds the call first, because video_editor() appears earlier in the file --
        # which is how this test first passed over the wrong 900 characters.
        start = code.index(chr(9) + "chapters_editor(lesson, block) {")
        self.assertIn("if (!block.block_key)", code[start : start + 900])


if __name__ == "__main__":
    unittest.main()
