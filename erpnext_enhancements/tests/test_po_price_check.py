# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The $0 Purchase Order line warning (POL-0602 §4.6). Bench-free.

``po_price_check.warn_zero_rate_lines`` runs on Purchase Order ``before_submit`` (v1.532.0;
Nik, 2026-09-24, TASK-2026-02238). It must WARN and never block: 281 of 327 submitted lines
in the 90 days to 2026-09-24 were $0, so anything that raised would stop purchasing outright.
Asserted here by making the message itself fail and checking the submit still goes through.

Also asserted: it sits after the two submit gates, so a refused order is not also warned
about, and it is silent where nobody would read it.

Installs its own ``frappe`` stub in ``setUpModule`` (execution time), so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_po_price_check
"""

import ast
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"

STATE = {"messages": [], "errors": [], "msgprint_raises": False}
check = None


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s

	def msgprint(msg, title=None, indicator=None, **kwargs):
		if STATE["msgprint_raises"]:
			raise RuntimeError("boom")
		STATE["messages"].append({"msg": msg, "title": title, "indicator": indicator})

	frappe.msgprint = msgprint
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	frappe.get_traceback = lambda: "traceback"
	frappe.flags = types.SimpleNamespace()
	frappe.local = types.SimpleNamespace(request=object())
	sys.modules["frappe"] = frappe


def setUpModule():
	global check
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.po_price_check", None)
	from erpnext_enhancements import po_price_check as mod

	check = mod


class _Row:
	"""A child row as ERPNext hands it over: attributes plus .get()."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class _PO(dict):
	pass


class ZeroRateRowsTest(unittest.TestCase):
	def test_only_zero_rate_lines_with_their_row_numbers(self):
		rows = [
			{"idx": 1, "item_code": "806-020", "qty": 4, "uom": "Nos", "rate": 3.25},
			{"idx": 2, "item_code": "22-1044", "qty": 2, "uom": "Nos", "rate": 0},
			{"idx": 3, "item_code": "13974", "qty": 1.5, "uom": "Quart", "rate": 0.0},
		]
		self.assertEqual(
			check.zero_rate_rows(rows),
			[
				{"idx": 2, "item_code": "22-1044", "qty": 2.0, "uom": "Nos"},
				{"idx": 3, "item_code": "13974", "qty": 1.5, "uom": "Quart"},
			],
		)

	def test_blank_and_unreadable_rates_are_unpriced(self):
		for rate in (None, "", "0", "0.00", "n/a"):
			self.assertEqual(len(check.zero_rate_rows([{"item_code": "X", "rate": rate}])), 1, repr(rate))

	def test_the_half_cent_boundary(self):
		"""Below half a cent rounds to $0.00 on the order and the receipt; half a cent does not."""
		self.assertEqual(len(check.zero_rate_rows([{"rate": 0.0049}])), 1)
		self.assertEqual(check.zero_rate_rows([{"rate": 0.005}]), [])
		self.assertEqual(check.zero_rate_rows([{"rate": "12.50"}]), [])

	def test_row_objects_and_missing_idx(self):
		rows = [_Row(item_code="A", qty=1, stock_uom="Unit", rate=0), _Row(item_code="B", qty=1, rate=5)]
		self.assertEqual(
			check.zero_rate_rows(rows), [{"idx": 1, "item_code": "A", "qty": 1.0, "uom": "Unit"}]
		)

	def test_no_rows(self):
		self.assertEqual(check.zero_rate_rows(None), [])
		self.assertEqual(check.zero_rate_rows([]), [])


class MessageTest(unittest.TestCase):
	def test_names_every_line_the_cost_and_the_rule(self):
		msg = check.warning_message([{"idx": 2, "item_code": "22-1044", "qty": 2.0, "uom": "Nos"}])
		self.assertIn("Row 2: 22-1044, qty 2 Nos", msg)
		self.assertIn("comes in at $0", msg)
		self.assertIn("POL-0602 section 4.6", msg)
		self.assertIn("a line with no price", msg)

	def test_plural(self):
		rows = [{"idx": i, "item_code": f"I{i}", "qty": 1.0, "uom": ""} for i in (1, 2, 3)]
		self.assertIn("3 lines with no price", check.warning_message(rows))

	def test_values_from_the_order_are_escaped(self):
		msg = check.warning_message([{"idx": 1, "item_code": "<script>x</script>", "qty": 1.0, "uom": "<b>"}])
		self.assertNotIn("<script>", msg)
		self.assertIn("&lt;script&gt;", msg)


class HookTest(unittest.TestCase):
	def setUp(self):
		frappe = sys.modules["frappe"]
		frappe.flags = types.SimpleNamespace()
		frappe.local = types.SimpleNamespace(request=object())
		STATE.update(messages=[], errors=[], msgprint_raises=False)
		self.po = _PO(items=[{"idx": 1, "item_code": "22-1044", "qty": 2, "uom": "Nos", "rate": 0}])

	def test_warns_orange_with_the_title(self):
		check.warn_zero_rate_lines(self.po)
		self.assertEqual(len(STATE["messages"]), 1)
		self.assertEqual(STATE["messages"][0]["title"], "Unpriced lines")
		self.assertEqual(STATE["messages"][0]["indicator"], "orange")

	def test_a_fully_priced_order_says_nothing(self):
		check.warn_zero_rate_lines(_PO(items=[{"item_code": "A", "qty": 1, "rate": 9}]))
		check.warn_zero_rate_lines(_PO())
		self.assertEqual(STATE["messages"], [])

	def test_never_raises(self):
		"""The whole contract. A failure inside the warning must not stop the submit."""
		STATE["msgprint_raises"] = True
		check.warn_zero_rate_lines(self.po)
		self.assertEqual(len(STATE["errors"]), 1)

	def test_silent_in_bulk_contexts(self):
		for flag in ("in_import", "in_migrate", "in_install", "in_patch"):
			with self.subTest(flag=flag):
				sys.modules["frappe"].flags = types.SimpleNamespace(**{flag: True})
				check.warn_zero_rate_lines(self.po)
		self.assertEqual(STATE["messages"], [])

	def test_silent_outside_a_web_request(self):
		sys.modules["frappe"].local = types.SimpleNamespace()
		check.warn_zero_rate_lines(self.po)
		self.assertEqual(STATE["messages"], [])


class WiringTest(unittest.TestCase):
	def test_registered_last_on_before_submit_after_the_gates(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		doc_events = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "doc_events" for t in node.targets)
		)
		po = next(
			v
			for k, v in zip(doc_events.keys, doc_events.values, strict=False)
			if getattr(k, "value", None) == "Purchase Order"
		)
		before_submit = next(
			ast.literal_eval(v)
			for k, v in zip(po.keys, po.values, strict=False)
			if getattr(k, "value", None) == "before_submit"
		)
		ours = "erpnext_enhancements.po_price_check.warn_zero_rate_lines"
		self.assertEqual(before_submit[-1], ours)
		for gate in (
			"erpnext_enhancements.po_segregation.enforce_requester_separation",
			"erpnext_enhancements.po_approval.enforce_threshold",
		):
			self.assertLess(before_submit.index(gate), before_submit.index(ours), gate)


if __name__ == "__main__":
	unittest.main()
