"""Bench-free tests for the project a row added with Update Items takes (ER-2026-1333210).

ERPNext's *Update Items* inserts a new ``Purchase Order Item`` by itself, before it saves the
order, so the order-level ``cascade_project_to_items`` never sees it and the mandatory Project
check refuses the row. ``procurement_line_project.fill_new_line_project`` is the ``before_insert``
hook on the row that fills it. These tests run the rule and the hook on a minimal ``frappe``
stand-in, and hold ``hooks.py`` and ``ci.yml`` to the wiring.

What it cannot see: that Frappe really fires ``before_insert`` for a child inserted alone and
ahead of the mandatory check. That was read in frappe v16.56.0 (``Document.insert``) and is
written down in the module's docstring, but only a bench proves it.

The stand-in is installed in ``setUpModule`` (execution time, not import), so it never fools the
bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_po_line_project -v
"""

import ast
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

MODULE = "erpnext_enhancements.procurement_line_project"
HOOK = f"{MODULE}.fill_new_line_project"

mod = None
FRAPPE = None


class _Thrown(Exception):
	"""What the stand-in ``frappe.throw`` raises."""


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.ORDERS = {}
	frappe.LINES = {}
	frappe.READ_FAILS = False
	frappe.reads = []
	frappe.logged = []
	frappe.thrown = []

	def get_value(doctype, name, fieldname):
		frappe.reads.append(("get_value", doctype, name))
		if frappe.READ_FAILS:
			raise RuntimeError("database gone")
		return frappe.ORDERS.get(name)

	def get_all(doctype, filters=None, pluck=None, **kw):
		frappe.reads.append(("get_all", doctype, (filters or {}).get("parent")))
		if frappe.READ_FAILS:
			raise RuntimeError("database gone")
		assert doctype == "Purchase Order Item" and pluck == "project"
		return list(frappe.LINES.get((filters or {}).get("parent"), []))

	def throw(message, title=None, **kw):
		frappe.thrown.append((message, title))
		raise _Thrown(message)

	def log_error(title=None, message=None, **kw):
		frappe.logged.append(title)

	frappe.db = types.SimpleNamespace(get_value=get_value)
	frappe.get_all = get_all
	frappe.throw = throw
	frappe.log_error = log_error
	sys.modules["frappe"] = frappe
	return frappe


def setUpModule():
	global mod, FRAPPE
	FRAPPE = _install_frappe_stub()
	sys.modules.pop(MODULE, None)
	import importlib

	mod = importlib.import_module(MODULE)


def tearDownModule():
	sys.modules.pop(MODULE, None)
	sys.modules.pop("frappe", None)


class _Flags(dict):
	pass


class _Line:
	"""A ``Purchase Order Item`` as ERPNext builds it for Update Items: parent set, project not."""

	def __init__(self, **values):
		self.__dict__.update(
			{"parenttype": "Purchase Order", "parent": "PUR-ORD-1", "project": None, "flags": _Flags()}
		)
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _reset(order_project=None, lines=(), order="PUR-ORD-1", exists=True):
	FRAPPE.ORDERS.clear()
	FRAPPE.LINES.clear()
	FRAPPE.READ_FAILS = False
	FRAPPE.reads.clear()
	FRAPPE.logged.clear()
	FRAPPE.thrown.clear()
	if exists:
		FRAPPE.ORDERS[order] = order_project
	FRAPPE.LINES[order] = list(lines)


class TestRule(unittest.TestCase):
	"""``project_for_new_line``: the whole rule, without a database."""

	def test_header_wins(self):
		self.assertEqual(mod.project_for_new_line("PRJ-1", ["PRJ-2"]), ("PRJ-1", []))

	def test_header_is_stripped(self):
		self.assertEqual(mod.project_for_new_line("  PRJ-1  ", []), ("PRJ-1", []))

	def test_blank_header_and_one_shared_line_project(self):
		self.assertEqual(mod.project_for_new_line("", ["PRJ-2", "PRJ-2", None, " "]), ("PRJ-2", []))

	def test_blank_header_and_two_line_projects_is_ambiguous(self):
		self.assertEqual(
			mod.project_for_new_line(None, ["PRJ-2", "PRJ-3", "PRJ-2"]), ("", ["PRJ-2", "PRJ-3"])
		)

	def test_nothing_to_go_on(self):
		self.assertEqual(mod.project_for_new_line("   ", [None, ""]), ("", []))
		self.assertEqual(mod.project_for_new_line(None, None), ("", []))


class TestHook(unittest.TestCase):
	def test_new_line_takes_the_header_project(self):
		_reset(order_project="PRJ-1", lines=["PRJ-1", "PRJ-1"])
		row = _Line()
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-1")

	def test_header_project_beats_the_lines_projects(self):
		_reset(order_project="PRJ-1", lines=["PRJ-2", "PRJ-3"])
		row = _Line()
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-1")

	def test_blank_header_takes_the_one_project_the_lines_share(self):
		_reset(order_project=None, lines=["PRJ-2", "PRJ-2", "PRJ-2"])
		row = _Line()
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-2")

	def test_blank_header_and_two_projects_is_refused_in_words(self):
		_reset(order_project="", lines=["PRJ-2", "PRJ-3"])
		row = _Line()
		with self.assertRaises(_Thrown):
			mod.fill_new_line_project(row)
		self.assertIsNone(row.project)  # nothing guessed
		message, title = FRAPPE.thrown[0]
		for needle in ("PUR-ORD-1", "PRJ-2", "PRJ-3", "own Purchase Order", "amend"):
			self.assertIn(needle, message)
		self.assertNotIn("Value missing", message)
		self.assertTrue(title)

	def test_a_project_already_set_is_kept_and_nothing_is_read(self):
		_reset(order_project="PRJ-1", lines=["PRJ-2"])
		row = _Line(project="PRJ-9")
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-9")
		self.assertEqual(FRAPPE.reads, [])

	def test_a_project_already_set_is_kept_even_when_the_order_is_ambiguous(self):
		_reset(order_project=None, lines=["PRJ-2", "PRJ-3"])
		row = _Line(project="PRJ-9")
		mod.fill_new_line_project(row)  # does not raise
		self.assertEqual(row.project, "PRJ-9")

	def test_whitespace_is_not_a_project(self):
		_reset(order_project="PRJ-1")
		row = _Line(project="   ")
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-1")

	def test_an_order_that_cannot_be_read_leaves_the_row_for_the_normal_mandatory_error(self):
		_reset(order_project="PRJ-1", lines=["PRJ-1"])
		FRAPPE.READ_FAILS = True
		row = _Line()
		mod.fill_new_line_project(row)  # no exception of its own
		self.assertIsNone(row.project)
		self.assertEqual(len(FRAPPE.logged), 1)

	def test_a_parent_that_does_not_exist_leaves_the_row_alone(self):
		_reset(exists=False, lines=[])
		row = _Line()
		mod.fill_new_line_project(row)
		self.assertIsNone(row.project)
		self.assertEqual(FRAPPE.thrown, [])

	def test_no_header_and_no_line_projects_leaves_the_mandatory_error_to_stand(self):
		_reset(order_project=None, lines=[None, ""])
		row = _Line()
		mod.fill_new_line_project(row)
		self.assertIsNone(row.project)
		self.assertEqual(FRAPPE.thrown, [])

	def test_other_parents_are_none_of_its_business(self):
		_reset(order_project="PRJ-1")
		for row in (
			_Line(parenttype="Sales Order"),
			_Line(parenttype="Purchase Receipt"),
			_Line(parenttype=None),
			_Line(parent=None),
			_Line(parent="  "),
		):
			mod.fill_new_line_project(row)
			self.assertIsNone(row.project)
		self.assertEqual(FRAPPE.reads, [])

	def test_ignore_mandatory_is_not_refused_for_ambiguity(self):
		_reset(order_project=None, lines=["PRJ-2", "PRJ-3"])
		row = _Line()
		row.flags["ignore_mandatory"] = True
		mod.fill_new_line_project(row)  # the mandatory check is off, so is this refusal
		self.assertIsNone(row.project)

	def test_ignore_mandatory_still_fills_when_the_answer_is_clear(self):
		_reset(order_project="PRJ-1")
		row = _Line()
		row.flags["ignore_mandatory"] = True
		mod.fill_new_line_project(row)
		self.assertEqual(row.project, "PRJ-1")

	def test_a_row_without_flags_does_not_crash(self):
		_reset(order_project=None, lines=["PRJ-2", "PRJ-3"])
		row = _Line()
		del row.flags
		with self.assertRaises(_Thrown):
			mod.fill_new_line_project(row)

	def test_called_the_way_frappe_calls_it_with_a_method_name(self):
		_reset(order_project="PRJ-1")
		row = _Line()
		mod.fill_new_line_project(row, "before_insert")
		self.assertEqual(row.project, "PRJ-1")


class TestWiring(unittest.TestCase):
	def _doc_events(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
				return node.value
		self.fail("hooks.py has no doc_events")

	def test_doc_events_has_no_duplicate_doctype_keys(self):
		# A repeated key in a dict literal is not an error: the later one silently wins and the
		# earlier hooks vanish. Two branches that each add "Purchase Order Item" would do it.
		keys = [k.value for k in self._doc_events().keys if isinstance(k, ast.Constant)]
		dupes = sorted({k for k in keys if keys.count(k) > 1})
		self.assertEqual(dupes, [])

	def test_the_hook_is_registered_on_the_row_before_insert(self):
		events = self._doc_events()
		for key, value in zip(events.keys, events.values, strict=True):
			if isinstance(key, ast.Constant) and key.value == "Purchase Order Item":
				for hook_key, hook_value in zip(value.keys, value.values, strict=True):
					if hook_key.value == "before_insert":
						names = (
							[e.value for e in hook_value.elts]
							if isinstance(hook_value, (ast.List, ast.Tuple))
							else [hook_value.value]
						)
						self.assertIn(HOOK, names)
						return
		self.fail("Purchase Order Item.before_insert is not registered in hooks.py")

	def test_the_registered_path_resolves_to_a_function(self):
		self.assertTrue(callable(getattr(mod, HOOK.rsplit(".", 1)[1])))

	def test_it_runs_in_ci(self):
		text = CI.read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.tests.test_po_line_project", text)


if __name__ == "__main__":
	unittest.main()
