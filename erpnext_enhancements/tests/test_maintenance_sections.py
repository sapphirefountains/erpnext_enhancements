"""Tests for the modular maintenance forms + Maintenance Contract subsystem.

Covers: section -> template composition and visit-payload instantiation
(including per-feature chemistry range overrides and the wizard enrichment
columns), the pure reading-range evaluator, the consumable-warehouse fallback
chain, stock-entry row building (qty-0 prefills skipped), contract mapping
from Sales Order / Project Contract, contract validate() materialization of
frequency/next-visit defaults, contract-driven predictive scheduling for both
visit shapes (with dedupe) including flat + custom seasonal visits, mandatory
row enforcement on submit, the Visit Wizard API (bootstrap/save/finish),
next-visit date roll-forward on submit-time scheduling, and the Time Kiosk's
maintenance-form context (required / form link / submitted-since).
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
				"items": [
					{
						"sequence": 1,
						"label": "Chlorine (Gal)",
						"item": CHEMICAL_ITEM,
						"default_qty": 2,
						"qty_step": 0.5,
					}
				],
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
				"items": [
					{
						"sequence": 1,
						"label": "Pump operating normally",
						"options": "OK\nWorn\nReplace",
						"is_mandatory": 1,
					}
				],
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

	def test_visit_payload_carries_wizard_enrichment(self):
		contract = self._make_contract(features=[{"serial_no": SERIALS[0]}])
		payload = get_visit_payload(
			project=self.project, serial_no=SERIALS[0], maintenance_contract=contract.name
		)
		consumable = payload["consumables"][0]
		self.assertEqual(consumable["item_name"], "Test Chlorine")
		self.assertEqual(consumable["default_qty"], 2)
		self.assertEqual(consumable["qty_step"], 0.5)
		self.assertTrue(consumable["uom"])
		self.assertEqual(consumable["section_title"], "Test Dosing Section")
		result = payload["results"][0]
		self.assertEqual(result["options"], "OK\nWorn\nReplace")
		self.assertEqual(result["is_mandatory"], 1)
		self.assertEqual(payload["readings"][0]["section_title"], "Test Chemistry Section")

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

	def test_make_contract_from_project(self):
		"""Verbal/legacy arrangements: contract from a bare Project, features from its Serial Nos."""
		frappe.db.set_value("Project", self.project, "customer", self.customer)
		for serial in SERIALS:
			frappe.db.set_value("Serial No", serial, "custom_project", self.project)

		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			make_contract_from_project,
		)
		contract = make_contract_from_project(self.project)
		self.assertEqual(contract.customer, self.customer)
		self.assertEqual(contract.project, self.project)
		self.assertEqual({row.serial_no for row in contract.covered_features}, set(SERIALS))

	def test_single_active_contract_per_project(self):
		self._make_contract(features=[{"serial_no": SERIALS[0]}])
		with self.assertRaises(frappe.ValidationError):
			self._make_contract(features=[{"serial_no": SERIALS[1]}])

	def test_contract_validate_materializes_defaults(self):
		"""Blank row frequency inherits the contract default; blank next visit
		anchors to the start date — the scheduler never sees a silent blank."""
		contract = frappe.new_doc("Sapphire Maintenance Contract")
		contract.customer = self.customer
		contract.project = self.project
		contract.status = "Draft"
		contract.default_frequency = "Weekly"
		contract.start_date = add_days(nowdate(), 3)
		contract.append("covered_features", {"serial_no": SERIALS[0]})
		contract.append("covered_features", {"serial_no": SERIALS[1], "frequency": "Monthly"})
		contract.insert(ignore_permissions=True)

		self.assertEqual(contract.covered_features[0].frequency, "Weekly")
		self.assertEqual(
			getdate(contract.covered_features[0].next_visit_date), getdate(contract.start_date)
		)
		self.assertEqual(contract.covered_features[1].frequency, "Monthly")

	def test_iter_seasonal_visits_unifies_flat_and_custom(self):
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			iter_seasonal_visits,
		)
		contract = self._make_contract(features=[{"serial_no": SERIALS[0]}])
		contract.seasonal_startup = 1
		contract.startup_month = "April"
		contract.startup_template = self.template_name
		contract.append(
			"seasonal_visits", {"visit_label": "Filter Deep Clean", "target_month": "June"}
		)
		contract.save(ignore_permissions=True)

		visits = list(iter_seasonal_visits(contract))
		self.assertEqual(
			[(v["label"], v["target_month"]) for v in visits],
			[("Seasonal Startup", "April"), ("Filter Deep Clean", "June")],
		)
		self.assertEqual(visits[0]["template"], self.template_name)

		visits[0]["stamp"](2030)
		self.assertEqual(
			frappe.db.get_value(
				"Sapphire Maintenance Contract", contract.name, "startup_last_generated_year"
			),
			2030,
		)
		visits[1]["stamp"](2031)
		contract.reload()
		self.assertEqual(contract.seasonal_visits[0].last_generated_year, 2031)

	def test_get_project_water_features(self):
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
			get_project_water_features,
		)
		for serial in SERIALS:
			frappe.db.set_value("Serial No", serial, "custom_project", self.project)
		features = get_project_water_features(self.project)
		self.assertEqual({f["value"] for f in features}, set(SERIALS))

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

	def test_flat_seasonal_visit_generation(self):
		"""The standard startup pair lives as flat contract fields now — the
		scheduler drafts from them and stamps the dedup year on the contract."""
		month_name = getdate(nowdate()).strftime("%B")
		contract = self._make_contract(
			features=[{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": add_days(nowdate(), 60)}]
		)
		contract.seasonal_startup = 1
		contract.startup_month = month_name
		contract.startup_template = self.template_name
		contract.save(ignore_permissions=True)

		from erpnext_enhancements.tasks import generate_predictive_maintenance_records
		generate_predictive_maintenance_records()
		generate_predictive_maintenance_records()  # once per year, even across runs

		drafts = frappe.get_all(
			"Sapphire Maintenance Record",
			filters={"maintenance_contract": contract.name, "visit_label": "Seasonal Startup", "docstatus": 0},
		)
		self.assertEqual(len(drafts), 1)
		self.assertEqual(
			frappe.db.get_value(
				"Sapphire Maintenance Contract", contract.name, "startup_last_generated_year"
			),
			getdate(nowdate()).year,
		)

	# ------------------------------------------------------------------ extras

	def test_compute_completion_percent(self):
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
			compute_completion_percent,
		)
		doc = frappe._dict(
			maintenance_results=[frappe._dict(selection="Pass"), frappe._dict(selection=None, answer=None)],
			chemistry_readings=[frappe._dict(reading_value=7.4)],
			cleaning_tasks=[frappe._dict(is_done=1, notes=None)],
			consumables=[frappe._dict(qty=0)],  # never counted
		)
		self.assertEqual(compute_completion_percent(doc), 75.0)
		self.assertEqual(compute_completion_percent(frappe._dict()), 0)

	def test_out_of_range_followup_visit(self):
		original = frappe.db.get_single_value("ERPNext Enhancements Settings", "out_of_range_followup_days")
		frappe.db.set_single_value("ERPNext Enhancements Settings", "out_of_range_followup_days", 3)
		try:
			record = frappe.new_doc("Sapphire Maintenance Record")
			record.customer = self.customer
			record.project = self.project
			record.serial_no = SERIALS[0]
			record.append("chemistry_readings", {"reading": "pH", "reading_value": 9.0, "min_value": 7.2, "max_value": 7.8})
			record.insert(ignore_permissions=True)
			self.assertEqual(record.has_out_of_range_readings, 1)

			from erpnext_enhancements.api.maintenance_workflow import create_followup_visit
			create_followup_visit(record)
			create_followup_visit(record)  # deduped

			followups = frappe.get_all(
				"Sapphire Maintenance Record",
				filters={"project": self.project, "visit_label": "Chemistry Follow-Up", "docstatus": 0},
			)
			self.assertEqual(len(followups), 1)
		finally:
			frappe.db.set_single_value("ERPNext Enhancements Settings", "out_of_range_followup_days", original or 0)

	def test_mandatory_rows_block_submit(self):
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.append("maintenance_results", {"question": "Pump operating normally", "is_mandatory": 1})
		record.insert(ignore_permissions=True)

		with self.assertRaises(frappe.ValidationError):
			record.submit()

		record.reload()
		record.maintenance_results[0].selection = "Pass"
		record.save(ignore_permissions=True)
		record.submit()
		self.assertEqual(record.docstatus, 1)

	# ------------------------------------------------------------------ wizard api

	def test_visit_api_bootstrap_save_finish(self):
		from erpnext_enhancements.api.maintenance_visit import (
			finish_visit,
			get_visit_bootstrap,
			save_visit,
		)

		contract = self._make_contract(features=[{"serial_no": SERIALS[0], "frequency": "Monthly"}])
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.maintenance_contract = contract.name
		record.insert(ignore_permissions=True)

		# bootstrap instantiates the template server-side and saves real rows
		data = get_visit_bootstrap(record.name)
		readings = data["record"]["chemistry_readings"]
		self.assertTrue(readings and readings[0]["name"])
		ph = next(row for row in readings if row["reading"] == "pH")

		# step save: server re-validates and returns the out-of-range verdict
		state = save_visit(
			record.name,
			frappe.as_json({"rows": {"chemistry_readings": [{"name": ph["name"], "reading_value": 9.5}]}}),
			modified=data["state"]["modified"],
		)
		flagged = next(row for row in state["readings"] if row["name"] == ph["name"])
		self.assertEqual(flagged["out_of_range"], 1)

		# stale writes are rejected, not silently merged
		with self.assertRaises(frappe.ValidationError):
			save_visit(
				record.name,
				frappe.as_json({"fields": {"visit_notes": "lost update"}}),
				modified="2000-01-01 00:00:00.000000",
			)

		# fields outside the allowlist are dropped, allowed ones stick
		state = save_visit(
			record.name,
			frappe.as_json({"fields": {"visit_notes": "wizard note", "project": "HACK"}}),
			modified=state["modified"],
		)
		self.assertEqual(
			frappe.db.get_value("Sapphire Maintenance Record", record.name, "visit_notes"),
			"wizard note",
		)
		self.assertEqual(
			frappe.db.get_value("Sapphire Maintenance Record", record.name, "project"), self.project
		)

		# ad-hoc consumable rows append and report their new names
		state = save_visit(
			record.name,
			frappe.as_json({"rows": {"consumables": [{"item": CHEMICAL_ITEM, "qty": 1}]}}),
			modified=state["modified"],
		)
		self.assertTrue(state["added"]["consumables"])

		# answer the mandatory inspection so finishing can submit
		record.reload()
		for row in record.maintenance_results:
			row.selection = "OK"
		record.save(ignore_permissions=True)

		state = finish_visit(record.name, modified=str(record.modified))
		from frappe.model.workflow import get_workflow_name
		if get_workflow_name("Sapphire Maintenance Record"):
			# house workflow: tech finish = Draft -> Pending Review
			self.assertEqual(state["workflow_state"], "Pending Review")
			self.assertEqual(state["docstatus"], 0)
		else:
			self.assertEqual(state["docstatus"], 1)

	def test_visit_bootstrap_returns_section_instructions(self):
		from erpnext_enhancements.api.maintenance_visit import get_visit_bootstrap

		section = frappe.get_doc("Sapphire Maintenance Section", "Test Chemistry Section")
		section.step_instructions = "<p>Dip the test strip and read it after 15 seconds.</p>"
		section.append("step_images", {"image": "/files/howto.png", "caption": "Strip colour chart"})
		section.save(ignore_permissions=True)

		contract = self._make_contract(features=[{"serial_no": SERIALS[0], "frequency": "Monthly"}])
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.maintenance_contract = contract.name
		record.insert(ignore_permissions=True)

		meta = get_visit_bootstrap(record.name)["sections"].get("Test Chemistry Section")
		self.assertTrue(meta, "the chemistry section's how-to content should ride along in bootstrap")
		self.assertIn("test strip", meta["instructions"])
		self.assertEqual(meta["images"][0]["image"], "/files/howto.png")
		self.assertEqual(meta["images"][0]["caption"], "Strip colour chart")

	def test_template_named_by_template_name(self):
		"""autoname field:template_name -> the doc name IS the friendly name,
		not an opaque hash."""
		self.assertEqual(self.template_name, self.template)

	def test_get_upcoming_visits_window_and_dedupe(self):
		from erpnext_enhancements.api.maintenance_visit import create_visit_today, get_upcoming_visits

		contract = self._make_contract(
			features=[
				{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": add_days(nowdate(), 15)},
				{"serial_no": SERIALS[1], "frequency": "Monthly", "next_visit_date": add_days(nowdate(), 3)},
			]
		)

		# 15 days out = in the 8–30 window; 3 days out = inside the scheduler
		# horizon, so it's a "Today's Visits" draft, not an upcoming pull-forward.
		ours = [u for u in get_upcoming_visits() if u["contract"] == contract.name]
		self.assertEqual([u["serial_no"] for u in ours], [SERIALS[0]])

		# a narrower look-ahead drops the 15-day visit too
		near = [u for u in get_upcoming_visits(days=10) if u["contract"] == contract.name]
		self.assertFalse(near)

		# once "Do Visit Today" creates a draft, the feature leaves the list
		create_visit_today(contract.name, SERIALS[0])
		after = [
			u for u in get_upcoming_visits()
			if u["contract"] == contract.name and u["serial_no"] == SERIALS[0]
		]
		self.assertFalse(after)

	def test_create_visit_today_is_extra_one_off(self):
		from erpnext_enhancements.api.maintenance_visit import EXTRA_VISIT_LABEL, create_visit_today
		from erpnext_enhancements.api.maintenance_scheduling import update_next_visit_dates

		due = add_days(nowdate(), 15)
		contract = self._make_contract(
			features=[{"serial_no": SERIALS[0], "frequency": "Monthly", "next_visit_date": due}]
		)
		name = create_visit_today(contract.name, SERIALS[0])
		record = frappe.get_doc("Sapphire Maintenance Record", name)
		self.assertEqual(record.visit_label, EXTRA_VISIT_LABEL)
		self.assertEqual(record.serial_no, SERIALS[0])
		self.assertEqual(record.maintenance_contract, contract.name)

		# extra one-off: completing it must NOT advance the feature's cadence —
		# the originally scheduled visit still stands.
		update_next_visit_dates(record, None)
		contract.reload()
		self.assertEqual(getdate(contract.covered_features[0].next_visit_date), getdate(due))

	def test_haversine_distance(self):
		from erpnext_enhancements.api.time_kiosk import _haversine_m
		# one degree of latitude ≈ 111.2 km
		self.assertAlmostEqual(_haversine_m(40.0, -111.0, 41.0, -111.0), 111195, delta=300)
		self.assertEqual(_haversine_m(40.0, -111.0, 40.0, -111.0), 0)

	def test_chemistry_trends(self):
		from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
			_chemistry_trends,
		)
		record = frappe.new_doc("Sapphire Maintenance Record")
		record.customer = self.customer
		record.project = self.project
		record.serial_no = SERIALS[0]
		record.append("chemistry_readings", {"reading": "pH", "reading_value": 8.4, "min_value": 7.2, "max_value": 7.8})
		record.insert(ignore_permissions=True)
		record.submit()

		trends = _chemistry_trends(self.project, SERIALS[0])
		ph = next(t for t in trends if t["reading"] == "pH")
		self.assertEqual(ph["points"][-1]["value"], 8.4)
		self.assertEqual(ph["points"][-1]["out_of_range"], 1)

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
