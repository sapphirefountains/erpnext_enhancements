"""Bench-free tests for the glossary review queue and the two ways it fixes a collision.

The thing worth guarding here is **the merge**, because it is the one button in the Training module
that deletes somebody's content. Three properties hold it:

* it refuses an entry a person has written or checked,
* every spelling the losing entry answered to survives on the winner, so no lesson quietly stops
  matching a word it used to match,
* every ``see_also`` elsewhere that named the loser is repointed — ``see_also`` is a plain text
  field, not a child table of Links, so the framework will not stop the delete and nothing would
  ever notice the reference had gone dead.

The second guarded thing is the **acronym rule** in the patch. Nine names on production collide
after normalising away the bracket, and one of them — ``Scale`` / ``Scale (on a drawing)`` — is two
real concepts sharing a word. Merging on name similarity would have destroyed one of them, so the
rule merges only when the bracket is an abbreviation, and that is tested against all nine.

Run: python -m unittest erpnext_enhancements.tests.test_glossary_review
"""

import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
HISTORY_HARNESS = REPO_ROOT / "scripts" / "test_training_desk_history.mjs"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE_PY = APP_ROOT / "training" / "glossary_review.py"
PATCH_PY = APP_ROOT / "patches" / "merge_acronym_glossary_terms.py"
PAGE_DIR = APP_ROOT / "training" / "page" / "training_glossary_review"
PAGE_JS = PAGE_DIR / "training_glossary_review.js"
PAGE_JSON = PAGE_DIR / "training_glossary_review.json"
PAGE_CSS = PAGE_DIR / "training_glossary_review.css"
DESK_NAV = APP_ROOT / "public" / "js" / "training" / "desk_nav.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _raw(path):
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- the fake site


class _Doc:
    def __init__(self, site, data):
        self._site = site
        self.__dict__.update(data)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def save(self, **kwargs):
        self._site.rows[self.name] = {
            k: v for k, v in self.__dict__.items() if not k.startswith("_")
        }
        self._site.saved.append(self.name)

    def add_comment(self, kind, text):
        self._site.comments.append((self.name, text))


class _Site:
    def __init__(self, rows):
        self.rows = {row["name"]: dict(row) for row in rows}
        self.saved = []
        self.deleted = []
        self.comments = []
        self.set_values = []

    # -- the frappe surface the module uses -------------------------------------------------

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
        rows = [dict(r) for r in self.rows.values()]
        rows.sort(key=lambda r: (r.get("term") or "").lower())
        hits = [r for r in rows if self._matches(r, filters)]
        if pluck:
            return [r.get(pluck) for r in hits]
        return hits

    @staticmethod
    def _matches(row, filters):
        for field, rule in (filters or {}).items():
            value = row.get(field)
            if isinstance(rule, (list, tuple)):
                op, operand = rule[0], rule[1]
                if op == "is" and operand == "set" and not value:
                    return False
                if op == "is" and operand == "not set" and value:
                    return False
                if op == "in" and value not in operand:
                    return False
            elif value != rule:
                return False
        return True

    def get_doc(self, doctype, name):
        if name not in self.rows:
            raise KeyError(name)
        return _Doc(self, dict(self.rows[name]))

    def delete_doc(self, doctype, name, **kwargs):
        self.rows.pop(name, None)
        self.deleted.append(name)

    def set_value(self, doctype, name, field, value=None, **kwargs):
        self.set_values.append((name, field, value))
        if name in self.rows:
            self.rows[name][field] = value

    def count(self, doctype, filters=None):
        return len(self.get_all(doctype, filters=filters, pluck="name"))


def _install(site, roles):
    fake = types.ModuleType("frappe")

    class PermissionError_(Exception):
        pass

    class ValidationError_(Exception):
        pass

    fake.PermissionError = PermissionError_
    fake.ValidationError = ValidationError_

    def throw(msg, exc=None):
        raise (exc or ValidationError_)(str(msg))

    fake.throw = throw
    fake.msgprint = lambda *a, **k: None
    fake.session = types.SimpleNamespace(user="author@example.com")
    fake.get_roles = lambda user=None: roles
    fake._ = lambda s: s
    fake.whitelist = lambda **kw: (lambda fn: fn)
    fake.get_all = site.get_all
    fake.get_doc = site.get_doc
    fake.delete_doc = site.delete_doc
    fake.db = types.SimpleNamespace(
        set_value=site.set_value, count=site.count, exists=lambda *a, **k: True
    )
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: "traceback"

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v, default=0: int(v) if str(v).strip() not in ("", "None", "False", "0") else 0
    fake.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    model.document = document

    keys = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document")
    saved = {k: sys.modules.get(k) for k in keys}
    sys.modules["frappe"] = fake
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document
    return saved


def _restore(saved):
    for key, value in saved.items():
        if value is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = value


def _load(site, roles=("Training Author",)):
    import importlib

    saved = _install(site, list(roles))
    controller = importlib.import_module(
        "erpnext_enhancements.training.doctype.training_glossary_term.training_glossary_term"
    )
    importlib.reload(controller)
    mod = importlib.import_module("erpnext_enhancements.training.glossary_review")
    return importlib.reload(mod), saved


def _term(name, **over):
    row = {
        "name": name,
        "term": name,
        "aliases": "",
        "short_definition": "A definition.",
        "trade_trap": 0,
        "ordinary_meaning": "",
        "explanation": "",
        "example": "",
        "see_also": "",
        "category": None,
        "enabled": 1,
        "ai_generated": 1,
        "reviewed_by": None,
    }
    row.update(over)
    return row


# ----------------------------------------------------------------------------- the gate


class TestOnlyAnAuthorReviews(unittest.TestCase):
    def test_a_learner_is_refused(self):
        site = _Site([_term("Weir")])
        mod, saved = _load(site, roles=("Training Learner",))
        try:
            with self.assertRaises(Exception):
                mod.get_glossary_queue()
        finally:
            _restore(saved)

    def test_the_reviewer_comes_from_the_session(self):
        """The payload has no way to name one. A signature you can address to somebody else is
        not a signature — the same rule `training/review.py` keeps for questions."""
        src = _raw(MODULE_PY)
        accept = src.split("def accept_term(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("doc.reviewed_by = user", accept)
        self.assertNotIn("reviewed_by=", accept.split('"""', 2)[-1])


class TestAcceptingATerm(unittest.TestCase):
    def _accept(self, site, **kwargs):
        mod, saved = _load(site)
        try:
            return mod.accept_term("Weir", **kwargs)
        finally:
            _restore(saved)

    def test_it_stamps_the_session_user(self):
        site = _Site([_term("Weir")])
        self._accept(site)
        self.assertEqual(site.rows["Weir"]["reviewed_by"], "author@example.com")

    def test_corrections_are_saved_with_it(self):
        site = _Site([_term("Weir")])
        self._accept(site, short_definition="An edge water flows over.")
        self.assertEqual(site.rows["Weir"]["short_definition"], "An edge water flows over.")

    def test_accepting_unchanged_needs_no_echo(self):
        """The common case. Requiring the client to send every field back to accept one entry
        makes 717 repetitions into 717 opportunities to send a stale value."""
        site = _Site([_term("Weir", short_definition="Unchanged.")])
        self._accept(site)
        self.assertEqual(site.rows["Weir"]["short_definition"], "Unchanged.")

    def test_an_entry_cannot_be_accepted_without_a_definition(self):
        """`short_definition` is the one field Help shows mid-quiz. An accepted entry that renders
        as an empty box at that moment is worse than one nobody has checked."""
        site = _Site([_term("Weir")])
        with self.assertRaises(Exception):
            self._accept(site, short_definition="   ")


class TestDisablingRatherThanDeleting(unittest.TestCase):
    def test_it_disables_and_records_why(self):
        site = _Site([_term("Weir")])
        mod, saved = _load(site)
        try:
            mod.disable_term("Weir", "Wrong about the tolerance.")
        finally:
            _restore(saved)
        self.assertEqual(site.rows["Weir"]["enabled"], 0)
        self.assertIn("Weir", [name for name, _text in site.comments])

    def test_a_reason_is_required(self):
        """A disabled entry with no reason is one nobody can put back."""
        site = _Site([_term("Weir")])
        mod, saved = _load(site)
        try:
            with self.assertRaises(Exception):
                mod.disable_term("Weir", "  ")
        finally:
            _restore(saved)

    def test_the_row_is_not_deleted(self):
        site = _Site([_term("Weir")])
        mod, saved = _load(site)
        try:
            mod.disable_term("Weir", "Wrong.")
        finally:
            _restore(saved)
        self.assertIn("Weir", site.rows)
        self.assertEqual(site.deleted, [])


# ----------------------------------------------------------------------------- collisions


class TestFindingCollisions(unittest.TestCase):
    def _clusters(self, rows):
        site = _Site(rows)
        mod, saved = _load(site)
        try:
            return mod.get_collisions()["clusters"]
        finally:
            _restore(saved)

    def test_two_entries_claiming_one_alias_collide(self):
        clusters = self._clusters(
            [_term("Surge tank", aliases="vaults"), _term("Equipment vault", aliases="vaults")]
        )
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["holders"], ["Equipment vault", "Surge tank"])

    def test_a_name_claimed_as_somebody_elses_alias_collides(self):
        clusters = self._clusters([_term("GFCI"), _term("Class A GFCI", aliases="GFCI")])
        self.assertTrue(clusters)

    def test_punctuation_does_not_hide_a_collision(self):
        """`Lock-out/tag-out` and `Lock-out tag-out` are one word to a technician and two
        different strings to a database. The glossary holds both."""
        clusters = self._clusters([_term("Lock-out/tag-out"), _term("Lock-out tag-out")])
        self.assertTrue(clusters)

    def test_entries_that_share_nothing_do_not_collide(self):
        self.assertEqual(self._clusters([_term("Weir"), _term("Invert")]), [])

    def test_the_worst_cluster_comes_first(self):
        rows = [
            _term("A one", aliases="shared"),
            _term("A two", aliases="shared"),
            _term("A three", aliases="shared"),
            _term("B one", aliases="pair"),
            _term("B two", aliases="pair"),
        ]
        clusters = self._clusters(rows)
        self.assertEqual(len(clusters[0]["holders"]), 3)


class TestDroppingAnAlias(unittest.TestCase):
    """The lighter move, and the right one for most collisions."""

    def test_the_alias_goes_and_the_entry_stays(self):
        site = _Site([_term("Surge tank", aliases="vaults\nbalance tank")])
        mod, saved = _load(site)
        try:
            mod.drop_alias("Surge tank", "vaults")
        finally:
            _restore(saved)
        self.assertEqual(site.rows["Surge tank"]["aliases"], "balance tank")
        self.assertIn("Surge tank", site.rows)

    def test_dropping_one_that_is_not_there_is_refused(self):
        site = _Site([_term("Surge tank", aliases="balance tank")])
        mod, saved = _load(site)
        try:
            with self.assertRaises(Exception):
                mod.drop_alias("Surge tank", "vaults")
        finally:
            _restore(saved)


class TestMerging(unittest.TestCase):
    """The one button in this module that deletes content."""

    def _merge(self, rows, loser, winner):
        site = _Site(rows)
        mod, saved = _load(site)
        try:
            return site, mod.merge_terms(loser, winner)
        finally:
            _restore(saved)

    def test_the_loser_is_deleted(self):
        site, _result = self._merge([_term("GFCI"), _term("GFCI (device)")], "GFCI (device)", "GFCI")
        self.assertNotIn("GFCI (device)", site.rows)
        self.assertIn("GFCI", site.rows)

    def test_every_spelling_the_loser_answered_to_survives(self):
        """The real invariant: nothing a lesson used to match stops matching.

        Asserted by running the matcher rather than by looking for a literal in the alias box.
        `GFCI (device)` normalises to the word the winner already answers to, so it is correctly
        NOT copied across — and a test that demanded the literal string would have called that a
        bug and pushed the code into storing a redundant alias.
        """
        from erpnext_enhancements.training.doctype.training_glossary_term import (
            training_glossary_term as controller,
        )

        rows = [_term("GFCI"), _term("GFCI (device)", aliases="ground fault interrupter\nGFI")]
        site, result = self._merge(rows, "GFCI (device)", "GFCI")

        winner = site.rows["GFCI"]
        patterns = [
            controller.TrainingGlossaryTerm.compile_pattern(form)
            for form in controller.match_patterns_for(winner["term"], winner["aliases"])
        ]
        for spelling in ("GFCI (device)", "ground fault interrupter", "GFI", "GFCI"):
            with self.subTest(spelling):
                sentence = f"a lesson mentioning {spelling} here"
                self.assertTrue(
                    [p for p in patterns if p.search(sentence)],
                    f"{spelling} no longer matches anything",
                )
        self.assertTrue(result["spellings_moved"])

    def test_a_reviewed_entry_is_never_deleted(self):
        rows = [_term("GFCI"), _term("GFCI (device)", reviewed_by="someone@example.com")]
        site = _Site(rows)
        mod, saved = _load(site)
        try:
            with self.assertRaises(Exception):
                mod.merge_terms("GFCI (device)", "GFCI")
        finally:
            _restore(saved)
        self.assertIn("GFCI (device)", site.rows)

    def test_a_hand_written_entry_is_never_deleted(self):
        rows = [_term("GFCI"), _term("GFCI (device)", ai_generated=0)]
        site = _Site(rows)
        mod, saved = _load(site)
        try:
            with self.assertRaises(Exception):
                mod.merge_terms("GFCI (device)", "GFCI")
        finally:
            _restore(saved)
        self.assertIn("GFCI (device)", site.rows)

    def test_see_also_elsewhere_is_repointed(self):
        """`see_also` is a plain text field, not a child table of Links — the framework will not
        stop the delete and nothing else would ever notice the name had gone."""
        rows = [
            _term("GFCI"),
            _term("GFCI (device)"),
            _term("Bonding", see_also="GFCI (device)\nEarth"),
        ]
        site, result = self._merge(rows, "GFCI (device)", "GFCI")
        self.assertIn("GFCI", site.rows["Bonding"]["see_also"])
        self.assertNotIn("(device)", site.rows["Bonding"]["see_also"])
        self.assertIn("Bonding", result["see_also_repointed"])

    def test_repointing_never_leaves_an_entry_pointing_at_itself(self):
        """`_reject_self_reference` refuses that save, and the whole merge would fail on an
        unrelated row."""
        rows = [_term("GFCI", see_also="GFCI (device)"), _term("GFCI (device)")]
        site, _result = self._merge(rows, "GFCI (device)", "GFCI")
        self.assertNotIn("GFCI (device)", site.rows["GFCI"].get("see_also", ""))

    def test_merging_a_term_into_itself_is_refused(self):
        site = _Site([_term("GFCI")])
        mod, saved = _load(site)
        try:
            with self.assertRaises(Exception):
                mod.merge_terms("GFCI", "GFCI")
        finally:
            _restore(saved)


# ----------------------------------------------------------------------------- the patch's rule


class TestTheAcronymRule(unittest.TestCase):
    """Nine names on production collide after the bracket is normalised away. One of them is not
    a duplicate, and merging on name similarity would have destroyed a real concept."""

    @classmethod
    def setUpClass(cls):
        # The patch imports frappe at module scope, so the stub has to be standing before it is
        # imported at all — and it stays up for the class, because the module object is cached.
        cls.saved = _install(_Site([]), ["Training Author"])
        import importlib

        cls.patch = importlib.import_module(
            "erpnext_enhancements.patches.merge_acronym_glossary_terms"
        )

    @classmethod
    def tearDownClass(cls):
        _restore(cls.saved)

    def _is_acronym(self, name):
        found = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", name)
        return self.patch._is_acronym_of(found.group(2).strip(), found.group(1).strip())

    def test_the_eight_real_abbreviations_are_recognised(self):
        for name in (
            "Authority having jurisdiction (AHJ)",
            "International Swimming Pool and Spa Code (ISPSC)",
            "Langelier Saturation Index (LSI)",
            "Lock-out/tag-out (LOTO)",
            "Safety data sheet (SDS)",
            "Total dissolved solids (TDS)",
            "Ultraviolet (UV)",
            "Variable frequency drive (VFD)",
        ):
            with self.subTest(name):
                self.assertTrue(self._is_acronym(name))

    def test_scale_on_a_drawing_is_spared(self):
        """The one that makes the rule worth having. Scale on a drawing and scale in a basin are
        two real concepts that share a word; the bracket is a disambiguator, not an abbreviation,
        and a merge would produce one entry that is wrong about both."""
        self.assertFalse(self._is_acronym("Scale (on a drawing)"))

    def test_an_acronym_skipping_a_joining_word_still_counts(self):
        """ISPSC drops the "and", the way every acronym drops joining words. A strict initials
        test refused it, which is why the rule is a subsequence."""
        self.assertTrue(self._is_acronym("International Swimming Pool and Spa Code (ISPSC)"))

    def test_a_one_word_term_abbreviating_from_inside_itself_counts(self):
        """Ultraviolet -> UV. Its initials are just "U"."""
        self.assertTrue(self._is_acronym("Ultraviolet (UV)"))

    def test_a_lower_case_bracket_is_prose(self):
        for name in ("Prime (to prime)", "Scale (build-up)", "Weir (the wall)"):
            with self.subTest(name):
                self.assertFalse(self._is_acronym(name))

    def test_the_patch_never_aborts_a_migrate(self):
        src = _raw(PATCH_PY)
        self.assertIn("except Exception", src)
        self.assertIn("frappe.db.rollback()", src)

    def test_the_patch_reuses_the_review_screens_merge(self):
        """A second implementation of what a merge does is how the patch and the button end up
        disagreeing about what happened to somebody's aliases."""
        self.assertIn("from erpnext_enhancements.training.glossary_review import merge_into", _raw(PATCH_PY))

    def test_it_is_registered(self):
        self.assertIn(
            "erpnext_enhancements.patches.merge_acronym_glossary_terms",
            _raw(APP_ROOT / "patches.txt"),
        )


class TestTheFragmentAliasRule(unittest.TestCase):
    """Reported live: hovering **flooded** — about flooding a planter bed — offered *Flooded
    suction*, a way of mounting a pump. The entry carried `flooded` as an alias, so every ordinary
    use of the word claimed it. That is a shape, not one bad row.
    """

    @classmethod
    def setUpClass(cls):
        import importlib

        cls.saved = _install(_Site([]), ["Training Author"])
        cls.patch = importlib.import_module(
            "erpnext_enhancements.patches.drop_fragment_glossary_aliases"
        )

    @classmethod
    def tearDownClass(cls):
        _restore(cls.saved)

    def _drops(self, term, aliases):
        return self.patch._split(term, aliases)[1]

    def test_the_reported_case(self):
        self.assertEqual(
            self._drops("Flooded suction", "flooded-suction\nflooded\nflooded suction line"),
            ["flooded"],
        )

    def test_the_term_still_matches_its_own_name(self):
        """Nothing is lost. A lesson actually discussing flooded suction still finds the entry —
        what goes is the claim on the bare word."""
        keep, _gone = self.patch._split("Flooded suction", "flooded\nflooded-suction")
        self.assertIn("flooded-suction", keep)

    def test_the_other_ordinary_words(self):
        for term, alias in (
            ("Mechanical seal", "seal"),
            ("Circuit breaker", "breaker"),
            ("Sub-panel", "panel"),
            ("Float switch", "float"),
            ("Return inlet", "return"),
            ("Water hammer", "hammer"),
            ("Conduit body", "body"),
            ("Head loss", "loss"),
        ):
            with self.subTest(f"{term} / {alias}"):
                self.assertEqual(self._drops(term, alias), [alias])

    # ------------------------------------------------------------------ what is spared

    def test_an_acronym_fragment_is_kept(self):
        """Nobody writes NEMA or PTFE meaning anything else, so there the fragment IS the word
        people say. Dropping these would lose real matches and prevent nothing."""
        for term, alias in (
            ("ASHRAE Standard 188", "ASHRAE"),
            ("NEMA Type 6P", "NEMA"),
            ("NFPA 70E", "NFPA"),
            ("PTFE tape", "PTFE"),
            ("IP rating", "IP"),
            ("UV chamber", "UV"),
        ):
            with self.subTest(f"{term} / {alias}"):
                self.assertEqual(self._drops(term, alias), [])

    def test_a_proper_noun_fragment_is_kept(self):
        self.assertEqual(self._drops("Langelier Saturation Index", "Langelier"), [])

    def test_a_number_fragment_is_kept(self):
        """`316` for 316 stainless, `680` for Article 680 — an identifier, not a word."""
        self.assertEqual(self._drops("316 stainless", "316"), [])
        self.assertEqual(self._drops("Article 680", "680"), [])

    def test_a_real_synonym_is_kept(self):
        """`haunch` is not a word of "Haunching" — it is a different spelling of the same idea."""
        self.assertEqual(self._drops("Haunching", "haunch\nhaunches"), [])

    def test_a_multi_word_alias_is_kept(self):
        self.assertEqual(self._drops("Flooded suction", "flooded suction line"), [])

    def test_a_single_word_term_gives_nothing_away(self):
        """Its only word IS the term, and `_clean_aliases` already refuses an alias equal to it."""
        self.assertEqual(self._drops("Weir", "weirs\nspillway"), [])


class TestTheNamedAcronymMerges(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib

        cls.saved = _install(_Site([]), ["Training Author"])
        cls.patch = importlib.import_module(
            "erpnext_enhancements.patches.merge_named_glossary_acronyms"
        )

    @classmethod
    def tearDownClass(cls):
        _restore(cls.saved)

    def test_the_five_pairs_are_named_not_derived(self):
        """No rule can tell that LSI means Langelier Saturation Index rather than large-scale
        integration. One loose enough to pair them would pair things that merely look alike."""
        pairs = dict(self.patch.PAIRS)
        self.assertEqual(pairs["AHJ"], "Authority having jurisdiction")
        self.assertEqual(pairs["LSI"], "Langelier Saturation Index")
        self.assertEqual(len(self.patch.PAIRS), 5)

    def test_the_acronym_is_the_one_that_goes(self):
        """It survives as an alias, so a lesson saying LSI still matches — it just reaches one
        entry instead of two."""
        for acronym, spelled_out in self.patch.PAIRS:
            with self.subTest(acronym):
                self.assertLess(len(acronym), len(spelled_out))

    def test_it_reuses_the_review_screens_merge(self):
        self.assertIn("from erpnext_enhancements.training.glossary_review import merge_into", _raw(self.PATCH_SRC))

    PATCH_SRC = APP_ROOT / "patches" / "merge_named_glossary_acronyms.py"

    def test_it_never_creates_the_survivor(self):
        """If the spelled-out entry is gone somebody has already reorganised this, and guessing
        which entry should absorb the acronym is not a guess a patch gets to make."""
        self.assertIn("is not there to merge into", _raw(self.PATCH_SRC))

    def test_both_patches_are_registered(self):
        registered = _raw(APP_ROOT / "patches.txt")
        self.assertIn("erpnext_enhancements.patches.merge_named_glossary_acronyms", registered)
        self.assertIn("erpnext_enhancements.patches.drop_fragment_glossary_aliases", registered)


# ----------------------------------------------------------------------------- the page


class TestThePage(unittest.TestCase):
    def test_the_folder_matches_the_page_name(self):
        """Frappe resolves a Desk Page's assets from `scrub(name)`, so a folder that does not match
        means the JS and CSS are never served and the page renders empty."""
        import json

        page = json.loads(_raw(PAGE_JSON))
        self.assertEqual(PAGE_DIR.name, page["name"].replace("-", "_"))
        self.assertEqual(page["page_name"], page["name"])

    def test_it_is_in_the_training_module(self):
        import json

        self.assertEqual(json.loads(_raw(PAGE_JSON))["module"], "Training")

    def test_its_roles_match_who_the_server_lets_in(self):
        """A page somebody can open and every button refuses is worse than no page."""
        import json

        page_roles = {row["role"] for row in json.loads(_raw(PAGE_JSON))["roles"]}
        src = _raw(MODULE_PY)
        declared = src.split("REVIEWER_ROLES = {", 1)[1].split("}", 1)[0]
        server_roles = set(re.findall(r'"([^"]+)"', declared))
        self.assertEqual(page_roles, server_roles)

    def test_the_rail_links_to_it(self):
        """The queue is not forced by any gate, so the link is the only thing that makes it exist."""
        self.assertIn("training-glossary-review", _raw(DESK_NAV))

    def test_there_is_no_bulk_accept(self):
        """review.py's reasoning, applied here: a button that clears the backlog in one click makes
        reviewed-vs-unreviewed meaningless, and that distinction is the only thing the Help panel
        has to tell a learner with."""
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        for marker in ("accept_all", "acceptAll", "bulk"):
            with self.subTest(marker):
                self.assertNotIn(marker, js)

    def test_a_merge_asks_first(self):
        """It deletes a row and cannot be undone."""
        js = _raw(PAGE_JS)
        merge = js.split("\tmerge(loser, winner, card) {", 1)[1][:600]
        self.assertIn("frappe.confirm", merge)

    def test_every_class_it_renders_has_a_rule(self):
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        css = _raw(PAGE_CSS)
        rendered = set(re.findall(r'class="([^"]*gr-[^"]*)"', js))
        classes = set()
        for blob in rendered:
            for name in blob.split():
                if name.startswith("gr-"):
                    classes.add(name)
        missing = sorted(name for name in classes if f".{name}" not in css)
        self.assertEqual(missing, [], f"rendered but never styled: {missing}")

    def test_the_page_declares_no_palette_of_its_own(self):
        """Desk tokens only, so it follows the desk theme and needs no dark-mode handling."""
        self.assertIsNone(re.search(r"^\s*--(?!\s)", _raw(PAGE_CSS), re.M))

    def test_this_suite_runs_in_ci(self):
        self.assertIn("erpnext_enhancements.tests.test_glossary_review", _raw(CI))


class TestBackAndForwardWalkTheTabs(unittest.TestCase):
    """The tab is in the route (v1.535.2): /desk/training-glossary-review, .../traps,
    .../collisions.

    Before, the tabs, the trap toggle and the pager were instance state under one URL,
    so browser Back from a tab left the page — and on_page_show ran refresh(), whose
    first act is `this.body.empty()`, so a return from "Open the record" wiped every
    correction typed into a card. Executed in `scripts/test_training_desk_history.mjs`
    against the real page; these pin the two rules underneath it.
    """

    def test_a_show_that_changes_nothing_reloads_nothing(self):
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        show = js.split('frappe.pages["training-glossary-review"].on_page_show', 1)[1][:200]
        self.assertIn("handleRoute()", show)
        self.assertNotIn("refresh()", show)
        body = js.split("\thandleRoute() {", 1)[1][:400]
        self.assertIn("if (view === this.view) return;", body)

    def test_a_typed_correction_outlives_a_reload_of_its_card(self):
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        self.assertIn("this.drafts[entry.name]", js)
        self.assertIn('box.on("input"', js)

    def test_the_primary_refresh_is_still_the_way_back_to_the_server_text(self):
        """Every other rebuild keeps what was typed; Refresh drops it for the cards on
        screen, as it always did, and asks first when there is something to lose."""
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        self.assertIn('set_primary_action(__("Refresh"), () => this.reload()', js)
        body = js.split("\treload() {", 1)[1].split("\trefresh() {", 1)[0]
        self.assertIn("frappe.confirm(", body)
        self.assertIn("delete this.drafts[name]", body)

    def test_next_waits_for_the_verdicts_still_in_flight(self):
        """`judged` counts a verdict when its reply lands, so Accept then Next inside one
        round trip advanced by the whole page and skipped an entry."""
        js = re.sub(r"//[^\n]*", "", _raw(PAGE_JS))
        pager = js.split("\tpager(data) {", 1)[1].split("\tturn(start) {", 1)[0]
        self.assertIn("Promise.all(", pager)
        self.assertIn("this.sending", pager)
        self.assertEqual(js.count("this.send("), 2, "Accept and Disable must both be tracked")

    # Skipped where node is genuinely absent -- a laptop without it -- but never in CI.
    # This wrapper is the only thing that runs the executed checks there, and a skip
    # reports OK: a runner image without node would pass them all without running one.
    @unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "node is not on PATH")
    def test_the_executed_harness_passes(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "no node on PATH in CI: the Back/Forward harness did not run")
        result = subprocess.run(
            [node, str(HISTORY_HARNESS), "glossary"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(REPO_ROOT),
            timeout=120,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"\b([1-9]\d*)/\1 passed")


if __name__ == "__main__":
    unittest.main()
