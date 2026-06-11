"""Seed the standard Sapphire Service Plans.

The plans are the contract form's one-pick presets: choosing one stamps visit
frequency, form template, visit shape and the seasonal startup/winterization
defaults onto a Sapphire Maintenance Contract in a single dropdown selection.

**Insert-only and idempotent** (model: ``seed_maintenance_sections``): a plan
whose name already exists is left untouched, so site-side edits survive
re-migrations. Template links are resolved by template_name at seed time and
silently skipped when the template doesn't exist on the site.
"""

import frappe

PLANS = [
	{
		"plan_name": "Weekly Full Service",
		"default_frequency": "Weekly",
		"template": "Standard Fountain Maintenance",
		"seasonal": True,
		"description": "Weekly visit with the full inspection/chemistry/cleaning/dosing form, plus spring startup and fall winterization.",
	},
	{
		"plan_name": "Bi-Weekly Full Service",
		"default_frequency": "Bi-Weekly",
		"template": "Standard Fountain Maintenance",
		"seasonal": True,
		"description": "Every-other-week visit with the full form, plus spring startup and fall winterization.",
	},
	{
		"plan_name": "Monthly Full Service",
		"default_frequency": "Monthly",
		"template": "Standard Fountain Maintenance",
		"seasonal": True,
		"description": "Monthly visit with the full form, plus spring startup and fall winterization.",
	},
	{
		"plan_name": "Quarterly Inspection Only",
		"default_frequency": "Quarterly",
		"template": "Standard Fountain Maintenance",
		"seasonal": False,
		"description": "Quarterly check-up visit; no seasonal startup or winterization included.",
	},
]


def execute():
	if not frappe.db.exists("DocType", "Sapphire Service Plan"):
		# fresh install ordering safety; doctype sync precedes post_model_sync
		# patches, so this should never trip — belt and suspenders.
		return

	def template_by_name(template_name):
		return frappe.db.get_value(
			"Sapphire Maintenance Template", {"template_name": template_name}, "name"
		)

	startup_template = template_by_name("Seasonal Startup")
	winterization_template = template_by_name("Winterization")

	for spec in PLANS:
		if frappe.db.exists("Sapphire Service Plan", spec["plan_name"]):
			continue
		plan = frappe.new_doc("Sapphire Service Plan")
		plan.plan_name = spec["plan_name"]
		plan.description = spec["description"]
		plan.default_frequency = spec["default_frequency"]
		plan.default_template = template_by_name(spec["template"])
		plan.visit_shape = "Per Feature"
		if spec["seasonal"]:
			plan.seasonal_startup = 1
			plan.startup_month = "April"
			plan.startup_template = startup_template
			plan.winterization = 1
			plan.winterization_month = "October"
			plan.winterization_template = winterization_template
		plan.insert(ignore_permissions=True)
