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

STATE = {
	"existing": [],
	"get_all_calls": 0,
	"today": "2026-10-01",
	"names": {},
	"inserted": [],
	"intake": None,
}
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
		if isinstance(name, str) and name == doctype:
			# v16's Single shortcut: `exists(dt, dn)` returns dn unchecked when dn == dt.
			return name
		key = name.get("name") if isinstance(name, dict) else name
		return key if key in STATE["existing"] else None

	class _NewItem:
		def __init__(self, data):
			self.data = data
			self.name = data["item_code"]
			self.flags = types.SimpleNamespace()

		def insert(self, ignore_permissions=False):
			STATE["inserted"].append(self.data)
			STATE["existing"].append(self.name)

	def get_doc(arg, name=None):
		if isinstance(arg, dict):
			assert arg["doctype"] == "Item", arg
			return _NewItem(arg)
		assert arg == "Document Intake", arg
		return STATE["intake"]

	def get_value(doctype, filters, fieldname):
		assert doctype == "Item" and fieldname == "name", (doctype, fieldname)
		return STATE["names"].get(filters.get("item_name"))

	frappe.throw = throw
	frappe.get_all = get_all
	frappe._ = lambda s: s
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_doc = get_doc
	frappe.get_roles = lambda *a: ["Stock Manager"]
	frappe.session = types.SimpleNamespace(user="stock.manager@example.com")
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
	review.log_intake = lambda *a, **k: None


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
		STATE["inserted"] = []
		STATE["intake"] = None

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
		self.new_item_proposed = 1
		self.item_review_status = "Approved"
		self.matched_item = None
		self.proposed_item_group = "PVC Fittings"
		self.proposed_uom = "Unit"
		self.is_stock_item = 1

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

	def test_the_same_code_on_two_lines_with_different_names_is_refused(self):
		"""Framework review: the second insert would link to the first line's new Item, silently."""
		problems = review._naming_problems(
			[_Row(1, self.NAME, code="ACME-1"), _Row(2, 'CAP, SOC, PVC, 2", SCH40', code="ACME-1")]
		)
		self.assertEqual(len(problems), 1)
		self.assertIn("Line 2", problems[0])
		self.assertIn("same Proposed Item Code as line 1", problems[0])

	def test_the_same_part_on_two_lines_is_one_item(self):
		self.assertEqual(
			review._naming_problems([_Row(1, self.NAME, code="ACME-1"), _Row(4, self.NAME, code="ACME-1")]),
			[],
		)

	def test_a_name_equal_to_the_doctype_is_not_an_existing_item(self):
		"""v16 returns `exists("Item", "Item")` unchecked; the "Item" fallback name must not link to it."""
		self.assertIsNone(review._existing_item("Item", "Item"))

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


class _Intake:
	def __init__(self, rows):
		self.line_items = rows
		self.status = "Needs Item Review"
		self.item_reviewed_by = None
		self.saved = False

	def save(self, ignore_permissions=False):
		self.saved = True


class ApproveItemsTest(_Base):
	"""The insert itself: the proposed code reaches the Item, and a refusal comes before any insert.

	Fidelity review: without these, reverting ``item_code`` to the name, or moving the check
	inside the insert loop, would pass every other test here.
	"""

	NAME = 'COUPLING, SOC, PVC, 2", SCH40'

	def test_the_item_gets_the_proposed_code_and_the_name(self):
		self.assertEqual(review._create_item(_Row(1, self.NAME, code="429-020")), "429-020")
		self.assertEqual(len(STATE["inserted"]), 1)
		self.assertEqual(STATE["inserted"][0]["item_code"], "429-020")
		self.assertEqual(STATE["inserted"][0]["item_name"], self.NAME)

	def test_one_bad_line_refuses_the_batch_before_any_insert(self):
		STATE["intake"] = _Intake([_Row(1, self.NAME, code="429-020"), _Row(2, 'TEE, SOC, PVC, 2", SCH40')])
		with self.assertRaises(StubThrow) as ctx:
			review.approve_items("DI-0001")
		self.assertEqual(STATE["inserted"], [])
		self.assertFalse(STATE["intake"].saved)
		self.assertIn("Line 2", ctx.exception.msg)
		self.assertIn("Nothing was created", ctx.exception.msg)

	def test_a_clean_batch_creates_each_item_and_advances(self):
		rows = [_Row(1, self.NAME, code="429-020"), _Row(2, 'TEE, SOC, PVC, 2", SCH40', code="401-020")]
		STATE["intake"] = _Intake(rows)
		out = review.approve_items("DI-0001")
		self.assertEqual(out["created"], 2)
		self.assertEqual([d["item_code"] for d in STATE["inserted"]], ["429-020", "401-020"])
		self.assertEqual([r.matched_item for r in rows], ["429-020", "401-020"])
		self.assertEqual(STATE["intake"].status, "Needs Review")


if __name__ == "__main__":
	unittest.main()
