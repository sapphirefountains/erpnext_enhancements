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
    """Strip block comments AND line comments, including TRAILING ones.

    The previous version only dropped lines whose stripped text STARTED with a line
    comment, so a trailing one kept the very word it was warning about -- and every
    absence assertion built on this helper was weaker than it looked. That is the
    seventh time this repo has been bitten by an assertion matching its own
    explanation.

    Quote-aware, so a comment marker inside a string or a URL survives. Not a full
    JS parser (it does not model regex literals), but this file contains none and the
    failure direction is safe: an unstripped comment can only make an absence
    assertion stricter, never looser.
    """
    out, in_block = [], False
    for line in src.splitlines():
        if in_block:
            if '*/' in line:
                in_block = False
                line = line.split('*/', 1)[1]
            else:
                continue
        kept, quote, i = [], None, 0
        while i < len(line):
            ch = line[i]
            if quote:
                kept.append(ch)
                if ch == "\\" and i + 1 < len(line):
                    kept.append(line[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
                kept.append(ch)
            elif ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                break
            elif ch == '/' and i + 1 < len(line) and line[i + 1] == '*':
                rest = line[i + 2:]
                if '*/' in rest:
                    line = rest.split('*/', 1)[1]
                    i = -1
                else:
                    in_block = True
                    break
            else:
                kept.append(ch)
            i += 1
        out.append(''.join(kept))
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


class TestChaptersAreReachable(unittest.TestCase):
    """v1.398.0. `this.chapters` was read in four places and written only from the
    bootstrap; `dirty.chapters` was read in three and written by NOTHING. The picker
    in lesson settings hides itself when the array is empty, so a course authored
    start to finish on the canvas had every lesson Unfiled with no way out — and the
    only thing that could ever populate that array was the classic builder, the tool
    being retired. Unreachable by construction, exactly like `transcript` was.
    """

    def test_something_writes_the_dirty_chapters_slot(self):
        src = _canvas()
        self.assertIn("this.dirty.chapters = kept.map(", src)

    def test_the_manager_is_reachable_from_the_page(self):
        self.assertIn("open_chapters()", _canvas())

    def test_an_empty_list_is_still_sent(self):
        """Deleting the last chapter must reach the server, which refuses it if
        lessons still point at one. Sending `undefined` would look like success."""
        src = _canvas()
        at = src.index("this.dirty.chapters = kept.map(")
        self.assertNotIn("kept.length ?", src[max(0, at - 200) : at])


class TestTheSaveFlushIsHonest(unittest.TestCase):
    """`save()` returns a bare Promise.resolve() while a save is in flight, so
    anything chained off it runs against the version before the one just typed.
    Three later steps depend on this being honest, because a pin and a preview both
    resolve their target through the DATABASE — where a block that exists only in
    memory is simply absent.
    """

    def test_flush_returns_the_in_flight_promise(self):
        src = _canvas()
        self.assertIn("flush_save()", src)
        at = src.index("flush_save() {")
        block = src[at : at + 700]
        self.assertIn("this._inflight", block)
        # The in-flight check must come BEFORE the has_dirty short-circuit, or a
        # caller chaining off a flush during a save still proceeds early.
        self.assertLess(block.index("_inflight"), block.index("has_dirty"))

    def test_save_records_the_in_flight_promise(self):
        src = _canvas()
        self.assertIn("this._inflight = frappe", src)
        self.assertIn("return this._inflight;", src)

    def test_flush_no_longer_swallows_the_failure(self):
        """Both exits used to end `.catch(() => {})`, which made a failed save
        indistinguishable from a clean one to every caller. A pin writer chained off
        it would fire AFTER the save had thrown, fail again resolving a block that was
        never written, and report the wrong cause -- `enter_conflict()` on a stale
        `modified` is exactly that case.

        A promise that resolves whether or not the work landed is not a flush, it is
        a delay."""
        src = _canvas()
        at = src.index("flush_save() {")
        block = src[at : src.index("save_then(label) {", at)]
        self.assertNotIn("catch(() => {})", block)

    def test_the_swallow_moved_to_the_caller_that_wants_it(self):
        """The reorder genuinely does want to give up quietly -- the rail has already
        re-rendered optimistically and the autosave has surfaced its own error. That is
        a property of that caller, not of the flush, and while it lived inside
        flush_save it was hiding failures from every other caller too."""
        src = _canvas()
        at = src.index("commit_lesson_order($list) {")
        body = src[at : src.index("load_vtt(lesson) {", at)]
        self.assertIn("catch(() => {})", body)

    def test_save_then_names_the_abandoned_action(self):
        """When the save fails the chained action is correctly abandoned -- but the
        dialog has closed and the only thing on screen is frappe's error about the
        AUTOSAVE. Nothing connects that to the button the author pressed, so the thing
        they asked for never happens and no message says why."""
        src = _canvas()
        at = src.index("save_then(label) {")
        body = src[at : at + 1200]
        self.assertIn("this.flush_save()", body)
        self.assertIn("msgprint", body)
        self.assertIn("throw error", body)

    def test_the_reorder_waits_for_it(self):
        """`.filter(Boolean)` drops any lesson created this session, because it has
        no `name` until the save returns — and reorder_lessons renumbers only what it
        was given, so dragging a new lesson to the top silently left it put."""
        src = _canvas()
        # The DEFINITION, not the first call site -- src.index("commit_lesson_order")
        # lands on `this.commit_lesson_order($list)` and slices the wrong method.
        at = src.index("commit_lesson_order($list) {")
        block = src[at : at + 1400]
        # Chains off `save_then`, which WRAPS flush_save and additionally names the
        # abandoned action when the save fails. The wrapper's own delegation is
        # asserted below, so both links stay pinned -- a save_then that stopped
        # flushing would otherwise pass this.
        self.assertIn("this.save_then(", block)
        self.assertLess(block.index("save_then"), block.index("reorder_lessons"))

    def test_a_closing_tab_is_warned(self):
        """The autosave debounce is 1200ms, so a tab closed a second after the last
        keystroke loses it. The canvas had no guard at all."""
        src = _canvas()
        self.assertIn("beforeunload", src)
        at = src.index("beforeunload")
        self.assertIn("has_dirty()", src[max(0, at - 500) : at])


class TestTranscriptIsNoLongerUnreachable(unittest.TestCase):
    """The server allowlisted `transcript` and round-tripped it on the bootstrap all
    along. It was missing from the CLIENT allowlist, and `set_lesson_field` silently
    returns on a field outside it — so there was no error to notice.
    """

    def test_it_is_in_the_client_allowlist(self):
        src = _canvas()
        at = src.index("TC_LESSON_FIELDS")
        self.assertIn('"transcript"', src[at : src.index("]", at)])

    def test_there_is_somewhere_to_type_it(self):
        """An allowlist entry with no control is still unreachable."""
        self.assertIn('set_lesson_field(lesson, "transcript"', _canvas())


class TestTheCommentStripperItself(unittest.TestCase):
    """A meta-test, and it earns its place: every absence assertion in this file
    depends on the stripper, and the previous version only dropped lines that
    STARTED with a line comment. A trailing one kept the very token it warned about.
    """

    def test_a_trailing_comment_is_dropped(self):
        self.assertNotIn("bar", _strip_js_comments("foo(); // never call bar()"))

    def test_a_url_inside_a_string_survives(self):
        kept = _strip_js_comments('const u = "https://example.com/x"; // note')
        self.assertIn("https://example.com/x", kept)
        self.assertNotIn("note", kept)

    def test_an_inline_block_comment_is_dropped(self):
        self.assertNotIn("gone", _strip_js_comments("a /* gone */ b"))


class TestTheFlushDrainsTheQueue(unittest.TestCase):
    """`_inflight` must settle only once the queue has DRAINED. Keystrokes typed
    during an in-flight save land in `this.dirty`, and handing them to `mark_dirty()`
    puts them behind the 1200 ms debounce while the promise resolves — so a caller
    awaiting the flush is told the work is stored while the last keystrokes sit in a
    timer. The pin writer and the preview both depend on this being honest.
    """

    def test_the_success_path_chains_rather_than_rearming(self):
        src = _canvas()
        at = src.index("this._inflight = frappe")
        block = src[at : at + 1600]
        self.assertIn("return this.save().then(", block)

    def test_it_does_not_rearm_the_debounce_on_success(self):
        src = _canvas()
        at = src.index("this._inflight = frappe")
        block = src[at : src.index(".catch(", at)]
        self.assertNotIn("this.mark_dirty()", block)


class TestTheChapterControlIsAlwaysOffered(unittest.TestCase):
    def test_it_is_not_gated_on_having_chapters(self):
        """Gating it on `this.chapters.length` is what made chapters unreachable:
        no chapters meant no control, and the control was the only place they were
        mentioned."""
        # Keyed on the DEFINITION. `src.index("render_lesson_settings()")` lands on
        # the CALL SITE hundreds of lines earlier, and the fixed 3000-char window from
        # there never reached the method at all -- so this assertion passed over source
        # that could not contain the string either way, and guarded nothing. Found by
        # an adversarial review of the checkpoint-pin plan, which edits this method.
        src = _canvas()
        at = src.index("render_lesson_settings() {")
        # Bounded from the definition, so adding lines ABOVE it cannot silently move
        # the window off the code under test the way the old call-site index did.
        block = src[at : at + 4000]
        self.assertIn("tc-add-chapter", block)
        self.assertNotIn("if (this.chapters.length) {", block)

    def test_there_is_an_escape_hatch_when_there_are_none(self):
        self.assertIn("tc-add-chapter", _canvas())


class TestTheTabletCase(unittest.TestCase):
    def test_backgrounding_saves_rather_than_prompts(self):
        """A tablet locking mid-edit on site fires visibilitychange, not
        beforeunload — and a prompt on a backgrounding tab is one nobody sees."""
        src = _canvas()
        self.assertIn("visibilitychange", src)
        at = src.index("visibilitychange")
        self.assertIn("this.save()", src[at : at + 300])


class TestTheTranscriptLoaderRefusesUntimedText(unittest.TestCase):
    def test_it_checks_for_cue_timings(self):
        """Without them, AI checkpoint drafting has nothing to place a question
        against and refuses later with no clue why."""
        src = _canvas()
        self.assertIn("load_vtt(lesson)", src)
        at = src.index("load_vtt(lesson) {")
        self.assertIn("-->", src[at : at + 1600])

    def test_it_reads_locally_rather_than_uploading(self):
        """A round trip through File storage leaves a second copy nobody maintains
        beside the one that is actually read."""
        src = _canvas()
        at = src.index("load_vtt(lesson) {")
        block = src[at : at + 1600]
        self.assertIn("FileReader", block)
        self.assertNotIn("upload_file", block)


class TestTurnIntoKeepsTheKeyAndDuplicateMintsOne(unittest.TestCase):
    """`block_key` is a relational identity, not a detail. Learner watch intervals
    and in-video checkpoints are filed under it, and `_apply_blocks` replaces the
    child table wholesale by position, minting a key only where one is blank or
    duplicated. So the rule is exact and opposite for the two verbs, and it can only
    be asserted client-side — the server cannot tell the two apart.
    """

    def _fn(self, name):
        """One class method, bounded at its own closing brace.

        Keyed on the DEFINITION. A bare name + '(lesson, block' matches the first
        CALL SITE instead, and a fixed-size window then runs past the end of the
        method into the next one. Both of those bit this file already, which is why
        the slice is anchored to the line start and ended at the brace.
        """
        src = _canvas()
        opener = "\n\t" + name + '(lesson, block'
        at = src.index(opener) + 1
        end = src.index("\n\t}", at)
        return src[at:end]

    def test_turn_into_never_assigns_a_key(self):
        """Delete-and-re-add would mint a new one and strand every learner
        mid-video, which is what an author would do by hand without this."""
        body = self._fn("turn_into")
        self.assertNotIn("block.block_key =", body)
        self.assertNotIn("block_key:", body)

    def test_turn_into_changes_the_type_in_place(self):
        self.assertIn("block.block_type = target;", self._fn("turn_into"))

    def test_duplicate_mints_a_fresh_key(self):
        """Two rows sharing a key is the one case the server rewrites, silently,
        and the author would never see it."""
        body = self._fn("duplicate_block")
        self.assertIn("block_key:", body)
        self.assertIn("Math.random()", body)

    def test_duplicate_does_not_carry_checkpoints(self):
        """They are separate documents filed under the original key. A duplicate
        that silently acquired somebody else's questions is worse than one that
        acquired none."""
        body = self._fn("duplicate_block")
        self.assertNotIn("checkpoints", body)

    def test_both_go_through_dirty_blocks(self):
        for fn in ("turn_into", "duplicate_block"):
            with self.subTest(fn=fn):
                self.assertIn("this.dirty_blocks(lesson)", self._fn(fn))


class TestTurnIntoSaysWhatItWillCost(unittest.TestCase):
    def test_it_warns_before_discarding_anything(self):
        body = _canvas()
        at = body.index("turn_into(lesson, block, target) {")
        block = body[at : at + 2200]
        self.assertIn("frappe.confirm(", block)
        self.assertIn("if (!losses.length) return apply();", block)

    def test_it_names_the_checkpoints_by_timestamp(self):
        """The canvas has had `lesson.checkpoints` on the bootstrap all along and
        thrown it away. Naming them by timestamp is the difference between a warning
        and a surprise."""
        body = _canvas()
        at = body.index("turn_losses(lesson, block, target) {")
        block = body[at : at + 1800]
        self.assertIn("lesson.checkpoints", block)
        self.assertIn("this.mmss(", block)

    def test_it_reassures_that_progress_survives(self):
        """The key is kept, so watched time stays counted -- and an author who is
        not told that will avoid the feature."""
        self.assertIn("stays counted", _canvas())


if __name__ == "__main__":
    unittest.main()


class TestCheckpointsAreReconciledAfterEverySave(unittest.TestCase):
    """`_reap_orphan_checkpoints` runs after every single `lesson.save()` and deletes
    pins whose block has stopped being a Video — which is exactly what `turn_into`
    does. Until v1.412.0 the client never heard about it.

    The visible symptom was a confirmation dialog that lied: `turn_losses` reads
    `lesson.checkpoints` to warn "you will lose 2 in-video checkpoints", and after one
    turn those rows were already deleted server-side. The author was being asked to
    weigh a cost they had already paid.
    """

    def test_the_save_response_carries_them(self):
        author = (APP / "api/training_author.py").read_text(encoding="utf-8")
        at = author.index("def save_draft_version(")
        body = author[at : author.index("\ndef ", at + 10)]
        self.assertIn('"checkpoints": _builder_checkpoints(', body)

    def test_it_reuses_the_bootstrap_builder(self):
        """Same shape from the same builder, so the client has one parser rather than
        two — and the second would be the one that drifts."""
        author = (APP / "api/training_author.py").read_text(encoding="utf-8")
        self.assertIn("def _builder_checkpoints(", author)

    def test_the_canvas_adopts_them(self):
        src = _canvas()
        self.assertIn("adopt_checkpoints(state.checkpoints)", src)

    def test_it_replaces_rather_than_merges(self):
        """A merge would preserve precisely the ghosts this exists to drop."""
        src = _canvas()
        at = src.index("adopt_checkpoints(byLesson) {")
        body = src[at : src.index("adopt_created(created) {", at)]
        self.assertIn("lesson.checkpoints = byLesson[lesson.name] || [];", body)

    def test_a_lesson_not_in_this_save_is_left_alone(self):
        """`_builder_checkpoints` omits a lesson with no checkpoints, so an absent key
        means either "saved, now has none" or "not part of this save". Only the first
        should clear the list — clearing the second would drop pins from every lesson
        the author was not editing."""
        src = _canvas()
        at = src.index("adopt_checkpoints(byLesson) {")
        body = src[at : src.index("adopt_created(created) {", at)]
        self.assertIn("_saved_names", body)
        self.assertIn("hasOwnProperty", body)

    def test_the_saved_list_is_recorded_before_it_is_read(self):
        """`_saved_names` is set from the same response; if it were assigned after
        `adopt_checkpoints` ran, every lesson would look "not part of this save" and
        nothing would ever be cleared."""
        src = _canvas()
        self.assertLess(
            src.index("this._saved_names = state.saved"),
            src.index("this.adopt_checkpoints(state.checkpoints)"),
        )


class TestInVideoCheckpointsOnTheCanvas(unittest.TestCase):
    """S4b. The canvas can now place, edit and delete in-video checkpoints.

    The port is not a copy. Twelve members the classic builder's pin code calls do
    not exist here — `paint_save_state`, `render_canvas`, `render_inspector`,
    `guard_editable`, `set_block_field`, `seek_preview`, `_pending_pins` and the
    module-level `tb_mmss` among them — so each is shimmed explicitly. An adversarial
    review of the original plan found exactly that, including one (`seek_preview`)
    whose absence would have thrown inside a `pointermove` handler on every drag.
    """

    def test_every_pin_write_chains_off_the_save(self):
        """THE point of the step. `TrainingCheckpoint._validate_block` resolves its
        block by querying `tabTraining Content Block` — and a canvas block exists only
        in memory until the 1200ms autosave lands. Writing a pin first throws "No
        content block on X has the key Y", on the commonest authoring sequence there
        is: add a Video block, drop a pin on it."""
        src = _canvas()
        at = src.index("persist_checkpoint(lesson, cp) {")
        body = src[at : at + 1400]
        self.assertIn("this.save_then(", body)
        self.assertLess(body.index("save_then"), body.index("write_checkpoint"))

    def test_there_is_no_second_debounce(self):
        """The classic has one. With the save chained there is no window for it, and a
        second uncoordinated timer beside TC_SAVE_DEBOUNCE_MS is how two writers end up
        racing over one row."""
        src = _canvas()
        at = src.index("persist_checkpoint(lesson, cp) {")
        body = src[at : at + 1400]
        self.assertNotIn("setTimeout", body)
        self.assertNotIn("_pending_pins", src)

    def test_the_checkpoint_key_is_never_minted_except_once(self):
        """`checkpoint_key` is server-owned: learner answers are filed under it. The
        only mint permitted in this file is the transient "cp-" in `add_pin`, which the
        insert response immediately replaces. Comment-stripped, because the comments
        explaining the rule name the field repeatedly."""
        src = _canvas()
        mints = re.findall(r'checkpoint_key:\s*"cp-"', src)
        self.assertEqual(len(mints), 1, "exactly one transient mint is allowed")
        self.assertIn("cp.checkpoint_key = saved.checkpoint_key", src)

    def test_the_real_key_goes_back_on_every_save(self):
        """Omitting it blanks the field and strands every answer already recorded
        against it — the single most expensive mistake available here."""
        src = _canvas()
        at = src.index("write_checkpoint(lesson, cp) {")
        body = src[at : src.index("after_checkpoint_write() {", at)]
        self.assertIn("checkpoint_key: cp.checkpoint_key", body)

    def test_a_write_repaints_pins_not_the_sheet(self):
        """A full re-render tears down the rich-text controls, so a checkpoint save
        landing seconds after a pin drag would eat whatever the author had started
        typing in a block."""
        src = _canvas()
        at = src.index("after_checkpoint_write() {")
        body = src[at : src.index("checkpoint_write_failed(error) {", at)]
        self.assertIn("refresh_pins()", body)
        self.assertNotIn("render_sheet()", body)

    def test_turning_a_video_into_something_else_drops_its_pins(self):
        """`_reap_orphan_checkpoints` deletes them server-side on the next save.
        Keeping them here would repaint ghost pins whose `cp.name` points at a deleted
        document, and the next edit would 404."""
        src = _canvas()
        at = src.index("turn_into(lesson, block, target) {")
        body = src[at : at + 2200]
        self.assertIn("lesson.checkpoints = (lesson.checkpoints || []).filter", body)

    def test_the_duplicate_still_carries_none(self):
        """The opposite rule, and it must not be undone by the one above: a duplicate
        that silently acquired somebody else's questions is worse than one that
        acquired none."""
        src = _canvas()
        at = src.index("duplicate_block(lesson, block) {")
        body = src[at : at + 1200]
        self.assertNotIn("checkpoints", body)

    def test_the_hint_no_longer_sends_authors_away_to_place_one(self):
        """Until v1.400.0 a pin could not actually be placed in the classic builder
        either: `add_pin` seeded an empty question against a `reqd` field, so the insert
        was refused on a four-second autosave."""
        src = _canvas()
        self.assertNotIn("place its checkpoints on the timeline, in the classic builder", src)


class TestAiDraftingOnTheCanvas(unittest.TestCase):
    """S8, and it is the blocker for retiring the classic builder rather than a
    nicety.

    `publish_version` refuses any course holding an `ai_generated` question with no
    `ai_reviewed_by`; `accept_ai_suggestions` is the ONLY thing that stamps a
    reviewer; and the classic builder's quiz section is read-only with no hand-add
    anywhere. So the AI drawer is the only surface on the site that can unblock a
    Triton-authored course. Production is not blocked today only because the four
    spec-authored courses have zero quiz rows — the next one will not.
    """

    def test_the_canvas_dials_the_three_ai_endpoints(self):
        src = _canvas()
        for method in ("draft_quiz_questions", "suggest_checkpoints", "accept_ai_suggestions"):
            with self.subTest(method=method):
                self.assertIn("training_ai." + method, src)

    def test_drafting_chains_off_the_save(self):
        """Peculiar to this endpoint: `_lesson()` resolves the lesson FROM THE
        DATABASE and `draft_quiz_questions` refuses below MIN_SOURCE_CHARS (120). So
        drafting against unflushed edits does not merely use stale text — it tells the
        author there is not enough written content in a lesson that is visibly full on
        their screen."""
        src = _canvas()
        for fn in ("draft_questions(lesson) {", "draft_checkpoints(lesson, block) {"):
            with self.subTest(fn=fn):
                at = src.index(fn)
                body = src[at : at + 1200]
                self.assertIn("this.save_then(", body)
                self.assertLess(body.index("save_then"), body.index("training_ai."))

    def test_there_is_no_accept_all(self):
        """Accepting IS the human review the publish gate is built on. A button that
        performs it in bulk without anyone reading anything makes the gate
        ornamental."""
        src = _canvas()
        self.assertIn("Reject all", src)
        self.assertNotIn("Accept all", src)

    def test_accepting_sends_one_suggestion_at_a_time(self):
        """The same rule expressed in the payload, not just the absence of a button."""
        src = _canvas()
        at = src.index("accept_draft(lesson, item) {")
        body = src[at : at + 900]
        self.assertIn("JSON.stringify([item])", body)

    def test_drafts_are_scoped_to_the_lesson_they_came_from(self):
        """A suggestion drafted from lesson A shown under lesson B is a question about
        content the reviewer is not looking at."""
        src = _canvas()
        at = src.index("render_ai_drawer(lesson) {")
        body = src[at : at + 600]
        self.assertIn("d.lesson !== lesson.name", body)

    def test_the_entry_points_are_gated_on_ai_enabled(self):
        """`get_builder_bootstrap` already returns `ai_enabled`; offering a button that
        the server will refuse is worse than not offering it."""
        src = _canvas()
        self.assertIn("this.ai_enabled = !!data.ai_enabled", src)
        self.assertIn("if (this.ai_enabled) {", src)

    def test_an_ungrounded_suggestion_is_marked(self):
        """The server already drops what it cannot trace back to the lesson, so this is
        belt and braces — but an ungrounded suggestion is the one a reviewer must read
        hardest and it must not look like the others."""
        src = _canvas()
        self.assertIn("is-ungrounded", src)
        css = (APP / "training/page/training_canvas/training_canvas.css").read_text(encoding="utf-8")
        self.assertIn(".tc-ai-card.is-ungrounded", css)

    def test_a_failed_draft_does_not_leave_the_drawer_busy(self):
        """A drawer stuck on "Drafting…" reads as a hang, and `suggest_checkpoints`
        genuinely refuses on the only video asset production has — its
        `transcript_source` is "None"."""
        src = _canvas()
        at = src.index("ai_failed() {")
        body = src[at : at + 400]
        self.assertIn("this.ai_reset()", body)
