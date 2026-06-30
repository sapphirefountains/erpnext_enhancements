"""Bench-free tests for the Custom HTML Block seeder's placement logic.

Like test_process_documents, this avoids importing frappe: the module-level
constants and the pure ``_merge_blocks`` helper are extracted with ``ast`` and
exec'd against a tiny ``frappe`` stub, so the seeder's ``import frappe`` (and its
``import os``) never run. This keeps the idempotency contract — the rule that the
cockpit is never placed twice — covered by plain ``pytest``/``unittest``.
"""

import ast
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "setup" / "custom_html_blocks.py"


class _FrappeStub:
	"""Just enough of ``frappe`` for ``_merge_blocks`` to build a block id."""

	@staticmethod
	def scrub(text):
		return text.lower().replace(" ", "_").replace("-", "_")


def _load():
	"""Exec the module's constants + ``_merge_blocks`` without importing frappe."""
	tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
	wanted = []
	for node in tree.body:
		if isinstance(node, ast.Assign):
			wanted.append(node)
		elif isinstance(node, ast.FunctionDef) and node.name == "_merge_blocks":
			wanted.append(node)
	namespace = {"frappe": _FrappeStub}
	exec(compile(ast.Module(body=wanted, type_ignores=[]), str(MODULE_PATH), "exec"), namespace)
	return namespace


class TestCustomHtmlBlockConstants(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.ns = _load()

	def test_kpi_cockpit_is_a_seeded_block(self):
		names = [name for name, _prefix in self.ns["BLOCKS"]]
		self.assertEqual(self.ns["KPI_COCKPIT"], "KPI Cockpit")
		self.assertIn(self.ns["KPI_COCKPIT"], names)

	def test_department_dashboards_cover_the_kpi_departments(self):
		expected = {"Finance", "Sales", "Operations", "Design", "Production", "Marketing", "Product", "Executive"}
		got = {ws.replace(" Dashboard", "") for ws in self.ns["KPI_DEPARTMENT_DASHBOARDS"]}
		self.assertEqual(got, expected)
		for ws in self.ns["KPI_DEPARTMENT_DASHBOARDS"]:
			self.assertTrue(ws.endswith(" Dashboard"), ws)

	def test_kpi_cockpit_not_in_home_blocks(self):
		# KPI Cockpit reaches Home via the dashboard loop, not HOME_BLOCKS.
		self.assertNotIn(self.ns["KPI_COCKPIT"], self.ns["HOME_BLOCKS"])


class TestMergeBlocks(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.merge = staticmethod(_load()["_merge_blocks"])

	def test_appends_when_absent(self):
		blocks, changed = self.merge([], ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 1)
		self.assertEqual(blocks[0]["type"], "custom_block")
		self.assertEqual(blocks[0]["data"]["custom_block_name"], "KPI Cockpit")
		self.assertEqual(blocks[0]["id"], "ee_chb_kpi_cockpit")

	def test_idempotent_when_already_present(self):
		existing = [{"type": "custom_block", "data": {"custom_block_name": "KPI Cockpit", "col": 12}}]
		blocks, changed = self.merge(existing, ["KPI Cockpit"])
		self.assertFalse(changed)
		self.assertEqual(len(blocks), 1)

	def test_preserves_existing_blocks(self):
		existing = [{"type": "header", "data": {"text": "Hi", "col": 12}}]
		blocks, changed = self.merge(existing, ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 2)
		self.assertEqual(blocks[0]["type"], "header")
		self.assertEqual(blocks[1]["data"]["custom_block_name"], "KPI Cockpit")

	def test_handles_non_list_content(self):
		blocks, changed = self.merge(None, ["KPI Cockpit"])
		self.assertTrue(changed)
		self.assertEqual(len(blocks), 1)


if __name__ == "__main__":
	unittest.main()
