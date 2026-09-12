"""Can somebody who is not a developer actually start a course?

Nik's first ask was that the WYSIWYG be more robust, and his seventh that building
a training be simple enough that anyone can do it. Those turned out to be the same
problem approached from two ends, and neither was mainly about the editor's
features.

**The visual editor had no entry point at all.** `training-canvas` shipped in
v1.364.0 and, until v1.386.0, grepping for it outside its own directory returned
two CHANGELOG lines and its own test file. Nothing linked to it. The only way in
was to know the URL and type it — so the surface built for non-technical authors
was reachable only by people who read the changelog.

**And "New" gives you an empty form.** Most of what stops somebody authoring is
not a missing toolbar button; it is being asked to invent the content and the
shape at the same time. A starter gives away the shape.

The rule the starters follow, and the one worth pinning: **a starter is a Course
Spec and nothing else** — same validator, same builder, same block vocabulary, same
review gate as an AI-drafted course. If a starter could express something the AI
path cannot, one of the two would be wrong, and the drift would surface only when
a template produced a course the builder could not render.

Run: python -m unittest erpnext_enhancements.tests.test_training_authoring_entry
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
GALLERY = APP / "training/templates_gallery.py"
SPEC = APP / "training/course_spec.py"
AUTHORING = APP / "api/training_course_authoring.py"
COURSE_JS = APP / "public/js/training/training_course.js"
LIST_JS = APP / "public/js/training/training_course_list.js"
HOOKS = APP / "hooks.py"
CONTENT_BLOCK = APP / "training/doctype/training_content_block/training_content_block.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src):
    """`src` with whole-line JS comments removed.

    Needed because R1 writes comments that NAME the thing they explain the absence
    of: the note replacing the Open Builder button says "/app/training-builder stays
    reachable by URL". Run over raw source, an assertion that the string is gone
    matches the sentence saying it is gone. Eleventh occurrence of that trap.

    Defined here rather than imported from `test_training_canvas`, because this file
    has a `__main__` block and a cross-module import breaks the direct run with
    ModuleNotFoundError while leaving CI green."""
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


def _const(name, path):
    for node in ast.parse(_text(path)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def _templates():
    return _const("TEMPLATES", GALLERY)


class TestTheVisualEditorIsReachable(unittest.TestCase):
    def test_something_links_to_it(self):
        """It shipped with no entry point anywhere and stayed that way for two
        releases — reachable only by typing the URL."""
        self.assertIn("training-canvas", _text(COURSE_JS))

    def test_the_link_hands_over_the_course(self):
        """The page reads `course` from the query string and falls back to
        route_options, so this has to be set or the editor opens on nothing.

        The slice used to end at `function open_builder(`, which R1 deleted — so
        this raised ValueError and reported an ERROR with a stack trace, inside a
        test about the canvas. Anchored to the next function instead, whichever it
        happens to be."""
        src = _text(COURSE_JS)
        at = src.index("function open_canvas(")
        end = src.index("\nfunction ", at + 1)
        body = src[at:end]
        self.assertIn("frappe.route_options = { course: frm.doc.name }", body)

    def test_the_course_form_has_exactly_one_authoring_door(self):
        """R1 (v1.416.0). Replaces `test_the_classic_builder_is_still_there`, whose
        whole purpose was to keep the classic entry point wired — and whose docstring
        had gone stale on four of the five gaps it named (chapters v1.398.0,
        checkpoint placement v1.413.0, preview v1.415.0; only video registration and
        the quiz pool survive).

        Two authoring buttons made an author guess which editor was the real one.
        Asserted over comment-stripped source, because the comment that replaced the
        button necessarily says "training-builder"."""
        src = _strip_js_comments(_text(COURSE_JS))
        self.assertIn("training-canvas", src)
        self.assertNotIn("training-builder", src)
        self.assertNotIn("open_builder", src)

    def test_the_visual_editor_is_the_highlighted_one(self):
        """It is the surface an author who is not a developer can use, so on a draft
        it is the primary action. The paired `assertNotIn` on `$builder` was dropped
        in v1.416.0: with the const deleted it could never fire again, so it read
        like a guard while guarding nothing."""
        src = _text(COURSE_JS)
        self.assertIn('$canvas.addClass("btn-primary")', src)


class TestStartersAreCourseSpecs(unittest.TestCase):
    """Not a second scaffolder. Same validator, same builder, same vocabulary."""

    def test_the_builder_is_the_ai_one(self):
        src = _text(AUTHORING)
        at = src.index("def create_from_starter(")
        body = src[at : src.index("def author_course_from_spec(")]
        self.assertIn("author_course_from_spec(", body)

    def test_every_starter_block_type_is_one_a_spec_allows(self):
        """A template that used a block type the spec refuses would fail at
        creation, and only for that starter."""
        allowed = set(_const("SPEC_BLOCK_TYPES", SPEC))
        used = set(re.findall(r'"block_type": "([^"]+)"', _text(GALLERY)))
        self.assertEqual(sorted(used - allowed), [])

    def test_every_starter_tone_is_one_the_doctype_declares(self):
        """`callout_tone` is a Select and `_validate_selects` throws on an unknown
        value — the same defect that killed lesson autosave on the canvas."""
        import json

        declared = {
            o
            for o in next(
                f
                for f in json.loads(_text(CONTENT_BLOCK))["fields"]
                if f["fieldname"] == "callout_tone"
            )["options"].split("\n")
            if o
        }
        used = set(re.findall(r'"callout_tone": "([^"]+)"', _text(GALLERY)))
        self.assertEqual(sorted(used - declared), [])

    def test_the_spec_and_the_doctype_agree_on_tones(self):
        """Three files hold this vocabulary and they have already drifted once."""
        import json

        declared = {
            o
            for o in next(
                f
                for f in json.loads(_text(CONTENT_BLOCK))["fields"]
                if f["fieldname"] == "callout_tone"
            )["options"].split("\n")
            if o
        }
        self.assertEqual(set(_const("CALLOUT_TONES", SPEC)), declared)

    def test_every_starter_has_at_least_one_lesson(self):
        """`validate_course_spec` requires it, so a starter without one would fail
        only when somebody picked it."""
        for key, template in _templates().items():
            with self.subTest(starter=key):
                self.assertTrue(template["spec"].get("lessons"))

    def test_a_chaptered_starter_indexes_real_chapters(self):
        """`chapter` is a 0-based index into `chapters`; an out-of-range one
        strands the lesson outside the outline with no hint why."""
        for key, template in _templates().items():
            spec = template["spec"]
            chapters = len(spec.get("chapters") or [])
            for lesson in spec["lessons"]:
                if "chapter" in lesson:
                    with self.subTest(starter=key, lesson=lesson["lesson_title"]):
                        self.assertLess(lesson["chapter"], chapters)

    def test_the_gallery_hands_out_a_copy(self):
        """TEMPLATES is module-level state shared by every request in the worker.
        Handing out the live dict would let one author's retitle leak into the next
        author's course — cheap to do, very confusing to find."""
        src = _text(GALLERY)
        at = src.index("def spec_for(")
        self.assertIn("copy.deepcopy", src[at:])

    def test_the_prose_is_instructions_not_filler(self):
        """Filler is worse than an empty page, because filler gets published."""
        blob = _text(GALLERY)
        self.assertIn("Replace with", blob)
        self.assertIn("Replace this with", blob)


class TestTheGalleryIsReachable(unittest.TestCase):
    def test_the_endpoints_exist(self):
        src = _text(AUTHORING)
        self.assertIn("def list_starters(", src)
        self.assertIn("def create_from_starter(", src)

    def test_both_are_author_gated(self):
        src = _text(AUTHORING)
        for fn in ("list_starters", "create_from_starter"):
            with self.subTest(fn=fn):
                at = src.index(f"def {fn}(")
                nxt = src.find("\ndef ", at)
                self.assertIn("_require_author()", src[at : nxt if nxt != -1 else len(src)])

    def test_author_course_from_spec_kept_its_decorator(self):
        """It is the AI path's entry point. Inserting functions above it is an easy
        way to orphan a decorator onto the wrong def, which silently un-whitelists
        the endpoint Triton calls."""
        tree = ast.parse(_text(AUTHORING))
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "author_course_from_spec"
        )
        self.assertEqual(len(node.decorator_list), 1)

    def test_the_list_view_offers_them(self):
        src = _text(LIST_JS)
        self.assertIn("list_starters", src)
        self.assertIn("create_from_starter", src)

    def test_the_list_script_is_wired_into_hooks(self):
        """This module has shipped a complete feature with no caller twice."""
        self.assertIn(
            '"Training Course": "public/js/training/training_course_list.js"', _text(HOOKS)
        )

    def test_creating_a_starter_lands_in_the_editor(self):
        """The next thing an author does with a starter is replace the words.
        Landing on the course form would mean finding the editor first."""
        src = _text(LIST_JS)
        at = src.index("create_from_starter")
        self.assertIn("training-canvas", src[at : at + 900])

    def test_the_ai_button_is_only_shown_where_it_works(self):
        """A button that opens nothing is worse than no button — the 'Open Builder'
        placeholder outlived its feature by three releases and told everyone the
        builder did not exist."""
        self.assertIn("window.SapphireTriton && window.SapphireTriton.ask", _text(LIST_JS))

    def test_starter_text_is_not_interpolated_into_markup(self):
        """These strings are ours today, and a template somebody adds later goes
        through the same renderer."""
        src = _text(LIST_JS)
        at = src.index("function render_gallery(")
        body = src[at:]
        self.assertIn(".text(starter.label)", body)
        self.assertIn(".text(starter.blurb)", body)


class TestOrphanedCheckpointsAreReaped(unittest.TestCase):
    """v1.396.0. A Training Checkpoint is a TOP-LEVEL document keyed on `block_key`,
    so removing a block strands it: `_apply_blocks` replaces the child table wholesale
    and `Training Lesson.on_trash` cascades only when the whole lesson goes. The
    classic builder deleted them client-side; the canvas does not and cannot, since it
    does not model checkpoints at all.
    """

    AUTHOR_PY = Path(__file__).resolve().parents[1] / "api/training_author.py"

    def _src(self):
        return self.AUTHOR_PY.read_text(encoding="utf-8")

    def _fn_src(self, name):
        """Source of one function, DOCSTRING STRIPPED.

        Every assertion below that checks a token is ABSENT would otherwise match
        the docstring explaining why it is absent -- the trap this repo has now hit
        five times. Strip the prose, assert on the code.
        """
        src = self._src()
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                body = node.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                if not body:
                    return ""
                return "\n".join(lines[body[0].lineno - 1 : node.end_lineno])
        raise AssertionError(f"{name} not found")

    def test_the_reaper_exists_and_runs_after_the_save(self):
        """After, not before: `_assign_block_keys` can mint a key during validate, so
        reading the table before the save would reap a block about to get one."""
        body = self._fn_src("save_draft_version")
        self.assertIn("_reap_orphan_checkpoints(lesson)", body)
        self.assertLess(
            body.index("lesson.save(ignore_permissions=True)"),
            body.index("_reap_orphan_checkpoints(lesson)"),
        )

    def test_it_filters_in_python_not_in_sql(self):
        """db_query coalesces the column, so an empty key set compiles to
        `NOT IN ('')` and matches every row -- the same shape as the PAD SPACE traps."""
        body = self._fn_src("_reap_orphan_checkpoints")
        self.assertNotIn('"not in"', body)
        self.assertIn("block_key", body)

    def test_it_does_not_force_the_delete(self):
        """A checkpoint still referenced by an answered attempt is exactly the one
        that must not vanish. LinkExistsError is logged and skipped, not overridden."""
        body = self._fn_src("_reap_orphan_checkpoints")
        self.assertNotIn("force=1", body)
        self.assertNotIn("force=True", body)
        self.assertIn("LinkExistsError", body)

    def test_the_clone_path_refuses_to_carry_an_orphan_forward(self):
        """`save_draft_version` refuses anything that is not docstatus 0, so it can
        never reach a published version's checkpoints. `_clone_lessons` is the only
        function that touches them, and without a guard one stranded row breeds into
        every later version."""
        body = self._fn_src("_clone_lessons")
        self.assertIn("live_keys", body)
        at = body.index("Training Checkpoint")
        self.assertIn("continue", body[at : at + 600])


if __name__ == "__main__":
    unittest.main()
