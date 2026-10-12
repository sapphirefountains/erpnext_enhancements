# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What approving a maintenance visit does, and does not, do. Bench-free.

Approve & Submit runs ``api/maintenance_workflow.process_maintenance_submission``. On
2026-10-12, before and right after the first visits were approved on prod, Nik decided:

* **"Keep billing outside of ERPNext until we switch over from QBO."** ERPNext Enhancements
  Settings → *Draft Invoices from Approved Visits* ships off, and while it is off
  ``create_sales_invoice`` does nothing at all (v1.593.0).
* **"Can we just make it record the chemicals and then we update stock when we take it from our
  inventory to vehicles?"** Approval never issues chemicals (v1.594.0). A visit records the dose
  in the unit the technician counts in (a tablet, a cap full of flocculant, half a bottle), and
  stock moves by the container on the Stock Scan page. Tablets per bucket varies by brand and
  size, so no conversion from a dose to the stock unit exists to make. v1.593.0's *Chemicals
  Used Account* was removed with the step it gated.

The second half pins the dose unit itself: a template's Chemical Dosing line names it, the visit
keeps it as written, and a chemical added on site can carry one.

Run: python -m unittest erpnext_enhancements.tests.test_visit_approval_switches
"""

import ast
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
STUBBED = ("frappe", "frappe.utils", "erpnext_enhancements.email_style", WORKFLOW)

workflow = None
STATE = {}
_saved = {}


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


def _reset():
	STATE.clear()
	STATE.update(
		{
			"settings": {},
			"comments": [],
			"db_reads": [],
			"contract": {"invoicing_frequency": "Monthly", "sales_order": None},
		}
	)


def _visit():
	return _Doc(
		name="SMR-1",
		project="PRJ-1",
		technician="austin@example.com",
		serial_no="SN-1",
		maintenance_contract=None,
		consumables=[_Row(item="CON-SRV-FLOCCULANT", qty=1.0, uom="cap full", serial_no="SN-1")],
	)


def setUpModule():
	global workflow
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.get_link_to_form = lambda doctype, name: f"{doctype} {name}"
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire Fountains")
	frappe.get_all = lambda *a, **k: []
	frappe.get_single = lambda doctype: _Row(maintenance_fee_item="SRV-202", maintenance_services_group=None)

	def _get_single_value(doctype, field):
		STATE["db_reads"].append(("single", field))
		return STATE["settings"].get(field)

	def _get_value(doctype, name, fields=None, as_dict=False, **kwargs):
		STATE["db_reads"].append((doctype, name))
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

	email_style = types.ModuleType("erpnext_enhancements.email_style")

	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "erpnext_enhancements.email_style": email_style}
	)
	import erpnext_enhancements

	erpnext_enhancements.email_style = email_style
	from erpnext_enhancements.api import maintenance_workflow

	workflow = maintenance_workflow


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


class TestApprovalNeverIssuesChemicals(unittest.TestCase):
	"""Stock moves by the container, on Stock Scan. A dose is not a stock quantity."""

	def _steps(self):
		"""The step functions ``process_maintenance_submission`` chains, read from its source."""
		src = (APP / "api/maintenance_workflow.py").read_text(encoding="utf-8")
		fn = next(
			n
			for n in ast.parse(src).body
			if isinstance(n, ast.FunctionDef) and n.name == "process_maintenance_submission"
		)
		return {
			elt.elts[1].id
			for node in ast.walk(fn)
			if isinstance(node, ast.List)
			for elt in node.elts
			if isinstance(elt, ast.Tuple) and len(elt.elts) == 2 and isinstance(elt.elts[1], ast.Name)
		}

	def test_the_chain_has_no_stock_step(self):
		steps = self._steps()
		self.assertIn("create_timesheet", steps, "the step list must still be found")
		self.assertNotIn("create_stock_entry", steps)

	def test_the_module_builds_no_stock_entry(self):
		self.assertFalse(hasattr(workflow, "create_stock_entry"))
		self.assertFalse(hasattr(workflow, "build_stock_entry_rows"))
		src = (APP / "api/maintenance_workflow.py").read_text(encoding="utf-8")
		self.assertNotIn('new_doc("Stock Entry")', src)

	def test_the_account_setting_is_gone(self):
		"""v1.593.0 gated the stock step on it; with no step there is nothing to gate."""
		path = (
			APP / "enhancements_core/doctype/erpnext_enhancements_settings/erpnext_enhancements_settings.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		self.assertNotIn("maintenance_chemicals_account", {f["fieldname"] for f in meta["fields"]})
		self.assertNotIn("maintenance_chemicals_account", meta["field_order"])


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

	def test_the_switch_is_declared_off(self):
		path = (
			APP / "enhancements_core/doctype/erpnext_enhancements_settings/erpnext_enhancements_settings.json"
		)
		meta = json.loads(path.read_text(encoding="utf-8"))
		field = {f["fieldname"]: f for f in meta["fields"]}["maintenance_invoicing_enabled"]
		self.assertEqual((field["fieldtype"], field.get("default")), ("Check", "0"))
		self.assertIn("maintenance_invoicing_enabled", meta["field_order"])


class TestDoseUnitsAreTheTechniciansOwn(unittest.TestCase):
	"""Nik: "those dose units need to be custom. For example adding flocculant we only do a cap
	full or about a tablespoon out of a bottle"."""

	def _field(self, doctype_folder, fieldname):
		path = APP / f"sapphire_maintenance/doctype/{doctype_folder}/{doctype_folder}.json"
		meta = json.loads(path.read_text(encoding="utf-8"))
		return {f["fieldname"]: f for f in meta["fields"]}[fieldname]

	def test_the_visit_keeps_the_unit_as_written(self):
		"""``fetch_from: item.stock_uom`` re-fetched "Unit" over it on every save."""
		uom = self._field("sapphire_maintenance_consumable", "uom")
		self.assertNotIn("fetch_from", uom)
		self.assertFalse(uom.get("read_only"))

	def test_the_template_line_carries_the_dose_unit(self):
		uom = self._field("sapphire_section_item", "uom")
		self.assertIn("Chemical Dosing", uom["description"])
		src = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_record/sapphire_maintenance_record.py"
		).read_text(encoding="utf-8")
		self.assertIn('uom=(item.uom or "").strip() or details.stock_uom,', src)

	def test_the_template_form_shows_the_unit_for_chemical_dosing(self):
		src = (
			APP / "sapphire_maintenance/doctype/sapphire_maintenance_section/sapphire_maintenance_section.js"
		).read_text(encoding="utf-8")
		self.assertIn('"Chemical Dosing": ["item", "uom"],', src)

	def test_a_chemical_added_on_site_can_carry_one(self):
		wizard = (APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js").read_text(encoding="utf-8")
		self.assertIn('fieldname: "uom"', wizard)
		self.assertIn("uom: row.uom,", wizard)
		visit_api = (APP / "api/maintenance_visit.py").read_text(encoding="utf-8")
		self.assertIn('"consumables": {"item", "qty", "warehouse", "uom"},', visit_api)


if __name__ == "__main__":
	unittest.main()
