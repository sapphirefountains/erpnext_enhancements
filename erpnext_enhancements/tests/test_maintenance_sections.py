"""Tests for the modular maintenance forms + Maintenance Contract subsystem.

Covers: section -> template composition and visit-payload instantiation
(including per-feature chemistry range overrides), the pure reading-range
evaluator, the consumable-warehouse fallback chain, stock-entry row building
(qty-0 prefills skipped), contract mapping from Sales Order / Project
Contract, contract-driven predictive scheduling for both visit shapes (with
dedupe), next-visit date roll-forward on submit-time scheduling, and the Time
Kiosk's maintenance-form context (required / form link / submitted-since).
"""
import frappe
import unittest
from frappe.utils import nowdate, add_days, getdate

from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
	evaluate_reading_ranges,
	get_visit_payload,
)
from erpnext_enhancements.api.maintenance_workflow import (
	build_stock_entry_rows,
	resolve_consumable_warehouse,
)

WATER_FEATURE_ITEM = "Customer Water Feature"
CHEMICAL_ITEM = "TEST-MNT-CHLORINE"
SERIALS = ["TEST-MNT-SN-A", "TEST-MNT-SN-B"]
PROJECT = "TEST-PROJECT-MNT-SECTIONS"


class TestMaintenanceSections(unittest.TestCase):
	def setUp(self):
		if not frappe.db.exists("Item", WATER_FEATURE_ITEM):
			item = frappe.new_doc("Item")
			item.item_code = WATER_FEATURE_ITEM
			item.item_name = WATER_FEATURE_ITEM
			item.item_group = "All Item Groups"
			item.is_stock_item = 0
			item.insert(ignore_permissions=True)

		if not frappe.db.exists("Item", CHEMICAL_ITEM):
			item = frappe.new_doc("Item")
			item.item_code = CHEMICAL_ITEM
			item.item_name = "Test Chlorine"
			item.item_group = "All Item Groups"
			item.is_stock_item = 1
			item.insert(ignore_permissions=True)

		for serial in SERIALS:
			if not frappe.db.exists("Serial No", serial):
				sn = frappe.new_doc("Serial No")
				sn.item_code = WATER_FEATURE_ITEM
				sn.serial_no = serial
				sn.insert(ignore_permissions=True)

		if not frappe.db.exists("Project", PROJECT):
			project = frappe.new_doc("Project")
			project.project_name = PROJECT
			project.status = "Open"
			project.insert(ignore_permissions=True)
		self.project = frappe.db.get_value("Project", {"project_name": PROJECT}, "name") or PROJECT

		self.customer = frappe.get_all("Customer", limit=1)[0].name

		self.sections = {
			"Test Dosing Section": {
				"section_type": "Chemical Dosing",
				"items": [{"sequence": 1, "label": "Chlorine (Gal)", "item": CHEMICAL_ITEM}],
			},
			"Test Chemistry Section": {
				"section_type": "Water Chemistry",
				"items": [
					{"sequence": 1, "label": "pH", "uom": "pH", "min_value": 7.2, "max_value": 7.8},
					{"sequence": 2, "label": "Free Chlorine", "uom": "ppm", "min_value": 1.0, "max_value": 3.0},
				],
			},
			"Test Inspection Section": {
				"section_type": "Equipment Inspection",
				"items": [{"sequence": 1, "label": "Pump operating normally"}],
			},
			"Test Cleaning Section": {
				"section_type": "Cleaning Tasks",
				"items": [{"sequence": 1, "label": "Skim surface debris"}],
			},
		}
		for title, definition in self.sections.items():
			if frappe.db.exists("Sapphire Maintenance Section", title):
				continue
			section = frappe.new_doc("Sapphire Maintenance Section")
			section.section_title = title
			section.section_type = definition["section_type"]
			for row in definition["items"]:
				section.append("items", row)
			section.insert(ignore_permissions=True)

		self.template = "Test Composed Template"
		if not frappe.db.exists("Sapphire Maintenance Template", {"template_name": self.template}):
			template = frappe.new_doc("Sapphire Maintenance Template")
			template.template_name = self.template
			template.status = "Draft"
			for title in self.sections:
				template.append("sections", {"section": title})
			template.insert(ignore_permissions=True)
		self.template_name = frappe.db.get_value(
			"Sapphire Maintenance Template", {"template_name": self.template}, "name"
		)

	def _make_contract(self, visit_shape="Per Feature", status="Active", features=None):
		contract = frappe.new_doc("Sapphire Maintenance Contract")
		contract.customer = self.customer
		contract.project = self.project
		contract.status = status
		contract.visit_shape = visit_shape
		contract.default_template = self.template_name
		for feature in features or []:
			contract.append("covered_features", feature)
		contract.insert(ignore_permissions=True)
		return contract

	def tearDown(self):
		frappe.db.rollback()

	# ------------------------------------------------------------------ payload

	def test_visit_payload_instantiates_all_section_types(self):
		contract = self._make_contract(
			features=[{"serial_no": SERIALS[0], "frequency": "Monthly"}]
		)
		payload = get_visit_payload(
			project=self.project, serial_no=SERIALS[0], maintenance_contract=contract.name
		)
		self.assertEqual(payload["template"], self.template_name)
		self.assertEqual([r["question"] for r in payload["results"]], ["Pump operating normally"])
		self.assertEqual([r["reading"] for r in payload["readings"]], ["pH", "Free Chlorine"])
		self.assertEqual([r["task"] for r in payload["tasks"]], ["Skim surface debris"])
		self.assertEqual([r["item"] for r in payload["consumables"]], [CHEMICAL_ITEM])
		self.assertEqual(payload["consumables"][0]["qty"], 0)
		for key in ("results", "readings", "tasks", "consumables"):
			self.assertTrue(all(row["serial_no"] == SERIALS[0] for row in payload[key]))

	def test_visit_payload_per_site_covers_all_features(self):
		contract = self._make_contract(
			visit_shape="Per Site Visit",
			features=[
				{"serial_no": SERIALS[0], "frequency": "Monthly"},
				{"serial_no": SERIALS[1], "frequency": "Monthly"},
			],
		)
		payload = get_visit_payload(maintenance_contract=contract.name)
		self.assertEqual(len(payload["readings"]), 4)  # 2 readings x 2 features
		self.assertEqual(
			{row["serial_no"] for row in payload["readings"]}, set(SERIALS)
		)

	def test_reading_range_override_from_serial_no(self):
		serial = frappe.get_doc("Serial No", SERIALS[0])
		serial.append("custom_reading_overrides", {"reading": "pH", "min_value": 6.8, "max_value": 7.4})
		serial.save(ignore_permissions=True)

		contract = self._make_contract(features=[{"serial_no": SERIALS[0]}])
		payload = get_visit_payload(
			project=self.project, serial_no=SERIALS[0], maintenance_contract=contract.name
		)
		ph = next(r for r in payload["readings"] if r["reading"] == "pH")
		self.assertEqual((ph["min_value"], ph["max_value"]), (6.8, 7.4))
		chlorine = next(r for r in payload["readings"] if r["reading"] == "Free Chlorine")
		self.assertEqual((chlorine["min_value"], chlorine["max_value"]), (1.0, 3.0))

	# ------------------------------------------------------------------ readings

	def test_evaluate_reading_ranges(self):
		rows = [
			frappe._dict(reading="pH", reading_value=7.5, min_value=7.2, max_value=7.8),
			frappe._dict(reading="pH high", reading_value=8.2, min_value=7.2, max_value=7.8),
			frappe._dict(reading="Chlorine low", reading_value=0.5, min_value=1.0, max_value=3.0),
			frappe._dict(reading="not measured", reading_value=0, min_value=1.0, max_value=3.0),
			frappe._dict(reading="no bounds", reading_value=42, min_value=0, max_value=0),
		]
		flagged = evaluate_reading_ranges(rows)
		self.assertEqual([r.reading for r in flagged], ["pH high", "Chlorine low"])
		self.assertEqual([r.out_of_range for r in rows], [0, 1, 1, 0, 0])

	def test_record_validate_sets_out_of_range_flag(self):
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.append("chemistry_readings", {"reading": "pH", "reading_value": 9.0, "min_value": 7.2, "max_value": 7.8})
		record.insert(ignore_permissions=True)
		self.assertEqual(record.has_out_of_range_readings, 1)
		self.assertEqual(record.chemistry_readings[0].out_of_range, 1)

	# ------------------------------------------------------------------ warehouse

	def test_resolve_consumable_warehouse_fallbacks(self):
		settings_value = frappe.db.get_single_value(
			"ERPNext Enhancements Settings", "default_consumables_warehouse"
		)
		warehouse = frappe.get_all("Warehouse", filters={"is_group": 0}, limit=2, pluck="name")
		self.assertTrue(warehouse, "test site needs at least one leaf warehouse")

		# 1. feature warehouse wins
		self.assertEqual(
			resolve_consumable_warehouse(feature_warehouse=warehouse[0], technician="nobody@example.com"),
			warehouse[0],
		)
		# 2. technician's vehicle warehouse
		employee = frappe.get_all("Employee", filters={"user_id": ["is", "set"]}, fields=["name", "user_id"], limit=1)
		if employee:
			frappe.db.set_value("Employee", employee[0].name, "custom_default_vehicle_warehouse", warehouse[0])
			self.assertEqual(
				resolve_consumable_warehouse(technician=employee[0].user_id), warehouse[0]
			)
		# 3. settings default
		frappe.db.set_single_value(
			"ERPNext Enhancements Settings", "default_consumables_warehouse", warehouse[-1]
		)
		self.assertEqual(resolve_consumable_warehouse(), warehouse[-1])
		frappe.db.set_single_value(
			"ERPNext Enhancements Settings", "default_consumables_warehouse", settings_value
		)

	def test_build_stock_entry_rows_skips_untouched_prefills(self):
		warehouse = frappe.get_all("Warehouse", filters={"is_group": 0}, limit=1, pluck="name")[0]
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.append("consumables", {"item": CHEMICAL_ITEM, "qty": 0, "warehouse": warehouse})
		record.append("consumables", {"item": CHEMICAL_ITEM, "qty": 2, "warehouse": warehouse})
		rows = build_stock_entry_rows(record)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty"], 2)
		self.assertEqual(rows[0]["s_warehouse"], warehouse)

	# ------------------------------------------------------------------ contract

	def test_make_contract_from_sales_order(self):
		so = frappe.new_doc("Sales Order")
		so.customer = self.customer
		so.transaction_date = nowdate()
		so.delivery_date = add_days(nowdate(), 30)
		so.project = self.project
		for serial in SERIALS:
			so.append("items", {
				"item_code": WATER_FEATURE_ITEM,
				"qty": 1,
				"rate": 100,
				"custom_serial_no": serial,
				"custom_maintenance_frequency": "Monthly",
			})
		so.insert(ignore_permissions=True)
		so.submit()

		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			make_contract_from_sales_order,
		)
		contract = make_contract_from_sales_order(so.name)
		self.assertEqual(contract.customer, self.customer)
		self.assertEqual(contract.sales_order, so.name)
		self.assertEqual(
			{row.serial_no for row in contract.covered_features}, set(SERIALS)
		)
		self.assertTrue(all(row.frequency == "Monthly" for row in contract.covered_features))

	def test_single_active_contract_per_project(self):
		self._make_contract(features=[{"serial_no": SERIALS[0]}])
		with self.assertRaises(frappe.ValidationError):
			self._make_contract(features=[{"serial_no": SERIALS[1]}])

	# ------------------------------------------------------------------ scheduling

	def test_contract_scheduling_per_feature_with_dedupe(self):
		self._make_contract(
			features=[
				{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": nowdate()},
				{"serial_no": SERIALS[1], "frequency": "Monthly", "next_visit_date": add_days(nowdate(), 60)},
			]
		)
		from erpnext_enhancements.tasks import generate_predictive_maintenance_records
		generate_predictive_maintenance_records()
		generate_predictive_maintenance_records()  # dedupe: second run adds nothing

		drafts = frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"project": self.project, "docstatus": 0},
			fields=["serial_no"],
		)
		self.assertEqual([d.serial_no for d in drafts], [SERIALS[0]])

	def test_contract_scheduling_per_site_visit(self):
		contract = self._make_contract(
			visit_shape="Per Site Visit",
			features=[
				{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": nowdate()},
				{"serial_no": SERIALS[1], "frequency": "Monthly", "next_visit_date": nowdate()},
			],
		)
		from erpnext_enhancements.tasks import generate_predictive_maintenance_records
		generate_predictive_maintenance_records()
		generate_predictive_maintenance_records()

		drafts = frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"maintenance_contract": contract.name, "docstatus": 0},
			fields=["serial_no"],
		)
		self.assertEqual(len(drafts), 1)
		self.assertFalse(drafts[0].serial_no)

	def test_seasonal_visit_generation(self):
		month_name = getdate(nowdate()).strftime("%B")
		contract = self._make_contract(features=[{"serial_no": SERIALS[0], "frequency": "Monthly"}])
		contract.append(
			"seasonal_visits",
			{"visit_label": "Seasonal Startup", "target_month": month_name, "template": self.template_name},
		)
		contract.save(ignore_permissions=True)

		from erpnext_enhancements.tasks import generate_predictive_maintenance_records
		generate_predictive_maintenance_records()
		generate_predictive_maintenance_records()

		drafts = frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"maintenance_contract": contract.name, "visit_label": "Seasonal Startup", "docstatus": 0},
		)
		self.assertEqual(len(drafts), 1)
		contract.reload()
		self.assertEqual(contract.seasonal_visits[0].last_generated_year, getdate(nowdate()).year)

	# ------------------------------------------------------------------ kiosk

	def test_kiosk_maintenance_context_and_submission_check(self):
		from frappe.utils import now_datetime
		from erpnext_enhancements.api.time_kiosk import get_maintenance_context

		# No Active contract and no Active template -> not required
		self.assertFalse(get_maintenance_context(project=self.project)["required"])

		contract = self._make_contract(features=[{"serial_no": SERIALS[0], "frequency": "Monthly"}])
		clock_in = now_datetime()

		ctx = get_maintenance_context(project=self.project, since=clock_in)
		self.assertTrue(ctx["required"])
		self.assertEqual(ctx["contract"], contract.name)
		self.assertIn("/app/sapphire-maintenance-record/new?", ctx["form_route"])
		self.assertIn("maintenance_contract=", ctx["form_route"])
		self.assertFalse(ctx["submitted_since"])

		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.technician = "Administrator"
		record.maintenance_contract = contract.name
		record.insert(ignore_permissions=True)

		# An open draft becomes the link target
		ctx = get_maintenance_context(project=self.project)
		self.assertEqual(ctx["draft"], record.name)
		self.assertTrue(ctx["form_route"].endswith(record.name))

		record.submit()
		ctx = get_maintenance_context(project=self.project, since=clock_in)
		self.assertTrue(ctx["submitted_since"])

	def test_update_next_visit_dates_rolls_contract_forward(self):
		contract = self._make_contract(
			features=[{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": nowdate()}]
		)
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.maintenance_contract = contract.name
		record.insert(ignore_permissions=True)

		from erpnext_enhancements.api.maintenance_scheduling import update_next_visit_dates, calculate_next_date
		update_next_visit_dates(record, None)

		contract.reload()
		feature = contract.covered_features[0]
		self.assertEqual(getdate(feature.last_visit_date), getdate(nowdate()))
		self.assertEqual(
			getdate(feature.next_visit_date),
			getdate(calculate_next_date(getdate(nowdate()), "Monthly")),
		)
