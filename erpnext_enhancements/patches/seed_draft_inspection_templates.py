"""Seed the strawman inspection checklists — **as Drafts, which can generate nothing**.

v1.457.0, WI-075 sub-phase I follow-up.

Sub-phases C and I both declined to seed these, and the reasoning has not changed: a checklist
carries the authority of the company that issued it, and an invented one is indistinguishable
from a real one right up until it fails to catch something.

What changed is that Nik asked for strawmen to correct rather than a blank page. The compromise
that keeps the reasoning intact is the ``status`` field that already exists:

**Every template here is created ``Draft``, and a Draft template generates nothing.**
``api/quality_inspection.generate_inspection`` refuses a non-Active template, and
``quality/scheduling._active_template_keys`` counts only Active ones — so until a person opens a
template, reads it, and sets it Active, the milestone keeps reporting *due and blocked* exactly
as it did before this patch ran. **Setting it Active is the act of adopting it**, and it is a
deliberate act by a named person. Nobody can be handed one of these by accident.

Three of the four sets are not invented — they are lifted from what this company has already
written down elsewhere in this system, and every item records where in ``reference_standard``.
See :mod:`erpnext_enhancements.quality.draft_catalog` for the provenance in full. Events is the
exception, and its sections say so in their own description.

Also creates five UOMs — pH, ppm, mV, Volt, PSI — which ERPNext does not ship and the
measurement checks need. Without a unit a Measurement check cannot have its bounds compared,
which turns a measured reading back into an opinion. Insert-only.

Insert-only and idempotent throughout, keyed on section title and template name. Running it
twice creates nothing and overwrites nothing — in particular it will **not** reset a template
somebody has already corrected and set Active.
"""

import frappe

from erpnext_enhancements.quality.draft_catalog import (
	DRAFT_NOTICE,
	DRAFT_SECTIONS,
	DRAFT_TEMPLATES,
	REQUIRED_UOMS,
)

REQUIRED_DOCTYPES = ("Inspection Section", "Project Inspection Template", "Inspection Milestone")


def execute() -> None:
	for doctype in REQUIRED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			return

	uoms = _ensure_uoms()
	sections = [title for title in (_ensure_section(row) for row in DRAFT_SECTIONS) if title]
	templates = [name for name in (_ensure_template(row) for row in DRAFT_TEMPLATES) if name]

	frappe.logger().info(
		f"seed_draft_inspection_templates: {len(uoms)} uoms, {len(sections)} sections, "
		f"{len(templates)} templates created (all templates Draft; a person must set one "
		f"Active before it can be used)"
	)


def _ensure_uoms():
	created = []
	for uom in REQUIRED_UOMS:
		if frappe.db.exists("UOM", uom):
			continue
		doc = frappe.new_doc("UOM")
		doc.update({"uom_name": uom, "must_be_whole_number": 0, "enabled": 1})
		doc.insert(ignore_permissions=True)
		created.append(uom)
	return created


def _ensure_section(row):
	title, section_type, description, instructions, items = row
	if frappe.db.exists("Inspection Section", title):
		return None

	doc = frappe.new_doc("Inspection Section")
	doc.update(
		{
			"section_title": title,
			"section_type": section_type,
			# The notice leads, because it is the sentence somebody reads when they open this
			# in the Desk and it is the only thing standing between a strawman and being
			# mistaken for the company's standard of care.
			"description": f"{DRAFT_NOTICE} {description}",
			"instructions": instructions,
		}
	)
	for item in items:
		(
			label,
			criteria,
			check_type,
			method,
			uom,
			minimum,
			maximum,
			mandatory,
			photo,
			reference,
		) = item
		doc.append(
			"items",
			{
				"label": label,
				"acceptance_criteria": criteria,
				"check_type": check_type,
				"method": method,
				# A UOM the site has not got would make the row unsaveable, and a missing unit
				# is a far smaller problem than a check nobody can record. _ensure_uoms runs
				# first, so this only bites if that failed.
				"uom": uom if uom and frappe.db.exists("UOM", uom) else None,
				"min_value": minimum or 0,
				"max_value": maximum or 0,
				"is_mandatory": mandatory,
				"requires_photo": photo,
				"reference_standard": reference,
			},
		)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_template(row):
	name, project_type, milestone_key, section_titles, safety, wrapup = row
	if frappe.db.exists("Project Inspection Template", name):
		return None

	milestone = frappe.db.get_value("Inspection Milestone", {"milestone_key": milestone_key}, "name")
	if not milestone:
		# The milestone seed skips a project type the site has not got. Without the milestone
		# there is nothing to hang this on, and inventing one here would put a row in the
		# catalog the milestone patch deliberately did not create.
		frappe.logger().info(
			f"seed_draft_inspection_templates: milestone {milestone_key!r} absent, {name!r} skipped"
		)
		return None

	present = [title for title in section_titles if frappe.db.exists("Inspection Section", title)]
	if not present:
		# An Active template with no sections generates an empty inspection, which reads as a
		# clean pass. A Draft one cannot generate anything, but it would still be a template
		# somebody could set Active by mistake, so it is not created at all.
		frappe.logger().info(
			f"seed_draft_inspection_templates: no sections exist for {name!r}, skipped"
		)
		return None

	doc = frappe.new_doc("Project Inspection Template")
	doc.update(
		{
			"template_name": name,
			"project_type": project_type if frappe.db.exists("Project Type", project_type) else None,
			"milestone": milestone,
			# DRAFT. This is the whole safety property of this patch -- see the module
			# docstring. Do not change it to Active here; a person setting it Active in the
			# Desk is what adoption means.
			"status": "Draft",
			"safety_instructions": safety,
			"wrapup_instructions": wrapup,
		}
	)
	for title in present:
		doc.append("sections", {"section": title})
	doc.insert(ignore_permissions=True)
	return doc.name
