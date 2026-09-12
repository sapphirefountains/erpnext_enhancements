# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The `publish_training_course` assistant tool. Bench-free.

Publishing was the one step of the AI authoring chain with no tool: `draft_course_spec`
proposes, `author_training_course` builds a Draft, and nothing could take it the last
inch. Not an oversight in the tool surface — publishing is **not a document submit**.
It is `api/training_author.publish_version`, which materializes `toc_json` and
`content_hash` from the lessons and only then submits. So the generic tools fail in
both directions, quietly: `submit_document` skips the materializer and is refused by
`_require_materialized_content`, and `update_document` on `toc_json` would satisfy that
gate while writing a table of contents nothing derived from the lessons.

What this suite guards is the two ways the tool could be wrong in a way no bench-free
test would otherwise notice: a `change_type` literal that does not match the DocType,
and a risk classification that lets a one-way door through without confirmation.

Run: python -m unittest erpnext_enhancements.tests.test_training_publish_tool
"""

import ast
import io
import json
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TOOL = APP / "assistant_tools/publish_training_course.py"
GATE = APP / "assistant_tools/_gate.py"
HOOKS = APP / "hooks.py"
VERSION_PY = APP / "training/doctype/training_course_version/training_course_version.py"
VERSION_JSON = APP / "training/doctype/training_course_version/training_course_version.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _executable(path):
    """Source with comments and docstrings stripped.

    Needed because this tool's docstring is largely an explanation of the rules it
    deliberately does NOT implement, so it names every token an absence assertion
    would look for.
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


def _module_const(path, name):
    for node in ast.parse(_text(path)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def _set_literal(path, name):
    """A module-level `NAME = {...}` set, read without importing (it imports frappe)."""
    for node in ast.parse(_text(path)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"{name} not found in {path.name}")


def _json_change_types():
    doc = json.loads(_text(VERSION_JSON))
    for field in doc["fields"]:
        if field["fieldname"] == "change_type":
            return [o for o in (field.get("options") or "").splitlines() if o.strip()]
    raise AssertionError("Training Course Version has no change_type field")


class TestTheChangeTypesCannotDrift(unittest.TestCase):
    """Three places name these strings and all three must agree.

    Retyping them got `Material Change (require retake)` wrong on the first attempt —
    as `(supersede completions)`, which is what the option *does* rather than what it
    is *called*. A wrong literal there passes every bench-free test and is then
    refused by the controller at the moment of publishing, which is the worst place to
    find out.
    """

    def test_the_tool_derives_them_rather_than_retyping_them(self):
        src = _text(TOOL)
        self.assertIn("_change_types()", src)
        self.assertIn("training_course_version.json", src)

    def test_the_controller_agrees_with_the_doctype(self):
        options = _json_change_types()
        self.assertEqual(_module_const(VERSION_PY, "MINOR_EDIT"), options[0])
        self.assertEqual(_module_const(VERSION_PY, "MATERIAL_CHANGE"), options[1])

    def test_there_are_exactly_two_and_the_order_is_the_one_assumed(self):
        """The tool unpacks `MINOR_EDIT, MATERIAL_CHANGE = _change_types()`, so a third
        option or a reordering would silently swap the two meanings — and the one that
        invalidates everybody's completions is the one that would arrive by default."""
        self.assertEqual(len(_json_change_types()), 2)
        self.assertIn("Minor Edit", _json_change_types()[0])
        self.assertIn("Material Change", _json_change_types()[1])


class TestItIsGatedAsAOneWayDoor(unittest.TestCase):
    def test_it_is_registered_as_mutating(self):
        """Not in APP_MUTATING, `is_mutating()` falls to the fail-closed default — which
        happens to confirm anyway, but by accident rather than by classification, and
        `test_every_registered_tool_is_classified` exists because that once went the
        other way for two read tools."""
        self.assertIn("publish_training_course", _set_literal(GATE, "APP_MUTATING"))

    def test_it_is_neither_low_risk_nor_high_risk(self):
        """Medium, deliberately. Not Low: unlike `author_training_course` — a create
        that yields a draft nobody can see — this is not undoable and reaches people.
        Not High: nothing is destroyed and nothing arbitrary executes."""
        self.assertNotIn("publish_training_course", _set_literal(GATE, "LOW_RISK"))
        self.assertNotIn("publish_training_course", _set_literal(GATE, "HIGH_RISK"))

    def test_it_is_wired_into_the_hook(self):
        self.assertIn(
            "erpnext_enhancements.assistant_tools.publish_training_course.PublishTrainingCourse",
            _text(HOOKS),
        )


class TestTheToolRefusesRatherThanGuesses(unittest.TestCase):
    def test_change_type_is_required_with_no_default(self):
        """The argument that can invalidate every existing completion of a course must
        never arrive by omission."""
        src = _text(TOOL)
        self.assertIn('"required": ["course", "change_type"]', src)
        self.assertNotIn('"default": MINOR_EDIT', src)
        self.assertNotIn("change_type = MINOR_EDIT", src)

    def test_it_resolves_the_draft_rather_than_taking_a_version_name(self):
        """A caller naming a version could name a submitted one, or the wrong one of
        several. `docstatus: 0` is the only safe input."""
        src = _text(TOOL)
        self.assertIn('"docstatus": 0', src)

    def test_it_delegates_authority_instead_of_reimplementing_it(self):
        """`publish_version` calls `_require_manager` and refuses unreviewed AI
        questions. A second copy of either rule here would be a second thing to keep
        in step, and the one that matters is the one that says an AI may draft a course
        but not ship it.

        Asserted over EXECUTABLE source only. The tool's own docstring names
        `_require_manager` precisely because it is explaining that the check lives
        there — this assertion failed on itself the first time it was run, which is the
        eighth occurrence of that trap in this project.
        """
        code = _executable(TOOL)
        self.assertIn("publish_version", code)
        self.assertNotIn("_require_manager", code)
        self.assertNotIn("has_role", code)

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_training_publish_tool", ci)


if __name__ == "__main__":
    unittest.main()
