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
    "tr-help-example-body",
    "tr-help-seealso",
    "tr-help-seealso-link",
    "tr-help-search",
    "tr-help-search-input",
    "tr-help-found",
    "tr-help-found-head",
    "tr-gloss",
    "tr-gloss-pop",
    "tr-gloss-word",
    "tr-gloss-trap",
    "tr-gloss-plain",
    "tr-gloss-more",
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


TERM_B = {
    "name": "GT-2",
    "term": "Invert",
    "aliases": "",
    "short_definition": "The inside bottom of a pipe.",
    "trade_trap": 1,
    "ordinary_meaning": "Turning something upside down.",
    "explanation": "<p>The level you actually measure to.</p>",
    "example": "A drain tie-in that misses by an inch.",
    "see_also": "Haunching",
    "ai_generated": 1,
    "reviewed_by": None,
}


class _Glossary:
    """A fake site holding two terms, one lesson and one quiz pool."""

    def __init__(self, lesson_text="", pool_text="", terms=(TERM, TERM_B)):
        self.terms = [dict(t) for t in terms]
        self.lesson_text = lesson_text
        self.pool_text = pool_text

    def get_all(self, doctype, **kw):
        if doctype == "Training Glossary Term":
            return [dict(t) for t in self.terms]
        if doctype == "Training Content Block":
            return [{"heading": "", "content": self.lesson_text, "caption": "", "data": ""}]
        if doctype == "Training Quiz Question":
            return ["Q-1"] if self.pool_text else []
        if doctype == "Training Question":
            return [{"question_text": self.pool_text}] if self.pool_text else []
        if doctype == "Training Answer Option":
            return []
        return []

    def load(self):
        return _load_help({"get_all": self.get_all, "get_doc": lambda *a, **k: None})


class TestTheOrderTheLessonUsesThem(unittest.TestCase):
    """A-Z is the wrong order for a list of fifty-seven.

    The word somebody has just read sits at a random position in an alphabetical list, so the panel
    reads as a dictionary bolted to the page rather than as a key to the thing in front of them.
    """

    def _terms(self, text):
        site = _Glossary(lesson_text=text)
        mod, saved = site.load()
        try:
            return [e["term"] for e in mod.help_for_lesson("LSN-1", False)["terms"]]
        finally:
            _restore(saved)

    def test_first_mentioned_comes_first(self):
        self.assertEqual(self._terms("The invert matters, and haunching carries it."), ["Invert", "Haunching"])

    def test_and_the_other_way_round(self):
        self.assertEqual(self._terms("Haunching first, then the invert."), ["Haunching", "Invert"])

    def test_an_alias_counts_as_an_appearance(self):
        """"haunch" is an alias of Haunching. A lesson that only ever says the alias still used the
        word, and ordering on the term's own spelling alone would put it last."""
        self.assertEqual(self._terms("Pack the haunch, then check the invert."), ["Haunching", "Invert"])


class TestTheSpellingsComeBack(unittest.TestCase):
    """The player marks the word where the lesson uses it, so it needs to know how it was spelled."""

    def _entry(self, text, term="Haunching"):
        site = _Glossary(lesson_text=text)
        mod, saved = site.load()
        try:
            for entry in mod.help_for_lesson("LSN-1", False)["terms"]:
                if entry["term"] == term:
                    return entry
            return None
        finally:
            _restore(saved)

    def test_the_matched_alias_is_returned(self):
        entry = self._entry("Pack the haunch properly.")
        self.assertIn("haunch", entry["spellings"])

    def test_every_matched_spelling_is_returned_not_just_the_first(self):
        """The old matcher stopped at the first spelling that hit, which answers "is it here" and
        cannot tell the player which words in the text to mark."""
        entry = self._entry("Haunching, haunch and haunches all appear.")
        self.assertEqual(sorted(entry["spellings"], key=str.lower), ["haunch", "haunches", "Haunching"])

    def test_longest_first(self):
        """So the client can try them in order and a short spelling cannot claim the first half of
        a long one."""
        entry = self._entry("Haunching, haunch and haunches all appear.")
        lengths = [len(s) for s in entry["spellings"]]
        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_a_spelling_that_did_not_match_is_absent(self):
        entry = self._entry("Only the haunch is mentioned.")
        self.assertEqual(entry["spellings"], ["haunch"])

    def test_a_term_reached_by_search_has_none(self):
        """There is no occurrence to point at, and an empty list says that rather than lying."""
        site = _Glossary()
        mod, saved = site.load()
        try:
            self.assertEqual(mod.search_glossary("invert")["terms"][0]["spellings"], [])
        finally:
            _restore(saved)


class TestOneTermByName(unittest.TestCase):
    """The See also link, and a search hit being opened."""

    def _call(self, term, pool_text="", in_quiz=False):
        site = _Glossary(lesson_text="Haunching and invert.", pool_text=pool_text)
        mod, saved = site.load()
        try:
            return mod.term_help(term, lesson="LSN-1" if pool_text else None, in_quiz=in_quiz)
        finally:
            _restore(saved)

    def test_a_known_term_comes_back(self):
        self.assertEqual(self._call("Invert")["entry"]["term"], "Invert")

    def test_the_lookup_is_case_insensitive(self):
        self.assertEqual(self._call("invert")["entry"]["term"], "Invert")

    def test_an_unknown_term_is_none_rather_than_an_error(self):
        """`see_also` is a plain text field, not a child table of Links, so it can name a term that
        was renamed or never written. That is a sentence in the panel, not an exception."""
        found = self._call("Nothing like this")
        self.assertIsNone(found["entry"])
        self.assertEqual(found["withheld"], 0)

    # ------------------------------------------------------------------ the quiz rule

    def test_a_term_the_pool_gives_away_is_withheld(self):
        """The panel's suppression is computed from the terms the LESSON uses. A See also can point
        outside that set, so the pool has to be asked about this term directly -- otherwise the one
        word a question turns on stays reachable in two clicks from a word that is safe."""
        found = self._call("Invert", pool_text="What is the invert of the pipe?", in_quiz=True)
        self.assertIsNone(found["entry"])
        self.assertEqual(found["withheld"], 1)

    def test_a_term_the_pool_does_not_mention_is_served(self):
        found = self._call("Invert", pool_text="A question about something else.", in_quiz=True)
        self.assertEqual(found["entry"]["term"], "Invert")

    def test_and_it_is_served_in_quiz_shape(self):
        found = self._call("Invert", pool_text="A question about something else.", in_quiz=True)
        self.assertEqual(found["entry"]["explanation"], "")
        self.assertEqual(found["entry"]["example"], "")

    def test_outside_a_quiz_the_pool_is_irrelevant(self):
        found = self._call("Invert", pool_text="What is the invert of the pipe?", in_quiz=False)
        self.assertEqual(found["entry"]["term"], "Invert")
        self.assertTrue(found["entry"]["explanation"])


class TestSearchingTheGlossary(unittest.TestCase):
    def _search(self, query, in_quiz=False):
        site = _Glossary()
        mod, saved = site.load()
        try:
            return mod.search_glossary(query, in_quiz=in_quiz)
        finally:
            _restore(saved)

    def test_a_word_is_found(self):
        self.assertEqual([e["term"] for e in self._search("invert")["terms"]], ["Invert"])

    def test_an_alias_is_found(self):
        self.assertEqual([e["term"] for e in self._search("haunches")["terms"]], ["Haunching"])

    def test_the_word_itself_sorts_above_one_that_merely_contains_it(self):
        site = _Glossary(terms=(TERM, TERM_B, dict(TERM, name="GT-3", term="Inverted siphon", aliases="")))
        mod, saved = site.load()
        try:
            hits = mod.search_glossary("invert")["terms"]
            self.assertEqual(hits[0]["term"], "Invert")
        finally:
            _restore(saved)

    def test_one_character_finds_nothing(self):
        """A single letter matches most of the glossary and answers nothing."""
        self.assertEqual(self._search("i")["terms"], [])

    def test_an_empty_query_finds_nothing(self):
        self.assertEqual(self._search("")["terms"], [])
        self.assertEqual(self._search("   ")["terms"], [])

    # ------------------------------------------------------------------ the quiz rule

    def test_search_is_closed_during_a_quiz(self):
        """The panel's suppression is answerable because the lesson is known. A free search over
        the whole glossary has no equivalent guarantee, and approximating one would be worse than
        saying so -- the suppressed panel is still there mid-question."""
        self.assertEqual(self._search("invert", in_quiz=True)["terms"], [])
        self.assertEqual(self._search("invert", in_quiz=True)["more"], 0)

    def test_a_search_result_is_served_in_full_outside_a_quiz(self):
        self.assertTrue(self._search("invert")["terms"][0]["explanation"])


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

    def test_every_palette_token_used_is_declared(self):
        """A `var(--tr-whatever)` that nothing declares does not fail — it falls back.

        Which is the trap. The fallback is the light-theme colour written inline beside it, so the
        rule looks perfect until somebody switches to dark and that one element stays pale. It has
        happened twice here: `--tr-danger` for `--tr-bad`, and `--tr-surface-2` for
        `--tr-surface-alt`, both introduced by Help rules and both invisible in light mode.
        """
        css = _raw(PLAYER_CSS)
        declared = set(re.findall(r"^\s*(--tr-[a-z0-9-]+)\s*:", css, re.M))
        used = set(re.findall(r"var\((--tr-[a-z0-9-]+)", css))
        self.assertEqual(sorted(used - declared), [], "these tokens are read but never declared")

    def test_no_help_rule_declares_the_palette(self):
        """--tr-* may be declared in player.css only, and the Help rules READ it rather than adding
        to it — but a new token here would still be a silent divergence from the three-way dark
        handling the rest of the file keeps."""
        css = _raw(PLAYER_CSS)
        block = css.split(".tr-help {", 1)[1].split(".tr-qa {", 1)[0]
        self.assertIsNone(re.search(r"--tr-[\w-]+\s*:", block))

    def test_this_suite_runs_in_ci(self):
        self.assertIn("erpnext_enhancements.tests.test_training_help", _raw(CI))


class TestTheGlossaryIsReachable(unittest.TestCase):
    """Somebody has to be able to find the table to correct it.

    Until v1.469.0 `Training Glossary Term` was linked from **no workspace at all** — the doctype
    appeared in exactly one file in the repo, its own. The only route to 717 AI-drafted definitions
    was typing the doctype name into the awesomebar, which is not a route anybody finds. A glossary
    nobody can reach to correct is a glossary that stays wrong.
    """

    WORKSPACE = APP_ROOT / "training" / "workspace" / "training" / "training.json"
    PATCH = APP_ROOT / "patches" / "reload_training_workspace_for_glossary.py"

    @classmethod
    def setUpClass(cls):
        cls.ws = json.loads(_raw(cls.WORKSPACE))

    def test_the_workspace_links_it(self):
        labels = [row.get("link_to") for row in self.ws["links"]]
        self.assertIn("Training Glossary Term", labels)

    def test_it_sits_under_authoring(self):
        """Writing a definition is authoring, and putting it under Setup files it with the
        switches nobody opens twice."""
        card = None
        for row in self.ws["links"]:
            if row.get("type") == "Card Break":
                card = row.get("label")
            elif row.get("link_to") == "Training Glossary Term":
                self.assertEqual(card, "Authoring")
                return
        self.fail("the glossary link is not in the workspace at all")

    def test_a_forced_reload_ships_with_it(self):
        """`import_file` compares the file's `modified` against the row and SILENTLY skips when the
        row is not older, so a workspace edit reaches a fresh install and no existing site. This
        module has been caught by that before."""
        self.assertTrue(self.PATCH.exists())
        src = _raw(self.PATCH)
        self.assertIn('reload_doc("training", "workspace", "training", force=True)', src)
        self.assertIn(
            "erpnext_enhancements.patches.reload_training_workspace_for_glossary",
            _raw(APP_ROOT / "patches.txt"),
        )

    def test_the_patch_cannot_abort_a_migrate(self):
        """A workspace card is not worth a half-finished deploy."""
        src = _raw(self.PATCH)
        self.assertIn("except Exception:", src)
        self.assertIn("frappe.log_error(", src)

    def test_an_author_can_actually_edit_it(self):
        """Reachable and read-only would be a worse answer than not reachable at all."""
        doctype = json.loads(_raw(DOCTYPE_JSON))
        writers = {p["role"] for p in doctype.get("permissions", []) if p.get("write")}
        self.assertIn("Training Author", writers)
        self.assertIn("Training Manager", writers)

    def test_a_learner_still_holds_no_permission(self):
        """The mid-quiz suppression is only real because a learner cannot fetch the table."""
        doctype = json.loads(_raw(DOCTYPE_JSON))
        roles = {p["role"] for p in doctype.get("permissions", [])}
        self.assertNotIn("Training Learner", roles)


class TestTheWordsInTheLessonAreMarked(unittest.TestCase):
    """Hover-to-define, and the three things that decide whether it is usable."""

    @classmethod
    def setUpClass(cls):
        cls.js = _strip_js_comments(_raw(PLAYER))

    def test_the_payload_is_fetched_on_render_not_on_the_toggle(self):
        """The panel alone could stay lazy. The marks cannot: a reader hovering a word has not
        opened anything, so a fetch that waits for the toggle means hover does nothing until the
        reader has already found the answer another way."""
        block = self.js.split("function renderHelp(", 1)[1][:2000]
        self.assertIn("loadHelp(key, inQuiz)", block)
        toggle = self.js.split('t("What does that mean?")', 1)[1][:600]
        self.assertNotIn("loadHelp(", toggle)

    def test_only_the_first_occurrence_of_a_term_is_marked(self):
        """A lesson that says "bonding" fourteen times would otherwise become a field of dotted
        underlines, which stops meaning anything."""
        self.assertIn("marked[candidate.entry.term]", self.js)
        self.assertIn("GLOSS_MARK_LIMIT", self.js)

    def test_longest_spelling_first(self):
        """So "breakpoint chlorination" claims the phrase before "breakpoint" takes half of it."""
        block = self.js.split("function markGlossary(", 1)[1][:1200]
        self.assertIn("forms.sort", block)
        self.assertIn("length - String(left.form).length", block)

    def test_the_mark_is_reachable_by_keyboard_and_by_touch(self):
        """Hover alone is not an interaction on the phone this course is read on, and a mark that
        cannot be reached by Tab is one a screen reader never announces."""
        block = self.js.split("function bindGloss(", 1)[1][:900]
        for event in ("mouseenter", "focus", "click"):
            with self.subTest(event):
                self.assertIn('addEventListener("' + event + '"', block)

    def test_the_mark_is_a_button_with_a_label(self):
        block = self.js.split("function markFirst(", 1)[1][:1200]
        self.assertIn('el("button", "tr-gloss", word)', block)
        self.assertIn('setAttribute("aria-label"', block)

    def test_marking_never_descends_into_the_help_panel(self):
        """The panel lists the same words. Marking those makes a term explain itself."""
        block = self.js.split("function glossSkip(", 1)[1][:800]
        self.assertIn('contains("tr-help")', block)
        self.assertIn('contains("tr-gloss")', block)

    def test_the_lesson_text_is_not_marked_during_a_quiz(self):
        block = self.js.split("function loadHelp(", 1)[1][:1200]
        self.assertIn("if (!inQuiz) markGlossary(", block)

    def test_the_tooltip_carries_the_definition_and_not_the_teaching(self):
        """A tooltip long enough to hold the explanation and the worked example is a tooltip
        covering the sentence the reader was in the middle of."""
        block = self.js.split("function showGloss(", 1)[1][:1200]
        self.assertIn("entry.short_definition", block)
        self.assertNotIn("entry.explanation", block)
        self.assertNotIn("entry.example", block)


class TestSeeAlsoIsReachable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _strip_js_comments(_raw(PLAYER))

    def test_each_see_also_is_a_button(self):
        block = self.js.split("entry.see_also.forEach(", 1)[1][:500]
        self.assertIn("tr-help-seealso-link", block)
        self.assertIn("openTerm(name)", block)

    def test_a_term_already_loaded_is_not_fetched_again(self):
        block = self.js.split("function openTerm(", 1)[1][:1400]
        self.assertIn("if (knownTerm(name))", block)

    def test_the_fetch_sends_the_lesson_so_the_quiz_rule_can_be_applied(self):
        """A cross-reference can point at a word the lesson never uses, which the panel therefore
        never had the chance to suppress."""
        block = self.js.split('call("glossaryTerm"', 1)[1][:400]
        self.assertIn("lesson_key: helpState.key", block)
        self.assertIn("in_quiz: helpState.inQuiz", block)

    def test_the_fetch_sends_the_course_docname(self):
        """state.course carries no `name` key, so `state.course.name` is undefined and
        JSON.stringify DROPS the argument. That bug has shipped twice in this module."""
        for endpoint in ('call("glossaryTerm"', 'call("glossarySearch"'):
            with self.subTest(endpoint):
                block = self.js.split(endpoint, 1)[1][:400]
                self.assertIn("course: state.courseName", block)
                self.assertNotIn("state.course.name", block)

    def test_a_reference_that_resolves_to_nothing_says_so(self):
        """`see_also` is a plain text field, not a child table of Links, so it can name a term that
        was renamed, disabled or never written."""
        block = self.js.split("function openTerm(", 1)[1][:1800]
        self.assertIn("There is no glossary entry for", block)


class TestSearchingFromThePanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _strip_js_comments(_raw(PLAYER))

    def test_the_box_filters_the_lesson_list_without_a_round_trip(self):
        self.assertIn("matchesQuery(entry, query)", self.js)

    def test_the_box_keeps_focus_across_the_repaint_it_causes(self):
        """Every keystroke repaints the panel. Without this the box can only be used one letter at
        a time, which is not a search box."""
        block = self.js.split("function helpSearchBox(", 1)[1][:1400]
        self.assertIn("input.focus()", block)

    def test_the_whole_glossary_search_is_a_deliberate_second_step(self):
        """Filtering what is already loaded costs nothing; searching 717 entries is a request, and
        it should happen because somebody asked rather than on every keystroke."""
        block = self.js.split("function helpSearchResults(", 1)[1][:2000]
        self.assertIn("tr-help-search-all", block)
        self.assertIn("runGlossarySearch(helpState.query)", block)

    def test_the_panel_says_search_is_closed_during_a_quiz(self):
        """Rather than showing an empty result, which reads as a broken search."""
        block = self.js.split("function helpSearchResults(", 1)[1][:2000]
        self.assertIn("if (helpState.inQuiz)", block)
        self.assertIn("closed while the quiz is open", block)

    def test_a_new_keystroke_drops_the_last_whole_glossary_answer(self):
        """Leaving it up shows results for a word the reader has finished typing over."""
        block = self.js.split("function helpSearchBox(", 1)[1][:1400]
        self.assertIn("helpState.search = null", block)


class TestAFieldIsRenderedAsWhatItIs(unittest.TestCase):
    """Text Editor fields go in as HTML; Small Text fields go in as text.

    v1.468.1 rendered `explanation` with `textContent`, so every one of the 717 seeded entries
    showed its own `<p>` tags as words — and 473 of them carry more than one paragraph, so the
    result was a wall of text with the markup in it rather than a stray tag. The mirror mistake
    is just as available: rendering a Small Text field as markup, where a `<` somebody typed
    becomes an unclosed element and swallows the rest of the definition.
    """

    @classmethod
    def setUpClass(cls):
        cls.js = _strip_js_comments(_raw(PLAYER))
        cls.term = cls.js.split("function helpTerm(", 1)[1].split("\n\t\tfunction ", 1)[0]

    # ------------------------------------------------------- the Text Editor fields

    def test_the_explanation_goes_in_as_html(self):
        self.assertIn("helpHtml(", self.term)
        self.assertRegex(self.term, r"helpHtml\(.*tr-help-more.*,\s*entry\.explanation\)")

    def test_the_example_goes_in_as_html(self):
        self.assertRegex(self.term, r"helpHtml\(.*tr-help-example-body.*,\s*entry\.example\)")

    def test_neither_is_passed_to_the_text_helper(self):
        """`el(tag, cls, text)` sets textContent. Passing HTML to it is the whole bug."""
        self.assertNotIn('el("p", "tr-help-more", entry.explanation)', self.term)
        self.assertNotIn('" " + entry.example', self.term)

    # ------------------------------------------------------- the plain fields

    def test_the_plain_fields_stay_text(self):
        for field in ("short_definition", "ordinary_meaning"):
            with self.subTest(field):
                self.assertNotRegex(self.term, rf"helpHtml\([^;]*entry\.{field}")

    # ------------------------------------------------------- one sanitiser, not two

    def test_the_player_reuses_the_runtime_sanitiser(self):
        """quiz.js already owns the scrubber and the reasoning behind it (an inert DOMParser,
        because assigning to a detached div's innerHTML still fires an `<img onerror>`). A second
        copy here is how the two drift and one of them ends up weaker."""
        self.assertIn("TR.setHtml", self.js)
        quiz = _raw(APP_ROOT / "public" / "js" / "training" / "quiz.js")
        self.assertIn("TR.setHtml = setHtml", quiz)
        self.assertIn("TR.scrubHtml = scrub", quiz)

    def test_the_player_never_assigns_innerhtml_itself(self):
        """The fallback when `TR.setHtml` is absent must be textContent, not a raw assignment."""
        self.assertNotIn("innerHTML", self.js)

    def test_quiz_js_loads_before_player_js(self):
        """`TR.setHtml` is read at call time, but the order is what guarantees it exists at all.
        `loadAsset` sets async = false, so insertion order IS execution order."""
        learn = _raw(APP_ROOT / "training" / "page" / "learn" / "learn.js")
        self.assertLess(learn.index("training/quiz.js"), learn.index("training/player.js"))


class TestTheCapIsAbove(unittest.TestCase):
    """`MAX_TERMS` has to sit above how many terms a real lesson matches, not below it.

    At 24 it sat below **every** lesson measured on production: twelve technician lessons sampled
    2026-09-15 matched between 28 and 105 terms, middle around 57. So the truncation note was not
    an edge case a dense lesson occasionally hit — it was on every panel, every time, naming a
    number of words the reader had no route to. A cap that always fires is not a cap, it is a
    silent content limit with a counter attached to it.
    """

    #: The worst lesson in that sample. The cap must clear it with room, or the note is furniture
    #: again the first time somebody writes a longer lesson.
    MEASURED_WORST = 105

    @classmethod
    def setUpClass(cls):
        state = {"get_all": lambda *a, **k: [], "get_doc": lambda *a, **k: None}
        cls.mod, cls.saved = _load_help(state)

    @classmethod
    def tearDownClass(cls):
        _restore(cls.saved)

    def test_the_cap_clears_the_densest_lesson_measured(self):
        self.assertGreater(self.mod.MAX_TERMS, self.MEASURED_WORST)

    def test_there_is_still_a_cap(self):
        """Not unbounded. The glossary is a table anybody can add to, and one lesson's panel
        should not be able to become a multi-megabyte reply because it grew to 5,000 entries."""
        self.assertLessEqual(self.mod.MAX_TERMS, 500)

    def test_the_overflow_is_still_counted_rather_than_dropped(self):
        src = _raw(HELP_PY)
        self.assertIn('"more":', src)

    def test_the_note_says_why_rather_than_just_how_many(self):
        """"…and 4 more terms in this lesson" describes a number, not a reason, and a reader who
        cannot act on it is owed the rule instead."""
        js = _strip_js_comments(_raw(PLAYER))
        # The `more` branch specifically. `tr-help-note` is also the class the withheld-in-quiz
        # note uses, and that one is a different sentence with a different job.
        note = js.split("if (data.more)", 1)[1][:300]
        self.assertIn("one panel will show", note)
        self.assertNotIn("more terms in this lesson.", note)


class TestMarkupInAPlainField(unittest.TestCase):
    """`_plain` takes markup back out of the two Small Text fields, server-side."""

    @classmethod
    def setUpClass(cls):
        from erpnext_enhancements.training import help as help_module

        # staticmethod, not a bare assignment: a plain function stored on a class becomes a
        # bound method on access and swallows the first argument as `self`.
        cls.plain = staticmethod(help_module._plain)

    def test_inline_tags_are_removed(self):
        self.assertEqual(self.plain("work that <i>makes</i> heat"), "work that makes heat")
        self.assertEqual(self.plain("<p>a definition</p>"), "a definition")
        self.assertEqual(self.plain("<b>bold</b> and <strong>strong</strong>"), "bold and strong")

    def test_a_comparison_is_not_eaten(self):
        """The reason this is an allowlist and not `<[^>]+>`. A glossary is full of these, and a
        greedy pattern takes everything between the two signs and calls it a tag."""
        self.assertEqual(self.plain("keep pH < 7.8 and > 7.2"), "keep pH < 7.8 and > 7.2")
        self.assertEqual(self.plain("a gap of < 3 mm"), "a gap of < 3 mm")

    def test_empty_is_empty_not_none(self):
        self.assertEqual(self.plain(None), "")
        self.assertEqual(self.plain(""), "")

    def test_the_serve_path_uses_it(self):
        src = _raw(APP_ROOT / "training" / "help.py")
        serve = src.split("def _serve(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"short_definition": _plain(', serve)
        self.assertIn('"ordinary_meaning": _plain(', serve)

    def test_the_text_editor_fields_are_not_stripped(self):
        """`explanation` and `example` are meant to hold HTML — stripping them here would fix the
        symptom by deleting the formatting, which is the other way to get this wrong."""
        src = _raw(APP_ROOT / "training" / "help.py")
        serve = src.split("def _serve(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn('"explanation": _plain(', serve)
        self.assertNotIn('"example": _plain(', serve)


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
