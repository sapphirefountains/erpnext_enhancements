"""Reorder the PRO-0204 hand-off so the hand-off meeting leads project creation.

v1.66.0 changes the sequence to Mark Won (1) -> Hold Hand-Off Meeting (2) ->
Create Project (3) -> Create Accounting Project (4) -> ... (5-7 unchanged), so
the internal hand-off happens before the project is stood up. Renumbers the
affected Process Step Templates and existing Project Process Step rows by title
(idempotent; running twice sets the same numbers). Steps not listed keep their
numbers. Title-edited rows on a site simply aren't matched (left as-is).
"""

import frappe

# Canonical step title -> new step_number for the steps that move.
NEW_NUMBERS = {
	"Hold Hand-Off Meeting": 2,
	"Create Project in PM System": 3,
	"Create Accounting Project & Send Invoice": 4,
}


def execute():
	for doctype in ("Process Step Template", "Project Process Step"):
		if not frappe.db.has_column(doctype, "step_number"):
			continue
		for title, number in NEW_NUMBERS.items():
			for name in frappe.get_all(doctype, filters={"step_title": title}, pluck="name"):
				frappe.db.set_value(doctype, name, "step_number", number, update_modified=False)
