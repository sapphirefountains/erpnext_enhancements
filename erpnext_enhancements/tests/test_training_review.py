"""Bench-free tests for the AI question review queue.

The screen exists because the publish gate has always implied one and the app never had it:
`_unreviewed_ai_questions` blocks `submit_for_review` and `publish_version`, and measured on
production 2026-09-15 it was holding **128 questions across all 11 Draft courses** with 14 ever
reviewed. The ten Technician Program drafts add 239 more.

Four things are guarded here, and each is a way this could be quietly wrong rather than broken.

**The queue's predicate must be the gate's predicate.** If they drift, the page says "nothing left"
while `publish_version` still refuses — and the reviewer has no way to find out why. So the filter
literal is extracted from `api/training_author._unreviewed_ai_questions` and compared against
`review.PENDING_FILTER`.

**The reviewer must come from the session.** `ai_reviewed_by` exists to record *who vouched for this
answer key*. Until this module the only writer was the browser, via core `frappe.client.save` with
the reviewer in the request body — an attestation the caller chose. These tests run the real
`accept_question` against a fake frappe and assert the stamp is the session user even when the
payload tries to name somebody else.

**Reject must not be able to wedge a lesson.** `TrainingLesson._validate_quiz` throws when
`has_quiz` is ticked and the pool is empty, so removing a lesson's last question makes that lesson
unsaveable by anybody. `reject_question` refuses unless the reviewer confirms, and that is tested
behaviourally rather than by grepping for the word.

**There must be no bulk accept.** A button that clears a course in one click turns the gate into
theatre. The endpoint set is pinned by set equality, so adding one is a deliberate act that fails
the build first.

Run: python -m unittest erpnext_enhancements.tests.test_training_review
"""

import ast
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REVIEW_PY = APP_ROOT / "training" / "review.py"
AUTHOR_PY = APP_ROOT / "api" / "training_author.py"
PAGE_DIR = APP_ROOT / "training" / "page" / "training_review"
PAGE_JSON = PAGE_DIR / "training_review.json"
PAGE_JS = PAGE_DIR / "training_review.js"
PAGE_CSS = PAGE_DIR / "training_review.css"
CANVAS_JSON = APP_ROOT / "training" / "page" / "training_canvas" / "training_canvas.json"
NAV_JS = APP_ROOT / "public" / "js" / "training" / "desk_nav.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: The four endpoints this module is allowed to expose. Set equality, not a subset: a fifth one
#: named `accept_all` or `accept_course` would pass a subset check, and it is exactly the thing
#: this screen must not grow.
ENDPOINTS = {"get_review_queue", "get_review_lesson", "accept_question", "reject_question"}


def _raw(path):
    return path.read_text(encoding="utf-8")


def _strip_js_comments(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ----------------------------------------------------------------------------- the fake frappe


class _FakeThrow(Exception):
    pass


class _Doc(dict):
    """Just enough Document for review.py: attribute access, .set, .append, .save."""

    def __init__(self, data=None):
        super().__init__(data or {})
        self.saved = 0

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        if key == "saved":
            super().__setattr__(key, value)
        else:
            self[key] = value

    def set(self, field, value):
        self[field] = value

    def append(self, field, row):
        self.setdefault(field, []).append(_Doc(row))

    def save(self, **kwargs):
        self.saved += 1
        return self


def _install_fake_frappe(state):
    """Put a fake `frappe` in sys.modules and return it. Caller restores."""
    fake = types.ModuleType("frappe")

    class PermissionError_(Exception):
        pass

    def throw(msg, exc=None):
        raise _FakeThrow(str(msg))

    fake.PermissionError = PermissionError_
    fake.throw = throw
    # A no-op decorator factory. These tests call the endpoints directly; what
    # @frappe.whitelist() does at runtime — register the dotted path as callable over HTTP — is
    # not what is under test here, and `test_the_endpoint_surface` checks the decorators statically.
    fake.whitelist = lambda **kw: (lambda fn: fn)
    fake.session = types.SimpleNamespace(user=state["user"])
    fake.get_roles = lambda user=None: state["roles"]
    fake._ = lambda s: s
    fake.get_traceback = lambda: "traceback"
    fake.log_error = lambda **kw: state.setdefault("logged", []).append(kw)

    def get_doc(arg, name=None):
        if isinstance(arg, dict):  # inserting a Comment
            state.setdefault("inserted", []).append(arg)
            doc = _Doc(arg)
            doc.insert = lambda **kw: state.setdefault("comments", []).append(arg)
            return doc
        return state["docs"][(arg, name)]

    fake.get_doc = get_doc
    fake.get_all = lambda doctype, **kw: state["get_all"](doctype, **kw)
    fake.delete_doc = lambda *a, **k: state.setdefault("deleted", []).append(a)

    db = types.SimpleNamespace()
    db.count = lambda doctype, filters=None: state["count"](doctype, filters)
    db.exists = lambda doctype, filters=None: state.get("exists", lambda *a: False)(doctype, filters)
    fake.db = db

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v, default=0: int(v) if str(v).strip() not in ("", "None") else default
    utils.escape_html = lambda s: str(s).replace("<", "&lt;").replace(">", "&gt;")
    utils.sanitize_html = lambda s, **kw: s
    fake.utils = utils

    sys.modules["frappe"] = fake
    sys.modules["frappe.utils"] = utils
    return fake


def _load_review(state):
    """Import `training.review` fresh against a fake frappe. Returns the module."""
    import importlib

    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
    _install_fake_frappe(state)
    try:
        mod = importlib.import_module("erpnext_enhancements.training.review")
        return importlib.reload(mod)
    finally:
        state["_restore"] = saved


def _restore(state):
    for k, v in (state.get("_restore") or {}).items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ----------------------------------------------------------------------------- static contracts


class TestTheQueueMatchesTheGate(unittest.TestCase):
    """If the queue and the publish gate disagree about what 'pending' means, the page says
    'nothing left' while publish_version still refuses — and nothing on screen explains why."""

    def test_the_gate_filter_is_what_we_think_it_is(self):
        src = _raw(AUTHOR_PY)
        body = src.split("def _unreviewed_ai_questions(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"ai_generated": 1', body)
        self.assertIn('"ai_reviewed_by": ["is", "not set"]', body)

    def test_review_restates_exactly_that_filter(self):
        src = _raw(REVIEW_PY)
        match = re.search(r"PENDING_FILTER = (\{[^}]*\})", src)
        self.assertIsNotNone(match, "PENDING_FILTER is not a plain dict literal any more")
        literal = match.group(1)
        self.assertIn('"ai_generated": 1', literal)
        self.assertIn('"ai_reviewed_by": ["is", "not set"]', literal)

    def test_the_two_literals_are_equal_as_data(self):
        """Not a substring check on either side — both parsed and compared."""
        review_literal = re.search(r"PENDING_FILTER = (\{[^}]*\})", _raw(REVIEW_PY)).group(1)
        gate_body = _raw(AUTHOR_PY).split("def _unreviewed_ai_questions(", 1)[1].split("\ndef ", 1)[0]
        gate_literal = re.search(
            r"(\{\s*\"name\":[^}]*\})", gate_body.replace("\n", " ")
        ).group(1)
        # The gate's dict carries `"name": ["in", rows]` — `rows` is a variable, so the literal
        # does not parse as-is. That key is the gate scoping itself to one version and is no part
        # of the predicate; substitute it out rather than loosening the comparison of the two keys
        # that actually decide what "pending" means.
        gate_filter = ast.literal_eval(gate_literal.replace("rows", "[]"))
        gate_filter.pop("name", None)
        self.assertEqual(ast.literal_eval(review_literal), gate_filter)


class TestTheEndpointSurface(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(_raw(REVIEW_PY))

    def _whitelisted(self):
        out = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                    out[node.name] = node
        return out

    def test_exactly_the_four_endpoints(self):
        """Set equality. A fifth endpoint named accept_all or accept_course would pass a subset
        check, and it is precisely what this screen must not grow."""
        self.assertEqual(set(self._whitelisted()), ENDPOINTS)

    def test_every_endpoint_checks_the_role_itself(self):
        """`frappe.get_all` sets ignore_permissions=True unconditionally, so the role gate is the
        only thing keeping this data in the right hands — including the answer key."""
        for name, node in self._whitelisted().items():
            calls = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            with self.subTest(name):
                self.assertIn("_reviewer", calls, f"{name} does not call _reviewer()")

    def test_no_endpoint_takes_a_reviewer_from_the_caller(self):
        """The attestation cannot be addressed to somebody else."""
        for name, node in self._whitelisted().items():
            args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            with self.subTest(name):
                for banned in ("user", "reviewed_by", "ai_reviewed_by", "reviewer"):
                    self.assertNotIn(banned, args, f"{name} accepts {banned} from the caller")

    def test_no_private_function_is_whitelisted(self):
        """test_whitelist_placement enforces this app-wide; asserted here too because this module
        is one careless blank line away from decorating `_reviewer`."""
        for name in self._whitelisted():
            self.assertFalse(name.startswith("_"), name)

    def test_the_module_is_tab_indented(self):
        for i, line in enumerate(_raw(REVIEW_PY).split("\n"), 1):
            if line.startswith("    ") and not line.startswith("\t"):
                self.fail(f"review.py line {i} is space-indented")


class TestThePage(unittest.TestCase):
    def test_the_page_directory_has_the_four_files(self):
        for path in (PAGE_JSON, PAGE_JS, PAGE_CSS, PAGE_DIR / "__init__.py"):
            with self.subTest(path.name):
                self.assertTrue(path.exists(), f"{path.name} is missing")

    def test_the_docname_matches_the_folder(self):
        """Frappe's Page.load_assets does `scrub(self.name)` and reads `<that>/<that>.js`. Get the
        pairing wrong and the page loads with an empty script and no error anywhere."""
        doc = json.loads(_raw(PAGE_JSON))
        self.assertEqual(doc["name"], "training-review")
        self.assertEqual(doc["page_name"], "training-review")
        self.assertEqual(PAGE_DIR.name, "training_review")
        self.assertEqual(PAGE_JS.name, "training_review.js")
        self.assertEqual(PAGE_CSS.name, "training_review.css")

    def test_the_docname_shadows_no_workspace(self):
        """One URL with two destinations otherwise: frappe.router resolves segment 0 against
        workspaces before pages, and allowed_workspaces is permission-filtered."""
        slugs = set()
        for path in APP_ROOT.rglob("workspace/*/*.json"):
            name = json.loads(_raw(path)).get("name", "")
            slugs.add(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
        self.assertNotIn(json.loads(_raw(PAGE_JSON))["name"], slugs)

    def test_it_is_a_standard_module_page(self):
        doc = json.loads(_raw(PAGE_JSON))
        self.assertEqual(doc["standard"], "Yes")
        self.assertEqual(doc["module"], "Training")
        self.assertEqual(doc["system_page"], 0)

    def test_the_roles_match_the_canvas(self):
        """The rail gates this entry on AUTHOR_ROLES, which test_training_desk_nav pins to
        training_canvas.json. If the page's own roles differ, the rail offers a link the Page
        refuses — which reads as the feature being broken rather than as not being yours."""
        page_roles = {r["role"] for r in json.loads(_raw(PAGE_JSON))["roles"]}
        canvas_roles = {r["role"] for r in json.loads(_raw(CANVAS_JSON))["roles"]}
        self.assertEqual(page_roles, canvas_roles)

    def test_the_server_roles_match_the_page_roles(self):
        """A Page's role list is show/hide, never a permission boundary — the endpoint re-checks.
        These two must agree or somebody is offered a screen whose every call throws."""
        page_roles = {r["role"] for r in json.loads(_raw(PAGE_JSON))["roles"]}
        literal = re.search(r"REVIEWER_ROLES = (\{[^}]*\})", _raw(REVIEW_PY)).group(1)
        self.assertEqual(ast.literal_eval(literal), page_roles)


class TestTheRail(unittest.TestCase):
    def setUp(self):
        self.src = _strip_js_comments(_raw(NAV_JS))

    def test_the_rail_offers_the_page(self):
        self.assertIn('route: ["training-review"]', self.src)

    def test_the_key_is_unique(self):
        """`this.rows[spec.key]` is one flat map shared by both sections; a duplicate key orphans
        the first element so it can never be painted active again."""
        keys = re.findall(r'key: "([a-z0-9]+)"', self.src)
        self.assertIn("review", keys)
        self.assertEqual(len(keys), len(set(keys)), f"duplicate nav key in {keys}")

    def test_it_is_author_gated_not_manager_gated(self):
        """Reviewing a drafted question is authoring work on an unpublished draft."""
        line = next(l for l in self.src.split("\n") if '"training-review"' in l)
        self.assertIn("if (author)", line)

    def test_it_did_not_add_a_third_section(self):
        """test_training_desk_nav asserts `src.count("return this.reachable(links);") == 2`."""
        self.assertEqual(_raw(NAV_JS).count("return this.reachable(links);"), 2)


class TestTheFrontEndDiscipline(unittest.TestCase):
    def test_the_page_calls_exactly_the_four_endpoints(self):
        """Set equality against review.py. A typo'd method name fails silently at runtime with a
        server error the page would have to render; a method the page never calls is dead weight."""
        js = _raw(PAGE_JS)
        called = set(re.findall(r"erpnext_enhancements\.training\.review\.([a-z_]+)", js))
        self.assertEqual(called, ENDPOINTS)

    def test_no_stylesheet_or_script_declares_the_learner_palette(self):
        """test_training_desk_theme allows exactly two files to declare a --tr- custom property and
        neither is this page. It does NOT strip comments, so prose counts too."""
        for path in (PAGE_JS, PAGE_CSS):
            with self.subTest(path.name):
                self.assertIsNone(re.search(r"--tr-[\w-]+\s*:", _raw(path)))

    def test_every_class_uses_the_new_prefix(self):
        """tr-, tc-, tl-, ti-, tn- and tpr- belong to other surfaces."""
        classes = set(re.findall(r"\.(t[a-z]+)-[a-z0-9-]+\s*[,{.:]", _raw(PAGE_CSS)))
        self.assertTrue(classes, "no prefixed classes found in the stylesheet at all")
        self.assertEqual(classes, {"tq"}, f"unexpected prefixes: {classes}")

    def test_it_does_not_pull_the_player_stylesheet(self):
        """127KB of learner palette on a desk console, for nothing."""
        self.assertNotIn("player.css", _raw(PAGE_JS))

    def test_it_tears_its_keyboard_handler_down(self):
        """There is no on_page_hide hook in v16 and a Desk page div stays in the DOM after you
        navigate away — an un-removed document handler fires on every other page in the Desk."""
        js = _strip_js_comments(_raw(PAGE_JS))
        self.assertIn('"hide"', js)

    def test_the_narrow_breakpoint_is_frappes_own(self):
        """Written as 991 the pair leaves a gap at every fractional width, which browser zoom
        produces routinely."""
        css = _raw(PAGE_CSS)
        if "max-width" in css:
            self.assertIn("991.98px", css)

    def test_both_files_are_tab_indented(self):
        for path in (PAGE_JS, PAGE_CSS):
            for i, line in enumerate(_raw(path).split("\n"), 1):
                if line.startswith("    ") and not line.startswith("\t"):
                    self.fail(f"{path.name} line {i} is space-indented")

    def test_there_is_no_bulk_accept(self):
        """The gate is the only thing between a machine-written answer key and somebody's
        compliance record. Fast is the goal; skippable is not."""
        js = _strip_js_comments(_raw(PAGE_JS)).lower()
        for banned in ("accept_all", "acceptall", "accept all", "accept every", "accept the rest"):
            self.assertNotIn(banned, js)


class TestItIsWiredIntoCI(unittest.TestCase):
    def test_this_suite_runs_in_ci(self):
        self.assertIn("erpnext_enhancements.tests.test_training_review", _raw(CI))


# ----------------------------------------------------------------------------- behaviour


class TestAcceptStampsTheSessionUser(unittest.TestCase):
    """The reason this module exists rather than another `frappe.client.save` from the browser."""

    def _run(self, payload_extra=None):
        doc = _Doc({"name": "Q1", "ai_generated": 1, "question_text": "stem", "options": []})
        state = {
            "user": "reviewer@example.com",
            "roles": ["Training Author"],
            "docs": {("Training Question", "Q1"): doc},
            "get_all": lambda dt, **kw: [],
            "count": lambda dt, f=None: 0,
        }
        mod = _load_review(state)
        try:
            result = mod.accept_question("Q1", **(payload_extra or {}))
        finally:
            _restore(state)
        return doc, result

    def test_the_stamp_is_the_session_user(self):
        doc, result = self._run()
        self.assertEqual(doc["ai_reviewed_by"], "reviewer@example.com")
        self.assertEqual(result["reviewed_by"], "reviewer@example.com")
        self.assertEqual(doc.saved, 1)

    def test_the_payload_cannot_name_a_different_reviewer(self):
        """TypeError is the correct failure: the parameter does not exist, so a caller cannot even
        express the idea. This is stronger than ignoring the value."""
        with self.assertRaises(TypeError):
            self._run({"ai_reviewed_by": "someone.else@example.com"})

    def test_a_hand_written_question_cannot_be_accepted(self):
        """`ai_reviewed_by` means a human accepted the MACHINE's answer key. Setting it on a
        hand-written question puts a reviewer's name against work nobody drafted for them."""
        doc = _Doc({"name": "Q2", "ai_generated": 0})
        state = {
            "user": "reviewer@example.com",
            "roles": ["Training Manager"],
            "docs": {("Training Question", "Q2"): doc},
            "get_all": lambda dt, **kw: [],
            "count": lambda dt, f=None: 0,
        }
        mod = _load_review(state)
        try:
            with self.assertRaises(_FakeThrow):
                mod.accept_question("Q2")
        finally:
            _restore(state)
        # Not "is None" — the field was never written at all, and the throw happened before the
        # save, so nothing reached the document.
        self.assertNotIn("ai_reviewed_by", doc)
        self.assertEqual(doc.saved, 0)


class TestOnlyAReviewerGetsIn(unittest.TestCase):
    def test_a_learner_is_refused(self):
        state = {
            "user": "learner@example.com",
            "roles": ["Training Learner"],
            "docs": {},
            "get_all": lambda dt, **kw: [],
            "count": lambda dt, f=None: 0,
        }
        mod = _load_review(state)
        try:
            with self.assertRaises(_FakeThrow):
                mod.get_review_queue()
        finally:
            _restore(state)


class TestRejectCannotWedgeALesson(unittest.TestCase):
    """`TrainingLesson._validate_quiz` throws when has_quiz is ticked and the pool is empty, so
    removing a lesson's last question makes that lesson unsaveable by anybody until somebody works
    out why. Tested behaviourally — a source grep for 'drop_quiz' would pass on a version that
    never checked it."""

    def _state(self, pool_size, has_quiz=1):
        lesson = _Doc(
            {
                "name": "L1",
                "lesson_title": "A lesson",
                "has_quiz": has_quiz,
                "quiz_questions": [_Doc({"question": "Q1"})],
            }
        )
        question = _Doc({"name": "Q1", "ai_generated": 1, "question_text": "stem", "is_bank_question": 0})

        def get_all(doctype, **kw):
            if doctype == "Training Quiz Question":
                return [_Doc({"parent": "L1", "question": "Q1"})]
            if doctype == "Training Lesson":
                return [
                    _Doc(
                        {
                            "name": "L1",
                            "lesson_title": "A lesson",
                            "course_version": "V1",
                            "chapter_key": "c",
                            "idx_in_chapter": 1,
                            "has_quiz": has_quiz,
                            "creation": "2026-01-01",
                        }
                    )
                ]
            if doctype == "Training Course Version":
                return [_Doc({"name": "V1", "course": "C1", "version_number": 1})]
            if doctype == "Training Course":
                return [_Doc({"name": "C1", "course_title": "A course", "status": "Draft"})]
            return []

        return {
            "user": "reviewer@example.com",
            "roles": ["Training Manager"],
            "docs": {("Training Question", "Q1"): question, ("Training Lesson", "L1"): lesson},
            "get_all": get_all,
            "count": lambda dt, f=None: pool_size,
            "exists": lambda dt, f=None: False,
        }, lesson, question

    def test_it_refuses_to_empty_a_ticked_quiz(self):
        state, lesson, _q = self._state(pool_size=1)
        mod = _load_review(state)
        try:
            with self.assertRaises(_FakeThrow) as caught:
                mod.reject_question("Q1", "the key is wrong")
        finally:
            _restore(state)
        self.assertIn("A lesson", str(caught.exception))
        self.assertEqual(lesson.saved, 0, "the lesson was saved despite the refusal")

    def test_confirming_unticks_the_quiz_and_proceeds(self):
        state, lesson, _q = self._state(pool_size=1)
        mod = _load_review(state)
        try:
            result = mod.reject_question("Q1", "the key is wrong", drop_quiz=1)
        finally:
            _restore(state)
        self.assertEqual(lesson["has_quiz"], 0)
        self.assertEqual(lesson["quiz_questions"], [])
        self.assertEqual(lesson.saved, 1)
        self.assertEqual(result["removed_from"], ["A lesson"])

    def test_a_lesson_with_other_questions_needs_no_confirmation(self):
        state, lesson, _q = self._state(pool_size=3)
        mod = _load_review(state)
        try:
            result = mod.reject_question("Q1", "the key is wrong")
        finally:
            _restore(state)
        self.assertEqual(lesson.saved, 1)
        self.assertEqual(lesson["has_quiz"], 1, "has_quiz was unticked without being asked")
        self.assertIn("A lesson", result["removed_from"])

    def test_a_rejection_needs_a_reason(self):
        """The question is about to be deleted; the reason is the only surviving record of it."""
        state, lesson, _q = self._state(pool_size=3)
        mod = _load_review(state)
        try:
            with self.assertRaises(_FakeThrow):
                mod.reject_question("Q1", "   ")
        finally:
            _restore(state)
        self.assertEqual(lesson.saved, 0)

    def test_an_accepted_question_cannot_then_be_rejected(self):
        state, lesson, question = self._state(pool_size=3)
        question["ai_reviewed_by"] = "someone@example.com"
        mod = _load_review(state)
        try:
            with self.assertRaises(_FakeThrow):
                mod.reject_question("Q1", "changed my mind")
        finally:
            _restore(state)
        self.assertEqual(lesson.saved, 0)


if __name__ == "__main__":
    unittest.main()
