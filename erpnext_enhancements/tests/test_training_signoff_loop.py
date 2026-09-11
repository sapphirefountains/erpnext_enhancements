"""The sign-off loop: does recording a verdict actually finish anything?

Three defects shipped together and stayed live from v1.215.0 to v1.386.0. All
three share a shape — the code that *records* an attestation was complete and
correct, and the code that should have acted on it did not exist:

1. **``Training Signoff`` had no ``on_submit``.** The controller held ``validate``
   and ``before_submit`` and five private helpers. ``record_signoff`` submitted
   the document, emailed the learner and returned. So a supervisor recording
   *Competent* moved nothing: no completion was minted and the assignment stayed
   parked at ``Awaiting Sign-off`` for ever. The only path to a completion was the
   learner going back to ``/training`` and pressing finish a second time — which,
   having been told they were done and waiting on somebody else, nobody does.
   The controller docstring asserted that ``training/grading.py`` re-opened the
   assignment on a cancel; ``grading.py`` contains no reference to sign-off at all.

2. **``competent_signoff_name`` had no date clause**, so an attestation was good
   for ever while the course it backed recertified on a schedule.
   ``TRN-CRS-00001`` recertifies every 24 months: at month 25 the completion
   expired, the assignment was raised again, the learner re-watched the video, and
   the gate re-opened against the *original two-year-old signature*. Note the
   failure direction — **it passes**. A compliance check that reports clean when
   it should fail is worse than not having one, because nobody goes looking.

3. **The gamification tables leaked.** ``Training Badge Award`` and ``Training
   Learner Stat`` each grant ``read`` to ``Training Learner`` in their doctype
   JSON and neither was registered in ``permission_query_conditions``. ``Training
   Learner`` is held by customer Website Users, so a client contact could
   enumerate every staff member's points, streaks and badges through
   ``/api/resource``. DocPerms with no scoping hook is the one combination that
   leaks, and it is invisible until somebody looks.

These are static assertions on source and JSON, deliberately: this app has no
Frappe integration-test job, and all three defects are the kind a bench-free
reader can catch. Where a test asserts *presence* of a call it also asserts the
delegation chain around it, because "the string is in the file" is how the
Phase-4 contract passed on a gate that did not gate.

Run: python -m unittest erpnext_enhancements.tests.test_training_signoff_loop
"""

import ast
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
RUNTIME = APP / "api/training.py"
SIGNOFF = APP / "training/signoff.py"
PERMISSIONS = APP / "training/permissions.py"
HOOKS = APP / "hooks.py"
CONTROLLER = APP / "training/doctype/training_signoff/training_signoff.py"


def _read(path):
    return path.read_text(encoding="utf-8")


def _fn(path, name):
    """A top-level function's source, from ``def name`` to the next top-level def."""
    src = _read(path)
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


def _fn_node(path, name):
    """The ``ast.FunctionDef`` for a top-level function.

    Used wherever the assertion is about what the code *does* rather than what it
    says. Searching the source text for ``"raise"`` finds the word in a docstring
    promising the function never raises — which is the same class of mistake as
    the Phase-4 contract that passed on a gate that did not gate.
    """
    for node in ast.parse(_read(path)).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {path.name}")


def _raises(node):
    """Every ``raise`` statement lexically inside *node*, docstrings excluded."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Raise)]


def _calls(node):
    """Names of every function called inside *node*. ``foo()`` and ``a.foo()``."""
    names = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        fn = n.func
        if isinstance(fn, ast.Name):
            names.add(fn.id)
        elif isinstance(fn, ast.Attribute):
            names.add(fn.attr)
    return names


def _method(path, cls, name):
    """A method's source, extracted through the AST so indentation cannot fool it."""
    tree = ast.parse(_read(path))
    lines = _read(path).splitlines()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return "\n".join(lines[item.lineno - 1 : item.end_lineno])
    raise AssertionError(f"{cls}.{name} not found in {path.name}")


def _hooks_dict(name):
    """One top-level dict literal from hooks.py, by assigned name."""
    tree = ast.parse(_read(HOOKS))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"hooks.py has no {name} dict")


class TestSignoffClosesTheLoop(unittest.TestCase):
    """Recording Competent has to advance something."""

    def test_the_controller_has_an_on_submit(self):
        src = _read(CONTROLLER)
        self.assertIn("def on_submit(self):", src)
        self.assertIn("def on_cancel(self):", src)

    def test_on_submit_delegates_to_the_routing_module(self):
        """Both transitions live in ``training/signoff.py``, which already owns the
        write in the other direction (``_mark_assignment_awaiting``). Two owners of
        one status column is how they drift."""
        self.assertIn("after_signoff_submitted", _method(CONTROLLER, "TrainingSignoff", "on_submit"))
        self.assertIn("after_signoff_cancelled", _method(CONTROLLER, "TrainingSignoff", "on_cancel"))

    def test_competent_redrives_the_attempt(self):
        node = _fn_node(SIGNOFF, "after_signoff_submitted")
        self.assertIn("resume_after_signoff", _calls(node))
        self.assertIn("COMPETENT", {n.id for n in ast.walk(node) if isinstance(n, ast.Name)})

    def test_needs_practice_unparks_the_assignment(self):
        """Left in Awaiting Sign-off it reads as 'waiting on somebody else' for ever,
        when in fact the ball is back with the learner."""
        body = _fn(SIGNOFF, "after_signoff_submitted")
        self.assertIn('_set_assignment_status(doc, "In Progress")', body)

    def test_cancelling_reopens_the_gate(self):
        self.assertIn("_set_assignment_status(doc, AWAITING)", _fn(SIGNOFF, "after_signoff_cancelled"))

    def test_neither_transition_can_break_the_attestation(self):
        """These run inside submit/cancel. The attestation is the evidence; the
        bookkeeping behind it can be re-driven, so it must never take the document
        down with it."""
        for name in ("after_signoff_submitted", "after_signoff_cancelled"):
            with self.subTest(fn=name):
                node = _fn_node(SIGNOFF, name)
                self.assertIn("log_error", _calls(node))
                self.assertEqual(
                    _raises(node), [], f"{name} can raise out of a submit/cancel"
                )
                handlers = [n for n in ast.walk(node) if isinstance(n, ast.Try)]
                self.assertTrue(handlers, f"{name} has no try/except at all")

    def test_the_stale_grading_claim_is_gone(self):
        """The controller docstring named ``training/grading.py`` as what re-opened
        the assignment. It never did — assert the claim stays deleted, and assert
        the thing that made it false."""
        self.assertNotIn("grading.py`` is what re-opens", _read(CONTROLLER))
        self.assertNotIn("signoff", _read(APP / "training/grading.py").lower())


class TestResumeIsSafeAndNotAShortcut(unittest.TestCase):
    """The sign-off unblocks one gate. It does not grant a pass."""

    def test_resume_reevaluates_every_gate(self):
        """Re-running the whole evaluation, not writing a completion directly, is
        what stops a sign-off arriving mid-course from certifying somebody with
        three lessons left."""
        calls = _calls(_fn_node(RUNTIME, "resume_after_signoff"))
        self.assertIn("_evaluate_attempt", calls)
        self.assertNotIn("_issue_completion", calls)

    def test_resume_is_not_whitelisted(self):
        """It takes no verdict and performs no identity check of its own, because
        the caller is a doc_event with no session learner. Exposing it over HTTP
        would let anyone finish anyone's attempt."""
        src = _read(RUNTIME)
        idx = src.index("def resume_after_signoff")
        preceding = src[max(0, idx - 400) : idx]
        self.assertNotIn("@frappe.whitelist", preceding)

    def test_resume_cannot_raise(self):
        node = _fn_node(RUNTIME, "resume_after_signoff")
        self.assertIn("log_error", _calls(node))
        self.assertEqual(_raises(node), [], "resume_after_signoff can raise into on_submit")

    def test_finish_attempt_still_delegates(self):
        """``_evaluate_attempt`` holds the gate logic that
        ``test_training_runtime_regressions`` asserts on. If somebody inlines it
        back into ``finish_attempt``, those assertions start reading a function
        that no longer contains what they check — so pin the split here."""
        self.assertIn("_evaluate_attempt", _calls(_fn_node(RUNTIME, "finish_attempt")))

    def test_the_learner_identity_check_stays_on_the_public_entry(self):
        """``_evaluate_attempt`` is reached from a doc_event with no session
        learner, so the identity check has to sit on the whitelisted entry and
        nowhere else — repeating it inside would make the sign-off resume throw."""
        self.assertIn("_learner", _calls(_fn_node(RUNTIME, "finish_attempt")))
        self.assertNotIn("_learner", _calls(_fn_node(RUNTIME, "_evaluate_attempt")))


class TestSignoffExpiresWithTheCourse(unittest.TestCase):
    """The check that passed when it should have failed."""

    def test_the_lookup_reads_the_recertification_window(self):
        self.assertIn("recertify_months", _fn(SIGNOFF, "competent_signoff_name"))

    def test_the_comparison_is_not_pushed_into_the_filter(self):
        """A datetime comparison inside ``get_value`` filters is coalesced, so a row
        with a NULL ``signed_on`` — every sign-off written before
        ``_stamp_signed_on`` existed — lands on whichever side of the comparison
        the sentinel falls, and it is not the side you assumed."""
        body = _fn(SIGNOFF, "competent_signoff_name")
        self.assertNotIn('"signed_on", ">="', body)
        self.assertIn("add_months(now_datetime(), -months)", body)

    def test_a_missing_signature_date_fails_closed(self):
        body = _fn(SIGNOFF, "competent_signoff_name")
        self.assertIn("if not row.signed_on:", body)
        after = body[body.index("if not row.signed_on:") :]
        self.assertIn("return None", after.split("\n")[1])

    def test_no_window_means_no_expiry(self):
        """A course that never recertifies has an attestation that never lapses —
        the honest reading, and it keeps Optional courses out of the machinery."""
        self.assertIn("if months <= 0:", _fn(SIGNOFF, "competent_signoff_name"))

    def test_the_newest_attestation_wins(self):
        self.assertIn('order_by="signed_on desc, creation desc"', _fn(SIGNOFF, "competent_signoff_name"))


class TestGamificationTablesAreScoped(unittest.TestCase):
    """DocPerms with no scoping hook is the one combination that leaks."""

    LEAKY = ("Training Badge Award", "Training Learner Stat")

    def test_both_have_a_query_condition(self):
        registered = _hooks_dict("permission_query_conditions")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_both_have_the_single_document_twin(self):
        """A query condition filters lists and says nothing about
        ``frappe.get_doc()``, so shipping one without the other leaves the hole in
        whichever half you skipped."""
        registered = _hooks_dict("has_permission")
        for doctype in self.LEAKY:
            with self.subTest(doctype=doctype):
                self.assertIn(doctype, registered)

    def test_the_handlers_exist(self):
        src = _read(PERMISSIONS)
        for fn in (
            "badge_award_query_conditions",
            "learner_stat_query_conditions",
            "badge_award_has_permission",
            "learner_stat_has_permission",
        ):
            with self.subTest(fn=fn):
                self.assertIn(f"def {fn}(", src)

    def test_every_training_doctype_granting_a_learner_read_is_registered(self):
        """The generalisation, and the reason this suite exists: the two that leaked
        were found by reading, not by a rule. Any Training doctype that grants read
        to ``Training Learner``, carries a ``user`` column and is not a child table
        must be scoped — so a future one cannot ship the same way.
        """
        registered = set(_hooks_dict("permission_query_conditions"))
        unscoped = []
        for path in sorted((APP / "training/doctype").glob("*/*.json")):
            data = json.loads(_read(path))
            if data.get("istable"):
                continue
            grants_learner = any(
                p.get("role") == "Training Learner" and p.get("read")
                for p in data.get("permissions", [])
            )
            # `user` OR `from_user`: the convention is `user`, and Training Kudos
            # broke it. A naming convention is only a safety net where it is
            # actually followed, so the scan looks for either -- Kudos grants
            # Training Learner read and would have gone straight past a check that
            # trusted the convention.
            owner_columns = {"user", "from_user"}
            has_user = any(
                f.get("fieldname") in owner_columns for f in data.get("fields", [])
            )
            if grants_learner and has_user and data.get("name") not in registered:
                unscoped.append(data.get("name"))
        self.assertEqual(
            unscoped,
            [],
            f"{unscoped} grant Training Learner read on rows carrying a `user` column "
            "with no permission_query_conditions — customer Website Users hold that role",
        )

    def test_the_badge_catalogue_stays_open(self):
        """The sibling that is deliberately not scoped. ``Training Badge`` is a
        catalogue of definitions with no ``user`` column and the player shows
        learners what there is to earn — pinned so nobody 'fixes' it."""
        registered = _hooks_dict("permission_query_conditions")
        self.assertNotIn("Training Badge", registered)


if __name__ == "__main__":
    unittest.main()
