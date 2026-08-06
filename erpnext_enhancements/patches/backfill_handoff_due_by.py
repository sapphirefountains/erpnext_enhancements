"""Backfill hand-off due dates from the won date (v1.250.0).

Two populations, both invisible until somebody goes looking, which is how 17 of
17 pending hand-off meetings ended up past SLA with nothing reporting it:

**1. Pending step-2 rows on existing Projects.** ``due_by`` is only stamped when
a step becomes *current*, so hand-off rows seeded before the SLA existed carry a
blank one. A blank ``due_by`` is not "on time" — it is invisible: the escalation
sweep skips it and the compliance report counts it as never-started. Backfilled
to two business days from the source Opportunity's ``custom_date_closed_won``,
which is the date the SLA has always been measured from.

**2. Opportunities the transition hook will never fire for.** The launch
deadline (``custom_launch_deadline``) is stamped on the transition into Closed
Won. Deals already won will not transition again, so those that *do* carry a won
date get theirs computed here — the 7-business-day launch metric would otherwise
read "no won date" for the entire back catalogue and the report would open blank.

Fill-blanks-only in both cases: a row somebody has already dated is left exactly
as it is. ``custom_handoff_due_by`` is deliberately NOT backfilled onto the
legacy backlog — those Opportunities are exempt from the gate
(``custom_handoff_gate_applies = 0``, see ``ensure_handoff_gate_fields``), and
giving them a due date would start a daily escalation for 227 meetings the team
has explicitly decided not to hold.
"""

import frappe

from erpnext_enhancements.crm_enhancements.handoff import (
	HANDOFF_SLA_BUSINESS_DAYS,
	LAUNCH_GOAL_BUSINESS_DAYS,
)
from erpnext_enhancements.process_steps import HANDOFF_STEP_NUMBER
from erpnext_enhancements.utils.working_days import add_working_days


def execute():
	holiday_list = frappe.db.get_single_value(
		"ERPNext Enhancements Settings", "handoff_holiday_list"
	)
	_backfill_step_due_by(holiday_list)
	_backfill_launch_deadlines(holiday_list)


def _backfill_step_due_by(holiday_list):
	if not frappe.db.exists("DocType", "Project Process Step"):
		return
	if not frappe.db.has_column("Opportunity", "custom_date_closed_won"):
		return

	rows = frappe.db.sql(
		"""
		SELECT step.name AS step, opp.custom_date_closed_won AS won_on
		FROM `tabProject Process Step` step
		INNER JOIN `tabProject` project ON project.name = step.parent
		INNER JOIN `tabOpportunity` opp ON opp.name = project.custom_opportunity
		WHERE step.parenttype = 'Project'
		  AND step.status = 'Pending'
		  AND step.step_number = %(step_number)s
		  AND COALESCE(step.due_by, '') = ''
		  AND COALESCE(opp.custom_date_closed_won, '') != ''
		""",
		{"step_number": HANDOFF_STEP_NUMBER},
		as_dict=True,
	)

	for row in rows:
		due_by = add_working_days(
			frappe.utils.get_datetime(frappe.utils.getdate(row.won_on)),
			HANDOFF_SLA_BUSINESS_DAYS,
			holiday_list=holiday_list,
		)
		# update_modified=False: back-dating a due date is bookkeeping, and
		# touching `modified` would drag every one of these projects to the top
		# of every recently-modified list on the site.
		frappe.db.set_value(
			"Project Process Step", row.step, "due_by", due_by, update_modified=False
		)

	if rows:
		frappe.log_error(
			f"Backfilled due_by on {len(rows)} pending hand-off step rows.",
			"Hand-off SLA backfill",
		)


def _backfill_launch_deadlines(holiday_list):
	if not frappe.db.has_column("Opportunity", "custom_launch_deadline"):
		return

	rows = frappe.get_all(
		"Opportunity",
		filters={
			"status": "Closed Won",
			"custom_date_closed_won": ("is", "set"),
			"custom_launch_deadline": ("is", "not set"),
		},
		fields=["name", "custom_date_closed_won"],
	)
	for row in rows:
		deadline = add_working_days(
			frappe.utils.get_datetime(frappe.utils.getdate(row.custom_date_closed_won)),
			LAUNCH_GOAL_BUSINESS_DAYS,
			holiday_list=holiday_list,
		)
		frappe.db.set_value(
			"Opportunity", row.name, "custom_launch_deadline", deadline, update_modified=False
		)
