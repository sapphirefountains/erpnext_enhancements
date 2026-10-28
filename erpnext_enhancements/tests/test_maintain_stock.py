# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for "Turn on Maintain Stock" (v1.754.0).

Five parts:

* **The rules** (``inventory_enhancements/maintain_stock_rules.py``, no Frappe): every check,
  alone and together; ERPNext's lock list read from a method's source, and a list that grows
  refusing; who may switch; the receipts' guard decisions; the words a person reads.
* **The queries** (``maintain_stock.gather_facts``), run for real on SQLite through
  ``tests/sqlite_sql_helper.py``: an open order line by ERPNext's own Bin rule, documents counted
  once however many lines they hold, drafts and canceled documents ignored.
* **The switch** (``maintain_stock.turn_on``): the Item row locked before anything is read, a
  failed check refusing with nothing written, and a pass writing the flag, the Comment and the
  cache clear, and naming the Stock Adjustment account.
* **The endpoints** (``api/maintain_stock.py``): a person at a screen only (no Authorization
  header, a real session id), the role gate, service accounts refused whatever they hold, write
  permission on the Item, documents named only where the caller may read them, a missing or blank
  item, GET for the check and POST only for the switch.
* **The guards** on the old receipts (cancel, return, bill, and the Landed Cost Voucher and the two
  reposts that would rebuild them), and the wiring: hooks.py points at
  functions that exist, and the form script's roles and endpoints match the server's. The form
  script itself runs under node in ``scripts/test_item_maintain_stock.mjs``.

Fake item codes and document names only: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_maintain_stock -v
"""

import ast
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import sqlite_sql_helper as sqlite_helper

APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"
FORM_JS = APP / "public" / "js" / "item_maintain_stock.js"
HARNESS = REPO_ROOT / "scripts" / "test_item_maintain_stock.mjs"
API_README = APP / "api" / "README.md"
INVENTORY_README = APP / "inventory_enhancements" / "README.md"

#: ERPNext v16.52.1's lock list for Maintain Stock (Item._get_linked_submitted_documents, both
#: lists), read 2026-10-28 from the tag production runs. LOCK_HANDLING must cover exactly these.
ERPNEXT_V16_52_1_LOCK_LIST = [
	"Delivery Note Item",
	"Sales Invoice Item",
	"POS Invoice Item",
	"Purchase Receipt Item",
	"Purchase Invoice Item",
	"Stock Entry Detail",
	"Stock Reconciliation Item",
	"Sales Order Item",
	"Purchase Order Item",
	"Material Request Item",
	"Work Order Item",
	"Product Bundle",
	"BOM",
]

ITEM = "TST-PUMP-100"
OTHER = "TST-TEE-200"
COMPANY = "Test Company"
ADJUSTMENT = "5119 - Stock Adjustment - TC"

rules = None
ms = None
api = None
FRAPPE = None
STATE = None
CONN = None


class _dict(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class StubItem:
	"""Stands in for erpnext's Item: the one method whose source the lock-list reader parses.
	Its shape mirrors ERPNext's, with this file's own words."""

	def _get_linked_submitted_documents(self, changed_fields):
		linked_doctypes = [
			"Delivery Note Item",
			"Sales Invoice Item",
			"POS Invoice Item",
			"Purchase Receipt Item",
			"Purchase Invoice Item",
			"Stock Entry Detail",
			"Stock Reconciliation Item",
		]

		if "is_stock_item" in changed_fields:
			linked_doctypes += [
				"Sales Order Item",
				"Purchase Order Item",
				"Material Request Item",
				"Work Order Item",
				"Product Bundle",
				"BOM",
			]

		return linked_doctypes


class Row(_dict):
	pass


class Doc:
	def __init__(self, doctype, name, data=None):
		self.doctype = doctype
		self.name = name
		self._data = dict(data or {})
		self._data["items"] = [Row(r) for r in self._data.get("items") or []]
		self.flags = _dict()

	def get(self, key, default=None):
		if key == "name":
			return self.name
		return self._data.get(key, default)

	def add_comment(self, comment_type, text):
		STATE["comments"].append((self.doctype, self.name, comment_type, text))

	def check_permission(self, ptype="read"):
		STATE["calls"].append(("check_permission", self.doctype, self.name, ptype))
		if self.doctype == "Item" and ptype == "write" and not STATE["item_write"]:
			raise FRAPPE.PermissionError(f"No permission to {ptype} {self.doctype}")

	def _get_linked_submitted_documents(self, changed_fields):
		STATE["calls"].append(("erpnext_linked", self.name, tuple(changed_fields)))
		return STATE["linked"]


SCHEMA = (
	"create table `tabItem` (name text primary key, item_name text, is_stock_item int default 0, "
	"is_fixed_asset int default 0, variant_of text, has_variants int default 0, modified text)",
	"create table `tabCompany` (name text primary key, stock_adjustment_account text)",
	"create table `tabAccount` (name text primary key, company text, is_group int default 0, "
	"disabled int default 0, account_type text)",
	"create table `tabStock Ledger Entry` (name text, item_code text, voucher_type text, voucher_no text, "
	"voucher_detail_no text, is_cancelled int default 0, creation text)",
	"create table `tabProduct Bundle` (name text, new_item_code text)",
	"create table `tabBOM` (name text, item text, docstatus int, creation text)",
	"create table `tabPurchase Order` (name text, docstatus int, status text, creation text)",
	"create table `tabMaterial Request` (name text, docstatus int, status text, creation text)",
	"create table `tabSales Order` (name text, docstatus int, status text, creation text)",
	"create table `tabPurchase Order Item` (name text, parent text, parenttype text, item_code text, "
	"docstatus int, qty real, received_qty real, delivered_by_supplier int, creation text)",
	"create table `tabMaterial Request Item` (name text, parent text, parenttype text, item_code text, "
	"docstatus int, stock_qty real, ordered_qty real, creation text)",
	"create table `tabSales Order Item` (name text, parent text, parenttype text, item_code text, "
	"docstatus int, qty real, delivered_qty real, delivered_by_supplier int, creation text)",
	"create table `tabPurchase Receipt Item` (name text, parent text, parenttype text, idx int, "
	"item_code text, docstatus int, purchase_order_item text, creation text)",
	"create table `tabPacked Item` (name text, parent text, parenttype text, item_code text, docstatus int, "
	"parent_detail_docname text, creation text)",
	"create table `tabPurchase Receipt` (name text, docstatus int, is_return int default 0, creation text)",
	*(
		f"create table `tab{child}` (name text, parent text, parenttype text, item_code text, "
		"docstatus int, creation text)"
		for child in (
			"Delivery Note Item",
			"Sales Invoice Item",
			"POS Invoice Item",
			"Purchase Invoice Item",
			"Stock Entry Detail",
			"Stock Reconciliation Item",
			"Work Order Item",
		)
	),
)


def _insert(table, **row):
	cols = ", ".join(row)
	marks = ", ".join(f":{k}" for k in row)
	CONN.execute(f"insert into `tab{table}` ({cols}) values ({marks})", row)


def _where(filters):
	clauses, values = [], {}
	for i, (field, cond) in enumerate((filters or {}).items()):
		if isinstance(cond, (tuple, list)) and cond and cond[0] == "in":
			names = list(cond[1])
			marks = ", ".join(f":f{i}_{j}" for j in range(len(names))) or "null"
			clauses.append(f"`{field}` in ({marks})")
			values.update({f"f{i}_{j}": n for j, n in enumerate(names)})
		else:
			clauses.append(f"`{field}` = :f{i}")
			values[f"f{i}"] = cond
	return (" where " + " and ".join(clauses)) if clauses else "", values


class FrappeError(Exception):
	pass


def build_frappe():
	frappe = types.ModuleType("frappe")

	class ValidationError(FrappeError):
		pass

	class PermissionError_(FrappeError):
		pass

	class DoesNotExistError(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _dict
	frappe.flags = _dict()
	frappe.session = _dict(user="buyer@example.com", sid="a1b2c3d4e5")
	frappe.local = types.SimpleNamespace(request=types.SimpleNamespace(headers={}))

	def has_permission(doctype=None, ptype="read", doc=None, **kwargs):
		STATE["calls"].append(("has_permission", doctype, ptype, doc))
		return (doctype, doc) not in STATE["unreadable"] and doctype not in STATE["unreadable"]

	frappe.has_permission = has_permission

	def throw(msg, exc=None, title=None, **kwargs):
		STATE["thrown"].append({"message": msg, "title": title})
		raise (exc or ValidationError)(msg)

	frappe.throw = throw
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.whitelisted = {}

	def whitelist(*args, **kwargs):
		def wrap(fn):
			frappe.whitelisted[fn.__name__] = kwargs.get("methods")
			return fn

		if args and callable(args[0]) and not kwargs:
			return wrap(args[0])
		return wrap

	frappe.whitelist = whitelist

	db = types.SimpleNamespace()
	db.sql_text = sqlite_helper.sql_for(CONN, strict=True)

	def sql(query, values=None, **kwargs):
		STATE["calls"].append(("sql", query))
		return db.sql_text(query, values)

	def get_value(doctype, name, fieldname="name", as_dict=False, for_update=False, **kwargs):
		STATE["calls"].append(("get_value", doctype, name, fieldname, bool(for_update)))
		fields = [fieldname] if isinstance(fieldname, str) else list(fieldname)
		cur = CONN.execute(
			f"select {', '.join(f'`{f}`' for f in fields)} from `tab{doctype}` where name = :n", {"n": name}
		)
		row = cur.fetchone()
		if row is None:
			return None
		if as_dict:
			return _dict(zip(fields, row, strict=True))
		return row[0] if len(fields) == 1 else row

	def set_value(doctype, name, field, value, **kwargs):
		STATE["calls"].append(("set_value", doctype, name, field, value))
		STATE["tick"] += 1
		CONN.execute(
			f"update `tab{doctype}` set `{field}` = :v, modified = :m where name = :n",
			{"v": value, "m": f"2026-10-28 10:00:{STATE['tick']:02d}", "n": name},
		)

	def exists(doctype, name):
		return (
			CONN.execute(f"select 1 from `tab{doctype}` where name = :n", {"n": name}).fetchone() is not None
		)

	db.sql = sql
	db.get_value = get_value
	db.set_value = set_value
	db.exists = exists
	frappe.db = db

	def get_all(doctype, filters=None, fields=None, pluck=None, limit=None, **kwargs):
		STATE["calls"].append(("get_all", doctype, filters))
		cols = [pluck] if pluck else list(fields or ["name"])
		where, values = _where(filters)
		query = f"select {', '.join(f'`{c}`' for c in cols)} from `tab{doctype}`{where}"
		if limit:
			query += f" limit {int(limit)}"
		rows = CONN.execute(query, values).fetchall()
		if pluck:
			return [r[0] for r in rows]
		return [_dict(zip(cols, r, strict=True)) for r in rows]

	frappe.get_all = get_all
	frappe.get_doc = lambda doctype, name: Doc(doctype, name)
	frappe.get_cached_value = lambda doctype, name, field: get_value(doctype, name, field)

	def clear_document_cache(doctype, name=None):
		STATE["calls"].append(("clear_document_cache", doctype, name))

	frappe.clear_document_cache = clear_document_cache

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.format_datetime = lambda dt=None, *a, **k: "10-28-2026 10:00:00"
	utils.get_fullname = lambda user=None: STATE.get("full_name") or (user or "")
	utils.now_datetime = lambda: "2026-10-28 10:00:00"
	frappe.utils = utils
	return frappe, utils


def install_stubs():
	global FRAPPE
	frappe, utils = build_frappe()
	FRAPPE = frappe
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	erpnext = types.ModuleType("erpnext")
	erpnext.get_default_company = lambda: COMPANY
	sys.modules["erpnext"] = erpnext
	path = "erpnext"
	for part in ("stock", "doctype", "item", "item"):
		path = f"{path}.{part}"
		sys.modules.setdefault(path, types.ModuleType(path))
	item_module = sys.modules["erpnext.stock.doctype.item.item"]
	item_module.Item = StubItem
	item_module.get_item_defaults = lambda item_code, company: STATE["item_defaults"].get(item_code) or {}


def reset_state(**overrides):
	global STATE
	STATE = {
		"roles": ["Stock Manager"],
		"calls": [],
		"comments": [],
		"thrown": [],
		"linked": {"doctype": "Purchase Receipt", "docname": "PR-TEST-1"},
		"item_defaults": {},
		"full_name": "Test Buyer",
		"item_write": True,
		"unreadable": set(),
		"tick": 0,
	}
	STATE.update(overrides)
	FRAPPE.flags = _dict()
	FRAPPE.session = _dict(user="buyer@example.com", sid="a1b2c3d4e5")
	FRAPPE.local.request = types.SimpleNamespace(headers={})


def reset_db():
	for (table,) in CONN.execute("select name from sqlite_master where type = 'table'").fetchall():
		CONN.execute(f"delete from `{table}`")
	_insert(
		"Item",
		name=ITEM,
		item_name="Test pump",
		is_stock_item=0,
		is_fixed_asset=0,
		variant_of=None,
		modified="2026-10-01 09:00:00",
	)
	_insert(
		"Item",
		name=OTHER,
		item_name="Test tee",
		is_stock_item=0,
		is_fixed_asset=0,
		variant_of=None,
		modified="2026-10-01 09:00:00",
	)
	_insert("Company", name=COMPANY, stock_adjustment_account=None)
	_insert(
		"Account", name=ADJUSTMENT, company=COMPANY, is_group=0, disabled=0, account_type="Stock Adjustment"
	)
	# A receipt line that came in non-stock, against a fully received order: the common case.
	_insert("Purchase Order", name="PO-TEST-1", docstatus=1, status="To Bill", creation="2026-09-01")
	_insert("Purchase Receipt", name="PR-TEST-1", docstatus=1, is_return=0, creation="2026-09-05")
	_insert(
		"Purchase Order Item",
		name="poi-1",
		parent="PO-TEST-1",
		parenttype="Purchase Order",
		item_code=ITEM,
		docstatus=1,
		qty=4,
		received_qty=4,
		delivered_by_supplier=0,
		creation="2026-09-01",
	)
	_insert(
		"Purchase Receipt Item",
		name="pri-1",
		parent="PR-TEST-1",
		parenttype="Purchase Receipt",
		idx=1,
		item_code=ITEM,
		docstatus=1,
		purchase_order_item="poi-1",
		creation="2026-09-05",
	)


def setUpModule():
	global rules, ms, api, CONN
	CONN = sqlite_helper.connect(*SCHEMA)
	install_stubs()
	reset_state()
	for name in (
		"erpnext_enhancements.inventory_enhancements.maintain_stock_rules",
		"erpnext_enhancements.inventory_enhancements.stock_accounts",
		"erpnext_enhancements.inventory_enhancements.maintain_stock",
		"erpnext_enhancements.api.maintain_stock",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.api import maintain_stock as api_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock as ms_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules_module

	rules, ms, api = rules_module, ms_module, api_module


def tearDownModule():
	CONN.close()


def passing_facts(**overrides):
	facts = {
		"item_code": ITEM,
		"item_name": "Test pump",
		"is_stock_item": 0,
		"is_fixed_asset": 0,
		"variant_of": "",
		"has_variants": 0,
		"packed": {},
		"receipt_returns": {"count": 0, "examples": []},
		"stock_ledger": {"count": 0, "examples": []},
		"bundles": {"count": 0, "examples": []},
		"submitted": {d: {"count": 0, "examples": []} for d in rules.SUBMITTED_DOCTYPES},
		"open": {d: {"count": 0, "examples": []} for d in rules.OPEN_DOCTYPES},
		"receipts": {"count": 3, "examples": []},
		"lock_list": list(ERPNEXT_V16_52_1_LOCK_LIST),
	}
	facts.update(overrides)
	return facts


def by_key(checks):
	return {c["key"]: c for c in checks}


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


class LockListTest(unittest.TestCase):
	def test_handling_covers_exactly_erpnext_v16_52_1(self):
		self.assertEqual(sorted(rules.LOCK_HANDLING), sorted(ERPNEXT_V16_52_1_LOCK_LIST))

	def test_receipts_are_the_only_doctype_let_through(self):
		self.assertEqual(
			[d for d, how in rules.LOCK_HANDLING.items() if how == "guarded"], ["Purchase Receipt Item"]
		)

	def test_reads_both_lists_from_the_method_source(self):
		import inspect

		found = rules.lock_list_from_source(inspect.getsource(StubItem._get_linked_submitted_documents))
		self.assertEqual(found, ERPNEXT_V16_52_1_LOCK_LIST)

	def test_reads_the_installed_method_through_the_module(self):
		self.assertEqual(ms.erpnext_lock_list(), ERPNEXT_V16_52_1_LOCK_LIST)

	def test_unparseable_or_listless_source_is_none(self):
		self.assertIsNone(rules.lock_list_from_source("def broken(:\n\tpass"))
		self.assertIsNone(rules.lock_list_from_source("def f(self):\n\treturn 1\n"))
		self.assertIsNone(rules.lock_list_from_source(None))

	def test_a_new_doctype_in_erpnext_refuses(self):
		grown = [*ERPNEXT_V16_52_1_LOCK_LIST, "Subcontracting Receipt Item"]
		checks = by_key(rules.evaluate(passing_facts(lock_list=grown)))
		self.assertFalse(checks["known_lock"]["ok"])
		self.assertIn("Subcontracting Receipt Item", checks["known_lock"]["detail"])
		self.assertFalse(rules.can_switch(list(checks.values())))

	def test_an_unreadable_list_refuses(self):
		checks = by_key(rules.evaluate(passing_facts(lock_list=None)))
		self.assertFalse(checks["known_lock"]["ok"])
		self.assertIn("Couldn't read", checks["known_lock"]["detail"])


class ChecksTest(unittest.TestCase):
	def test_all_pass_with_receipts_behind_it(self):
		checks = rules.evaluate(passing_facts())
		self.assertTrue(rules.can_switch(checks), [c for c in checks if not c["ok"]])
		self.assertEqual(
			[c["key"] for c in checks],
			[
				"still_off",
				"not_asset",
				"not_variant",
				"not_template",
				"no_ledger",
				"not_bundle",
				"not_bundle_component",
				"no_stock_documents",
				"no_receipt_returns",
				"no_open_orders",
				"known_lock",
			],
		)

	def test_each_failure_fails_only_its_own_check(self):
		one = {"count": 1, "examples": [{"doctype": "X", "name": "X-1"}]}
		cases = {
			"still_off": {"is_stock_item": 1},
			"not_asset": {"is_fixed_asset": 1},
			"not_variant": {"variant_of": "TST-TEMPLATE"},
			"not_template": {"has_variants": 1},
			"not_bundle_component": {"packed": {"Delivery Note": one}},
			"no_ledger": {"stock_ledger": one},
			"not_bundle": {"bundles": one},
			"no_stock_documents": {"submitted": {"Sales Invoice Item": one}},
			"no_receipt_returns": {"receipt_returns": one},
			"no_open_orders": {"open": {"Purchase Order Item": one}},
		}
		for key, overrides in cases.items():
			with self.subTest(key):
				checks = rules.evaluate(passing_facts(**overrides))
				failed = [c["key"] for c in checks if not c["ok"]]
				self.assertEqual(failed, [key])
				self.assertTrue(by_key(checks)[key]["detail"])
				self.assertFalse(rules.can_switch(checks))

	def test_every_submitted_doctype_refuses(self):
		for doctype in rules.SUBMITTED_DOCTYPES:
			with self.subTest(doctype):
				facts = passing_facts(submitted={doctype: {"count": 2, "examples": []}})
				self.assertFalse(by_key(rules.evaluate(facts))["no_stock_documents"]["ok"])

	def test_every_order_doctype_refuses_when_open(self):
		for doctype in rules.OPEN_DOCTYPES:
			with self.subTest(doctype):
				facts = passing_facts(open={doctype: {"count": 1, "examples": []}})
				self.assertFalse(by_key(rules.evaluate(facts))["no_open_orders"]["ok"])

	def test_detail_counts_documents_in_words(self):
		facts = passing_facts(
			submitted={
				"Sales Invoice Item": {
					"count": 2,
					"examples": [{"doctype": "Sales Invoice", "name": "SI-T-1"}],
				},
				"Stock Entry Detail": {
					"count": 1,
					"examples": [{"doctype": "Stock Entry", "name": "SE-T-1"}],
				},
			}
		)
		check = by_key(rules.evaluate(facts))["no_stock_documents"]
		self.assertIn("2 Sales Invoices and 1 Stock Entry use it.", check["detail"])
		self.assertEqual([e["name"] for e in check["examples"]], ["SI-T-1", "SE-T-1"])

	def test_examples_are_capped(self):
		many = {"count": 9, "examples": [{"doctype": "Sales Order", "name": f"SO-T-{i}"} for i in range(9)]}
		check = by_key(rules.evaluate(passing_facts(open={"Sales Order Item": many})))["no_open_orders"]
		self.assertEqual(len(check["examples"]), rules.EXAMPLES)

	def test_refusal_message_names_every_failure(self):
		checks = rules.evaluate(passing_facts(is_stock_item=1, open={"Material Request Item": {"count": 1}}))
		message = rules.refusal_message(ITEM, checks)
		self.assertTrue(message.startswith(f"Maintain Stock was not turned on for {ITEM}."))
		self.assertIn("already on", message)
		self.assertIn("1 Material Request", message)


class WhoTest(unittest.TestCase):
	def test_either_role_may_switch(self):
		self.assertTrue(rules.may_switch("buyer@example.com", ["Stock Manager"]))
		self.assertTrue(rules.may_switch("buyer@example.com", ["Item Manager", "Employee"]))

	def test_other_roles_may_not(self):
		self.assertFalse(rules.may_switch("buyer@example.com", ["Purchase User", "System Manager"]))
		self.assertFalse(rules.may_switch("buyer@example.com", []))

	def test_service_accounts_may_not_whatever_they_hold(self):
		for user in (
			"Administrator",
			"Guest",
			"triton@sapphirefountains.com",
			"mdm@sapphirefountains.com",
			"",
			None,
		):
			with self.subTest(user=user):
				self.assertFalse(rules.may_switch(user, ["Stock Manager", "Item Manager"]))


class WordsTest(unittest.TestCase):
	def test_comment_says_who_when_and_how(self):
		text = rules.switch_comment("Test Buyer", "10-28-2026 10:00:00", 2)
		self.assertIn("Test Buyer turned on Maintain Stock on 10-28-2026 10:00:00", text)
		self.assertIn('"Turn on Maintain Stock" action', text)
		self.assertIn("2 Purchase Receipts", text)
		self.assertNotIn("Purchase Receipt", rules.switch_comment("Test Buyer", "now", 0))

	def test_next_steps_name_the_account_only_when_picked_by_hand(self):
		by_hand = rules.next_steps(ITEM, ADJUSTMENT, False)
		self.assertIn("Stock Reconciliation", by_hand)
		self.assertIn(f"Pick {ADJUSTMENT} as its Difference Account", by_hand)
		self.assertNotIn("Pick", rules.next_steps(ITEM, ADJUSTMENT, True))
		self.assertIn("Pick the Stock Adjustment account", rules.next_steps(ITEM, None, False))

	def test_refusals_keep_the_item_code_as_written(self):
		lines = [{"idx": 1, "item_code": "TST-mixed-Case"}, {"idx": 3, "item_code": OTHER}]
		message = rules.cancel_refusal("PR-TEST-1", lines)
		self.assertIn("Row 1 (TST-mixed-Case), row 3 (TST-TEE-200) came in", message)
		self.assertIn("take that quantity out of stock", message)
		self.assertIn(
			"put that quantity back into stock", rules.cancel_refusal("PR-TEST-2", lines, is_return=True)
		)
		self.assertIn("Row 1 (TST-mixed-Case)", rules.return_refusal(lines))
		self.assertIn("Stock Received But Not Billed", rules.bill_refusal(lines))

	def test_american_spelling(self):
		for path in (
			APP / "inventory_enhancements" / "maintain_stock_rules.py",
			APP / "inventory_enhancements" / "maintain_stock.py",
			APP / "api" / "maintain_stock.py",
			FORM_JS,
		):
			text = path.read_text(encoding="utf-8").replace("is_cancelled", "")
			with self.subTest(path.name):
				self.assertNotRegex(text, r"(?i)cancelled|cancelling|behaviour|colour")


class GuardRulesTest(unittest.TestCase):
	def test_unstocked_lines(self):
		lines = [
			{"idx": 1, "item_code": ITEM, "name": "a"},
			{"idx": 2, "item_code": ITEM, "name": "b"},
			{"idx": 3, "item_code": OTHER, "name": "c"},
		]
		bad = rules.unstocked_lines(lines, {ITEM}, {"b"})
		self.assertEqual([line["name"] for line in bad], ["a"])

	def test_billing_offenders(self):
		rows = [
			{"idx": 1, "item_code": ITEM, "pr_detail": "unstocked", "po_detail": ""},
			{"idx": 2, "item_code": ITEM, "pr_detail": "stocked", "po_detail": ""},
			{"idx": 3, "item_code": ITEM, "pr_detail": "", "po_detail": "mixed-order"},
			{"idx": 4, "item_code": ITEM, "pr_detail": "", "po_detail": "clean-order"},
			{"idx": 5, "item_code": ITEM, "pr_detail": "", "po_detail": "unreceived-order"},
			{"idx": 6, "item_code": OTHER, "pr_detail": "unstocked", "po_detail": ""},
		]
		by_order = {"mixed-order": ["stocked", "unstocked-2"], "clean-order": ["stocked"]}
		bad = rules.billing_offenders(rows, {ITEM}, by_order, {"stocked"})
		self.assertEqual([r["idx"] for r in bad], [1, 3])


class RowJudgementTest(unittest.TestCase):
	def test_billed_lines_precedence(self):
		by_order = {"po-1": ["a", "b"]}
		by_receipt = {("PR-1", ITEM): ["c"], ("PR-1", OTHER): ["d"]}
		self.assertEqual(
			rules.billed_lines({"pr_detail": "x", "po_detail": "po-1"}, by_order, by_receipt), ["x"]
		)
		self.assertEqual(
			rules.billed_lines(
				{"item_code": ITEM, "purchase_receipt": "PR-1", "po_detail": "po-1"}, by_order, by_receipt
			),
			["c", "a", "b"],
		)
		self.assertEqual(
			rules.billed_lines({"item_code": ITEM, "po_detail": "po-none"}, by_order, by_receipt), []
		)

	def test_touches_unstocked(self):
		rows = [
			{"idx": 1, "item_code": ITEM, "lines": ["a"]},
			{"idx": 2, "item_code": ITEM, "lines": ["a", "b"]},
			{"idx": 3, "item_code": ITEM, "lines": []},
			{"idx": 4, "item_code": OTHER, "lines": ["b"]},
		]
		self.assertEqual([r["idx"] for r in rules.touches_unstocked(rows, {ITEM}, {"a"})], [2])

	def test_rebuild_refusal_words(self):
		lines = {"PR-1": [{"idx": 2, "item_code": ITEM}], "PR-2": [{"idx": 1, "item_code": OTHER}]}
		text = rules.rebuild_refusal("Landed Cost Voucher", lines, canceling=True)
		self.assertTrue(
			text.startswith("This Landed Cost Voucher can't be canceled. Purchase Receipt PR-1: row 2")
		)
		self.assertIn("Purchase Receipt PR-2: row 1 (TST-TEE-200)", text)
		self.assertIn("backdated", text)
		self.assertTrue(
			rules.rebuild_refusal("Repost Item Valuation", lines).startswith("This repost can't include it.")
		)


# ---------------------------------------------------------------------------
# The queries, on SQLite
# ---------------------------------------------------------------------------


class QueriesTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()

	def facts(self):
		return ms.gather_facts(ITEM)

	def test_the_common_case_passes(self):
		result = ms.run_checks(ITEM)
		self.assertTrue(result["can_switch"], [c for c in result["checks"] if not c["ok"]])
		self.assertEqual(result["receipts"]["count"], 1)
		self.assertEqual(
			result["receipts"]["examples"], [{"doctype": "Purchase Receipt", "name": "PR-TEST-1"}]
		)
		self.assertEqual(result["locked_by"], {"doctype": "Purchase Receipt", "name": "PR-TEST-1"})

	def test_missing_item(self):
		self.assertIsNone(ms.gather_facts("TST-NOPE"))
		with self.assertRaises(FRAPPE.DoesNotExistError):
			ms.run_checks("TST-NOPE")

	def test_documents_are_counted_once_and_drafts_and_cancels_ignored(self):
		for name, parent, docstatus in (
			("sii-1", "SI-T-1", 1),
			("sii-2", "SI-T-1", 1),
			("sii-3", "SI-T-2", 0),
			("sii-4", "SI-T-3", 2),
		):
			_insert(
				"Sales Invoice Item",
				name=name,
				parent=parent,
				parenttype="Sales Invoice",
				item_code=ITEM,
				docstatus=docstatus,
				creation="2026-09-10",
			)
		group = self.facts()["submitted"]["Sales Invoice Item"]
		self.assertEqual(group, {"count": 1, "examples": [{"doctype": "Sales Invoice", "name": "SI-T-1"}]})

	def test_bom_and_work_order(self):
		_insert("BOM", name="BOM-T-1", item=ITEM, docstatus=1, creation="2026-09-10")
		_insert("BOM", name="BOM-T-2", item=ITEM, docstatus=0, creation="2026-09-11")
		_insert(
			"Work Order Item",
			name="woi-1",
			parent="WO-T-1",
			parenttype="Work Order",
			item_code=ITEM,
			docstatus=1,
			creation="2026-09-10",
		)
		facts = self.facts()
		self.assertEqual(
			facts["submitted"]["BOM"], {"count": 1, "examples": [{"doctype": "BOM", "name": "BOM-T-1"}]}
		)
		self.assertEqual(facts["submitted"]["Work Order Item"]["count"], 1)

	def test_stock_ledger_counts_live_vouchers(self):
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Stock Entry",
			voucher_no="SE-T-1",
			voucher_detail_no="x",
			is_cancelled=0,
			creation="2026-09-10",
		)
		_insert(
			"Stock Ledger Entry",
			name="sle-2",
			item_code=ITEM,
			voucher_type="Stock Entry",
			voucher_no="SE-T-1",
			voucher_detail_no="y",
			is_cancelled=0,
			creation="2026-09-10",
		)
		_insert(
			"Stock Ledger Entry",
			name="sle-3",
			item_code=ITEM,
			voucher_type="Stock Entry",
			voucher_no="SE-T-2",
			voucher_detail_no="z",
			is_cancelled=1,
			creation="2026-09-11",
		)
		self.assertEqual(
			self.facts()["stock_ledger"],
			{"count": 1, "examples": [{"doctype": "Stock Entry", "name": "SE-T-1"}]},
		)

	def test_product_bundle(self):
		_insert("Product Bundle", name=ITEM, new_item_code=ITEM)
		self.assertEqual(self.facts()["bundles"]["count"], 1)
		self.assertFalse(by_key(ms.run_checks(ITEM)["checks"])["not_bundle"]["ok"])

	def _po(self, name, status, qty, received, drop_ship=0, docstatus=1):
		_insert("Purchase Order", name=name, docstatus=docstatus, status=status, creation="2026-09-20")
		_insert(
			"Purchase Order Item",
			name=f"{name}-1",
			parent=name,
			parenttype="Purchase Order",
			item_code=ITEM,
			docstatus=docstatus,
			qty=qty,
			received_qty=received,
			delivered_by_supplier=drop_ship,
			creation="2026-09-20",
		)

	def test_purchase_order_open_by_erpnexts_rule(self):
		self._po("PO-T-OPEN", "To Receive and Bill", 5, 2)
		self._po("PO-T-CLOSED", "Closed", 5, 2)
		self._po("PO-T-DELIVERED", "Delivered", 5, 0)
		self._po("PO-T-DROP", "To Receive and Bill", 5, 0, drop_ship=1)
		self._po("PO-T-DRAFT", "Draft", 5, 0, docstatus=0)
		self._po("PO-T-OVER", "To Bill", 5, 6)
		group = self.facts()["open"]["Purchase Order Item"]
		self.assertEqual(
			group, {"count": 1, "examples": [{"doctype": "Purchase Order", "name": "PO-T-OPEN"}]}
		)

	def test_material_request_open_unless_stopped_or_done(self):
		for name, status, stock_qty, ordered in (
			("MR-T-OPEN", "Pending", 5, 0),
			("MR-T-PART", "Partially Ordered", 5, 3),
			("MR-T-STOPPED", "Stopped", 5, 0),
			("MR-T-DONE", "Ordered", 5, 5),
		):
			_insert("Material Request", name=name, docstatus=1, status=status, creation="2026-09-20")
			_insert(
				"Material Request Item",
				name=f"{name}-1",
				parent=name,
				parenttype="Material Request",
				item_code=ITEM,
				docstatus=1,
				stock_qty=stock_qty,
				ordered_qty=ordered,
				creation="2026-09-20",
			)
		group = self.facts()["open"]["Material Request Item"]
		self.assertEqual(group["count"], 2)
		self.assertEqual(sorted(e["name"] for e in group["examples"]), ["MR-T-OPEN", "MR-T-PART"])

	def test_sales_order_on_hold_still_counts(self):
		for name, status, qty, delivered, drop_ship in (
			("SO-T-OPEN", "To Deliver and Bill", 2, 0, 0),
			("SO-T-HOLD", "On Hold", 2, 0, 0),
			("SO-T-CLOSED", "Closed", 2, 0, 0),
			("SO-T-DONE", "To Bill", 2, 2, 0),
			("SO-T-DROP", "To Deliver and Bill", 2, 0, 1),
		):
			_insert("Sales Order", name=name, docstatus=1, status=status, creation="2026-09-20")
			_insert(
				"Sales Order Item",
				name=f"{name}-1",
				parent=name,
				parenttype="Sales Order",
				item_code=ITEM,
				docstatus=1,
				qty=qty,
				delivered_qty=delivered,
				delivered_by_supplier=drop_ship,
				creation="2026-09-20",
			)
		group = self.facts()["open"]["Sales Order Item"]
		self.assertEqual(sorted(e["name"] for e in group["examples"]), ["SO-T-HOLD", "SO-T-OPEN"])

	def test_a_submitted_return_receipt_refuses(self):
		# An ordinary receipt is the point and passes; a return's GL would be re-booked by any repost.
		self.assertTrue(by_key(ms.run_checks(ITEM)["checks"])["no_receipt_returns"]["ok"])
		for name, docstatus in (("PR-T-RET", 1), ("PR-T-RET-DRAFT", 0), ("PR-T-RET-CANCELED", 2)):
			_insert("Purchase Receipt", name=name, docstatus=docstatus, is_return=1, creation="2026-10-01")
			_insert(
				"Purchase Receipt Item",
				name=f"{name}-1",
				parent=name,
				parenttype="Purchase Receipt",
				idx=1,
				item_code=ITEM,
				docstatus=docstatus,
				purchase_order_item=None,
				creation="2026-10-01",
			)
		facts = self.facts()
		self.assertEqual(
			facts["receipt_returns"],
			{"count": 1, "examples": [{"doctype": "Purchase Receipt", "name": "PR-T-RET"}]},
		)
		check = by_key(ms.run_checks(ITEM)["checks"])["no_receipt_returns"]
		self.assertFalse(check["ok"])
		self.assertIn("1 Purchase Receipt returns it", check["detail"])
		self.assertIn("ordinary repost", check["detail"])

	def test_a_template_refuses(self):
		CONN.execute("update `tabItem` set has_variants = 1 where name = ?", (ITEM,))
		check = by_key(ms.run_checks(ITEM)["checks"])["not_template"]
		self.assertFalse(check["ok"])
		self.assertIn("Switch the variants themselves", check["detail"])

	def test_bundle_component_on_submitted_documents(self):
		for name, parent, parenttype, docstatus in (
			("pk-1", "DN-T-1", "Delivery Note", 1),
			("pk-2", "SI-T-1", "Sales Invoice", 1),
			("pk-3", "SI-T-2", "Sales Invoice", 0),
			("pk-4", "POS-T-1", "POS Invoice", 2),
		):
			_insert(
				"Packed Item",
				name=name,
				parent=parent,
				parenttype=parenttype,
				item_code=ITEM,
				docstatus=docstatus,
				parent_detail_docname="x",
				creation="2026-09-10",
			)
		packed = self.facts()["packed"]
		self.assertEqual(packed["Delivery Note"]["count"], 1)
		self.assertEqual(
			packed["Sales Invoice"],
			{"count": 1, "examples": [{"doctype": "Sales Invoice", "name": "SI-T-1"}]},
		)
		self.assertEqual(packed["POS Invoice"]["count"], 0)
		check = by_key(ms.run_checks(ITEM)["checks"])["not_bundle_component"]
		self.assertFalse(check["ok"])
		self.assertIn("inside a Product Bundle on 1 Delivery Note and 1 Sales Invoice", check["detail"])

	def test_bundle_component_on_open_sales_orders(self):
		for so, status, qty, delivered in (
			("SO-T-OPEN", "To Deliver and Bill", 2, 0),
			("SO-T-CLOSED", "Closed", 2, 0),
			("SO-T-DONE", "Completed", 2, 2),
		):
			_insert("Sales Order", name=so, docstatus=1, status=status, creation="2026-09-20")
			_insert(
				"Sales Order Item",
				name=f"{so}-1",
				parent=so,
				parenttype="Sales Order",
				item_code="TST-BUNDLE",
				docstatus=1,
				qty=qty,
				delivered_qty=delivered,
				delivered_by_supplier=0,
				creation="2026-09-20",
			)
			_insert(
				"Packed Item",
				name=f"{so}-p",
				parent=so,
				parenttype="Sales Order",
				item_code=ITEM,
				docstatus=1,
				parent_detail_docname=f"{so}-1",
				creation="2026-09-20",
			)
		facts = self.facts()
		self.assertEqual(
			facts["packed"]["Sales Order"],
			{"count": 1, "examples": [{"doctype": "Sales Order", "name": "SO-T-OPEN"}]},
		)
		# The open line is the bundle's, not this item's: only the bundle check sees it.
		self.assertEqual(facts["open"]["Sales Order Item"]["count"], 0)
		self.assertFalse(by_key(ms.run_checks(ITEM)["checks"])["not_bundle_component"]["ok"])

	def test_another_items_documents_are_not_this_ones(self):
		_insert(
			"Sales Invoice Item",
			name="sii-9",
			parent="SI-T-9",
			parenttype="Sales Invoice",
			item_code=OTHER,
			docstatus=1,
			creation="2026-09-10",
		)
		self.assertTrue(ms.run_checks(ITEM)["can_switch"])


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


class TurnOnTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()

	def test_switches_comments_clears_and_points_at_the_account(self):
		out = ms.turn_on(ITEM)
		self.assertEqual(
			CONN.execute("select is_stock_item from `tabItem` where name = ?", (ITEM,)).fetchone()[0], 1
		)
		self.assertIn(("set_value", "Item", ITEM, "is_stock_item", 1), STATE["calls"])
		self.assertIn(("clear_document_cache", "Item", ITEM), STATE["calls"])
		self.assertEqual(len(STATE["comments"]), 1)
		doctype, name, kind, text = STATE["comments"][0]
		self.assertEqual((doctype, name, kind), ("Item", ITEM, "Comment"))
		self.assertIn("Test Buyer turned on Maintain Stock", text)
		self.assertIn('"Turn on Maintain Stock" action', text)
		self.assertIn("1 Purchase Receipt", text)
		self.assertEqual(out["account"], ADJUSTMENT)
		self.assertFalse(out["account_is_company_default"])
		self.assertEqual(out["company"], COMPANY)
		self.assertTrue(out["modified"].startswith("2026-10-28 10:00:"))
		self.assertIn(f"Pick {ADJUSTMENT}", out["next_steps"])
		# The other item is untouched.
		self.assertEqual(
			CONN.execute("select is_stock_item from `tabItem` where name = ?", (OTHER,)).fetchone()[0], 0
		)

	def test_comment_is_escaped(self):
		reset_state(full_name="<b>Test</b> Buyer")
		ms.turn_on(ITEM)
		text = STATE["comments"][0][3]
		self.assertIn("&lt;b&gt;Test&lt;/b&gt; Buyer turned on", text)
		self.assertNotIn("<b>", text)

	def test_locks_the_row_before_reading_anything(self):
		ms.turn_on(ITEM)
		reads = [c for c in STATE["calls"] if c[0] in ("sql", "get_all", "get_value", "has_permission")]
		first = reads[0]
		self.assertEqual(first[:3], ("get_value", "Item", ITEM))
		self.assertTrue(first[4], "the first read is the locking one")
		for field in ("is_stock_item", "is_fixed_asset", "variant_of", "has_variants"):
			self.assertIn(field, first[3])
		# The checks judge that row: the Item is not read again before the write.
		write = STATE["calls"].index(("set_value", "Item", ITEM, "is_stock_item", 1))
		item_reads = [c for c in STATE["calls"][:write] if c[0] == "get_value" and c[1] == "Item"]
		self.assertEqual(item_reads, [first])

	def test_the_locked_row_is_what_the_checks_judge(self):
		plain = FRAPPE.db.get_value

		def under_lock(doctype, name, fieldname="name", as_dict=False, for_update=False, **kwargs):
			row = plain(doctype, name, fieldname, as_dict=as_dict, for_update=for_update, **kwargs)
			if for_update and doctype == "Item":
				row["is_stock_item"] = 1  # somebody switched it while this request waited for the lock
			return row

		FRAPPE.db.get_value = under_lock
		try:
			with self.assertRaises(FRAPPE.ValidationError) as caught:
				ms.turn_on(ITEM)
		finally:
			FRAPPE.db.get_value = plain
		self.assertIn("already on", str(caught.exception))
		self.assertEqual(STATE["comments"], [])

	def test_a_failed_check_refuses_and_writes_nothing(self):
		_insert(
			"Delivery Note Item",
			name="dni-1",
			parent="DN-T-1",
			parenttype="Delivery Note",
			item_code=ITEM,
			docstatus=1,
			creation="2026-09-10",
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.turn_on(ITEM)
		self.assertIn("1 Delivery Note uses it. It was posted", str(caught.exception))
		self.assertEqual(STATE["thrown"][-1]["title"], "Maintain Stock not turned on")
		self.assertFalse([c for c in STATE["calls"] if c[0] in ("set_value", "clear_document_cache")])
		self.assertEqual(STATE["comments"], [])

	def test_a_second_click_refuses(self):
		ms.turn_on(ITEM)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.turn_on(ITEM)
		self.assertIn("already on", str(caught.exception))
		self.assertEqual(len(STATE["comments"]), 1)

	def test_a_company_default_needs_no_picking(self):
		CONN.execute("update `tabCompany` set stock_adjustment_account = ?", (ADJUSTMENT,))
		out = ms.turn_on(ITEM)
		self.assertTrue(out["account_is_company_default"])
		self.assertNotIn("Pick", out["next_steps"])

	def test_several_adjustment_accounts_name_none(self):
		_insert(
			"Account",
			name="5120 - Stock Adjustment 2 - TC",
			company=COMPANY,
			is_group=0,
			disabled=0,
			account_type="Stock Adjustment",
		)
		self.assertEqual(ms.stock_adjustment_account(COMPANY), (None, False))

	def test_default_warehouse_is_passed_on(self):
		STATE["item_defaults"][ITEM] = {"default_warehouse": "Test Bin - TC"}
		self.assertEqual(ms.turn_on(ITEM)["warehouse"], "Test Bin - TC")


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


class EndpointTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()

	def test_check_is_read_only_and_get(self):
		result = api.check(ITEM)
		self.assertTrue(result["can_switch"])
		self.assertFalse([c for c in STATE["calls"] if c[0] == "set_value"])
		self.assertEqual(FRAPPE.whitelisted["check"], ["GET", "POST"])

	def test_turn_on_is_post_only(self):
		self.assertEqual(FRAPPE.whitelisted["turn_on"], ["POST"])

	def test_no_role_is_refused_before_anything_is_read(self):
		reset_state(roles=["Purchase User", "System Manager"])
		for endpoint in (api.check, api.turn_on):
			with self.subTest(endpoint.__name__):
				with self.assertRaises(FRAPPE.PermissionError):
					endpoint(ITEM)
		self.assertFalse([c for c in STATE["calls"] if c[0] in ("sql", "get_all", "set_value")])

	def test_service_accounts_are_refused(self):
		reset_state(roles=["Stock Manager", "Item Manager"])
		FRAPPE.session.user = "triton@sapphirefountains.com"
		with self.assertRaises(FRAPPE.PermissionError):
			api.turn_on(ITEM)
		self.assertEqual(
			CONN.execute("select is_stock_item from `tabItem` where name = ?", (ITEM,)).fetchone()[0], 0
		)

	def test_item_manager_alone_may_switch(self):
		reset_state(roles=["Item Manager"])
		self.assertEqual(api.turn_on(ITEM)["is_stock_item"], 1)

	def test_missing_and_blank_items(self):
		with self.assertRaises(FRAPPE.DoesNotExistError):
			api.turn_on("TST-NOPE")
		for blank in (None, "", "   ", ["x"]):
			with self.subTest(blank=blank):
				with self.assertRaises(FRAPPE.ValidationError):
					api.check(blank)

	def test_a_token_or_api_key_is_refused(self):
		FRAPPE.local.request = types.SimpleNamespace(headers={"Authorization": "token abc:def"})
		for endpoint in (api.check, api.turn_on):
			with self.subTest(endpoint.__name__):
				with self.assertRaises(FRAPPE.PermissionError) as caught:
					endpoint(ITEM)
				self.assertIn("signed in as yourself", str(caught.exception))
		self.assertEqual(
			CONN.execute("select is_stock_item from `tabItem` where name = ?", (ITEM,)).fetchone()[0], 0
		)

	def test_no_browser_login_is_refused(self):
		# Frappe gives a token login, a background job and the console sid == user.
		FRAPPE.session.sid = FRAPPE.session.user
		with self.assertRaises(FRAPPE.PermissionError):
			api.turn_on(ITEM)
		reset_state()
		FRAPPE.local.request = None
		with self.assertRaises(FRAPPE.PermissionError):
			api.check(ITEM)

	def test_write_permission_on_the_item_is_needed(self):
		reset_state(item_write=False)
		for endpoint in (api.check, api.turn_on):
			with self.subTest(endpoint.__name__):
				with self.assertRaises(FRAPPE.PermissionError):
					endpoint(ITEM)
		self.assertIn(("check_permission", "Item", ITEM, "write"), STATE["calls"])
		self.assertFalse([c for c in STATE["calls"] if c[0] in ("sql", "set_value")])

	def test_documents_are_named_only_where_readable(self):
		for name, parent in (("sii-1", "SI-T-1"), ("sii-2", "SI-T-2")):
			_insert(
				"Sales Invoice Item",
				name=name,
				parent=parent,
				parenttype="Sales Invoice",
				item_code=ITEM,
				docstatus=1,
				creation="2026-09-10",
			)
		reset_state(unreadable={("Sales Invoice", "SI-T-1"), ("Purchase Receipt", "PR-TEST-1")})
		result = api.check(ITEM)
		check = by_key(result["checks"])["no_stock_documents"]
		self.assertIn("2 Sales Invoices use it", check["detail"])
		self.assertEqual([e["name"] for e in check["examples"]], ["SI-T-2"])
		self.assertEqual(check["hidden_examples"], 1)
		self.assertEqual(result["receipts"]["count"], 1)
		self.assertEqual(result["receipts"]["examples"], [])
		self.assertEqual(result["receipts"]["hidden_examples"], 1)
		self.assertEqual(result["locked_by"], {"doctype": None, "name": None})

	def test_refusal_reaches_the_caller(self):
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Stock Entry",
			voucher_no="SE-T-1",
			voucher_detail_no="x",
			is_cancelled=0,
			creation="2026-09-10",
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			api.turn_on(ITEM)
		self.assertIn("1 stock ledger entry", str(caught.exception))


# ---------------------------------------------------------------------------
# The guards on the old receipts
# ---------------------------------------------------------------------------


def receipt(name="PR-TEST-1", items=None, is_return=0):
	return Doc("Purchase Receipt", name, {"is_return": is_return, "items": items or []})


def make_stock(code=ITEM):
	CONN.execute("update `tabItem` set is_stock_item = 1 where name = ?", (code,))


class GuardsTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()

	def test_cancel_refused_for_a_switched_items_unstocked_line(self):
		make_stock()
		doc = receipt(
			items=[
				{"idx": 1, "name": "pri-1", "item_code": ITEM},
				{"idx": 2, "name": "pri-2", "item_code": OTHER},
			]
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_unstocked_receipt_cancel(doc)
		self.assertIn(
			"Purchase Receipt PR-TEST-1 can't be canceled. Row 1 (TST-PUMP-100) came in",
			str(caught.exception),
		)
		self.assertNotIn(OTHER, str(caught.exception))

	def test_cancel_passes_when_the_line_wrote_stock(self):
		make_stock()
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Purchase Receipt",
			voucher_no="PR-TEST-1",
			voucher_detail_no="pri-1",
			is_cancelled=0,
			creation="2026-09-05",
		)
		ms.refuse_unstocked_receipt_cancel(receipt(items=[{"idx": 1, "name": "pri-1", "item_code": ITEM}]))

	def test_cancel_passes_for_items_that_keep_no_stock(self):
		ms.refuse_unstocked_receipt_cancel(receipt(items=[{"idx": 1, "name": "pri-1", "item_code": ITEM}]))

	def test_cancel_of_a_return_says_it_would_put_stock_back(self):
		make_stock()
		doc = receipt("PR-TEST-RET", items=[{"idx": 1, "name": "ret-1", "item_code": ITEM}], is_return=1)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_unstocked_receipt_cancel(doc)
		self.assertIn("back into stock", str(caught.exception))

	def test_guards_stand_aside_on_flags(self):
		make_stock()
		for flag in ms.SKIP_FLAGS:
			with self.subTest(flag):
				FRAPPE.flags = _dict({flag: True})
				ms.refuse_unstocked_receipt_cancel(
					receipt(items=[{"idx": 1, "name": "pri-1", "item_code": ITEM}])
				)
		FRAPPE.flags = _dict()
		doc = receipt(items=[{"idx": 1, "name": "pri-1", "item_code": ITEM}])
		doc.flags[ms.IGNORE_FLAG] = True
		ms.refuse_unstocked_receipt_cancel(doc)

	def test_return_of_an_unstocked_line_refused(self):
		make_stock()
		doc = receipt(
			"PR-TEST-RET",
			items=[{"idx": 1, "name": "ret-1", "item_code": ITEM, "purchase_receipt_item": "pri-1"}],
			is_return=1,
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_unstocked_receipt_return(doc)
		self.assertIn("This return can't be submitted. Row 1 (TST-PUMP-100)", str(caught.exception))

	def test_return_of_a_stocked_line_and_a_plain_receipt_pass(self):
		make_stock()
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Purchase Receipt",
			voucher_no="PR-TEST-1",
			voucher_detail_no="pri-1",
			is_cancelled=0,
			creation="2026-09-05",
		)
		ms.refuse_unstocked_receipt_return(
			receipt(
				"PR-TEST-RET",
				items=[{"idx": 1, "name": "r", "item_code": ITEM, "purchase_receipt_item": "pri-1"}],
				is_return=1,
			)
		)
		ms.refuse_unstocked_receipt_return(receipt(items=[{"idx": 1, "name": "x", "item_code": ITEM}]))

	def _invoice(self, *rows):
		return Doc("Purchase Invoice", "PI-TEST-1", {"items": list(rows)})

	def test_billing_an_unstocked_receipt_line_refused(self):
		make_stock()
		doc = self._invoice({"idx": 1, "item_code": ITEM, "pr_detail": "pri-1", "po_detail": "poi-1"})
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_billing_unstocked_receipts(doc)
		self.assertIn("can't bill row 1 (TST-PUMP-100)", str(caught.exception))

	def test_billing_from_the_order_refused_when_it_was_received_unstocked(self):
		make_stock()
		doc = self._invoice({"idx": 1, "item_code": ITEM, "pr_detail": "", "po_detail": "poi-1"})
		with self.assertRaises(FRAPPE.ValidationError):
			ms.refuse_billing_unstocked_receipts(doc)

	def test_billing_passes_for_stocked_receipts_unreceived_orders_and_non_stock_items(self):
		ms.refuse_billing_unstocked_receipts(
			self._invoice({"idx": 1, "item_code": ITEM, "pr_detail": "pri-1"})
		)
		make_stock()
		_insert(
			"Purchase Order Item",
			name="poi-2",
			parent="PO-T-2",
			parenttype="Purchase Order",
			item_code=ITEM,
			docstatus=1,
			qty=1,
			received_qty=0,
			delivered_by_supplier=0,
			creation="2026-10-20",
		)
		_insert(
			"Purchase Receipt Item",
			name="pri-9",
			parent="PR-TEST-9",
			parenttype="Purchase Receipt",
			item_code=ITEM,
			docstatus=1,
			purchase_order_item="poi-9",
			creation="2026-10-21",
		)
		_insert(
			"Stock Ledger Entry",
			name="sle-9",
			item_code=ITEM,
			voucher_type="Purchase Receipt",
			voucher_no="PR-TEST-9",
			voucher_detail_no="pri-9",
			is_cancelled=0,
			creation="2026-10-21",
		)
		ms.refuse_billing_unstocked_receipts(
			self._invoice(
				{"idx": 1, "item_code": ITEM, "pr_detail": "pri-9", "po_detail": "poi-9"},
				{"idx": 2, "item_code": ITEM, "pr_detail": "", "po_detail": "poi-2"},
				{"idx": 3, "item_code": ITEM},
			)
		)


class RebuildGuardsTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()

	def _lcv(self, receipts, docstatus=0):
		return Doc(
			"Landed Cost Voucher",
			"LCV-T-1",
			{
				"docstatus": docstatus,
				"purchase_receipts": [
					{"receipt_document_type": t, "receipt_document": r} for t, r in receipts
				],
			},
		)

	def _repost(self, **overrides):
		data = {
			"based_on": "Transaction",
			"voucher_type": "Purchase Receipt",
			"voucher_no": "PR-TEST-1",
			"recreate_stock_ledgers": 1,
		}
		data.update(overrides)
		return Doc("Repost Item Valuation", "RIV-T-1", data)

	def test_landed_cost_refused_on_save_and_on_cancel(self):
		make_stock()
		for docstatus, words in ((0, "can't include it"), (1, "can't include it"), (2, "can't be canceled")):
			with self.subTest(docstatus=docstatus):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					ms.refuse_rebuilding_unstocked_receipts(
						self._lcv([("Purchase Receipt", "PR-TEST-1")], docstatus)
					)
				self.assertIn(
					f"This Landed Cost Voucher {words}. Purchase Receipt PR-TEST-1: row 1 (TST-PUMP-100)",
					str(caught.exception),
				)

	def test_landed_cost_passes_otherwise(self):
		lcv = self._lcv([("Purchase Receipt", "PR-TEST-1"), ("Stock Entry", "SE-T-1")])
		ms.refuse_rebuilding_unstocked_receipts(lcv)  # the item keeps no stock yet
		make_stock()
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Purchase Receipt",
			voucher_no="PR-TEST-1",
			voucher_detail_no="pri-1",
			is_cancelled=0,
			creation="2026-09-05",
		)
		ms.refuse_rebuilding_unstocked_receipts(lcv)  # the line wrote stock

	def test_recreating_a_receipts_stock_ledger_refused(self):
		make_stock()
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_rebuilding_unstocked_receipts(self._repost())
		self.assertIn("This repost can't include it. Purchase Receipt PR-TEST-1", str(caught.exception))
		for overrides in (
			{"recreate_stock_ledgers": 0},
			{"based_on": "Item and Warehouse"},
			{"voucher_type": "Stock Entry", "voucher_no": "SE-T-1"},
		):
			with self.subTest(**overrides):
				ms.refuse_rebuilding_unstocked_receipts(self._repost(**overrides))

	def test_reposting_a_receipts_accounting_ledger_refused(self):
		make_stock()
		doc = Doc(
			"Repost Accounting Ledger",
			"RAL-T-1",
			{
				"vouchers": [
					{"voucher_type": "Sales Invoice", "voucher_no": "SI-T-1"},
					{"voucher_type": "Purchase Receipt", "voucher_no": "PR-TEST-1"},
				]
			},
		)
		with self.assertRaises(FRAPPE.ValidationError):
			ms.refuse_rebuilding_unstocked_receipts(doc)
		doc.flags[ms.IGNORE_FLAG] = True
		ms.refuse_rebuilding_unstocked_receipts(doc)

	def test_a_hand_built_return_is_judged_by_its_receipt(self):
		make_stock()
		doc = receipt("PR-TEST-RET", items=[{"idx": 1, "name": "ret-1", "item_code": ITEM}], is_return=1)
		doc._data["return_against"] = "PR-TEST-1"
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_unstocked_receipt_return(doc)
		self.assertIn("Row 1 (TST-PUMP-100) returns goods", str(caught.exception))
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=ITEM,
			voucher_type="Purchase Receipt",
			voucher_no="PR-TEST-1",
			voucher_detail_no="pri-1",
			is_cancelled=0,
			creation="2026-09-05",
		)
		ms.refuse_unstocked_receipt_return(doc)

	def test_billing_by_the_receipt_header_alone_refused(self):
		make_stock()
		doc = Doc(
			"Purchase Invoice",
			"PI-TEST-1",
			{"items": [{"idx": 1, "item_code": ITEM, "purchase_receipt": "PR-TEST-1"}]},
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_billing_unstocked_receipts(doc)
		self.assertIn("can't bill row 1 (TST-PUMP-100)", str(caught.exception))
		other = Doc(
			"Purchase Invoice",
			"PI-TEST-2",
			{"items": [{"idx": 1, "item_code": OTHER, "purchase_receipt": "PR-TEST-1"}]},
		)
		make_stock(OTHER)
		ms.refuse_billing_unstocked_receipts(other)  # the receipt has no line of that item


# ---------------------------------------------------------------------------
# The wiring
# ---------------------------------------------------------------------------


def _hooks_dict(name):
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return node.value
	raise AssertionError(f"{name} not in hooks.py")


def _key(dict_node, key):
	for k, v in zip(dict_node.keys, dict_node.values, strict=True):
		if isinstance(k, ast.Constant) and k.value == key:
			return v
	return None


class WiringTest(unittest.TestCase):
	def test_doc_events_point_at_real_guards(self):
		events = _hooks_dict("doc_events")
		expected = {
			("Purchase Receipt", "before_cancel"): "refuse_unstocked_receipt_cancel",
			("Purchase Receipt", "validate"): "refuse_unstocked_receipt_return",
			("Purchase Invoice", "validate"): "refuse_billing_unstocked_receipts",
			("Landed Cost Voucher", "validate"): "refuse_rebuilding_unstocked_receipts",
			("Landed Cost Voucher", "before_cancel"): "refuse_rebuilding_unstocked_receipts",
			("Repost Item Valuation", "validate"): "refuse_rebuilding_unstocked_receipts",
			("Repost Accounting Ledger", "validate"): "refuse_rebuilding_unstocked_receipts",
		}
		for (doctype, event), function in expected.items():
			with self.subTest(doctype=doctype, event=event):
				block = _key(events, doctype)
				self.assertIsNotNone(block, doctype)
				value = ast.literal_eval(_key(block, event))
				paths = value if isinstance(value, list) else [value]
				path = f"erpnext_enhancements.inventory_enhancements.maintain_stock.{function}"
				self.assertIn(path, paths)
				self.assertTrue(callable(getattr(ms, function)))
		# A second key for either doctype would silently replace the first.
		keys = [k.value for k in events.keys if isinstance(k, ast.Constant)]
		for doctype in {d for d, _ in expected}:
			self.assertEqual(keys.count(doctype), 1, doctype)

	def test_form_script_is_loaded_on_item(self):
		value = ast.literal_eval(_key(_hooks_dict("doctype_js"), "Item"))
		self.assertIn("public/js/item_maintain_stock.js", value)
		self.assertTrue(FORM_JS.exists())

	def test_form_script_roles_and_endpoints_match_the_server(self):
		source = FORM_JS.read_text(encoding="utf-8")
		roles = re.search(r"const SWITCH_ROLES = \[([^\]]*)\]", source).group(1)
		self.assertEqual(tuple(re.findall(r'"([^"]+)"', roles)), rules.SWITCH_ROLES)
		people = re.search(r"const NOT_A_PERSON = \[([^\]]*)\]", source).group(1)
		self.assertEqual(frozenset(re.findall(r'"([^"]+)"', people)), rules.NOT_A_PERSON)
		for constant, function in (("CHECK", "check"), ("TURN_ON", "turn_on")):
			path = re.search(rf'const {constant} = "([^"]+)"', source).group(1)
			self.assertEqual(path, f"erpnext_enhancements.api.maintain_stock.{function}")
			self.assertIn(function, FRAPPE.whitelisted)

	def test_form_script_stops_the_save_only_when_erpnext_would_refuse(self):
		source = FORM_JS.read_text(encoding="utf-8")
		validate = source[source.index("validate(frm) {") :]
		self.assertIn("if (!res || !res.locked_by) return;", validate)
		self.assertLess(validate.index("res.locked_by"), validate.index("frappe.validated = false"))

	def test_documented(self):
		self.assertIn("`maintain_stock.py`", API_README.read_text(encoding="utf-8"))
		inventory = INVENTORY_README.read_text(encoding="utf-8")
		self.assertIn("`maintain_stock_rules.py`", inventory)
		self.assertIn("`maintain_stock.py`", inventory)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class FormScriptUnderNodeTest(unittest.TestCase):
	def test_the_harness_passes(self):
		run = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=REPO_ROOT
		)
		self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
		self.assertIn("all checks passed", run.stdout)


if __name__ == "__main__":
	unittest.main()
