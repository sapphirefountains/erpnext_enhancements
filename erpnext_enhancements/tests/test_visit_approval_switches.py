# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The two switches on approving a maintenance visit. Bench-free.

Approve & Submit runs ``api/maintenance_workflow.process_maintenance_submission``. On
2026-10-12, before the first visit was ever approved on prod, Nik decided:

* **"Keep billing outside of ERPNext until we switch over from QBO."** ERPNext Enhancements
  Settings → *Draft Invoices from Approved Visits* ships off, and while it is off
  ``create_sales_invoice`` does nothing at all.
* **"Don't post to any account yet, but make it configurable so I can add it when I have the
  information."** *Chemicals Used Account* ships empty. While it is empty ``create_stock_entry``
  issues nothing, so nothing posts (perpetual inventory makes the issue a posting), and it says
  so on the visit. Before this it posted to whatever the resolver found, which on prod is the
  lone 5119 Stock Adjustment account. A set but unusable account fails the step rather than
  falling back to that.

Both fields default to off/empty, and the falsy reading of a new field on a Single (no
``tabSingles`` row on an existing site) is that same off/empty. So prod is safe on deploy
with no backfill.

Run: python -m unittest erpnext_enhancements.tests.test_visit_approval_switches
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
WORKFLOW = "erpnext_enhancements.api.maintenance_workflow"
ACCOUNTS = "erpnext_enhancements.inventory_enhancements.stock_accounts"
STUBBED = ("frappe", "frappe.utils", "erpnext_enhancements.email_style", ACCOUNTS, WORKFLOW)

workflow = None
STATE = {}
_saved = {}


class _Throw(Exception):
	pass


class _Row(dict):
	"""Attribute access over a dict, both ways: the code under test sets fields as attributes."""

	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Doc(_Row):
	def add_comment(self, kind, text):
		STATE["comments"].append(text)

	def db_set(self, field, value):
		self[field] = value


class _StockEntry(_Row):
	def append(self, table, row):
		self.setdefault(table, []).append(row)

	def insert(self):
		STATE["inserted"].append(self)

	def submit(self):
		STATE["submitted"].append(self)


def _reset():
	STATE.clear()
	STATE.update(
		{
			"settings": {},
			"comments": [],
			"inserted": [],
			"submitted": [],
			"db_reads": [],
			"usable": {"5400 - Chemicals Used - SF"},
			"contract": {"invoicing_frequency": "Monthly", "sales_order": None},
		}
	)


def _visit(qty=1.0):
	return _Doc(
		name="SMR-1",
		project="PRJ-1",
		technician="austin@example.com",
		serial_no="SN-1",
		maintenance_contract=None,
		consumables=[
			_Row(item="CON-SRV-ACID-GAL", qty=qty, warehouse="Inventory Room - SF", serial_no="SN-1")
		],
	)


def setUpModule():
	global workflow
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text

	def _throw(message, *a, **k):
		raise _Throw(message)

	frappe.throw = _throw
	frappe.get_link_to_form = lambda doctype, name: f"{doctype} {name}"
	frappe.new_doc = lambda doctype: _StockEntry(doctype=doctype)
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire Fountains")
	frappe.get_all = lambda *a, **k: []
	frappe.get_single = lambda doctype: _Row(maintenance_fee_item="SRV-202", maintenance_services_group=None)

	def _get_single_value(doctype, field):
		STATE["db_reads"].append(("single", field))
		return STATE["settings"].get(field)

	def _get_value(doctype, name, fields=None, as_dict=False, **kwargs):
		STATE["db_reads"].append((doctype, name))
		if doctype == "Project":
			return "Sapphire Fountains"
		if doctype == "Sapphire Maintenance Contract":
			return _Row(STATE["contract"]) if as_dict else None
		return None

	frappe.db = types.SimpleNamespace(
		get_single_value=_get_single_value,
		get_value=_get_value,
		exists=lambda *a, **k: False,
	)

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda value, *a: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.nowdate = lambda: "2026-10-12"
	utils.get_datetime = lambda value: value
	frappe.utils = utils

	accounts = types.ModuleType(ACCOUNTS)
	accounts.usable_account = lambda account, company: account if account in STATE["usable"] else None
	accounts.difference_account = lambda company, item_code, configured=None: configured

	email_style = types.ModuleType("erpnext_enhancements.email_style")

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"erpnext_enhancements.email_style": email_style,
			ACCOUNTS: accounts,
		}
	)
	import erpnext_enhancements
	import erpnext_enhancements.inventory_enhancements as inventory

	erpnext_enhancements.email_style = email_style
	inventory.stock_accounts = accounts
	from erpnext_enhancements.api import maintenance_workflow

	workflow = maintenance_workflow


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


class TestChemicalsWaitForTheAccount(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_no_account_issues_nothing_and_says_so(self):
		doc = _visit()
		workflow.create_stock_entry(doc)
		self.assertEqual(STATE["inserted"], [])
		self.assertEqual(len(STATE["comments"]), 1)
		self.assertIn("no Chemicals Used Account", STATE["comments"][0])

	def test_an_account_issues_the_chemicals_to_it(self):
		STATE["settings"]["maintenance_chemicals_account"] = "5400 - Chemicals Used - SF"
		workflow.create_stock_entry(_visit())
		(entry,) = STATE["submitted"]
		self.assertEqual(entry["stock_entry_type"], "Material Issue")
		self.assertEqual([row["expense_account"] for row in entry["items"]], ["5400 - Chemicals Used - SF"])
		self.assertEqual([row["s_warehouse"] for row in entry["items"]], ["Inventory Room - SF"])

	def test_an_unusable_account_fails_rather_than_falling_back_to_stock_adjustment(self):
		STATE["settings"]["maintenance_chemicals_account"] = "5100 - Cost of Goods - SF"
		with self.assertRaises(_Throw):
			workflow.create_stock_entry(_visit())
		self.assertEqual(STATE["inserted"], [])

	def test_nothing_used_needs_no_account_and_leaves_no_comment(self):
		workflow.create_stock_entry(_visit(qty=0))
		self.assertEqual((STATE["inserted"], STATE["comments"]), ([], []))


class TestBillingStaysInQuickBooks(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_off_does_nothing_at_all(self):
		doc = _visit()
		workflow.create_sales_invoice(doc)
		self.assertEqual(STATE["comments"], [])
		self.assertEqual(STATE["db_reads"], [("single", "maintenance_invoicing_enabled")])
		self.assertIsNone(doc.get("sales_invoice"))

	def test_on_goes_back_to_the_contract_rules(self):
		STATE["settings"]["maintenance_invoicing_enabled"] = 1
		doc = _visit()
		doc["maintenance_contract"] = "MNT-1"
		workflow.create_sales_invoice(doc)
		self.assertEqual(STATE["comments"], ["Sales Invoice not drafted: contract bills Monthly."])

	def test_on_reaches_the_contract_even_for_a_per_visit_one(self):
		STATE["settings"]["maintenance_invoicing_enabled"] = 1
		STATE["contract"] = {"invoicing_frequency": "Per Visit", "sales_order": None}
		doc = _visit()
		doc["maintenance_contract"] = "MNT-1"
		workflow.create_sales_invoice(doc)
		self.assertIn(("Sapphire Maintenance Contract", "MNT-1"), STATE["db_reads"])


class TestTheSettingsShipOff(unittest.TestCase):
	def test_both_fields_are_declared_off(self):
		path = (
			APP / "enhancements_core/doctype/erpnext_enhancements_settings/erpnext_enhancements_settings.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		invoicing = fields["maintenance_invoicing_enabled"]
		self.assertEqual((invoicing["fieldtype"], invoicing.get("default")), ("Check", "0"))
		account = fields["maintenance_chemicals_account"]
		self.assertEqual((account["fieldtype"], account["options"]), ("Link", "Account"))
		self.assertFalse(account.get("default"))
		for name in fields:
			if name.startswith("maintenance_"):
				self.assertIn(name, meta["field_order"])


if __name__ == "__main__":
	unittest.main()
