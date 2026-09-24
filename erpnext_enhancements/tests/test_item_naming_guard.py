# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The new-Item naming guard: what it refuses, and every door it leaves open. Bench-free.

``inventory_enhancements.item_naming_guard`` is the app's first ``Item`` doc_event (v1.532.0;
Nik, 2026-09-24, TASK-2026-02238). It refuses a NEW Item for two findings only. The half of
this worth a test is the skips, because every one of them is a place where refusing would be
wrong and none of them would look wrong in review: an existing Item, a Data Import, a migrate,
a background job such as the QuickBooks sync, and an in-request caller that generates the
name from the code. The judgement itself is ``item_naming_rules.blocking_findings``, covered
in ``test_item_naming_rules``; this suite runs the hook around it.

Installs its own ``frappe`` stub in ``setUpModule`` (execution time, not import time, so the
bench suites' ``import frappe`` guards are not fooled), which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_item_naming_guard
"""

import ast
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"

STATE = {"existing": [], "get_all_calls": 0}
guard = None


class StubThrow(Exception):
	def __init__(self, msg, title=None):
		super().__init__(msg)
		self.msg = msg
		self.title = title


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def throw(msg, exc=None, title=None, **kwargs):
		raise StubThrow(msg, title=title)

	def get_all(doctype, pluck=None, **kwargs):
		assert doctype == "Item" and pluck == "name", (doctype, pluck)
		STATE["get_all_calls"] += 1
		return list(STATE["existing"])

	frappe.throw = throw
	frappe.get_all = get_all
	frappe._ = lambda s: s
	frappe.flags = types.SimpleNamespace()
	frappe.local = types.SimpleNamespace(request=object())
	sys.modules["frappe"] = frappe


def setUpModule():
	global guard
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.inventory_enhancements.item_naming_guard", None)
	from erpnext_enhancements.inventory_enhancements import item_naming_guard as mod

	guard = mod


class _Item:
	"""An Item doc double: is_new(), get(), flags, name."""

	def __init__(self, item_code, item_name, new=True, **flags):
		self.name = item_code
		self._data = {"item_code": item_code, "item_name": item_name, "name": item_code}
		self._new = new
		self.flags = types.SimpleNamespace(**flags)

	def is_new(self):
		return self._new

	def get(self, key, default=None):
		return self._data.get(key, default)


class _Base(unittest.TestCase):
	def setUp(self):
		frappe = sys.modules["frappe"]
		frappe.flags = types.SimpleNamespace()
		frappe.local = types.SimpleNamespace(request=object())
		STATE["existing"] = ["806-020", "PDT-0009", "GMCB-1B-1"]
		STATE["get_all_calls"] = 0

	def assertRefused(self, doc):
		with self.assertRaises(StubThrow) as ctx:
			guard.validate_new_item(doc)
		self.assertEqual(ctx.exception.title, "Item naming")
		return ctx.exception.msg

	def assertAllowed(self, doc):
		guard.validate_new_item(doc)


class RefusesTheTwoFindingsTest(_Base):
	def test_a_punctuation_variant_of_an_existing_code(self):
		msg = self.assertRefused(_Item("806020", 'ELBOW, 90, SOC, PVC, 2" SCH80'))
		self.assertIn("<b>806-020</b>", msg)
		self.assertIn("806020", msg)

	def test_a_name_that_is_just_the_code(self):
		msg = self.assertRefused(_Item("22-1044", "22-1044"))
		self.assertIn("descriptive name", msg)

	def test_a_blank_name(self):
		"""ERPNext copies the code into a blank name before this runs; either way it is refused."""
		self.assertRefused(_Item("22-1044", ""))
		self.assertRefused(_Item("22-1044", None))

	def test_the_existing_codes_are_read_once(self):
		self.assertRefused(_Item("806020", "806020"))
		self.assertEqual(STATE["get_all_calls"], 1)

	def test_the_message_says_nothing_else_is_enforced(self):
		msg = self.assertRefused(_Item("22-1044", "22-1044"))
		self.assertIn("Only these two naming problems", msg)


class AdviceNeverRefusesTest(_Base):
	def test_a_clean_new_item_saves(self):
		self.assertAllowed(_Item("2622-015", 'VALVE, BALL, UTILITY, SOC, PVC, 1-1/2", EPDM'))

	def test_an_unapproved_category_saves(self):
		"""A STOP in the advisor, and the reason the decision stopped at two findings:
		TASK-2026-02215 still has rulings open."""
		self.assertAllowed(_Item("NEW-1", 'PLMB, FITTING, 1"'))

	def test_untidy_names_save(self):
		for name in ("Valve, ball", "WIDGET", "ELBOW,90", "ELBOW, 90 , PVC "):
			self.assertAllowed(_Item("NEW-2", name))


class SkipsTest(_Base):
	BAD = ("806020", "806020")  # both findings at once

	def test_an_existing_item_is_never_refused(self):
		self.assertAllowed(_Item(*self.BAD, new=False))
		self.assertEqual(STATE["get_all_calls"], 0, "an existing Item must not even read the corpus")

	def test_every_bulk_flag_skips(self):
		for flag in ("in_import", "in_migrate", "in_install", "in_patch", "in_test", "in_setup_wizard"):
			with self.subTest(flag=flag):
				sys.modules["frappe"].flags = types.SimpleNamespace(**{flag: True})
				self.assertAllowed(_Item(*self.BAD))
		self.assertEqual(STATE["get_all_calls"], 0)

	def test_outside_a_web_request_it_skips(self):
		"""The QuickBooks sync creates Items on the scheduler; nobody would read the refusal."""
		sys.modules["frappe"].local = types.SimpleNamespace()
		self.assertAllowed(_Item(*self.BAD))
		sys.modules["frappe"].local = types.SimpleNamespace(request=None)
		self.assertAllowed(_Item(*self.BAD))

	def test_the_caller_flag_skips(self):
		self.assertAllowed(_Item(*self.BAD, ignore_naming_guard=True))

	def test_a_falsy_flag_does_not_skip(self):
		sys.modules["frappe"].flags = types.SimpleNamespace(in_import=False, in_test=None)
		self.assertRefused(_Item(*self.BAD, ignore_naming_guard=False))


class MessageTest(_Base):
	def test_every_interpolated_value_is_escaped(self):
		code = '<img src=x onerror="alert(1)">'
		findings = [
			{"code": "duplicate_code_normalised", "matches": ['<script>"x"</script>']},
			{"code": "name_equals_code"},
		]
		msg = guard.refusal_message(code, findings)
		self.assertNotIn("<img", msg)
		self.assertNotIn("<script>", msg)
		self.assertIn("&lt;img", msg)
		self.assertIn("&lt;script&gt;", msg)


def _dict_node(tree, name):
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return node.value
	raise AssertionError(f"{name} not found in hooks.py")


def _get(dict_node, key):
	for k, v in zip(dict_node.keys, dict_node.values, strict=False):
		if isinstance(k, ast.Constant) and k.value == key:
			return v
	return None


class WiringTest(unittest.TestCase):
	def test_item_validate_is_wired_to_the_guard(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		item = _get(_dict_node(tree, "doc_events"), "Item")
		self.assertIsNotNone(item, "doc_events has no Item entry")
		validate = ast.literal_eval(_get(item, "validate"))
		handlers = validate if isinstance(validate, list) else [validate]
		self.assertIn(
			"erpnext_enhancements.inventory_enhancements.item_naming_guard.validate_new_item", handlers
		)


if __name__ == "__main__":
	unittest.main()
