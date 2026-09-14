"""Seed the Commissioning inspection section, and the Build template that carries it.

v1.449.0, WI-075 sub-phase C.

``docs/KPI_DASHBOARD_DESIGN.md`` calls this **"the biggest fountain-specific gap"**, and states
the consequence plainly: leak test, flow verification, electrical/GFCI check and nozzle-pattern
sign-off *"are not recorded anywhere, so first-pass yield and build quality are invisible."* For
a water feature, commissioning is the moment of truth — leaks, wrong flow, tripping GFCIs or
off-spec nozzle patterns all mean tear-back and re-test, and the rate at which that happens is
the purest quality signal the business has.

The six checks below are that document's own proposed ``test_type`` list — Fill, Leak,
Flow (GPM), Electrical/GFCI, Nozzle Pattern, Light Function — turned into a section. Once they
are being recorded, KPI #10 First-Pass Yield stops being Manual and becomes a query.

What this patch deliberately does **not** seed
-----------------------------------------------

The other Build milestones — pre-pour, in-process fabrication, final walkthrough — get their
milestone rows from ``seed_inspection_milestones`` and **no template**. Their checklists are
Sapphire's own standard of care, and nobody has written them down yet.

Seeding plausible-sounding invented checks would be worse than seeding nothing: a checklist
carries the authority of the company that issued it, an inspector works through it assuming
somebody chose those items on purpose, and an invented one would be indistinguishable from a
real one right up until it failed to catch something. So those milestones stand empty and
visible, and whoever runs Build fills them in through the Desk.

Commissioning is here because the list is not invented — it is written down in this repo, by
somebody who knew the trade.

Insert-only and idempotent, keyed on the section title and the template name.
"""

import frappe

from erpnext_enhancements.quality.catalog import (
	COMMISSIONING_CHECKS,
	COMMISSIONING_INSTRUCTIONS,
	COMMISSIONING_MILESTONE_KEY,
	COMMISSIONING_SECTION_TITLE,
	COMMISSIONING_TEMPLATE_NAME,
)

SECTION_TITLE = COMMISSIONING_SECTION_TITLE
TEMPLATE_NAME = COMMISSIONING_TEMPLATE_NAME
MILESTONE_KEY = COMMISSIONING_MILESTONE_KEY
INSTRUCTIONS = COMMISSIONING_INSTRUCTIONS

def execute() -> None:
	for doctype in ("Inspection Section", "Project Inspection Template", "Inspection Milestone"):
		if not frappe.db.exists("DocType", doctype):
			return

	section = _ensure_section()
	template = _ensure_template(section)
	frappe.logger().info(
		f"seed_commissioning_section: section={section or 'existing'} template={template or 'existing'}"
	)


def _ensure_section():
	if frappe.db.exists("Inspection Section", SECTION_TITLE):
		return None

	doc = frappe.new_doc("Inspection Section")
	doc.update(
		{
			"section_title": SECTION_TITLE,
			"section_type": "Functional Test",
			"description": (
				"Start-up verification for a water feature. The measure of first-pass yield."
			),
			"instructions": INSTRUCTIONS,
		}
	)
	for label, criteria, check_type, method, uom, photo in COMMISSIONING_CHECKS:
		doc.append(
			"items",
			{
				"label": label,
				"acceptance_criteria": criteria,
				"check_type": check_type,
				"method": method,
				# A UOM the site has not got would make the row unsaveable, and a missing unit
				# is a far smaller problem than a check nobody can record.
				"uom": uom if uom and frappe.db.exists("UOM", uom) else None,
				"is_mandatory": 1,
				"requires_photo": photo,
			},
		)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_template(section_created):
	if frappe.db.exists("Project Inspection Template", TEMPLATE_NAME):
		return None

	milestone = frappe.db.get_value(
		"Inspection Milestone", {"milestone_key": MILESTONE_KEY}, "name"
	)
	if not milestone:
		# The milestone seed skips a project type the site has not got. Without the milestone
		# there is nothing to hang this template on, and inventing one here would put a row in
		# the catalog that the milestone patch did not choose to create.
		frappe.logger().info(
			f"seed_commissioning_section: milestone {MILESTONE_KEY!r} absent, template not created"
		)
		return None

	doc = frappe.new_doc("Project Inspection Template")
	doc.update(
		{
			"template_name": TEMPLATE_NAME,
			"milestone": milestone,
			# Active rather than Draft: the checks are real and the section is the only one
			# this template has. An empty Active template would be the thing to avoid.
			"status": "Active",
			"safety_instructions": (
				"<p>Water and live electrical in the same place. Confirm the GFCI protection is "
				"in service <b>before</b> the first fill, not after.</p>"
			),
		}
	)
	doc.append("sections", {"section": SECTION_TITLE})
	doc.insert(ignore_permissions=True)
	return doc.name
