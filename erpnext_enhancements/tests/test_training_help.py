"""Bench-free tests for the Help panel and the glossary behind it.

Help is the **second** learner-facing payload in this module. `_split_lesson`'s docstring says
plainly that *"nothing else in the module may assemble one"*, and the guarantee it is protecting is
that `is_correct` and `correct_text_answers` never reach a browser. Help is allowed to exist because
it is built from a different table entirely — `Training Glossary Term` — and never opens
`answer_key_json`. Most of what follows is holding that line.

The three failures worth naming:

**Serving an explanation during a quiz.** A worked example good enough to be worth writing is
usually good enough to answer a question about the thing it works through. Quiz mode is therefore
enforced by never *assembling* those fields, and that is tested by running `_serve` and looking at
what comes back — not by grepping for the word "quiz".

**Reading `is_correct` to decide what to withhold.** Help does read question stems and option text,
which is defensible because a learner is looking at both, and it reads them only to take something
away. `is_correct` is a different thing entirely and must never appear in the field lists.

**Suppressing against the client's idea of the question.** The server derives the suppression set
from the lesson's own quiz pool. A client that declares its own suppression list can declare an
empty one.

Run: python -m unittest erpnext_enhancements.tests.test_training_help
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

HELP_PY = APP_ROOT / "training" / "help.py"
API_PY = APP_ROOT / "api" / "training.py"
TRANSPORT = APP_ROOT / "public" / "js" / "training" / "transport.js"
PLAYER = APP_ROOT / "public" / "js" / "training" / "player.js"
PLAYER_CSS = APP_ROOT / "public" / "css" / "training" / "player.css"
DOCTYPE_DIR = APP_ROOT / "training" / "doctype" / "training_glossary_term"
DOCTYPE_JSON = DOCTYPE_DIR / "training_glossary_term.json"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Every tr- class the Help panel emits. The player CSS contract checks both directions, but this
#: list is restated so that removing a rule AND its only user still fails something.
HELP_CLASSES = (
    "tr-help",
    "tr-help-toggle",
    "tr-help-body",
    "tr-help-term",
    "tr-help-word",
    "tr-help-trap",
    "tr-help-plain",
    "tr-help-more",
    "tr-help-example",
    "tr-help-example-label",
    "tr-help-seealso",
    "tr-help-draft",
    "tr-help-note",
    "tr-help-error",
)


def _raw(path):
    return path.read_text(encoding="utf-8")


def _code_only(path):
    src = re.sub(r'"""[\s\S]*?"""', "", _raw(path))
    return re.sub(r"(?m)^\s*#.*$", "", src)


def _strip_js_comments(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ----------------------------------------------------------------------------- the fake frappe


class _FakeThrow(Exception):
    pass


def _install(state):
    fake = types.ModuleType("frappe")

    class PermissionError_(Exception):
        pass

    fake.PermissionError = PermissionError_
    fake.throw = lambda msg, exc=None: (_ for _ in ()).throw(_FakeThrow(str(msg)))
    fake.session = types.SimpleNamespace(user=state.get("user", "learner@example.com"))
    fake.get_roles = lambda user=None: state.get("roles", [])
    fake._ = lambda s: s
    fake.whitelist = lambda **kw: (lambda fn: fn)
    fake.get_all = lambda doctype, **kw: state["get_all"](doctype, **kw)
    fake.get_doc = lambda arg, name=None: state["get_doc"](arg, name)
    db = types.SimpleNamespace()
    db.get_value = lambda *a, **k: state.get("get_value", lambda *x, **y: None)(*a, **k)
    fake.db = db

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v, default=0: int(v) if str(v).strip() not in ("", "None", "False") else (
        1 if v is True else default
    )
    utils.escape_html = lambda s: str(s)
    utils.sanitize_html = lambda s, **kw: s
    fake.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, data=None):
            for k, v in (data or {}).items():
                setattr(self, k, v)

        def get(self, key, default=None):
            return getattr(self, key, default)

    document.Document = Document
    model.document = document

    settings = types.ModuleType(
        "erpnext_enhancements.training.doctype.training_settings.training_settings"
    )
    settings.runtime_ready = lambda: state.get("runtime", True)
    settings.is_enabled = lambda k: True

    saved = {
        k: sys.modules.get(k)
        for k in (
            "frappe",
            "frappe.utils",
            "frappe.model",
            "frappe.model.document",
            "erpnext_enhancements.training.doctype.training_settings.training_settings",
        )
    }
    sys.modules["frappe"] = fake
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document
    sys.modules["erpnext_enhancements.training.doctype.training_settings.training_settings"] = settings
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _load_help(state):
    import importlib

    saved = _install(state)
    mod = importlib.import_module("erpnext_enhancements.training.help")
    return importlib.reload(mod), saved


TERM = {
    "name": "GT-1",
    "term": "Haunching",
    "aliases": "haunch\nhaunches",
    "short_definition": "The bedding worked in under the bottom curve of a buried pipe.",
    "trade_trap": 0,
    "ordinary_meaning": "",
    "explanation": "It is what actually carries the load.",
    "example": "A trench backfilled without it settles.",
    "see_also": "Invert",
    "ai_generated": 1,
    "reviewed_by": None,
}


# ----------------------------------------------------------------------------- the answer-key line


class TestHelpNeverTouchesTheAnswerKey(unittest.TestCase):
    def test_the_field_lists_exclude_is_correct(self):
        """Help reads stems and option text — both already on the learner's screen — to decide what
        to withhold. `is_correct` is a different thing and must never be in reach."""
        state = {"get_all": lambda *a, **k: [], "get_doc": lambda *a, **k: None}
        mod, saved = _load_help(state)
        try:
            self.assertNotIn("is_correct", mod.QUESTION_FIELDS)
            self.assertNotIn("is_correct", mod.OPTION_FIELDS)
            self.assertEqual(mod.OPTION_FIELDS, ("option_text",))
            self.assertEqual(mod.QUESTION_FIELDS, ("question_text",))
        finally:
            _restore(saved)

    def test_the_module_never_names_the_answer_key(self):
        src = _code_only(HELP_PY)
        for marker in ("answer_key_json", "is_correct", "correct_text_answers", "accepted_text"):
            self.assertNotIn(marker, src, f"help.py references {marker}")

    def test_it_does_not_reuse_a_learner_facing_builder(self):
        """`_split_lesson` and `grading.public_lesson` assemble the one sanctioned learner payload.
        Help builds its own from a different table; borrowing theirs would put a second caller on
        the code that guarantee depends on."""
        src = _code_only(HELP_PY)
        for borrowed in ("_split_lesson", "public_lesson", "published_content_json"):
            self.assertNotIn(borrowed, src)


class TestQuizMode(unittest.TestCase):
    """Enforced by never assembling the fields, and tested by looking at what comes back."""

    def _serve(self, in_quiz):
        state = {"get_all": lambda *a, **k: [], "get_doc": lambda *a, **k: None}
        mod, saved = _load_help(state)
        try:
            return mod._serve(dict(TERM), in_quiz)
        finally:
            _restore(saved)

    def test_a_quiz_payload_carries_no_explanation_or_example(self):
        served = self._serve(True)
        self.assertEqual(served["explanation"], "")
        self.assertEqual(served["example"], "")
        self.assertEqual(served["see_also"], [])

    def test_it_still_carries_the_definition(self):
        """Withholding the definition too would defeat the point: a technician who can do the work
        but has never met the word should not fail for the word."""
        served = self._serve(True)
        self.assertTrue(served["short_definition"])
        self.assertEqual(served["term"], "Haunching")

    def test_outside_a_quiz_everything_is_served(self):
        served = self._serve(False)
        self.assertTrue(served["explanation"])
        self.assertTrue(served["example"])
        self.assertEqual(served["see_also"], ["Invert"])

    def test_the_payload_has_one_shape_either_way(self):
        """Same keys in both modes, so no client branch can render a key it did not expect."""
        self.assertEqual(set(self._serve(True)), set(self._serve(False)))

    def test_an_unreviewed_entry_says_so(self):
        self.assertTrue(self._serve(False)["unreviewed"])

    def test_a_reviewed_entry_does_not(self):
        state = {"get_all": lambda *a, **k: [], "get_doc": lambda *a, **k: None}
        mod, saved = _load_help(state)
        try:
            served = mod._serve(dict(TERM, reviewed_by="somebody@example.com"), False)
        finally:
            _restore(saved)
        self.assertFalse(served["unreviewed"])


class TestSuppressionIsServerSide(unittest.TestCase):
    def test_the_pool_is_read_from_the_lesson_not_the_caller(self):
        """`_pool_text` takes a lesson and looks the pool up. Nothing about which questions to
        suppress can arrive from the browser."""
        tree = ast.parse(_raw(HELP_PY))
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_pool_text"
        )
        self.assertEqual([a.arg for a in fn.args.args], ["lesson"])

    def test_the_public_entry_takes_no_question_list(self):
        tree = ast.parse(_raw(HELP_PY))
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "get_lesson_help"
        )
        args = [a.arg for a in fn.args.args]
        self.assertEqual(args, ["course", "lesson_key", "in_quiz"])
        for banned in ("questions", "question_ids", "suppress", "exclude"):
            self.assertNotIn(banned, args)

    def test_the_gate_translates_the_lesson_key(self):
        """`help_for_lesson` takes a DOCNAME. Handing it a lesson_key would match no content and
        return an empty panel rather than raising — a Help button that silently explains nothing."""
        src = _code_only(HELP_PY)
        self.assertIn("def _lesson_row(course, lesson_key):", src)
        self.assertIn("help_for_lesson(_lesson_row(course, lesson_key)", src)

    def test_visibility_is_not_re_derived(self):
        """Whether somebody may read help about a course is the same predicate as whether they may
        take it, and api/training._visible_course_names is the single implementation."""
        src = _code_only(HELP_PY)
        self.assertIn("_visible_course_names", src)


class TestTheMatcher(unittest.TestCase):
    def _compile(self):
        state = {"get_all": lambda *a, **k: [], "get_doc": lambda *a, **k: None}
        saved = _install(state)
        try:
            import importlib

            mod = importlib.reload(
                importlib.import_module(
                    "erpnext_enhancements.training.doctype.training_glossary_term.training_glossary_term"
                )
            )
            return mod.TrainingGlossaryTerm.compile_pattern, saved
        except Exception:
            _restore(saved)
            raise

    def test_whole_word_matching(self):
        compile_pattern, saved = self._compile()
        try:
            cases = [
                ("weir", "the flap at the mouth is the weir.", True),
                ("weir", "weirdly enough", False),
                ("GFCI", "a Class A GFCI trips", True),
                ("GFCI", "GFCIs everywhere", False),
                ("Link-Seal", "fit a Link-Seal through the sleeve", True),
                ("invert", "read the pipe at the invert", True),
                ("invert", "inverted siphon", False),
            ]
            for term, text, want in cases:
                with self.subTest(f"{term} in {text!r}"):
                    self.assertEqual(bool(compile_pattern(term).search(text)), want)
        finally:
            _restore(saved)

    def test_a_plural_needs_its_own_alias(self):
        """Stated as a fact rather than a wish: the field description tells authors this, and the
        glossary's aliases are written on the strength of it."""
        compile_pattern, saved = self._compile()
        try:
            self.assertFalse(compile_pattern("skimmer").search("two skimmers"))
            self.assertTrue(compile_pattern("skimmers").search("two skimmers"))
        finally:
            _restore(saved)


class TestTheDocType(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(_raw(DOCTYPE_JSON))
        self.fields = {f["fieldname"]: f for f in self.doc["fields"]}

    def test_it_lives_in_the_training_module(self):
        self.assertEqual(self.doc["module"], "Training")
        self.assertEqual(DOCTYPE_DIR.name, "training_glossary_term")
        self.assertTrue((DOCTYPE_DIR / "__init__.py").exists())

    def test_a_learner_holds_no_docperm_at_all(self):
        """The same stance the module takes on Training Question. Without it a learner could fetch
        the whole glossary from /api/resource and the mid-quiz suppression would be decoration on
        data they already had."""
        roles = {p["role"] for p in self.doc["permissions"]}
        self.assertNotIn("Training Learner", roles)
        self.assertIn("Training Manager", roles)
        self.assertIn("Training Author", roles)

    def test_the_quiz_safe_field_says_so_on_the_field(self):
        """A rule that lives only in a module docstring is a rule the next author edits past."""
        self.assertIn("DURING A QUIZ", self.fields["short_definition"]["description"])
        self.assertEqual(self.fields["short_definition"]["reqd"], 1)

    def test_the_term_is_unique_and_names_the_record(self):
        self.assertEqual(self.fields["term"]["unique"], 1)
        self.assertEqual(self.doc["autoname"], "field:term")

    def test_the_example_field_carries_the_no_invented_number_rule(self):
        self.assertIn("do NOT invent", self.fields["example"]["description"])


class TestTheWiring(unittest.TestCase):
    def test_the_endpoint_is_post_only(self):
        src = _raw(API_PY)
        block = src.split("def lesson_help(", 1)[0]
        self.assertTrue(block.rstrip().endswith('@frappe.whitelist(methods=["POST"])'))

    def test_the_endpoint_name_has_no_digits(self):
        """test_training_endpoint_surface extracts METHOD values with [a-z_]+, so an endpoint named
        help_v2 is silently absent from the extracted map and fails set-equality as 'whitelisted
        but not dialled'."""
        self.assertTrue(re.fullmatch(r"[a-z_]+", "lesson_help"))
        self.assertIn("lessonHelp: \"lesson_help\"", _raw(TRANSPORT))

    def test_the_player_dials_it(self):
        self.assertIn('call("lessonHelp"', _strip_js_comments(_raw(PLAYER)))

    def test_the_player_sends_the_course_docname(self):
        """state.course carries no `name` key, so state.course.name is undefined and
        JSON.stringify DROPS the argument — the call reaches the server with `course` missing and
        raises a bare TypeError at the learner. That exact bug shipped once on Ask-the-author."""
        js = _strip_js_comments(_raw(PLAYER))
        help_call = js.split('call("lessonHelp"', 1)[1][:400]
        self.assertIn("course: state.courseName", help_call)
        self.assertNotIn("state.course.name", help_call)

    def test_the_panel_is_a_proper_disclosure(self):
        js = _strip_js_comments(_raw(PLAYER))
        self.assertIn('toggle.setAttribute("aria-expanded"', js)
        self.assertIn('region.id = regionId', js)

    def test_every_help_class_has_a_rule(self):
        css = _raw(PLAYER_CSS)
        for cls in HELP_CLASSES:
            with self.subTest(cls):
                self.assertIn(f".{cls}", css)

    def test_no_help_rule_declares_the_palette(self):
        """--tr-* may be declared in player.css only, and the Help rules READ it rather than adding
        to it — but a new token here would still be a silent divergence from the three-way dark
        handling the rest of the file keeps."""
        css = _raw(PLAYER_CSS)
        block = css.split(".tr-help {", 1)[1].split(".tr-qa {", 1)[0]
        self.assertIsNone(re.search(r"--tr-[\w-]+\s*:", block))

    def test_this_suite_runs_in_ci(self):
        self.assertIn("erpnext_enhancements.tests.test_training_help", _raw(CI))


class TestTheGlossaryContent(unittest.TestCase):
    """The 717 seeded entries. Content, not mechanism — the mechanism is above."""

    @classmethod
    def setUpClass(cls):
        from erpnext_enhancements.training.glossary_seed import GLOSSARY_TERMS

        cls.terms = GLOSSARY_TERMS

    def test_there_is_a_glossary(self):
        self.assertGreaterEqual(len(self.terms), 400)

    def test_every_entry_has_the_two_required_fields(self):
        for t in self.terms:
            with self.subTest(t.get("term")):
                self.assertTrue((t.get("term") or "").strip())
                self.assertTrue((t.get("short_definition") or "").strip())

    def test_terms_are_unique_case_insensitively(self):
        """`Training Glossary Term` autonames on the term and the field is unique, so a collision
        is not cosmetic — it is the patch silently failing to create that entry."""
        names = [t["term"].strip().lower() for t in self.terms]
        dupes = {n for n in names if names.count(n) > 1}
        self.assertEqual(dupes, set())

    def test_no_alias_shadows_another_entry(self):
        """An alias equal to another term's head word means the longer, more specific entry can
        never win a match — the shorter one is found first and the reader gets the wrong page."""
        heads = {t["term"].strip().lower() for t in self.terms}
        for t in self.terms:
            for alias in t.get("aliases") or []:
                with self.subTest(f"{t['term']} -> {alias}"):
                    self.assertNotIn(alias.strip().lower(), heads)

    def test_no_alias_repeats_its_own_term(self):
        for t in self.terms:
            lowered = {a.strip().lower() for a in t.get("aliases") or []}
            self.assertNotIn(t["term"].strip().lower(), lowered, t["term"])

    def test_every_alias_is_long_enough_to_match_on(self):
        """`MIN_MATCHABLE` is 2, and the controller refuses anything shorter on save — so a
        one-character alias here would abort the seeding patch.

        Two rather than three because matching is whole-word: `IP` cannot match inside `IP68`, and
        twenty-one real acronyms in this glossary are two letters (CO, LB, CT, PI, FI, TI, IP).
        A single letter is still refused; it carries no signal and would match a variable in a
        formula."""
        for t in self.terms:
            for alias in t.get("aliases") or []:
                with self.subTest(f"{t['term']} -> {alias}"):
                    self.assertGreaterEqual(len(alias.strip()), 2)

    def test_the_alias_floor_matches_the_controller(self):
        """Restated from the controller rather than hard-coded twice: if somebody raises
        MIN_MATCHABLE without pruning the data, the seed throws on every short acronym."""
        from erpnext_enhancements.training.doctype.training_glossary_term import (
            training_glossary_term as ctrl,
        )

        shortest = min(
            len(a.strip()) for t in self.terms for a in (t.get("aliases") or []) if a.strip()
        )
        self.assertGreaterEqual(shortest, ctrl.MIN_MATCHABLE)

    def test_a_trade_trap_says_what_it_sounds_like(self):
        """The whole value of the flag is the contrast. Ticked with nothing to contrast against,
        Help renders a warning it cannot explain — and the controller refuses to save it."""
        for t in self.terms:
            if not t.get("trade_trap"):
                continue
            with self.subTest(t["term"]):
                self.assertTrue((t.get("ordinary_meaning") or "").strip())

    def test_nothing_carries_an_ordinary_meaning_it_will_never_show(self):
        """`_serve` only sends `ordinary_meaning` when `trade_trap` is set, so one without the flag
        is text nobody will ever read — and a sign somebody meant to tick the box."""
        for t in self.terms:
            if t.get("trade_trap"):
                continue
            self.assertFalse((t.get("ordinary_meaning") or "").strip(), t["term"])

    def test_there_are_real_trade_traps(self):
        """The highest-value entries in the set. If this collapses toward zero somebody has been
        unticking them."""
        traps = [t for t in self.terms if t.get("trade_trap")]
        self.assertGreaterEqual(len(traps), 100)

    def test_no_entry_self_references(self):
        for t in self.terms:
            lowered = {s.strip().lower() for s in t.get("see_also") or []}
            self.assertNotIn(t["term"].strip().lower(), lowered, t["term"])

    def test_every_category_exists_on_the_site(self):
        """The seeder drops an unknown category rather than failing, so a typo is silent: the term
        is filed under nothing and never appears under the filter somebody browses by."""
        live = {
            "Customer Handover",
            "ERPNext System Training",
            "Installation",
            "Safety",
            "Service & Maintenance",
            "Systems & Admin",
            "Water Chemistry",
        }
        for t in self.terms:
            if t.get("category"):
                with self.subTest(t["term"]):
                    self.assertIn(t["category"], live)

    def test_no_entry_invents_a_company_deadline_or_frequency(self):
        """Same two guards the course specs carry. A glossary is exactly where somebody writes
        'backwash every 6 weeks' without noticing they have just set a maintenance policy."""
        deadline = re.compile(
            r"(?:within|inside|no later than)\s+\d+\s*(?:hour|hours|day|days|week|weeks)", re.I
        )
        frequency = re.compile(
            r"every\s+\d+\s*(?:day|days|week|weeks|month|months|year|years)", re.I
        )
        for t in self.terms:
            text = " ".join(
                str(t.get(f) or "") for f in ("short_definition", "explanation", "example", "ordinary_meaning")
            )
            with self.subTest(t["term"]):
                hit = deadline.search(text) or frequency.search(text)
                self.assertIsNone(hit, hit.group(0) if hit else "")

    def test_the_quiz_safe_field_stays_short(self):
        """`short_definition` is what a learner reads mid-question. An essay there is both useless
        at that moment and much more likely to carry the answer."""
        for t in self.terms:
            with self.subTest(t["term"]):
                self.assertLessEqual(len(t["short_definition"]), 400)

    def test_the_data_file_is_where_the_loader_looks(self):
        from erpnext_enhancements.training import glossary_seed

        self.assertTrue(glossary_seed.GLOSSARY_PATH.exists())
        self.assertEqual(glossary_seed.GLOSSARY_PATH.suffix, ".json")

    def test_a_missing_data_file_does_not_break_the_import(self):
        """The seeding patch imports this at migrate time, and a patch that raises aborts
        `bench migrate` -- which on this repo is the deploy."""
        from erpnext_enhancements.training import glossary_seed

        src = _raw(APP_ROOT / "training" / "glossary_seed.py")
        self.assertIn("except (OSError, ValueError)", src)
        self.assertTrue(callable(glossary_seed._load))

    def test_the_patch_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.seed_training_glossary",
            _raw(APP_ROOT / "patches.txt"),
        )


if __name__ == "__main__":
    unittest.main()
