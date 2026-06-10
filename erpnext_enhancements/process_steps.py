"""The PRO-0204 "Won Opportunity Hand-Off" process engine.

Drives the 7-step hand-off tracker on Projects (the ``custom_process_steps``
child table, rows of **Project Process Step**, definition in **Process Step
Template** records):

* :func:`seed_process_steps` (Project ``before_insert``) — copies the enabled
  templates onto every new Project created from an Opportunity. Anchored
  steps complete retroactively: *Opportunity Won* from the source
  opportunity's ``custom_date_closed_won``, *Project Created* now. Per the
  Jun 9 meeting, in-flight projects are never back-filled automatically —
  they opt in via the "Start Hand-Off Process" form button
  (:func:`start_process`).
* :func:`announce_seeded_steps` (Project ``after_insert``) — tells the first
  pending step's owner their step is up. For a fresh project that is the
  Accounts Receivable rep (Step 3): "the project number exists, start the
  accounting setup" — exactly the hand-off the meeting said kept dropping.
* :func:`sync_process_steps` (Project ``before_save``, after the payment
  stamp from ``status_alerts``) — auto-completes *Payment Received*-anchored
  steps when the Payment Received box is ticked, stamps
  ``completed_on``/``completed_by`` on manual completions, and keeps the
  current step's ``due_by`` set (now + SLA hours when it becomes current).
* :func:`notify_step_transitions` (Project ``on_update``) — when a save
  completed one or more steps, notifies the *new* current step's responsible
  person (SMS + Notification Log via ``status_alerts._deliver``); when the
  last step completes, posts a "process complete" comment instead.
* :func:`escalate_overdue_steps` (daily scheduler) — re-nags the responsible
  person for every active project whose current step is past ``due_by``
  (max once per day per step, ``last_reminded_on``).

Responsibility is role-shaped, resolved per project at notification time
(never stored): **Project Manager** → ``Project.custom_project_owner``
(Employee); **Account Executive** → ``opportunity_owner`` (User) of the
source Opportunity (``custom_opportunity``, with a ``custom_created_project``
reverse lookup as fallback); **Accounts Receivable** → the Employee named in
**ERPNext Enhancements Settings → Hand-Off Process** (``handoff_ar_rep``).
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, get_url_to_form, now_datetime

from erpnext_enhancements.status_alerts import _deliver

ROLE_AE = "Account Executive"
ROLE_AR = "Accounts Receivable"
ROLE_PM = "Project Manager"

ANCHOR_WON = "Opportunity Won"
ANCHOR_CREATED = "Project Created"
ANCHOR_PAYMENT = "Payment Received"

STEPS_FIELD = "custom_process_steps"


def _has_steps_field(doc):
	"""Schema guard: the fixtures that add the child table sync later in the
	same migrate that ships these hooks — never touch a doc that predates them."""
	return bool(doc.meta.get_field(STEPS_FIELD))


def _templates():
	return frappe.get_all(
		"Process Step Template",
		filters={"enabled": 1},
		fields=["step_number", "step_title", "responsible_role", "auto_anchor", "sla_hours", "description"],
		order_by="step_number asc",
	)


def _first_pending(steps):
	pending = [row for row in steps if row.status == "Pending"]
	return min(pending, key=lambda row: cint(row.step_number)) if pending else None


def _refresh_due(doc):
	"""Ensure the current (first pending) step has a due date: now + SLA."""
	current = _first_pending(doc.get(STEPS_FIELD) or [])
	if current and not current.due_by and cint(current.sla_hours) > 0:
		current.due_by = add_to_date(now_datetime(), hours=cint(current.sla_hours))


def _append_steps(doc):
	"""Copy enabled templates onto ``doc``; retro-complete anchored steps.

	Returns True if steps were appended.
	"""
	templates = _templates()
	if not templates:
		return False

	won_on = None
	if doc.get("custom_opportunity"):
		won_on = frappe.db.get_value("Opportunity", doc.custom_opportunity, "custom_date_closed_won")

	now = now_datetime()
	for template in templates:
		row = {
			"step_number": template.step_number,
			"step_title": template.step_title,
			"responsible_role": template.responsible_role,
			"sla_hours": cint(template.sla_hours),
			"auto_anchor": template.auto_anchor or "",
			"status": "Pending",
		}
		if template.auto_anchor == ANCHOR_WON:
			row["status"] = "Completed"
			row["completed_on"] = get_datetime(won_on) if won_on else now
		elif template.auto_anchor == ANCHOR_CREATED:
			row["status"] = "Completed"
			row["completed_on"] = get_datetime(doc.get("creation")) if doc.get("creation") else now
		elif template.auto_anchor == ANCHOR_PAYMENT and cint(doc.get("custom_payment_received")):
			row["status"] = "Completed"
			row["completed_on"] = get_datetime(doc.get("custom_payment_received_on")) if doc.get("custom_payment_received_on") else now
		doc.append(STEPS_FIELD, row)

	_refresh_due(doc)
	return True


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def seed_process_steps(doc, method=None):
	"""Project ``before_insert`` — seed the tracker on opportunity-born projects.

	Also normalizes the source link: a doc arriving with only the legacy
	in-memory ``custom_sales_opportunity`` attribute (pre-v1.3.0 make_project
	mapping, possibly replayed from a stale client) gets it copied onto the
	persisted ``custom_opportunity`` field.
	"""
	if not _has_steps_field(doc) or doc.get(STEPS_FIELD):
		return
	if not doc.get("custom_opportunity") and doc.get("custom_sales_opportunity"):
		doc.custom_opportunity = doc.get("custom_sales_opportunity")
	if not doc.get("custom_opportunity"):
		return
	_append_steps(doc)


def announce_seeded_steps(doc, method=None):
	"""Project ``after_insert`` — tell the first pending step's owner it's up."""
	if not _has_steps_field(doc):
		return
	current = _first_pending(doc.get(STEPS_FIELD) or [])
	if current:
		_enqueue_step_notice(doc.name, "up")


def sync_process_steps(doc, method=None):
	"""Project ``before_save`` — anchors, completion stamps, due dates."""
	if not _has_steps_field(doc):
		return
	steps = doc.get(STEPS_FIELD) or []
	if not steps:
		return

	payment_received = cint(doc.get("custom_payment_received"))
	for row in steps:
		if (
			payment_received
			and row.auto_anchor == ANCHOR_PAYMENT
			and row.status == "Pending"
		):
			row.status = "Completed"
			row.completed_on = get_datetime(doc.get("custom_payment_received_on")) if doc.get("custom_payment_received_on") else now_datetime()
			row.completed_by = frappe.session.user
		if row.status == "Completed":
			if not row.completed_on:
				row.completed_on = now_datetime()
			if not row.completed_by:
				row.completed_by = frappe.session.user

	_refresh_due(doc)


def notify_step_transitions(doc, method=None):
	"""Project ``on_update`` — a step just completed: hand off to the next owner."""
	if not _has_steps_field(doc):
		return
	steps = doc.get(STEPS_FIELD) or []
	before = doc.get_doc_before_save()
	if not steps or before is None:
		return

	before_status = {row.name: row.status for row in before.get(STEPS_FIELD) or []}
	newly_completed = [
		row
		for row in steps
		if row.status == "Completed"
		and row.name in before_status
		and before_status[row.name] != "Completed"
	]
	if not newly_completed:
		return

	current = _first_pending(steps)
	if current:
		_enqueue_step_notice(doc.name, "up")
	else:
		done = sum(1 for row in steps if row.status == "Completed")
		doc.add_comment(
			"Comment",
			_("Hand-off process complete — all {0} steps done (PRO-0204).").format(done),
		)


def _enqueue_step_notice(project, kind):
	frappe.enqueue(
		"erpnext_enhancements.process_steps.deliver_step_notice",
		project=project,
		kind=kind,
		queue="short",
		enqueue_after_commit=True,
	)


# ---------------------------------------------------------------------------
# Delivery / escalation
# ---------------------------------------------------------------------------


def _resolve_responsible(project, role):
	"""Recipient dicts (employee_name / cell_number / user_id) for a role on a project."""
	fields = ["name", "employee_name", "cell_number", "user_id"]

	if role == ROLE_PM:
		if project.get("custom_project_owner"):
			pm = frappe.db.get_value("Employee", project.custom_project_owner, fields, as_dict=True)
			return [pm] if pm else []
		return []

	if role == ROLE_AR:
		ar_rep = frappe.get_single("ERPNext Enhancements Settings").get("handoff_ar_rep")
		if ar_rep:
			ar = frappe.db.get_value("Employee", ar_rep, fields, as_dict=True)
			return [ar] if ar else []
		return []

	if role == ROLE_AE:
		opportunity = project.get("custom_opportunity") or frappe.db.get_value(
			"Opportunity", {"custom_created_project": project.name}, "name"
		)
		ae_user = (
			frappe.db.get_value("Opportunity", opportunity, "opportunity_owner") if opportunity else None
		)
		if not ae_user:
			return []
		ae = frappe.db.get_value("Employee", {"user_id": ae_user, "status": "Active"}, fields, as_dict=True)
		return [ae or frappe._dict(employee_name=ae_user, cell_number=None, user_id=ae_user)]

	return []


def deliver_step_notice(project, kind="up", step_name=None):
	"""Background job: notify the responsible person for a step.

	``kind="up"`` targets the project's current (first pending) step;
	``kind="overdue"`` targets the specific child row ``step_name``.
	"""
	doc = frappe.db.get_value(
		"Project",
		project,
		["name", "project_name", "custom_project_owner", "custom_opportunity"],
		as_dict=True,
	)
	if not doc:
		return

	steps = frappe.get_all(
		"Project Process Step",
		filters={"parent": project, "parenttype": "Project"},
		fields=["name", "step_number", "step_title", "responsible_role", "status", "due_by"],
		order_by="step_number asc",
	)
	if kind == "overdue" and step_name:
		step = next((row for row in steps if row.name == step_name), None)
	else:
		step = _first_pending(steps)
	if not step or step.status != "Pending":
		return

	recipients = _resolve_responsible(doc, step.responsible_role)
	if not recipients:
		return

	label = doc.project_name or doc.name
	total = len(steps)
	link = get_url_to_form("Project", doc.name)
	if kind == "overdue":
		message = (
			f"OVERDUE on {label}: step {step.step_number}/{total} - {step.step_title} "
			f"(due {frappe.utils.format_datetime(step.due_by)}).\n{link}"
		)
		subject = _("Overdue hand-off step on {0}: {1}").format(label, step.step_title)
	else:
		due = f" Due {frappe.utils.format_datetime(step.due_by)}." if step.due_by else ""
		message = f"Your step on {label}: {step.step_number}/{total} - {step.step_title}.{due}\n{link}"
		subject = _("Hand-off step {0} of {1} is up on {2}: {3}").format(
			step.step_number, total, label, step.step_title
		)

	_deliver(recipients, message, subject=subject, reference_doctype="Project", reference_docname=doc.name)


def escalate_overdue_steps():
	"""Daily scheduler — re-nag the current step's owner once it's past due.

	Only the *current* step of each active project escalates (later pending
	steps aren't actionable yet), at most once per day per step.
	"""
	now = now_datetime()
	rows = frappe.get_all(
		"Project Process Step",
		filters={"parenttype": "Project", "status": "Pending"},
		fields=["name", "parent", "step_number", "due_by", "last_reminded_on"],
		order_by="parent asc, step_number asc",
	)
	if not rows:
		return

	active = set(
		frappe.get_all(
			"Project",
			filters={"name": ("in", list({row.parent for row in rows})), "status": "Active"},
			pluck="name",
		)
	)

	current_by_project = {}
	for row in rows:
		if row.parent in active and row.parent not in current_by_project:
			current_by_project[row.parent] = row

	for project, row in current_by_project.items():
		if not row.due_by or get_datetime(row.due_by) > now:
			continue
		if row.last_reminded_on and get_datetime(row.last_reminded_on).date() == now.date():
			continue
		try:
			deliver_step_notice(project, kind="overdue", step_name=row.name)
			frappe.db.set_value(
				"Project Process Step", row.name, "last_reminded_on", now, update_modified=False
			)
		except Exception:
			frappe.log_error(
				f"Escalation for {project} step {row.name} failed:\n{frappe.get_traceback()}",
				"Process Step Escalation Error",
			)


# ---------------------------------------------------------------------------
# Manual kick-off for in-flight projects
# ---------------------------------------------------------------------------


@frappe.whitelist()
def start_process(project):
	"""Seed the hand-off tracker on an existing Project (form button).

	Per the Jun 9 meeting, in-flight projects are not back-filled
	automatically — this is their explicit opt-in. Anchored steps complete
	retroactively (won date, project creation, payment if already received).
	"""
	doc = frappe.get_doc("Project", project)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Project."), frappe.PermissionError)
	if not _has_steps_field(doc):
		frappe.throw(_("The process steps field is not available yet — run migrations first."))
	if doc.get(STEPS_FIELD):
		frappe.throw(_("This project already has a hand-off process."))

	if not _append_steps(doc):
		frappe.throw(_("No enabled Process Step Template records found."))
	doc.save()
	announce_seeded_steps(doc)
	return {"steps": len(doc.get(STEPS_FIELD))}
