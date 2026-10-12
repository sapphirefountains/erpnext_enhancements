"""Date the visits already waiting for review with no visit date (v1.594.1).

From v1.594.1 a maintenance visit gets its ``visit_date`` when the technician sends it for
review, if it has none (``SapphireMaintenanceRecord._finish_on_review``). Visits already in
Pending Review when that shipped went for review without one, so their approval would still
roll the contract's next visit forward from the approval day. On 2026-10-12 that was one
visit, Sequoia (4ltqt3v803).

Each gets the day it went for review: the newest "Pending Review" Workflow comment that
``apply_workflow`` leaves on the timeline, else the day it was last changed. That is the day
the new rule would have given it.

**Only Pending Review drafts with a blank date.** A date anybody set is kept, a visit sent
back to Draft gets its date when it is sent again, and submitted visits are left alone: their
schedule was already rolled forward, and changing the date now would not roll it again.

Written with ``set_value(..., update_modified=False)``: no controller runs, and a reviewer with
the visit open on the Visit Review page is not told it changed. Never raises: a patch that
raises aborts ``bench migrate``, which on this repo is the deploy (CLAUDE.md).
"""

import frappe
from frappe.utils import getdate

DOCTYPE = "Sapphire Maintenance Record"
REVIEW_STATE = "Pending Review"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	try:
		waiting = frappe.get_all(
			DOCTYPE,
			filters={"docstatus": 0, "workflow_state": REVIEW_STATE, "visit_date": ["is", "not set"]},
			fields=["name", "modified"],
		)
		for row in waiting:
			went = frappe.db.get_value(
				"Comment",
				{
					"reference_doctype": DOCTYPE,
					"reference_name": row.name,
					"comment_type": "Workflow",
					"content": REVIEW_STATE,
				},
				"creation",
				order_by="creation desc",
			)
			day = getdate(went or row.modified)
			frappe.db.set_value(DOCTYPE, row.name, "visit_date", day, update_modified=False)
			print(f"stamp_visit_date_on_pending_review: {row.name} dated {day}")
	except Exception:
		frappe.log_error(title="stamp_visit_date_on_pending_review", message=frappe.get_traceback())
