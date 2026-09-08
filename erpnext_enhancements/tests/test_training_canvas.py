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


if __name__ == "__main__":
    unittest.main()
