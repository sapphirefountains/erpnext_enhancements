# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Checkpoint completeness is refused at PUBLISH, not on every save. Bench-free.

**Both halves of this rule are asserted in this one file, deliberately.** A suite
that checked only the relaxation would go green on the dangerous half — and the
dangerous half is the one that reaches learners.

Why the relaxation
---------------------------------------------------------------------------

The classic builder's pin flow has never worked. `add_pin` seeds
`question_text: ""` and two options with `is_correct: 0`, while `question_text` was
`reqd: 1` and `_validate_options` threw *"Give the checkpoint at least two
options"* / *"Tick the correct option"*. So the insert was refused, and on a
four-second autosave that is a red dialog every four seconds until the author has
typed a question **and** ticked a correct option. Placing a pin was, in practice,
impossible. Porting that faithfully onto the canvas would have moved a day-one
defect onto the surface authors live in.

Why the gate has to ship with it
---------------------------------------------------------------------------

Relaxing `validate` alone lets a half-built checkpoint reach `_split_lesson`, which
writes an answer key of `"correct": []` — **a checkpoint nobody can ever pass**.
`grading._unanswered_checkpoints` then holds the lesson open forever, and nothing
raises anywhere: the learner is stuck on a video with no way forward and no error to
report. A release shipping only the relaxation would be strictly worse than shipping
neither.

The line between the two
---------------------------------------------------------------------------

*Incompleteness* is what every checkpoint has for the first few seconds of its life,
while somebody is typing. A *contradiction* — a Single Choice with two correct
answers, or one where every option is correct — can never become coherent by further
typing, so refusing it immediately costs nothing. Contradictions still throw in
`validate`; incompleteness waits for publish.

Run: python -m unittest erpnext_enhancements.tests.test_training_checkpoint_model
"""

import ast
import io
import json
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
CHECKPOINT_JSON = APP / "training/doctype/training_checkpoint/training_checkpoint.json"
CHECKPOINT_PY = APP / "training/doctype/training_checkpoint/training_checkpoint.py"
VERSION_PY = APP / "training/doctype/training_course_version/training_course_version.py"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(path):
    """Source with comments and docstrings stripped.

    Every absence assertion below would otherwise match the prose explaining the
    absence. Seven occurrences of that trap in this project so far.
    """
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _fn(path, name):
    """One function's source, docstring stripped, bounded at its own end."""
    src = _text(path)
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
            return "\n".join(lines[body[0].lineno - 1 : node.end_lineno]) if body else ""
    raise AssertionError(f"{name} not found in {path.name}")


def _field(fieldname):
    doc = json.loads(_text(CHECKPOINT_JSON))
    for f in doc["fields"]:
        if f["fieldname"] == fieldname:
            return f
    raise AssertionError(f"{fieldname} not on Training Checkpoint")


class TestTheRelaxation(unittest.TestCase):
    def test_question_text_is_not_required_at_save(self):
        """A reqd here meant the pin could not be written at all until the question
        was typed — so placing one was impossible, and the autosave retried the
        refusal every four seconds."""
        self.assertFalse(_field("question_text").get("reqd"))

    def test_the_doctype_modified_was_bumped(self):
        """DocType import is timestamp-gated. An unbumped `modified` never re-syncs
        to an existing site, so the relaxation would land in the repo and nowhere
        else — while the publish gate DID land, refusing publishes for a reason the
        author cannot act on."""
        doc = json.loads(_text(CHECKPOINT_JSON))
        self.assertGreater(doc["modified"], "2026-09-01")

    def test_incompleteness_no_longer_throws_on_save(self):
        body = _fn(CHECKPOINT_PY, "_validate_options")
        self.assertNotIn("at least two options", body)
        self.assertNotIn("Tick the correct option", body)

    def test_contradictions_still_throw_on_save(self):
        """These can never become coherent by further typing, so refusing them
        immediately costs nothing — and letting them through would mean a quiz that
        cannot be got wrong."""
        body = _fn(CHECKPOINT_PY, "_validate_options")
        self.assertIn("allows exactly one correct option", body)
        self.assertIn("cannot be got wrong", body)

    def test_the_structural_checks_are_untouched(self):
        """A checkpoint on a non-Video block can never fire, and its position rules
        are about the video, not about how finished the question is."""
        validate = _fn(CHECKPOINT_PY, "validate")
        for check in ("_validate_block", "_validate_position", "_assign_key"):
            with self.subTest(check=check):
                self.assertIn(check, validate)


class TestTheGateThatMakesItSafe(unittest.TestCase):
    """The half that must never be dropped."""

    def test_publish_refuses_an_unfinished_checkpoint(self):
        self.assertIn("def _require_finished_checkpoints(self)", _text(VERSION_PY))

    def test_it_runs_before_submit(self):
        body = _fn(VERSION_PY, "before_submit")
        self.assertIn("_require_finished_checkpoints", body)

    def test_it_runs_before_the_content_is_materialized(self):
        """`_materialize_lessons` writes the answer key. Refusing after it has run
        would leave the hollow key behind."""
        body = _fn(VERSION_PY, "before_submit")
        self.assertLess(
            body.index("_require_finished_checkpoints"),
            body.index("_require_materialized_content"),
        )

    def test_the_gate_reads_the_one_implementation(self):
        """Not a second copy of the rule. Two implementations of "unfinished" drift,
        and the drift is invisible until a learner is stuck.

        Since v1.410.0 the gate reads it INDIRECTLY: the loop moved into
        `unfinished_checkpoint_problems` so the canvas can show the same sentences
        while the version is still a draft. The chain is what matters, so both links
        are asserted -- a gate that stopped delegating would pass a test on the"
        builder alone."""
        gate = _fn(VERSION_PY, "_require_finished_checkpoints")
        self.assertIn("unfinished_checkpoint_problems()", gate)
        builder = _fn(VERSION_PY, "unfinished_checkpoint_problems")
        self.assertIn("incomplete_reasons()", builder)

    def test_it_names_the_lesson_and_the_timestamp(self):
        """"A checkpoint is unfinished somewhere in a forty-lesson course" is a
        scavenger hunt, not an error message. Now built in
        `unfinished_checkpoint_problems`, and read by both the gate and the draft
        advisory, so the author sees the same sentence either way."""
        body = _fn(VERSION_PY, "unfinished_checkpoint_problems")
        self.assertIn("lesson_title", body)
        self.assertIn("at_seconds", body)

    def test_the_builder_never_throws(self):
        """The advisory half of the contract. A panel that raised while somebody was
        typing would be the four-second red dialog this whole work item exists to
        remove."""
        body = _fn(VERSION_PY, "unfinished_checkpoint_problems")
        self.assertNotIn("frappe.throw", body)
        self.assertIn("return problems", body)


class TestTheOneImplementation(unittest.TestCase):
    def test_incomplete_reasons_exists_and_never_throws(self):
        """A caller asking what is missing must be able to ask about a checkpoint
        that is missing everything."""
        body = _fn(CHECKPOINT_PY, "incomplete_reasons")
        self.assertNotIn("frappe.throw", body)
        self.assertIn("return why", body)

    def test_it_covers_all_three_ways_of_being_unfinished(self):
        body = _fn(CHECKPOINT_PY, "incomplete_reasons")
        self.assertIn("question_text", body)
        self.assertIn("len(rows) < 2", body)
        self.assertIn("is_correct", body)

    def test_an_empty_list_means_ready(self):
        body = _fn(CHECKPOINT_PY, "incomplete_reasons")
        self.assertIn("why = []", body)


class TestBothHalvesShippedTogether(unittest.TestCase):
    """The assertion this file exists for.

    Shipping the relaxation without the gate lets a checkpoint with no correct
    option reach `_split_lesson`, which writes `"correct": []` — a checkpoint nobody
    can pass, holding the lesson open forever with nothing raising anywhere.
    """

    def test_relaxed_and_gated_in_the_same_tree(self):
        relaxed = not _field("question_text").get("reqd")
        gated = "def _require_finished_checkpoints(self)" in _text(VERSION_PY)
        called = "_require_finished_checkpoints" in _fn(VERSION_PY, "before_submit")
        self.assertEqual(
            (relaxed, gated, called),
            (True, True, True),
            "relaxation and publish gate must ship together; one without the other is "
            "worse than neither",
        )

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_training_checkpoint_model", ci)


if __name__ == "__main__":
    unittest.main()
