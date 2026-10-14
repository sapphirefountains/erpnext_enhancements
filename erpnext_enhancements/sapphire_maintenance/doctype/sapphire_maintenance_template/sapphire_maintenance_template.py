"""Controller for the Sapphire Maintenance Template doctype.

A maintenance form definition for field-service visits, scoped to a Customer
and/or Project with a ``status`` (Draft / Active / Disabled). Since the modular
forms rework the template is *composed* of reusable Sapphire Maintenance
Sections (the ``sections`` child table) rather than owning its own question
rows — the same Chemical Dosing / Water Chemistry / Equipment Inspection /
Cleaning Tasks sections are shared across all templates.

When a Maintenance Record is created, the template is resolved (contract
feature row -> contract default -> Active template for the record's Project or
Customer) and its sections are instantiated into the record's typed child
tables (see ``sapphire_maintenance_record.get_visit_payload``).
"""

import frappe
from frappe import _
from frappe.model.document import Document


class SapphireMaintenanceTemplate(Document):
	pass


@frappe.whitelist()
def preview_day(template, day):
	"""The checklist a visit on ``day`` (a weekday's full name) gets from this template (v1.678.0).

	For the form's **Preview a day** button: each section's lines, in the visit's order, with
	those set to other weekdays (a line's On Days) marked off. It reads the saved template, the
	same sections ``get_visit_payload`` walks.

	Returns:
		dict: {day, sections: [{title, type, lines: [{label, days, on, mandatory}]}], on, off,
		site_wide}.
	"""
	from erpnext_enhancements.sapphire_maintenance.visit_days import DAY_LONG, scheduled_on_weekday

	doc = frappe.get_doc("Sapphire Maintenance Template", template)
	doc.check_permission("read")
	if day not in DAY_LONG:
		frappe.throw(_("Choose a day of the week."))
	weekday = DAY_LONG.index(day)
	sections = []
	on = off = 0
	for row in doc.sections:
		if not row.section:
			continue
		section = frappe.get_doc("Sapphire Maintenance Section", row.section)
		if section.disabled:
			continue
		lines = []
		for item in sorted(section.items, key=lambda line: line.sequence or line.idx):
			today = scheduled_on_weekday(item.get("days"), weekday)
			on += 1 if today else 0
			off += 0 if today else 1
			lines.append(
				{
					# A Chemical Dosing line may have only its item.
					"label": item.label or item.get("item") or "",
					"days": item.get("days") or "",
					"on": today,
					"mandatory": item.is_mandatory,
				}
			)
		sections.append({"title": section.section_title, "type": section.section_type, "lines": lines})
	return {"day": day, "sections": sections, "on": on, "off": off, "site_wide": doc.get("site_wide")}
