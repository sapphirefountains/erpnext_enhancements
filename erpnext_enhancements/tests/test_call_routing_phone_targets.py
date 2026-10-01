# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``Phone Number`` routing target: a typed number, rung alongside everything else.

It exists because "ring these five phones at once" is the job, and on 2026-10-01 only 3 of
15 active Employee records had a ``cell_number`` — so a rule built from Employee targets
could not express it. A typed number compiles to the same ``number`` leg an Employee does,
which is why Triton needed no change; these tests pin the three places that could quietly
break that:

* **The compiler** — ``call_routing._compile_targets`` must turn the row into a dialable
  E.164 leg, and drop-and-explain one that is not, never pass an empty ``<Number>``.
* **The rule's validate** — throws on a non-number and on the business line itself. The
  second is the one nobody would think to test: dialing the inbound number from its own
  call loops the leg back into the IVR, which answers it, so every call would read as
  "answered" and nobody's phone would ring.
* **The option string** — controller, compiler and the Select's options must agree on
  ``"Phone Number"`` byte for byte, or the row compiles to "unknown target type" and is
  dropped with nothing but a payload warning to say so.

Stubs ``frappe`` in ``setUpModule`` (execution time, not import time, so it never fools the
bench-only suites' ``import frappe`` skip-guards). Its own CI step for that reason.
"""

from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path

_SAVED: dict[str, object] = {}
call_routing = None
rule_module = None

BUSINESS_LINE = "+18018372199"
TARGET_JSON = (
	Path(__file__).resolve().parent.parent
	/ "ai_governance"
	/ "doctype"
	/ "call_routing_target"
	/ "call_routing_target.json"
)


class _Thrown(Exception):
	pass


def _fake_frappe() -> types.ModuleType:
	frappe = types.ModuleType("frappe")
	frappe.flags = types.SimpleNamespace()
	frappe._ = lambda s: s

	def throw(msg, *a, **k):
		raise _Thrown(msg)

	frappe.throw = throw
	frappe.msgprint = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: "traceback"
	frappe.db = types.SimpleNamespace(
		get_single_value=lambda doctype, field: BUSINESS_LINE
		if (doctype, field) == ("Triton Settings", "primary_twilio_number")
		else None,
		get_singles_dict=lambda *a, **k: {},
		get_value=lambda *a, **k: None,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.get_system_timezone = lambda: "America/Denver"
	utils.getdate = lambda v=None: v
	utils.now_datetime = lambda: None
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		def get(self, key, default=None):
			return getattr(self, key, default)

	document.Document = Document
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	return frappe


def setUpModule() -> None:
	global call_routing, rule_module
	names = (
		"frappe",
		"frappe.utils",
		"frappe.model",
		"frappe.model.document",
		"erpnext_enhancements.api.telephony",
		"erpnext_enhancements.ai_governance.call_routing",
		"erpnext_enhancements.ai_governance.doctype.call_routing_rule.call_routing_rule",
	)
	for name in names:
		_SAVED[name] = sys.modules.get(name)
		# Drop any copy imported against a different stub, so these bind to this one.
		sys.modules.pop(name, None)

	_fake_frappe()

	# call_routing imports _softphone_identity from api.telephony, which imports the Twilio
	# SDK at module scope; stub the module, as test_call_routing_gateway_notify does.
	telephony = types.ModuleType("erpnext_enhancements.api.telephony")
	telephony._softphone_identity = lambda email: "erpnext_" + (email or "")
	telephony.LEGACY_SOFTPHONE_IDENTITY = "nikolas_erpnext"
	telephony._softphone_users = lambda settings: []
	sys.modules["erpnext_enhancements.api.telephony"] = telephony

	from erpnext_enhancements.ai_governance import call_routing as module
	from erpnext_enhancements.ai_governance.doctype.call_routing_rule import call_routing_rule

	call_routing = module
	rule_module = call_routing_rule


def tearDownModule() -> None:
	for name, original in _SAVED.items():
		if original is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = original


def _row(target_type, phone_number=None, phone_label=None, target_value=None, idx=1):
	return types.SimpleNamespace(
		target_type=target_type,
		phone_number=phone_number,
		phone_label=phone_label,
		target_value=target_value,
		target_doctype=None,
		idx=idx,
	)


def _rule(*rows):
	doc = rule_module.CallRoutingRule()
	doc.targets = list(rows)
	return doc


class TestCompile(unittest.TestCase):
	def test_bare_digits_become_one_e164_leg_with_its_label(self) -> None:
		warnings: list[str] = []
		out = call_routing._compile_targets(
			"Everyone",
			[{"target_type": "Phone Number", "phone_number": "(801) 555-0134", "phone_label": "Office"}],
			warnings,
		)
		self.assertEqual(out, [{"type": "number", "value": "+18015550134", "label": "Office"}])
		self.assertEqual(warnings, [])

	def test_a_missing_label_falls_back_to_the_number_typed(self) -> None:
		out = call_routing._compile_targets(
			"Everyone", [{"target_type": "Phone Number", "phone_number": "8015550134"}], []
		)
		self.assertEqual(out[0]["label"], "8015550134")

	def test_an_undialable_number_is_dropped_and_explained(self) -> None:
		"""Never an empty <Number> on a live call — that is a Twilio error mid-ring."""
		warnings: list[str] = []
		out = call_routing._compile_targets(
			"Everyone", [{"target_type": "Phone Number", "phone_number": "ext 12"}], warnings
		)
		self.assertEqual(out, [])
		self.assertEqual(len(warnings), 1)
		self.assertIn("ext 12", warnings[0])

	def test_it_rings_alongside_other_targets_in_declared_order(self) -> None:
		out = call_routing._compile_targets(
			"Everyone",
			[
				{"target_type": "Phone Number", "phone_number": "801-555-0134"},
				{"target_type": "Account Manager"},
				{"target_type": "Phone Number", "phone_number": "+447700900123"},
			],
			[],
		)
		self.assertEqual(
			[(leg["type"], leg.get("value")) for leg in out],
			[("number", "+18015550134"), ("account_manager", None), ("number", "+447700900123")],
		)


class TestRuleValidate(unittest.TestCase):
	def test_numbers_are_stored_as_e164(self) -> None:
		doc = _rule(_row("Phone Number", "801.555.0134"))
		doc._normalise_phone_targets()
		self.assertEqual(doc.targets[0].phone_number, "+18015550134")

	def test_a_non_number_refuses_the_save(self) -> None:
		with self.assertRaises(_Thrown):
			_rule(_row("Phone Number", "call Brian"))._normalise_phone_targets()

	def test_a_blank_number_refuses_the_save(self) -> None:
		with self.assertRaises(_Thrown):
			_rule(_row("Phone Number", ""))._normalise_phone_targets()

	def test_the_business_line_itself_refuses_the_save(self) -> None:
		"""In any format — the comparison is on the normalised number."""
		for typed in ("801-837-2199", "+1 (801) 837-2199", "18018372199"):
			with self.subTest(typed=typed), self.assertRaises(_Thrown) as caught:
				_rule(_row("Phone Number", typed))._normalise_phone_targets()
			self.assertIn("phone menu", str(caught.exception))

	def test_other_target_types_are_left_alone(self) -> None:
		doc = _rule(_row("Account Manager"), _row("Phone Number", "8015550134", idx=2))
		doc._normalise_phone_targets()
		self.assertEqual(doc.targets[1].phone_number, "+18015550134")

	def test_switching_away_from_phone_number_clears_the_hidden_number(self) -> None:
		"""Same reason target_value is cleared: a field hidden by depends_on must not keep a
		target the person thinks they removed, ready to come back on a switch back."""
		doc = _rule(_row("Voicemail", phone_number="+18015550134", phone_label="Office"))
		doc._stamp_target_doctypes()
		self.assertIsNone(doc.targets[0].phone_number)
		self.assertIsNone(doc.targets[0].phone_label)

	def test_phone_number_rows_keep_their_number_through_stamping(self) -> None:
		doc = _rule(_row("Phone Number", phone_number="+18015550134", phone_label="Office"))
		doc._stamp_target_doctypes()
		self.assertEqual(doc.targets[0].phone_number, "+18015550134")
		self.assertEqual(doc.targets[0].target_doctype, "")
		self.assertIsNone(doc.targets[0].target_value)


class TestOptionStringAgrees(unittest.TestCase):
	def test_the_select_offers_exactly_the_string_the_code_checks(self) -> None:
		target = json.loads(TARGET_JSON.read_text(encoding="utf-8"))
		select = next(f for f in target["fields"] if f["fieldname"] == "target_type")
		self.assertIn(rule_module.PHONE_NUMBER, select["options"].split("\n"))

		# And the compiler recognises that same string rather than reporting it unknown.
		warnings: list[str] = []
		call_routing._compile_targets(
			"r", [{"target_type": rule_module.PHONE_NUMBER, "phone_number": "8015550134"}], warnings
		)
		self.assertEqual(warnings, [])

	def test_the_number_field_is_shown_and_required_only_for_phone_number(self) -> None:
		target = json.loads(TARGET_JSON.read_text(encoding="utf-8"))
		field = next(f for f in target["fields"] if f["fieldname"] == "phone_number")
		self.assertIn('"Phone Number"', field["depends_on"])
		self.assertIn('"Phone Number"', field["mandatory_depends_on"])


if __name__ == "__main__":
	unittest.main()
