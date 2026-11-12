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

Added in v1.809.0, on the same stubs: an open Purchase Order or Material Request no longer
refuses (the Bin recounted through ERPNext's own functions, a missing warehouse filled, a partly
received line said so); the relaxed Sales Invoice rule and the Delivery Note guard behind it; and
the bulk page (``maintain_stock_bulk_rules``, ``maintain_stock_bulk``, the ``bulk_...`` endpoints):
the classification rule, the preview held equal to the single-item checks, the queue, the
background job and its restart after a deploy, and the downloaded list.

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
BULK_HARNESS = REPO_ROOT / "scripts" / "test_maintain_stock_switch.mjs"
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
bulk = None
bulk_rules = None
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


DECISION = "Maintain Stock Decision"
DECISION_COLUMNS = (
	"item_code",
	"decision",
	"decided_by",
	"decided_on",
	"status",
	"queued_by",
	"queued_on",
	"relax_sales_invoices",
	"rule",
	"result",
	"invoices_before_switch",
	"switched_on",
)


def _plain(value):
	return value if isinstance(value, (int, float, str)) or value is None else str(value)


class DecisionDoc:
	"""Stands in for a Maintain Stock Decision document: a row of the SQLite table."""

	def __init__(self, name=None, values=None):
		self.doctype = DECISION
		self.name = name
		self.flags = _dict()
		for column in DECISION_COLUMNS:
			setattr(self, column, (values or {}).get(column))

	@classmethod
	def load(cls, name):
		cur = CONN.execute(
			f"select {', '.join(DECISION_COLUMNS)} from `tab{DECISION}` where name = :n", {"n": name}
		)
		return cls(name, dict(zip(DECISION_COLUMNS, cur.fetchone(), strict=True)))

	def is_new(self):
		return self.name is None

	def _values(self):
		return {column: _plain(getattr(self, column)) for column in DECISION_COLUMNS}

	def insert(self, ignore_permissions=False):
		# A name that has nothing to do with the item code, like the doctype's hash naming.
		STATE["tick"] += 1
		self.name = f"msd-{STATE['tick']:04d}"
		STATE["calls"].append(("insert", DECISION, self.name, bool(ignore_permissions)))
		_insert(DECISION, name=self.name, **self._values())
		return self

	def save(self, ignore_permissions=False):
		STATE["calls"].append(("save", DECISION, self.name, bool(ignore_permissions)))
		for column, value in self._values().items():
			CONN.execute(
				f"update `tab{DECISION}` set `{column}` = :v where name = :n", {"v": value, "n": self.name}
			)
		return self


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
	"create table `tabItem` (name text primary key, item_name text, item_group text, "
	"is_stock_item int default 0, disabled int default 0, "
	"is_fixed_asset int default 0, variant_of text, has_variants int default 0, modified text)",
	"create table `tabCompany` (name text primary key, stock_adjustment_account text)",
	"create table `tabAccount` (name text primary key, company text, is_group int default 0, "
	"disabled int default 0, account_type text)",
	"create table `tabStock Ledger Entry` (name text, item_code text, voucher_type text, voucher_no text, "
	"voucher_detail_no text, is_cancelled int default 0, creation text)",
	"create table `tabProduct Bundle` (name text, new_item_code text)",
	"create table `tabBOM` (name text, item text, docstatus int, creation text)",
	"create table `tabPurchase Order` (name text, docstatus int, status text, company text, creation text)",
	"create table `tabMaterial Request` (name text, docstatus int, status text, company text, creation text)",
	"create table `tabSales Invoice` (name text, docstatus int, update_stock int default 0, "
	"posting_date text, creation text)",
	"create table `tabWarehouse` (name text primary key, company text, is_group int default 0, "
	"disabled int default 0)",
	"create table `tabMaintain Stock Decision` (name text primary key, item_code text, decision text, "
	"decided_by text, decided_on text, status text, queued_by text, queued_on text, "
	"relax_sales_invoices int default 0, rule text, result text, "
	"invoices_before_switch int default 0, switched_on text, modified text)",
	"create table `tabSales Order` (name text, docstatus int, status text, creation text)",
	"create table `tabPurchase Order Item` (name text, parent text, parenttype text, idx int, item_code text, "
	"docstatus int, qty real, received_qty real, delivered_by_supplier int, warehouse text, creation text)",
	"create table `tabMaterial Request Item` (name text, parent text, parenttype text, idx int, item_code text, "
	"docstatus int, stock_qty real, ordered_qty real, warehouse text, creation text)",
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
		elif isinstance(cond, (tuple, list)) and cond and cond[0] in (">", "<", ">=", "<="):
			clauses.append(f"`{field}` {cond[0]} :f{i}")
			values[f"f{i}"] = cond[1]
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
		if kwargs.get("as_dict"):
			cur = CONN.execute(re.sub(r"%\((\w+)\)s", r":\1", query), values or {})
			names = [c[0] for c in cur.description]
			return [_dict(zip(names, row, strict=True)) for row in cur.fetchall()]
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

	def set_value(doctype, name, field, value=None, **kwargs):
		if isinstance(field, dict):
			STATE["calls"].append(("set_value", doctype, name, dict(field)))
			for key, val in field.items():
				CONN.execute(
					f"update `tab{doctype}` set `{key}` = :v where name = :n", {"v": _plain(val), "n": name}
				)
			return
		STATE["calls"].append(("set_value", doctype, name, field, value))
		STATE["tick"] += 1
		if kwargs.get("update_modified") is False:
			CONN.execute(f"update `tab{doctype}` set `{field}` = :v where name = :n", {"v": value, "n": name})
			return
		CONN.execute(
			f"update `tab{doctype}` set `{field}` = :v, modified = :m where name = :n",
			{"v": value, "m": f"2026-10-28 10:00:{STATE['tick']:02d}", "n": name},
		)

	def get_single_value(doctype, field):
		return STATE["singles"].get((doctype, field))

	def table_exists(doctype):
		return (
			CONN.execute(
				"select 1 from sqlite_master where type = 'table' and name = :n", {"n": f"tab{doctype}"}
			).fetchone()
			is not None
		)

	def exists(doctype, name):
		return (
			CONN.execute(f"select 1 from `tab{doctype}` where name = :n", {"n": name}).fetchone() is not None
		)

	db.sql = sql
	db.get_value = get_value

	def count(doctype, filters=None):
		if STATE.get("count_raises"):
			raise RuntimeError("the database is away")
		where, values = _where(filters)
		return CONN.execute(f"select count(*) from `tab{doctype}`{where}", values).fetchone()[0]

	def commit():
		STATE["calls"].append(("commit",))

	def rollback():
		STATE["calls"].append(("rollback",))

	def delete(doctype, filters=None):
		if STATE.get("delete_raises"):
			raise RuntimeError("the database is away")
		where, values = _where(filters)
		CONN.execute(f"delete from `tab{doctype}`{where}", values)

	db.delete = delete
	db.count = count
	db.commit = commit
	db.rollback = rollback
	db.set_value = set_value
	db.get_single_value = get_single_value
	db.table_exists = table_exists
	db.exists = exists
	frappe.db = db

	def get_all(doctype, filters=None, fields=None, pluck=None, limit=None, order_by=None, **kwargs):
		STATE["calls"].append(("get_all", doctype, filters))
		cols = [pluck] if pluck else list(fields or ["name"])
		where, values = _where(filters)
		query = f"select {', '.join(f'`{c}`' for c in cols)} from `tab{doctype}`{where}"
		if order_by:
			query += f" order by {order_by}"
		if limit:
			query += f" limit {int(limit)}"
		rows = CONN.execute(query, values).fetchall()
		if pluck:
			return [r[0] for r in rows]
		return [_dict(zip(cols, r, strict=True)) for r in rows]

	frappe.get_all = get_all

	def get_doc(doctype, name):
		if doctype == DECISION:
			return DecisionDoc.load(name)
		return Doc(doctype, name)

	def new_doc(doctype):
		assert doctype == DECISION, doctype
		return DecisionDoc()

	def enqueue(method, **kwargs):
		STATE["enqueued"].append(dict(kwargs, method=method))

	def log_error(message=None, title=None, **kwargs):
		STATE["errors"].append((title, message))

	frappe.get_doc = get_doc
	frappe.new_doc = new_doc
	frappe.enqueue = enqueue
	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.get_cached_value = lambda doctype, name, field: get_value(doctype, name, field)

	def clear_document_cache(doctype, name=None):
		STATE["calls"].append(("clear_document_cache", doctype, name))

	frappe.clear_document_cache = clear_document_cache

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.format_datetime = lambda dt=None, *a, **k: "10-28-2026 10:00:00"
	utils.get_fullname = lambda user=None: STATE.get("full_name") or (user or "")
	utils.now_datetime = lambda: "2026-10-28 10:00:00"
	utils.nowdate = lambda: "2026-10-28"
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
	# ERPNext's own Bin functions, which the switch must call rather than copy. The stubs answer
	# from STATE and record every call, so a test can see which warehouses were recounted.
	balance = types.ModuleType("erpnext.stock.stock_balance")

	def get_ordered_qty(item_code, warehouse):
		STATE["calls"].append(("get_ordered_qty", item_code, warehouse))
		return STATE["ordered"].get((item_code, warehouse), 0)

	def get_indented_qty(item_code, warehouse):
		STATE["calls"].append(("get_indented_qty", item_code, warehouse))
		return STATE["indented"].get((item_code, warehouse), 0)

	def update_bin_qty(item_code, warehouse, qty_dict=None):
		STATE["calls"].append(("update_bin_qty", item_code, warehouse, dict(qty_dict or {})))
		STATE["bins"].setdefault((item_code, warehouse), {}).update(qty_dict or {})

	balance.get_ordered_qty = get_ordered_qty
	balance.get_indented_qty = get_indented_qty
	balance.update_bin_qty = update_bin_qty
	sys.modules["erpnext.stock.stock_balance"] = balance
	sys.modules["erpnext.stock"].stock_balance = balance
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
		"singles": {},
		"enqueued": [],
		"errors": [],
		"ordered": {},
		"indented": {},
		"bins": {},
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
	global rules, ms, api, bulk, bulk_rules, CONN
	CONN = sqlite_helper.connect(*SCHEMA)
	install_stubs()
	reset_state()
	for name in (
		"erpnext_enhancements.inventory_enhancements.maintain_stock_rules",
		"erpnext_enhancements.inventory_enhancements.stock_accounts",
		"erpnext_enhancements.inventory_enhancements.maintain_stock",
		"erpnext_enhancements.inventory_enhancements.maintain_stock_bulk_rules",
		"erpnext_enhancements.inventory_enhancements.maintain_stock_bulk",
		"erpnext_enhancements.api.maintain_stock",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.api import maintain_stock as api_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock as ms_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock_bulk as bulk_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock_bulk_rules as bulk_rules_module
	from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules_module

	rules, ms, api = rules_module, ms_module, api_module
	bulk, bulk_rules = bulk_module, bulk_rules_module


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
		"submitted": {
			d: {"count": 0, "examples": []} for d in (*rules.SUBMITTED_DOCTYPES, rules.SALES_INVOICE)
		},
		"open": {d: {"count": 0, "examples": []} for d in rules.OPEN_RULES},
		"open_lines": [],
		"fill_warehouses": {},
		"sales_invoices_with_stock": {"count": 0, "examples": []},
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
				"sales_invoices",
				"no_open_sales_orders",
				"open_orders_ready",
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
			"no_stock_documents": {"submitted": {"Delivery Note Item": one}},
			"no_receipt_returns": {"receipt_returns": one},
			"sales_invoices": {"submitted": {"Sales Invoice Item": one}},
			"no_open_sales_orders": {"open": {"Sales Order Item": one}},
			# An open order line with no warehouse, and none to give it.
			"open_orders_ready": {
				"open": {"Purchase Order Item": one},
				"open_lines": [
					{
						"doctype": "Purchase Order Item",
						"name": "poi-x",
						"parent": "PO-T-9",
						"idx": 2,
						"warehouse": "",
						"company": COMPANY,
						"received": 0,
					}
				],
			},
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
				self.assertFalse(by_key(rules.evaluate(facts))["no_open_sales_orders"]["ok"])

	def test_detail_counts_documents_in_words(self):
		facts = passing_facts(
			submitted={
				"Delivery Note Item": {
					"count": 2,
					"examples": [{"doctype": "Delivery Note", "name": "DN-T-1"}],
				},
				"Stock Entry Detail": {
					"count": 1,
					"examples": [{"doctype": "Stock Entry", "name": "SE-T-1"}],
				},
			}
		)
		check = by_key(rules.evaluate(facts))["no_stock_documents"]
		self.assertIn("2 Delivery Notes and 1 Stock Entry use it.", check["detail"])
		self.assertEqual([e["name"] for e in check["examples"]], ["DN-T-1", "SE-T-1"])

	def test_examples_are_capped(self):
		many = {"count": 9, "examples": [{"doctype": "Sales Order", "name": f"SO-T-{i}"} for i in range(9)]}
		check = by_key(rules.evaluate(passing_facts(open={"Sales Order Item": many})))["no_open_sales_orders"]
		self.assertEqual(len(check["examples"]), rules.EXAMPLES)

	def test_refusal_message_names_every_failure(self):
		checks = rules.evaluate(passing_facts(is_stock_item=1, open={"Sales Order Item": {"count": 1}}))
		message = rules.refusal_message(ITEM, checks)
		self.assertTrue(message.startswith(f"Maintain Stock was not turned on for {ITEM}."))
		self.assertIn("already on", message)
		self.assertIn("1 Sales Order", message)


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
		check = by_key(result["checks"])["sales_invoices"]
		self.assertFalse(check["ok"])
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
			("Delivery Note", "validate"): "refuse_delivering_old_invoices",
		}
		# And the Item's rows are cleared before Frappe's link check could refuse a delete.
		trash = ast.literal_eval(_key(_key(events, "Item"), "on_trash"))
		self.assertEqual(trash, "erpnext_enhancements.inventory_enhancements.maintain_stock_bulk.forget_item")
		self.assertTrue(callable(bulk.forget_item))
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


# ---------------------------------------------------------------------------
# Open purchase orders and material requests (v1.809.0)
# ---------------------------------------------------------------------------

STORES = "Stores - TC"
MAIN = "Main - TC"


def open_line(doctype="Purchase Order Item", parent="PO-T-9", idx=1, warehouse=STORES, received=0, name=None):
	return {
		"doctype": doctype,
		"name": name or f"{parent}-{idx}",
		"parent": parent,
		"idx": idx,
		"warehouse": warehouse,
		"company": COMPANY,
		"received": received,
	}


def add_warehouse(name, company=COMPANY, is_group=0, disabled=0):
	_insert("Warehouse", name=name, company=company, is_group=is_group, disabled=disabled)


def add_open_po(name="PO-T-OPEN", qty=5, received=0, warehouse=STORES, status="To Receive and Bill", idx=1):
	if not FRAPPE.db.exists("Purchase Order", name):
		_insert(
			"Purchase Order", name=name, docstatus=1, status=status, company=COMPANY, creation="2026-11-01"
		)
	_insert(
		"Purchase Order Item",
		name=f"{name}-{idx}",
		parent=name,
		parenttype="Purchase Order",
		idx=idx,
		item_code=ITEM,
		docstatus=1,
		qty=qty,
		received_qty=received,
		delivered_by_supplier=0,
		warehouse=warehouse,
		creation="2026-11-01",
	)


def add_open_request(name="MR-T-OPEN", stock_qty=4, ordered=0, warehouse=STORES):
	_insert(
		"Material Request", name=name, docstatus=1, status="Pending", company=COMPANY, creation="2026-11-01"
	)
	_insert(
		"Material Request Item",
		name=f"{name}-1",
		parent=name,
		parenttype="Material Request",
		idx=1,
		item_code=ITEM,
		docstatus=1,
		stock_qty=stock_qty,
		ordered_qty=ordered,
		warehouse=warehouse,
		creation="2026-11-01",
	)


def stock_flag(code=ITEM):
	return CONN.execute("select is_stock_item from `tabItem` where name = ?", (code,)).fetchone()[0]


class OpenOrderRulesTest(unittest.TestCase):
	def test_orders_and_requests_are_recounted_and_only_sales_orders_refuse(self):
		self.assertEqual(rules.RECOUNT_DOCTYPES, ("Purchase Order Item", "Material Request Item"))
		self.assertEqual(rules.OPEN_DOCTYPES, ("Sales Order Item",))
		self.assertEqual(rules.OPEN_RULES["Purchase Order Item"]["bin_field"], "ordered_qty")
		self.assertEqual(rules.OPEN_RULES["Material Request Item"]["bin_field"], "indented_qty")

	def test_plan_recounts_each_warehouse_once(self):
		plan = rules.plan_open_orders(
			[
				open_line(idx=1),
				open_line(idx=2),
				open_line(parent="PO-T-10", warehouse=MAIN),
				open_line(doctype="Material Request Item", parent="MR-T-1"),
			],
			{},
		)
		self.assertEqual(
			plan["recount"], [("ordered_qty", STORES), ("ordered_qty", MAIN), ("indented_qty", STORES)]
		)
		self.assertEqual((plan["fill"], plan["stuck"], plan["part_received"]), ([], [], []))

	def test_plan_fills_a_missing_warehouse_or_is_stuck(self):
		line = open_line(warehouse="")
		filled = rules.plan_open_orders([line], {COMPANY: MAIN})
		self.assertEqual([f["warehouse"] for f in filled["fill"]], [MAIN])
		self.assertEqual(filled["recount"], [("ordered_qty", MAIN)])
		stuck = rules.plan_open_orders([line], {})
		self.assertEqual(stuck["stuck"], [line])
		self.assertEqual(stuck["recount"], [])
		# Another company's default is not this order's.
		self.assertEqual(rules.plan_open_orders([line], {"Other Co": MAIN})["stuck"], [line])

	def test_an_open_purchase_order_passes_and_says_what_happens(self):
		facts = passing_facts(
			open={
				"Purchase Order Item": {
					"count": 1,
					"examples": [{"doctype": "Purchase Order", "name": "PO-T-9"}],
				}
			},
			open_lines=[open_line(received=2)],
		)
		checks = rules.evaluate(facts)
		self.assertTrue(rules.can_switch(checks))
		check = by_key(checks)["open_orders_ready"]
		self.assertIn("1 Purchase Order still has some of it to receive or order.", check["detail"])
		self.assertIn("will go into stock when it is received", check["detail"])
		self.assertIn("Purchase Order PO-T-9 row 1 was partly received", check["detail"])
		self.assertIn("Stock Reconciliation", check["detail"])
		self.assertEqual([e["name"] for e in check["examples"]], ["PO-T-9"])

	def test_a_filled_warehouse_is_named(self):
		facts = passing_facts(
			open={"Purchase Order Item": {"count": 1, "examples": []}},
			open_lines=[open_line(warehouse="", idx=3)],
			fill_warehouses={COMPANY: MAIN},
		)
		check = by_key(rules.evaluate(facts))["open_orders_ready"]
		self.assertTrue(check["ok"])
		self.assertIn(f"Purchase Order PO-T-9 row 3 gets {MAIN}", check["detail"])

	def test_a_line_with_no_warehouse_to_take_refuses_in_words(self):
		facts = passing_facts(
			open={"Material Request Item": {"count": 1, "examples": []}},
			open_lines=[open_line(doctype="Material Request Item", parent="MR-T-1", warehouse="")],
		)
		check = by_key(rules.evaluate(facts))["open_orders_ready"]
		self.assertFalse(check["ok"])
		self.assertIn("Material Request MR-T-1 row 1 has no warehouse", check["detail"])
		self.assertIn("Set a default warehouse on the item", check["detail"])

	def test_nothing_open_says_nothing(self):
		self.assertEqual(by_key(rules.evaluate(passing_facts()))["open_orders_ready"]["detail"], "")

	def test_comment_carries_the_open_orders_and_how_it_was_asked(self):
		text = rules.switch_comment(
			"Test Buyer", "today", 0, via=rules.VIA_BULK, open_orders="Orders recounted."
		)
		self.assertIn("through the Switch Items to Maintain Stock page", text)
		self.assertIn("Orders recounted.", text)
		self.assertIn('"Turn on Maintain Stock" action', rules.switch_comment("Test Buyer", "today", 0))


class RelaxedSalesInvoiceRulesTest(unittest.TestCase):
	def facts(self, invoices=2, with_stock=0):
		return passing_facts(
			submitted={"Sales Invoice Item": {"count": invoices, "examples": []}},
			sales_invoices_with_stock={"count": with_stock, "examples": []},
		)

	def test_strict_refuses_and_points_at_the_bulk_page(self):
		check = by_key(rules.evaluate(self.facts()))["sales_invoices"]
		self.assertFalse(check["ok"])
		self.assertIn("2 Sales Invoices use it.", check["detail"])
		self.assertIn("Switch Items to Maintain Stock page", check["detail"])

	def test_relaxed_passes_when_none_updated_stock(self):
		checks = rules.evaluate(self.facts(), relax_sales_invoices=True)
		self.assertTrue(rules.can_switch(checks))
		detail = by_key(checks)["sales_invoices"]["detail"]
		self.assertIn("None updated stock", detail)
		self.assertIn("Delivery Note", detail)

	def test_relaxed_still_refuses_an_invoice_that_updated_stock(self):
		check = by_key(rules.evaluate(self.facts(with_stock=1), relax_sales_invoices=True))["sales_invoices"]
		self.assertFalse(check["ok"])
		self.assertIn("1 of them updated stock", check["detail"])

	def test_relaxing_lets_nothing_else_through(self):
		facts = self.facts()
		facts["submitted"]["Delivery Note Item"] = {"count": 1, "examples": []}
		self.assertFalse(rules.can_switch(rules.evaluate(facts, relax_sales_invoices=True)))


class OpenOrderSwitchTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()
		add_warehouse(STORES)
		add_warehouse(MAIN)

	def test_the_bin_is_recounted_by_erpnext_after_the_flag_is_written(self):
		add_open_po(qty=5, received=2)
		STATE["ordered"][(ITEM, STORES)] = 3
		out = ms.turn_on(ITEM)
		self.assertEqual(stock_flag(), 1)
		self.assertEqual(STATE["bins"], {(ITEM, STORES): {"ordered_qty": 3}})
		calls = STATE["calls"]
		flag = calls.index(("set_value", "Item", ITEM, "is_stock_item", 1))
		cleared = calls.index(("clear_document_cache", "Item", ITEM))
		asked = calls.index(("get_ordered_qty", ITEM, STORES))
		# ERPNext reads the flag through get_cached_value, so the order matters.
		self.assertLess(flag, cleared)
		self.assertLess(cleared, asked)
		self.assertEqual(out["open_orders_recounted"], 1)
		text = STATE["comments"][-1][3]
		self.assertIn("1 Purchase Order still has some of it to receive or order.", text)
		self.assertIn("partly received", text)

	def test_a_material_request_recounts_what_is_requested(self):
		add_open_request()
		STATE["indented"][(ITEM, STORES)] = 4
		ms.turn_on(ITEM)
		self.assertEqual(STATE["bins"], {(ITEM, STORES): {"indented_qty": 4}})

	def test_a_missing_warehouse_takes_the_items_default_and_the_order_is_told(self):
		add_open_po(name="PO-T-BLANK", warehouse=None)
		STATE["item_defaults"][ITEM] = {"default_warehouse": MAIN}
		ms.turn_on(ITEM)
		self.assertEqual(
			CONN.execute(
				"select warehouse from `tabPurchase Order Item` where name = 'PO-T-BLANK-1'"
			).fetchone()[0],
			MAIN,
		)
		self.assertIn(("update_bin_qty", ITEM, MAIN, {"ordered_qty": 0}), STATE["calls"])
		on_order = [c for c in STATE["comments"] if c[0] == "Purchase Order"]
		self.assertEqual(len(on_order), 1)
		self.assertEqual(on_order[0][1], "PO-T-BLANK")
		self.assertIn(f"row 1 was given {MAIN}", on_order[0][3])

	def test_stock_settings_default_is_the_fallback(self):
		add_open_po(name="PO-T-BLANK", warehouse="")
		STATE["singles"][("Stock Settings", "default_warehouse")] = STORES
		ms.turn_on(ITEM)
		self.assertEqual(
			CONN.execute(
				"select warehouse from `tabPurchase Order Item` where name = 'PO-T-BLANK-1'"
			).fetchone()[0],
			STORES,
		)

	def test_no_warehouse_to_take_refuses_and_writes_nothing(self):
		add_open_po(name="PO-T-BLANK", warehouse=None)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.turn_on(ITEM)
		self.assertIn("Purchase Order PO-T-BLANK row 1 has no warehouse", str(caught.exception))
		self.assertEqual(stock_flag(), 0)
		self.assertEqual(STATE["comments"], [])
		self.assertEqual(STATE["bins"], {})

	def test_a_group_disabled_or_foreign_warehouse_is_not_used(self):
		add_warehouse("Group - TC", is_group=1)
		add_warehouse("Old - TC", disabled=1)
		add_warehouse("Away - OC", company="Other Co")
		add_open_po(name="PO-T-BLANK", warehouse=None)
		for bad in ("Group - TC", "Old - TC", "Away - OC", "Nowhere - TC"):
			with self.subTest(bad):
				STATE["item_defaults"][ITEM] = {"default_warehouse": bad}
				self.assertFalse(ms.run_checks(ITEM)["can_switch"])

	def test_an_open_sales_order_still_refuses(self):
		_insert(
			"Sales Order", name="SO-T-1", docstatus=1, status="To Deliver and Bill", creation="2026-11-01"
		)
		_insert(
			"Sales Order Item",
			name="soi-1",
			parent="SO-T-1",
			parenttype="Sales Order",
			item_code=ITEM,
			docstatus=1,
			qty=2,
			delivered_qty=0,
			delivered_by_supplier=0,
			creation="2026-11-01",
		)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.turn_on(ITEM)
		self.assertIn("1 Sales Order still has some of it to deliver", str(caught.exception))

	def test_closed_delivered_dropship_and_draft_orders_are_not_open(self):
		add_open_po(name="PO-T-CLOSED", status="Closed")
		add_open_po(name="PO-T-DONE", qty=5, received=5)
		ms.turn_on(ITEM)
		self.assertEqual(STATE["bins"], {})

	def test_the_dialogs_answer_names_the_open_order(self):
		add_open_po()
		result = ms.run_checks(ITEM)
		self.assertTrue(result["can_switch"])
		check = by_key(result["checks"])["open_orders_ready"]
		self.assertEqual(check["examples"], [{"doctype": "Purchase Order", "name": "PO-T-OPEN"}])
		self.assertIn("still has some of it to receive", check["detail"])


class DeliveryGuardTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		reset_db()
		_insert("Sales Invoice", name="SI-T-OLD", docstatus=1, update_stock=0, posting_date="2026-10-01")
		_insert("Sales Invoice", name="SI-T-NEW", docstatus=1, update_stock=0, posting_date="2026-11-20")

	def decision(self, invoices=2, status="Switched", switched_on="2026-11-12 10:00:00"):
		_insert(
			DECISION,
			name=ITEM,
			item_code=ITEM,
			status=status,
			invoices_before_switch=invoices,
			switched_on=switched_on,
		)

	def note(self, invoice="SI-T-OLD", is_return=0):
		return Doc(
			"Delivery Note",
			"DN-T-1",
			{
				"is_return": is_return,
				"items": [{"idx": 1, "item_code": ITEM, "against_sales_invoice": invoice}],
			},
		)

	def test_delivering_an_invoice_older_than_the_switch_is_refused(self):
		self.decision()
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			ms.refuse_delivering_old_invoices(self.note())
		self.assertIn("can't deliver row 1 (TST-PUMP-100)", str(caught.exception))
		self.assertIn("already left", str(caught.exception))

	def test_the_day_of_the_switch_counts_as_before(self):
		_insert("Sales Invoice", name="SI-T-DAY", docstatus=1, update_stock=0, posting_date="2026-11-12")
		self.decision()
		with self.assertRaises(FRAPPE.ValidationError):
			ms.refuse_delivering_old_invoices(self.note("SI-T-DAY"))

	def test_a_later_invoice_delivers(self):
		self.decision()
		ms.refuse_delivering_old_invoices(self.note("SI-T-NEW"))

	def test_only_an_item_switched_under_the_relaxed_rule_is_judged(self):
		ms.refuse_delivering_old_invoices(self.note())  # no row at all
		self.decision(invoices=0)
		ms.refuse_delivering_old_invoices(self.note())

	def test_a_refused_or_queued_row_is_not_a_switch(self):
		self.decision(status="Refused")
		ms.refuse_delivering_old_invoices(self.note())

	def test_a_return_a_plain_line_and_a_migrate_are_left_alone(self):
		self.decision()
		ms.refuse_delivering_old_invoices(self.note(is_return=1))
		ms.refuse_delivering_old_invoices(
			Doc("Delivery Note", "DN-T-2", {"items": [{"idx": 1, "item_code": ITEM}]})
		)
		FRAPPE.flags.in_migrate = True
		ms.refuse_delivering_old_invoices(self.note())

	def test_a_site_without_the_table_is_left_alone(self):
		CONN.execute(f"alter table `tab{DECISION}` rename to `tabGone`")
		try:
			ms.refuse_delivering_old_invoices(self.note())
		finally:
			CONN.execute(f"alter table `tabGone` rename to `tab{DECISION}`")


# ---------------------------------------------------------------------------
# The bulk page: which items, in words (v1.809.0)
# ---------------------------------------------------------------------------


def passing_checks():
	return rules.evaluate(passing_facts())


class BulkNameRuleTest(unittest.TestCase):
	def rule(self, code, name=None, group="Products", asset=0):
		found = bulk_rules.name_rule(code, name or code, group, asset)
		return found[0] if found else None

	def test_each_rule_fires_on_its_own_signal(self):
		cases = [
			("TST-FOUNTAIN", "Test rental fountain", "Products", 1, "asset"),
			("TST-OLD (deleted)", "TST-OLD (deleted)", "All Item Groups", 0, "deleted"),
			("TST-1", "Old labor line (deleted) ", "All Item Groups", 0, "deleted"),
			("TST-VALVE", "VALVE, BALL, 1 INCH", "Service", 0, "group"),
			("SRV-900", "TEST - SETUP", "All Item Groups", 0, "code"),
			("TST-2", "BUILD - Consulting", "All Item Groups", 0, "stream"),
			("TST-3", "Rent - Insurance", "All Item Groups", 0, "stream"),
			("TST-4", "Internal- Purchasing", "All Item Groups", 0, "stream"),
			("PDT-9000", "PDT-9000 SERVICE - TEST MATERIALS", "All Item Groups", 0, "stream"),
			("TST-5", "Late fee", "All Item Groups", 0, "word"),
			("TST-6", "TEST LABOR SENIOR TECHNICIAN", "Products", 0, "word"),
			("TST-7", "Freight", "Products", 0, "word"),
			("TST-8", "Deposits Received", "Products", 0, "word"),
			("TST-9", "Manual Mileage", "Products", 0, "word"),
			("TST-10", "Custom Amount", "Products", 0, "word"),
			("TST-11", "TAX, SALES", "Products", 0, "word"),
			("TST-12", "STAPLES, STANDARD", "Office", 0, "consumable"),
			("TST-13", "Test Portal", "All Item Groups", 0, "catch_all"),
			("900123", "900123", "Products", 0, "unclear"),
			("Customer Water Feature", "Customer Water Feature", "Products", 0, "reserved"),
		]
		for code, name, group, asset, expected in cases:
			with self.subTest(name):
				self.assertEqual(self.rule(code, name, group, asset), expected)

	def test_parts_are_parts_wherever_they_were_filed(self):
		for code, name, group in (
			("PDT-9050", "ANEMOMETER, PADDLE, 3-24 VDC", "All Item Groups"),
			("PDT-9151", 'DECK JET, ADJUSTABLE, 1" NPT, SS', "All Item Groups"),
			("PDT-9003", "PDT-9003 SPLASH TEST INDUSTRIAL", "All Item Groups"),
			("TST-20", "PUMP, TEST 150, 24 V", "Pumps"),
			("TST-21", "ADAPTER, 1 INCH INTERNAL TO NPT EXTERNAL", "Products"),
			("TST-22", "SENSOR, CURRENT, 20 A", "Electrical"),
			("TST-23", "LABEL, SHIPPING, BLANK", "Products"),
			("TST-25", "PARENT, RENTED-LOOKING, FEEDER", "Products"),
			# Words left out of the list on purpose, each for a real part.
			("TST-26", "NOZZLE TIPS, BRASS", "Nozzles"),
			("TST-27", "COUPLING, REPAIR, PVC, 1 INCH", "Products"),
			("TST-28", "WINTERIZING PLUG, FOR DECK JET", "Products"),
			("TST-29", "CONTROLLER, SOLAR CHARGE, 20 A", "Electrical"),
			# A size and a wildcard model number are not placeholders.
			("TST-30", "HOODIE, HI VIS, XX-LARGE", "Products"),
			("Test light kit (LR375XXX)", "LIGHT, TEST FOUNTAIN KIT (LR375XXX)", "Lights"),
			("TST-31", "CARTRIDGE, INK, 952XL", "Products"),
			# A name that is the code, but says what it is.
			("2 POLE TERMINALS", "2 POLE TERMINALS", "Electrical"),
		):
			with self.subTest(name):
				self.assertIsNone(self.rule(code, name, group))

	def test_the_six_names_from_the_brief(self):
		cases = (
			("SRV-202", "SRV-202 SERVICE - LABOR SENIOR TECHNICIAN", "All Item Groups", "code"),
			("TST-40", "SERVICE - LABOR SENIOR TECHNICIAN", "All Item Groups", "stream"),
			("Late fee", "Late fee", "All Item Groups", "word"),
			("PDT-0050", "ANEMOMETER, PADDLE, 3-24 VDC", "All Item Groups", None),
			("SRV-414", "RENT - FOUNTAIN PILLAR", "All Item Groups", "code"),
			("TST-41", "BUILD -  FOUNTAIN MATERIALS & EQUIPMENT", "All Item Groups", "stream"),
			("PDT-0003", "PDT-0003 SPLASH WIZARD INDUSTRIAL", "All Item Groups", None),
		)
		for code, name, group, expected in cases:
			with self.subTest(name):
				self.assertEqual(self.rule(code, name, group), expected)

	def test_office_supplies_stay_until_someone_says_otherwise(self):
		"""Nik's decision of 2026-11-12: office supplies stay non-stock by default."""
		self.assertEqual(bulk_rules.CONSUMABLE_GROUPS, ("Office",))
		for name in (
			"TRAIL MIX, MULTIPACK, 15 OZ",
			"HAND SOAP, LIQUID, PACK OF 6",
			"TOWEL, PAPER, 2-PLY, 12 ROLLS",
			"CARTRIDGE, INK, CYAN",
			"MOUSE, USB WIRED, 3 BUTTON",
			"INK CARTRIDGE, DESIGN JET",
		):
			with self.subTest(name):
				self.assertEqual(self.rule("TST-50", name, "Office"), "consumable")
		reason = bulk_rules.name_rule("TST-50", "HAND SOAP", "Office")[1]
		self.assertIn("Office item group", reason)
		self.assertIn("Choose Switch if we do count it", reason)
		# The flip: with the tuple emptied, an office item is judged like any other.
		kept = bulk_rules.CONSUMABLE_GROUPS
		bulk_rules.CONSUMABLE_GROUPS = ()
		try:
			self.assertIsNone(self.rule("TST-50", "HAND SOAP, LIQUID, PACK OF 6", "Office"))
		finally:
			bulk_rules.CONSUMABLE_GROUPS = kept

	def test_a_placeholder_a_copy_and_a_nameless_item_never_switch(self):
		cases = (
			("500977", "500977", "only a number"),
			("501-006", "501-006", "only a number"),
			("PDT-00051", "PDT-00051", "only its code"),
			("PDT-00XX", "PDT-00XX", '"XX" where a number belongs'),
			("PDT-9008 TEST BYPASS, 5HP - copy", "PDT-9008 TEST BYPASS, 5HP - copy", 'ends in "copy"'),
			("TST-60", "VALVE, BALL (copy)", 'ends in "copy"'),
			("TST-61", "VALVE, BALL - Copy 2", 'ends in "copy"'),
			("Test Item-1", "Test Item-1", '"test item"'),
			("TST-62", "PLACEHOLDER FOR NEW PUMP", '"placeholder"'),
			("TST-63", "TBD", '"tbd"'),
			("TST-64", "OLD VALVE DO NOT USE", '"do not use"'),
		)
		for group in ("Products", "All Item Groups", "Pumps"):
			for code, name, says in cases:
				with self.subTest(name=name, group=group):
					found = bulk_rules.name_rule(code, name, group)
					self.assertEqual(found[0], "unclear")
					self.assertIn(says, found[1])
					self.assertIn("Choose Switch if it is a part.", found[1])
		# "COPY PAPER" and "PAPER, COPY" are paper.
		self.assertIsNone(bulk_rules.unclear("TST-65", "PAPER, COPY, LETTER"))
		self.assertIsNone(bulk_rules.unclear("TST-66", "COPY PAPER, LETTER"))

	def test_the_catch_all_group_switches_only_what_carries_a_product_code(self):
		for code, name in (
			("Test Compliance Portal", "Test Compliance Portal"),
			("TestVendor", "TestVendor"),
			("Tips", "Tips"),
			("LED Fuse Holder", "LED Fuse Holder"),
		):
			with self.subTest(name):
				found = bulk_rules.name_rule(code, name, "All Item Groups")
				self.assertEqual(found[0], "catch_all")
				self.assertIn("its code is not a product code (PDT-)", found[1])
		# The same names anywhere else are judged by their words alone.
		self.assertIsNone(self.rule("LED Fuse Holder", "LED Fuse Holder", "Electrical"))
		self.assertIsNone(self.rule("pdt-9050", "ANEMOMETER, PADDLE", "All Item Groups"), "any case")

	def test_the_widened_service_words(self):
		for name in (
			"Fountain installation",
			"Pond cleaning, monthly",
			"Pump repairs",
			"Annual maintenance",
			"City permit",
			"Returning customer discount",
			"Fuel surcharge",
			"Monthly retainer",
			"Extended warranty, 2 years",
			"Winterization",
			"Subcontractor, electrical",
			"Misc",
			"Fountain design",
			"Controller programming",
			"Operator training",
			"Annual inspection",
			"Software license",
			"Hotel",
			"Per diem",
			"Restocking credit",
			"Shipping and handling",
		):
			with self.subTest(name):
				self.assertEqual(self.rule("TST-70", name, "Products"), "word")
		self.assertNotIn("tips", bulk_rules.SERVICE_WORDS)
		self.assertNotIn("repair", bulk_rules.SERVICE_WORDS)

	def test_an_item_this_app_needs_non_stock_is_never_offered(self):
		found = bulk_rules.name_rule("Customer Water Feature", "Customer Water Feature", "Products")
		self.assertEqual(found[0], "reserved")

	def test_what_leans_to_stay_and_is_known_to(self):
		"""These are parts the rule keeps non-stock. A part left non-stock is where it is today,
		and one click on the page switches it; the test is here so nobody is surprised."""
		self.assertEqual(self.rule("TST-80", "SERVICE VALVE, 1 INCH", "Plumbing"), "word")
		self.assertEqual(self.rule("TST-81", "HOSE, DELIVERY, 2 INCH", "Plumbing"), "word")

	def test_the_reason_is_a_sentence_about_this_item(self):
		self.assertEqual(
			bulk_rules.name_rule("TST-5", "Late fee", "Products")[1],
			'Its name has the word "fee": not a part.',
		)
		self.assertIn('starts with "RENT -"', bulk_rules.name_rule("TST-3", "Rent - Insurance", "x")[1])
		self.assertIn("Service item group", bulk_rules.name_rule("TST", "VALVE", "Service")[1])


class BulkOutcomeTest(unittest.TestCase):
	PART = {"item_code": "TST-20", "item_name": "PUMP, TEST", "item_group": "Pumps", "is_fixed_asset": 0}
	FEE = {"item_code": "TST-5", "item_name": "Late fee", "item_group": "Products", "is_fixed_asset": 0}
	ASSET = {
		"item_code": "TST-F",
		"item_name": "Test fountain",
		"item_group": "Products",
		"is_fixed_asset": 1,
	}

	def failing(self):
		return rules.evaluate(passing_facts(submitted={"Delivery Note Item": {"count": 1, "examples": []}}))

	def test_the_three_outcomes(self):
		self.assertEqual(bulk_rules.outcome(self.PART, passing_checks())["outcome"], "switch")
		self.assertEqual(bulk_rules.outcome(self.FEE, passing_checks())["outcome"], "stay")
		blocked = bulk_rules.outcome(self.PART, self.failing())
		self.assertEqual(blocked["outcome"], "blocked")
		self.assertIn("1 Delivery Note uses it.", blocked["reason"])

	def test_a_persons_choice_beats_the_rule_both_ways(self):
		kept = bulk_rules.outcome(self.PART, passing_checks(), "Stay non-stock")
		self.assertEqual((kept["outcome"], kept["overridden"]), ("stay", True))
		self.assertEqual(kept["reason"], "You chose to keep it non-stock.")
		forced = bulk_rules.outcome(self.FEE, passing_checks(), "Switch")
		self.assertEqual((forced["outcome"], forced["overridden"], forced["rule"]), ("switch", True, "stay"))
		self.assertIn("You chose to switch it", forced["reason"])

	def test_a_choice_never_beats_a_failing_check_or_an_asset(self):
		self.assertEqual(bulk_rules.outcome(self.FEE, self.failing(), "Switch")["outcome"], "blocked")
		asset = bulk_rules.outcome(self.ASSET, passing_checks(), "Switch")
		self.assertEqual(asset["outcome"], "stay")
		self.assertIn("fixed asset", asset["reason"])

	def test_no_checks_is_not_a_pass(self):
		self.assertEqual(bulk_rules.outcome(self.PART, [])["outcome"], "blocked")

	def test_blocked_only_by_sales_invoices(self):
		only = rules.evaluate(passing_facts(submitted={"Sales Invoice Item": {"count": 3, "examples": []}}))
		self.assertTrue(bulk_rules.blocked_only_by_sales_invoices(only))
		self.assertFalse(bulk_rules.blocked_only_by_sales_invoices(self.failing()))
		self.assertFalse(bulk_rules.blocked_only_by_sales_invoices(passing_checks()))


class BulkListTest(unittest.TestCase):
	ROWS = [
		{
			"item_code": "TST-A",
			"item_name": "Part",
			"item_group": "Pumps",
			"outcome": "switch",
			"reason": "ok",
		},
		{"item_code": "TST-B", "outcome": "stay", "reason": "fee", "decision": "Stay non-stock"},
		{"item_code": "TST-C", "outcome": "blocked", "reason": "blocked"},
		{"item_code": "TST-D", "outcome": "switch", "status": "Queued"},
		{"item_code": "TST-E", "outcome": "switch", "status": "Refused", "result": "no"},
	]

	def test_counts_and_what_an_apply_queues(self):
		self.assertEqual(bulk_rules.summarize(self.ROWS), {"switch": 3, "stay": 1, "blocked": 1})
		self.assertEqual(bulk_rules.to_apply(self.ROWS), ["TST-A", "TST-E"])
		# What the page showed can only narrow the server's list, never add to it.
		self.assertEqual(bulk_rules.to_apply(self.ROWS, picked=["TST-E", "TST-B", "TST-NOPE"]), ["TST-E"])
		self.assertEqual(bulk_rules.to_apply(self.ROWS, picked=[]), [])

	def test_before_and_after(self):
		rows = {row["item_code"]: row for row in bulk_rules.list_rows(self.ROWS)}
		self.assertEqual((rows["TST-A"]["before"], rows["TST-A"]["after"]), ("Off", "On"))
		self.assertEqual(rows["TST-B"]["after"], "Off")
		self.assertEqual(rows["TST-C"]["outcome_label"], "Can't switch yet")
		self.assertEqual(rows["TST-E"]["after"], "Off")
		done = bulk_rules.list_rows([{"item_code": "TST-F", "outcome": "switch", "status": "Switched"}])
		self.assertEqual(done[0]["after"], "On")

	def test_csv_is_quoted_and_cannot_run_a_formula(self):
		self.assertEqual(bulk_rules.csv_cell('PLATE, 1" THICK'), '"PLATE, 1"" THICK"')
		for risky in ("=1+1", "+1", "-2", "@x"):
			self.assertTrue(bulk_rules.csv_cell(risky).startswith("\"'"), risky)
		text = bulk_rules.to_csv(self.ROWS)
		lines = text.split("\r\n")
		self.assertEqual(lines[0].split(",")[0], '"Item"')
		self.assertEqual(len(lines), len(self.ROWS) + 2)  # the heading, the rows, the trailing newline
		self.assertIn('"TST-A","Part","Pumps","Off","On","Will switch","ok"', lines[1])

	def test_a_refusal_is_kept_as_plain_words(self):
		self.assertEqual(bulk_rules.refused_result("Not  <b>turned</b> on.<br>Why"), "Not turned on. Why")
		self.assertEqual(bulk_rules.refused_result(""), "The switch was refused.")
		self.assertLessEqual(len(bulk_rules.refused_result("x" * 5000)), 1000)


# ---------------------------------------------------------------------------
# The bulk page: the reads, the queue and the job
# ---------------------------------------------------------------------------

FEE = "TST-FEE-1"
STOCKED = "TST-STOCKED"
OFF = "TST-DISABLED"


def bulk_db():
	"""The usual two parts, plus a fee line, a stock item and a disabled item."""
	reset_db()
	add_warehouse(STORES)
	add_warehouse(MAIN)
	CONN.execute("update `tabItem` set item_group = 'Pumps'")
	_insert("Item", name=FEE, item_name="Late fee", item_group="All Item Groups", modified="2026-10-01")
	_insert("Item", name=STOCKED, item_name="Stocked part", item_group="Pumps", is_stock_item=1, modified="x")
	_insert("Item", name=OFF, item_name="Old part", item_group="Pumps", disabled=1, modified="x")


def block_with_delivery_note(code=OTHER):
	_insert(
		"Delivery Note Item",
		name=f"dni-{code}",
		parent="DN-T-1",
		parenttype="Delivery Note",
		item_code=code,
		docstatus=1,
		creation="2026-09-10",
	)


def add_invoice(code=OTHER, name="SI-T-1", update_stock=0):
	_insert("Sales Invoice", name=name, docstatus=1, update_stock=update_stock, posting_date="2026-09-10")
	_insert(
		"Sales Invoice Item",
		name=f"sii-{name}",
		parent=name,
		parenttype="Sales Invoice",
		item_code=code,
		docstatus=1,
		creation="2026-09-10",
	)


def decision_row(code):
	cur = CONN.execute(
		f"select status, result, queued_by, invoices_before_switch, decision from `tab{DECISION}` "
		"where item_code = ? order by name",
		(code,),
	)
	row = cur.fetchone()
	return (
		None
		if row is None
		else _dict(zip(("status", "result", "queued_by", "invoices", "decision"), row, strict=True))
	)


class BulkFactsTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		bulk_db()

	def assert_same_as_single(self):
		items = bulk._items()
		facts = bulk.bulk_facts(items)
		for item in items:
			code = item["name"]
			single = ms.gather_facts(code)
			many = facts[code]
			with self.subTest(code):
				for key in (
					"stock_ledger",
					"bundles",
					"receipts",
					"receipt_returns",
					"sales_invoices_with_stock",
				):
					self.assertEqual(many[key]["count"], single[key]["count"], key)
				for group in ("submitted", "open", "packed"):
					self.assertEqual(
						{k: v["count"] for k, v in many[group].items()},
						{k: v["count"] for k, v in single[group].items()},
						group,
					)
				self.assertEqual(many["open_lines"], single["open_lines"])
				self.assertEqual(many["fill_warehouses"], single["fill_warehouses"])
				for flag in ("is_stock_item", "is_fixed_asset", "variant_of", "has_variants"):
					self.assertEqual(many[flag], single[flag], flag)
				# And so the checks agree, which is the point.
				self.assertEqual(
					[(c["key"], c["ok"]) for c in rules.evaluate(many)],
					[(c["key"], c["ok"]) for c in rules.evaluate(single)],
				)

	def test_a_quiet_database(self):
		self.assert_same_as_single()

	def test_every_kind_of_document(self):
		block_with_delivery_note(OTHER)
		add_invoice(OTHER, "SI-T-1")
		add_invoice(OTHER, "SI-T-2", update_stock=1)
		add_invoice(ITEM, "SI-T-3")
		add_open_po(qty=5, received=2)
		add_open_po(name="PO-T-BLANK", warehouse=None)
		add_open_request()
		STATE["item_defaults"][ITEM] = {"default_warehouse": MAIN}
		_insert("Product Bundle", name="PB-1", new_item_code=OTHER)
		_insert("BOM", name="BOM-1", item=OTHER, docstatus=1, creation="2026-09-10")
		_insert(
			"Stock Ledger Entry",
			name="sle-1",
			item_code=OTHER,
			voucher_type="Stock Entry",
			voucher_no="SE-T-1",
			voucher_detail_no="x",
			is_cancelled=0,
			creation="2026-09-10",
		)
		_insert("Purchase Receipt", name="PR-T-RET", docstatus=1, is_return=1, creation="2026-09-12")
		_insert(
			"Purchase Receipt Item",
			name="pri-ret",
			parent="PR-T-RET",
			parenttype="Purchase Receipt",
			idx=1,
			item_code=OTHER,
			docstatus=1,
			creation="2026-09-12",
		)
		_insert(
			"Packed Item",
			name="pk-1",
			parent="DN-T-9",
			parenttype="Delivery Note",
			item_code=OTHER,
			docstatus=1,
			creation="2026-09-12",
		)
		self.assert_same_as_single()

	def test_only_enabled_non_stock_items_are_listed(self):
		self.assertEqual([item["name"] for item in bulk._items()], [FEE, ITEM, OTHER])


class BulkPreviewTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		bulk_db()

	def rows(self, **kwargs):
		return {row["item_code"]: row for row in bulk.preview(**kwargs)["rows"]}

	def test_three_outcomes_with_reasons(self):
		block_with_delivery_note(OTHER)
		data = bulk.preview()
		rows = {row["item_code"]: row for row in data["rows"]}
		self.assertEqual(rows[ITEM]["outcome"], "switch")
		self.assertEqual(rows[FEE]["outcome"], "stay")
		self.assertIn('the word "fee"', rows[FEE]["reason"])
		self.assertEqual(rows[FEE]["rule"], "stay")
		self.assertEqual(rows[OTHER]["outcome"], "blocked")
		self.assertIn("1 Delivery Note uses it.", rows[OTHER]["reason"])
		self.assertEqual(data["counts"], {"switch": 1, "stay": 1, "blocked": 1})
		self.assertNotIn(STOCKED, rows)
		self.assertNotIn(OFF, rows)

	def test_the_preview_writes_nothing(self):
		bulk.preview()
		self.assertFalse([c for c in STATE["calls"] if c[0] in ("set_value", "insert", "save")])
		self.assertEqual(STATE["enqueued"], [])

	def test_an_open_purchase_order_does_not_block(self):
		add_open_po()
		self.assertEqual(self.rows()[ITEM]["outcome"], "switch")

	def test_a_choice_is_kept_and_changes_the_outcome(self):
		bulk.set_decision(FEE, "Switch")
		bulk.set_decision(ITEM, "Stay non-stock")
		rows = self.rows()
		self.assertEqual(
			(rows[FEE]["outcome"], rows[FEE]["decision"], rows[FEE]["overridden"]), ("switch", "Switch", True)
		)
		self.assertEqual(rows[ITEM]["outcome"], "stay")
		self.assertEqual(stock_flag(ITEM), 0)  # a choice changes no Item
		bulk.set_decision(FEE, "")
		self.assertEqual(self.rows()[FEE]["outcome"], "stay")
		self.assertEqual(decision_row(FEE).decision, "")

	def test_a_choice_must_be_one_of_the_two(self):
		with self.assertRaises(FRAPPE.ValidationError):
			bulk.set_decision(FEE, "Delete")
		with self.assertRaises(FRAPPE.DoesNotExistError):
			bulk.set_decision("TST-NOPE", "Switch")

	def test_sales_invoices_block_until_the_box_is_ticked(self):
		add_invoice(OTHER, "SI-T-1")
		strict = bulk.preview()
		self.assertEqual({r["item_code"]: r["outcome"] for r in strict["rows"]}[OTHER], "blocked")
		self.assertEqual(strict["sales_invoices_only"], 1)
		relaxed = bulk.preview(relax_sales_invoices=1)
		self.assertEqual({r["item_code"]: r["outcome"] for r in relaxed["rows"]}[OTHER], "switch")
		self.assertTrue(relaxed["relax_sales_invoices"])

	def test_an_invoice_that_updated_stock_is_never_counted_as_relaxable(self):
		add_invoice(OTHER, "SI-T-1", update_stock=1)
		self.assertEqual(bulk.preview()["sales_invoices_only"], 0)
		self.assertEqual(self.rows(relax_sales_invoices=1)[OTHER]["outcome"], "blocked")

	def test_a_fee_with_invoices_is_not_counted_as_held_back(self):
		add_invoice(FEE, "SI-T-1")
		self.assertEqual(bulk.preview()["sales_invoices_only"], 0)


class BulkApplyTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		bulk_db()

	def test_queues_only_what_will_switch_and_starts_one_job(self):
		block_with_delivery_note(OTHER)
		out = bulk.queue_switch()
		self.assertEqual(out, {"queued": 1})
		self.assertEqual(decision_row(ITEM).status, "Queued")
		self.assertEqual(decision_row(ITEM).queued_by, "buyer@example.com")
		self.assertIsNone(decision_row(OTHER))
		self.assertIsNone(decision_row(FEE))
		self.assertEqual(stock_flag(ITEM), 0)  # queuing switches nothing
		self.assertEqual(len(STATE["enqueued"]), 1)
		job = STATE["enqueued"][0]
		self.assertEqual(job["method"], bulk.JOB_METHOD)
		self.assertEqual(
			(job["job_id"], job["deduplicate"], job["enqueue_after_commit"]), (bulk.JOB_ID, True, True)
		)
		self.assertEqual(job["queue"], "long")

	def test_what_the_page_showed_can_only_narrow_it(self):
		out = bulk.queue_switch(picked=[OTHER, FEE, "TST-NOPE"])
		self.assertEqual(out, {"queued": 1})
		self.assertEqual(decision_row(OTHER).status, "Queued")
		self.assertIsNone(decision_row(ITEM))
		self.assertIsNone(decision_row(FEE))

	def test_nothing_to_queue_starts_no_job(self):
		self.assertEqual(bulk.queue_switch(picked=[]), {"queued": 0})
		self.assertEqual(STATE["enqueued"], [])

	def test_the_job_switches_through_the_checked_switch_and_commits_each_item(self):
		bulk.queue_switch()
		STATE["calls"].clear()
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(OTHER), stock_flag(FEE)), (1, 1, 0))
		for code in (ITEM, OTHER):
			self.assertEqual(decision_row(code).status, "Switched")
			self.assertEqual(decision_row(code).result, "Maintain Stock is on.")
		# The locking read of the single switch, once an item, and a commit after each.
		locks = [c for c in STATE["calls"] if c[0] == "get_value" and c[1] == "Item" and c[4]]
		self.assertEqual(sorted(c[2] for c in locks), sorted([ITEM, OTHER]))
		self.assertEqual(STATE["calls"].count(("commit",)), 2)
		comments = {c[1]: c[3] for c in STATE["comments"] if c[0] == "Item"}
		self.assertIn("through the Switch Items to Maintain Stock page", comments[ITEM])
		self.assertIn("Test Buyer turned on Maintain Stock", comments[OTHER])

	def test_an_item_that_fails_when_the_job_reaches_it_is_refused_and_the_rest_go_on(self):
		bulk.queue_switch()
		block_with_delivery_note(ITEM)  # something happened between the click and the job
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(OTHER)), (0, 1))
		refused = decision_row(ITEM)
		self.assertEqual(refused.status, "Refused")
		self.assertIn("1 Delivery Note uses it.", refused.result)
		self.assertIn(("rollback",), STATE["calls"])
		self.assertEqual(decision_row(OTHER).status, "Switched")
		self.assertEqual(STATE["errors"], [])  # a refusal is not an error

	def test_an_unexpected_failure_is_logged_and_does_not_stop_the_run(self):
		bulk.queue_switch()
		real = ms.turn_on

		def flaky(item_code, **kwargs):
			if item_code == ITEM:
				raise RuntimeError("the database went away")
			return real(item_code, **kwargs)

		ms.turn_on = flaky
		try:
			bulk.process_queue()
		finally:
			ms.turn_on = real
		self.assertEqual(decision_row(ITEM).status, "Refused")
		self.assertIn("Error Log", decision_row(ITEM).result)
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertEqual(stock_flag(OTHER), 1)

	def test_a_run_killed_by_a_deploy_is_picked_up_from_the_rows(self):
		bulk.queue_switch()
		STATE["enqueued"].clear()  # the deploy flushed the queue: the job never ran
		bulk.resume_queue()
		self.assertEqual([job["method"] for job in STATE["enqueued"]], [bulk.JOB_METHOD])
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(OTHER)), (1, 1))
		STATE["enqueued"].clear()
		bulk.resume_queue()
		self.assertEqual(STATE["enqueued"], [])  # nothing queued, nothing started

	def test_running_the_job_twice_switches_nothing_twice(self):
		bulk.queue_switch()
		bulk.process_queue()
		comments = len(STATE["comments"])
		bulk.process_queue()
		self.assertEqual(len(STATE["comments"]), comments)

	def test_resume_never_raises(self):
		STATE["count_raises"] = True
		bulk.resume_queue()
		self.assertEqual(len(STATE["errors"]), 1)

	def test_stop_takes_the_queue_off_and_leaves_the_items(self):
		bulk.queue_switch()
		self.assertEqual(bulk.stop(), {"stopped": 2})
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(OTHER)), (0, 0))
		self.assertEqual(decision_row(ITEM).status, "")
		self.assertEqual(bulk.status(), {"queued": 0, "switched": 0, "refused": 0})

	def test_a_queued_item_is_not_queued_again_and_its_choice_is_locked(self):
		bulk.queue_switch()
		STATE["enqueued"].clear()
		self.assertEqual(bulk.queue_switch(), {"queued": 0})
		with self.assertRaises(FRAPPE.ValidationError):
			bulk.set_decision(ITEM, "Stay non-stock")

	def test_the_relaxed_switch_is_recorded_for_the_delivery_guard(self):
		add_invoice(OTHER, "SI-T-1")
		add_invoice(OTHER, "SI-T-2")
		self.assertEqual(bulk.queue_switch(), {"queued": 1})  # strict: the invoiced item waits
		self.assertEqual(bulk.queue_switch(relax_sales_invoices=1), {"queued": 1})
		bulk.process_queue()
		self.assertEqual(stock_flag(OTHER), 1)
		self.assertEqual(decision_row(OTHER).invoices, 2)
		self.assertEqual(decision_row(ITEM).invoices, 0)
		comment = next(c[3] for c in STATE["comments"] if c[1] == OTHER)
		self.assertIn("2 Sales Invoices billed it while it kept no stock", comment)
		# And the guard reads exactly that row.
		note = Doc(
			"Delivery Note",
			"DN-T-1",
			{"items": [{"idx": 1, "item_code": OTHER, "against_sales_invoice": "SI-T-1"}]},
		)
		with self.assertRaises(FRAPPE.ValidationError):
			ms.refuse_delivering_old_invoices(note)

	def test_results_and_status_follow_the_run(self):
		bulk.queue_switch()
		self.assertEqual(bulk.status(), {"queued": 2, "switched": 0, "refused": 0})
		block_with_delivery_note(ITEM)
		bulk.process_queue()
		self.assertEqual(bulk.status(), {"queued": 0, "switched": 1, "refused": 1})
		results = {row["item_code"]: row for row in bulk.results()}
		self.assertEqual(results[OTHER]["status"], "Switched")
		self.assertEqual(results[ITEM]["status"], "Refused")
		listed = {row["item_code"]: row for row in bulk_rules.list_rows(bulk.results())}
		self.assertEqual((listed[OTHER]["after"], listed[ITEM]["after"]), ("On", "Off"))


class BulkEndpointTest(unittest.TestCase):
	def setUp(self):
		reset_state(roles=["Item Manager"])
		bulk_db()

	ENDPOINTS = (
		"bulk_preview",
		"bulk_decide",
		"bulk_apply",
		"bulk_stop",
		"bulk_status",
		"bulk_results",
		"bulk_list_csv",
	)

	def test_every_endpoint_is_gated(self):
		calls = {
			"bulk_decide": lambda: api.bulk_decide(ITEM, "Switch"),
			"bulk_apply": lambda: api.bulk_apply(),
		}
		gates = (
			(
				"an API key",
				lambda: setattr(
					FRAPPE.local, "request", types.SimpleNamespace(headers={"Authorization": "token a:b"})
				),
			),
			("no role", lambda: STATE.update(roles=["Purchase User"])),
			("a service account", lambda: FRAPPE.session.update(user="Administrator")),
			("no write on Item", lambda: STATE.update(unreadable={"Item"})),
		)
		for name in self.ENDPOINTS:
			for label, close in gates:
				with self.subTest(endpoint=name, gate=label):
					reset_state(roles=["Item Manager"])
					close()
					with self.assertRaises(FRAPPE.PermissionError):
						calls.get(name, getattr(api, name))()
		self.assertEqual(STATE["enqueued"], [])

	def test_writes_are_post_only_and_reads_may_be_get(self):
		for name in ("bulk_decide", "bulk_apply", "bulk_stop"):
			self.assertEqual(FRAPPE.whitelisted[name], ["POST"], name)
		for name in ("bulk_preview", "bulk_status", "bulk_results", "bulk_list_csv"):
			self.assertEqual(FRAPPE.whitelisted[name], ["GET", "POST"], name)

	def test_the_page_round_trip(self):
		data = api.bulk_preview()
		self.assertEqual(data["counts"], {"switch": 2, "stay": 1, "blocked": 0})
		api.bulk_decide(FEE, "Switch")
		out = api.bulk_apply(items=f'["{ITEM}", "{FEE}"]')
		self.assertEqual(out, {"queued": 2})
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(FEE), stock_flag(OTHER)), (1, 1, 0))
		self.assertEqual(api.bulk_status()["switched"], 2)

	def test_a_list_that_cannot_be_read_is_refused_not_ignored(self):
		for junk in ("not json", '{"a": 1}'):
			with self.assertRaises(FRAPPE.ValidationError):
				api.bulk_apply(items=junk)
		self.assertEqual(STATE["enqueued"], [])

	def test_the_list_downloads_as_csv(self):
		out = api.bulk_list_csv()
		self.assertEqual(out["filename"], "maintain-stock-list-2026-10-28.csv")
		self.assertIn(
			'"Item","Item name","Item group","Maintain Stock before","Maintain Stock after"', out["csv"]
		)
		self.assertIn(f'"{FEE}","Late fee","All Item Groups","Off","Off","Stays non-stock"', out["csv"])
		self.assertTrue(api.bulk_list_csv(which="results")["filename"].startswith("maintain-stock-results-"))


class DecisionRowFollowsTheItemTest(unittest.TestCase):
	"""An Item with a row can still be renamed, merged and deleted (v1.809.0, found in review)."""

	def setUp(self):
		reset_state()
		bulk_db()

	def rows(self, code):
		return CONN.execute(
			f"select name, status from `tab{DECISION}` where item_code = ?", (code,)
		).fetchall()

	def rename(self, old, new):
		"""What Frappe's rename does to this table: the Link follows, the row's name stays."""
		CONN.execute("update `tabItem` set name = ? where name = ?", (new, old))
		CONN.execute(f"update `tab{DECISION}` set item_code = ? where item_code = ?", (new, old))

	def test_a_renamed_item_keeps_its_row(self):
		bulk.set_decision(ITEM, "Stay non-stock")
		name = self.rows(ITEM)[0][0]
		self.assertNotEqual(name, ITEM)
		self.rename(ITEM, "TST-PUMP-NEW")
		rows = {row["item_code"]: row for row in bulk.preview()["rows"]}
		self.assertEqual(rows["TST-PUMP-NEW"]["decision"], "Stay non-stock")
		bulk.set_decision("TST-PUMP-NEW", "")
		self.assertEqual(
			[row[0] for row in self.rows("TST-PUMP-NEW")], [name], "the same row, not a second one"
		)

	def test_a_renamed_item_can_be_queued_and_switched(self):
		bulk.set_decision(ITEM, "Switch")
		self.rename(ITEM, "TST-PUMP-NEW")
		bulk.queue_switch()
		bulk.process_queue()
		self.assertEqual(stock_flag("TST-PUMP-NEW"), 1)
		self.assertEqual(len(self.rows("TST-PUMP-NEW")), 1)

	def test_two_rows_after_a_merge_and_the_one_that_matters_speaks(self):
		bulk.queue_switch()
		bulk.process_queue()  # both items Switched, a row each
		bulk.set_decision(FEE, "Stay non-stock")
		self.rename(FEE, "TST-GONE")
		# The merge: the fee's row is re-pointed at the surviving item, which already has one.
		CONN.execute(f"update `tab{DECISION}` set item_code = ? where item_code = ?", (ITEM, "TST-GONE"))
		self.assertEqual(len(self.rows(ITEM)), 2)
		self.assertEqual(bulk._decisions()[ITEM]["status"], "Switched")
		self.assertEqual(bulk._row(ITEM).status, "Switched")

	def test_the_best_row(self):
		rows = [
			{"name": "a", "status": "", "modified": "2026-11-12 09:00:00"},
			{"name": "b", "status": "Refused", "modified": "2026-11-12 08:00:00"},
			{"name": "c", "status": "Switched", "modified": "2026-11-01 08:00:00"},
		]
		self.assertEqual(bulk_rules.best_row(rows)["name"], "c")
		self.assertEqual(bulk_rules.best_row(rows[:2])["name"], "b")
		self.assertEqual(bulk_rules.best_row(rows[:1])["name"], "a")
		self.assertIsNone(bulk_rules.best_row([]))

	def test_the_later_of_two_switches_guards_the_delivery_note(self):
		for name, day in (("msd-a", "2026-10-01 10:00:00"), ("msd-b", "2026-11-12 10:00:00")):
			_insert(
				DECISION,
				name=name,
				item_code=ITEM,
				status="Switched",
				invoices_before_switch=1,
				switched_on=day,
			)
		_insert("Sales Invoice", name="SI-T-MID", docstatus=1, update_stock=0, posting_date="2026-10-20")
		note = Doc(
			"Delivery Note",
			"DN-T-1",
			{"items": [{"idx": 1, "item_code": ITEM, "against_sales_invoice": "SI-T-MID"}]},
		)
		with self.assertRaises(FRAPPE.ValidationError):
			ms.refuse_delivering_old_invoices(note)

	def test_deleting_an_item_removes_its_rows_first(self):
		bulk.set_decision(ITEM, "Switch")
		bulk.set_decision(OTHER, "Switch")
		bulk.forget_item(Doc("Item", ITEM))
		self.assertEqual(self.rows(ITEM), [])
		self.assertEqual(len(self.rows(OTHER)), 1)

	def test_the_cleanup_never_raises(self):
		STATE["delete_raises"] = True
		bulk.forget_item(Doc("Item", ITEM))
		self.assertEqual(len(STATE["errors"]), 1)

	def test_a_second_job_does_not_write_refused_over_switched(self):
		"""Two jobs can overlap across a deploy. The second waits for the Item's row lock, finds
		the item already on, and must leave the first job's Switched row alone."""
		add_invoice(OTHER, "SI-T-1")
		bulk.queue_switch(relax_sales_invoices=1)
		queued = FRAPPE.get_all(
			DECISION,
			filters={"status": "Queued"},
			fields=["name", "item_code", "queued_by", "relax_sales_invoices"],
		)
		bulk.process_queue()
		for row in queued:  # the second job, holding the rows it read before the first one ran
			self.assertFalse(bulk._switch_one(row))
		for code in (ITEM, OTHER):
			self.assertEqual(decision_row(code).status, "Switched", code)
		self.assertEqual(decision_row(OTHER).invoices, 1, "the Delivery Note guard still has its row")


class ItemCreatorsSayStockOnPurposeTest(unittest.TestCase):
	"""New items default to Maintain Stock on (v1.809.0), so code that creates an Item must say
	which it means. A new creator that leaves the field out fails here, before it can quietly
	make a labor line a stock item."""

	#: Dict literals with ``"doctype": "Item"`` that are not a creation at all.
	NOT_A_CREATION = {"api/desk_search.py", "form_kit/config/item.py"}

	def sites(self):
		dicts, new_docs = [], []
		for path in sorted(APP.rglob("*.py")):
			rel = path.relative_to(APP).as_posix()
			if rel.startswith(("tests/", "patches/")):
				continue
			tree = ast.parse(path.read_text(encoding="utf-8"))
			for node in ast.walk(tree):
				if isinstance(node, ast.Dict):
					keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
					pairs = zip(node.keys, node.values, strict=True)
					if any(
						isinstance(k, ast.Constant)
						and k.value == "doctype"
						and isinstance(v, ast.Constant)
						and v.value == "Item"
						for k, v in pairs
					):
						dicts.append((rel, "is_stock_item" in keys, None in keys))
				elif (
					isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr == "new_doc"
					and node.args
					and isinstance(node.args[0], ast.Constant)
					and node.args[0].value == "Item"
				):
					new_docs.append(rel)
		return dicts, new_docs

	def test_every_item_built_from_a_dict_names_the_field(self):
		dicts, _new_docs = self.sites()
		self.assertTrue(dicts)
		for rel, names_it, spreads in dicts:
			if rel in self.NOT_A_CREATION:
				continue
			with self.subTest(rel):
				if rel == "water_engineering/catalog_seed.py":
					# Built from a plan spread into the dict; the plan names the field.
					self.assertTrue(spreads)
					source = (APP / rel).read_text(encoding="utf-8")
					self.assertIn('"is_stock_item": 0,', source[source.index("def _item_plan(") :])
				else:
					self.assertTrue(names_it, f"{rel} creates an Item without saying is_stock_item")

	def test_every_item_built_with_new_doc_sets_the_field(self):
		_dicts, new_docs = self.sites()
		self.assertEqual(new_docs, ["api/stock_scan.py"])
		source = _function_source_of(APP / "api" / "stock_scan.py", "_quick_item")
		self.assertIn("item.is_stock_item = 1", source)

	def test_the_quickbooks_import_still_says_non_stock(self):
		source = (APP / "quickbooks_online" / "core" / "mapping.py").read_text(encoding="utf-8")
		mapper = source[source.index("def _map_item(") :]
		self.assertIn('"is_stock_item": 0,', mapper[: mapper.index("\ndef ")])

	def test_a_person_sees_the_box_wherever_they_create_an_item(self):
		"""ERPNext's Item quick entry shows Maintain Stock (``allow_in_quick_entry`` on the field,
		read at v16.54.0), and so does the guided Item flow's first step. Nothing in this app's
		fixtures hides the box or takes it out of quick entry."""
		import json

		setters = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
		mine = {
			row["property"]: row["value"]
			for row in setters
			if row.get("doc_type") == "Item" and row.get("field_name") == "is_stock_item"
		}
		self.assertEqual(mine.get("default"), "1")
		for hides in ("hidden", "allow_in_quick_entry", "read_only"):
			self.assertNotIn(hides, mine)
		self.assertLessEqual(len(mine["description"]), 90)
		flow = (APP / "form_kit" / "config" / "item.py").read_text(encoding="utf-8")
		create = flow[flow.index('"create"') :]
		self.assertIn('"is_stock_item"', create[: create.index("]")])

	def test_an_assistant_must_say_it_too(self):
		gate = (APP / "assistant_tools" / "_gate.py").read_text(encoding="utf-8")
		self.assertIn(
			'if tool_name == "create_document" and doctype == "Item" and "is_stock_item" not in data:', gate
		)


def _function_source_of(path, name):
	source = path.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(source)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(source, node)
	raise AssertionError(name)


class JobTimeoutException(Exception):
	"""Stands in for rq.timeouts.JobTimeoutException: the same name, and an ``Exception``."""


class BulkJobTimeoutTest(unittest.TestCase):
	def setUp(self):
		reset_state()
		bulk_db()

	def test_a_timeout_leaves_the_item_queued_and_ends_the_job(self):
		bulk.queue_switch()
		real = ms.turn_on

		def out_of_time(item_code, **kwargs):
			raise JobTimeoutException("Task exceeded maximum timeout value")

		ms.turn_on = out_of_time
		try:
			with self.assertRaises(JobTimeoutException):
				bulk.process_queue()
		finally:
			ms.turn_on = real
		for code in (ITEM, OTHER):
			self.assertEqual(decision_row(code).status, "Queued", code)
		self.assertIn(("rollback",), STATE["calls"])
		self.assertEqual(STATE["errors"], [], "a timeout is not an item's failure")
		# The next job, started by the scheduler, takes them up again.
		bulk.resume_queue()
		self.assertEqual(len(STATE["enqueued"]), 2)  # the apply's own, and the restart
		bulk.process_queue()
		self.assertEqual((stock_flag(ITEM), stock_flag(OTHER)), (1, 1))

	def test_only_the_workers_own_timeout_counts(self):
		self.assertTrue(bulk._is_timeout(JobTimeoutException("x")))
		self.assertFalse(bulk._is_timeout(TimeoutError("x")))
		self.assertFalse(bulk._is_timeout(RuntimeError("JobTimeoutException")))


class SecondRecountTest(unittest.TestCase):
	"""The Bin is recounted once more after the commit, to see a receipt that committed while the
	switch was running."""

	def setUp(self):
		reset_state()
		reset_db()
		add_warehouse(STORES)
		self.callbacks = []
		FRAPPE.db.after_commit = types.SimpleNamespace(add=self.callbacks.append)
		FRAPPE.db.savepoint = lambda name: STATE["calls"].append(("savepoint", name))

	def tearDown(self):
		del FRAPPE.db.after_commit
		del FRAPPE.db.savepoint

	def test_a_second_recount_runs_after_the_commit_and_sees_the_new_figure(self):
		add_open_po(qty=5, received=0)
		STATE["ordered"][(ITEM, STORES)] = 5
		ms.turn_on(ITEM)
		self.assertEqual(STATE["bins"][(ITEM, STORES)], {"ordered_qty": 5})
		self.assertEqual(len(self.callbacks), 1, "queued for after the commit, not run yet")
		# A receipt of 2 committed while the switch was running.
		STATE["ordered"][(ITEM, STORES)] = 3
		STATE["calls"].clear()
		self.callbacks[0]()
		self.assertEqual(STATE["bins"][(ITEM, STORES)], {"ordered_qty": 3})
		self.assertIn(("commit",), STATE["calls"], "its own commit: the request's is already done")

	def test_nothing_open_queues_nothing(self):
		ms.turn_on(ITEM)
		self.assertEqual(self.callbacks, [])

	def test_a_failure_in_it_is_logged_and_never_a_full_rollback(self):
		add_open_po()
		ms.turn_on(ITEM)
		real = sys.modules["erpnext.stock.stock_balance"].update_bin_qty

		def broken(*args, **kwargs):
			raise RuntimeError("the database went away")

		rolled = []
		real_rollback = FRAPPE.db.rollback
		FRAPPE.db.rollback = lambda **kwargs: rolled.append(kwargs)
		sys.modules["erpnext.stock.stock_balance"].update_bin_qty = broken
		try:
			self.callbacks[0]()
		finally:
			sys.modules["erpnext.stock.stock_balance"].update_bin_qty = real
			FRAPPE.db.rollback = real_rollback
		self.assertEqual(len(STATE["errors"]), 1)
		# To the savepoint only: a full rollback here would drop the other commit callbacks.
		self.assertEqual(rolled, [{"save_point": "maintain_stock_recount"}])


BULK_PAGE_DIR = APP / "inventory_enhancements" / "page" / "maintain_stock_switch"
DECISION_JSON = (
	APP / "inventory_enhancements" / "doctype" / "maintain_stock_decision" / "maintain_stock_decision.json"
)


class BulkWiringTest(unittest.TestCase):
	def test_the_scheduler_restarts_a_run(self):
		source = HOOKS.read_text(encoding="utf-8")
		path = "erpnext_enhancements.inventory_enhancements.maintain_stock_bulk.resume_queue"
		self.assertEqual(source.count(f'"{path}"'), 1)
		cron = _key(_hooks_dict("scheduler_events"), "cron")
		ten = ast.literal_eval(_key(cron, "*/10 * * * *"))
		self.assertIn(path, ten)
		self.assertTrue(callable(bulk.resume_queue))
		self.assertEqual(bulk.JOB_METHOD.rsplit(".", 1)[1], "process_queue")
		self.assertTrue(callable(bulk.process_queue))

	def test_nothing_about_the_switch_runs_on_migrate(self):
		source = HOOKS.read_text(encoding="utf-8")
		self.assertNotIn("maintain_stock_bulk.queue_switch", source)
		self.assertNotIn("maintain_stock_bulk.process_queue", source)
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("maintain_stock", patches)

	def test_the_page_and_its_record(self):
		import json

		page = json.loads((BULK_PAGE_DIR / "maintain_stock_switch.json").read_text(encoding="utf-8"))
		self.assertEqual(page["name"], "maintain-stock-switch")
		self.assertEqual(page["module"], "Inventory Enhancements")
		self.assertEqual({r["role"] for r in page["roles"]}, {"System Manager", *rules.SWITCH_ROLES})
		doctype = json.loads(DECISION_JSON.read_text(encoding="utf-8"))
		self.assertEqual(doctype["name"], DECISION)
		# Named by a hash and found by its item: a rename or a merge of the Item must not strand it.
		self.assertEqual(doctype["autoname"], "hash")
		self.assertFalse(next(f for f in doctype["fields"] if f["fieldname"] == "item_code").get("unique"))
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		for column in DECISION_COLUMNS:
			self.assertIn(column, fields)
		self.assertEqual(fields["decision"]["options"].split("\n"), ["", *bulk_rules.DECISIONS])
		self.assertEqual(
			fields["status"]["options"].split("\n"),
			["", bulk_rules.STATUS_QUEUED, bulk_rules.STATUS_SWITCHED, bulk_rules.STATUS_REFUSED],
		)
		# Nobody edits these by hand: only System Manager may write.
		writers = {p["role"] for p in doctype["permissions"] if p.get("write")}
		self.assertEqual(writers, {"System Manager"})

	def test_the_page_script_calls_endpoints_that_exist(self):
		source = (BULK_PAGE_DIR / "maintain_stock_switch.js").read_text(encoding="utf-8")
		self.assertIn('const MSS_API = "erpnext_enhancements.api.maintain_stock.";', source)
		called = set(re.findall(r'this\.call\(\s*"(bulk_[a-z_]+)"', source))
		self.assertEqual(called, set(BulkEndpointTest.ENDPOINTS))
		for name in called:
			self.assertIn(name, FRAPPE.whitelisted)
		# A tab is a route, so Back and Forward work; the page never rewrites history.
		self.assertIn("frappe.set_route(...MSS.route_for(", source)
		for banned in ("history.pushState", "history.replaceState", "location.hash ="):
			self.assertNotIn(banned, source)

	def test_the_item_form_links_to_the_page(self):
		source = FORM_JS.read_text(encoding="utf-8")
		self.assertIn('const BULK_PAGE = "maintain-stock-switch";', source)
		self.assertIn("frappe.set_route(BULK_PAGE)", source)

	def test_documented(self):
		inventory = INVENTORY_README.read_text(encoding="utf-8")
		for name in ("`maintain_stock_bulk_rules.py`", "`maintain_stock_bulk.py`", "Maintain Stock Decision"):
			self.assertIn(name, inventory)
		self.assertIn("bulk_preview", API_README.read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class FormScriptUnderNodeTest(unittest.TestCase):
	def test_the_harness_passes(self):
		run = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=REPO_ROOT
		)
		self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
		self.assertIn("all checks passed", run.stdout)

	def test_the_bulk_pages_harness_passes(self):
		run = subprocess.run(
			[shutil.which("node"), str(BULK_HARNESS)],
			capture_output=True,
			text=True,
			timeout=120,
			cwd=REPO_ROOT,
		)
		self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
		self.assertIn("maintain_stock_switch: all checks passed", run.stdout)


if __name__ == "__main__":
	unittest.main()
