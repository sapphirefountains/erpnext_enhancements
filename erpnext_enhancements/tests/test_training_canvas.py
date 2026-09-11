"""The Training Canvas WYSIWYG spike, and the bootstrap round-trip it depends on.

Two things here are static-read assertions for the same reason the rest of the
training suite is: these files never run in CI, there is no bench, and the failure
modes are *text* problems a substring assertion is good at.

1. **The round-trip gap that made the canvas unsafe — and the classic builder too.**
   ``get_builder_bootstrap`` returns each block's edit shape, and a save re-sends the
   WHOLE block table (``_apply_blocks`` replaces it by position). But the bootstrap's
   block dict omitted ``data`` (the interactive list JSON) and ``callout_tone`` even
   though ``save_draft_version`` accepts them — so after a reload an interactive block
   loaded with neither, and the next save re-sent it without them, blanking the stored
   list. Any edit-in-place canvas destroys interactive content on the first autosave
   unless the bootstrap round-trips those two fields. Asserted by parsing the function,
   not by substring, because a comment on the fix quotes the field names.

2. **The canvas edits the real data path, not a private one.** The spike is only worth
   having if it loads through ``get_builder_bootstrap`` and saves through
   ``save_draft_version`` with the version's ``modified`` as the optimistic lock, sends
   the whole block table with ``block_key`` and the two round-trip fields carried, and
   renders with the learner's own ``TR.renderBlock`` rather than a second renderer that
   would drift from what publishes.

Run: python -m unittest erpnext_enhancements.tests.test_training_canvas
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
AUTHOR_PY = APP / "api/training_author.py"
CANVAS_JS = APP / "training/page/training_canvas/training_canvas.js"
CANVAS_JSON = APP / "training/page/training_canvas/training_canvas.json"


def _strip_js_comments(src):
    out, in_block = [], False
    for line in src.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


def _canvas():
    return _strip_js_comments(CANVAS_JS.read_text(encoding="utf-8"))


class TestBootstrapRoundTripsInteractiveData(unittest.TestCase):
    """The block dict get_builder_bootstrap builds must carry data + callout_tone."""

    def _builder_lesson_block_keys(self):
        tree = ast.parse(AUTHOR_PY.read_text(encoding="utf-8"))
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_builder_lesson":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Dict):
                        keys |= {
                            k.value
                            for k in inner.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        }
        return keys

    def test_the_function_is_found(self):
        self.assertIn("block_key", self._builder_lesson_block_keys(), "_builder_lesson not parsed")

    def test_it_returns_data_and_callout_tone(self):
        keys = self._builder_lesson_block_keys()
        for field in ("data", "callout_tone"):
            self.assertIn(
                field,
                keys,
                f"get_builder_bootstrap does not return block.{field}; an edit-in-place "
                f"save re-sends the block table without it and blanks interactive content",
            )

    def test_save_still_allows_them(self):
        """The other half: if save stopped accepting these, the round-trip would be
        one-directional and the read side would be pointless."""
        src = AUTHOR_PY.read_text(encoding="utf-8")
        allowed = re.search(r"BLOCK_ALLOWED_FIELDS\s*=\s*frozenset\(\s*\{(.*?)\}", src, re.DOTALL)
        self.assertIsNotNone(allowed, "BLOCK_ALLOWED_FIELDS not found")
        fields = set(re.findall(r'"(\w+)"', allowed.group(1)))
        self.assertTrue({"data", "callout_tone"} <= fields)


class TestCanvasUsesTheRealDataPath(unittest.TestCase):
    def test_it_loads_and_saves_through_the_builder_api(self):
        code = _canvas()
        self.assertIn("training_author.get_builder_bootstrap", code)
        self.assertIn("training_author.save_draft_version", code)

    def test_the_save_carries_the_optimistic_lock(self):
        code = _canvas()
        # sends the version's modified, and adopts the token the response returns.
        self.assertIn("modified: this.version.modified", code)
        self.assertIn("this.version.modified = state.modified", code)

    def test_the_whole_block_table_carries_block_key_and_the_round_trip_fields(self):
        """A dropped block_key strands learner progress; dropped data/callout_tone
        blanks interactive content. All three must be in the field list the save
        builds each block row from."""
        code = _canvas()
        fields = re.search(r"TC_BLOCK_FIELDS = \[([^\]]*)\]", code)
        self.assertIsNotNone(fields, "TC_BLOCK_FIELDS not found")
        sent = set(re.findall(r'"(\w+)"', fields.group(1)))
        for field in ("block_key", "data", "callout_tone", "block_type", "content"):
            self.assertIn(field, sent, f"the canvas save omits {field!r}")

    def test_it_reuses_the_learner_renderer(self):
        """Not a second renderer — TR.renderBlock is the learner's, so the canvas
        cannot drift from what publishes."""
        self.assertIn("TR.renderBlock", _canvas())

    def test_it_surfaces_rejected_fields(self):
        """A silently dropped field is the worst outcome — the save reports success
        and the work is gone. `rejected` is the only signal."""
        self.assertIn("report_rejected", _canvas())


class TestCanvasEditsContentInPlace(unittest.TestCase):
    """The WYSIWYG editing capabilities — guarded so a refactor cannot silently
    drop them back to a read-only preview."""

    def test_rich_text_has_a_formatting_toolbar(self):
        self.assertIn("execCommand", _canvas(), "no rich-text formatting toolbar")

    def test_blocks_can_be_added_moved_and_removed(self):
        code = _canvas()
        for method in ("add_block(", "move_block(", "remove_block("):
            self.assertIn(method, code, f"canvas cannot {method}")

    def test_a_new_block_mints_a_client_key_it_does_not_regenerate(self):
        """A new block needs a stable id before the first save; existing keys are
        never regenerated (that strands learner progress)."""
        self.assertIn('"blk-" + Math.random', _canvas())

    def test_the_interactive_types_are_edited_through_data(self):
        """Checklist / Flashcards / Accordion write their list back into block.data
        as JSON — the shape the server and the renderer both speak."""
        self.assertIn("block.data = JSON.stringify", _canvas())

    def test_callout_tone_is_editable(self):
        self.assertIn("block.callout_tone = val", _canvas())


class TestCanvasBuildsWholeCourses(unittest.TestCase):
    """Lesson management, settings and the draft lifecycle — the canvas is a whole
    builder, not just a block editor."""

    def test_lessons_can_be_added_reordered_and_deleted(self):
        code = _canvas()
        for method in ("add_lesson(", "remove_lesson(", "commit_lesson_order("):
            self.assertIn(method, code)
        # reorder goes through the dedicated endpoint that does not churn the lock.
        self.assertIn("training_author.reorder_lessons", code)

    def test_a_new_lesson_carries_a_temp_id_not_a_fake_name(self):
        """A client-invented name would disagree with the row the server mints; the
        save maps temp_id -> name in created_lessons and the canvas adopts it."""
        code = _canvas()
        self.assertIn("temp_id", code)
        self.assertIn("adopt_created", code)

    def test_deleted_lessons_are_sent_for_deletion(self):
        self.assertIn("deleted_lessons", _canvas())

    def test_lesson_settings_go_through_the_allowlist(self):
        code = _canvas()
        self.assertIn("set_lesson_field", code)
        fields = re.search(r"TC_LESSON_FIELDS = \[([^\]]*)\]", code)
        self.assertIsNotNone(fields, "TC_LESSON_FIELDS not found")
        sent = set(re.findall(r'"(\w+)"', fields.group(1)))
        for f in ("summary", "chapter_key", "has_quiz", "quiz_pass_score", "requires_submission"):
            self.assertIn(f, sent)

    def test_the_lifecycle_is_wired(self):
        code = _canvas()
        for endpoint in ("create_draft_version", "submit_for_review", "publish_version"):
            self.assertIn("training_author." + endpoint, code)

    def test_publish_sends_the_full_change_type_strings(self):
        """A truncated change_type is rejected by publish_version — the exact bug the
        classic builder shipped. The full parenthesised strings must be sent."""
        code = _canvas()
        self.assertIn("Minor Edit (keep completions)", code)
        self.assertIn("Material Change (require retake)", code)
        self.assertNotIn('"Minor Edit"', code)
        self.assertNotIn('"Material Change"', code)


class TestCanvasAuthorsMedia(unittest.TestCase):
    """Media blocks are authored on the canvas too — attach files, pick a video
    asset, place hotspots — reusing the classic builder's upload idiom."""

    def test_every_block_type_can_be_added(self):
        """The add menu offers all twelve types, not just text — so a whole lesson
        can be built without leaving the canvas."""
        code = _canvas()
        block = re.search(r"TC_ADDABLE = \[(.*?)\]", code, re.DOTALL)
        self.assertIsNotNone(block, "TC_ADDABLE not found")
        offered = set(re.findall(r'"([^"]+)"', block.group(1)))
        for t in ("Image", "Video", "PDF", "Downloadable File", "Image Hotspots", "External Embed"):
            self.assertIn(t, offered, f"{t!r} cannot be added on the canvas")

    def test_files_upload_as_private_via_upload_file(self):
        code = _canvas()
        self.assertIn("attach_media", code)
        self.assertIn("/api/method/upload_file", code)
        self.assertIn('"is_private"', code)

    def test_video_picks_a_registered_asset(self):
        code = _canvas()
        self.assertIn("video_editor", code)
        self.assertIn("video_assets", code)

    def test_hotspots_are_placed_on_the_canvas(self):
        self.assertIn("hotspots_editor", _canvas())


class TestCanvasPageIsRegistered(unittest.TestCase):
    def test_the_page_is_gated_to_authors(self):
        import json

        doc = json.loads(CANVAS_JSON.read_text(encoding="utf-8"))
        roles = {r["role"] for r in doc.get("roles", [])}
        self.assertEqual(roles, {"System Manager", "Training Author", "Training Manager"})
        self.assertEqual(doc["module"], "Training")

    def test_it_goes_full_bleed_through_a_scoped_marker(self):
        """The edge-to-edge overrides are scoped to a wrapper class so they cannot
        leak into other desk pages."""
        code = _canvas()
        self.assertIn("training-canvas-fullbleed", code)
class TestCanvasWritesLegalSelectValues(unittest.TestCase):
    """The defect that made this suite's absence from CI expensive.

    ``callout_tone`` is a Select on ``Training Content Block``. Frappe runs
    ``_validate_selects`` on child rows, and ``save_draft_version`` performs a
    full ``lesson.save()`` — so a value outside the declared options does not
    degrade to a default, it **throws**, and it takes the whole lesson autosave
    down with it. The canvas declared its tones in lowercase and invented an
    ``"info"`` the Select did not have, then stamped it on every new Callout. The
    first Callout an author created on this page therefore broke saving, silently
    from the code's point of view and very loudly from the author's.

    The general rule this pins: **a client that writes a Select field must write
    the DocType's own vocabulary, verbatim.** Asserted as a cross-file equality
    rather than a spot-check, so adding a tone to either side without the other
    fails here.
    """

    CONTENT_BLOCK = APP / "training/doctype/training_content_block/training_content_block.json"

    def _select_options(self, fieldname):
        import json

        data = json.loads(self.CONTENT_BLOCK.read_text(encoding="utf-8"))
        for field in data["fields"]:
            if field["fieldname"] == fieldname:
                # A leading blank means "no tone", which is legal and is not an option.
                return [o for o in (field.get("options") or "").split("\n") if o]
        raise AssertionError(f"Training Content Block has no {fieldname} field")

    def _canvas_tones(self):
        """The value half of every ``TC_CALLOUT_TONES`` entry."""
        line = re.search(r"const TC_CALLOUT_TONES = \[(.*?)\];", _canvas(), re.S)
        self.assertIsNotNone(line, "TC_CALLOUT_TONES is not declared as a flat array any more")
        return re.findall(r'\[\s*"([^"]+)"', line.group(1))

    def test_the_canvas_offers_exactly_the_declared_tones(self):
        self.assertEqual(self._canvas_tones(), self._select_options("callout_tone"))

    def test_a_new_callout_is_seeded_with_a_declared_tone(self):
        seeded = re.search(r'type === "Callout"\) \{[^}]*callout_tone = "([^"]+)"', _canvas())
        self.assertIsNotNone(seeded, "new Callouts no longer seed a tone")
        self.assertIn(seeded.group(1), self._select_options("callout_tone"))

    def test_no_lowercase_tone_is_written_to_the_field(self):
        """The specific shape of the bug: assignment of a lowercased literal."""
        for tone in self._select_options("callout_tone"):
            with self.subTest(tone=tone):
                self.assertNotIn(f'callout_tone = "{tone.lower()}"', _canvas())

    def test_the_block_type_seeds_are_declared_types(self):
        """Same rule, the other Select on the same child table. ``block_type`` is
        seeded by the canvas on every new block and has twelve legal values."""
        declared = set(self._select_options("block_type"))
        seeded = set(re.findall(r'type === "([A-Z][A-Za-z ]+)"', _canvas()))
        unknown = sorted(seeded - declared)
        self.assertEqual(
            unknown, [], f"canvas branches on block types {unknown} which the DocType does not declare"
        )

class TestTheEditorDoesNotTransformOnTheRenderPath(unittest.TestCase):
    """v1.396.0. The canvas is an edit-in-place surface, so the render path IS the
    write path: blocks.js assigns `html` to innerHTML, wire_inline_edit makes that
    node contenteditable, and its input handler writes `body.innerHTML` back to
    `block.content`. `frappe.utils.xss_sanitise` escapes rather than sanitises, so
    running it on the way out persisted `&lt;p&gt;` into the lesson on the first
    keystroke and compounded on every later edit.

    Comment-stripped, because the comment explaining the absence names the token --
    the trap this repo has now hit four times.
    """

    def test_rich_text_is_handed_to_the_renderer_untransformed(self):
        src = _canvas()
        self.assertIn('html: block.content || ""', src)
        self.assertNotIn("xss_sanitise(block.content", src)

    def test_the_accordion_escape_is_deliberately_kept(self):
        """NOT the same call site. Accordion bodies live in `data`, fieldtype Code,
        and frappe's `_sanitize_content` explicitly skips Code -- so there the escape
        is the only protection there is, and nothing writes back to it. Removing it
        as tidy-up would be a real security regression, so it is pinned."""
        self.assertIn("xss_sanitise(String(p.body", _canvas())

    def test_no_other_transform_crept_onto_the_render_path(self):
        """sanitize_html, DOMParser and remove_script_and_style would each be lossy
        in their own way. Identity is the only non-lossy option on this path."""
        src = _canvas()
        at = src.index("to_render_block(block)")
        block = src[at : at + 900]
        for banned in ("sanitize_html", "DOMParser", "remove_script_and_style"):
            with self.subTest(transform=banned):
                self.assertNotIn(banned, block)


if __name__ == "__main__":
    unittest.main()
