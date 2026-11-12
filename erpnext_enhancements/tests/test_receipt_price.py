# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for "a price is required when a stock item is received" (v1.803.0).

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
		assert doctype == "Item" and pluck == "name", (doctype, pluck)
		wanted = set(filters["name"][1])
		return [code for code in STATE["stock_items"] if code in wanted]

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
	frappe.get_doc = lambda doctype, name: Doc(doctype, name)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.get_fullname = lambda user=None: "Test Receiver"
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
	for name in (
		"erpnext_enhancements.inventory_enhancements.receipt_price_rules",
		"erpnext_enhancements.inventory_enhancements.receipt_price",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.inventory_enhancements import receipt_price as price_module
	from erpnext_enhancements.inventory_enhancements import receipt_price_rules as rules_module

	rules, price = rules_module, price_module


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
	return Doc("Purchase Order", ORDER, items=rows)


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
		self.assertFalse(rules.is_unpriced(line(is_free_item=1), self.STOCK), "a pricing rule's free item")
		self.assertFalse(rules.is_unpriced(line(item_code=None), self.STOCK))

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

	def test_settling_what_the_person_sent(self):
		needing = [
			{"purchase_order_item": "a", "idx": 1, "item_code": PART},
			{"purchase_order_item": "b", "idx": 2, "item_code": PART},
			{"purchase_order_item": "c", "idx": 3, "item_code": PART},
			{"purchase_order_item": "d", "idx": 4, "item_code": PART},
		]
		typed = {"a": {"rate": "7.25"}, "b": {"no_charge": 1}, "d": {"rate": -3}}
		prices, free, missing, problems = rules.settle(needing, typed)
		self.assertEqual(prices, {"a": 7.25})
		self.assertEqual(free, ["b"])
		self.assertEqual([m["purchase_order_item"] for m in missing], ["c"])
		self.assertEqual(problems, ["Row 4 (TST-PUMP-100): Enter a price above zero."])
		# "No charge" wins over a price sent with it.
		self.assertEqual(rules.settle(needing[:1], {"a": {"rate": 5, "no_charge": 1}})[:2], ({}, ["a"]))

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
		self.assertIn("mark it as no charge", text)
		self.assertIn("Ask purchasing", rules.cannot_price_refusal(ORDER))
		self.assertNotIn("19.99", rules.price_comment("Test Receiver", "1"))


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
			self.receipt([line(1, rate=5), line(2, allow_zero_valuation_rate=1), line(3, FEE)])
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

	def test_a_person_who_may_not_edit_the_order_is_told_who_to_ask(self):
		STATE["denied"].add(("Purchase Order", "write"))
		with self.assertRaises(FRAPPE.PermissionError) as caught:
			price.price_order_lines(order(), {"poi-1": 19.99})
		self.assertIn("Ask purchasing", str(caught.exception))
		self.assertEqual(STATE["updates"], [])

	def test_no_prices_touches_nothing(self):
		price.price_order_lines(order(), {})
		self.assertEqual((STATE["updates"], STATE["permission_checks"]), ([], []))

	def test_settling_before_a_receipt(self):
		po = order()
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(po, {"poi-1", "poi-2"}, {})
		self.assertIn("row 1 (TST-PUMP-100) has no price", str(caught.exception))
		self.assertEqual(STATE["updates"], [])
		self.assertEqual(price.settle_before_receipt(po, {"poi-1"}, {"poi-1": {"no_charge": 1}}), ["poi-1"])
		self.assertEqual(STATE["updates"], [], "a free line changes no price")
		self.assertEqual(price.settle_before_receipt(po, {"poi-1"}, {"poi-1": {"rate": "8"}}), [])
		self.assertEqual(STATE["updates"][0][1][0]["rate"], 8.0)
		# Lines that need no price cost nothing: no permission is even asked.
		reset_state()
		self.assertEqual(price.settle_before_receipt(po, {"poi-2", "poi-3"}, {}), [])
		self.assertEqual(STATE["permission_checks"], [])

	def test_a_bad_typed_price_is_refused_in_words(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			price.settle_before_receipt(order(), {"poi-1"}, {"poi-1": {"rate": "0"}})
		self.assertIn("Enter a price above zero", str(caught.exception))


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
		for doctype in ("Purchase Receipt", "Purchase Invoice"):
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
		for doctype in ("Purchase Receipt", "Purchase Invoice", "Delivery Note"):
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
		script = DIALOG_JS.read_text(encoding="utf-8")
		self.assertIn('"erpnext_enhancements.api.procurement.lines_needing_price"', script)
		self.assertIn("out.no_charge = 1", script)
		self.assertIn("out.rate = flt(line.price)", script)
		# The dialog opens even when that question fails: the server still refuses, in words.
		self.assertIn("() => open(null)", script)

	def test_stock_scan_asks_before_it_writes_a_log_row(self):
		source = _function_source(STOCK_SCAN, "add")
		prompt = source.index("_order_price_prompt(")
		self.assertLess(prompt, source.index("_begin_log("))
		self.assertIn("rate=order_rate", source)
		self.assertIn("no_charge=cint(no_charge)", source)
		question = _function_source(STOCK_SCAN, "_order_price_prompt")
		self.assertIn('"needs_order_price": True', question)
		self.assertIn('"can_price": receipt_price.may_price(po)', question)
		self.assertIn(
			"frappe.has_permission('Purchase Order', 'read', doc=purchase_order)",
			ast.unparse(ast.parse(question)),
		)

	def test_receive_order_line_hands_the_answer_on(self):
		source = _function_source(PROCUREMENT, "receive_order_line")
		self.assertIn('"rate": rate, "no_charge": no_charge', source)

	def test_the_phone_can_price_it_mark_it_free_or_stop(self):
		logic = SCAN_LOGIC_JS.read_text(encoding="utf-8")
		for name in ("isOrderPricePrompt", "orderPriceProblem", "orderPriceText"):
			self.assertIn(f"export function {name}(", logic)
		app = SCAN_APP_JS.read_text(encoding="utf-8")
		sheet = app[app.index("askOrderPrice(v, method, args, prompt, ref) {") :]
		sheet = sheet[: sheet.index("saveFailedElsewhere(")]
		self.assertIn("order_rate: answer.rate", sheet)
		self.assertIn("no_charge: 1", sheet)
		self.assertIn('label: "Stop", kind: "ghost"', sheet)
		self.assertIn("if (isOrderPricePrompt(result)) {", app)
		# A person who can't edit the order gets no price box.
		self.assertIn("if (canPrice) append(body", sheet)

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
