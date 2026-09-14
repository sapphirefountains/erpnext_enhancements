# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The `create_training_draft_version` assistant tool. Bench-free.

The rung between authoring and publishing, and its absence had a concrete cost:
nothing could open a draft of a course that **already exists**. An assistant asked to
correct a published course had two options and both were wrong — edit the live
version's lessons in place (which frappe permits, because a Training Lesson is a
plain document, and which the module forbids because learners are reading it and a
completion says exactly what somebody passed), or stop and ask a human to press a
button.

What this suite guards is the two ways the tool could be wrong that nothing else
would catch: **reimplementing the clone** instead of delegating to
`create_draft_version`, and a risk classification that lets a write on a live
training record through with no confirmation.

The clone is the part that must not be reinvented. `create_draft_version` preserves
`lesson_key`, `block_key` and the checkpoint and chapter keys; a hand-built copy
mints fresh ones, which silently strands every in-flight resume position, every
in-video checkpoint and every video chapter, because all of them join on the key
rather than on an index. Nothing errors. The learner simply restarts the course.

Run: python -m unittest erpnext_enhancements.tests.test_training_draft_tool
"""

import ast
import io
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TOOL = APP / "assistant_tools/create_training_draft_version.py"
GATE = APP / "assistant_tools/_gate.py"
HOOKS = APP / "hooks.py"
AUTHOR_API = APP / "api/training_author.py"

TOOL_NAME = "create_training_draft_version"


def _text(path):
    return path.read_text(encoding="utf-8")


def _calls(path):
    """Every function name this module actually CALLS.

    The three absence tests below are about behaviour, not vocabulary, and text
    matching cannot tell them apart: this tool's `description` is executable code,
    and it legitimately names `publish_training_course` (to point the caller at the
    next step) and the stable keys (to explain why the clone must not be hand-built).
    A substring search flagged its own documentation as an implementation.
    """
    tree = ast.parse(_text(path))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def _executable(path):
    """Source with comments and docstrings stripped.

    This tool's docstring is largely an explanation of the rules it deliberately does
    NOT implement, so it names every token an absence assertion here looks for. The
    publish-tool suite needed the same helper for the same reason; it is the single
    most repeated test-shape mistake in this repo.
    """
    source = _text(path)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok)
    stripped = tokenize.untokenize(out)

    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                body.pop(0)
    # Quote-normalised. `ast.unparse` rewrites every string literal to single
    # quotes, so an assertion written the way the source actually reads fails on a
    # difference that is not one. Normalising here rather than doubling every
    # assertion, and it only ever feeds substring matching.
    return ast.unparse(tree).replace("'", '"')


class TestItIsRegistered(unittest.TestCase):
    def test_the_file_exists(self):
        self.assertTrue(TOOL.exists(), f"{TOOL} is missing")

    def test_the_tool_name_matches_the_filename(self):
        """FAC's loader keys on it, and a mismatch makes the tool unreachable with no
        error anywhere."""
        self.assertIn(f'self.name = "{TOOL_NAME}"', _text(TOOL))
        self.assertEqual(TOOL.stem, TOOL_NAME)

    def test_hooks_registers_the_class(self):
        self.assertIn(
            "erpnext_enhancements.assistant_tools.create_training_draft_version.CreateTrainingDraftVersion",
            _text(HOOKS),
        )


class TestItGates(unittest.TestCase):
    def test_it_is_classified_as_mutating(self):
        """Otherwise it falls through to the gate's fail-closed default rather than
        being declared, and the classification a client reads becomes a guess."""
        gate = _executable(GATE)
        block = gate[gate.index("APP_MUTATING = {") : gate.index("HIGH_RISK = {")]
        self.assertIn(f'"{TOOL_NAME}"', block)

    def test_it_is_not_high_risk(self):
        """Nothing is destroyed, nothing arbitrary executes, no learner sees a change
        and an unwanted draft is simply deleted. Marking it destructive would put a
        scary card in front of the safest write in the group and teach people to
        click through them."""
        gate = _executable(GATE)
        block = gate[gate.index("HIGH_RISK = {") :]
        self.assertNotIn(f'"{TOOL_NAME}"', block[: block.index("}")])

    def test_it_advertises_its_annotations_from_the_gate(self):
        """Derived, not retyped, so the gate stays the single source of truth."""
        self.assertIn("annotations_for(self.name)", _text(TOOL))


class TestItDelegatesRatherThanReimplements(unittest.TestCase):
    def test_it_calls_the_real_endpoint(self):
        code = _executable(TOOL)
        self.assertIn("from erpnext_enhancements.api.training_author import create_draft_version", code)
        self.assertIn("create_draft_version(course, change_type)", code)

    def test_it_clones_nothing_itself(self):
        """The keys are the reason. `create_draft_version` preserves lesson_key,
        block_key and the checkpoint keys; anything hand-built mints fresh ones and
        silently strands every resume position, checkpoint and video chapter."""
        calls = _calls(TOOL)
        for name in ("_clone_lessons", "new_doc", "append"):
            with self.subTest(name):
                self.assertNotIn(name, calls)

    def test_it_creates_no_version_document_by_hand(self):
        """`create_document` on Training Course Version makes an empty shell with no
        lessons in it, which looks like success and is not."""
        calls = _calls(TOOL)
        self.assertNotIn("new_doc", calls)
        self.assertNotIn("insert", calls)

    def test_the_endpoint_it_calls_still_exists_with_that_signature(self):
        """A rename upstream would make this tool fail only at call time, on a live
        site, in front of somebody trying to fix a course."""
        self.assertIn("def create_draft_version(course, change_type=None):", _text(AUTHOR_API))

    def test_it_does_not_reimplement_authority(self):
        """`create_draft_version` calls `_require_author` and then check_permission
        on the course. A second copy here would be a second thing to keep in step."""
        calls = _calls(TOOL)
        self.assertNotIn("_require_author", calls)
        self.assertNotIn("get_roles", calls)
        self.assertNotIn("has_permission", calls)


class TestItStopsShortOfPublishing(unittest.TestCase):
    def test_it_never_publishes(self):
        """Opening a draft and publishing it are different decisions, and only one of
        them can supersede somebody's completion."""
        calls = _calls(TOOL)
        for name in ("publish_version", "submit", "publish"):
            with self.subTest(name):
                self.assertNotIn(name, calls)
        # And it delegates the one thing it does do, rather than open-coding it.
        self.assertIn("create_draft_version", calls)

    def test_change_type_is_documented_as_not_the_decision(self):
        """It is only a note at draft stage. The Minor Edit / Material Change choice
        that actually supersedes completions is made by publish_training_course, and
        a tool that implied otherwise here would invite it being set and forgotten."""
        self.assertIn("publish_training_course", _text(TOOL))


class TestTheAlreadyOpenDraftCase(unittest.TestCase):
    def test_an_existing_draft_is_returned_rather_than_raised(self):
        """`create_draft_version` throws when a draft exists. That reads as a failure
        and it is not one — the caller wanted an editable draft of this course and
        there is one. Answering before calling turns a traceback into an answer."""
        code = _executable(TOOL)
        self.assertIn('"created": False', code)
        at = code.index('"created": False')
        self.assertIn("draft_version", code[at - 400 : at + 400])

    def test_it_never_opens_a_second_draft(self):
        """Two drafts of one course both claim the same next version number, and the
        second to publish would overwrite the first author's work."""
        code = _executable(TOOL)
        self.assertEqual(code.count("create_draft_version(course, change_type)"), 1)


class TestItIsWiredIntoCI(unittest.TestCase):
    def test_ci_runs_this_module(self):
        ci = (APP.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.tests.test_training_draft_tool", ci)


if __name__ == "__main__":
    unittest.main()
