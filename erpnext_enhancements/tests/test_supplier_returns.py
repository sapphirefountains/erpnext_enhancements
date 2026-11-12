# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for Supplier Returns (v1.807.0): the module, the endpoints and the wiring.

The pure rules have their own suite (``test_supplier_return_rules``). This one runs
``inventory_enhancements/supplier_returns.py`` and ``api/supplier_returns.py`` against a ``frappe``
stub whose database is real SQLite, so the module's own queries run, and a small document class
that saves through the controller's ``validate``:

* **The quantity limit, on data**: what a receipt line has left after another return, after a
  return made with ERPNext's own button, and after a return is denied or canceled.
* **Each accounting case's choice of documents**: a stock item received and not billed; a
  non-stock line; a line received before its item kept stock (record only, and it never reaches
  ERPNext, where the Maintain Stock guard would refuse it); a partial return and a second return
  against the same receipt; a return before the bill, after the bill, and after the bill was paid.
  ERPNext's mappers are stand-ins here: what they are *handed back* is what is asserted.
* **The money rule**: typed by hand until a Debit Note or Payment Entry is linked, then read from
  the document; a payment *to* a supplier is never taken for a refund.
* **The email**: nothing is sent by saving, by a step, by a doc_event or by the daily job; the
  one sender refuses a bad address and a return that is not a Draft; the endpoint refuses a token.
* **The wiring**: hooks, the DocType's options against the rules, the scripts' lists, the Stock
  Scan page's endpoint map, the print format (rendered), the workspace and the patches.

What this cannot prove is in the suite's last class and in ``docs/supplier-returns.md``: the
standard Purchase Return and Debit Note actually posting needs a real bench.

Fake item codes, suppliers and addresses only: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_supplier_returns -v
"""

import ast
import datetime
import html
import json
import re
import sqlite3
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
INVENTORY = APP / "inventory_enhancements"
DOCTYPE_DIR = INVENTORY / "doctype" / "supplier_return"
CHILD_DIR = INVENTORY / "doctype" / "supplier_return_item"
CORE_PY = INVENTORY / "supplier_returns.py"
API_PY = APP / "api" / "supplier_returns.py"
HOOKS = APP / "hooks.py"

DOCTYPE = "Supplier Return"
CHILD = "Supplier Return Item"
SUPPLIER = "Test Supply Co"
OTHER_SUPPLIER = "Other Supply Co"
COMPANY = "Test Company"
RECEIPT = "PR-0001"
TODAY = "2026-11-12"

rules = None
core = None
api = None
FRAPPE = None
CONN = None
STATE = {}

LAYOUT = {"Section Break", "Column Break", "Tab Break", "Table", "HTML"}


def _fields(path):
	return [f for f in json.loads(path.read_text(encoding="utf-8"))["fields"] if f["fieldtype"] not in LAYOUT]


HEADER_FIELDS = [f["fieldname"] for f in _fields(DOCTYPE_DIR / "supplier_return.json")]
LINE_FIELDS = [f["fieldname"] for f in _fields(CHILD_DIR / "supplier_return_item.json")]

SCHEMA = (
	f"create table `tab{DOCTYPE}` (name text primary key, creation text, "
	+ ", ".join(f"`{f}`" for f in HEADER_FIELDS)
	+ ")",
	f"create table `tab{CHILD}` (name text, parent text, idx int, "
	+ ", ".join(f"`{f}`" for f in LINE_FIELDS)
	+ ")",
	"create table `tabPurchase Receipt` (name text primary key, docstatus int, is_return int default 0, "
	"return_against text, supplier text, supplier_name text, company text, posting_date text, net_total real, "
	"total_taxes_and_charges real, remarks text)",
	"create table `tabPurchase Receipt Item` (name text, parent text, parenttype text, idx int, item_code text, "
	"item_name text, qty real, uom text, conversion_factor real, rate real, project text, warehouse text, "
	"purchase_order text, purchase_order_item text, supplier_part_no text, purchase_receipt_item text)",
	"create table `tabItem` (name text primary key, is_stock_item int default 0)",
	"create table `tabStock Ledger Entry` (name text, voucher_type text, voucher_detail_no text)",
	"create table `tabPurchase Invoice` (name text primary key, docstatus int, is_return int default 0, "
	"return_against text, supplier text, grand_total real, outstanding_amount real, posting_date text, "
	"bill_no text, status text, update_stock int, remarks text)",
	"create table `tabPurchase Invoice Item` (name text, parent text, item_code text, qty real, pr_detail text, "
	"po_detail text, purchase_receipt text, conversion_factor real)",
	"create table `tabPayment Entry` (name text primary key, docstatus int, payment_type text, party_type text, "
	"party text, paid_amount real, posting_date text, reference_no text)",
	"create table `tabSupplier` (name text primary key, email_id text, supplier_primary_contact text)",
	"create table `tabContact` (name text primary key, email_id text, first_name text, last_name text, "
	"is_primary_contact int, modified text)",
	"create table `tabDynamic Link` (parent text, parenttype text, link_doctype text, link_name text)",
	"create table `tabCompany` (name text primary key, company_name text, phone_no text)",
	"create table `tabFile` (name text, attached_to_doctype text, attached_to_name text, is_folder int, "
	"file_name text, file_url text, creation text)",
	"create table `tabEmail Template` (name text primary key)",
	"create table `tabPrint Format` (name text primary key)",
	"create table `tabDocType` (name text primary key)",
)


class _dict(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class FrappeError(Exception):
	pass


class Row:
	"""A child row: attribute access, ``get`` and ``set``, like a Frappe child document."""

	def __init__(self, data=None, **more):
		object.__setattr__(self, "_d", dict(data or {}, **more))

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self._d.get(key)

	def __setattr__(self, key, value):
		self._d[key] = value

	def get(self, key, default=None):
		value = self._d.get(key)
		return default if value is None else value

	def set(self, key, value):
		self._d[key] = value

	def as_dict(self):
		return dict(self._d)


class Doc(Row):
	"""A document: a Row with child rows, flags, a snapshot of what was saved, and a ``save``."""

	def __init__(self, doctype, data=None, new=True):
		super().__init__(data)
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "flags", _dict())
		object.__setattr__(self, "_new", new)
		object.__setattr__(self, "_before", None)
		object.__setattr__(self, "meta", types.SimpleNamespace(get_label=lambda field: field))
		self._d.setdefault("items", [])

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return self._before

	def append(self, table, row=None):
		row = row if isinstance(row, Row) else Row(row or {})
		self._d.setdefault(table, []).append(row)
		row.idx = len(self._d[table])
		return row

	def set(self, key, value):
		self._d[key] = value
		if key == "items":
			for idx, row in enumerate(value, 1):
				row.idx = idx

	def as_dict(self):
		return {k: v for k, v in self._d.items() if k != "items"}

	def check_permission(self, ptype="read"):
		if (self.doctype, ptype) in STATE["denied"]:
			raise FRAPPE.PermissionError(f"No permission to {ptype} {self.doctype}")

	def has_permission(self, ptype="read"):
		return (self.doctype, ptype) not in STATE["denied"]

	def add_comment(self, kind, text):
		STATE["comments"].append((self.doctype, self.name, text))

	def run_method(self, name):
		STATE["ran"].append((self.doctype, name))

	def save(self):
		if self.doctype == DOCTYPE:
			core.validate(self)
			_store_return(self)
		elif self.doctype in ("Purchase Receipt", "Purchase Invoice"):
			_store_standard(self)
		object.__setattr__(self, "_new", False)
		return self

	def insert(self, ignore_permissions=False, **kwargs):
		if (self.doctype, "create") in STATE["denied"] and not ignore_permissions:
			raise FRAPPE.PermissionError(f"No permission to create {self.doctype}")
		if not self.name:
			STATE["serial"] += 1
			prefix = {
				DOCTYPE: "SRET-2026-",
				"Purchase Receipt": "PR-RET-",
				"Purchase Invoice": "DN-",
				"Communication": "COMM-",
			}.get(self.doctype, "DOC-")
			self.name = f"{prefix}{STATE['serial']:05d}"
		if self.doctype == "Communication":
			STATE["communications"].append(self.as_dict())
			return self
		if self.doctype == "Purchase Receipt" and self.is_return:
			STATE["guard"](self)
		return self.save()


def _store_return(doc):
	CONN.execute(f"delete from `tab{DOCTYPE}` where name = ?", (doc.name,))
	CONN.execute(f"delete from `tab{CHILD}` where parent = ?", (doc.name,))
	cols = ["name", "creation", *HEADER_FIELDS]
	values = [
		doc.name,
		doc.creation or f"2026-11-12 09:00:{STATE['serial']:02d}",
		*[_cell(doc.get(f)) for f in HEADER_FIELDS],
	]
	CONN.execute(
		f"insert into `tab{DOCTYPE}` ({', '.join(f'`{c}`' for c in cols)}) values ({', '.join('?' for _ in cols)})",
		values,
	)
	for idx, row in enumerate(doc.items, 1):
		cols = ["name", "parent", "idx", *LINE_FIELDS]
		values = [f"{doc.name}-{idx}", doc.name, idx, *[_cell(row.get(f)) for f in LINE_FIELDS]]
		CONN.execute(
			f"insert into `tab{CHILD}` ({', '.join(f'`{c}`' for c in cols)}) values ({', '.join('?' for _ in cols)})",
			values,
		)


def _cell(value):
	if isinstance(value, datetime.datetime | datetime.date):
		return str(value)
	return value


def _store_standard(doc):
	"""A draft Purchase Return or Debit Note the module inserted: a row a later read can find."""
	if doc.doctype == "Purchase Receipt":
		insert(
			"Purchase Receipt",
			name=doc.name,
			docstatus=0,
			is_return=1,
			return_against=doc.return_against,
			supplier=doc.supplier,
			remarks=doc.remarks,
		)
		for idx, row in enumerate(doc.items, 1):
			insert(
				"Purchase Receipt Item",
				name=f"{doc.name}-{idx}",
				parent=doc.name,
				parenttype="Purchase Receipt",
				idx=idx,
				item_code=row.item_code,
				qty=row.qty,
				purchase_receipt_item=row.purchase_receipt_item,
			)
	else:
		insert(
			"Purchase Invoice",
			name=doc.name,
			docstatus=0,
			is_return=1,
			return_against=doc.return_against,
			supplier=doc.supplier,
			grand_total=-sum(abs(r.qty) * (r.rate or 0) for r in doc.items),
			outstanding_amount=0,
			update_stock=doc.update_stock,
			remarks=doc.remarks,
		)
	STATE["standard"][doc.name] = doc


def insert(table, **row):
	cols = ", ".join(f"`{k}`" for k in row)
	CONN.execute(
		f"insert into `tab{table}` ({cols}) values ({', '.join('?' for _ in row)})", list(row.values())
	)


def update(table, name, **values):
	sets = ", ".join(f"`{k}` = ?" for k in values)
	CONN.execute(f"update `tab{table}` set {sets} where name = ?", [*values.values(), name])


def _where(filters):
	clauses, values = [], []
	for field, cond in (filters or {}).items():
		if isinstance(cond, tuple | list):
			op, operand = cond[0], cond[1]
			if op in ("in", "not in"):
				names = list(operand)
				clauses.append(f"`{field}` {op} ({', '.join('?' for _ in names) or 'null'})")
				values += names
			elif op == "is":
				blank = f"(`{field}` is null or `{field}` = '')"
				clauses.append(blank if operand == "not set" else f"not {blank}")
			else:
				clauses.append(f"`{field}` {op} ?")
				values.append(operand)
		else:
			clauses.append(f"`{field}` = ?")
			values.append(cond)
	return (" where " + " and ".join(clauses)) if clauses else "", values


def _select(doctype, filters, fields):
	where, values = _where(filters if isinstance(filters, dict) else {"name": filters})
	cur = CONN.execute(f"select {', '.join(f'`{f}`' for f in fields)} from `tab{doctype}`{where}", values)
	return [_dict(zip(fields, row, strict=True)) for row in cur.fetchall()]


def _sql(query, values=None, as_dict=False, **kwargs):
	if STATE.get("sql_breaks"):
		raise sqlite3.OperationalError("the database is away")
	values = dict(values or {})
	params = {}

	def swap(match):
		key = match.group(1)
		value = values[key]
		if isinstance(value, tuple | list):
			marks = []
			for i, entry in enumerate(value):
				params[f"{key}_{i}"] = entry
				marks.append(f":{key}_{i}")
			return "(" + (", ".join(marks) or "null") + ")"
		params[key] = value
		return f":{key}"

	text = re.sub(r"%\((\w+)\)s", swap, query)
	cur = CONN.execute(text, params)
	rows = cur.fetchall()
	if as_dict:
		names = [col[0] for col in cur.description]
		return [_dict(zip(names, row, strict=True)) for row in rows]
	return rows


def _load_return(name):
	rows = _select(DOCTYPE, name, ["name", "creation", *HEADER_FIELDS])
	if not rows:
		raise FRAPPE.DoesNotExistError(name)

	def build():
		doc = Doc(DOCTYPE, dict(rows[0]), new=False)
		lines = _select(CHILD, {"parent": name}, ["idx", *LINE_FIELDS])
		doc.set("items", [Row(dict(line)) for line in sorted(lines, key=lambda l: l["idx"])])
		return doc

	doc = build()
	object.__setattr__(doc, "_before", build())
	return doc


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

	def throw(msg, exc=None, title=None, **kwargs):
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

	def has_permission(doctype=None, ptype="read", doc=None, throw=False, **kwargs):
		ok = (doctype, ptype) not in STATE["denied"]
		if not ok and throw:
			raise PermissionError_(f"No permission to {ptype} {doctype}")
		return ok

	frappe.has_permission = has_permission

	db = types.SimpleNamespace()
	db.sql = _sql

	def get_value(doctype, name, fieldname="name", as_dict=False, **kwargs):
		fields = [fieldname] if isinstance(fieldname, str) else list(fieldname)
		rows = _select(doctype, name, fields)
		if not rows:
			return None
		if as_dict:
			return rows[0]
		return rows[0][fields[0]] if len(fields) == 1 else tuple(rows[0][f] for f in fields)

	def exists(doctype, name=None):
		if doctype == "DocType":
			return name in STATE["doctypes"]
		return (
			bool(_select(doctype, name, ["name"]))
			if doctype != "Purchase Receipt Item"
			else bool(_select(doctype, name, ["parent"]))
		)

	def set_value(doctype, name, field, value=None, **kwargs):
		update(doctype, name, **(field if isinstance(field, dict) else {field: value}))

	db.get_value = get_value
	db.exists = exists
	db.set_value = set_value
	db.table_exists = lambda doctype: STATE["installed"]
	db.commit = lambda: None
	db.savepoint = lambda name: STATE["savepoints"].append(("save", name))
	db.rollback = lambda save_point=None: STATE["savepoints"].append(("rollback", save_point))
	frappe.db = db

	def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, as_list=False, **kwargs):
		cols = [pluck] if pluck else list(fields or ["name"])
		rows = _select(doctype, filters or {}, cols)
		if order_by and order_by.split()[0] in cols:
			rows.sort(key=lambda r: (r[order_by.split()[0]] is None, r[order_by.split()[0]]))
		if pluck:
			return [row[pluck] for row in rows]
		if as_list:
			return [tuple(row[c] for c in cols) for row in rows]
		return rows

	frappe.get_all = get_all
	frappe.get_list = get_all

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			return Doc(doctype["doctype"], {k: v for k, v in doctype.items() if k != "doctype"})
		if doctype == DOCTYPE:
			return _load_return(name)
		return Doc(doctype, {"name": name}, new=False)

	frappe.get_doc = get_doc

	def new_doc(doctype, parent_doc=None, parentfield=None, **kwargs):
		return Row() if doctype == CHILD else Doc(doctype)

	frappe.new_doc = new_doc
	frappe.sendmail = lambda **kwargs: STATE["sent"].append(kwargs)
	frappe.attach_print = lambda *args, **kwargs: (
		STATE["prints"].append((args, kwargs)) or {"fname": f"{args[1]}.pdf", "fcontent": b"%PDF"}
	)
	frappe.msgprint = lambda message, **kwargs: STATE["messages"].append(message)
	frappe.format_value = lambda value, df=None: f"{float(value):g}"
	frappe.get_traceback = lambda: "traceback"

	def log_error(*args, **kwargs):
		STATE["logs"].append(kwargs.get("title") or (args[-1] if args else ""))

	frappe.log_error = log_error

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.flt = (
		lambda v, precision=None: (round(float(v), precision) if precision is not None else float(v))
		if v not in (None, "")
		else 0.0
	)
	utils.getdate = lambda v=None: datetime.date.fromisoformat(str(v or TODAY)[:10])
	utils.nowdate = lambda: TODAY
	utils.now_datetime = lambda: datetime.datetime(2026, 11, 12, 10, 0, 0)
	utils.get_fullname = lambda user=None: "Pat Buyer"
	utils.escape_html = lambda text: html.escape(str(text or ""))
	utils.fmt_money = lambda value, **kwargs: f"${float(value or 0):,.2f}"
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", text or "")
	utils.sanitize_html = lambda text: (text or "").replace("<script>", "")
	frappe.utils = utils
	return frappe, utils


def _fake_make_purchase_return(receipt):
	"""Stands in for ERPNext's mapper: every line of the receipt at what is still returnable."""
	STATE["mapper_calls"].append(("purchase_return", receipt))
	target = Doc("Purchase Receipt", {"is_return": 1, "return_against": receipt, "supplier": SUPPLIER})
	for line in _select(
		"Purchase Receipt Item",
		{"parent": receipt},
		["name", "item_code", "qty", "conversion_factor", "rate"],
	):
		returned = _sql(
			"select coalesce(sum(abs(c.qty)), 0) from `tabPurchase Receipt Item` c "
			"join `tabPurchase Receipt` p on p.name = c.parent "
			"where p.docstatus = 1 and p.is_return = 1 and c.purchase_receipt_item = %(line)s",
			{"line": line.name},
		)[0][0]
		left = line.qty - returned
		if left > 0:
			target.append(
				"items",
				Row(
					purchase_receipt_item=line.name,
					item_code=line.item_code,
					qty=-left,
					received_qty=-left,
					conversion_factor=line.conversion_factor,
					rate=line.rate,
				),
			)
	return target


def _fake_make_debit_note(invoice):
	STATE["mapper_calls"].append(("debit_note", invoice))
	target = Doc(
		"Purchase Invoice",
		{"is_return": 1, "return_against": invoice, "supplier": SUPPLIER, "update_stock": 1},
	)
	for line in _select(
		"Purchase Invoice Item",
		{"parent": invoice},
		["name", "item_code", "qty", "pr_detail", "po_detail", "conversion_factor"],
	):
		target.append(
			"items",
			Row(
				item_code=line.item_code,
				qty=-line.qty,
				pr_detail=line.pr_detail,
				po_detail=line.po_detail,
				conversion_factor=line.conversion_factor or 1,
				rate=10.0,
			),
		)
	return target


def _guard(doc):
	"""Stands in for ``maintain_stock.refuse_unstocked_receipt_return``: a return of a stock item's
	receipt line that wrote no stock ledger entry is refused. The module must never get here with one."""
	assert not doc.flags.get("ignore_maintain_stock_guard"), "the guard's way out must never be used"
	for row in doc.items:
		stock = _select("Item", row.item_code, ["is_stock_item"])
		wrote = _select("Stock Ledger Entry", {"voucher_detail_no": row.purchase_receipt_item}, ["name"])
		if stock and stock[0].is_stock_item and not wrote:
			raise FRAPPE.ValidationError(f"guard: {row.item_code} was received before it kept stock")


def install_stubs():
	global FRAPPE
	frappe, utils = build_frappe()
	FRAPPE = frappe
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = object
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	for path, name, fn in (
		(
			"erpnext.stock.doctype.purchase_receipt.purchase_receipt",
			"make_purchase_return",
			_fake_make_purchase_return,
		),
		(
			"erpnext.accounts.doctype.purchase_invoice.purchase_invoice",
			"make_debit_note",
			_fake_make_debit_note,
		),
	):
		parts = path.split(".")
		for i in range(1, len(parts) + 1):
			sys.modules.setdefault(".".join(parts[:i]), types.ModuleType(".".join(parts[:i])))
		setattr(sys.modules[path], name, fn)


def reset(roles=("Purchase User", "Purchase Manager")):
	STATE.clear()
	STATE.update(
		roles=set(roles),
		denied=set(),
		comments=[],
		sent=[],
		prints=[],
		logs=[],
		messages=[],
		ran=[],
		communications=[],
		mapper_calls=[],
		standard={},
		serial=0,
		installed=True,
		guard=_guard,
		savepoints=[],
		doctypes={DOCTYPE, "Email Template"},
	)
	FRAPPE.flags.clear()
	FRAPPE.session.update(user="buyer@example.com", sid="a1b2c3d4e5")
	FRAPPE.local.request = types.SimpleNamespace(headers={})
	for (table,) in CONN.execute("select name from sqlite_master where type = 'table'").fetchall():
		CONN.execute(f"delete from `{table}`")
	insert("Supplier", name=SUPPLIER, email_id="sales@supplier.example")
	insert("Supplier", name=OTHER_SUPPLIER)
	insert("Company", name=COMPANY, company_name=COMPANY)
	insert("Item", name="TST-PUMP-1", is_stock_item=1)
	insert("Item", name="TST-FITTING-1", is_stock_item=0)
	insert("Item", name="TST-OLD-1", is_stock_item=1)
	insert(
		"Purchase Receipt",
		name=RECEIPT,
		docstatus=1,
		is_return=0,
		supplier=SUPPLIER,
		supplier_name=SUPPLIER,
		company=COMPANY,
		posting_date="2026-11-01",
		net_total=250.0,
		total_taxes_and_charges=25.0,
	)
	for idx, (line, item, qty, rate) in enumerate(
		(
			("PRI-1", "TST-PUMP-1", 10, 20.0),
			("PRI-2", "TST-FITTING-1", 5, 6.0),
			("PRI-3", "TST-OLD-1", 2, 10.0),
		),
		1,
	):
		insert(
			"Purchase Receipt Item",
			name=line,
			parent=RECEIPT,
			parenttype="Purchase Receipt",
			idx=idx,
			item_code=item,
			item_name=f"Name of {item}",
			qty=qty,
			uom="Nos",
			conversion_factor=1,
			rate=rate,
			project="PRJ-TEST",
			warehouse="Stores - TC",
			purchase_order="PO-0009",
			purchase_order_item=f"POI-{idx}",
		)
	# Only the pump's line added stock. TST-OLD-1 keeps stock today but this receipt never added it.
	insert("Stock Ledger Entry", name="SLE-1", voucher_type="Purchase Receipt", voucher_detail_no="PRI-1")


def setUpModule():
	global rules, core, api, CONN
	CONN = sqlite3.connect(":memory:")
	for statement in SCHEMA:
		CONN.execute(statement)
	install_stubs()
	for name in (
		"erpnext_enhancements.inventory_enhancements.supplier_return_rules",
		"erpnext_enhancements.inventory_enhancements.supplier_returns",
		"erpnext_enhancements.api.supplier_returns",
	):
		sys.modules.pop(name, None)
	from erpnext_enhancements.api import supplier_returns as api_module
	from erpnext_enhancements.inventory_enhancements import supplier_return_rules as rules_module
	from erpnext_enhancements.inventory_enhancements import supplier_returns as core_module

	rules, core, api = rules_module, core_module, api_module
	core.wrap_request = lambda subject, body: f"<shell>{body}</shell>"
	core.lines_table = (
		lambda doc: "<TABLE:" + ",".join(f"{row.qty:g}x{row.item_code}" for row in doc.items) + ">"
	)
	reset()


def tearDownModule():
	CONN.close()
	for name in [n for n in sys.modules if n == "frappe" or n.startswith(("frappe.", "erpnext."))] + [
		"erpnext"
	]:
		sys.modules.pop(name, None)


def new_return(lines, **header):
	"""Save a Draft for ``lines``: ``{receipt line: quantity}``."""
	doc = core.build_from_receipt(RECEIPT, only_lines=list(lines))
	for row in doc.items:
		row.qty = lines[row.purchase_receipt_item]
		row.reason = "Damaged"
	for key, value in header.items():
		doc.set(key, value)
	doc.insert()
	return doc.name


def sent_back(lines, **header):
	name = new_return(lines, **header)
	core.mark_sent_back(name, "Dropped off")
	return name


def submit(doctype, name, method="on_submit", **values):
	update(doctype, name, docstatus=1 if method == "on_submit" else 2, **values)
	core.on_document_change(
		Doc(doctype, dict(_select(doctype, name, ["name"])[0], **_row(doctype, name)), new=False), method
	)


def _row(doctype, name):
	if doctype == "Payment Entry":
		row = _select(doctype, name, ["payment_type", "party_type", "party"])[0]
		return dict(row, references=STATE.get("references", {}).get(name, []))
	return {}


def get(name):
	return _load_return(name)


class Base(unittest.TestCase):
	def setUp(self):
		reset()


class TestWhatCanGoBack(Base):
	def test_each_line_gets_its_kind(self):
		kinds = {fact["item_code"]: fact["line_kind"] for fact in core.receipt_facts(RECEIPT)}
		self.assertEqual(
			kinds,
			{"TST-PUMP-1": rules.STOCK, "TST-FITTING-1": rules.NON_STOCK, "TST-OLD-1": rules.RECORD_ONLY},
		)

	def test_a_fresh_receipt_is_returnable_in_full(self):
		self.assertEqual([fact["returnable"] for fact in core.receipt_facts(RECEIPT)], [10, 5, 2])

	def test_another_return_holds_its_quantity(self):
		first = new_return({"PRI-1": 4})
		facts = {fact["purchase_receipt_item"]: fact for fact in core.receipt_facts(RECEIPT)}
		self.assertEqual(facts["PRI-1"]["returnable"], 6)
		self.assertEqual(facts["PRI-1"]["already_returned_qty"], 4)
		own = {fact["purchase_receipt_item"]: fact for fact in core.receipt_facts(RECEIPT, excluding=first)}
		self.assertEqual(own["PRI-1"]["returnable"], 10, "a return's own lines do not count against it")

	def test_a_denied_or_canceled_return_frees_its_quantity(self):
		first = new_return({"PRI-1": 4})
		core.cancel_return(first, "Ordered by mistake")
		self.assertEqual(core.receipt_facts(RECEIPT)[0]["returnable"], 10)
		second = new_return({"PRI-1": 4})
		core.record_denial(second, "Past the return window")
		self.assertEqual(core.receipt_facts(RECEIPT)[0]["returnable"], 10)

	def test_a_return_made_with_erpnexts_own_button_counts(self):
		insert(
			"Purchase Receipt",
			name="PR-RET-X",
			docstatus=1,
			is_return=1,
			return_against=RECEIPT,
			supplier=SUPPLIER,
		)
		insert(
			"Purchase Receipt Item",
			name="X-1",
			parent="PR-RET-X",
			item_code="TST-PUMP-1",
			qty=-3,
			purchase_receipt_item="PRI-1",
		)
		self.assertEqual(core.receipt_facts(RECEIPT)[0]["returnable"], 7)

	def test_a_returns_own_purchase_return_is_not_counted_twice(self):
		name = sent_back({"PRI-1": 4})
		made = core.make_purchase_return(name)["name"]
		submit("Purchase Receipt", made)
		self.assertEqual(core.receipt_facts(RECEIPT)[0]["returnable"], 6, "4 are gone once, not twice")

	def test_a_draft_or_canceled_standard_return_does_not_count(self):
		for docstatus in (0, 2):
			insert(
				"Purchase Receipt",
				name=f"PR-RET-{docstatus}",
				docstatus=docstatus,
				is_return=1,
				return_against=RECEIPT,
			)
			insert(
				"Purchase Receipt Item",
				name=f"Y-{docstatus}",
				parent=f"PR-RET-{docstatus}",
				qty=-3,
				purchase_receipt_item="PRI-1",
			)
		self.assertEqual(core.receipt_facts(RECEIPT)[0]["returnable"], 10)


class TestStartingAReturn(Base):
	def test_every_returnable_line_at_the_most_that_can_go_back(self):
		doc = core.build_from_receipt(RECEIPT)
		self.assertEqual(
			[(row.item_code, row.qty) for row in doc.items],
			[("TST-PUMP-1", 10), ("TST-FITTING-1", 5), ("TST-OLD-1", 2)],
		)
		self.assertEqual((doc.supplier, doc.purchase_receipt, doc.company), (SUPPLIER, RECEIPT, COMPANY))
		self.assertEqual(doc.contact_email, "sales@supplier.example")
		self.assertEqual(doc.purchase_order, "PO-0009")
		self.assertEqual(doc.project, "PRJ-TEST")
		self.assertIsNone(doc.name, "nothing is saved")

	def test_narrowed_to_ticked_rows_an_order_or_an_item(self):
		self.assertEqual(
			[r.item_code for r in core.build_from_receipt(RECEIPT, only_lines=["PRI-2"]).items],
			["TST-FITTING-1"],
		)
		self.assertEqual(
			[r.item_code for r in core.build_from_receipt(RECEIPT, item_code="TST-PUMP-1").items],
			["TST-PUMP-1"],
		)
		self.assertEqual(len(core.build_from_receipt(RECEIPT, purchase_order="PO-0009").items), 3)
		with self.assertRaises(FrappeError):
			core.build_from_receipt(RECEIPT, purchase_order="PO-NOPE")

	def test_nothing_left_says_so(self):
		new_return({"PRI-2": 5})
		with self.assertRaises(FrappeError) as caught:
			core.build_from_receipt(RECEIPT, only_lines=["PRI-2"])
		self.assertIn("already been returned or is on another Supplier Return", str(caught.exception))

	def test_a_return_or_a_draft_receipt_is_not_a_place_to_start(self):
		insert("Purchase Receipt", name="PR-RET-9", docstatus=1, is_return=1, supplier=SUPPLIER)
		insert("Purchase Receipt", name="PR-DRAFT", docstatus=0, is_return=0, supplier=SUPPLIER)
		for receipt, words in (
			("PR-RET-9", "itself a return"),
			("PR-DRAFT", "not submitted"),
			("PR-NOPE", "doesn't exist"),
		):
			with self.assertRaises(FrappeError) as caught:
				core.build_from_receipt(receipt)
			self.assertIn(words, str(caught.exception))

	def test_tax_and_expected_amount(self):
		name = new_return({"PRI-1": 5}, restocking_fee=10)
		doc = get(name)
		self.assertEqual(doc.total_amount, 100.0)
		self.assertEqual(doc.tax_amount, 10.0, "25 of tax on 250, for 100 going back")
		self.assertEqual(doc.expected_amount, 100.0)


class TestSaving(Base):
	def test_more_than_was_received_is_refused(self):
		with self.assertRaises(FrappeError) as caught:
			new_return({"PRI-1": 11})
		self.assertIn("only 10 of TST-PUMP-1 can still go back", str(caught.exception))

	def test_a_second_return_against_the_same_receipt(self):
		new_return({"PRI-1": 4})
		new_return({"PRI-1": 6})
		with self.assertRaises(FrappeError) as caught:
			new_return({"PRI-1": 1})
		self.assertIn("already been returned or is on another", str(caught.exception))

	def test_a_line_typed_by_hand_is_refused(self):
		doc = core.build_from_receipt(RECEIPT, only_lines=["PRI-1"])
		doc.items[0].reason = "Damaged"
		doc.append("items", Row(item_code="TST-PUMP-1", qty=1, reason="Damaged"))
		with self.assertRaises(FrappeError) as caught:
			doc.insert()
		self.assertIn("not from Purchase Receipt", str(caught.exception))

	def test_the_line_is_refilled_from_the_receipt(self):
		doc = core.build_from_receipt(RECEIPT, only_lines=["PRI-1"])
		row = doc.items[0]
		row.qty, row.reason, row.rate, row.item_code, row.line_kind = (
			1,
			"Damaged",
			9999.0,
			"TST-FITTING-1",
			rules.NON_STOCK,
		)
		doc.insert()
		saved = get(doc.name).items[0]
		self.assertEqual((saved.item_code, saved.rate, saved.line_kind), ("TST-PUMP-1", 20.0, rules.STOCK))

	def test_the_wrong_supplier(self):
		doc = core.build_from_receipt(RECEIPT, only_lines=["PRI-1"])
		doc.items[0].reason = "Damaged"
		doc.supplier = OTHER_SUPPLIER
		with self.assertRaises(FrappeError):
			doc.insert()

	def test_a_new_return_is_a_draft_whatever_was_sent(self):
		doc = core.build_from_receipt(RECEIPT, only_lines=["PRI-1"])
		doc.items[0].reason = "Damaged"
		doc.status, doc.amount_received, doc.money_source = rules.REFUND_RECEIVED, 999, rules.FROM_PAYMENT
		doc.insert()
		saved = get(doc.name)
		self.assertEqual((saved.status, saved.amount_received, saved.money_source), (rules.DRAFT, None, None))

	def test_a_status_typed_into_a_save_is_put_back(self):
		name = new_return({"PRI-1": 1})
		doc = get(name)
		doc.status, doc.amount_received, doc.purchase_return = rules.CREDIT_RECEIVED, 500, "PR-FAKE"
		doc.save()
		saved = get(name)
		self.assertEqual(
			(saved.status, saved.amount_received, saved.purchase_return), (rules.DRAFT, None, None)
		)

	def test_lines_are_locked_once_the_supplier_was_asked(self):
		name = new_return({"PRI-1": 2})
		core.mark_requested(name, "Phone")
		doc = get(name)
		doc.items[0].qty = 5
		with self.assertRaises(FrappeError) as caught:
			doc.save()
		self.assertIn("can't be changed after the return has left Draft", str(caught.exception))
		doc = get(name)
		doc.tracking_number, doc.restocking_fee = "1Z999", 5
		doc.save()
		self.assertEqual(get(name).expected_amount, 39.0, "40 + 4 tax - 5 fee: money fields stay editable")

	def test_only_a_draft_or_canceled_return_can_be_deleted(self):
		name = new_return({"PRI-1": 1})
		core.refuse_delete(get(name))
		core.mark_requested(name, "Phone")
		with self.assertRaises(FrappeError):
			core.refuse_delete(get(name))


class TestSteps(Base):
	def test_a_stock_user_starts_and_ships_but_does_not_mark_money(self):
		reset(roles=("Stock User",))
		name = sent_back({"PRI-1": 1})
		for call in (
			lambda: core.record_money(name, "refund", 20, TODAY, "CHK 1"),
			lambda: core.close_without_credit(name, "Supplier went quiet"),
			lambda: core.make_debit_note(name, "PINV-1"),
		):
			with self.assertRaises(FRAPPE.PermissionError):
				call()
		self.assertEqual(get(name).status, rules.SENT_BACK)

	def test_accounts_mark_money_but_do_not_ship(self):
		reset(roles=("Purchase User",))
		name = new_return({"PRI-1": 1})
		STATE["roles"] = {"Accounts User"}
		with self.assertRaises(FRAPPE.PermissionError):
			core.mark_sent_back(name, "Dropped off")
		STATE["roles"] = {"Purchase User"}
		core.mark_sent_back(name, "Dropped off")
		STATE["roles"] = {"Accounts User"}
		core.record_money(name, "refund", 22, TODAY, "CHK 1")
		self.assertEqual(get(name).status, rules.REFUND_RECEIVED)

	def test_no_write_permission_no_step(self):
		name = new_return({"PRI-1": 1})
		STATE["denied"].add((DOCTYPE, "write"))
		with self.assertRaises(FRAPPE.PermissionError):
			core.mark_requested(name, "Phone")

	def test_requested_another_way_records_how_and_who(self):
		name = new_return({"PRI-1": 1})
		core.mark_requested(name, "Phone", "Spoke to the counter")
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.requested_how, doc.requested_by), (rules.REQUESTED, "Phone", "buyer@example.com")
		)
		self.assertTrue(doc.requested_on)

	def test_email_cannot_be_claimed_without_sending(self):
		name = new_return({"PRI-1": 1})
		for call in (lambda: core.mark_requested(name, "Email"), lambda: api.mark_requested(name, "Email")):
			with self.assertRaises(FrappeError):
				call()
		self.assertEqual(get(name).status, rules.DRAFT)

	def test_approval_records_the_return_number(self):
		name = new_return({"PRI-1": 1})
		core.record_approval(name, " RMA-5521 ", "Ship to dock 4")
		doc = get(name)
		self.assertEqual((doc.status, doc.supplier_return_number), (rules.APPROVED, "RMA-5521"))

	def test_denial_needs_the_suppliers_words(self):
		name = new_return({"PRI-1": 1})
		with self.assertRaises(FrappeError):
			core.record_denial(name, " ")
		core.record_denial(name, "Past 30 days")
		self.assertEqual(get(name).status, rules.DENIED)

	def test_sent_back_by_carrier_needs_tracking(self):
		name = new_return({"PRI-1": 1})
		with self.assertRaises(FrappeError):
			core.mark_sent_back(name, "Carrier", "Test Freight", "")
		answer = core.mark_sent_back(name, "Carrier", "Test Freight", "1Z999", "2026-11-12")
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.carrier, doc.tracking_number), (rules.SENT_BACK, "Test Freight", "1Z999")
		)
		self.assertEqual(str(doc.sent_back_on), "2026-11-12")
		self.assertTrue(answer["needs_purchase_return"])
		self.assertTrue(answer["takes_stock"])
		self.assertEqual(doc.waiting_on_money, 1)

	def test_sent_back_cannot_be_in_the_future(self):
		name = new_return({"PRI-1": 1})
		with self.assertRaises(FrappeError):
			core.mark_sent_back(name, "Dropped off", sent_back_on="2026-12-01")

	def test_close_without_credit(self):
		name = sent_back({"PRI-1": 1})
		with self.assertRaises(FrappeError):
			core.close_without_credit(name, "")
		core.close_without_credit(name, "Supplier refused after inspection")
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.amount_received, doc.waiting_on_money), (rules.CLOSED_NO_CREDIT, 0, 0)
		)
		self.assertEqual(doc.amount_difference, doc.expected_amount)

	def test_reopen_clears_what_was_typed(self):
		name = sent_back({"PRI-1": 1})
		core.record_money(name, "refund", 22, TODAY, "CHK 1")
		STATE["roles"] = {"Purchase User"}
		with self.assertRaises(FRAPPE.PermissionError):
			core.reopen(name, "Wrong return")
		STATE["roles"] = {"Purchase Manager"}
		core.reopen(name, "Wrong return")
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.amount_received, doc.money_source, doc.closed_on),
			(rules.SENT_BACK, None, None, None),
		)

	def test_the_state_the_form_draws(self):
		name = sent_back({"PRI-1": 1})
		state = api.state(name)
		self.assertEqual(state["waiting_on"], "money")
		self.assertTrue(state["needs_purchase_return"])
		self.assertIn("refund", state["actions"])
		STATE["denied"].add((DOCTYPE, "write"))
		self.assertEqual(api.state(name)["actions"], [], "a reader is offered no step")


class TestStockAndAccountingCases(Base):
	"""Each case the brief names, through to the ERPNext document it chooses."""

	def test_a_stock_item_received_and_not_billed(self):
		name = sent_back({"PRI-1": 3})
		made = core.make_purchase_return(name)
		target = STATE["standard"][made["name"]]
		self.assertEqual(
			[(r.purchase_receipt_item, r.qty, r.received_qty, r.stock_qty) for r in target.items],
			[("PRI-1", -3.0, -3.0, -3.0)],
		)
		self.assertEqual(target.return_against, RECEIPT)
		self.assertEqual(
			_select("Purchase Receipt", made["name"], ["docstatus"])[0].docstatus,
			0,
			"a draft: the person submits it",
		)
		doc = get(name)
		self.assertEqual(doc.purchase_return, made["name"], "linked from the return")
		self.assertIn(name, target.remarks, "and named on the Purchase Return")
		self.assertIn("is a draft", doc.stock_state)
		self.assertEqual(core.bills_for(doc), [], "no bill in ERPNext: no debit note is offered")
		self.assertEqual(api.state(name)["bills"], [])
		submit("Purchase Receipt", made["name"])
		self.assertIn("took the items out of stock", get(name).stock_state)
		self.assertFalse(api.state(name)["needs_purchase_return"])

	def test_a_non_stock_line(self):
		name = sent_back({"PRI-2": 2})
		self.assertFalse(api.state(name)["takes_stock"])
		made = core.make_purchase_return(name)
		target = STATE["standard"][made["name"]]
		self.assertEqual([(r.item_code, r.qty) for r in target.items], [("TST-FITTING-1", -2.0)])
		submit("Purchase Receipt", made["name"])
		self.assertIn("lowered the received quantity on the order", get(name).stock_state)

	def test_an_old_line_of_a_switched_item_is_record_only(self):
		name = new_return({"PRI-3": 2})
		answer = core.mark_sent_back(name, "Dropped off")
		self.assertFalse(answer["needs_purchase_return"])
		self.assertIn("never added it to stock", answer["note"])
		doc = get(name)
		self.assertEqual(doc.status, rules.SENT_BACK, "still recorded and tracked")
		self.assertEqual(doc.stock_state, "No stock document: this return is a record only.")
		self.assertIn("TST-OLD-1", doc.record_only_note)
		with self.assertRaises(FrappeError) as caught:
			core.make_purchase_return(name)
		self.assertIn("No stock document is made", str(caught.exception))
		self.assertEqual(STATE["mapper_calls"], [], "ERPNext is never asked")
		self.assertEqual(STATE["standard"], {})
		STATE["roles"] = {"Accounts User"}
		core.record_money(name, "refund", 20, TODAY, "CHK 9")
		self.assertEqual(get(name).status, rules.REFUND_RECEIVED, "and its money is still tracked")

	def test_a_mixed_return_leaves_the_record_only_line_off_the_purchase_return(self):
		name = sent_back({"PRI-1": 1, "PRI-2": 1, "PRI-3": 1})
		made = core.make_purchase_return(name)
		target = STATE["standard"][made["name"]]
		self.assertEqual(sorted(r.item_code for r in target.items), ["TST-FITTING-1", "TST-PUMP-1"])
		self.assertIn("TST-OLD-1", get(name).record_only_note)

	def test_an_item_switched_after_the_return_was_saved_is_caught_at_the_document(self):
		"""Saved as Non-stock, then Maintain Stock is turned on: the kind is read again."""
		name = sent_back({"PRI-2": 1})
		update("Item", "TST-FITTING-1", is_stock_item=1)
		with self.assertRaises(FrappeError) as caught:
			core.make_purchase_return(name)
		self.assertIn("No stock document is made for TST-FITTING-1", str(caught.exception))
		self.assertEqual(STATE["standard"], {})

	def test_the_guards_way_out_is_never_used(self):
		source = _code_only(CORE_PY) + _code_only(API_PY)
		self.assertNotIn("ignore_maintain_stock_guard", source)

	def test_a_partial_return_then_a_second_one(self):
		first = sent_back({"PRI-1": 3})
		made = core.make_purchase_return(first)["name"]
		submit("Purchase Receipt", made)
		second = sent_back({"PRI-1": 7})
		target = STATE["standard"][core.make_purchase_return(second)["name"]]
		self.assertEqual([r.qty for r in target.items], [-7.0])
		with self.assertRaises(FrappeError):
			new_return({"PRI-1": 1})

	def test_erpnext_offering_less_is_said_not_trimmed(self):
		name = sent_back({"PRI-1": 5})
		insert(
			"Purchase Receipt",
			name="PR-RET-X",
			docstatus=1,
			is_return=1,
			return_against=RECEIPT,
			supplier=SUPPLIER,
		)
		insert(
			"Purchase Receipt Item",
			name="X-1",
			parent="PR-RET-X",
			item_code="TST-PUMP-1",
			qty=-8,
			purchase_receipt_item="PRI-1",
		)
		with self.assertRaises(FrappeError) as caught:
			core.make_purchase_return(name)
		self.assertIn("ERPNext can return 2 of TST-PUMP-1", str(caught.exception))
		self.assertIsNone(get(name).purchase_return)

	def test_no_purchase_return_before_the_items_have_gone(self):
		name = new_return({"PRI-1": 1})
		with self.assertRaises(FrappeError) as caught:
			core.make_purchase_return(name)
		self.assertIn("Sent Back first", str(caught.exception))

	def test_asking_twice_gives_the_same_draft(self):
		name = sent_back({"PRI-1": 1})
		first = core.make_purchase_return(name)
		again = core.make_purchase_return(name)
		self.assertEqual((again["name"], again["existing"]), (first["name"], True))
		self.assertEqual(len(STATE["mapper_calls"]), 1)

	def test_a_deleted_or_canceled_purchase_return_is_unlinked(self):
		name = sent_back({"PRI-1": 1})
		made = core.make_purchase_return(name)["name"]
		submit("Purchase Receipt", made)
		submit("Purchase Receipt", made, method="on_cancel")
		doc = get(name)
		self.assertIsNone(doc.purchase_return)
		self.assertIn("Not done yet", doc.stock_state)
		self.assertTrue(api.state(name)["needs_purchase_return"])

	def test_a_return_cannot_be_canceled_over_a_live_purchase_return(self):
		name = new_return({"PRI-1": 1})
		insert("Purchase Receipt", name="PR-RET-L", docstatus=0, is_return=1, return_against=RECEIPT)
		update(DOCTYPE, name, purchase_return="PR-RET-L")
		with self.assertRaises(FrappeError) as caught:
			core.cancel_return(name, "Changed our mind")
		self.assertIn("Cancel or delete it first", str(caught.exception))

	def test_a_replacement_is_a_new_receipt_from_the_same_supplier(self):
		name = sent_back({"PRI-1": 1}, requested_resolution=rules.REPLACEMENT)
		self.assertEqual(api.state(name)["waiting_on"], "replacement")
		self.assertEqual(get(name).waiting_on_money, 0)
		insert(
			"Purchase Receipt",
			name="PR-0002",
			docstatus=1,
			is_return=0,
			supplier=SUPPLIER,
			posting_date="2026-11-20",
		)
		insert("Purchase Receipt Item", name="PRI-9", parent="PR-0002", item_code="TST-PUMP-1", qty=1)
		insert("Purchase Receipt", name="PR-0003", docstatus=1, is_return=0, supplier=OTHER_SUPPLIER)
		insert("Purchase Receipt", name="PR-0004", docstatus=1, is_return=0, supplier=SUPPLIER)
		insert("Purchase Receipt Item", name="PRI-8", parent="PR-0004", item_code="TST-FITTING-1", qty=1)
		for receipt, words in (
			(RECEIPT, "not the replacement"),
			("PR-0003", "different supplier"),
			("PR-0004", "none of the items"),
		):
			with self.assertRaises(FrappeError) as caught:
				core.record_replacement(name, receipt)
			self.assertIn(words, str(caught.exception))
		core.record_replacement(name, "PR-0002")
		doc = get(name)
		self.assertEqual((doc.status, doc.replacement_receipt), (rules.REPLACEMENT_RECEIVED, "PR-0002"))

	def test_a_replacement_with_no_receipt_needs_a_date_and_a_note(self):
		name = sent_back({"PRI-3": 1}, requested_resolution=rules.REPLACEMENT)
		with self.assertRaises(FrappeError):
			core.record_replacement(name)
		core.record_replacement(name, received_on="2026-11-20", notes="Swapped at the counter")
		self.assertEqual(get(name).status, rules.REPLACEMENT_RECEIVED)


class TestMoney(Base):
	def bill(self, outstanding=200.0):
		insert(
			"Purchase Invoice",
			name="PINV-1",
			docstatus=1,
			is_return=0,
			supplier=SUPPLIER,
			grand_total=200.0,
			outstanding_amount=outstanding,
			posting_date="2026-11-05",
			bill_no="INV-7781",
			status="Unpaid",
		)
		insert(
			"Purchase Invoice Item",
			name="PII-1",
			parent="PINV-1",
			item_code="TST-PUMP-1",
			qty=10,
			pr_detail="PRI-1",
			purchase_receipt=RECEIPT,
		)
		insert(
			"Purchase Invoice Item",
			name="PII-2",
			parent="PINV-1",
			item_code="TST-FITTING-1",
			qty=5,
			pr_detail="PRI-2",
			purchase_receipt=RECEIPT,
		)

	def test_before_the_bill_money_is_typed_and_says_so(self):
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		core.record_money(name, "credit", 44, "2026-11-11", "CM-204")
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.amount_received, doc.money_reference, doc.money_source),
			(rules.CREDIT_RECEIVED, 44.0, "CM-204", rules.TYPED),
		)
		self.assertEqual(doc.money_recorded_by, "buyer@example.com")
		self.assertTrue(any(rules.NOT_BACKED in text for _dt, _name, text in STATE["comments"]))
		state = api.state(name)
		self.assertTrue(state["typed_by_hand"])
		self.assertEqual(state["not_backed"], rules.NOT_BACKED)

	def test_a_bill_in_erpnext_offers_the_debit_note(self):
		self.bill()
		name = sent_back({"PRI-1": 2})
		self.assertEqual([row.name for row in core.bills_for(get(name))], ["PINV-1"])
		self.assertEqual(api.state(name)["bills"][0]["name"], "PINV-1")
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")
		target = STATE["standard"][made["name"]]
		self.assertEqual(
			[(r.pr_detail, r.qty) for r in target.items],
			[("PRI-1", -2.0)],
			"this return's line and quantity only",
		)
		self.assertEqual(target.update_stock, 0, "the Purchase Return moves the stock, not the debit note")
		self.assertEqual(target.return_against, "PINV-1")
		self.assertEqual(get(name).debit_note, made["name"])
		self.assertEqual(get(name).status, rules.SENT_BACK, "a draft debit note decides nothing")

	def test_a_debit_note_for_a_bill_that_is_not_this_returns(self):
		self.bill()
		insert("Purchase Invoice", name="PINV-9", docstatus=1, is_return=0, supplier=SUPPLIER)
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		with self.assertRaises(FrappeError):
			core.make_debit_note(name, "PINV-9")

	def test_returned_before_the_bill_was_paid_the_debit_note_is_the_credit(self):
		self.bill()
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit(
			"Purchase Invoice",
			made,
			grand_total=-44.0,
			outstanding_amount=0,
			posting_date="2026-11-12",
			bill_no="CM-204",
		)
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.amount_received, doc.money_source, doc.money_reference),
			(rules.CREDIT_RECEIVED, 44.0, rules.FROM_DEBIT_NOTE, "CM-204"),
		)
		self.assertEqual(doc.waiting_on_money, 0)

	def test_the_amount_cannot_be_typed_over_a_document(self):
		self.bill()
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User", "Accounts Manager"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit(
			"Purchase Invoice", made, grand_total=-44.0, outstanding_amount=-44.0, posting_date="2026-11-12"
		)
		self.assertEqual(get(name).status, rules.SENT_BACK, "the supplier still owes on the note")
		with self.assertRaises(FrappeError) as caught:
			core.record_money(name, "refund", 9999, TODAY, "CHK 1")
		self.assertIn("can't be typed", str(caught.exception))
		with self.assertRaises(FrappeError):
			core.close_without_credit(name, "Give up")
		self.assertNotIn("refund", api.state(name)["actions"])
		doc = get(name)
		doc.amount_received, doc.money_source = 9999, rules.TYPED
		doc.save()
		self.assertIsNone(get(name).amount_received, "nor through an ordinary save")

	def test_returned_after_the_bill_was_paid_the_refund_is_a_payment_entry(self):
		self.bill(outstanding=0)
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit(
			"Purchase Invoice", made, grand_total=-44.0, outstanding_amount=-44.0, posting_date="2026-11-12"
		)
		self.assertEqual(core.refund_source(name), {"doctype": "Purchase Invoice", "name": made})
		insert(
			"Payment Entry",
			name="PE-1",
			docstatus=0,
			payment_type="Receive",
			party_type="Supplier",
			party=SUPPLIER,
			paid_amount=44.0,
			posting_date="2026-11-20",
			reference_no="CHK 4410",
		)
		STATE["references"] = {"PE-1": [Row(reference_doctype="Purchase Invoice", reference_name=made)]}
		submit("Payment Entry", "PE-1")
		doc = get(name)
		self.assertEqual(
			doc.payment_entry, "PE-1", "linked by the invoice it names, with no field on the payment"
		)
		self.assertEqual(
			(
				doc.status,
				doc.amount_received,
				doc.money_source,
				doc.money_reference,
				str(doc.money_received_on),
			),
			(rules.REFUND_RECEIVED, 44.0, rules.FROM_PAYMENT, "CHK 4410", "2026-11-20"),
		)
		with self.assertRaises(FrappeError) as caught:
			STATE["roles"] = {"Accounts Manager"}
			core.reopen(name, "Oops")
		self.assertIn("Cancel that document", str(caught.exception))
		submit("Payment Entry", "PE-1", method="on_cancel")
		doc = get(name)
		self.assertEqual((doc.status, doc.payment_entry, doc.amount_received), (rules.SENT_BACK, None, None))
		self.assertEqual(doc.waiting_on_money, 1)

	def test_a_refund_that_names_the_paid_bill_the_note_was_set_against(self):
		self.bill(outstanding=0)
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit("Purchase Invoice", made, grand_total=-44.0, outstanding_amount=0, posting_date="2026-11-12")
		update("Purchase Invoice", "PINV-1", outstanding_amount=-44.0)
		self.assertEqual(core.refund_source(name), {"doctype": "Purchase Invoice", "name": "PINV-1"})
		insert(
			"Payment Entry",
			name="PE-2",
			docstatus=0,
			payment_type="Receive",
			party_type="Supplier",
			party=SUPPLIER,
			paid_amount=44.0,
			posting_date="2026-11-20",
		)
		STATE["references"] = {"PE-2": [Row(reference_doctype="Purchase Invoice", reference_name="PINV-1")]}
		submit("Payment Entry", "PE-2")
		self.assertEqual((get(name).status, get(name).payment_entry), (rules.REFUND_RECEIVED, "PE-2"))

	def test_a_payment_to_the_supplier_is_never_taken_for_a_refund(self):
		self.bill()
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit("Purchase Invoice", made, grand_total=-44.0, outstanding_amount=-44.0)
		for pe, kind, party in (("PE-PAY", "Pay", SUPPLIER), ("PE-OTHER", "Receive", OTHER_SUPPLIER)):
			insert(
				"Payment Entry",
				name=pe,
				docstatus=0,
				payment_type=kind,
				party_type="Supplier",
				party=party,
				paid_amount=44.0,
			)
			STATE["references"] = {pe: [Row(reference_doctype="Purchase Invoice", reference_name=made)]}
			submit("Payment Entry", pe)
			self.assertIsNone(get(name).payment_entry, pe)
		self.assertEqual(get(name).status, rules.SENT_BACK)

	def test_no_refund_payment_without_a_debit_note(self):
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		with self.assertRaises(FrappeError) as caught:
			core.refund_source(name)
		self.assertIn("Record Money Received", str(caught.exception))

	def test_linking_documents_made_with_erpnexts_own_buttons(self):
		name = sent_back({"PRI-1": 2})
		insert(
			"Purchase Receipt",
			name="PR-RET-H",
			docstatus=1,
			is_return=1,
			return_against=RECEIPT,
			supplier=SUPPLIER,
		)
		insert(
			"Purchase Receipt",
			name="PR-RET-W",
			docstatus=1,
			is_return=1,
			return_against="PR-OTHER",
			supplier=SUPPLIER,
		)
		with self.assertRaises(FrappeError):
			core.link_purchase_return(name, "PR-RET-W")
		with self.assertRaises(FrappeError):
			core.link_purchase_return(name, RECEIPT)
		core.link_purchase_return(name, "PR-RET-H")
		self.assertIn("took the items out of stock", get(name).stock_state)
		other = sent_back({"PRI-2": 1})
		with self.assertRaises(FrappeError) as caught:
			core.link_purchase_return(other, "PR-RET-H")
		self.assertIn("already linked", str(caught.exception))
		STATE["roles"] = {"Accounts User"}
		insert(
			"Payment Entry",
			name="PE-H",
			docstatus=1,
			payment_type="Receive",
			party_type="Supplier",
			party=SUPPLIER,
			paid_amount=40,
			posting_date="2026-11-15",
		)
		insert(
			"Payment Entry",
			name="PE-P",
			docstatus=1,
			payment_type="Pay",
			party_type="Supplier",
			party=SUPPLIER,
			paid_amount=40,
		)
		with self.assertRaises(FrappeError):
			core.link_payment_entry(name, "PE-P")
		self.assertEqual(core.link_payment_entry(name, "PE-H"), rules.REFUND_RECEIVED)
		self.assertEqual(get(name).amount_received, 40.0)

	def test_the_daily_job_notices_a_debit_note_settled_elsewhere(self):
		self.bill(outstanding=0)
		name = sent_back({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		made = core.make_debit_note(name, "PINV-1")["name"]
		submit(
			"Purchase Invoice", made, grand_total=-44.0, outstanding_amount=-44.0, posting_date="2026-11-12"
		)
		update("Purchase Invoice", made, outstanding_amount=0)
		core.sync_open_returns()
		self.assertEqual(get(name).status, rules.CREDIT_RECEIVED)
		self.assertEqual(STATE["sent"], [], "the daily job sends nothing")


class TestTheBillNotice(Base):
	def bill_doc(self, **overrides):
		data = {"name": "PINV-NEW", "is_return": 0, "items": [Row(pr_detail="PRI-1", po_detail="POI-1")]}
		data.update(overrides)
		return Doc("Purchase Invoice", data)

	def test_the_person_entering_the_bill_is_told(self):
		name = sent_back({"PRI-1": 2})
		core.tell_the_bill(self.bill_doc())
		self.assertEqual(len(STATE["messages"]), 1)
		for words in (name, "2 x TST-PUMP-1", "went back to the supplier"):
			self.assertIn(words, STATE["messages"][0])

	def test_matched_by_the_order_line_when_the_bill_names_no_receipt(self):
		sent_back({"PRI-1": 2})
		core.tell_the_bill(self.bill_doc(items=[Row(po_detail="POI-1")]))
		self.assertEqual(len(STATE["messages"]), 1)

	def test_silent_when_nothing_is_on_a_return(self):
		core.tell_the_bill(self.bill_doc())
		canceled = new_return({"PRI-1": 2})
		core.cancel_return(canceled, "No longer needed")
		core.tell_the_bill(self.bill_doc())
		self.assertEqual(STATE["messages"], [])

	def test_silent_for_a_debit_note_a_job_an_import_and_a_missing_doctype(self):
		sent_back({"PRI-1": 2})
		core.tell_the_bill(self.bill_doc(is_return=1))
		FRAPPE.local.request = None
		core.tell_the_bill(self.bill_doc())
		FRAPPE.local.request = types.SimpleNamespace(headers={})
		FRAPPE.flags.in_import = True
		core.tell_the_bill(self.bill_doc())
		FRAPPE.flags.in_import = False
		STATE["installed"] = False
		core.tell_the_bill(self.bill_doc())
		self.assertEqual(STATE["messages"], [])

	def test_it_never_refuses_a_bill(self):
		sent_back({"PRI-1": 2})
		STATE["sql_breaks"] = True
		core.tell_the_bill(self.bill_doc())
		self.assertEqual(STATE["messages"], [])
		self.assertTrue(STATE["logs"])


class TestTheEmail(Base):
	def send(self, name, **overrides):
		args = {
			"recipient": "returns@supplier.example",
			"subject": "Return request",
			"body": "<p>Please advise.</p>",
		}
		args.update(overrides)
		return core.send_request(name, **args)

	def test_send_emails_once_logs_it_and_marks_requested(self):
		name = new_return({"PRI-1": 2})
		insert("Print Format", name=core.PRINT_FORMAT)
		self.assertEqual(self.send(name, cc=["boss@example.com"]), rules.REQUESTED)
		self.assertEqual(len(STATE["sent"]), 1)
		mail = STATE["sent"][0]
		self.assertEqual(mail["recipients"], ["returns@supplier.example"])
		self.assertEqual(mail["cc"], ["boss@example.com"])
		self.assertEqual(
			mail["message"],
			"<shell><p>Please advise.</p><TABLE:2xTST-PUMP-1></shell>",
			"in the app's one layout, with the items table built here from the return's own lines",
		)
		self.assertEqual((mail["reference_doctype"], mail["reference_name"]), (DOCTYPE, name))
		self.assertEqual(mail["attachments"][0]["fname"], f"{name}.pdf", "the PDF goes with it")
		self.assertEqual(STATE["prints"][0][1]["print_format"], core.PRINT_FORMAT)
		doc = get(name)
		self.assertEqual(
			(doc.status, doc.requested_how, doc.requested_by), (rules.REQUESTED, "Email", "buyer@example.com")
		)
		self.assertEqual(len(STATE["communications"]), 1, "on the timeline, with what was sent")
		self.assertEqual(STATE["communications"][0]["recipients"], "returns@supplier.example")
		self.assertEqual(STATE["communications"][0]["content"], "<p>Please advise.</p><TABLE:2xTST-PUMP-1>")

	def test_the_items_table_goes_where_the_marker_stands(self):
		name = new_return({"PRI-1": 2})
		body = f"<p>Hello,</p><p>{rules.LINES_MARKER}</p><p>Thanks</p>"
		self.send(name, body=body)
		self.assertEqual(
			STATE["sent"][0]["message"], "<shell><p>Hello,</p><TABLE:2xTST-PUMP-1><p>Thanks</p></shell>"
		)
		self.assertEqual(
			api.request_preview(name, "S", body)["html"],
			STATE["sent"][0]["message"],
			"the preview is what goes",
		)

	def test_a_message_that_is_only_the_marker_is_empty(self):
		name = new_return({"PRI-1": 2})
		with self.assertRaises(FrappeError):
			self.send(name, body=f"<p>{rules.LINES_MARKER}</p>")
		self.assertEqual(STATE["sent"], [])

	def test_a_table_typed_into_the_message_is_not_what_lists_the_items(self):
		"""The lines come from the saved return, whatever the request's body claims."""
		name = new_return({"PRI-1": 2})
		self.send(name, body="<p>Returning 500 pumps.</p><script>x</script>")
		message = STATE["sent"][0]["message"]
		self.assertIn("<TABLE:2xTST-PUMP-1>", message)
		self.assertNotIn("<script>", message)

	def test_only_a_contact_is_greeted_by_name(self):
		name = new_return({"PRI-1": 2})
		insert(
			"Contact",
			name="C-1",
			email_id="sam@supplier.example",
			first_name="Sam",
			last_name="Lee",
			is_primary_contact=1,
			modified="2026-11-01",
		)
		insert(
			"Dynamic Link", parent="C-1", parenttype="Contact", link_doctype="Supplier", link_name=SUPPLIER
		)
		doc = get(name)
		self.assertEqual(core.greeting_name(doc, "SAM@supplier.example"), "Sam")
		self.assertEqual(
			core.greeting_name(doc, "sales@supplier.example"), "", "a company's address gets no name"
		)
		self.assertEqual(
			[c["email"] for c in core.recipients(doc)],
			["sales@supplier.example", "sam@supplier.example"],
			"the address on the return first, then the supplier's contacts",
		)

	def test_a_bad_address_sends_nothing(self):
		name = new_return({"PRI-1": 2})
		for recipient in ("", "nobody", "a@b.example, c@d.example"):
			with self.assertRaises(FrappeError):
				self.send(name, recipient=recipient)
		with self.assertRaises(FrappeError):
			self.send(name, cc=["not-an-address"])
		self.assertEqual(STATE["sent"], [])
		self.assertEqual(get(name).status, rules.DRAFT)

	def test_an_empty_message_sends_nothing(self):
		name = new_return({"PRI-1": 2})
		for overrides in ({"subject": " "}, {"body": "<p> </p>"}):
			with self.assertRaises(FrappeError):
				self.send(name, **overrides)
		self.assertEqual(STATE["sent"], [])

	def test_only_a_draft_is_sent(self):
		name = new_return({"PRI-1": 2})
		self.send(name)
		with self.assertRaises(FrappeError):
			self.send(name)
		self.assertEqual(len(STATE["sent"]), 1, "a second click does not send a second request")

	def test_a_stock_user_may_send_and_accounts_may_not(self):
		name = new_return({"PRI-1": 2})
		STATE["roles"] = {"Accounts User"}
		with self.assertRaises(FRAPPE.PermissionError):
			self.send(name)
		self.assertEqual(STATE["sent"], [])
		STATE["roles"] = {"Stock User"}
		self.send(name)
		self.assertEqual(len(STATE["sent"]), 1)

	def test_photos_go_only_when_asked(self):
		name = new_return({"PRI-1": 2})
		insert(
			"File",
			name="F-1",
			attached_to_doctype=DOCTYPE,
			attached_to_name=name,
			is_folder=0,
			file_name="crack.JPG",
		)
		insert(
			"File",
			name="F-2",
			attached_to_doctype=DOCTYPE,
			attached_to_name=name,
			is_folder=0,
			file_name="quote.pdf",
		)
		self.send(name, attach_photos=True)
		self.assertEqual(STATE["sent"][0]["attachments"][1:], [{"fid": "F-1"}])

	def test_the_endpoint_needs_a_person_at_a_screen(self):
		name = new_return({"PRI-1": 2})
		args = (name, "returns@supplier.example", "Return request", "<p>Hi</p>")
		FRAPPE.local.request = types.SimpleNamespace(headers={"Authorization": "token abc:def"})
		with self.assertRaises(FRAPPE.PermissionError):
			api.send_request(*args)
		FRAPPE.local.request = None
		with self.assertRaises(FRAPPE.PermissionError):
			api.send_request(*args)
		FRAPPE.local.request = types.SimpleNamespace(headers={})
		FRAPPE.session.sid = FRAPPE.session.user
		with self.assertRaises(FRAPPE.PermissionError):
			api.send_request(*args)
		self.assertEqual(STATE["sent"], [])
		FRAPPE.session.sid = "a1b2c3d4e5"
		self.assertEqual(api.send_request(*args), rules.REQUESTED)
		self.assertEqual(len(STATE["sent"]), 1)

	def test_nothing_else_ever_sends(self):
		"""Saving, every step, the documents' hooks, the bill notice and the daily job."""
		name = new_return({"PRI-1": 2})
		get(name).save()
		core.mark_requested(name, "Phone")
		core.record_approval(name, "RMA-1")
		core.mark_sent_back(name, "Dropped off")
		made = core.make_purchase_return(name)["name"]
		submit("Purchase Receipt", made)
		STATE["roles"] = {"Accounts User"}
		core.record_money(name, "refund", 44, TODAY, "CHK 1")
		core.sync_open_returns()
		core.tell_the_bill(Doc("Purchase Invoice", {"is_return": 0, "items": [Row(pr_detail="PRI-1")]}))
		self.assertEqual(STATE["sent"], [])

	def test_one_sender_reached_from_one_endpoint(self):
		tree = ast.parse(CORE_PY.read_text(encoding="utf-8"))
		senders = [
			fn.name
			for fn in ast.walk(tree)
			if isinstance(fn, ast.FunctionDef)
			and any(
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Attribute)
				and node.func.attr == "sendmail"
				for node in ast.walk(fn)
			)
		]
		self.assertEqual(senders, ["send_request"])
		callers = []
		for path in (CORE_PY, API_PY):
			for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				if isinstance(fn, ast.FunctionDef) and any(
					isinstance(node, ast.Call)
					and getattr(node.func, "attr", getattr(node.func, "id", "")) == "send_request"
					for node in ast.walk(fn)
				):
					callers.append((path.name, fn.name))
		self.assertEqual(
			callers, [("supplier_returns.py", "send_request")], "the API endpoint, and nothing else"
		)
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertNotIn("supplier_returns.send_request", hooks)


class TestWhatOtherModulesRead(Base):
	def test_neither_argument_gives_nothing(self):
		new_return({"PRI-1": 2})
		self.assertEqual(core.returned_lines(), [])
		self.assertEqual(core.returned_lines(purchase_order_items=[], project=None), [])

	def test_by_order_line_and_by_project(self):
		first = new_return({"PRI-1": 2, "PRI-2": 1})
		lines = core.returned_lines(purchase_order_items=["POI-1"])
		self.assertEqual(len(lines), 1)
		self.assertEqual(tuple(lines[0]), rules.RETURNED_LINE_KEYS)
		self.assertEqual(
			(
				lines[0]["supplier_return"],
				lines[0]["item_code"],
				lines[0]["qty"],
				lines[0]["amount"],
				lines[0]["gone"],
				lines[0]["open"],
			),
			(first, "TST-PUMP-1", 2.0, 40.0, False, True),
		)
		self.assertEqual(len(core.returned_lines(project="PRJ-TEST")), 2)
		self.assertEqual(len(core.returned_lines(purchase_order_items=["POI-2"], project="PRJ-TEST")), 1)
		self.assertEqual(core.returned_lines(project="PRJ-OTHER"), [])
		self.assertEqual(len(core.returned_lines(purchase_order_items=("POI-1", "POI-2", None))), 2)

	def test_gone_once_sent_back_and_closed_once_paid(self):
		name = sent_back({"PRI-1": 2})
		self.assertTrue(core.returned_lines(project="PRJ-TEST")[0]["gone"])
		STATE["roles"] = {"Accounts User"}
		core.record_money(name, "refund", 44, TODAY, "CHK 1")
		line = core.returned_lines(project="PRJ-TEST")[0]
		self.assertEqual((line["gone"], line["open"], line["status"]), (True, False, rules.REFUND_RECEIVED))

	def test_canceled_and_denied_are_left_out(self):
		core.cancel_return(new_return({"PRI-1": 2}), "Mistake")
		core.record_denial(new_return({"PRI-2": 1}), "Too late")
		self.assertEqual(core.returned_lines(project="PRJ-TEST"), [])

	def test_it_never_raises(self):
		new_return({"PRI-1": 2})
		STATE["installed"] = False
		self.assertEqual(core.returned_lines(project="PRJ-TEST"), [], "before the DocType is installed")
		STATE["installed"] = True
		STATE["sql_breaks"] = True
		self.assertEqual(core.returned_lines(project="PRJ-TEST"), [])
		self.assertTrue(STATE["logs"])

	def test_the_signature_is_the_one_promised(self):
		import inspect

		signature = inspect.signature(core.returned_lines)
		self.assertEqual(str(signature), "(purchase_order_items=None, project=None)")


class TestHooksNeverBreakADocument(Base):
	def test_a_document_no_return_names_changes_nothing(self):
		core.on_document_change(Doc("Purchase Receipt", {"name": "PR-0042"}, new=False), "on_submit")
		core.on_document_change(Doc("Sales Invoice", {"name": "SI-1"}, new=False), "on_submit")
		self.assertEqual(STATE["logs"], [])

	def test_a_failure_is_logged_not_raised(self):
		name = sent_back({"PRI-1": 1})
		made = core.make_purchase_return(name)["name"]
		STATE["sql_breaks"] = True
		original = FRAPPE.get_all
		FRAPPE.get_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("away"))
		try:
			core.on_document_change(Doc("Purchase Receipt", {"name": made}, new=False), "on_submit")
		finally:
			FRAPPE.get_all = original
		self.assertTrue(STATE["logs"])

	def test_a_failed_refresh_is_rolled_back_to_its_savepoint(self):
		name = sent_back({"PRI-1": 1})
		made = core.make_purchase_return(name)["name"]
		original = core.sync
		core.sync = lambda name: (_ for _ in ()).throw(RuntimeError("half written"))
		try:
			core.on_document_change(Doc("Purchase Receipt", {"name": made}, new=False), "on_submit")
		finally:
			core.sync = original
		self.assertEqual(
			STATE["savepoints"], [("save", "supplier_return_sync"), ("rollback", "supplier_return_sync")]
		)
		self.assertTrue(STATE["logs"])

	def test_before_the_doctype_exists(self):
		STATE["installed"] = False
		core.on_document_change(Doc("Payment Entry", {"name": "PE-1"}, new=False), "on_submit")
		core.sync_open_returns()
		self.assertEqual(core.linked_to("Purchase Receipt", RECEIPT), [])
		self.assertEqual(STATE["logs"], [])

	def test_a_deleted_draft_is_unlinked(self):
		name = sent_back({"PRI-1": 1})
		made = core.make_purchase_return(name)["name"]
		core.on_document_change(Doc("Purchase Receipt", {"name": made}, new=False), "on_trash")
		self.assertIsNone(get(name).purchase_return)

	def test_what_a_standard_document_is_told(self):
		name = sent_back({"PRI-1": 1})
		made = core.make_purchase_return(name)["name"]
		self.assertEqual(
			core.linked_to("Purchase Receipt", made),
			[{"name": name, "status": rules.SENT_BACK, "role": "the Purchase Return for"}],
		)
		self.assertEqual(core.linked_to("Purchase Receipt", RECEIPT)[0]["role"], core.ORIGINAL_ROLE)
		self.assertEqual(api.linked("Sales Invoice", "SI-1"), [])
		STATE["denied"].add((DOCTYPE, "read"))
		self.assertEqual(api.linked("Purchase Receipt", made), [], "not to somebody who may not read returns")


class TestEndpoints(Base):
	def test_every_endpoint_that_writes_is_post_only(self):
		readers = {
			"receipt_choices",
			"make_from_receipt",
			"lines_for_receipt",
			"state",
			"linked",
			"request_draft",
			"refund_source",
		}
		for name, methods in FRAPPE.whitelisted.items():
			if name in readers:
				self.assertEqual(methods, ["GET", "POST"], name)
			else:
				self.assertEqual(methods, ["POST"], name)
		self.assertTrue(
			{"send_request", "make_purchase_return", "make_debit_note", "record_money", "start_from_scan"}
			<= set(FRAPPE.whitelisted)
		)

	def test_no_endpoint_is_open_to_guests(self):
		self.assertNotIn("allow_guest", API_PY.read_text(encoding="utf-8"))

	def test_who_may_start_one(self):
		reset(roles=("Accounts User",))
		self.assertEqual(api.receipt_choices(item_code="TST-PUMP-1"), {"allowed": False, "receipts": []})
		with self.assertRaises(FRAPPE.PermissionError):
			api.make_from_receipt(RECEIPT)
		reset(roles=("Stock User",))
		STATE["denied"].add((DOCTYPE, "create"))
		with self.assertRaises(FRAPPE.PermissionError):
			api.start_from_scan("PRI-1", 1, "Damaged")

	def test_choices_for_an_item_an_order_and_nothing(self):
		choices = api.receipt_choices(item_code="TST-PUMP-1")
		self.assertTrue(choices["allowed"])
		self.assertEqual(
			[(r["name"], [l["item_code"] for l in r["lines"]]) for r in choices["receipts"]],
			[(RECEIPT, ["TST-PUMP-1"])],
		)
		self.assertEqual(len(api.receipt_choices(purchase_order="PO-0009")["receipts"][0]["lines"]), 3)
		with self.assertRaises(FrappeError):
			api.receipt_choices()
		new_return({"PRI-1": 10})
		self.assertEqual(
			api.receipt_choices(item_code="TST-PUMP-1")["receipts"], [], "nothing left of it to return"
		)

	def test_a_receipt_the_caller_may_not_read_is_not_listed(self):
		STATE["denied"].add(("Purchase Receipt", "read"))
		self.assertEqual(api.receipt_choices(item_code="TST-PUMP-1")["receipts"], [])

	def test_the_mapper_passes_its_arguments_on_frappe_flags(self):
		FRAPPE.flags.args = _dict(filtered_children=["PRI-2"])
		doc = api.make_from_receipt(RECEIPT)
		self.assertEqual([row.item_code for row in doc.items], ["TST-FITTING-1"])

	def test_stock_scan_saves_a_draft_for_one_line(self):
		reset(roles=("Stock User",))
		made = api.start_from_scan("PRI-1", "2", "Defective", " Leaks at the seal ")
		doc = get(made["name"])
		self.assertEqual(doc.status, rules.DRAFT)
		self.assertEqual(
			[(r.item_code, r.qty, r.reason, r.condition_notes) for r in doc.items],
			[("TST-PUMP-1", 2.0, "Defective", "Leaks at the seal")],
		)
		with self.assertRaises(FrappeError):
			api.start_from_scan("PRI-1", 99, "Defective")
		with self.assertRaises(FrappeError):
			api.start_from_scan("PRI-NOPE", 1, "Defective")
		self.assertEqual(STATE["sent"], [])


def _code_only(path):
	"""A Python file with its comments and docstrings removed, for an absence check."""
	tree = ast.parse(path.read_text(encoding="utf-8"))
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef) and node.body:
			first = node.body[0]
			if (
				isinstance(first, ast.Expr)
				and isinstance(first.value, ast.Constant)
				and isinstance(first.value.value, str)
			):
				node.body[0] = ast.Pass()
	return ast.unparse(tree)


def _js_list(path, name):
	text = Path(path).read_text(encoding="utf-8")
	match = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];", text, flags=re.S)
	assert match, f"{path} has no `const {name} = [...]`"
	return re.findall(r"\"([^\"]+)\"", match.group(1))


class TestWiring(unittest.TestCase):
	"""No stub needed: files against each other."""

	@classmethod
	def setUpClass(cls):
		cls.hooks = HOOKS.read_text(encoding="utf-8")
		cls.parent = json.loads((DOCTYPE_DIR / "supplier_return.json").read_text(encoding="utf-8"))
		cls.child = json.loads((CHILD_DIR / "supplier_return_item.json").read_text(encoding="utf-8"))
		cls.fields = {f["fieldname"]: f for f in cls.parent["fields"]}
		cls.lines = {f["fieldname"]: f for f in cls.child["fields"]}

	def options(self, field, table=None):
		return [o for o in (table or self.fields)[field]["options"].split("\n") if o]

	def test_the_doctypes_options_are_the_rules(self):
		self.assertEqual(self.options("status"), list(rules.STATUSES))
		self.assertEqual(self.options("requested_resolution"), list(rules.RESOLUTIONS))
		self.assertEqual(self.options("return_method"), list(rules.METHODS))
		self.assertEqual(self.options("requested_how"), list(rules.REQUEST_WAYS))
		self.assertEqual(self.options("money_source"), list(rules.MONEY_SOURCES))
		self.assertEqual(self.options("reason", self.lines), list(rules.REASONS))
		self.assertEqual(self.options("line_kind", self.lines), list(rules.LINE_KINDS))
		self.assertEqual(self.fields["status"]["default"], rules.DRAFT)

	def test_what_only_an_action_sets_is_read_only_on_the_form(self):
		for field in core.ACTION_FIELDS:
			self.assertEqual(self.fields[field].get("read_only"), 1, field)
			self.assertEqual(self.fields[field].get("no_copy"), 1, f"{field}: a duplicate must start clean")
		for field in (
			"stock_state",
			"record_only_note",
			"waiting_on_money",
			"amount_difference",
			"expected_amount",
			"total_amount",
		):
			self.assertEqual(self.fields[field].get("read_only"), 1, field)

	def test_the_links_point_at_erpnexts_own_documents(self):
		self.assertEqual(self.fields["purchase_return"]["options"], "Purchase Receipt")
		self.assertEqual(self.fields["debit_note"]["options"], "Purchase Invoice")
		self.assertEqual(self.fields["payment_entry"]["options"], "Payment Entry")
		self.assertEqual(self.fields["replacement_receipt"]["options"], "Purchase Receipt")

	def test_permissions_are_the_roles_the_rules_name(self):
		perms = {p["role"]: p for p in self.parent["permissions"]}
		self.assertEqual(set(perms), set(rules.ALL_ROLES) | {"Accounts Manager"})
		for role in rules.CREATE_ROLES:
			self.assertEqual((perms[role].get("create"), perms[role].get("write")), (1, 1), role)
		for role in ("Accounts User", "Accounts Manager"):
			self.assertFalse(perms[role].get("create"), f"{role} records money; it does not start returns")
			self.assertEqual(perms[role].get("write"), 1, role)
		self.assertEqual(
			{r for r, p in perms.items() if p.get("delete")}, {"System Manager", "Purchase Manager"}
		)
		self.assertFalse(
			any(p.get("submit") or p.get("cancel") for p in perms.values()), "not a submittable doctype"
		)

	def test_it_sits_in_its_module_with_controllers_frappe_can_find(self):
		self.assertEqual(self.parent["module"], "Inventory Enhancements")
		self.assertEqual(self.child["module"], "Inventory Enhancements")
		self.assertEqual(self.child.get("istable"), 1)
		self.assertIn(
			"class SupplierReturn(Document)", (DOCTYPE_DIR / "supplier_return.py").read_text(encoding="utf-8")
		)
		self.assertIn(
			"class SupplierReturnItem(Document)",
			(CHILD_DIR / "supplier_return_item.py").read_text(encoding="utf-8"),
		)

	def test_no_custom_field_is_added_to_a_standard_doctype(self):
		fixture = (APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8")
		self.assertNotIn("supplier_return", fixture)

	def test_hooks_register_what_exists(self):
		for dotted in re.findall(
			r"erpnext_enhancements\.inventory_enhancements\.supplier_returns\.(\w+)", self.hooks
		):
			self.assertTrue(callable(getattr(core, dotted, None)), dotted)
		tree = ast.parse(self.hooks)
		events = next(
			n.value for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "doc_events"
		)
		by_doctype = {
			k.value: ast.literal_eval(v)
			for k, v in zip(events.keys, events.values, strict=True)
			if isinstance(k, ast.Constant)
		}
		handler = "erpnext_enhancements.inventory_enhancements.supplier_returns.on_document_change"
		for doctype in ("Purchase Receipt", "Purchase Invoice", "Payment Entry"):
			for event in ("on_submit", "on_cancel", "on_trash"):
				registered = by_doctype[doctype][event]
				self.assertIn(
					handler, registered if isinstance(registered, list) else [registered], (doctype, event)
				)
		self.assertEqual(
			by_doctype["Purchase Receipt"]["on_submit"][0],
			"erpnext_enhancements.po_order_stage.advance_on_receipt",
			"the order stage is walked back before the return is refreshed",
		)
		self.assertEqual(
			by_doctype["Purchase Receipt"]["validate"],
			"erpnext_enhancements.inventory_enhancements.maintain_stock.refuse_unstocked_receipt_return",
			"the Maintain Stock guard is untouched",
		)
		self.assertIn("supplier_returns.tell_the_bill", by_doctype["Purchase Invoice"]["validate"][1])

	def test_the_start_script_is_loaded_where_a_return_starts(self):
		tree = ast.parse(self.hooks)
		scripts = next(
			n.value for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "doctype_js"
		)
		by_doctype = {
			k.value: ast.literal_eval(v)
			for k, v in zip(scripts.keys, scripts.values, strict=True)
			if isinstance(k, ast.Constant)
		}
		for doctype in ("Purchase Receipt", "Purchase Order", "Purchase Invoice", "Payment Entry", "Project"):
			self.assertIn("public/js/supplier_return_start.js", by_doctype[doctype], doctype)
		self.assertNotIn("Supplier Return", by_doctype, "an app doctype's own script loads by itself")
		self.assertTrue((APP / "public" / "js" / "supplier_return_start.js").is_file())

	def test_the_print_format_is_made_before_the_pdf_backend_is_set(self):
		mine = self.hooks.index("setup_supplier_return_print_format.ensure_supplier_return_print_format")
		chrome = self.hooks.index(
			'"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator"'
		)
		self.assertLess(mine, chrome)

	def test_patches_and_the_daily_job_are_registered(self):
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		for patch in ("seed_supplier_return_request_template", "supplier_returns_hub_links"):
			self.assertIn(f"erpnext_enhancements.patches.{patch}", patches)
			self.assertTrue((APP / "patches" / f"{patch}.py").is_file())
		self.assertIn("supplier_returns.sync_open_returns", self.hooks)

	def test_nothing_here_writes_the_ledger(self):
		source = _code_only(CORE_PY) + _code_only(API_PY)
		for banned in ("GL Entry", "make_gl_entries", "make_sl_entries", ".submit("):
			self.assertNotIn(banned, source, banned)
		for path in (CORE_PY, API_PY):
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				targets = node.targets if isinstance(node, ast.Assign) else []
				self.assertFalse(
					[t for t in targets if isinstance(t, ast.Attribute) and t.attr == "docstatus"],
					f"{path.name} sets a docstatus: submitting is the person's click on ERPNext's own form",
				)
		self.assertIn("make_purchase_return as erpnext_make_purchase_return", source)
		self.assertIn("make_debit_note as erpnext_make_debit_note", source)

	def test_the_scripts_lists_are_the_rules(self):
		form = DOCTYPE_DIR / "supplier_return.js"
		self.assertEqual(_js_list(form, "SR_METHODS"), list(rules.METHODS))
		self.assertEqual(_js_list(form, "SR_WAYS"), [way for way in rules.REQUEST_WAYS if way != "Email"])
		scan = APP / "public" / "js" / "stock_scan" / "app.js"
		self.assertEqual(_js_list(scan, "RETURN_REASONS"), list(rules.REASONS))
		listing = (DOCTYPE_DIR / "supplier_return_list.js").read_text(encoding="utf-8")
		for status in rules.STATUSES:
			self.assertIn(status, listing, f"the list has no color for {status}")
		report = (
			INVENTORY
			/ "report"
			/ "supplier_returns_money_outstanding"
			/ "supplier_returns_money_outstanding.js"
		).read_text(encoding="utf-8")
		for view in rules.REPORT_VIEWS:
			self.assertIn(f'"{view}"', report)

	def test_the_form_script_dials_endpoints_that_exist(self):
		for script in (
			DOCTYPE_DIR / "supplier_return.js",
			APP / "public" / "js" / "supplier_return_start.js",
		):
			text = script.read_text(encoding="utf-8")
			prefix = re.search(r"const (?:SR_API|API) = \"([\w.]+)\";", text).group(1)
			self.assertEqual(prefix, "erpnext_enhancements.api.supplier_returns.")
			for name in set(re.findall(r"(?:SR_API|API) \+ \"(\w+)\"", text)) | set(
				re.findall(r"sr_(?:call|step)\(\s*(?:frm,\s*)?\"(\w+)\"", text)
			):
				self.assertIn(name, FRAPPE.whitelisted, f"{script.name} calls {name}")
		form = (DOCTYPE_DIR / "supplier_return.js").read_text(encoding="utf-8")
		start = (APP / "public" / "js" / "supplier_return_start.js").read_text(encoding="utf-8")
		scan = (APP / "public" / "js" / "stock_scan" / "transport.js").read_text(encoding="utf-8")
		for name in FRAPPE.whitelisted:
			self.assertTrue(
				f'"{name}"' in form or f'"{name}"' in start or f'.{name}"' in scan,
				f"api.supplier_returns.{name} has no caller in the form, the start script or Stock Scan",
			)

	def test_stock_scans_endpoint_map(self):
		transport = (APP / "public" / "js" / "stock_scan" / "transport.js").read_text(encoding="utf-8")
		block = re.search(r"export const RET = \{(.*?)\};", transport, flags=re.S).group(1)
		dialled = dict(re.findall(r"(\w+):\s*\"([\w.]+)\"", block))
		self.assertEqual(set(dialled), {"CHOICES", "START"})
		for dotted in dialled.values():
			module, _, name = dotted.rpartition(".")
			self.assertEqual(module, "erpnext_enhancements.api.supplier_returns")
			self.assertIn(name, FRAPPE.whitelisted)
		self.assertEqual(FRAPPE.whitelisted["start_from_scan"], ["POST"])
		app = (APP / "public" / "js" / "stock_scan" / "app.js").read_text(encoding="utf-8")
		sent = re.search(r"call\(RET\.START, \{(.*?)\}\)", app, flags=re.S).group(1)
		import inspect

		self.assertTrue(
			set(re.findall(r"(\w+):", sent)) <= set(inspect.signature(api.start_from_scan).parameters)
		)
		self.assertIn("can_return", (APP / "api" / "stock_scan.py").read_text(encoding="utf-8"))

	def test_the_tracker_offers_the_return_only_through_the_launcher(self):
		tracker = (APP / "public" / "js" / "project_enhancements.js").read_text(encoding="utf-8")
		for words in (
			"canReturn(group.doctype, doc)",
			"returnToSupplier(doc.name)",
			"canReturnLine(row)",
			"launcher.start_from_receipt",
		):
			self.assertIn(words, tracker)

	def test_the_workspace_has_the_purchasing_lines(self):
		workspace = json.loads(
			(INVENTORY / "workspace" / "inventory_enhancements" / "inventory_enhancements.json").read_text(
				encoding="utf-8"
			)
		)
		links = {(l.get("link_type"), l.get("link_to")) for l in workspace["links"]}
		self.assertIn(("DocType", DOCTYPE), links)
		self.assertIn(("Report", "Supplier Returns Money Outstanding"), links)
		shortcut = next(s for s in workspace["shortcuts"] if s["label"] == "Supplier Returns")
		self.assertEqual(json.loads(shortcut["stats_filter"]), {"waiting_on_money": 1})
		self.assertIn("waiting on money", shortcut["format"])
		self.assertIn("Supplier Returns", workspace["content"])
		self.assertGreaterEqual(
			workspace["modified"], "2026-11-12", "a workspace file imports only when it reads newer"
		)
		card = next(
			l for l in workspace["links"] if l.get("type") == "Card Break" and l.get("label") == "Purchasing"
		)
		self.assertEqual(card["link_count"], 4)

	def test_the_report_is_a_script_report_for_the_people_who_chase_money(self):
		report = json.loads(
			(
				INVENTORY
				/ "report"
				/ "supplier_returns_money_outstanding"
				/ "supplier_returns_money_outstanding.json"
			).read_text(encoding="utf-8")
		)
		self.assertEqual(
			(report["report_type"], report["ref_doctype"], report["module"]),
			("Script Report", DOCTYPE, "Inventory Enhancements"),
		)
		self.assertTrue(
			{"Accounts User", "Accounts Manager", "Purchase Manager"} <= {r["role"] for r in report["roles"]}
		)
		source = (
			INVENTORY
			/ "report"
			/ "supplier_returns_money_outstanding"
			/ "supplier_returns_money_outstanding.py"
		).read_text(encoding="utf-8")
		self.assertNotRegex(
			source,
			r"fields=\[[^\]]*\b(count|sum)\(",
			"frappe 16 refuses a function written as a string field",
		)
		self.assertIn("frappe.get_list", source, "the reader's own permissions apply")


class TestPatchesAndPrintFormat(Base):
	def run_patch(self, name):
		import importlib

		sys.modules.pop(f"erpnext_enhancements.patches.{name}", None)
		module = importlib.import_module(f"erpnext_enhancements.patches.{name}")
		module.execute()
		return module

	def test_the_template_is_seeded_once_and_never_overwritten(self):
		inserted = []
		original = FRAPPE.get_doc
		FRAPPE.get_doc = lambda data, *a: types.SimpleNamespace(insert=lambda **k: inserted.append((data, k)))
		try:
			self.run_patch("seed_supplier_return_request_template")
			self.assertEqual(len(inserted), 1)
			data, kwargs = inserted[0]
			self.assertEqual(kwargs["set_name"], rules.EMAIL_TEMPLATE)
			self.assertEqual(
				(data["subject"], data["response_html"], data["use_html"]),
				(rules.DEFAULT_SUBJECT, rules.DEFAULT_BODY, 1),
			)
			insert("Email Template", name=rules.EMAIL_TEMPLATE)
			self.run_patch("seed_supplier_return_request_template")
			self.assertEqual(len(inserted), 1, "safe twice, and an edited template is left alone")
		finally:
			FRAPPE.get_doc = original
		self.assertEqual(STATE["sent"], [], "seeding sends nothing")

	def test_the_patches_cannot_raise(self):
		original_exists, original_doc = FRAPPE.db.exists, FRAPPE.get_doc
		FRAPPE.db.exists = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("away"))
		FRAPPE.get_app_path = lambda *a: (_ for _ in ()).throw(RuntimeError("away"))
		try:
			self.run_patch("seed_supplier_return_request_template")
			self.run_patch("supplier_returns_hub_links")
		finally:
			FRAPPE.db.exists, FRAPPE.get_doc = original_exists, original_doc
		self.assertTrue(STATE["logs"])

	def test_the_print_format_renders_a_request_and_a_packing_slip(self):
		import jinja2

		sys.modules.pop(
			"erpnext_enhancements.inventory_enhancements.setup_supplier_return_print_format", None
		)
		from erpnext_enhancements.inventory_enhancements import setup_supplier_return_print_format as setup

		self.assertEqual(
			(setup.FORMAT, setup.DOCTYPE, setup.MODULE),
			(core.PRINT_FORMAT, DOCTYPE, "Inventory Enhancements"),
		)
		env = jinja2.Environment(undefined=jinja2.StrictUndefined)
		fake = types.SimpleNamespace(
			db=types.SimpleNamespace(get_value=lambda *a, **k: "2026-11-01"),
			utils=types.SimpleNamespace(fmt_money=lambda value, **k: f"${float(value or 0):,.2f}"),
		)
		env.globals.update(
			frappe=fake,
			ps_party=lambda doc: "PARTY",
			ps_date=lambda v: f"D{v}",
			ps_qty=lambda v: f"{float(v):g}",
			ps_uom=lambda v: v or "",
			ps_address=lambda v: v,
			ps_rich=lambda v: v,
		)
		doc = Doc(
			DOCTYPE,
			{
				"name": "SRET-2026-00001",
				"owner": "buyer@example.com",
				"posting_date": "2026-11-12",
				"purchase_receipt": RECEIPT,
				"requested_resolution": rules.REFUND,
				"total_amount": 40.0,
				"tax_amount": 4.0,
				"restocking_fee": 5.0,
				"expected_amount": 39.0,
				"request_notes": "Box <crushed> on arrival",
			},
		)
		doc.set(
			"items",
			[
				Row(
					item_code="TST-PUMP-1",
					item_name="Test pump",
					qty=2,
					uom="Nos",
					rate=20.0,
					amount=40.0,
					reason="Damaged",
					condition_notes="Cracked & leaking",
					purchase_order="PO-0009",
				)
			],
		)
		page = env.from_string(setup.HTML).render(doc=doc)
		for words in (
			"Return Request",
			"SRET-2026-00001",
			"PARTY",
			"Refund",
			"TST-PUMP-1",
			"Damaged: Cracked &amp; leaking",
			"PO-0009",
			"$39.00",
			"Restocking fee",
			"Box &lt;crushed&gt; on arrival",
			"Please reply with a return authorization number",
		):
			self.assertIn(words, page)
		for internal in ("Record only", "Typed by hand", "stock_state", "Stock handling"):
			self.assertNotIn(internal, page)
		doc.supplier_return_number, doc.return_method, doc.carrier, doc.tracking_number = (
			"RMA-5521",
			"Carrier",
			"Test Freight",
			"1Z999",
		)
		doc.requested_resolution = rules.REPLACEMENT
		page = env.from_string(setup.HTML).render(doc=doc)
		for words in ("RMA-5521", "Test Freight", "1Z999"):
			self.assertIn(words, page)
		self.assertNotIn("Expected back", page, "a replacement asks for goods, not money")
		self.assertNotIn("Please reply with a return authorization number", page)


class TestWhatOnlyABenchCanProve(unittest.TestCase):
	"""Written down so nobody reads a green run as more than it is."""

	def test_the_rehearsal_is_documented(self):
		doc = (REPO_ROOT / "docs" / "supplier-returns.md").read_text(encoding="utf-8")
		for words in (
			"What only a real bench can prove",
			"Rehearsal on the production mirror",
			"Stock Received But Not Billed",
		):
			self.assertIn(words, doc)


if __name__ == "__main__":
	unittest.main()
