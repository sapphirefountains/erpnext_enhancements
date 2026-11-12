# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for "a price is required when a stock item is received" (v1.809.0).

Three parts:

* **The rule** (``inventory_enhancements/receipt_price_rules.py``, no Frappe): which line is
  unpriced, which documents are judged, the words of the refusal and the warning, a typed price,
  and the rows handed to ERPNext's *Update Items*.
* **The hooks and reads** (``receipt_price.py``) on a small ``frappe`` stand-in: the submit
  refuses, the draft only warns and never raises, both stand aside during a migrate, and a price
  reaches the order only through ERPNext's own ``update_child_qty_rate``, for a person who may
  edit the order.
* **Every receiving path** ends in that refusal, and the two screens ask first: ``hooks.py``, the
  Receive Items endpoint and dialog, the Stock Scan endpoint and page, and the intake's drafts,
  read as source.

Fake item codes and document names only: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_receipt_price -v
"""

import ast
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"
PROCUREMENT = APP / "api" / "procurement.py"
STOCK_SCAN = APP / "api" / "stock_scan.py"
DIALOG_JS = APP / "public" / "js" / "po_receive_items.js"
SCAN_LOGIC_JS = APP / "public" / "js" / "stock_scan" / "logic.js"
SCAN_APP_JS = APP / "public" / "js" / "stock_scan" / "app.js"
INTAKE = APP / "accounting_intake" / "actions"

PART = "TST-PUMP-100"
OTHER_PART = "TST-TEE-200"
FEE = "TST-FREIGHT"
ORDER = "PO-TEST-1"
BUYER = "buyer@example.com"
OTHER_BUYER = "second.buyer@example.com"
FULL_NAMES = {BUYER: "Test Buyer", OTHER_BUYER: "Second Buyer", "manager@example.com": "Test Manager"}
BOARD = "erpnext_enhancements.inventory_enhancements.reorder_board"
_REAL_BOARD = None

rules = None
price = None
FRAPPE = None
STATE = None


class _dict(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class Doc:
	def __init__(self, doctype, name="DOC-1", items=None, **fields):
		self.doctype = doctype
		self.name = name
		self.items = [_dict(row) for row in items or []]
		self._fields = fields
		self.flags = _dict()

	def get(self, key, default=None):
		if key == "items":
			return self.items
		return self._fields.get(key, default)

	def add_comment(self, kind, text):
		STATE["comments"].append((self.doctype, self.name, kind, text))


class FrappeError(Exception):
	pass


def build_frappe():
	frappe = types.ModuleType("frappe")

	class ValidationError(FrappeError):
		pass

	class PermissionError_(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _dict
	frappe.flags = _dict()
	frappe.session = _dict(user="receiver@example.com")
	frappe.local = types.SimpleNamespace(request=types.SimpleNamespace(headers={}))

	def throw(msg, exc=None, title=None, **kwargs):
		STATE["thrown"].append({"message": msg, "title": title})
		raise (exc or ValidationError)(msg)

	def msgprint(msg, title=None, indicator=None, **kwargs):
		STATE["messages"].append({"message": msg, "title": title, "indicator": indicator})

	def get_all(doctype, filters=None, pluck=None, **kwargs):
		if STATE.get("get_all_raises"):
			raise RuntimeError("the database is away")
		filters = filters or {}
		if doctype == "Item":
			wanted = set(filters["name"][1])
			if "is_customer_provided_item" in filters:
				return [code for code in STATE["customer_provided"] if code in wanted]
			assert filters.get("is_stock_item") == 1, filters
			return [code for code in STATE["stock_items"] if code in wanted]
		if doctype == "Has Role":
			assert filters["role"] == "PO Creator", filters
			return list(STATE["po_creators"])
		if doctype == "User":
			wanted = set(filters["name"][1])
			return [user for user in STATE["enabled_users"] if user in wanted]
		if doctype == "Material Request":
			# The requests among these that the session user raised.
			wanted = set(filters["name"][1])
			assert filters["owner"] == frappe.session.user
			return [name for name in STATE["my_requests"] if name in wanted]
		if doctype == "ToDo":
			if STATE.get("todo_raises"):
				raise RuntimeError("the database is away")
			return [
				_dict(name=todo["name"], description=todo["description"])
				for todo in STATE["todos"]
				if todo["reference_name"] == filters["reference_name"]
				and todo["allocated_to"] == filters["allocated_to"]
				and todo["status"] == filters["status"]
				and filters["description"][1].strip("%") in todo["description"]
			]
		raise AssertionError(doctype)

	def has_permission(doctype=None, ptype="read", doc=None, **kwargs):
		STATE["permission_checks"].append((doctype, ptype, doc))
		return (doctype, ptype) not in STATE["denied"]

	def log_error(message=None, title=None, **kwargs):
		STATE["errors"].append((title, message))

	frappe.throw = throw
	frappe.msgprint = msgprint
	frappe.get_all = get_all
	frappe.has_permission = has_permission
	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: "traceback"

	class NewDoc:
		def __init__(self, values):
			self.values = dict(values)

		def insert(self, ignore_permissions=False):
			assert self.values["doctype"] == "ToDo", self.values
			self.values["name"] = f"todo-{len(STATE['todos']) + 1}"
			STATE["todos"].append(self.values)
			return self

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			return NewDoc(doctype)
		# The order as the database holds it now, which may be newer than the one a caller holds.
		return STATE["orders"].get(name) or Doc(doctype, name)

	frappe.get_doc = get_doc

	def set_value(doctype, name, field, value, **kwargs):
		assert doctype == "ToDo" and field == "description", (doctype, field)
		next(todo for todo in STATE["todos"] if todo["name"] == name)["description"] = value

	frappe.db = types.SimpleNamespace(
		get_single_value=lambda doctype, field: STATE["singles"].get((doctype, field)),
		set_value=set_value,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.get_fullname = lambda user=None: FULL_NAMES.get(user, "Test Receiver")
	frappe.utils = utils
	return frappe, utils


def reset_state(**overrides):
	global STATE
	STATE = {
		"stock_items": [PART, OTHER_PART],
		"thrown": [],
		"messages": [],
		"errors": [],
		"comments": [],
		"permission_checks": [],
		"denied": set(),
		"updates": [],
		"orders": {},
		"todos": [],
		"po_creators": [BUYER, OTHER_BUYER],
		"enabled_users": [BUYER, OTHER_BUYER, "manager@example.com", "receiver@example.com"],
		"my_requests": [],
		"customer_provided": [],
		# Who the Reorder Board alerts (its own recipients(): the alert role's people).
		"alert_people": [OTHER_BUYER, "manager@example.com", "Administrator", "receiver@example.com"],
		"singles": {("ERPNext Enhancements Settings", "po_sod_enforcement_enabled"): 1},
	}
	STATE.update(overrides)
	FRAPPE.flags = _dict()
	FRAPPE.local.request = types.SimpleNamespace(headers={})


def setUpModule():
	global rules, price, FRAPPE
	frappe, utils = build_frappe()
	FRAPPE = frappe
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	path = "erpnext"
	sys.modules.setdefault(path, types.ModuleType(path))
	for part in ("controllers", "accounts_controller"):
		path = f"{path}.{part}"
		sys.modules.setdefault(path, types.ModuleType(path))

	def update_child_qty_rate(parent_doctype, trans_items, parent_doctype_name, child_docname="items"):
		STATE["updates"].append((parent_doctype, json.loads(trans_items), parent_doctype_name))

	sys.modules["erpnext.controllers.accounts_controller"].update_child_qty_rate = update_child_qty_rate
	reset_state()
	# The Reorder Board's own "who is purchasing" (recipients()), which the ToDo borrows. The real
	# module needs a bench's worth of imports; the one function is stood in for, and put back after.
	global _REAL_BOARD
	_REAL_BOARD = sys.modules.get(BOARD)
	board = types.ModuleType(BOARD)
	board.recipients = lambda: list(STATE["alert_people"])
	sys.modules[BOARD] = board
	for name in (
		"erpnext_enhancements.po_segregation",
		"erpnext_enhancements.inventory_enhancements.maintain_stock_rules",
		"erpnext_enhancements.inventory_enhancements.receipt_price_rules",
		"erpnext_enhancements.inventory_enhancements.receipt_price",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.inventory_enhancements import receipt_price as price_module
	from erpnext_enhancements.inventory_enhancements import receipt_price_rules as rules_module

	rules, price = rules_module, price_module


def tearDownModule():
	if _REAL_BOARD is None:
		sys.modules.pop(BOARD, None)
	else:
		sys.modules[BOARD] = _REAL_BOARD


def line(idx=1, item_code=PART, rate=0, **extra):
	row = {"idx": idx, "name": f"row-{idx}", "item_code": item_code, "rate": rate, "purchase_order": ORDER}
	row.update(extra)
	return row


def order(rows=None):
	rows = rows or [
		{
			"name": "poi-1",
			"idx": 1,
			"item_code": PART,
			"item_name": "Test pump",
			"rate": 0,
			"qty": 2,
			"uom": "Nos",
			"conversion_factor": 1,
			"schedule_date": "2026-11-20",
			"description": "A pump",
		},
		{
			"name": "poi-2",
			"idx": 2,
			"item_code": OTHER_PART,
			"item_name": "Test tee",
			"rate": 4.5,
			"qty": 10,
			"uom": "Box",
			"conversion_factor": 12,
			"schedule_date": "2026-11-20",
			"description": "A tee",
		},
		{
			"name": "poi-3",
			"idx": 3,
			"item_code": FEE,
			"item_name": "Freight",
			"rate": 0,
			"qty": 1,
			"uom": "Nos",
			"conversion_factor": 1,
			"schedule_date": None,
			"description": "Freight",
		},
	]
	doc = Doc("Purchase Order", ORDER, items=rows, owner=BUYER)
	STATE["orders"][ORDER] = doc
	return doc


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


class RuleTest(unittest.TestCase):
	STOCK = {PART, OTHER_PART}

	def test_a_stock_items_line_with_no_price_is_unpriced(self):
		self.assertTrue(rules.is_unpriced(line(rate=0), self.STOCK))
		self.assertTrue(rules.is_unpriced(line(rate=None), self.STOCK))
		self.assertTrue(rules.is_unpriced(line(rate=0.004), self.STOCK))
		self.assertTrue(rules.is_unpriced(line(rate="not a number"), self.STOCK))

	def test_what_is_not_unpriced(self):
		self.assertFalse(rules.is_unpriced(line(rate=0.01), self.STOCK))
		self.assertFalse(rules.is_unpriced(line(item_code=FEE), self.STOCK), "a non-stock line")
		self.assertFalse(rules.is_unpriced(line(allow_zero_valuation_rate=1), self.STOCK), "free on purpose")
		self.assertFalse(
			rules.is_unpriced(line(is_free_item=1, pricing_rules="PRLE-0001"), self.STOCK),
			"a pricing rule's free item",
		)
		self.assertFalse(rules.is_unpriced(line(item_code=None), self.STOCK))
		# A row that only claims to be a free item, with no rule behind it, was not made by ERPNext.
		self.assertTrue(rules.is_unpriced(line(is_free_item=1), self.STOCK))

	def test_what_erpnext_frees_itself_is_told_from_a_persons_tick(self):
		"""Read from ERPNext v16.54.0: a pricing rule's free row carries ``is_free_item`` and the
		rule's name in ``pricing_rules``; a customer-provided item is ticked for the Item's flag."""
		by_rule = line(is_free_item=1, pricing_rules="PRLE-0001")
		self.assertTrue(rules.erpnext_free(by_rule))
		self.assertFalse(rules.erpnext_free(line(is_free_item=1)))
		self.assertFalse(rules.erpnext_free(line(is_free_item=1, pricing_rules="  ")))
		provided = line(allow_zero_valuation_rate=1)
		self.assertTrue(rules.erpnext_free(provided, customer_provided={PART}))
		self.assertFalse(rules.erpnext_free(provided))
		# A person's tick: the box, a stock item, $0, and not ERPNext's own doing.
		self.assertTrue(rules.is_ticked(provided, self.STOCK))
		self.assertFalse(rules.is_ticked(provided, self.STOCK, customer_provided={PART}))
		self.assertFalse(rules.is_ticked(line(allow_zero_valuation_rate=1, rate=5), self.STOCK), "priced")
		self.assertFalse(rules.is_ticked(line(item_code=FEE, allow_zero_valuation_rate=1), self.STOCK))
		self.assertFalse(rules.is_ticked(line(), self.STOCK), "not ticked: that is unpriced")
		both = line(is_free_item=1, pricing_rules="PRLE-0001", allow_zero_valuation_rate=1)
		self.assertFalse(rules.is_ticked(both, self.STOCK), "a rule's free row stays the rule's")
		found = rules.ticked_lines([line(1, rate=5), line(2, allow_zero_valuation_rate=1)], self.STOCK)
		self.assertEqual([(l["idx"], l["order"]) for l in found], [(2, ORDER)])

	def test_the_reason_is_a_line_in_remarks(self):
		self.assertEqual(rules.reason_in("No charge: warranty replacement"), "warranty replacement")
		self.assertEqual(
			rules.reason_in("Scanned in by Test.\nno charge:   free sample \nmore"), "free sample"
		)
		for empty in (
			None,
			"",
			"No Remarks",
			"Created from Document Intake DI-0001",
			"No charge:",
			"No charge:   \nx",
		):
			self.assertEqual(rules.reason_in(empty), "", empty)
		remark = rules.no_charge_remark("warranty replacement", "2, 3")
		self.assertEqual(remark, "No charge: warranty replacement (row 2, 3)")
		self.assertTrue(rules.reason_in(remark))

	def test_the_tick_refusals_say_who_can_and_where_to_say_why(self):
		lines = rules.ticked_lines([line(2, allow_zero_valuation_rate=1)], self.STOCK)
		who = rules.tick_refusal(
			"Purchase Receipt", [(lines, rules.block_sentence(rules.BLOCK_NO_WRITE, ORDER))]
		)
		self.assertTrue(
			who.startswith("This receipt can't be submitted. Row 2 (TST-PUMP-100) is ticked Allow Zero")
		)
		self.assertIn("Purchasing can.", who)
		self.assertIn("Untick the box", who)
		why = rules.tick_reason_refusal("Purchase Receipt", lines)
		self.assertIn('Say why in Remarks, on a line that starts with "No charge:"', why)
		self.assertIn("for example, No charge: warranty replacement", why)
		self.assertIn("purchase orders", rules.no_order_block_sentence())

	def test_a_todos_rows_can_be_read_back(self):
		text = rules.price_todo(
			ORDER, [{"idx": 2, "item_code": PART}, {"idx": 11, "item_code": "A (B)"}], "x"
		)
		self.assertEqual(rules.todo_rows(text), {2, 11})
		self.assertEqual(rules.todo_rows(None), set())

	def test_the_free_mark_is_erpnexts_own_field(self):
		self.assertEqual(rules.FREE_FIELD, "allow_zero_valuation_rate")

	def test_which_documents_are_judged(self):
		self.assertTrue(rules.applies("Purchase Receipt"))
		self.assertFalse(rules.applies("Purchase Receipt", is_return=True))
		self.assertFalse(rules.applies("Purchase Receipt", is_internal=True))
		self.assertTrue(rules.applies("Purchase Invoice", update_stock=True))
		self.assertFalse(rules.applies("Purchase Invoice", update_stock=False))
		self.assertFalse(rules.applies("Purchase Order"))
		self.assertFalse(rules.applies("Stock Entry"))

	def test_lines_are_named_by_row_and_order(self):
		rows = [line(1, rate=5), line(2), line(3, FEE), {"item_code": OTHER_PART, "rate": 0}]
		found = rules.unpriced_lines(rows, self.STOCK)
		self.assertEqual(
			[(l["idx"], l["item_code"], l["order"]) for l in found], [(2, PART, ORDER), (4, OTHER_PART, "")]
		)

	def test_the_refusal_names_the_line_and_what_to_do(self):
		text = rules.submit_refusal("Purchase Receipt", rules.unpriced_lines([line(2)], self.STOCK))
		self.assertTrue(
			text.startswith("This receipt can't be submitted. Row 2 (TST-PUMP-100) has no price.")
		)
		self.assertIn("valued at $0", text)
		self.assertIn(f"Put the price on Purchase Order {ORDER} with Update Items", text)
		self.assertIn("has to match its order's", text)
		self.assertIn("tick Allow Zero Valuation Rate on that row", text)

	def test_a_line_with_no_order_is_priced_on_the_line(self):
		lines = rules.unpriced_lines([{"idx": 1, "item_code": PART, "rate": 0}], self.STOCK)
		text = rules.submit_refusal("Purchase Invoice", lines)
		self.assertTrue(text.startswith("This invoice can't be submitted."))
		self.assertIn("Enter the price paid in Rate.", text)
		self.assertNotIn("Update Items", text)

	def test_a_mix_says_both(self):
		lines = rules.unpriced_lines([line(1), {"idx": 2, "item_code": OTHER_PART, "rate": 0}], self.STOCK)
		text = rules.submit_refusal("Purchase Receipt", lines)
		self.assertIn("Row 1 (TST-PUMP-100), row 2 (TST-TEE-200) have no price.", text)
		self.assertIn("For row 2 (TST-TEE-200), enter the price paid in Rate.", text)

	def test_the_draft_warning_says_it_saved(self):
		text = rules.draft_warning("Purchase Receipt", rules.unpriced_lines([line(1)], self.STOCK))
		self.assertTrue(text.startswith("This receipt is saved, but it can't be submitted yet."))

	def test_order_lines_that_need_a_price(self):
		po = order()
		lines = [dict(row) for row in po.items]
		self.assertEqual(
			[l["purchase_order_item"] for l in rules.order_lines_needing_price(lines, self.STOCK)], ["poi-1"]
		)
		self.assertEqual(rules.order_lines_needing_price(lines, self.STOCK, wanted={"poi-2", "poi-3"}), [])
		self.assertEqual(rules.order_lines_needing_price(lines, set()), [])

	def test_a_typed_price(self):
		self.assertEqual(rules.checked_price("12.50"), (12.5, None))
		self.assertEqual(rules.checked_price(0)[0], None)
		self.assertEqual(rules.checked_price("abc")[0], None)
		self.assertIn("more than this page takes", rules.checked_price(rules.MAX_UNIT_PRICE + 1)[1])

	NEEDING = [
		{"purchase_order_item": "a", "idx": 1, "item_code": PART},
		{"purchase_order_item": "b", "idx": 2, "item_code": PART},
		{"purchase_order_item": "c", "idx": 3, "item_code": PART},
		{"purchase_order_item": "d", "idx": 4, "item_code": PART},
	]

	def test_settling_what_a_person_who_may_price_sent(self):
		typed = {
			"a": {"rate": "7.25"},
			"b": {"no_charge": 1, "reason": "  warranty   replacement "},
			"d": {"rate": -3},
		}
		out = rules.settle(self.NEEDING, typed)
		self.assertEqual(out["prices"], {"a": 7.25})
		self.assertEqual(out["free"], [("b", "warranty replacement")])
		self.assertEqual([m["purchase_order_item"] for m in out["missing"]], ["c"])
		self.assertEqual(out["problems"], ["Row 4 (TST-PUMP-100): Enter a price above zero."])
		self.assertEqual(out["held"], [])
		# "No charge" wins over a price sent with it.
		both = rules.settle(self.NEEDING[:1], {"a": {"rate": 5, "no_charge": 1, "reason": "sample"}})
		self.assertEqual((both["prices"], both["free"]), ({}, [("a", "sample")]))

	def test_no_charge_needs_a_reason(self):
		self.assertTrue(rules.NO_CHARGE_NEEDS_REASON, "the default pending Nik's answer")
		out = rules.settle(self.NEEDING[:1], {"a": {"no_charge": 1, "reason": "  "}})
		self.assertEqual(out["free"], [])
		self.assertEqual(
			out["problems"], ["Row 1 (TST-PUMP-100): say why it was free (a sample, a warranty replacement)."]
		)

	def test_a_person_who_can_do_neither_has_every_line_held_whatever_they_send(self):
		self.assertTrue(rules.NO_CHARGE_NEEDS_ORDER_WRITE, "the default pending Nik's answer")
		typed = {"a": {"rate": 5}, "b": {"no_charge": 1, "reason": "sample"}}
		out = rules.settle(self.NEEDING, typed, can_price=False, can_mark_free=False)
		self.assertEqual([h["purchase_order_item"] for h in out["held"]], ["a", "b", "c", "d"])
		self.assertEqual((out["prices"], out["free"], out["missing"], out["problems"]), ({}, [], [], []))

	def test_flipping_the_default_lets_any_receiver_mark_a_line_free(self):
		"""What ``NO_CHARGE_NEEDS_ORDER_WRITE = False`` would mean: free is taken, the rest is held."""
		typed = {"a": {"rate": 5}, "b": {"no_charge": 1, "reason": "sample"}}
		out = rules.settle(self.NEEDING[:3], typed, can_price=False, can_mark_free=True)
		self.assertEqual(out["free"], [("b", "sample")])
		self.assertEqual([h["purchase_order_item"] for h in out["held"]], ["a", "c"])

	def test_the_rows_for_update_items_change_one_rate_and_nothing_else(self):
		po = order()
		rows = rules.price_changes(po.items, {"poi-1": 19.99})
		self.assertEqual(
			[r["docname"] for r in rows], ["poi-1", "poi-2", "poi-3"], "a row left out is deleted"
		)
		self.assertEqual(rows[0]["rate"], 19.99)
		for sent, kept in zip(rows[1:], po.items[1:], strict=True):
			self.assertEqual(sent["rate"], kept["rate"])
		for sent, kept in zip(rows, po.items, strict=True):
			for field in ("item_code", "qty", "uom", "conversion_factor", "description"):
				self.assertEqual(sent[field], kept[field], field)
		self.assertEqual(rows[0]["schedule_date"], "2026-11-20")
		self.assertIsNone(rows[2]["schedule_date"])

	def test_the_receivers_words(self):
		text = rules.receive_refusal(ORDER, [{"idx": 2, "item_code": PART}])
		self.assertTrue(
			text.startswith(f"Nothing was received. On {ORDER}, row 2 (TST-PUMP-100) has no price")
		)
		self.assertIn("mark it as no charge and say why", text)
		self.assertNotIn("19.99", rules.price_comment("Test Receiver", "1"))
		self.assertIn("Reason given: warranty", rules.free_comment("Test Receiver", "1", "warranty"))

	def test_why_a_person_cannot_price_and_who_can(self):
		no_write = rules.block_sentence(rules.BLOCK_NO_WRITE, ORDER)
		self.assertIn(f"You can receive against {ORDER}, but you can't set prices on it.", no_write)
		self.assertIn("Purchasing can.", no_write)
		requester = rules.block_sentence(rules.BLOCK_REQUESTER, ORDER)
		self.assertIn(f"You raised the request that {ORDER} fills", requester)
		self.assertIn("Someone else in purchasing can.", requester)
		# Nobody who can't price is told to tick anything.
		for text in (no_write, requester):
			self.assertNotIn("No charge", text)
			self.assertNotIn("free", text)

	def test_what_a_receiver_is_told_about_a_held_line(self):
		lines = [{"idx": 2, "item_code": PART}]
		some = rules.held_message(ORDER, lines, rules.BLOCK_NO_WRITE, ["Test Buyer"], True)
		self.assertTrue(some.startswith("Row 2 (TST-PUMP-100) was not received: no price on the order"))
		self.assertIn("Test Buyer has been asked to put the price on the order (a ToDo).", some)
		self.assertIn("Set the goods aside", some)
		none = rules.held_message(ORDER, lines, rules.BLOCK_REQUESTER, ["Test Buyer", "Second Buyer"], False)
		self.assertTrue(none.startswith("Nothing was received. Row 2 (TST-PUMP-100) has no price"))
		self.assertIn("Test Buyer and Second Buyer have been asked", none)
		self.assertIn("Tell purchasing it needs a price.", rules.asked_sentence([]))

	def test_the_todo_names_the_order_the_rows_and_who_is_waiting(self):
		text = rules.price_todo(ORDER, [{"idx": 2, "item_code": PART}], "Test Receiver")
		self.assertTrue(text.startswith(f"{rules.TODO_MARKER} on {ORDER}: row 2 (TST-PUMP-100) has no price"))
		self.assertIn("Test Receiver has them at the dock", text)
		self.assertIn("Update Items", text)

	def test_a_material_receipt_is_judged_on_its_basic_rate(self):
		self.assertTrue(rules.applies("Stock Entry", purpose="Material Receipt"))
		for purpose in ("Material Issue", "Material Transfer", "Manufacture", "Repack", None):
			self.assertFalse(rules.applies("Stock Entry", purpose=purpose), purpose)
		self.assertEqual(rules.rate_field("Stock Entry"), "basic_rate")
		self.assertEqual(rules.rate_field("Purchase Receipt"), "rate")
		rows = [
			{"idx": 1, "item_code": PART, "t_warehouse": "Bin - TC", "basic_rate": 0},
			{"idx": 2, "item_code": PART, "t_warehouse": "Bin - TC", "basic_rate": 4},
			{"idx": 3, "item_code": PART, "s_warehouse": "A - TC", "t_warehouse": "B - TC", "basic_rate": 0},
			{
				"idx": 4,
				"item_code": PART,
				"t_warehouse": "Bin - TC",
				"basic_rate": 0,
				"allow_zero_valuation_rate": 1,
			},
			{"idx": 5, "item_code": FEE, "t_warehouse": "Bin - TC", "basic_rate": 0},
		]
		incoming = rules.incoming_rows("Stock Entry", rows)
		self.assertEqual([r["idx"] for r in incoming], [1, 2, 4, 5])
		found = rules.unpriced_lines(incoming, self.STOCK, rate_field="basic_rate")
		self.assertEqual([l["idx"] for l in found], [1])
		text = rules.submit_refusal("Stock Entry", found)
		self.assertTrue(
			text.startswith("This stock entry can't be submitted. Row 1 (TST-PUMP-100) would come")
		)
		self.assertIn("Basic Rate", text)
		self.assertIn("Allow Zero Valuation Rate", text)


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------


class SubmitRefusalTest(unittest.TestCase):
	def setUp(self):
		reset_state()

	def receipt(self, items=None, **fields):
		return Doc("Purchase Receipt", "PR-TEST-1", items=items or [line(1, rate=5), line(2)], **fields)

	def test_a_receipt_with_an_unpriced_stock_line_is_refused(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(self.receipt())
		self.assertIn("Row 2 (TST-PUMP-100) has no price", str(caught.exception))
		self.assertEqual(STATE["thrown"][0]["title"], "A line has no price")

	def test_priced_free_and_non_stock_lines_pass(self):
		price.refuse_unpriced_stock_lines(
			self.receipt(
				[line(1, rate=5), line(2, allow_zero_valuation_rate=1), line(3, FEE)],
				remarks="No charge: warranty replacement",
			)
		)

	def test_a_return_and_an_internal_transfer_pass(self):
		price.refuse_unpriced_stock_lines(self.receipt(is_return=1))
		price.refuse_unpriced_stock_lines(self.receipt(is_internal_supplier=1))

	def test_an_invoice_is_judged_only_when_it_receives_stock(self):
		bill = Doc("Purchase Invoice", "PI-TEST-1", items=[line(1)], update_stock=0)
		price.refuse_unpriced_stock_lines(bill)
		receiving = Doc("Purchase Invoice", "PI-TEST-2", items=[line(1)], update_stock=1)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(receiving)
		self.assertIn("This invoice can't be submitted", str(caught.exception))

	def test_it_stands_aside_for_a_migrate_and_for_the_way_out(self):
		for flag in price.SKIP_FLAGS:
			with self.subTest(flag):
				reset_state()
				FRAPPE.flags[flag] = True
				price.refuse_unpriced_stock_lines(self.receipt())
		reset_state()
		doc = self.receipt()
		doc.flags[price.IGNORE_FLAG] = True
		price.refuse_unpriced_stock_lines(doc)


class MaterialReceiptTest(unittest.TestCase):
	"""A Desk Stock Entry of type Material Receipt is the other way to bring stock in."""

	def setUp(self):
		reset_state()

	def entry(self, purpose="Material Receipt", **row):
		values = {"idx": 1, "item_code": PART, "t_warehouse": "Bin - TC", "basic_rate": 0}
		values.update(row)
		return Doc("Stock Entry", "STE-TEST-1", items=[values], purpose=purpose)

	def test_a_stock_item_coming_in_at_zero_is_refused(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(self.entry())
		self.assertIn("would come into stock at $0", str(caught.exception))

	def test_a_cost_the_free_tick_and_a_non_stock_item_pass(self):
		price.refuse_unpriced_stock_lines(self.entry(basic_rate=12.5))
		price.refuse_unpriced_stock_lines(self.entry(item_code=FEE))
		ticked = self.entry(allow_zero_valuation_rate=1)
		ticked._fields["remarks"] = "No charge: found on the shelf, never bought"
		price.refuse_unpriced_stock_lines(ticked)

	def test_the_free_tick_is_held_to_the_same_people_and_needs_a_reason(self):
		"""No order stands behind a Material Receipt, so the person must be able to edit purchase
		orders at all."""
		ticked = self.entry(allow_zero_valuation_rate=1)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(ticked)
		self.assertIn("This stock entry can't be submitted.", str(caught.exception))
		self.assertIn('"No charge:"', str(caught.exception))
		ticked._fields["remarks"] = "No charge: sample"
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(ticked)
		self.assertIn("Only someone who can edit purchase orders", str(caught.exception))

	def test_other_purposes_and_outgoing_rows_are_left_alone(self):
		for purpose in ("Material Issue", "Material Transfer", "Manufacture"):
			price.refuse_unpriced_stock_lines(self.entry(purpose=purpose))
		price.refuse_unpriced_stock_lines(self.entry(s_warehouse="A - TC"))

	def test_a_draft_is_only_warned(self):
		doc = self.entry()
		doc._fields["docstatus"] = 0
		price.warn_unpriced_stock_lines(doc)
		self.assertIn("is saved, but it can't be submitted yet", STATE["messages"][0]["message"])


class TickedOnTheFormTest(unittest.TestCase):
	"""The hole round 2 of the review found: "no charge" was held to the right people only on the
	two receiving screens. A receiver whose line had just been held could make a Purchase Receipt
	from the order in the Desk, tick Allow Zero Valuation Rate and submit. The same on an intake
	draft. The submit itself asks now, whoever built the document and however."""

	def setUp(self):
		reset_state()
		self.po = order()

	def desk_receipt(self, remarks=None, **row):
		"""Create > Purchase Receipt on the order, with row 1 ticked free by hand."""
		values = line(1, allow_zero_valuation_rate=1, purchase_order_item="poi-1")
		values.update(row)
		return Doc(
			"Purchase Receipt", "PR-TEST-1", items=[values, line(2, OTHER_PART, rate=4.5)], remarks=remarks
		)

	def test_a_receiver_who_cannot_price_the_order_cannot_tick_their_way_past(self):
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(self.desk_receipt(remarks="No charge: sample"))
		text = str(caught.exception)
		self.assertIn("Row 1 (TST-PUMP-100) is ticked Allow Zero Valuation Rate", text)
		self.assertIn(
			f"You can receive against {ORDER}, but you can't set prices on it. Purchasing can.", text
		)
		self.assertIn(("Purchase Order", "write", ORDER), STATE["permission_checks"], "asked of THIS order")
		self.assertEqual(STATE["thrown"][0]["title"], "A line is marked free")

	def test_the_person_who_raised_the_request_cannot_either(self):
		self.po.items[0]["material_request"] = "MR-TEST-1"
		STATE["my_requests"] = ["MR-TEST-1"]
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(self.desk_receipt(remarks="No charge: sample"))
		self.assertIn("You raised the request", str(caught.exception))

	def test_a_permitted_person_must_still_say_why(self):
		for remarks in (None, "", "Thanks!", "No charge:"):
			with self.subTest(remarks=remarks):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					price.refuse_unpriced_stock_lines(self.desk_receipt(remarks=remarks))
				self.assertIn("Say why in Remarks", str(caught.exception))

	def test_a_permitted_persons_tick_with_a_reason_passes(self):
		price.refuse_unpriced_stock_lines(self.desk_receipt(remarks="No charge: warranty replacement"))
		self.assertEqual(STATE["thrown"], [])

	def test_an_intake_draft_is_judged_by_who_submits_it(self):
		"""The packing-slip intake builds the draft as the system and leaves its own remark. Who
		submits it is who is asked, and that remark is not a reason."""
		draft = self.desk_receipt(remarks="Created from Document Intake DI-TEST-1")
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(draft)
		self.assertIn("Purchasing can.", str(caught.exception))
		reset_state()
		order()
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(draft)
		self.assertIn("Say why in Remarks", str(caught.exception), "the intake's own remark is no reason")
		draft._fields["remarks"] = "Created from Document Intake DI-TEST-1\nNo charge: free sample"
		price.refuse_unpriced_stock_lines(draft)

	def test_what_erpnext_ticks_or_frees_itself_still_passes_for_anyone(self):
		STATE["denied"].add(("Purchase Order", "write"))
		# A customer-provided item: ERPNext ticks the box on every save.
		STATE["customer_provided"] = [PART]
		price.refuse_unpriced_stock_lines(self.desk_receipt())
		# A pricing rule's free item: is_free_item with the rule's name, and no tick at all.
		STATE["customer_provided"] = []
		by_rule = Doc(
			"Purchase Receipt",
			"PR-TEST-2",
			items=[line(1, is_free_item=1, pricing_rules="PRLE-0001")],
		)
		price.refuse_unpriced_stock_lines(by_rule)
		self.assertEqual(STATE["thrown"], [])

	def test_a_row_with_no_order_needs_write_on_purchase_orders(self):
		loose = Doc(
			"Purchase Receipt",
			"PR-TEST-3",
			items=[{"idx": 1, "name": "row-1", "item_code": PART, "rate": 0, "allow_zero_valuation_rate": 1}],
			remarks="No charge: sample",
		)
		price.refuse_unpriced_stock_lines(loose)
		self.assertIn(("Purchase Order", "write", None), STATE["permission_checks"])
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(loose)
		self.assertIn("Only someone who can edit purchase orders", str(caught.exception))

	def test_a_receiving_invoice_is_held_to_it_too(self):
		STATE["denied"].add(("Purchase Order", "write"))
		bill = Doc(
			"Purchase Invoice",
			"PI-TEST-1",
			items=[line(1, allow_zero_valuation_rate=1)],
			update_stock=1,
			remarks="No charge: sample",
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(bill)
		self.assertIn("This invoice can't be submitted.", str(caught.exception))

	def test_an_unpriced_line_is_still_said_first(self):
		doc = Doc(
			"Purchase Receipt",
			"PR-TEST-4",
			items=[line(1, allow_zero_valuation_rate=1), line(2)],
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.refuse_unpriced_stock_lines(doc)
		self.assertIn("Row 2 (TST-PUMP-100) has no price", str(caught.exception))

	def test_each_default_is_one_constant(self):
		STATE["denied"].add(("Purchase Order", "write"))
		kept = (rules.NO_CHARGE_NEEDS_ORDER_WRITE, rules.NO_CHARGE_NEEDS_REASON)
		try:
			rules.NO_CHARGE_NEEDS_ORDER_WRITE = False
			price.refuse_unpriced_stock_lines(self.desk_receipt(remarks="No charge: sample"))
			with self.assertRaises(FRAPPE.ValidationError):
				price.refuse_unpriced_stock_lines(self.desk_receipt())
			rules.NO_CHARGE_NEEDS_REASON = False
			price.refuse_unpriced_stock_lines(self.desk_receipt())
		finally:
			rules.NO_CHARGE_NEEDS_ORDER_WRITE, rules.NO_CHARGE_NEEDS_REASON = kept

	def test_it_stands_aside_like_the_rest(self):
		STATE["denied"].add(("Purchase Order", "write"))
		FRAPPE.flags["in_migrate"] = True
		price.refuse_unpriced_stock_lines(self.desk_receipt())


class DraftWarningTest(unittest.TestCase):
	def setUp(self):
		reset_state()

	def draft(self, **fields):
		return Doc("Purchase Receipt", "PR-TEST-1", items=[line(1)], docstatus=0, **fields)

	def test_a_draft_is_warned_not_refused(self):
		price.warn_unpriced_stock_lines(self.draft())
		self.assertEqual(len(STATE["messages"]), 1)
		message = STATE["messages"][0]
		self.assertEqual(message["indicator"], "orange")
		self.assertIn("is saved, but it can't be submitted yet", message["message"])
		self.assertEqual(STATE["thrown"], [])

	def test_silent_when_nobody_is_there_to_read_it(self):
		FRAPPE.local.request = None
		price.warn_unpriced_stock_lines(self.draft())
		self.assertEqual(STATE["messages"], [])

	def test_silent_while_it_is_being_submitted(self):
		doc = self.draft()
		doc._action = "submit"
		price.warn_unpriced_stock_lines(doc)
		price.warn_unpriced_stock_lines(Doc("Purchase Receipt", "PR-2", items=[line(1)], docstatus=1))
		self.assertEqual(STATE["messages"], [])

	def test_it_never_raises(self):
		STATE["get_all_raises"] = True
		price.warn_unpriced_stock_lines(self.draft())
		self.assertEqual(len(STATE["errors"]), 1)

	def test_markup_in_an_item_code_is_escaped(self):
		STATE["stock_items"].append("<b>x</b>")
		doc = Doc("Purchase Receipt", "PR-3", items=[line(1, "<b>x</b>")], docstatus=0)
		price.warn_unpriced_stock_lines(doc)
		self.assertNotIn("<b>", STATE["messages"][0]["message"])


class PricingTheOrderTest(unittest.TestCase):
	def setUp(self):
		reset_state()

	def test_the_lines_that_need_a_price(self):
		po = order()
		self.assertEqual([l["purchase_order_item"] for l in price.needing_price(po)], ["poi-1"])
		self.assertEqual(price.needing_price(po, wanted={"poi-2"}), [])

	def test_a_price_goes_through_erpnexts_update_items(self):
		po = order()
		price.price_order_lines(po, {"poi-1": 19.99})
		self.assertEqual(len(STATE["updates"]), 1)
		doctype, rows, name = STATE["updates"][0]
		self.assertEqual((doctype, name), ("Purchase Order", ORDER))
		self.assertEqual(
			[(r["docname"], r["rate"]) for r in rows], [("poi-1", 19.99), ("poi-2", 4.5), ("poi-3", 0.0)]
		)
		self.assertIn(("Purchase Order", "write", ORDER), STATE["permission_checks"])
		comment = STATE["comments"][0]
		self.assertEqual(comment[:3], ("Purchase Order", ORDER, "Comment"))
		self.assertIn("Test Receiver entered the price for row 1", comment[3])
		self.assertNotIn("19.99", comment[3], "no amounts in a comment")

	def test_the_rows_sent_are_the_orders_rows_now_not_the_callers_copy(self):
		"""Update Items deletes a row it is not sent, so a row somebody added since the request
		began must be in the list."""
		stale = order()
		newer = order()
		newer.items.append(
			_dict(name="poi-4", idx=4, item_code=OTHER_PART, rate=2, qty=1, uom="Nos", conversion_factor=1)
		)
		price.price_order_lines(stale, {"poi-1": 19.99})
		rows = STATE["updates"][0][1]
		self.assertEqual([r["docname"] for r in rows], ["poi-1", "poi-2", "poi-3", "poi-4"])

	def test_a_person_who_may_not_edit_the_order_is_told_who_can(self):
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.PermissionError) as caught:
			price.price_order_lines(order(), {"poi-1": 19.99})
		self.assertIn("Purchasing can.", str(caught.exception))
		self.assertEqual(STATE["updates"], [])

	def test_the_person_who_raised_the_request_cannot_price_the_order_it_fills(self):
		"""Update Items re-runs the separation-of-duties gate and would refuse them, so they are
		told so before any price box is offered, in their own words."""
		po = order()
		po.items[0]["material_request"] = "MR-TEST-1"
		STATE["my_requests"] = ["MR-TEST-1"]
		self.assertEqual(price.price_block(po), rules.BLOCK_REQUESTER)
		self.assertFalse(price.may_price(po))
		self.assertFalse(price.may_mark_free(po))
		with self.assertRaises(FRAPPE.PermissionError) as caught:
			price.price_order_lines(po, {"poi-1": 19.99})
		self.assertIn("You raised the request", str(caught.exception))
		self.assertEqual(STATE["updates"], [])
		# Somebody else's request, or the gate switched off in Settings, does not block.
		STATE["my_requests"] = []
		self.assertIsNone(price.price_block(po))
		STATE["my_requests"] = ["MR-TEST-1"]
		STATE["singles"][("ERPNext Enhancements Settings", "po_sod_enforcement_enabled")] = 0
		self.assertIsNone(price.price_block(po))

	def test_no_write_on_the_order_is_the_other_block(self):
		STATE["denied"].add(("Purchase Order", "write"))
		self.assertEqual(price.price_block(order()), rules.BLOCK_NO_WRITE)

	def test_no_prices_touches_nothing(self):
		price.price_order_lines(order(), {})
		self.assertEqual((STATE["updates"], STATE["permission_checks"]), ([], []))

	def test_settling_before_a_receipt(self):
		po = order()
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(po, {"poi-1", "poi-2"}, {})
		self.assertIn("row 1 (TST-PUMP-100) has no price", str(caught.exception))
		self.assertEqual(STATE["updates"], [])
		out = price.settle_before_receipt(po, {"poi-1"}, {"poi-1": {"no_charge": 1, "reason": "warranty"}})
		self.assertEqual((out["free"], out["held"]), (["poi-1"], []))
		self.assertEqual(STATE["updates"], [], "a free line changes no price")
		# But it leaves a trace on the order: who, which row and why.
		self.assertEqual(len(STATE["comments"]), 1)
		self.assertEqual(STATE["comments"][0][:3], ("Purchase Order", ORDER, "Comment"))
		self.assertIn("Test Receiver received row 1 at no charge", STATE["comments"][0][3])
		self.assertIn("Reason given: warranty", STATE["comments"][0][3])
		# And the line for the receipt's own Remarks, which the submit refusal reads.
		self.assertEqual(out["remark"], "No charge: warranty (row 1)")
		self.assertEqual(rules.reason_in(out["remark"]), "warranty (row 1)")
		STATE["comments"].clear()
		self.assertEqual(price.settle_before_receipt(po, {"poi-1"}, {"poi-1": {"rate": "8"}})["free"], [])
		self.assertEqual(STATE["updates"][0][1][0]["rate"], 8.0)
		# Lines that need no price cost nothing: no permission is even asked.
		reset_state()
		out = price.settle_before_receipt(po, {"poi-2", "poi-3"}, {})
		self.assertEqual((out["free"], out["held"], out["message"]), ([], [], ""))
		self.assertEqual(STATE["permission_checks"], [])

	def test_no_charge_without_a_reason_is_refused(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(order(), {"poi-1"}, {"poi-1": {"no_charge": 1}})
		self.assertIn("say why it was free", str(caught.exception))
		self.assertEqual(STATE["comments"], [])

	def test_a_bad_typed_price_is_refused_in_words(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(order(), {"poi-1"}, {"poi-1": {"rate": "0"}})
		self.assertIn("Enter a price above zero", str(caught.exception))


class HeldLinesTest(unittest.TestCase):
	"""A receiver who can neither price a line nor mark it free: the line is left unreceived,
	the rest goes through, and purchasing is asked. Never a $0 receipt, and never stuck."""

	def setUp(self):
		reset_state(denied={("Purchase Order", "write")})

	def test_the_line_is_held_whatever_was_sent_and_purchasing_gets_a_todo(self):
		po = order()
		typed = {"poi-1": {"no_charge": 1, "reason": "sample", "rate": 5}}
		out = price.settle_before_receipt(po, {"poi-1", "poi-2"}, typed)
		self.assertEqual(out["held"], ["poi-1"])
		self.assertEqual(out["free"], [], "no charge is not theirs to give")
		self.assertEqual(STATE["updates"], [], "and neither is a price")
		self.assertEqual(STATE["comments"], [])
		self.assertEqual(out["block"], rules.BLOCK_NO_WRITE)
		self.assertEqual(out["asked"], ["Test Buyer"])
		self.assertTrue(out["message"].startswith("Row 1 (TST-PUMP-100) was not received"))
		self.assertEqual(len(STATE["todos"]), 1)
		todo = STATE["todos"][0]
		self.assertEqual(
			(todo["allocated_to"], todo["reference_type"], todo["reference_name"]),
			(BUYER, "Purchase Order", ORDER),
		)
		self.assertEqual(todo["assigned_by"], "receiver@example.com")
		self.assertTrue(
			todo["description"].startswith(f"{rules.TODO_MARKER} on {ORDER}: row 1 (TST-PUMP-100)")
		)

	def test_when_every_line_is_held_the_message_says_nothing_was_received(self):
		out = price.settle_before_receipt(order(), {"poi-1"}, {})
		self.assertTrue(out["message"].startswith("Nothing was received."))

	def test_the_same_line_is_not_asked_about_twice(self):
		po = order()
		price.settle_before_receipt(po, {"poi-1"}, {})
		before = STATE["todos"][0]["description"]
		out = price.settle_before_receipt(po, {"poi-1"}, {})
		self.assertEqual(len(STATE["todos"]), 1)
		self.assertEqual(STATE["todos"][0]["description"], before, "nothing new to say")
		self.assertEqual(out["asked"], ["Test Buyer"], "the person already asked is still named")

	def two_unpriced(self):
		po = order()
		po.items.append(
			_dict(
				name="poi-5",
				idx=5,
				item_code=OTHER_PART,
				item_name="Test tee",
				rate=0,
				qty=3,
				uom="Nos",
				conversion_factor=1,
			)
		)
		for row in po.items:
			row.setdefault("received_qty", 0)
		return po

	def test_a_line_held_later_is_added_to_the_open_todo(self):
		"""Row 1 is held on Monday, row 5 on Tuesday while that ToDo is still open: one ToDo, and
		it names both rows, each once."""
		po = self.two_unpriced()
		price.settle_before_receipt(po, {"poi-1"}, {})
		self.assertEqual(rules.todo_rows(STATE["todos"][0]["description"]), {1})
		price.settle_before_receipt(po, {"poi-5"}, {})
		self.assertEqual(len(STATE["todos"]), 1)
		text = STATE["todos"][0]["description"]
		self.assertEqual(rules.todo_rows(text), {1, 5})
		self.assertEqual(text.count("row 1 ("), 1)
		self.assertEqual(text.count("row 5 ("), 1)
		self.assertTrue(
			text.startswith(f"{rules.TODO_MARKER} on {ORDER}: row 1 (TST-PUMP-100), row 5 (TST-TEE-200) have")
		)

	def test_a_row_priced_since_drops_out_of_the_todo(self):
		po = self.two_unpriced()
		price.settle_before_receipt(po, {"poi-1"}, {})
		po.items[0]["rate"] = 12.5  # purchasing priced row 1 but did not close the ToDo
		price.settle_before_receipt(po, {"poi-5"}, {})
		self.assertEqual(rules.todo_rows(STATE["todos"][0]["description"]), {5})

	def test_two_lines_held_at_once_are_both_named(self):
		po = self.two_unpriced()
		price.settle_before_receipt(po, {"poi-1", "poi-5"}, {})
		self.assertEqual(rules.todo_rows(STATE["todos"][0]["description"]), {1, 5})

	def test_who_is_asked(self):
		po = order()
		# The order's own author first, when they can still price it.
		self.assertEqual(price._who_to_ask(po), [BUYER])
		# When they can't be (disabled, or not a PO Creator any more): the people the Reorder Board
		# alerts, every one of them, read with the Board's own function.
		STATE["po_creators"] = [OTHER_BUYER]
		self.assertEqual(price._who_to_ask(po), [OTHER_BUYER, "manager@example.com"])
		# Never a service account, never the asker, never somebody disabled.
		self.assertNotIn("Administrator", price._who_to_ask(po))
		self.assertNotIn("receiver@example.com", price._who_to_ask(po))
		STATE["enabled_users"] = ["manager@example.com"]
		self.assertEqual(price._who_to_ask(po), ["manager@example.com"])

	def test_everyone_asked_gets_a_todo_of_their_own(self):
		STATE["po_creators"] = []
		out = price.settle_before_receipt(order(), {"poi-1"}, {})
		self.assertEqual(out["asked"], ["Second Buyer", "Test Manager"])
		self.assertEqual([t["allocated_to"] for t in STATE["todos"]], [OTHER_BUYER, "manager@example.com"])

	def test_a_todo_that_cannot_be_made_does_not_stop_the_delivery(self):
		STATE["todo_raises"] = True
		out = price.settle_before_receipt(order(), {"poi-1", "poi-2"}, {})
		self.assertEqual(out["held"], ["poi-1"])
		self.assertEqual(out["asked"], [])
		self.assertIn("Tell purchasing it needs a price.", out["message"])
		self.assertEqual(len(STATE["errors"]), 1)

	def test_the_phone_path_refuses_instead_and_makes_no_todo_here(self):
		"""Stock Scan receives one line and asks first; it made the ToDo when it asked. A refusal
		here rolls the request back, so a ToDo made now would be lost anyway."""
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(order(), {"poi-1"}, {}, hold=False)
		self.assertIn("Nothing was received.", str(caught.exception))
		self.assertIn("Purchasing can.", str(caught.exception))
		self.assertEqual(STATE["todos"], [])

	def test_the_requester_is_held_the_same_way_with_their_own_reason(self):
		reset_state()
		po = order()
		po.items[0]["material_request"] = "MR-TEST-1"
		STATE["my_requests"] = ["MR-TEST-1"]
		out = price.settle_before_receipt(po, {"poi-1"}, {"poi-1": {"rate": 5}})
		self.assertEqual(out["block"], rules.BLOCK_REQUESTER)
		self.assertIn("You raised the request", out["message"])
		self.assertEqual(STATE["updates"], [])
		self.assertEqual(len(STATE["todos"]), 1)


# ---------------------------------------------------------------------------
# Every receiving path
# ---------------------------------------------------------------------------


def _hooks_dict(name):
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == name for t in node.targets
		):
			return node.value
	raise AssertionError(name)


def _key(dict_node, key):
	for k, v in zip(dict_node.keys, dict_node.values, strict=True):
		if isinstance(k, ast.Constant) and k.value == key:
			return v
	return None


def _function_source(path, name):
	source = path.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(source, node)
	raise AssertionError(f"{name} is not in {path.name}")


class PathsTest(unittest.TestCase):
	BASE = "erpnext_enhancements.inventory_enhancements.receipt_price."

	def _paths(self, doctype, event):
		value = ast.literal_eval(_key(_key(_hooks_dict("doc_events"), doctype), event))
		return value if isinstance(value, list) else [value]

	def test_the_submit_of_a_receipt_and_of_a_receiving_invoice_is_guarded(self):
		for doctype in ("Purchase Receipt", "Purchase Invoice", "Stock Entry"):
			with self.subTest(doctype):
				self.assertIn(
					self.BASE + "refuse_unpriced_stock_lines", self._paths(doctype, "before_submit")
				)
				self.assertIn(self.BASE + "warn_unpriced_stock_lines", self._paths(doctype, "validate"))
		self.assertTrue(callable(price.refuse_unpriced_stock_lines))
		self.assertTrue(callable(price.warn_unpriced_stock_lines))

	def test_the_earlier_guards_are_still_hooked(self):
		base = "erpnext_enhancements.inventory_enhancements.maintain_stock."
		self.assertIn(base + "refuse_unstocked_receipt_return", self._paths("Purchase Receipt", "validate"))
		self.assertIn(base + "refuse_billing_unstocked_receipts", self._paths("Purchase Invoice", "validate"))
		keys = [k.value for k in _hooks_dict("doc_events").keys if isinstance(k, ast.Constant)]
		for doctype in ("Purchase Receipt", "Purchase Invoice", "Delivery Note", "Stock Entry"):
			self.assertEqual(keys.count(doctype), 1, doctype)

	def test_the_order_still_only_warns(self):
		self.assertIn(
			"erpnext_enhancements.po_price_check.warn_zero_rate_lines",
			self._paths("Purchase Order", "before_submit"),
		)
		self.assertFalse(
			[p for p in self._paths("Purchase Order", "before_submit") if p.startswith(self.BASE)],
			"the order warns; only the receipt refuses",
		)

	def test_receive_items_settles_prices_before_it_builds_a_receipt(self):
		source = _function_source(PROCUREMENT, "receive_items")
		settle = source.index("receipt_price.settle_before_receipt(")
		self.assertLess(settle, source.index("make_purchase_receipt(po.name"))
		self.assertLess(
			source.index("plan_receipt("), settle, "only the lines being received are asked about"
		)
		self.assertIn("item.allow_zero_valuation_rate = 1", source)
		self.assertIn("if item.purchase_order_item in free:", source)
		# It still goes through the framework's own insert and submit, where the backstop is.
		self.assertIn("receipt.insert()", source)
		self.assertIn("receipt.submit()", source)
		# The reason goes onto the receipt's Remarks, where its own submit refusal looks for it.
		self.assertIn('if settled["remark"]:', source)
		self.assertLess(source.index('if settled["remark"]:'), source.index("receipt.insert()"))

	def test_who_is_purchasing_is_the_reorder_boards_answer(self):
		source = (APP / "inventory_enhancements" / "receipt_price.py").read_text(encoding="utf-8")
		ask = source[
			source.index("def _who_to_ask(po):") : source.index("def _open_lines_needing_price(po):")
		]
		self.assertIn("return _people(reorder_board.recipients())", ask)
		board = (APP / "inventory_enhancements" / "reorder_board.py").read_text(encoding="utf-8")
		self.assertIn("def recipients():", board)
		self.assertIn('ROLE_FIELD = "reorder_alert_role"', board)

	def test_a_held_line_is_dropped_from_the_receipt_and_never_raises(self):
		source = _function_source(PROCUREMENT, "receive_items")
		dropped = source.index('for name in settled["held"]:')
		self.assertIn("wanted.pop(name, None)", source[dropped:])
		self.assertLess(dropped, source.index("make_purchase_receipt(po.name"))
		# Every line held: an answer with no receipt, not a throw, so purchasing's ToDo is kept.
		nothing = source[source.index("if not wanted:") : source.index("receipt = make_purchase_receipt")]
		self.assertIn('"purchase_receipt": None', nothing)
		self.assertIn("return dict(", nothing)
		self.assertNotIn("frappe.throw", nothing)
		# The Stock Scan path refuses instead (it asked first).
		line_helper = _function_source(PROCUREMENT, "receive_order_line")
		self.assertIn("hold_unpriced=0", line_helper)

	def test_the_dialog_asks_which_lines_need_a_price(self):
		source = PROCUREMENT.read_text(encoding="utf-8")
		tree = ast.parse(source)
		node = next(
			n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "lines_needing_price"
		)
		decorator = ast.unparse(node.decorator_list[0])
		self.assertIn("frappe.whitelist", decorator)
		body = ast.unparse(node)
		self.assertIn("po.check_permission('read')", body)
		for key in ("can_price", "can_mark_free", "reason_required", "why"):
			self.assertIn(f"'{key}'", body)
		script = DIALOG_JS.read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.api.procurement.lines_needing_price"', script)
		self.assertIn("if (can_free && cint(line.no_charge)) out.no_charge = 1;", script)
		self.assertIn("else if (can_price && flt(line.price) > 0) out.rate = flt(line.price);", script)
		self.assertIn("no_charge_reason: reason || undefined", script)
		# The dialog opens even when that question fails: the server still refuses, in words.
		self.assertIn("() => open(null)", script)

	def test_the_dialog_never_tells_a_person_who_cannot_price_to_tick_no_charge(self):
		script = DIALOG_JS.read_text(encoding="utf-8")
		note = script[
			script.index("function price_note(needing, answer) {") : script.index("function open_dialog(")
		]
		stuck = note[note.rindex("} else {") :]
		self.assertIn("left unreceived", stuck)
		self.assertIn("purchasing is asked for the price", stuck)
		self.assertNotIn("No charge", stuck)
		# And they get no column to tick: a column exists only for what a person may do.
		self.assertIn("if (can_price) {\n\t\t\tprice_fields.push({", script)
		self.assertIn("if (can_free) {\n\t\t\tprice_fields.push({", script)
		# What the server left unreceived is said in a dialog of its own.
		self.assertIn("if (out.message) {", script)

	def test_stock_scan_asks_before_it_writes_a_log_row(self):
		source = _function_source(STOCK_SCAN, "add")
		prompt = source.index("_order_price_prompt(")
		self.assertLess(prompt, source.index("_begin_log("))
		self.assertIn("rate=order_rate", source)
		self.assertIn("no_charge=cint(no_charge)", source)
		self.assertIn("no_charge_reason=no_charge_reason", source)
		question = _function_source(STOCK_SCAN, "_order_price_prompt")
		self.assertIn('"needs_order_price": True', question)
		self.assertIn('"can_price": block is None', question)
		self.assertIn('"can_mark_free": can_mark_free', question)
		unparsed = ast.unparse(ast.parse(question))
		self.assertIn("frappe.has_permission('Purchase Order', 'read', doc=purchase_order)", unparsed)

	def test_stock_scan_only_takes_an_answer_from_a_person_who_may_give_it(self):
		question = _function_source(STOCK_SCAN, "_order_price_prompt")
		# A price only from one who may price; "no charge" only from one who may, with its reason.
		self.assertIn('if not cint(no_charge) and order_rate not in (None, "") and block is None:', question)
		self.assertIn(
			"if cint(no_charge) and can_mark_free and (cstr(no_charge_reason).strip() or not reason_required):",
			question,
		)
		# A person who can do neither: purchasing is asked right here, where it will be kept.
		self.assertIn("stuck = block is not None and not can_mark_free", question)
		self.assertIn('"asked": receipt_price.ask_purchasing(po, needing) if stuck else []', question)

	def test_receive_order_line_hands_the_answer_on(self):
		source = _function_source(PROCUREMENT, "receive_order_line")
		self.assertIn('"rate": rate, "no_charge": no_charge', source)

	def test_the_phone_offers_only_what_this_person_may_do(self):
		logic = SCAN_LOGIC_JS.read_text(encoding="utf-8")
		for name in ("isOrderPricePrompt", "orderPriceProblem", "orderPriceText"):
			self.assertIn(f"export function {name}(", logic)
		app = SCAN_APP_JS.read_text(encoding="utf-8")
		sheet = app[app.index("askOrderPrice(v, method, args, prompt, ref) {") :]
		sheet = sheet[: sheet.index("saveFailedElsewhere(")]
		self.assertIn("order_rate: answer.rate", sheet)
		self.assertIn("no_charge: 1, no_charge_reason: reason || undefined", sheet)
		self.assertIn("if (reasonRequired && !reason) {", sheet)
		self.assertIn("if (isOrderPricePrompt(result)) {", app)
		# A price box only for a person who may price; "It was free" only for one who may say so.
		self.assertIn("if (canPrice) append(body", sheet)
		self.assertIn("if (canPrice) {\n\t\t\tactions.push({ label: copy.button", sheet)
		self.assertIn("if (canFree) {\n\t\t\tactions.push({ label: copy.free", sheet)
		# Stop is always there, and is the only button for a person who can do neither.
		self.assertIn('actions.push({ label: copy.stop, kind: "ghost" });', sheet)

	def test_the_price_cap_is_one_number_everywhere(self):
		logic = SCAN_LOGIC_JS.read_text(encoding="utf-8")
		cap = int(re.search(r"export const MAX_UNIT_PRICE = (\d+);", logic).group(1))
		self.assertEqual(cap, rules.MAX_UNIT_PRICE)
		self.assertEqual(rules.ZERO_RATE_BELOW, 0.005)
		po_check = (APP / "po_price_check.py").read_text(encoding="utf-8")
		self.assertIn("ZERO_RATE_BELOW = 0.005", po_check)

	def test_the_intake_only_ever_makes_drafts(self):
		"""The packing-slip and vendor-bill intake insert and never submit, so their receipts are
		drafts: the warning speaks when one is saved, and the refusal when a person submits it."""
		for name in ("packing_slip.py", "vendor_bill.py"):
			source = (INTAKE / name).read_text(encoding="utf-8")
			self.assertIn(".insert(", source, name)
			self.assertNotIn(".submit(", source, name)

	def test_documented(self):
		readme = (APP / "inventory_enhancements" / "README.md").read_text(encoding="utf-8")
		self.assertIn("`receipt_price_rules.py`", readme)
		self.assertIn("`receipt_price.py`", readme)
		self.assertIn("lines_needing_price", (APP / "api" / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
