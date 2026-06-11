"""Seed the expanded maintenance catalog: more Sections, Templates, Plans.

Companion to ``seed_maintenance_sections`` (the four original Sections + three
Draft Templates) and ``seed_service_plans`` (the four standard plans). This
patch adds a broader library so the office has ready-made building blocks for
the common feature types (spray features, pondless, interior fountains, large
displays) without composing them by hand.

**Insert-only and idempotent** (same model): a Section/Template/Plan whose name
already exists is left untouched, so site-side edits survive re-migrations.
Templates link Sections by name and Plans link Templates by name; a missing
dependency is skipped, never created blindly. No new **Chemical Dosing**
sections are seeded here — those require per-row Item links (Item codes differ
by site), so they stay in the item-existence-guarded original seed; the new
templates reuse the original "Chemical Dosing" section where dosing applies.
"""

import frappe

# (section_title, section_type, description, [items])
# item dict keys by type: Water Chemistry -> uom/min_value/max_value;
# Equipment Inspection -> options (blank = Pass/Fail/Replace/Other)/is_mandatory;
# Cleaning Tasks -> label only.
SECTIONS = [
	(
		"Advanced Water Chemistry",
		"Water Chemistry",
		"Secondary water balance readings for larger or salt systems.",
		[
			{"label": "Calcium Hardness", "uom": "ppm", "min_value": 150, "max_value": 400},
			{"label": "Cyanuric Acid", "uom": "ppm", "min_value": 30, "max_value": 50},
			{"label": "Total Dissolved Solids", "uom": "ppm", "min_value": 0, "max_value": 1500},
			{"label": "Salt", "uom": "ppm", "min_value": 2700, "max_value": 3400},
			{"label": "Water Temperature", "uom": "°F"},
		],
	),
	(
		"Pump & Filter Service",
		"Equipment Inspection",
		"Pump and filtration condition checks.",
		[
			{"label": "Pump basket cleaned", "is_mandatory": 1},
			{"label": "Filter backwashed / rinsed"},
			{"label": "Impeller clear of debris"},
			{"label": "Shaft seal condition"},
			{"label": "Filter pressure within normal range"},
		],
	),
	(
		"Lighting Inspection",
		"Equipment Inspection",
		"Water-feature lighting and its electrical supply.",
		[
			{"label": "Fixtures operational"},
			{"label": "Transformer / driver OK"},
			{"label": "GFCI trips correctly", "is_mandatory": 1},
			{"label": "Lenses / gaskets intact"},
			{"label": "Bulbs / LEDs replaced as needed"},
		],
	),
	(
		"Auto-Fill & Water Level",
		"Equipment Inspection",
		"Make-up water system and leak check.",
		[
			{"label": "Auto-fill valve operates"},
			{"label": "Float / level sensor OK"},
			{"label": "No visible leaks"},
			{"label": "Water level correct"},
			{"label": "Overflow / drain clear"},
		],
	),
	(
		"Algae & Water Clarity",
		"Cleaning Tasks",
		"Algae control and basin clarity tasks.",
		[
			{"label": "Brush walls and tile"},
			{"label": "Treat visible algae"},
			{"label": "Clear surface debris"},
			{"label": "Clean weirs / spillways"},
			{"label": "Vacuum basin floor"},
		],
	),
	(
		"Spring Startup Steps",
		"Cleaning Tasks",
		"Bringing a feature back online for the season.",
		[
			{"label": "Remove winter cover"},
			{"label": "Refill basin"},
			{"label": "Prime and start pump"},
			{"label": "Reconnect / aim nozzles"},
			{"label": "Inspect for winter damage"},
			{"label": "Balance chemistry"},
		],
	),
	(
		"Winterization Steps",
		"Cleaning Tasks",
		"Shutting a feature down for winter.",
		[
			{"label": "Drain basin and lines"},
			{"label": "Blow out plumbing"},
			{"label": "Remove and store pump"},
			{"label": "Add antifreeze where needed"},
			{"label": "Install winter cover"},
			{"label": "Disconnect / secure power"},
		],
	),
	(
		"Interior Fountain Care",
		"Cleaning Tasks",
		"Indoor / lobby fountain upkeep.",
		[
			{"label": "Wipe and polish surfaces"},
			{"label": "Descale nozzles / jets"},
			{"label": "Top off and treat water"},
			{"label": "Clean reservoir / indoor pump"},
			{"label": "Check for splash / overflow"},
		],
	),
	(
		"Safety & Electrical",
		"Equipment Inspection",
		"Electrical safety checks for the site. GFCI protection is mandatory.",
		[
			{"label": "GFCI protection verified", "is_mandatory": 1},
			{"label": "Bonding / grounding intact"},
			{"label": "No exposed wiring"},
			{"label": "Junction boxes sealed"},
			{"label": "Signage / barriers in place"},
		],
	),
]

# template_name -> ordered list of Section names (base + new)
TEMPLATES = {
	"Spray Feature Maintenance": [
		"Safety & Electrical",
		"Equipment Inspection",
		"Water Chemistry Readings",
		"Algae & Water Clarity",
		"Chemical Dosing",
	],
	"Pondless Water Feature Maintenance": [
		"Safety & Electrical",
		"Pump & Filter Service",
		"Auto-Fill & Water Level",
		"Cleaning Tasks",
	],
	"Interior Fountain Maintenance": [
		"Interior Fountain Care",
		"Water Chemistry Readings",
		"Lighting Inspection",
	],
	"Large Display Fountain Maintenance": [
		"Safety & Electrical",
		"Equipment Inspection",
		"Pump & Filter Service",
		"Lighting Inspection",
		"Auto-Fill & Water Level",
		"Water Chemistry Readings",
		"Advanced Water Chemistry",
		"Algae & Water Clarity",
		"Chemical Dosing",
		"Cleaning Tasks",
	],
	"Spring Startup — Full": [
		"Safety & Electrical",
		"Spring Startup Steps",
		"Equipment Inspection",
		"Water Chemistry Readings",
		"Chemical Dosing",
	],
	"Winterization — Full": [
		"Safety & Electrical",
		"Winterization Steps",
		"Equipment Inspection",
	],
}

# Service Plans. startup/winterization templates default to the "— Full"
# variants this patch seeds.
FULL_STARTUP = "Spring Startup — Full"
FULL_WINTER = "Winterization — Full"
PLANS = [
	{
		"plan_name": "Weekly Spray Feature",
		"default_frequency": "Weekly",
		"template": "Spray Feature Maintenance",
		"visit_shape": "Per Feature",
		"seasonal": True,
		"description": "Weekly visit for spray/jet features, with spring startup and fall winterization.",
	},
	{
		"plan_name": "Bi-Weekly Pondless",
		"default_frequency": "Bi-Weekly",
		"template": "Pondless Water Feature Maintenance",
		"visit_shape": "Per Feature",
		"seasonal": True,
		"description": "Every-other-week visit for pondless features, with seasonal startup/winterization.",
	},
	{
		"plan_name": "Monthly Interior Fountain",
		"default_frequency": "Monthly",
		"template": "Interior Fountain Maintenance",
		"visit_shape": "Per Feature",
		"invoicing_frequency": "Monthly",
		"seasonal": False,
		"description": "Monthly indoor-fountain care, billed monthly. No seasonal visits (interior).",
	},
	{
		"plan_name": "Monthly Large Display (Per Site)",
		"default_frequency": "Monthly",
		"template": "Large Display Fountain Maintenance",
		"visit_shape": "Per Site Visit",
		"seasonal": True,
		"description": "One monthly site visit covering all features of a large display, with seasonal startup/winterization.",
	},
	{
		"plan_name": "Seasonal Service Only",
		"default_frequency": None,
		"template": None,
		"visit_shape": "Per Feature",
		"seasonal": True,
		"description": "No routine cadence — only the spring startup and fall winterization visits.",
	},
]


def execute():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Section"):
		return

	_seed_sections()
	_seed_templates()
	_seed_plans()


def _seed_sections():
	for section_title, section_type, description, items in SECTIONS:
		if frappe.db.exists("Sapphire Maintenance Section", section_title):
			continue
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = section_title
		section.section_type = section_type
		section.description = description
		for sequence, item in enumerate(items, start=1):
			section.append("items", dict(item, sequence=sequence))
		section.insert(ignore_permissions=True)


def _seed_templates():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Template"):
		return
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


def _seed_plans():
	if not frappe.db.exists("DocType", "Sapphire Service Plan"):
		return

	def template_name_if_exists(template_name):
		if template_name and frappe.db.exists(
			"Sapphire Maintenance Template", {"template_name": template_name}
		):
			return template_name
		return None

	startup_template = template_name_if_exists(FULL_STARTUP)
	winterization_template = template_name_if_exists(FULL_WINTER)

	for spec in PLANS:
		if frappe.db.exists("Sapphire Service Plan", spec["plan_name"]):
			continue
		plan = frappe.new_doc("Sapphire Service Plan")
		plan.plan_name = spec["plan_name"]
		plan.description = spec["description"]
		plan.default_frequency = spec["default_frequency"]
		plan.default_template = template_name_if_exists(spec["template"])
		plan.visit_shape = spec["visit_shape"]
		if spec.get("invoicing_frequency"):
			plan.invoicing_frequency = spec["invoicing_frequency"]
		if spec["seasonal"]:
			plan.seasonal_startup = 1
			plan.startup_month = "April"
			plan.startup_template = startup_template
			plan.winterization = 1
			plan.winterization_month = "October"
			plan.winterization_template = winterization_template
		plan.insert(ignore_permissions=True)
