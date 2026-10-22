# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for bin assignments (v1.689.0, ER-2026-1319376 / ER-2026-1320545).

``inventory_enhancements/bin_assignments.py`` puts items on bins and takes them off (ERPNext
Putaway Rules), pushes stock out of a bin, sends it to the boneyard, and corrects a count. It
runs here against the **real** ``api/stock_scan.py`` (whose reference check, log row, transfer
builder and Undo it reuses) on a ``frappe`` stub installed in ``setUpModule``: an in-memory
store whose Putaway Rule mimics ERPNext's own ``validate`` (Duplicate, Insufficient Capacity,
capacity above 0, priority >= 1) and whose ``insert``/``save``/``submit``/``cancel`` check the
caller's permission unless ``ignore_permissions`` is passed -- so a rule written past the
framework, or a new rule left with ERPNext's unset ``stock_capacity``, fails here.

What it pins: ``ensure_assignment`` is idempotent (create / existing / re-enable) and refuses in
sentences; the permission refusals come before any write; ``unassign`` refuses with stock on
hand and clears only the matching Item Default; Undo puts every rule and Item Default back;
``move_to`` with *make it the item's home* moves the assignment; ``send_to_boneyard`` reads the
setting and never makes a rule at the boneyard; ``set_count`` posts a Stock Reconciliation, not
a Material Issue; the location listing's four conditions (on the SQL text); the backfill's
selection and idempotency; the Boneyard patch; the Boneyard gets a QR label like any location;
and the source rules (POST-only, role check first, the log row before any write, no
``ignore_permissions``, no ``count(...)`` string in a ``get_all``).

Run: python -m unittest erpnext_enhancements.tests.test_bin_assignments -v
"""

import ast
import copy
import datetime
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "inventory_enhancements" / "bin_assignments.py"
API = APP / "api" / "stock_scan.py"
BACKFILL = APP / "patches" / "backfill_bin_putaway_rules.py"
BONEYARD_PATCH = APP / "patches" / "add_boneyard_warehouse.py"
LOG_JSON = APP / "inventory_enhancements" / "doctype" / "stock_scan_log" / "stock_scan_log.json"
SETTINGS_JSON = (
	APP
	/ "inventory_enhancements"
	/ "doctype"
	/ "inventory_scanner_settings"
	/ "inventory_scanner_settings.json"
)
PATCHES_TXT = APP / "patches.txt"

COMPANY = "Example Co"
BIN_A = "Bin A1-1-1 - SF"
BIN_B = "Bin A1-1-2 - SF"
STORES = "Stores - SF"
BONEYARD = "Boneyard - SF"
USER = "stock.lead@example.com"

#: Modules this suite imports against its stub, popped before and after so other suites in the
#: same process get their own.
_OWN_MODULES = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"erpnext_enhancements.api.stock_scan",
	"erpnext_enhancements.inventory_enhancements.bin_assignments",
	"erpnext_enhancements.inventory_enhancements.stock_accounts",
	"erpnext_enhancements.inventory_enhancements.warehouse_labels",
	"erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings",
	"erpnext_enhancements.patches.backfill_bin_putaway_rules",
	"erpnext_enhancements.patches.add_boneyard_warehouse",
)

FRAPPE = None
STATE = {}
scan = None
bins = None
backfill = None
boneyard_patch = None
labels = None


# --------------------------------------------------------------------------- the frappe stub


class _dict(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _cmp(value, condition):
	if isinstance(condition, (list, tuple)) and len(condition) == 2 and isinstance(condition[0], str):
		op, want = condition
		if op == "in":
			return value in want
		if op == ">":
			return (value or 0) > want
		if op == "<":
			return (value or 0) < want
		if op == "!=":
			return value != want
	return value == condition


def _matches(record, filters):
	if not filters:
		return True
	return all(_cmp(record.get(key), cond) for key, cond in filters.items())


def _table(doctype):
	return STATE["db"].setdefault(doctype, {})


def _next(prefix):
	STATE["seq"] += 1
	return f"{prefix}-{STATE['seq']:04d}"


class Doc:
	"""Just enough of ``frappe.model.document.Document``: a copy of the stored row, written back
	on insert/save/submit/cancel/db_set, with the per-doctype rules the tests lean on."""

	def __init__(self, doctype, data):
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "_data", _dict(data))
		object.__setattr__(self, "flags", _dict())

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self._data.get(key)

	def __setattr__(self, key, value):
		self._data[key] = value

	def get(self, key, default=None):
		value = self._data.get(key)
		return default if value is None else value

	def append(self, field, row):
		self._data.setdefault(field, []).append(_dict(row))
		return row

	def _check(self, ptype, ignore_permissions):
		if not ignore_permissions and not FRAPPE.has_permission(self.doctype, ptype):
			raise FRAPPE.PermissionError(f"No permission to {ptype} {self.doctype}")

	def _store(self):
		_table(self.doctype)[self._data["name"]] = copy.deepcopy(dict(self._data))

	def _validate(self):
		if self.doctype == "Putaway Rule" and not self.flags.get("ignore_validate"):
			for other in _table("Putaway Rule").values():
				if (
					other["item_code"] == self.item_code
					and other["warehouse"] == self.warehouse
					and other["name"] != self.name
				):
					raise FRAPPE.ValidationError("Duplicate")
			balance = _bin_qty(self.item_code, self.warehouse)
			if float(self.stock_capacity or 0) < balance:
				raise FRAPPE.ValidationError("Insufficient Capacity")
			if not self.capacity:
				raise FRAPPE.ValidationError("Capacity must be greater than 0")
			if int(self.priority or 0) < 1:
				raise FRAPPE.ValidationError("Priority cannot be lesser than 1.")
			self.stock_capacity = float(self.conversion_factor or 1) * float(self.capacity)

	def insert(self, ignore_permissions=False):
		self._check("create", ignore_permissions)
		STATE["writes"].append(("insert", self.doctype, bool(ignore_permissions)))
		if self.doctype == "Stock Scan Log":
			if any(r.get("client_ref") == self.client_ref for r in _table(self.doctype).values()):
				raise FRAPPE.UniqueValidationError("client_ref")
			self.name = _next("SCAN")
		elif self.doctype == "Putaway Rule":
			self.name = _next("PUT")
			self.creation = STATE["seq"]
		elif self.doctype == "Warehouse":
			self.name = f"{self.warehouse_name} - SF"
		elif self.doctype == "Stock Entry":
			self.name = _next("SE")
		elif self.doctype == "Stock Reconciliation":
			self.name = _next("SR")
		else:
			self.name = self.name or _next(self.doctype.upper().replace(" ", ""))
		self._validate()
		self.docstatus = 0
		self._store()
		return self

	def save(self, ignore_permissions=False):
		self._check("write", ignore_permissions)
		STATE["writes"].append(("save", self.doctype, bool(ignore_permissions)))
		self._validate()
		self._store()
		return self

	def submit(self):
		self._check("submit", False)
		self.docstatus = 1
		self._store()

	def cancel(self):
		self._check("cancel", False)
		self.docstatus = 2
		self._store()

	def db_set(self, values, update_modified=True):
		self._data.update(values)
		self._store()


def _bin_qty(item_code, warehouse):
	for row in _table("Bin").values():
		if row["item_code"] == item_code and row["warehouse"] == warehouse:
			return float(row.get("actual_qty") or 0)
	return 0.0


def _install_frappe_stub():
	global FRAPPE
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	class UniqueValidationError(ValidationError):
		pass

	class DuplicateEntryError(ValidationError):
		pass

	class DoesNotExistError(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.UniqueValidationError = UniqueValidationError
	frappe.DuplicateEntryError = DuplicateEntryError
	frappe.DoesNotExistError = DoesNotExistError
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _dict
	frappe.bold = lambda text: text

	def throw(msg="", exc=ValidationError, title=None):
		raise exc(msg)

	frappe.throw = throw

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			fn._whitelist = dkwargs
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user=USER)
	frappe.get_roles = lambda user=None: list(STATE["roles"])

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		allowed = STATE["perm"].get((doctype, ptype), True)
		STATE["perm_checks"].append((doctype, ptype, doc))
		if throw and not allowed:
			raise PermissionError(doctype)
		return allowed

	frappe.has_permission = has_permission

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			data = dict(doctype)
			return Doc(data.pop("doctype"), data)
		record = _table(doctype).get(name)
		if record is None:
			raise DoesNotExistError(f"{doctype} {name}")
		return Doc(doctype, copy.deepcopy(record))

	frappe.get_doc = get_doc
	frappe.new_doc = lambda doctype: Doc(doctype, {})

	def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kwargs):
		STATE["get_all"].append((doctype, fields))
		rows = [_dict(r) for r in _table(doctype).values() if _matches(r, filters)]
		if order_by and order_by.startswith("disable asc"):
			rows.sort(key=lambda r: (int(r.get("disable") or 0), r.get("creation") or 0))
		elif order_by and order_by.startswith("creation"):
			rows.sort(key=lambda r: r.get("creation") or 0)
		if limit:
			rows = rows[:limit]
		if pluck:
			return [r.get(pluck) for r in rows]
		return [_dict({f: r.get(f) for f in fields}) for r in rows] if fields else rows

	frappe.get_all = get_all

	def exists(doctype, name=None):
		if doctype == "DocType":
			return name in STATE["doctypes"]
		if isinstance(name, dict):
			found = [r for r in _table(doctype).values() if _matches(r, name)]
			return found[0]["name"] if found else None
		return name if name in _table(doctype) else None

	def get_value(doctype, filters, fieldname="name", as_dict=False, order_by=None, **kwargs):
		if doctype == "User":
			return None
		if isinstance(filters, str):
			record = _table(doctype).get(filters)
		else:
			found = [r for r in _table(doctype).values() if _matches(r, filters)]
			record = found[0] if found else None
		if record is None:
			return None
		if isinstance(fieldname, (list, tuple)):
			if as_dict:
				return _dict({f: record.get(f) for f in fieldname})
			return [record.get(f) for f in fieldname]
		return record.get(fieldname)

	def set_value(doctype, name, field, value=None, update_modified=True):
		values = field if isinstance(field, dict) else {field: value}
		STATE["set_values"].append((doctype, name, dict(values)))
		_table(doctype)[name].update(values)

	def sql(query, values=None, as_dict=False, **kwargs):
		text = " ".join(query.split())
		STATE["sql"].append(text)
		if "from tabSingles" in text:
			return [(field,) for (dt, field) in STATE["singles"] if dt == values[0]]
		if "`tabPutaway Rule` pr" in text:
			return []
		if "from `tabItem Default`" in text and "parentfield" in text:
			return [
				(r["name"],)
				for r in sorted(_table("Item Default").values(), key=lambda r: r.get("idx") or 0)
				if r["parent"] == values["item"]
				and r.get("parenttype") == "Item"
				and r.get("default_warehouse") == values["warehouse"]
				and r.get("company") == values["company"]
			]
		if text.startswith("select item_code, warehouse, actual_qty from `tabBin`"):
			return [_dict(r) for r in _table("Bin").values()]
		if text.startswith("select name, is_stock_item"):
			return [_dict(r) for r in _table("Item").values()]
		if text.startswith("select name, is_group"):
			return [_dict(r) for r in _table("Warehouse").values()]
		if text.startswith("select item_code, warehouse from `tabPutaway Rule`"):
			return [(r["item_code"], r["warehouse"]) for r in _table("Putaway Rule").values()]
		return []

	def savepoint(name):
		STATE["savepoints"][name] = copy.deepcopy(STATE["db"])

	def rollback(save_point=None):
		if save_point in STATE["savepoints"]:
			STATE["db"] = copy.deepcopy(STATE["savepoints"][save_point])

	def set_single_value(doctype, field, value):
		STATE["singles"][(doctype, field)] = value

	frappe.db = types.SimpleNamespace(
		exists=exists,
		get_value=get_value,
		set_value=set_value,
		sql=sql,
		savepoint=savepoint,
		rollback=rollback,
		table_exists=lambda doctype: doctype in STATE["tables"],
		set_single_value=set_single_value,
		get_single_value=lambda doctype, field: STATE["singles"].get((doctype, field)),
	)
	frappe.get_cached_value = lambda doctype, name, field: None
	frappe.get_cached_doc = lambda *a, **k: Doc("Inventory Scanner Settings", {})
	frappe.clear_document_cache = lambda doctype, name=None: STATE["cache_cleared"].append((doctype, name))
	frappe.log_error = lambda title=None, message=None, **kwargs: STATE["errors"].append((title, message))
	frappe.get_traceback = lambda: "traceback"
	frappe.parse_json = json.loads

	def delete_doc(doctype, name, ignore_permissions=False, **kwargs):
		if not ignore_permissions and not has_permission(doctype, "delete"):
			raise PermissionError(f"No permission to delete {doctype}")
		STATE["writes"].append(("delete", doctype, bool(ignore_permissions)))
		_table(doctype).pop(name, None)

	frappe.delete_doc = delete_doc

	utils = types.ModuleType("frappe.utils")

	def flt(value, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	def cint(value):
		try:
			return int(float(value or 0))
		except (TypeError, ValueError):
			return 0

	def get_datetime(value=None):
		if value is None:
			return STATE["now"]
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.fromisoformat(str(value))

	utils.flt = flt
	utils.cint = cint
	utils.cstr = lambda value: "" if value is None else str(value)
	utils.now_datetime = lambda: STATE["now"]
	utils.get_datetime = get_datetime
	utils.getdate = lambda value=None: (get_datetime(value) if value else STATE["now"]).date()
	utils.nowdate = lambda: STATE["now"].date().isoformat()
	utils.add_days = lambda value, days: get_datetime(value) + datetime.timedelta(days=days)
	utils.formatdate = lambda value=None, *a, **k: str(value)
	utils.strip_html = lambda text: re.sub(r"<[^>]+>", "", text or "")
	utils.get_url = lambda *a, **k: "https://erp.example.com"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	FRAPPE = frappe


def setUpModule():
	global scan, bins, backfill, boneyard_patch, labels
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)
	_install_frappe_stub()
	from erpnext_enhancements.api import stock_scan as scan_module
	from erpnext_enhancements.inventory_enhancements import bin_assignments as bins_module
	from erpnext_enhancements.inventory_enhancements import warehouse_labels as labels_module
	from erpnext_enhancements.patches import add_boneyard_warehouse as boneyard_module
	from erpnext_enhancements.patches import backfill_bin_putaway_rules as backfill_module

	scan, bins, backfill, boneyard_patch, labels = (
		scan_module,
		bins_module,
		backfill_module,
		boneyard_module,
		labels_module,
	)
	# What the saves reach past this suite's concern: the payload a save returns, the settings
	# Single, the accounts resolver (tests/test_stock_entry_builders.py pins it) and the rate.
	scan._result = lambda name, repeated=False, undone=False, already_canceled=False: {
		"log": name,
		"repeated": repeated,
		"undone": undone,
	}
	scan.get_settings = lambda: dict(STATE["settings"])
	scan._difference_account = lambda company, item_code, configured=None: "5119 - Stock Adjustment - SF"
	scan._receipt_rate = lambda item_code, warehouse, company: STATE["rate"]
	labels.qr_svg = types.SimpleNamespace(qr_svg=lambda url, title=None: "<svg/>")


def tearDownModule():
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)


def _warehouse(name, warehouse_name, **values):
	row = {
		"name": name,
		"warehouse_name": warehouse_name,
		"company": COMPANY,
		"is_group": 0,
		"disabled": 0,
		"warehouse_type": "",
		"parent_warehouse": "Inventory - SF",
		"lft": 10,
		"rgt": 11,
	}
	row.update(values)
	_table("Warehouse")[name] = row


def _item(code, **values):
	row = {
		"name": code,
		"item_code": code,
		"item_name": f"PART, {code}",
		"stock_uom": "Unit",
		"is_stock_item": 1,
		"disabled": 0,
		"has_variants": 0,
		"has_serial_no": 0,
		"has_batch_no": 0,
		"is_customer_provided_item": 0,
		"end_of_life": None,
	}
	row.update(values)
	_table("Item")[code] = row


def _bin(item_code, warehouse, actual_qty=0.0, ordered_qty=0.0, reserved_stock=0.0):
	_table("Bin")[f"{item_code}@{warehouse}"] = {
		"name": f"{item_code}@{warehouse}",
		"item_code": item_code,
		"warehouse": warehouse,
		"actual_qty": actual_qty,
		"ordered_qty": ordered_qty,
		"reserved_stock": reserved_stock,
	}


def _rule(item_code, warehouse, disable=0, capacity=1_000_000):
	STATE["seq"] += 1
	name = f"PUT-{STATE['seq']:04d}"
	_table("Putaway Rule")[name] = {
		"name": name,
		"item_code": item_code,
		"warehouse": warehouse,
		"company": COMPANY,
		"disable": disable,
		"capacity": capacity,
		"stock_capacity": capacity,
		"conversion_factor": 1,
		"priority": 1,
		"creation": STATE["seq"],
	}
	return name


def _default(item_code, warehouse, company=COMPANY, idx=1):
	name = f"ID-{item_code}-{idx}"
	_table("Item Default")[name] = {
		"name": name,
		"parent": item_code,
		"parenttype": "Item",
		"parentfield": "item_defaults",
		"company": company,
		"default_warehouse": warehouse,
		"default_supplier": "Some Supplier",
		"idx": idx,
	}
	return name


def _reset(perm=None, settings=None):
	STATE.clear()
	STATE.update(
		{
			"db": {},
			"perm": dict(perm or {}),
			"perm_checks": [],
			"roles": ["Stock Manager", "Stock User", "Item Manager"],
			"writes": [],
			"set_values": [],
			"sql": [],
			"get_all": [],
			"singles": {},
			"savepoints": {},
			"cache_cleared": [],
			"errors": [],
			"doctypes": {"Inventory Scanner Settings"},
			"tables": {"Putaway Rule"},
			"seq": 0,
			"rate": 12.5,
			"now": datetime.datetime(2026, 10, 14, 9, 0, 0),
			"settings": {
				"undo_window_minutes": 30,
				"take_expense_account": None,
				"scan_cost_center": None,
				"boneyard_warehouse": BONEYARD,
				**(settings or {}),
			},
		}
	)
	FRAPPE.session.user = USER
	_warehouse(BIN_A, "Bin A1-1-1")
	_warehouse(BIN_B, "Bin A1-1-2")
	_warehouse(STORES, "Stores")
	_warehouse(BONEYARD, "Boneyard")
	_warehouse(
		"Inventory - SF", "Inventory", is_group=1, parent_warehouse="All Warehouses - SF", lft=1, rgt=20
	)
	_warehouse("Goods In Transit - SF", "Goods In Transit", warehouse_type="Transit")
	_warehouse("Bin Z - OC", "Bin Z", company="Other Co")
	_item("PDT-0001")


def _ref(n=1):
	return f"ss-test-{n:04d}"


def _rules_for(item_code, warehouse):
	return [
		r
		for r in _table("Putaway Rule").values()
		if r["item_code"] == item_code and r["warehouse"] == warehouse
	]


def _log(name):
	return _table("Stock Scan Log")[name]


def _vouchers(doctype):
	return list(_table(doctype).values())


def _as_stock_user():
	"""A Stock User who can read Items and post transfers, but has no Item write and no
	Putaway Rule create, write or delete: the people the 2026-10-14 decision let in."""
	STATE["roles"] = ["Stock User"]
	STATE["perm"].update(
		{
			("Item", "write"): False,
			("Putaway Rule", "create"): False,
			("Putaway Rule", "write"): False,
			("Putaway Rule", "delete"): False,
			("Stock Reconciliation", "create"): False,
			("Stock Reconciliation", "submit"): False,
		}
	)


def _without_item_read():
	STATE["perm"][("Item", "read")] = False


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def assertRefused(self, fn, *words, exc=None, **kwargs):
		with self.assertRaises(exc or FRAPPE.ValidationError) as caught:
			fn(**kwargs)
		for word in words:
			self.assertIn(word, str(caught.exception))
		return str(caught.exception)


# --------------------------------------------------------------------------- the helper


class TestEnsureAssignment(_Base):
	def test_creates_a_rule_shaped_like_every_other(self):
		name = bins.ensure_assignment("PDT-0001", BIN_A)
		(rule,) = _rules_for("PDT-0001", BIN_A)
		self.assertEqual(rule["name"], name)
		self.assertEqual(
			(rule["capacity"], rule["stock_capacity"], rule["conversion_factor"], rule["uom"]),
			(bins.CAPACITY, bins.CAPACITY, 1, "Unit"),
		)
		self.assertEqual((rule["priority"], rule["company"], rule["disable"]), (1, COMPANY, 0))
		self.assertEqual(bins.CAPACITY, 1_000_000)

	def test_a_new_rule_on_a_bin_with_stock_passes_erpnexts_capacity_check(self):
		"""ERPNext checks stock_capacity before it computes it: left unset, a bin with stock is
		refused "Insufficient Capacity" (the stub's validate does what v16's does)."""
		_bin("PDT-0001", BIN_A, actual_qty=40)
		bins.ensure_assignment("PDT-0001", BIN_A)
		self.assertEqual(len(_rules_for("PDT-0001", BIN_A)), 1)

	def test_an_existing_rule_is_returned_and_nothing_is_written(self):
		name = _rule("PDT-0001", BIN_A)
		self.assertEqual(bins.ensure_assignment("PDT-0001", BIN_A), name)
		self.assertEqual(STATE["writes"], [])

	def test_outside_the_gate_an_existing_rule_is_still_returned(self):
		name = _rule("PDT-0001", BIN_A)
		STATE["roles"] = ["Employee"]
		_without_item_read()
		self.assertEqual(bins.ensure_assignment("PDT-0001", BIN_A), name)

	def test_a_stock_user_without_putaway_rule_create_can_assign(self):
		_as_stock_user()
		name = bins.ensure_assignment("PDT-0001", BIN_A)
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["name"], name)
		self.assertIn(("insert", "Putaway Rule", True), STATE["writes"], "past ERPNext's own permission")

	def test_a_disabled_rule_is_re_enabled_not_duplicated(self):
		name = _rule("PDT-0001", BIN_A, disable=1)
		self.assertEqual(bins.ensure_assignment("PDT-0001", BIN_A), name)
		(rule,) = _rules_for("PDT-0001", BIN_A)
		self.assertEqual(rule["disable"], 0)

	def test_a_hand_made_rule_too_small_for_the_stock_is_widened_when_re_enabled(self):
		_bin("PDT-0001", BIN_A, actual_qty=40)
		_rule("PDT-0001", BIN_A, disable=1, capacity=10)
		bins.ensure_assignment("PDT-0001", BIN_A)
		(rule,) = _rules_for("PDT-0001", BIN_A)
		self.assertEqual((rule["disable"], rule["stock_capacity"]), (0, bins.CAPACITY))

	def test_twice_is_once(self):
		first = bins.ensure_assignment("PDT-0001", BIN_A)
		self.assertEqual(bins.ensure_assignment("PDT-0001", BIN_A), first)
		self.assertEqual(len(_rules_for("PDT-0001", BIN_A)), 1)

	def test_refusals_are_sentences_before_any_write(self):
		_item("SRV-001", is_stock_item=0)
		_item("OLD-1", disabled=1)
		_item("TPL-1", has_variants=1)
		cases = (
			({"item_code": "NOPE", "warehouse": BIN_A}, "There is no item NOPE."),
			({"item_code": "SRV-001", "warehouse": BIN_A}, "not kept in stock"),
			({"item_code": "OLD-1", "warehouse": BIN_A}, "is disabled"),
			({"item_code": "TPL-1", "warehouse": BIN_A}, "template"),
			({"item_code": "PDT-0001", "warehouse": "Inventory - SF"}, "group of locations"),
			({"item_code": "PDT-0001", "warehouse": "Goods In Transit - SF"}, "transit"),
			({"item_code": "PDT-0001", "warehouse": BONEYARD}, "is the boneyard"),
		)
		for kwargs, words in cases:
			with self.subTest(**kwargs):
				self.assertRefused(bins.ensure_assignment, words, **kwargs)
		self.assertEqual(STATE["writes"], [])

	def test_more_stock_than_any_rule_holds_is_a_sentence_not_insufficient_capacity(self):
		_bin("PDT-0001", BIN_A, actual_qty=bins.CAPACITY + 1)
		message = self.assertRefused(bins.ensure_assignment, "more of", item_code="PDT-0001", warehouse=BIN_A)
		self.assertNotIn("Insufficient", message)

	def test_outside_the_gate_a_change_is_a_sentence(self):
		for setup in (_without_item_read, lambda: STATE.update(roles=["Item Manager", "Employee"])):
			with self.subTest(setup=setup):
				_reset()
				setup()
				self.assertRefused(
					bins.ensure_assignment,
					"stock role",
					exc=FRAPPE.PermissionError,
					item_code="PDT-0001",
					warehouse=BIN_A,
				)
				self.assertEqual(STATE["writes"], [])


class TestReleaseIfEmpty(_Base):
	"""v1.692.1: the Item flow's other half (``api.item_flow.save_stock`` when its bin changes),
	what *Move to* with ``make_home`` does to a source it empties."""

	def test_an_empty_bin_is_taken_off_and_its_default_cleared(self):
		name = _rule("PDT-0001", BIN_A)
		here = _default("PDT-0001", BIN_A)
		elsewhere = _default("PDT-0001", STORES, idx=2)
		self.assertEqual(bins.release_if_empty("PDT-0001", BIN_A), 0.0)
		self.assertEqual(_table("Putaway Rule")[name]["disable"], 1)
		self.assertIsNone(_table("Item Default")[here]["default_warehouse"])
		self.assertEqual(_table("Item Default")[elsewhere]["default_warehouse"], STORES)

	def test_stock_on_hand_keeps_the_bin_and_answers_how_much(self):
		name = _rule("PDT-0001", BIN_A)
		_bin("PDT-0001", BIN_A, actual_qty=3)
		self.assertEqual(bins.release_if_empty("PDT-0001", BIN_A), 3)
		self.assertEqual(_table("Putaway Rule")[name]["disable"], 0)
		self.assertEqual(STATE["writes"], [])

	def test_a_pair_nothing_claims_is_left_alone(self):
		# No rule and no Bin row: no record of a decision is made where nobody listed it.
		self.assertIsNone(bins.release_if_empty("PDT-0001", BIN_A))
		_rule("PDT-0001", BIN_B, disable=1)
		self.assertIsNone(bins.release_if_empty("PDT-0001", BIN_B), "already taken off")
		self.assertEqual(STATE["writes"], [])
		self.assertIsNone(bins.release_if_empty("PDT-0001", "Nowhere - SF"))

	def test_an_emptied_undecided_pair_is_recorded_as_taken_off(self):
		_bin("PDT-0001", BIN_A, actual_qty=0)
		self.assertEqual(bins.release_if_empty("PDT-0001", BIN_A), 0.0)
		(rule,) = _rules_for("PDT-0001", BIN_A)
		self.assertEqual(rule["disable"], 1)

	def test_outside_the_gate_only_a_change_is_refused(self):
		_rule("PDT-0001", BIN_A)
		_bin("PDT-0001", BIN_A, actual_qty=2)
		_without_item_read()
		self.assertEqual(bins.release_if_empty("PDT-0001", BIN_A), 2, "nothing to change: no gate")
		_table("Bin")["PDT-0001@" + BIN_A]["actual_qty"] = 0
		self.assertRefused(
			bins.release_if_empty,
			"stock role",
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			warehouse=BIN_A,
		)
		self.assertEqual(STATE["writes"], [])


# --------------------------------------------------------------------------- assign / unassign


class TestAssign(_Base):
	def test_puts_the_item_on_the_bin_and_logs_it(self):
		out = bins.assign("PDT-0001", BIN_A, _ref())
		(rule,) = _rules_for("PDT-0001", BIN_A)
		log = _log(out["log"])
		self.assertEqual(
			(log["action"], log["warehouse"], log["putaway_rule"]), ("Assign", BIN_A, rule["name"])
		)
		self.assertEqual(
			json.loads(log["rule_changes"]),
			[{"kind": "rule", "rule": rule["name"], "created": 1, "disable": None, "warehouse": BIN_A}],
		)

	def test_a_resend_returns_the_first_save(self):
		first = bins.assign("PDT-0001", BIN_A, _ref())
		again = bins.assign("PDT-0001", BIN_A, _ref())
		self.assertEqual(again, {"log": first["log"], "repeated": True, "undone": False})
		self.assertEqual(len(_rules_for("PDT-0001", BIN_A)), 1)

	def test_already_on_the_bin_is_a_sentence(self):
		_rule("PDT-0001", BIN_A)
		self.assertRefused(
			bins.assign, "already on", item_code="PDT-0001", warehouse=BIN_A, client_ref=_ref()
		)
		self.assertEqual(_table("Stock Scan Log"), {})

	def test_without_item_read_nothing_is_written(self):
		_without_item_read()
		self.assertRefused(
			bins.assign,
			"stock role",
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			warehouse=BIN_A,
			client_ref=_ref(),
		)
		self.assertEqual((STATE["writes"], _table("Putaway Rule")), ([], {}))

	def test_outside_the_scan_roles_it_is_refused(self):
		STATE["roles"] = ["Employee", "Item Manager"]
		self.assertRefused(
			bins.assign, exc=FRAPPE.PermissionError, item_code="PDT-0001", warehouse=BIN_A, client_ref=_ref()
		)
		self.assertEqual(_table("Putaway Rule"), {})

	def test_a_stock_user_assigns_and_undoes(self):
		"""Item read, no Item write, no Putaway Rule create: the gate, not ERPNext's DocPerm."""
		_as_stock_user()
		out = bins.assign("PDT-0001", BIN_A, _ref())
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["disable"], 0)
		scan.undo(out["log"])
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["disable"], 1)

	def test_an_undecided_pair_is_assigned_with_an_enabled_rule(self):
		_bin("PDT-0001", BIN_A, actual_qty=0)
		bins.assign("PDT-0001", BIN_A, _ref())
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["disable"], 0)

	def test_undo_disables_the_rule_it_created(self):
		out = bins.assign("PDT-0001", BIN_A, _ref())
		scan.undo(out["log"])
		(rule,) = _rules_for("PDT-0001", BIN_A)
		self.assertEqual(rule["disable"], 1)
		self.assertEqual(_log(out["log"])["status"], "Undone")

	def test_undo_of_a_re_enable_disables_it_again(self):
		_rule("PDT-0001", BIN_A, disable=1)
		out = bins.assign("PDT-0001", BIN_A, _ref())
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["disable"], 0)
		scan.undo(out["log"])
		self.assertEqual(_rules_for("PDT-0001", BIN_A)[0]["disable"], 1)


class TestUnassign(_Base):
	def setUp(self):
		super().setUp()
		self.rule = _rule("PDT-0001", BIN_A)
		self.here = _default("PDT-0001", BIN_A, idx=1)
		self.other_company = _default("PDT-0001", BIN_A, company="Other Co", idx=2)
		self.elsewhere = _default("PDT-0001", STORES, idx=3)

	def test_disables_the_rule_and_clears_only_the_matching_default(self):
		out = bins.unassign("PDT-0001", BIN_A, _ref())
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 1)
		defaults = _table("Item Default")
		self.assertIsNone(defaults[self.here]["default_warehouse"])
		self.assertEqual(defaults[self.here]["default_supplier"], "Some Supplier", "only the warehouse")
		self.assertEqual(defaults[self.other_company]["default_warehouse"], BIN_A, "another company's")
		self.assertEqual(defaults[self.elsewhere]["default_warehouse"], STORES)
		self.assertEqual(_log(out["log"])["action"], "Unassign")
		self.assertIn(("Item", "PDT-0001"), STATE["cache_cleared"])

	def test_the_item_default_is_one_row_write_behind_the_gate(self):
		bins.unassign("PDT-0001", BIN_A, _ref())
		self.assertIn(("Item", "read", None), STATE["perm_checks"])
		self.assertFalse([w for w in STATE["writes"] if w[1] == "Item"], "never Item.save()")
		touched = [v for v in STATE["set_values"] if v[0] == "Item"]
		self.assertEqual(len(touched), 1)
		self.assertEqual(set(touched[0][2]), {"modified", "modified_by"}, "nothing else on the Item")
		rows = [v for v in STATE["set_values"] if v[0] == "Item Default"]
		self.assertEqual(rows, [("Item Default", self.here, {"default_warehouse": None})])

	def test_refused_with_stock_on_hand(self):
		_bin("PDT-0001", BIN_A, actual_qty=3)
		self.assertRefused(
			bins.unassign, "3 Unit", "Move them", item_code="PDT-0001", warehouse=BIN_A, client_ref=_ref()
		)
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 0)
		self.assertEqual(_table("Stock Scan Log"), {})

	def test_without_item_read_nothing_changes(self):
		_without_item_read()
		self.assertRefused(
			bins.unassign,
			"stock role",
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			warehouse=BIN_A,
			client_ref=_ref(),
		)
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.here]["default_warehouse"], BIN_A)

	def test_a_stock_user_without_item_write_unassigns_clears_the_default_and_undoes(self):
		_as_stock_user()
		out = bins.unassign("PDT-0001", BIN_A, _ref())
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 1)
		self.assertIsNone(_table("Item Default")[self.here]["default_warehouse"])
		self.assertIn(("save", "Putaway Rule", True), STATE["writes"])
		scan.undo(out["log"])
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.here]["default_warehouse"], BIN_A)

	def test_an_undecided_pair_is_taken_off_with_a_disabled_rule_and_undo_removes_it(self):
		_bin("PDT-0002", BIN_B, actual_qty=0)
		_item("PDT-0002")
		out = bins.unassign("PDT-0002", BIN_B, _ref())
		(rule,) = _rules_for("PDT-0002", BIN_B)
		self.assertEqual((rule["disable"], rule["stock_capacity"]), (1, bins.CAPACITY))
		(change,) = json.loads(_log(out["log"])["rule_changes"])
		self.assertEqual((change["created"], change["created_disabled"]), (1, 1))
		scan.undo(out["log"])
		self.assertEqual(_rules_for("PDT-0002", BIN_B), [], "undecided again, so it lists again")

	def test_not_on_the_bin_is_a_sentence(self):
		self.assertRefused(
			bins.unassign, "is not on", item_code="PDT-0001", warehouse=BIN_B, client_ref=_ref()
		)

	def test_undo_puts_the_rule_and_the_default_back(self):
		out = bins.unassign("PDT-0001", BIN_A, _ref())
		scan.undo(out["log"])
		self.assertEqual(_table("Putaway Rule")[self.rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.here]["default_warehouse"], BIN_A)

	def test_undo_leaves_a_default_somebody_set_since(self):
		out = bins.unassign("PDT-0001", BIN_A, _ref())
		_table("Item Default")[self.here]["default_warehouse"] = BIN_B
		scan.undo(out["log"])
		self.assertEqual(_table("Item Default")[self.here]["default_warehouse"], BIN_B)

	def test_undo_needs_the_same_gate(self):
		out = bins.unassign("PDT-0001", BIN_A, _ref())
		_without_item_read()
		self.assertRefused(scan.undo, "stock role", exc=FRAPPE.PermissionError, log=out["log"])
		self.assertEqual(_log(out["log"])["status"], "Posted")


# --------------------------------------------------------------------------- moves


class TestMoveTo(_Base):
	def setUp(self):
		super().setUp()
		_bin("PDT-0001", BIN_A, actual_qty=5)
		self.source_rule = _rule("PDT-0001", BIN_A)
		self.default = _default("PDT-0001", BIN_A)

	def test_a_plain_move_is_a_transfer_and_changes_no_assignment(self):
		out = bins.move_to("PDT-0001", BIN_A, BIN_B, 2, _ref())
		(se,) = _vouchers("Stock Entry")
		self.assertEqual((se["stock_entry_type"], se["docstatus"]), ("Material Transfer", 1))
		(row,) = se["items"]
		self.assertEqual((row["s_warehouse"], row["t_warehouse"], row["qty"]), (BIN_A, BIN_B, 2.0))
		self.assertTrue(row["expense_account"])
		self.assertEqual(_rules_for("PDT-0001", BIN_B), [])
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		log = _log(out["log"])
		self.assertEqual((log["action"], log["warehouse"], log["from_warehouse"]), ("Move To", BIN_B, BIN_A))
		self.assertIsNone(log.get("rule_changes"))
		self.assertIsNone(scan._source_kept(FRAPPE.get_doc("Stock Scan Log", out["log"])))

	def test_make_home_with_a_full_move_takes_the_source_off(self):
		out = bins.move_to("PDT-0001", BIN_A, BIN_B, 5, _ref(), make_home=1)
		(dest,) = _rules_for("PDT-0001", BIN_B)
		self.assertEqual(dest["disable"], 0)
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 1)
		self.assertIsNone(_table("Item Default")[self.default]["default_warehouse"])
		doc = FRAPPE.get_doc("Stock Scan Log", out["log"])
		self.assertFalse(scan._source_kept(doc))
		self.assertNotIn("still at", scan._message(doc))

	def test_make_home_with_a_partial_move_keeps_the_source_and_says_so(self):
		out = bins.move_to("PDT-0001", BIN_A, BIN_B, 2, _ref(), make_home=1)
		self.assertEqual(_rules_for("PDT-0001", BIN_B)[0]["disable"], 0, "the destination is still ensured")
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.default]["default_warehouse"], BIN_A)
		doc = FRAPPE.get_doc("Stock Scan Log", out["log"])
		self.assertTrue(scan._source_kept(doc))
		self.assertTrue(scan._log_row(doc)["source_kept"])
		self.assertIn("3 Unit still at Bin A1-1-1, so it stays there.", scan._message(doc))
		kinds = [c["kind"] for c in json.loads(doc.rule_changes)]
		self.assertEqual(kinds, ["rule", "source"], "only what changed, plus the marker")
		scan.undo(out["log"])
		self.assertEqual(_rules_for("PDT-0001", BIN_B)[0]["disable"], 1)
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)

	def test_undo_of_make_home_cancels_the_transfer_and_restores_the_rules(self):
		out = bins.move_to("PDT-0001", BIN_A, BIN_B, 5, _ref(), make_home=1)
		scan.undo(out["log"])
		(se,) = _vouchers("Stock Entry")
		self.assertEqual(se["docstatus"], 2)
		self.assertEqual(_rules_for("PDT-0001", BIN_B)[0]["disable"], 1)
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.default]["default_warehouse"], BIN_A)

	def test_a_full_move_out_of_an_undecided_source_leaves_a_disabled_rule(self):
		_item("PDT-0002")
		_bin("PDT-0002", BIN_B, actual_qty=2)
		out = bins.move_to("PDT-0002", BIN_B, BIN_A, 2, _ref(2), make_home=1)
		(source,) = _rules_for("PDT-0002", BIN_B)
		self.assertEqual(source["disable"], 1)
		scan.undo(out["log"])
		self.assertEqual(_rules_for("PDT-0002", BIN_B), [])

	def test_the_boneyard_is_never_a_home(self):
		self.assertRefused(
			bins.move_to,
			"is the boneyard",
			item_code="PDT-0001",
			from_warehouse=BIN_A,
			to_warehouse=BONEYARD,
			qty=5,
			client_ref=_ref(),
			make_home=1,
		)
		self.assertEqual((_table("Stock Scan Log"), _table("Stock Entry")), ({}, {}))
		self.assertEqual(_rules_for("PDT-0001", BONEYARD), [])
		bins.move_to("PDT-0001", BIN_A, BONEYARD, 1, _ref(2))
		self.assertEqual(_rules_for("PDT-0001", BONEYARD), [], "a plain move there is fine")

	def test_more_than_is_there_is_refused_before_anything(self):
		self.assertRefused(
			bins.move_to,
			"Only 5",
			item_code="PDT-0001",
			from_warehouse=BIN_A,
			to_warehouse=BIN_B,
			qty=6,
			client_ref=_ref(),
		)
		self.assertEqual((_table("Stock Scan Log"), _table("Stock Entry")), ({}, {}))

	def test_make_home_outside_the_gate_posts_nothing(self):
		_without_item_read()
		self.assertRefused(
			bins.move_to,
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			from_warehouse=BIN_A,
			to_warehouse=BIN_B,
			qty=1,
			client_ref=_ref(),
			make_home=1,
		)
		self.assertEqual(_table("Stock Entry"), {})

	def test_the_transfer_still_posts_as_the_user(self):
		_as_stock_user()
		bins.move_to("PDT-0001", BIN_A, BIN_B, 5, _ref(), make_home=1)
		posted = [w for w in STATE["writes"] if w[1] == "Stock Entry"]
		self.assertEqual(posted, [("insert", "Stock Entry", False)])
		_bin("PDT-0001", BIN_B, actual_qty=5)
		STATE["perm"][("Stock Entry", "create")] = False
		self.assertRefused(
			bins.move_to,
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			from_warehouse=BIN_B,
			to_warehouse=BIN_A,
			qty=1,
			client_ref=_ref(2),
		)


class TestSendToBoneyard(_Base):
	def setUp(self):
		super().setUp()
		_bin("PDT-0001", BIN_A, actual_qty=4)
		self.source_rule = _rule("PDT-0001", BIN_A)
		self.default = _default("PDT-0001", BIN_A)

	def test_a_full_trip_transfers_and_takes_the_item_off_the_bin(self):
		out = bins.send_to_boneyard("PDT-0001", BIN_A, 4, _ref())
		(se,) = _vouchers("Stock Entry")
		(row,) = se["items"]
		self.assertEqual(
			(se["stock_entry_type"], row["s_warehouse"], row["t_warehouse"]),
			("Material Transfer", BIN_A, BONEYARD),
		)
		self.assertEqual(_rules_for("PDT-0001", BONEYARD), [], "never a rule at the boneyard")
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 1)
		self.assertIsNone(_table("Item Default")[self.default]["default_warehouse"])
		log = _log(out["log"])
		self.assertEqual(
			(log["action"], log["warehouse"], log["from_warehouse"]), ("Boneyard", BONEYARD, BIN_A)
		)
		self.assertIn("It is off", scan._message(FRAPPE.get_doc("Stock Scan Log", out["log"])))

	def test_a_partial_trip_keeps_the_source_and_says_so(self):
		out = bins.send_to_boneyard("PDT-0001", BIN_A, 1, _ref())
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.default]["default_warehouse"], BIN_A)
		doc = FRAPPE.get_doc("Stock Scan Log", out["log"])
		self.assertTrue(scan._source_kept(doc))
		self.assertIn("3 Unit still at Bin A1-1-1", scan._message(doc))
		self.assertEqual([c["kind"] for c in json.loads(doc.rule_changes)], ["source"])
		scan.undo(out["log"])
		self.assertEqual(_vouchers("Stock Entry")[0]["docstatus"], 2)
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)

	def test_undo_reverses_the_transfer_and_the_rule_changes(self):
		out = bins.send_to_boneyard("PDT-0001", BIN_A, 4, _ref())
		scan.undo(out["log"])
		self.assertEqual(_vouchers("Stock Entry")[0]["docstatus"], 2)
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.default]["default_warehouse"], BIN_A)
		self.assertEqual(_rules_for("PDT-0001", BONEYARD), [])

	def test_a_stock_user_can_send_and_undo(self):
		_as_stock_user()
		out = bins.send_to_boneyard("PDT-0001", BIN_A, 4, _ref())
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 1)
		scan.undo(out["log"])
		self.assertEqual(_table("Putaway Rule")[self.source_rule]["disable"], 0)
		self.assertEqual(_table("Item Default")[self.default]["default_warehouse"], BIN_A)

	def _refused(self, *words):
		message = self.assertRefused(
			bins.send_to_boneyard,
			*words,
			item_code="PDT-0001",
			from_warehouse=BIN_A,
			qty=1,
			client_ref=_ref(),
		)
		self.assertEqual((_table("Stock Scan Log"), _table("Stock Entry")), ({}, {}))
		return message

	def test_an_empty_setting_says_who_sets_it(self):
		STATE["settings"]["boneyard_warehouse"] = None
		self._refused("Stock Manager", "Inventory Scanner Settings")

	def test_a_disabled_boneyard_says_who_sets_it(self):
		_table("Warehouse")[BONEYARD]["disabled"] = 1
		self._refused("Inventory Scanner Settings")

	def test_a_group_boneyard_is_refused(self):
		_table("Warehouse")[BONEYARD]["is_group"] = 1
		self._refused("group")

	def test_another_companys_boneyard_is_refused(self):
		_table("Warehouse")[BONEYARD]["company"] = "Other Co"
		self._refused("belongs to Other Co")

	def test_it_reads_the_setting_not_a_constant(self):
		_warehouse("Scrap Shelf - SF", "Scrap Shelf")
		STATE["settings"]["boneyard_warehouse"] = "Scrap Shelf - SF"
		bins.send_to_boneyard("PDT-0001", BIN_A, 1, _ref())
		self.assertEqual(_vouchers("Stock Entry")[0]["items"][0]["t_warehouse"], "Scrap Shelf - SF")
		self.assertNotIn("Boneyard - SF", MODULE.read_text(encoding="utf-8"))

	def test_needs_the_gate(self):
		_without_item_read()
		self.assertRefused(
			bins.send_to_boneyard,
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			from_warehouse=BIN_A,
			qty=1,
			client_ref=_ref(),
		)
		self.assertEqual(_table("Stock Entry"), {})


# --------------------------------------------------------------------------- counts


class TestSetCount(_Base):
	def test_posts_a_stock_reconciliation_not_a_material_issue(self):
		_bin("PDT-0001", BIN_A, actual_qty=7)
		out = bins.set_count("PDT-0001", BIN_A, 5, _ref())
		self.assertEqual(_table("Stock Entry"), {}, "a count is never a Material Issue")
		(sr,) = _vouchers("Stock Reconciliation")
		self.assertEqual(
			(sr["purpose"], sr["company"], sr["docstatus"]), ("Stock Reconciliation", COMPANY, 1)
		)
		self.assertTrue(sr["expense_account"])
		(row,) = sr["items"]
		self.assertEqual((row["item_code"], row["warehouse"], row["qty"]), ("PDT-0001", BIN_A, 5.0))
		self.assertNotIn("valuation_rate", row, "blank keeps the current rate")
		log = _log(out["log"])
		self.assertEqual(
			(log["action"], log["qty"], log["qty_before"], log["voucher_type"]),
			("Count", 5.0, 7.0, "Stock Reconciliation"),
		)

	def test_from_zero_it_carries_the_receipt_rate(self):
		bins.set_count("PDT-0001", BIN_A, 3, _ref())
		(sr,) = _vouchers("Stock Reconciliation")
		self.assertEqual(sr["items"][0]["valuation_rate"], 12.5)

	def test_from_zero_with_no_cost_asks_for_one_and_posts_nothing(self):
		"""TASK-2026-02914: not a refusal sentence any more but a payload the page answers with a unit
		cost (tests/test_unit_cost_prompt.py has the whole behaviour)."""
		STATE["rate"] = 0
		out = bins.set_count("PDT-0001", BIN_A, 3, _ref())
		self.assertTrue(out["needs_valuation_rate"])
		self.assertEqual(_vouchers("Stock Reconciliation"), [])

	def test_zero_is_a_count(self):
		_bin("PDT-0001", BIN_A, actual_qty=2)
		bins.set_count("PDT-0001", BIN_A, 0, _ref())
		self.assertEqual(_vouchers("Stock Reconciliation")[0]["items"][0]["qty"], 0.0)

	def test_the_same_number_and_any_reservation_are_sentences(self):
		"""v16's validate_reserved_stock refuses every reconciliation (and its cancel) of a pair
		with reserved stock, above the reserved quantity too."""
		_bin("PDT-0001", BIN_A, actual_qty=4, reserved_stock=3)
		self.assertRefused(
			bins.set_count, "already shows 4", item_code="PDT-0001", warehouse=BIN_A, qty=4, client_ref=_ref()
		)
		for n, qty in enumerate((2, 6), start=2):
			with self.subTest(qty=qty):
				self.assertRefused(
					bins.set_count,
					"reserved",
					"does not allow a count",
					item_code="PDT-0001",
					warehouse=BIN_A,
					qty=qty,
					client_ref=_ref(n),
				)
		self.assertEqual(_table("Stock Reconciliation"), {})

	def test_the_count_posts_as_the_user(self):
		_as_stock_user()
		_bin("PDT-0001", BIN_A, actual_qty=7)
		self.assertRefused(
			bins.set_count,
			"Stock Reconciliation",
			exc=FRAPPE.PermissionError,
			item_code="PDT-0001",
			warehouse=BIN_A,
			qty=5,
			client_ref=_ref(),
		)
		self.assertEqual(_table("Stock Scan Log"), {})

	def test_undo_cancels_the_reconciliation(self):
		_bin("PDT-0001", BIN_A, actual_qty=7)
		out = bins.set_count("PDT-0001", BIN_A, 5, _ref())
		scan.undo(out["log"])
		self.assertEqual(_vouchers("Stock Reconciliation")[0]["docstatus"], 2)

	def test_the_count_rules(self):
		self.assertEqual(rules.check_count("0"), (0.0, None))
		self.assertEqual(rules.check_count("-1")[1], "A count cannot be less than zero.")
		self.assertIn("whole numbers", rules.check_count("2.5", whole_number=True, uom="Unit")[1])
		self.assertIsNone(rules.check_recount(3, 4))
		self.assertIsNone(rules.unassign_refusal(0))
		self.assertIn("Move them", rules.unassign_refusal(2, "Unit", "PUMP", "Bin A"))


# --------------------------------------------------------------------------- the listing


class TestTheListing(_Base):
	def _query(self):
		scan._items_at(_dict(name=BIN_A, company=COMPANY))
		(query,) = [q for q in STATE["sql"] if "`tabPutaway Rule` pr" in q]
		return query

	def test_one_query_with_the_five_conditions(self):
		query = self._query()
		for needle in (
			"b.actual_qty > 0",
			"b.ordered_qty > 0",
			"pr.warehouse = %(warehouse)s and pr.disable = 0",
			"idf.default_warehouse = %(warehouse)s and idf.company = %(company)s",
			"(b.name is not null and ar.item_code is null)",
			"i.disabled = 0 and i.is_stock_item = 1",
		):
			with self.subTest(condition=needle):
				self.assertIn(needle, query)

	def test_the_conditions_run_for_real(self):
		"""The query itself, executed on SQLite in memory (it ports as written: backticks, derived
		tables, ``x is not null as y``); only the ``%(name)s`` placeholders become ``:name``."""
		import sqlite3

		db = sqlite3.connect(":memory:")
		db.executescript(
			"""
			create table `tabItem` (name text, item_name text, image text, stock_uom text,
				disabled int, is_stock_item int);
			create table `tabBin` (name text, item_code text, warehouse text, actual_qty real,
				ordered_qty real);
			create table `tabPutaway Rule` (name text, item_code text, warehouse text, disable int);
			create table `tabItem Default` (name text, parent text, parenttype text,
				default_warehouse text, company text);
			"""
		)
		items = {
			"ONHAND": (1, 0),
			"ORDER": (1, 0),
			"ASSIGNED": (1, 0),
			"DEFAULT": (1, 0),
			"DEFAULT_OTHER_CO": (1, 0),
			"UNDECIDED": (1, 0),
			"TAKEN_OFF": (1, 0),
			"RULE_ELSEWHERE": (1, 0),
			"NOT_HERE": (1, 0),
			"NON_STOCK": (0, 0),
			"DISABLED": (1, 1),
		}
		db.executemany(
			"insert into `tabItem` values (?, ?, null, 'Unit', ?, ?)",
			[(code, code, disabled, stock) for code, (stock, disabled) in items.items()],
		)
		db.executemany(
			"insert into `tabBin` values (?, ?, ?, ?, ?)",
			[
				("b1", "ONHAND", BIN_A, 3, 0),
				("b2", "ORDER", BIN_A, 0, 2),
				("b3", "UNDECIDED", BIN_A, 0, 0),
				("b4", "TAKEN_OFF", BIN_A, 0, 0),
				("b5", "RULE_ELSEWHERE", BIN_A, 0, 0),
				("b6", "NOT_HERE", BIN_B, 5, 0),
				("b7", "NON_STOCK", BIN_A, 3, 0),
			],
		)
		db.executemany(
			"insert into `tabPutaway Rule` values (?, ?, ?, ?)",
			[
				("r1", "ONHAND", BIN_A, 1),
				("r2", "ORDER", BIN_A, 1),
				("r3", "ASSIGNED", BIN_A, 0),
				("r3b", "ASSIGNED", BIN_A, 0),
				("r4", "TAKEN_OFF", BIN_A, 1),
				("r5", "RULE_ELSEWHERE", BIN_B, 0),
				("r6", "DISABLED", BIN_A, 0),
			],
		)
		db.executemany(
			"insert into `tabItem Default` values (?, ?, 'Item', ?, ?)",
			[("d1", "DEFAULT", BIN_A, COMPANY), ("d2", "DEFAULT_OTHER_CO", BIN_A, "Other Co")],
		)
		query = re.sub(r"%\((\w+)\)s", r":\1", self._query())
		rows = db.execute(query, {"warehouse": BIN_A, "company": COMPANY}).fetchall()
		listed = sorted(row[0] for row in rows)
		self.assertEqual(listed, ["ASSIGNED", "DEFAULT", "ONHAND", "ORDER", "RULE_ELSEWHERE", "UNDECIDED"])
		undecided = {row[0]: row[-1] for row in rows}
		self.assertEqual((undecided["UNDECIDED"], undecided["ASSIGNED"], undecided["ONHAND"]), (1, 0, 0))

	def test_the_location_and_the_boot_say_whether_the_caller_may_manage(self):
		def flag():
			return scan._location_payload(_dict(name=BIN_A, company=COMPANY, lft=None))["can_manage"]

		self.assertTrue(flag())
		_as_stock_user()
		self.assertTrue(flag(), "a Stock User who can read items may")
		_without_item_read()
		self.assertFalse(flag())
		_reset()
		STATE["roles"] = ["Item Manager"]
		self.assertFalse(flag())


# --------------------------------------------------------------------------- the patches


class TestBackfillSelection(unittest.TestCase):
	def setUp(self):
		_reset()

	def _select(self, bin_rows, existing=(), prefix=None, items=None, warehouses=None):
		items = items or {
			"A": {"is_stock_item": 1, "disabled": 0, "has_variants": 0, "stock_uom": "Unit"},
			"OFF": {"is_stock_item": 1, "disabled": 1, "has_variants": 0, "stock_uom": "Unit"},
			"SRV": {"is_stock_item": 0, "disabled": 0, "has_variants": 0, "stock_uom": "Unit"},
		}
		warehouses = warehouses or {
			BIN_A: {"is_group": 0, "disabled": 0, "company": COMPANY, "warehouse_type": ""},
			STORES: {"is_group": 0, "disabled": 0, "company": COMPANY, "warehouse_type": ""},
			"Group - SF": {"is_group": 1, "disabled": 0, "company": COMPANY, "warehouse_type": ""},
			"Transit - SF": {"is_group": 0, "disabled": 0, "company": COMPANY, "warehouse_type": "Transit"},
			"Shut - SF": {"is_group": 0, "disabled": 1, "company": COMPANY, "warehouse_type": ""},
		}
		return backfill.select_pairs(bin_rows, items, warehouses, set(existing), 1_000_000, prefix=prefix)

	def test_every_enabled_stock_item_at_every_enabled_leaf(self):
		rows = [
			{"item_code": "A", "warehouse": STORES, "actual_qty": 0},
			{"item_code": "A", "warehouse": BIN_A, "actual_qty": 4},
			{"item_code": "OFF", "warehouse": BIN_A, "actual_qty": 1},
			{"item_code": "SRV", "warehouse": BIN_A, "actual_qty": 0},
			{"item_code": "A", "warehouse": "Group - SF", "actual_qty": 0},
			{"item_code": "A", "warehouse": "Transit - SF", "actual_qty": 0},
			{"item_code": "A", "warehouse": "Shut - SF", "actual_qty": 0},
		]
		pairs, refused = self._select(rows)
		self.assertEqual([(p["item_code"], p["warehouse"]) for p in pairs], [("A", BIN_A), ("A", STORES)])
		self.assertEqual(pairs[0]["company"], COMPANY)
		self.assertEqual(refused, [])

	def test_a_pair_with_a_rule_is_skipped(self):
		pairs, _r = self._select(
			[{"item_code": "A", "warehouse": BIN_A, "actual_qty": 0}], existing={("A", BIN_A)}
		)
		self.assertEqual(pairs, [])

	def test_the_bins_only_cut(self):
		rows = [
			{"item_code": "A", "warehouse": STORES, "actual_qty": 0},
			{"item_code": "A", "warehouse": BIN_A, "actual_qty": 0},
		]
		pairs, _r = self._select(rows, prefix="Bin ")
		self.assertEqual([p["warehouse"] for p in pairs], [BIN_A])
		self.assertIsNone(
			backfill.ONLY_WAREHOUSES_STARTING_WITH, "the owner chose every pair, rooms included"
		)

	def test_what_erpnext_would_refuse_is_reported_not_raised(self):
		pairs, refused = self._select([{"item_code": "A", "warehouse": BIN_A, "actual_qty": 2_000_000}])
		self.assertEqual(pairs, [])
		self.assertEqual(refused[0][:2], ("A", BIN_A))


class TestBackfillRun(_Base):
	def setUp(self):
		super().setUp()
		_item("PDT-0002")
		_bin("PDT-0001", BIN_A, actual_qty=3)
		_bin("PDT-0002", STORES, actual_qty=0)
		_bin("PDT-0002", BIN_B, actual_qty=0)
		_rule("PDT-0002", BIN_B, disable=1)

	def test_creates_the_missing_rules_once(self):
		backfill.execute()
		self.assertEqual(len(_rules_for("PDT-0001", BIN_A)), 1)
		self.assertEqual(len(_rules_for("PDT-0002", STORES)), 1)
		self.assertEqual(
			_rules_for("PDT-0002", BIN_B)[0]["disable"], 1, "a disabled rule is somebody's decision"
		)
		count = len(_table("Putaway Rule"))
		backfill.execute()
		self.assertEqual(len(_table("Putaway Rule")), count, "safe twice")
		self.assertEqual(STATE["errors"], [])
		rule = _rules_for("PDT-0001", BIN_A)[0]
		self.assertEqual(
			(rule["stock_capacity"], rule["priority"], rule["company"]), (bins.CAPACITY, 1, COMPANY)
		)

	def test_a_failing_pair_is_skipped_and_logged_and_the_rest_go_in(self):
		original = backfill._insert

		def flaky(pair, capacity, priority):
			if pair["warehouse"] == STORES:
				raise RuntimeError("refused")
			return original(pair, capacity, priority)

		backfill._insert = flaky
		try:
			backfill.execute()
		finally:
			backfill._insert = original
		self.assertEqual(len(_rules_for("PDT-0001", BIN_A)), 1)
		self.assertEqual(_rules_for("PDT-0002", STORES), [])
		(title, message) = STATE["errors"][0]
		self.assertIn("skipped", title)
		self.assertIn(STORES, message)

	def test_no_table_no_op(self):
		STATE["tables"] = set()
		backfill.execute()
		self.assertEqual(len(_table("Putaway Rule")), 1)


class TestBoneyardPatch(_Base):
	def setUp(self):
		super().setUp()
		del _table("Warehouse")[BONEYARD]
		STATE["singles"][("Inventory Scanner Settings", "undo_window_minutes")] = 30

	def test_creates_the_warehouse_once_under_the_parent(self):
		boneyard_patch.execute()
		row = _table("Warehouse")[BONEYARD]
		self.assertEqual(
			(row["warehouse_name"], row["parent_warehouse"], row["company"], row["is_group"]),
			("Boneyard", "Inventory - SF", COMPANY, 0),
		)
		self.assertFalse(row.get("warehouse_type"), "not Transit")
		boneyard_patch.execute()
		self.assertEqual(
			[w for w in _table("Warehouse").values() if w["warehouse_name"] == "Boneyard"], [row]
		)

	def test_finds_an_existing_one_by_name_and_company(self):
		_warehouse("Boneyard Shelf X", "Boneyard")
		boneyard_patch.execute()
		self.assertNotIn(BONEYARD, _table("Warehouse"))
		self.assertEqual(
			STATE["singles"][("Inventory Scanner Settings", "boneyard_warehouse")], "Boneyard Shelf X"
		)

	def test_skips_without_the_parent(self):
		del _table("Warehouse")["Inventory - SF"]
		boneyard_patch.execute()
		self.assertNotIn(BONEYARD, _table("Warehouse"))
		self.assertNotIn(("Inventory Scanner Settings", "boneyard_warehouse"), STATE["singles"])
		self.assertEqual(STATE["errors"], [])

	def test_fills_the_setting_only_when_it_has_no_row(self):
		boneyard_patch.execute()
		self.assertEqual(STATE["singles"][("Inventory Scanner Settings", "boneyard_warehouse")], BONEYARD)
		STATE["singles"][("Inventory Scanner Settings", "boneyard_warehouse")] = ""
		boneyard_patch.execute()
		self.assertEqual(
			STATE["singles"][("Inventory Scanner Settings", "boneyard_warehouse")],
			"",
			"an emptied setting stays off",
		)

	def test_a_never_saved_single_is_left_alone(self):
		STATE["singles"].clear()
		boneyard_patch.execute()
		self.assertEqual(STATE["singles"], {})

	def test_it_cannot_raise(self):
		def boom(*a, **k):
			raise RuntimeError("db gone")

		FRAPPE.db.get_value, saved = boom, FRAPPE.db.get_value
		try:
			boneyard_patch.execute()
		finally:
			FRAPPE.db.get_value = saved
		self.assertTrue(STATE["errors"])
		for title, _m in STATE["errors"]:
			self.assertIsInstance(title, str)


class TestTheBoneyardIsAnOrdinaryLocation(_Base):
	def test_it_gets_a_qr_label(self):
		rows = labels.label_rows()
		self.assertIn(BONEYARD, [r["warehouse"] for r in rows])
		self.assertNotIn("Inventory - SF", [r["warehouse"] for r in rows])
		self.assertNotIn("Goods In Transit - SF", [r["warehouse"] for r in rows])

	def test_stock_scan_accepts_it(self):
		self.assertIsNone(scan._location_problem(scan._warehouse(BONEYARD), BONEYARD))


# --------------------------------------------------------------------------- the source rules


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _functions(path):
	return {n.name: n for n in _tree(path).body if isinstance(n, ast.FunctionDef)}


def _calls(fn):
	out = []
	for node in ast.walk(fn):
		if isinstance(node, ast.Call):
			out.append((node.lineno, node.col_offset, ast.unparse(node.func), node))
	return sorted(out, key=lambda c: (c[0], c[1]))


ENDPOINTS = {
	"assign": ["item_code", "warehouse", "client_ref"],
	"unassign": ["item_code", "warehouse", "client_ref"],
	"move_to": ["item_code", "from_warehouse", "to_warehouse", "qty", "client_ref", "make_home"],
	"send_to_boneyard": ["item_code", "from_warehouse", "qty", "client_ref"],
	"set_count": ["item_code", "warehouse", "qty", "client_ref", "rate"],
	# v1.693.0, the Bin Manager page's New item (tests/test_bin_manager.py has its behaviour).
	"create_and_assign": ["warehouse", "client_ref", "item_code", "item_name", "item_group", "stock_uom"],
}

#: Read-only endpoints (v1.693.0): GET, role-checked first, no client_ref.
READS = {"manager_boot": []}

#: The one write allowed before the log row: create_and_assign's quick Item, because the log's
#: item_code is a Link and the Item has to exist first (the store run does the same). A retry
#: still finds the first save through _already_saved, and a refusal rolls the Item back.
WRITES_BEFORE_LOG = {"create_and_assign": {"scan._quick_item"}}

#: What writes, in an endpoint: none of it may come before the log row.
WRITES = {
	"_ensure",
	"_release",
	"_disable_rules",
	"_clear_defaults",
	"scan._post",
	"scan._transfer_entry",
	"_reconciliation",
}


class TestTheSource(unittest.TestCase):
	def test_the_endpoints_and_their_parameters(self):
		found = {
			name: fn
			for name, fn in _functions(MODULE).items()
			if any("whitelist" in ast.unparse(d) for d in fn.decorator_list)
		}
		self.assertEqual(set(found), set(ENDPOINTS) | set(READS))
		for name in READS:
			fn = found.pop(name)
			with self.subTest(read=name):
				self.assertEqual(
					[ast.unparse(d) for d in fn.decorator_list], ["frappe.whitelist(methods=['GET'])"]
				)
				self.assertEqual([a.arg for a in fn.args.args], READS[name])
				body = [
					s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
				]
				self.assertEqual(ast.unparse(body[0]), "scan._check_access()")
		for name, fn in found.items():
			with self.subTest(endpoint=name):
				self.assertEqual(
					[ast.unparse(d) for d in fn.decorator_list], ["frappe.whitelist(methods=['POST'])"]
				)
				self.assertEqual([a.arg for a in fn.args.args], ENDPOINTS[name])
				body = [
					s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
				]
				self.assertEqual(
					[ast.unparse(s) for s in body[:4]],
					[
						"scan._check_access()",
						"ref = scan._client_ref(client_ref)",
						"done = scan._already_saved(ref)",
						"if done:\n    return done",
					],
				)

	def test_the_log_row_comes_before_any_write(self):
		for name in ENDPOINTS:
			fn = _functions(MODULE)[name]
			calls = _calls(fn)
			begin = [pos for pos in ((c[0], c[1]) for c in calls if c[2] == "scan._begin_log")]
			writes = [(c[0], c[1]) for c in calls if c[2] in WRITES]
			early = WRITES_BEFORE_LOG.get(name, set())
			with self.subTest(endpoint=name):
				self.assertEqual(len(begin), 1)
				self.assertTrue(writes)
				self.assertLess(begin[0], min(writes))
				for allowed in early:
					(at,) = [(c[0], c[1]) for c in calls if c[2] == allowed]
					self.assertLess(at, begin[0])
					checks = [
						(c[0], c[1]) for c in calls if c[2] in ("scan._check_quick_item", "require_manage")
					]
					self.assertTrue(checks and max(checks) < at, "every refusal before the Item is made")
				finish = [(c[0], c[1]) for c in calls if c[2] == "scan._finish_log"]
				self.assertTrue(finish and finish[0] > max(writes))

	def test_nothing_is_written_past_the_permission_check(self):
		"""Read from the code with docstrings and comments gone: the prose explains these words."""
		tree = _tree(MODULE)
		for node in ast.walk(tree):
			body = getattr(node, "body", None)
			if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
				if isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
					body[0] = ast.Pass()
		code = ast.unparse(tree)
		# The one exception (the owner's 2026-10-14 decision): the Putaway Rule itself, past the
		# module gate. Every ignore_permissions is on a rule write, in these three functions only.
		uses = {}
		for fn in tree.body:
			if isinstance(fn, ast.FunctionDef):
				for node in ast.walk(fn):
					if isinstance(node, ast.Call) and any(
						k.arg == "ignore_permissions" for k in node.keywords
					):
						first = ast.unparse(node.args[0]) if node.args else ""
						uses.setdefault(fn.name, []).append((ast.unparse(node.func), first))
		self.assertEqual(set(uses), {"_save_rule", "_new_rule", "restore"})
		self.assertEqual(
			sorted(c for calls in uses.values() for c in calls),
			[("frappe.delete_doc", "RULE"), ("rule.insert", ""), ("rule.save", "")],
		)
		gate = ast.unparse(_functions(MODULE)["require_manage"]) + ast.unparse(_functions(API)["_can_manage"])
		self.assertIn("rules.SCAN_ROLES", gate)
		self.assertIn("frappe.has_permission('Item', 'read')", gate)
		for name in ("_new_rule", "_disable_rules", "restore"):
			with self.subTest(gated=name):
				self.assertIn("require_manage()", ast.unparse(_functions(MODULE)[name]))
		self.assertNotRegex(code, r"flags\.ignore_")
		self.assertNotIn("scan._post(se, ", code)
		self.assertNotIn("set_user", code)
		self.assertNotRegex(code, r"\.commit\(")
		self.assertNotRegex(code, r"get_doc\(\s*['\"]Item['\"]", "an Item is never loaded to be saved whole")
		self.assertIn("frappe.get_doc(RULE", code, "anti-vacuity for the regex above")

	def test_no_sql_function_strings_in_get_all_and_no_singles_get_value(self):
		"""Frappe 16 refuses ``count(name) as n`` in ``get_all`` fields (CLAUDE.md), and
		``db.get_value("Singles", ...)`` cannot succeed."""
		for path in (MODULE, API, BACKFILL, BONEYARD_PATCH):
			for node in ast.walk(_tree(path)):
				if not isinstance(node, ast.Call):
					continue
				name = ast.unparse(node.func)
				if name.endswith(("get_all", "get_list")):
					for kw in node.keywords:
						if kw.arg == "fields" and isinstance(kw.value, ast.List):
							for element in kw.value.elts:
								with self.subTest(path=path.name, line=node.lineno):
									self.assertFalse(
										isinstance(element, ast.Constant) and "(" in str(element.value),
										f"{element.value!r} is a SQL function string",
									)
				if name.endswith("get_value") and node.args:
					first = node.args[0]
					self.assertFalse(isinstance(first, ast.Constant) and first.value == "Singles")

	def test_the_log_fields_and_actions(self):
		meta = json.loads(LOG_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(tuple(fields["action"]["options"].split("\n")), rules.ACTIONS)
		for name in ("putaway_rule", "qty_before", "rule_changes"):
			with self.subTest(field=name):
				self.assertEqual(fields[name].get("read_only"), 1)
		self.assertEqual(fields["putaway_rule"]["options"], "Putaway Rule")
		self.assertGreaterEqual(meta["modified"], "2026-10-14")
		written = set()
		for fn in _functions(MODULE).values():
			for _l, _c, name, call in _calls(fn):
				if name in ("scan._begin_log", "scan._finish_log"):
					written |= {k.arg for k in call.keywords if k.arg}
		self.assertTrue({"putaway_rule", "rule_changes", "qty_before", "from_warehouse"} <= written)
		self.assertEqual(sorted(written - set(fields)), [])

	def test_the_boneyard_setting(self):
		meta = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		self.assertEqual(
			(fields["boneyard_warehouse"]["fieldtype"], fields["boneyard_warehouse"]["options"]),
			("Link", "Warehouse"),
		)
		self.assertTrue(fields["boneyard_warehouse"].get("description"))
		self.assertGreaterEqual(meta["modified"], "2026-10-14")
		self.assertIn("get_settings()", ast.unparse(_functions(MODULE)["_boneyard_for"]))

	def test_both_patches_are_registered_after_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
		post = lines.index("[post_model_sync]")
		for patch in ("backfill_bin_putaway_rules", "add_boneyard_warehouse"):
			with self.subTest(patch=patch):
				self.assertGreater(lines.index(f"erpnext_enhancements.patches.{patch}"), post)


if __name__ == "__main__":
	unittest.main()
