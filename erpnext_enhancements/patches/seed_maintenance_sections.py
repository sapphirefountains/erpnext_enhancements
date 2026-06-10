"""Seed the modular maintenance-form building blocks.

Creates the four sample Sapphire Maintenance Sections (Chemical Dosing, Water
Chemistry Readings, Equipment Inspection, Cleaning Tasks), three Draft
templates composing them (Standard Fountain Maintenance, Seasonal Startup,
Winterization), the "Maintenance Supervisor" role the out-of-range
notification emails, and sensible Settings defaults.

**Insert-only and idempotent** (model: ``seed_process_step_templates``): a
section/template whose name already exists is left untouched, so site-side
edits survive re-migrations. Item codes and warehouse names appear here *once*
at seed time and are existence-guarded — rows for missing items/warehouses are
simply skipped (runtime code never matches anything by code string; sections
store Item links, which Frappe keeps valid through renames).
"""

import frappe

CHEMICALS = [
	# (item_code at seed time, label)
	("CON-SRV-ACID-GAL", "Muriatic Acid (Gal)"),
	("CON-SRV-CHLORINE-GAL", "Liquid Chlorine (Gal)"),
	("CON-SRV-CHLORINE-GRANULAR", "Granular Chlorine"),
	("CON-SRV-CHLORINE-TABITS", "Chlorine Tablets"),
]

READINGS = [
	# (label, uom, min, max)
	("pH", "pH", 7.2, 7.8),
	("Free Chlorine", "ppm", 1.0, 3.0),
	("ORP", "mV", 650, 750),
	("Total Alkalinity", "ppm", 80, 120),
]

INSPECTIONS = [
	"Pump operating normally",
	"Filter condition / pressure",
	"Lights functioning",
	"Autofill / water level",
	"Skimmer and drains clear",
	"Visible leaks",
]

CLEANING = [
	"Skim surface debris",
	"Brush walls and tile",
	"Empty skimmer / pump baskets",
	"Backwash or rinse filter",
	"Wipe down equipment",
	"Tidy equipment area",
]

SECTIONS = {
	"Chemical Dosing": "Chemical Dosing",
	"Water Chemistry Readings": "Water Chemistry",
	"Equipment Inspection": "Equipment Inspection",
	"Cleaning Tasks": "Cleaning Tasks",
}

TEMPLATES = {
	"Standard Fountain Maintenance": [
		"Equipment Inspection",
		"Water Chemistry Readings",
		"Cleaning Tasks",
		"Chemical Dosing",
	],
	"Seasonal Startup": ["Equipment Inspection", "Cleaning Tasks", "Chemical Dosing"],
	"Winterization": ["Equipment Inspection", "Cleaning Tasks"],
}


def execute():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Section"):
		# fresh install ordering safety; doctype sync precedes post_model_sync
		# patches, so this should never trip — belt and suspenders.
		return

	_seed_role()
	_seed_settings_defaults()
	_seed_sections()
	_seed_templates()


def _seed_role():
	if not frappe.db.exists("Role", "Maintenance Supervisor"):
		role = frappe.new_doc("Role")
		role.role_name = "Maintenance Supervisor"
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def _seed_settings_defaults():
	settings = frappe.get_single("ERPNext Enhancements Settings")
	changed = False
	if not settings.consumables_item_group and frappe.db.exists("Item Group", "Service"):
		settings.consumables_item_group = "Service"
		changed = True
	if not settings.default_consumables_warehouse and frappe.db.exists("Warehouse", "Service Truck - SF"):
		settings.default_consumables_warehouse = "Service Truck - SF"
		changed = True
	if not settings.water_feature_item and frappe.db.exists("Item", "Customer Water Feature"):
		settings.water_feature_item = "Customer Water Feature"
		changed = True
	if changed:
		settings.save(ignore_permissions=True)


def _seed_sections():
	if not frappe.db.exists("Sapphire Maintenance Section", "Chemical Dosing"):
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = "Chemical Dosing"
		section.section_type = "Chemical Dosing"
		section.description = "Chemicals consumed during the visit. Quantities entered here reduce stock on submit."
		for sequence, (item_code, label) in enumerate(CHEMICALS, start=1):
			if frappe.db.exists("Item", item_code):
				section.append("items", {"sequence": sequence, "label": label, "item": item_code})
		if section.items:
			section.insert(ignore_permissions=True)

	if not frappe.db.exists("Sapphire Maintenance Section", "Water Chemistry Readings"):
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = "Water Chemistry Readings"
		section.section_type = "Water Chemistry"
		section.description = "Measured values. Out-of-range readings flag the record and email Maintenance Supervisors."
		for sequence, (label, uom, low, high) in enumerate(READINGS, start=1):
			section.append(
				"items",
				{"sequence": sequence, "label": label, "uom": uom, "min_value": low, "max_value": high},
			)
		section.insert(ignore_permissions=True)

	if not frappe.db.exists("Sapphire Maintenance Section", "Equipment Inspection"):
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = "Equipment Inspection"
		section.section_type = "Equipment Inspection"
		section.description = "Fail/Replace selections on in-warranty features raise a draft Warranty Claim."
		for sequence, label in enumerate(INSPECTIONS, start=1):
			section.append("items", {"sequence": sequence, "label": label})
		section.insert(ignore_permissions=True)

	if not frappe.db.exists("Sapphire Maintenance Section", "Cleaning Tasks"):
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = "Cleaning Tasks"
		section.section_type = "Cleaning Tasks"
		for sequence, label in enumerate(CLEANING, start=1):
			section.append("items", {"sequence": sequence, "label": label})
		section.insert(ignore_permissions=True)


def _seed_templates():
	for template_name, section_names in TEMPLATES.items():
		if frappe.db.exists("Sapphire Maintenance Template", {"template_name": template_name}):
			continue
		template = frappe.new_doc("Sapphire Maintenance Template")
		template.template_name = template_name
		template.status = "Draft"
		for section_name in section_names:
			if frappe.db.exists("Sapphire Maintenance Section", section_name):
				template.append("sections", {"section": section_name})
		if template.sections:
			template.insert(ignore_permissions=True)
