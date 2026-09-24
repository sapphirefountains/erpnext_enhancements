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

STATE = {"existing": [], "get_all_calls": 0, "today": "2026-10-01", "names": {}}
guard = None
review = None


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

	def exists(doctype, name=None):
		assert doctype == "Item", doctype
		return name in STATE["existing"]

	def get_value(doctype, filters, fieldname):
		assert doctype == "Item" and fieldname == "name", (doctype, fieldname)
		return STATE["names"].get(filters.get("item_name"))

	frappe.throw = throw
	frappe.get_all = get_all
	frappe._ = lambda s: s
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(exists=exists, get_value=get_value)
	frappe.flags = types.SimpleNamespace()
	frappe.local = types.SimpleNamespace(request=object())
	utils = types.ModuleType("frappe.utils")
	utils.nowdate = lambda: STATE["today"]
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global guard, review
	_install_frappe_stub()
	for mod_name in (
		"erpnext_enhancements.inventory_enhancements.item_naming_guard",
		"erpnext_enhancements.accounting_intake.review",
		"erpnext_enhancements.accounting_intake.audit",
	):
		sys.modules.pop(mod_name, None)
	from erpnext_enhancements.accounting_intake import review as intake_review
	from erpnext_enhancements.inventory_enhancements import item_naming_guard as mod

	guard = mod
	review = intake_review


class _Item:
	"""An Item doc double: is_new(), get(), flags, name."""

	def __init__(self, item_code, item_name, new=True, variant_of=None, **flags):
		self.name = item_code
		self._data = {
			"item_code": item_code,
			"item_name": item_name,
			"name": item_code,
			"variant_of": variant_of,
		}
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
		STATE["today"] = "2026-10-01"
		STATE["names"] = {}

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

	def test_a_code_spelled_from_a_good_name_is_told_to_fix_the_code(self):
		"""Review finding: the name is in schema order and the code was invented from it. The
		comparison is punctuation-blind, so this is "name is the code" -- and the remedy is the
		code, which the message has to say, or it asks for a name the user already wrote."""
		msg = self.assertRefused(_Item("SKIMMER-HAYWARD-1084FVE", "SKIMMER, HAYWARD, 1084FVE"))
		self.assertIn("the code is what needs changing", msg)
		self.assertIn("vendor's part number", msg)

	def test_a_blank_name(self):
		"""ERPNext copies the code into a blank name before this runs; either way it is refused."""
		self.assertRefused(_Item("22-1044", ""))
		self.assertRefused(_Item("22-1044", None))

	def test_the_existing_codes_are_read_once(self):
		self.assertRefused(_Item("806020", "806020"))
		self.assertEqual(STATE["get_all_calls"], 1)

	def test_the_message_says_nothing_else_is_enforced(self):
		msg = self.assertRefused(_Item("22-1044", "22-1044"))
		self.assertIn("Only two naming problems", msg)
		self.assertIn("Once it is saved", msg, "an unsaved Item form has no Naming menu yet")


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

	def test_a_variant_is_skipped(self):
		"""ERPNext derives a variant's code and name from its template; a manufacturer variant
		copies no name at all, so its name becomes its code. Nobody at the screen chose either."""
		self.assertAllowed(_Item(*self.BAD, variant_of="806-020"))
		self.assertEqual(STATE["get_all_calls"], 0)


class GoLiveTest(_Base):
	"""The refusal starts on POL-0602's effective date, the date the new-items KPI counts from."""

	BAD = ("806020", "806020")

	def test_the_go_live_is_the_policy_date(self):
		self.assertEqual(guard.rules.NAMING_GO_LIVE, "2026-10-01")

	def test_the_day_before_it_saves(self):
		STATE["today"] = "2026-09-30"
		self.assertAllowed(_Item(*self.BAD))
		self.assertEqual(STATE["get_all_calls"], 0)

	def test_on_the_day_and_after_it_refuses(self):
		for today in ("2026-10-01", "2026-10-02", "2027-01-01"):
			with self.subTest(today=today):
				STATE["today"] = today
				self.assertRefused(_Item(*self.BAD))

	def test_in_force_takes_an_explicit_date(self):
		self.assertFalse(guard.in_force("2026-09-24"))
		self.assertTrue(guard.in_force("2026-10-01"))


class _Row:
	"""A Document Intake Line double."""

	def __init__(self, idx, name, code=None):
		self.idx = idx
		self.proposed_item_name = name
		self.description = name
		self._data = {"proposed_item_code": code}

	def get(self, key, default=None):
		return self._data.get(key, default)


class IntakeNamingProblemsTest(_Base):
	"""Document Intake's Approve Items keeps the guard, so it has to be usable under it.

	Before v1.532.0 shipped, it built every new Item with the proposed name as its code,
	which the guard refuses: the reviewers' blocker. The Stock Manager now enters a Proposed
	Item Code, and ``_naming_problems`` checks every line with the guard's own rule before the
	first insert, so a refusal names the line and the field instead of an Item form nobody
	opened.
	"""

	NAME = 'COUPLING, SOC, PVC, 2", SCH40'

	def test_a_line_with_no_code_is_told_to_enter_one(self):
		problems = review._naming_problems([_Row(3, self.NAME)])
		self.assertEqual(len(problems), 1)
		self.assertIn("Line 3", problems[0])
		self.assertIn("Proposed Item Code", problems[0])

	def test_a_line_with_a_real_code_passes(self):
		self.assertEqual(review._naming_problems([_Row(1, self.NAME, code="429-020")]), [])

	def test_a_near_duplicate_code_is_refused_with_the_guards_words(self):
		problems = review._naming_problems([_Row(2, self.NAME, code="806020")])
		self.assertEqual(len(problems), 1)
		self.assertIn("Line 2", problems[0])
		self.assertIn("<b>806-020</b>", problems[0])

	def test_codes_earlier_in_the_batch_count(self):
		problems = review._naming_problems(
			[_Row(1, self.NAME, code="429-020"), _Row(2, 'TEE, SOC, PVC, 2", SCH40', code="429 020")]
		)
		self.assertEqual(len(problems), 1)
		self.assertIn("Line 2", problems[0])

	def test_a_line_that_names_an_existing_item_is_not_checked(self):
		"""It links to that Item instead of creating one, as it always did."""
		STATE["names"] = {self.NAME: "429-020"}
		self.assertEqual(review._naming_problems([_Row(1, self.NAME)]), [])
		self.assertEqual(review._existing_item(self.NAME, self.NAME), "429-020")

	def test_the_line_name_is_escaped(self):
		problems = review._naming_problems([_Row(1, "<b>x</b>")])
		self.assertNotIn("<b>x</b>", problems[0])

	def test_silent_before_the_go_live(self):
		STATE["today"] = "2026-09-30"
		self.assertEqual(review._naming_problems([_Row(1, self.NAME)]), [])

	def test_the_code_falls_back_to_the_name(self):
		self.assertEqual(review._proposed_code_and_name(_Row(1, self.NAME)), (self.NAME, self.NAME))
		self.assertEqual(
			review._proposed_code_and_name(_Row(1, self.NAME, code="  429-020 ")), ("429-020", self.NAME)
		)


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
