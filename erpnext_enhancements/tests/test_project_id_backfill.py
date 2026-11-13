# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project ID kept true (v1.816.4): the backfill patch and the three Project hooks. Bench-free.

``Project.custom_project_id`` mirrors the Project's name for the report "Active Projects w/ TL and PM".
On production 451 of 671 Projects had it blank. This pins:

* the patch -- selects by the RULE (NULL or not byte-for-byte the name, so a case or trailing-space
  difference counts, which a plain ``<>`` under MariaDB's case-insensitive PAD SPACE collation would
  miss), leaves matching rows alone, never touches ``modified``, logs counts and never names, is safe
  twice, and cannot raise (a patch that raises aborts ``bench migrate``, which is the deploy);
* the hooks -- ``validate``, ``after_insert`` and ``after_rename`` write the name, skip quietly when the
  column is absent, use the rename's ``new`` over the document's name, and cannot raise;
* the wiring -- hooks.py, patches.txt (post_model_sync) and the patches README.

No MariaDB here, so the SQL's own semantics are not executed; the stub applies the byte-exact rule the
BINARY comparison implements, and the text of the statement is asserted separately.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_project_id_backfill
"""

import ast
import contextlib
import importlib.util
import io
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

STATE = {}
patch = None
hooks = None
_saved = {}


def _reset(rows=None, column=True):
	STATE.clear()
	STATE.update(
		{
			"rows": dict(rows or {}),  # name -> custom_project_id (None = NULL)
			"column": column,
			"has_column_raises": False,
			"sql_log": [],
			"errors": [],
			"fail_sql": False,
		}
	)


def _differs(name, value):
	"""The rule BINARY implements: NULL, or not equal byte for byte."""
	return value is None or value != name


def _norm(text):
	return " ".join(text.split())


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def has_column(doctype, column):
		assert not doctype.startswith("tab"), "has_column takes a DocType, not a table name"
		if STATE["has_column_raises"]:
			raise RuntimeError("TableMissingError")
		return STATE["column"] and (doctype, column) == ("Project", "custom_project_id")

	def sql(query, values=None, *args, **kwargs):
		text = _norm(query)
		STATE["sql_log"].append((text, values))
		if STATE["fail_sql"]:
			raise RuntimeError("database gone")
		if text == _norm(patch.COUNT_SQL):
			return [(sum(1 for n, v in STATE["rows"].items() if _differs(n, v)),)]
		if text == _norm(patch.UPDATE_SQL):
			for n, v in list(STATE["rows"].items()):
				if _differs(n, v):
					STATE["rows"][n] = n
			return []
		if text.startswith("UPDATE `tabProject` SET `custom_project_id` = %s WHERE `name` = %s"):
			assert values[0] == values[1], values
			STATE["rows"][values[1]] = values[0]
			return []
		raise AssertionError(query)

	frappe.db = types.SimpleNamespace(has_column=has_column, sql=sql)
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or a)
	frappe.get_traceback = lambda: "traceback"
	_saved["frappe"] = sys.modules.get("frappe")
	sys.modules["frappe"] = frappe


def _load(name, path):
	spec = importlib.util.spec_from_file_location("_pid_test_" + name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def setUpModule():
	global patch, hooks
	_install_frappe_stub()
	# Both files are loaded by path: importing them as package members would run the
	# project_enhancements package __init__, which pulls half the app and a real frappe.
	patch = _load("backfill_project_id", APP / "patches" / "backfill_project_id.py")
	hooks = _load("project_id", APP / "project_enhancements" / "project_id.py")


def tearDownModule():
	if _saved.get("frappe") is None:
		sys.modules.pop("frappe", None)
	else:
		sys.modules["frappe"] = _saved["frappe"]


def _run_patch():
	out = io.StringIO()
	with contextlib.redirect_stdout(out):
		patch.execute()
	return out.getvalue()


class Doc:
	def __init__(self, name, value=None, set_raises=False):
		self.name = name
		self.custom_project_id = value
		self._set_raises = set_raises

	def set(self, key, value):
		if self._set_raises:
			raise RuntimeError("boom")
		setattr(self, key, value)


MIXED = {
	"PRJ-00001": None,  # blank, the 451 on production
	"PRJ-00002": "",  # empty string
	"PRJ-00003": "PRJ-00003",  # already right
	"PRJ-00004": "PRJ-OLD",  # stale after a rename
	"PRJ-00005": "prj-00005",  # case only: a plain <> would call it equal
	"PRJ-00006": "PRJ-00006 ",  # trailing space only: PAD SPACE would call it equal
}


class PatchSelectsByTheRuleTest(unittest.TestCase):
	def setUp(self):
		_reset(MIXED)

	def test_every_row_that_differs_is_corrected(self):
		_run_patch()
		self.assertEqual(STATE["rows"], {n: n for n in MIXED})

	def test_the_count_is_exactly_the_rows_that_differ(self):
		out = _run_patch()
		self.assertIn("set the Project ID on 5 of 5 Projects", out)

	def test_a_matching_row_is_not_in_the_selection(self):
		self.assertFalse(_differs("PRJ-00003", "PRJ-00003"))
		_run_patch()
		self.assertEqual(STATE["rows"]["PRJ-00003"], "PRJ-00003")

	def test_safe_twice_the_second_run_writes_nothing(self):
		_run_patch()
		STATE["sql_log"].clear()
		out = _run_patch()
		self.assertIn("nothing to do", out)
		self.assertFalse([q for q, _ in STATE["sql_log"] if q.startswith("UPDATE")])
		self.assertEqual(STATE["rows"], {n: n for n in MIXED})

	def test_nothing_to_do_when_all_rows_match(self):
		_reset({"PRJ-1": "PRJ-1", "PRJ-2": "PRJ-2"})
		out = _run_patch()
		self.assertIn("nothing to do", out)
		self.assertFalse([q for q, _ in STATE["sql_log"] if q.startswith("UPDATE")])

	def test_output_names_no_project(self):
		out = _run_patch()
		self.assertNotRegex(out, r"PRJ-")

	def test_the_statements_compare_bytes_and_never_touch_modified(self):
		for sql in (patch.COUNT_SQL, patch.UPDATE_SQL):
			self.assertIn("BINARY `custom_project_id` <> BINARY `name`", sql)
			self.assertIn("`custom_project_id` IS NULL", sql)
			self.assertNotIn("modified", sql)
			# every comparison of the two columns is wrapped in BINARY
			self.assertEqual(len(re.findall(r"<>", sql)), len(re.findall(r"BINARY `custom_project_id` <>", sql)))
		self.assertTrue(patch.UPDATE_SQL.startswith("UPDATE `tabProject` SET `custom_project_id` = `name` WHERE "))


class PatchCannotRaiseTest(unittest.TestCase):
	def test_absent_column_does_nothing(self):
		_reset(MIXED, column=False)
		out = _run_patch()
		self.assertIn("not on this site", out)
		self.assertEqual(STATE["sql_log"], [])
		self.assertEqual(STATE["errors"], [])

	def test_has_column_raising_is_treated_as_absent(self):
		_reset(MIXED)
		STATE["has_column_raises"] = True
		_run_patch()
		self.assertEqual(STATE["sql_log"], [])
		self.assertEqual(STATE["errors"], [])

	def test_a_failing_query_logs_once_and_returns(self):
		_reset(MIXED)
		STATE["fail_sql"] = True
		_run_patch()  # must not raise
		self.assertEqual(STATE["errors"], ["backfill_project_id patch failed"])


class HooksTest(unittest.TestCase):
	def setUp(self):
		_reset({"PRJ-9": None})

	def test_validate_fills_blank_stale_and_case_or_space_differences(self):
		for stored in (None, "", "OLD", "prj-9", "PRJ-9 "):
			doc = Doc("PRJ-9", stored)
			hooks.ensure_project_id(doc, "validate")
			self.assertEqual(doc.custom_project_id, "PRJ-9", repr(stored))

	def test_validate_leaves_a_matching_value_and_writes_no_sql(self):
		doc = Doc("PRJ-9", "PRJ-9")
		hooks.ensure_project_id(doc, "validate")
		self.assertEqual(STATE["sql_log"], [])

	def test_validate_skips_a_document_with_no_name(self):
		doc = Doc(None, None)
		hooks.ensure_project_id(doc, "validate")
		self.assertIsNone(doc.custom_project_id)

	def test_validate_without_the_column_does_nothing(self):
		_reset(column=False)
		doc = Doc("PRJ-9", None)
		hooks.ensure_project_id(doc, "validate")
		self.assertIsNone(doc.custom_project_id)

	def test_validate_survives_a_document_that_raises(self):
		hooks.ensure_project_id(Doc("PRJ-9", None, set_raises=True), "validate")
		self.assertEqual(STATE["errors"], ["Project ID: validate hook failed"])

	def test_after_insert_writes_the_row_when_validate_was_skipped(self):
		doc = Doc("PRJ-9", None)
		hooks.ensure_on_insert(doc, "after_insert")
		self.assertEqual(STATE["rows"]["PRJ-9"], "PRJ-9")
		self.assertEqual(doc.custom_project_id, "PRJ-9")
		query, values = STATE["sql_log"][-1]
		self.assertNotIn("modified", query)
		self.assertEqual(values, ("PRJ-9", "PRJ-9"))

	def test_after_insert_is_a_no_op_when_validate_already_set_it(self):
		hooks.ensure_on_insert(Doc("PRJ-9", "PRJ-9"), "after_insert")
		self.assertEqual(STATE["sql_log"], [])

	def test_after_insert_cannot_raise(self):
		STATE["fail_sql"] = True
		hooks.ensure_on_insert(Doc("PRJ-9", None), "after_insert")
		self.assertEqual(STATE["errors"], ["Project ID: after_insert hook failed"])

	def test_after_rename_uses_the_new_name_not_the_documents(self):
		_reset({"PRJ-NEW": "PRJ-OLD"})
		doc = Doc("PRJ-OLD", "PRJ-OLD")  # frappe may hand over the document as it was
		hooks.follow_rename(doc, "after_rename", "PRJ-OLD", "PRJ-NEW", False)
		self.assertEqual(STATE["rows"]["PRJ-NEW"], "PRJ-NEW")
		self.assertEqual(doc.custom_project_id, "PRJ-NEW")
		self.assertNotIn("modified", STATE["sql_log"][-1][0])

	def test_after_rename_accepts_keywords_and_falls_back_to_the_name(self):
		_reset({"PRJ-7": "x"})
		hooks.follow_rename(Doc("PRJ-7", "x"), "after_rename", old="PRJ-6", new=None, merge=True)
		self.assertEqual(STATE["rows"]["PRJ-7"], "PRJ-7")

	def test_after_rename_without_the_column_does_nothing(self):
		_reset(column=False)
		hooks.follow_rename(Doc("PRJ-7", None), "after_rename", "a", "b", False)
		self.assertEqual(STATE["sql_log"], [])

	def test_after_rename_cannot_raise(self):
		STATE["fail_sql"] = True
		hooks.follow_rename(Doc("PRJ-7", None), "after_rename", "a", "b", False)
		self.assertEqual(STATE["errors"], ["Project ID: after_rename hook failed"])


class WiringTest(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
				cls.project = ast.literal_eval(node.value)["Project"]

	def _handlers(self, event):
		value = self.project.get(event, [])
		return [value] if isinstance(value, str) else list(value)

	def test_the_three_hooks_are_registered_on_project(self):
		base = "erpnext_enhancements.project_enhancements.project_id."
		self.assertIn(base + "ensure_project_id", self._handlers("validate"))
		self.assertIn(base + "ensure_on_insert", self._handlers("after_insert"))
		self.assertIn(base + "follow_rename", self._handlers("after_rename"))

	def test_the_existing_project_hooks_are_still_there(self):
		self.assertIn("erpnext_enhancements.process_steps.announce_seeded_steps", self._handlers("after_insert"))
		self.assertIn(
			"erpnext_enhancements.project_enhancements.budget_rollup.on_project_validate",
			self._handlers("validate"),
		)

	def test_the_patch_is_registered_post_model_sync_once(self):
		text = (APP / "patches.txt").read_text(encoding="utf-8")
		pre, post = text.split("[post_model_sync]", 1)
		self.assertEqual(post.count("erpnext_enhancements.patches.backfill_project_id\n"), 1)
		self.assertNotIn("backfill_project_id", pre)

	def test_the_patch_is_in_the_patches_readme(self):
		self.assertIn("`backfill_project_id`", (APP / "patches" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
