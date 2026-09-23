"""``Task.custom_enhancement_request``: the fixture, the backfill patch, and what they must agree on.

Bench-free: JSON and AST only, no frappe import. WI-079 slice 1 / ADR 0016 §1.

The field reaches a site two ways in the same migrate. The patch creates it first, because
patches run before fixture sync and the backfill needs the column, and fixture sync then adopts the
record of the same name. So the two definitions must say the same thing, or the patch's copy wins
on the deploy that introduces the field and the fixture's on every one after — a drift nobody
would see. The patch also has four properties that only matter on production and fail silently:
it must be registered after ``[post_model_sync]``, never ``save()`` a Task (that fires the Task
hooks and bumps ``modified``), anchor its group match on the writer's origin note (hand-written
Tasks name requests in prose), and give the field no ``default`` (on a normal doctype the ALTER
would write it into every existing row, and an emptiness predicate would then lie).

Run: python -m unittest erpnext_enhancements.tests.test_feedback_task_backlink
"""

import ast
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FIXTURE = APP / "fixtures" / "custom_field.json"
PATCH = APP / "patches" / "backfill_task_enhancement_request.py"
PATCHES_TXT = APP / "patches.txt"
DOTTED = "erpnext_enhancements.patches.backfill_task_enhancement_request"
NAME = "Task-custom_enhancement_request"


def _fixture_record():
	records = json.loads(FIXTURE.read_text(encoding="utf-8"))
	matches = [record for record in records if record.get("name") == NAME]
	return matches[0] if len(matches) == 1 else None


def _patch_field():
	"""The ``FIELD`` dict literal in the patch, evaluated without importing frappe."""
	tree = ast.parse(PATCH.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(target, ast.Name) and target.id == "FIELD" for target in node.targets
		):
			return ast.literal_eval(node.value)
	return None


class TestFixtureAndPatchAgree(unittest.TestCase):
	def test_the_fixture_defines_the_field_once(self):
		record = _fixture_record()
		self.assertIsNotNone(record, f"{NAME} must appear exactly once in custom_field.json")
		self.assertEqual(record["dt"], "Task")
		self.assertEqual(record["fieldtype"], "Link")
		self.assertEqual(record["options"], "Enhancement Request")
		self.assertEqual(record["is_system_generated"], 0)
		self.assertEqual(record["read_only"], 1)

	def test_no_copy_so_a_recurring_successor_does_not_inherit_the_link(self):
		# tasks.create_duplicate_task clones with frappe.copy_doc, which carries every field
		# that is not no_copy.
		self.assertEqual(_fixture_record()["no_copy"], 1)

	def test_no_default(self):
		self.assertIsNone(_fixture_record()["default"])
		self.assertNotIn("default", _patch_field())

	def test_the_patch_creates_exactly_the_fixture_definition(self):
		field = _patch_field()
		self.assertIsNotNone(field, "the patch must define FIELD as a literal")
		record = _fixture_record()
		for key, value in field.items():
			with self.subTest(key=key):
				self.assertEqual(record[key], value)


class TestThePatchIsSafeOnProduction(unittest.TestCase):
	def setUp(self):
		self.source = PATCH.read_text(encoding="utf-8")

	def test_registered_after_post_model_sync(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		self.assertIn(DOTTED, lines)
		self.assertGreater(lines.index(DOTTED), lines.index("[post_model_sync]"))
		self.assertEqual(lines.count(DOTTED), 1)

	def test_never_saves_a_task(self):
		tree = ast.parse(self.source)
		saves = [
			node.lineno
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and isinstance(node.func, ast.Attribute)
			and node.func.attr in ("save", "insert", "db_update")
		]
		self.assertEqual(saves, [], "stamp with set_value(update_modified=False), not save()")
		self.assertIn("update_modified=False", self.source)

	def test_the_group_match_is_anchored_on_the_origin_note(self):
		tree = ast.parse(self.source)
		pattern = next(
			node.value.args[0].value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(isinstance(t, ast.Name) and t.id == "ORIGIN_NOTE" for t in node.targets)
		)
		self.assertTrue(pattern.startswith("^<p><b>Raised from "), pattern)
		self.assertIn('"<p><b>Raised from ER-%"', self.source)

	def test_it_creates_the_field_before_it_stamps(self):
		self.assertIn("create_custom_field", self.source)
		self.assertIn("is_system_generated=False", self.source)


if __name__ == "__main__":
	unittest.main()
