# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Migrate-time provisioning for Training — starter categories.

Insert-only and idempotent: a category an admin renamed or deleted stays that
way. The point is only that the builder's category picker is never empty on a
fresh site, because an empty picker reads as a broken form.
"""

import frappe

# Chosen from what this business actually trains on — fountain service, water
# chemistry and the safety that goes with both — rather than generic LMS filler.
STARTER_CATEGORIES = (
	("Safety", "Site safety, PPE, confined space, electrical and chemical handling."),
	("Water Chemistry", "Treatment, dosing, testing and balancing."),
	("Service & Maintenance", "Routine visits, troubleshooting and repairs."),
	("Installation", "Build, plumbing and commissioning."),
	("Systems & Admin", "ERPNext, timekeeping and internal process."),
	("Customer Handover", "What customers are shown about operating their fountain."),
)


def ensure_training_categories():
	"""Create any starter category that does not already exist."""
	if frappe.flags.in_test:
		return
	for name, description in STARTER_CATEGORIES:
		if frappe.db.exists("Training Category", name):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Training Category",
					"category_name": name,
					"description": description,
				}
			).insert(ignore_permissions=True)
		except Exception:
			# A failure here must never abort a migrate — the categories are a
			# convenience, and an author can always type their own.
			frappe.log_error(
				f"Could not seed Training Category {name}\n{frappe.get_traceback()}",
				"Training setup",
			)
