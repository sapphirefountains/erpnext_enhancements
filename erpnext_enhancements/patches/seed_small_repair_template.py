"""Seed the "Small Repair" visit template and its sections (Nikolas, 2026-11-03).

The fourth service type of a Service Call. The other three use the generic templates
``seed_maintenance_sections`` made (Seasonal Startup, Winterization, Standard Fountain
Maintenance); a small repair needed its own form: what was done, how long it took, the parts used
from stock, and photos before and after.

Three new Sections (``SECTIONS``), each reusable in any template:

* **Small Repair - Work Performed** (Equipment Inspection): the work done, with its notes and a
  photo before and after. The *Work performed* line is mandatory, so a repair cannot be submitted
  without saying what was done.
* **Small Repair - Labor** (Water Chemistry): one reading, *Labor hours*. The invoice builder
  (``service_calls.billable_hours``) bills this figure, falling back to the clock.
* **Small Repair - Parts Used** (Chemical Dosing): no preset lines. The technician adds the parts
  from stock on the wizard's Chemicals step (*Add another item*), and the invoice builder bills
  them at the Item's price.

The template puts the catalog's **Safety & Electrical** section first when it exists.

Insert-only and idempotent, like the other catalog seeds: a Section or Template whose name exists
is left alone, so site-side edits survive. Cannot raise.
"""

import frappe

from erpnext_enhancements.sapphire_maintenance import service_calls

LOG_TITLE = "Service calls: Small Repair template"
SAFETY_SECTION = "Safety & Electrical"

SECTIONS = [
	(
		service_calls.WORK_SECTION,
		"Equipment Inspection",
		"What was done on a small repair, with a photo before and after.",
		[
			{"label": "Work performed", "options": "Completed\nPartly done\nNot done", "is_mandatory": 1},
			{"label": "Fault found", "options": "Pump\nValve\nPlumbing\nElectrical\nLighting\nControls\nOther"},
			{"label": "Before photo"},
			{"label": "After photo"},
			{"label": "Tested after repair", "options": "Pass\nFail\nNot tested"},
		],
	),
	(
		service_calls.LABOR_SECTION,
		"Water Chemistry",
		"Hours on site, for the invoice. Leave 0 to bill the clock instead.",
		[{"label": service_calls.LABOR_READING, "uom": "hours", "min_value": 0, "max_value": 24}],
	),
	(
		service_calls.PARTS_SECTION,
		"Chemical Dosing",
		"Parts used from stock. Add each one on site; they bill at the Item's price.",
		[],
	),
]
INSTRUCTIONS = {
	service_calls.WORK_SECTION: (
		"<p>Describe the repair in the notes of <b>Work performed</b>: what was wrong, what you did, "
		"and anything the client should know. Take the <b>Before photo</b> before touching anything "
		"and the <b>After photo</b> when the feature is running again.</p>"
	),
	service_calls.LABOR_SECTION: "<p>Hours on site, to the quarter hour. The office bills this figure.</p>",
	service_calls.PARTS_SECTION: (
		"<p>Add every part you fitted with <b>Add another item</b>, so stock and the invoice agree.</p>"
	),
}
TEMPLATE_SAFETY = (
	"<p>Walk the site before starting: check for trip hazards, confirm GFCI protection, and lock out "
	"the pump before opening any equipment.</p>"
)
TEMPLATE_WRAPUP = (
	"<p>Run the feature and watch it for a few minutes: water level correct, no leaks, no noise. "
	"Photograph it running, tidy the equipment area and tell the client what was done.</p>"
)


def execute():
	try:
		if not frappe.db.exists("DocType", "Sapphire Maintenance Section"):
			return
		_seed_sections()
		_seed_template()
	except Exception:
		frappe.log_error(
			title=LOG_TITLE,
			message="The Small Repair template was not seeded. Re-run this patch with bench execute.\n\n"
			+ frappe.get_traceback(),
		)


def _seed_sections():
	for section_title, section_type, description, items in SECTIONS:
		if frappe.db.exists("Sapphire Maintenance Section", section_title):
			continue
		section = frappe.new_doc("Sapphire Maintenance Section")
		section.section_title = section_title
		section.section_type = section_type
		section.description = description
		section.step_instructions = INSTRUCTIONS.get(section_title)
		for sequence, item in enumerate(items, start=1):
			section.append("items", dict(item, sequence=sequence))
		section.insert(ignore_permissions=True)


def _seed_template():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Template"):
		return
	name = service_calls.SMALL_REPAIR_TEMPLATE
	if frappe.db.exists("Sapphire Maintenance Template", {"template_name": name}):
		return
	template = frappe.new_doc("Sapphire Maintenance Template")
	template.template_name = name
	template.status = "Active"
	template.safety_instructions = TEMPLATE_SAFETY
	template.wrapup_instructions = TEMPLATE_WRAPUP
	for section_name in (SAFETY_SECTION, service_calls.WORK_SECTION, service_calls.LABOR_SECTION, service_calls.PARTS_SECTION):
		if frappe.db.exists("Sapphire Maintenance Section", section_name):
			template.append("sections", {"section": section_name})
	if template.sections:
		template.insert(ignore_permissions=True)
