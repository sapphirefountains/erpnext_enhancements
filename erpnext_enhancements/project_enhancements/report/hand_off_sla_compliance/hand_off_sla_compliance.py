# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Hand-Off SLA Compliance — did the PRO-0204 steps actually happen, and on time?

The 2026-08-06 audit that prompted this report found the process was working
exactly where it was automatic and nowhere else: anchored steps 1/3/5 completed
100% of the time, manual steps 5%, and all 17 pending hand-off meetings were past
SLA. Nothing surfaced that until somebody went looking, which is the real defect.

One row per step per project, plus the two summaries the meeting asked for:

* **On-time %** per role and per step — computed against the step's own
  ``due_by``, i.e. "did you do it within your SLA once it became your turn".
* **The 7-business-day Closed-Won → Launch goal** per project — a completely
  separate clock, measured from the Opportunity being marked won. A step can be
  perfectly on time against the step before it and still blow the launch goal,
  because the goal is about the whole chain, not any one link. Both are shown;
  neither is derivable from the other.

Also counts the steps whose ``due_by`` **never started**. Those are not late —
they are blocked upstream, waiting on a step that has not completed — and rolling
them into "overdue" would overstate lateness while hiding a stall. They get their
own bucket.

Population defaults to work touched since 2026-06-10 (when the tracker went
live); it is a filter, so the window can be moved without a deploy.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, get_datetime, getdate, now_datetime

from erpnext_enhancements.crm_enhancements.handoff import LAUNCH_GOAL_BUSINESS_DAYS
from erpnext_enhancements.process_steps import HANDOFF_STEP_NUMBER, SKIPPED_PREFIX

#: The tracker went live on this date; earlier projects have no meaningful steps.
DEFAULT_FROM_DATE = "2026-06-10"

#: "Hold Project Launch Meeting" — the step the launch goal is measured against.
LAUNCH_STEP_NUMBER = 7


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, get_summary_message(data, filters), None, get_report_summary(data)


def get_columns():
	return [
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Data", "width": 180},
		{"label": _("Step"), "fieldname": "step_number", "fieldtype": "Int", "width": 55},
		{"label": _("Step Title"), "fieldname": "step_title", "fieldtype": "Data", "width": 230},
		{"label": _("Responsible Role"), "fieldname": "responsible_role", "fieldtype": "Data", "width": 150},
		{"label": _("State"), "fieldname": "state", "fieldtype": "Data", "width": 110},
		{"label": _("Due By"), "fieldname": "due_by", "fieldtype": "Datetime", "width": 150},
		{"label": _("Completed On"), "fieldname": "completed_on", "fieldtype": "Datetime", "width": 150},
		{"label": _("Completed By"), "fieldname": "completed_by", "fieldtype": "Link", "options": "User", "width": 160},
		{"label": _("Days Over"), "fieldname": "days_over", "fieldtype": "Int", "width": 90},
		{"label": _("Launch Deadline"), "fieldname": "launch_deadline", "fieldtype": "Datetime", "width": 150},
		{"label": _("Launch Goal"), "fieldname": "launch_goal", "fieldtype": "Data", "width": 130},
		{"label": _("Notes"), "fieldname": "notes", "fieldtype": "Data", "width": 220},
	]


def _projects(filters):
	"""Projects in the reporting window, with their Opportunity-side dates."""
	from_date = filters.get("from_date") or DEFAULT_FROM_DATE
	project_filters = {"modified": (">=", getdate(from_date))}
	if filters.get("project"):
		project_filters = {"name": filters.get("project")}
	if filters.get("project_status"):
		project_filters["status"] = filters.get("project_status")

	projects = frappe.get_all(
		"Project",
		filters=project_filters,
		fields=["name", "project_name", "customer", "status", "custom_opportunity"],
		order_by="modified desc",
	)
	if not projects:
		return {}

	# Bulk-read the Opportunity dates rather than one lookup per project.
	opportunities = [p.custom_opportunity for p in projects if p.custom_opportunity]
	launch = {}
	if opportunities and _has_launch_field():
		for row in frappe.get_all(
			"Opportunity",
			filters={"name": ("in", opportunities)},
			fields=["name", "custom_launch_deadline", "custom_date_closed_won"],
		):
			launch[row.name] = row

	for project in projects:
		project.launch = launch.get(project.custom_opportunity) or frappe._dict()
	return {p.name: p for p in projects}


def _has_launch_field():
	"""The report must not 500 on a bench where the fixtures have not landed."""
	try:
		return frappe.db.has_column("Opportunity", "custom_launch_deadline")
	except Exception:
		return False


def _state(step):
	"""What this row actually is: Completed / Skipped / Overdue / Due / Not Started."""
	if step.status == "Completed":
		if (step.notes or "").startswith(SKIPPED_PREFIX):
			return "Skipped"
		return "Completed"
	if step.status == "Skipped":
		return "Skipped"
	if not step.due_by:
		# Blocked upstream: its clock has not started because the prior step is
		# still open. Not late — stalled. Kept distinct on purpose.
		return "Not Started"
	if get_datetime(step.due_by) < now_datetime():
		return "Overdue"
	return "Due"


def get_data(filters):
	projects = _projects(filters)
	if not projects:
		return []

	steps = frappe.get_all(
		"Project Process Step",
		filters={"parenttype": "Project", "parent": ("in", list(projects))},
		fields=[
			"parent",
			"step_number",
			"step_title",
			"responsible_role",
			"status",
			"due_by",
			"completed_on",
			"completed_by",
			"notes",
		],
		order_by="parent asc, step_number asc",
	)

	wanted_role = filters.get("responsible_role")
	wanted_state = filters.get("state")
	now = now_datetime()

	data = []
	for step in steps:
		project = projects[step.parent]
		state = _state(step)
		if wanted_role and step.responsible_role != wanted_role:
			continue
		if wanted_state and state != wanted_state:
			continue

		if state == "Overdue":
			days_over = max(date_diff(now, get_datetime(step.due_by)), 0)
		elif state == "Completed" and step.due_by and step.completed_on:
			days_over = max(date_diff(get_datetime(step.completed_on), get_datetime(step.due_by)), 0)
		else:
			days_over = 0

		deadline = project.launch.get("custom_launch_deadline")
		data.append(
			{
				"project": step.parent,
				"customer": project.customer or project.project_name,
				"step_number": step.step_number,
				"step_title": step.step_title,
				"responsible_role": step.responsible_role,
				"state": _(state),
				"due_by": step.due_by,
				"completed_on": step.completed_on,
				"completed_by": step.completed_by,
				"days_over": days_over,
				"launch_deadline": deadline if cint(step.step_number) == LAUNCH_STEP_NUMBER else None,
				"launch_goal": _launch_goal(step, deadline)
				if cint(step.step_number) == LAUNCH_STEP_NUMBER
				else None,
				"notes": step.notes,
				# Sort key only; hidden from the columns above.
				"_state_raw": state,
			}
		)
	return data


def _launch_goal(step, deadline):
	"""Met / Missed / At risk against the 7-business-day Closed-Won → Launch goal.

	Deliberately independent of ``days_over``: the step SLA asks "were you quick
	once it was your turn", this asks "did the deal launch in a week". The meeting
	wanted both visible because they disagree, and the disagreement is the signal.
	"""
	if not deadline:
		return _("No won date")
	deadline = get_datetime(deadline)
	if step.status == "Completed" and step.completed_on:
		return _("Met") if get_datetime(step.completed_on) <= deadline else _("Missed")
	return _("Missed") if now_datetime() > deadline else _("On track")


def get_report_summary(data):
	"""The KPI row across the top — the plain summary the meeting asked for."""
	if not data:
		return None

	completed = [row for row in data if row["_state_raw"] in ("Completed", "Skipped")]
	on_time = [row for row in completed if not row["days_over"]]
	overdue = [row for row in data if row["_state_raw"] == "Overdue"]
	blocked = [row for row in data if row["_state_raw"] == "Not Started"]

	launch_rows = [row for row in data if row["launch_goal"]]
	launch_met = [row for row in launch_rows if row["launch_goal"] == _("Met")]

	pct = round(100.0 * len(on_time) / len(completed)) if completed else 0
	launch_pct = round(100.0 * len(launch_met) / len(launch_rows)) if launch_rows else 0

	return [
		{"label": _("Steps Completed"), "value": len(completed), "datatype": "Int"},
		{
			"label": _("On Time"),
			"value": f"{pct}%",
			"datatype": "Data",
			"indicator": "Green" if pct >= 80 else ("Orange" if pct >= 50 else "Red"),
		},
		{
			"label": _("Overdue Now"),
			"value": len(overdue),
			"datatype": "Int",
			"indicator": "Red" if overdue else "Green",
		},
		{"label": _("Blocked Upstream"), "value": len(blocked), "datatype": "Int"},
		{
			"label": _("Launched in {0} Business Days").format(LAUNCH_GOAL_BUSINESS_DAYS),
			"value": f"{launch_pct}%",
			"datatype": "Data",
			"indicator": "Green" if launch_pct >= 80 else ("Orange" if launch_pct >= 50 else "Red"),
		},
	]


def get_summary_message(data, filters):
	"""The prose line under the KPIs: per-role on-time, worst first."""
	if not data:
		return _("No hand-off steps in this window.")

	by_role = {}
	for row in data:
		if row["_state_raw"] not in ("Completed", "Skipped"):
			continue
		bucket = by_role.setdefault(row["responsible_role"] or _("Unassigned"), [0, 0])
		bucket[0] += 1
		if not row["days_over"]:
			bucket[1] += 1

	if not by_role:
		return _("Nothing has been completed in this window yet.")

	parts = []
	for role, (total, on_time) in sorted(
		by_role.items(), key=lambda item: (item[1][1] / item[1][0]) if item[1][0] else 0
	):
		parts.append(f"{frappe.utils.escape_html(role)}: {round(100.0 * on_time / total)}% ({on_time}/{total})")

	pending_handoffs = len(
		[
			row
			for row in data
			if cint(row["step_number"]) == HANDOFF_STEP_NUMBER and row["_state_raw"] == "Overdue"
		]
	)
	tail = (
		_(" — {0} hand-off meeting(s) past SLA.").format(pending_handoffs) if pending_handoffs else ""
	)
	return _("On-time completion by role: {0}{1}").format(" · ".join(parts), tail)
