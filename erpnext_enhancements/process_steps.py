"""The PRO-0204 "Won Opportunity Hand-Off" process engine.

Drives the 7-step hand-off tracker on Projects (the ``custom_process_steps``
child table, rows of **Project Process Step**, definition in **Process Step
Template** records):

* :func:`enforce_handoff_gate` (Project ``before_insert``, registered FIRST) —
  refuses to create a Project from a Closed-Won Opportunity whose hand-off
  meeting has not been recorded. Added by the 2026-08-06 process meeting, which
  reverted the June decision allowing project-first creation: step 2 now lives
  on the Opportunity, *in front of* project creation, because a gate downstream
  of the thing it gates is not a gate. See
  :mod:`erpnext_enhancements.crm_enhancements.handoff`.
* :func:`seed_process_steps` (Project ``before_insert``) — copies the enabled
  templates onto every new Project created from an Opportunity. Anchored
  steps complete retroactively: *Opportunity Won* from the source
  opportunity's ``custom_date_closed_won``, *Project Created* now. Step 2
  carries across its real who/when from the Opportunity, so project-side
  reporting measures the hand-off that actually happened rather than stamping
  it complete at project-creation time. Per the Jun 9 meeting, in-flight
  projects are never back-filled automatically — they opt in via the "Start
  Hand-Off Process" form button (:func:`start_process`).
* :func:`complete_step` (whitelisted) — the only supported way to tick a manual
  step. Timestamps are server-generated and permission is the step's own
  responsible role, because the audit found retroactive box-checking.
* :func:`announce_seeded_steps` (Project ``after_insert``) — tells the first
  pending step's owner their step is up. As of the v1.66.0 reorder that is the
  Account Executive holding the hand-off meeting (Step 2): the internal hand-off
  now leads, before the downstream accounting setup the meeting said kept
  dropping.
* :func:`_actionable_steps` — which Pending steps are workable right now. Steps
  used to have exactly one "current" step (the lowest-numbered Pending row);
  now step 5 (Payment) is exempted from *blocking* what comes after it, so 6
  and 7 can become actionable — and get notified, due-dated and escalated —
  while payment is still outstanding (2026-08-07 decision: customer payment
  can take weeks, and neither step is money-dependent). See
  ``NON_BLOCKING_STEP_NUMBERS``.
* :func:`sync_process_steps` (Project ``before_save``, after the payment
  stamp from ``status_alerts``) — auto-completes *Payment Received*-anchored
  steps when the Payment Received box is ticked, stamps
  ``completed_on``/``completed_by`` on manual completions, and keeps every
  actionable step's ``due_by`` set (now + SLA hours when it becomes
  actionable).
* :func:`notify_step_transitions` (Project ``on_update``) — when a save
  completed one or more steps, notifies whichever step(s) *newly* became
  actionable (SMS + Notification Log via ``status_alerts._deliver``); when
  nothing is left pending, posts a "process complete" comment instead.
* :func:`escalate_overdue_steps` (daily scheduler) — re-nags the responsible
  person **and their manager** for every actionable step of every active
  project that is past ``due_by`` (max once per day per step,
  ``last_reminded_on``), by email as well as in-app. Step 5 itself is
  excluded — customer payment timing is not ours to fix, so nagging about it
  would just teach people to filter these emails.
  :func:`escalate_overdue_handoffs` does the same for step 2 while it still
  lives on the Opportunity and has no project row to be found by.
* :func:`send_weekly_sla_digest` (Friday cron) — the compliance summary behind
  the "Hand-Off SLA Compliance" report.

Responsibility is role-shaped, resolved per project at notification time
(never stored): **Project Manager** → ``Project.custom_project_owner``
(Employee); **Account Executive** → ``opportunity_owner`` (User) of the
source Opportunity (``custom_opportunity``, with a ``custom_created_project``
reverse lookup as fallback); **Finance & Accounting Manager** → the Employee
named in **ERPNext Enhancements Settings → Hand-Off Process**
(``handoff_ar_rep``; the fieldname keeps its old spelling — renaming a Single's
field would orphan the configured value for no gain, so only the label moved).
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_url_to_form, now_datetime

from erpnext_enhancements.feature_flags import (
	handoff_gate_enabled,
	process_automation_enabled,
	throw_if_process_automation_disabled,
)
from erpnext_enhancements.status_alerts import _deliver
from erpnext_enhancements.utils.working_days import add_working_days

ROLE_AE = "Account Executive"
#: Renamed from "Accounts Receivable" in v1.251.0 to match what the role is
#: actually called. This string is STORED on every Process Step Template and
#: Project Process Step row, so changing it needs the data migrated with it —
#: see ``patches/rename_handoff_ar_role``. A Select whose stored value is no
#: longer in its options renders blank on the form and drops out of the report
#: filter, which is the failure mode that patch exists to prevent.
ROLE_AR = "Finance & Accounting Manager"
ROLE_PM = "Project Manager"

ANCHOR_WON = "Opportunity Won"
ANCHOR_CREATED = "Project Created"
ANCHOR_PAYMENT = "Payment Received"

STEPS_FIELD = "custom_process_steps"

#: "Hold Hand-Off Meeting". As of the 2026-08-06 process meeting this step is
#: recorded on the **Opportunity**, before a Project can be created, and the
#: completion is carried onto the project row when the project is finally made.
HANDOFF_STEP_NUMBER = 2

#: "Initial Payment From Customer". Excluded from overdue escalation: when the
#: customer pays is not something anybody here can be nagged into fixing.
#: Step 4 (sending the invoice) is ours and stays in. Also excluded from
#: *blocking* later steps (see :func:`_actionable_steps`) — payment can take
#: weeks, and there is no reason "Outline Tasks" (6) or "Hold Project Launch
#: Meeting" (7), both PM-owned and neither dependent on money having arrived,
#: should sit stalled behind it.
PAYMENT_STEP_NUMBER = 5

#: Step numbers that never block a *later* step from becoming actionable, even
#: while still Pending themselves. Currently just the payment step; see above.
NON_BLOCKING_STEP_NUMBERS = frozenset({PAYMENT_STEP_NUMBER})

#: How far out :func:`schedule_payment_followup` puts the next chase, in business
#: days. The payment step has ``sla_business_days = 0`` — no due date, no
#: escalation, deliberately, because the customer's cheque is not ours to hurry.
#: That left it the one actionable step with nothing to *do*, so the follow-up is
#: a manual chain instead: each reminder that fires is an invitation to set the
#: next one, rather than a clock nobody can beat.
PAYMENT_FOLLOWUP_BUSINESS_DAYS = 2

#: Marks a step completed by the permission-gated skip path rather than done.
#: A prefix on ``notes``, not a status, because a skipped step must not stall the
#: due-date chain for the steps after it — but it must never read as a completion.
SKIPPED_PREFIX = "[SKIPPED]"

#: Cap on the weekly digest's detail table, so one bad week cannot produce an
#: unreadable email. The count above it always reports the true total.
DIGEST_ROW_LIMIT = 25


def _has_steps_field(doc):
	"""Schema guard: the fixtures that add the child table sync later in the
	same migrate that ships these hooks — never touch a doc that predates them."""
	return bool(doc.meta.get_field(STEPS_FIELD))


def _templates():
	return frappe.get_all(
		"Process Step Template",
		filters={"enabled": 1},
		fields=["step_number", "step_title", "responsible_role", "auto_anchor", "sla_hours", "sla_business_days", "description"],
		order_by="step_number asc",
	)


def _first_pending(steps):
	pending = [row for row in steps if row.status == "Pending"]
	return min(pending, key=lambda row: cint(row.step_number)) if pending else None


def _actionable_steps(steps):
	"""Every Pending step whose predecessors are all resolved — not just the
	single lowest-numbered one.

	A step blocks later ones only while it is itself Pending *and* not in
	``NON_BLOCKING_STEP_NUMBERS``. That lets step 5 (payment) sit Pending
	indefinitely without stalling 6 and 7, while still requiring, e.g., step 6
	to finish before step 7 opens up. Always non-empty when ``steps`` has any
	Pending row at all: the lowest-numbered Pending step can never be blocked
	by anything smaller than itself.
	"""
	blocking_pending = {
		cint(row.step_number)
		for row in steps
		if row.status == "Pending" and cint(row.step_number) not in NON_BLOCKING_STEP_NUMBERS
	}
	actionable = [
		row
		for row in steps
		if row.status == "Pending" and not any(n < cint(row.step_number) for n in blocking_pending)
	]
	return sorted(actionable, key=lambda row: cint(row.step_number))


def _handoff_holiday_list():
	"""Holiday List used for business-day due dates (settings; None = weekends only)."""
	return frappe.db.get_single_value("ERPNext Enhancements Settings", "handoff_holiday_list")


def _refresh_due(doc):
	"""Ensure every actionable step has a due date: now + SLA business days.

	Was "the current (first pending) step"; now every step
	:func:`_actionable_steps` returns, since 6 and 7 can become actionable at
	the same time as (or ahead of) 5 — each needs its own SLA clock starting
	the moment it opens up, not just whichever step is numerically first.

	The SLA is counted in business days (Mon-Fri, skipping the configured Holiday
	List) so a 2-day SLA set on a Friday lands the following Tuesday. ``0`` business
	days means no due date / no escalation. Rows predating the ``sla_business_days``
	field (pre-backfill) fall back to the legacy calendar ``sla_hours``, rounded up
	to whole days.
	"""
	for row in _actionable_steps(doc.get(STEPS_FIELD) or []):
		if row.due_by:
			continue
		days = row.get("sla_business_days")
		if days is None:
			days = (cint(row.get("sla_hours")) + 23) // 24  # legacy fallback: ceil(hours/24)
		days = cint(days)
		if days <= 0:
			continue
		row.due_by = add_working_days(now_datetime(), days, holiday_list=_handoff_holiday_list())


def _opportunity_handoff(opportunity):
	"""Pre-project hand-off completion recorded on the Opportunity, or ``None``.

	Returns ``{"on", "by", "skip_reason"}``. Column-guarded because ``doc_events``
	fire during ERPNext's own test bootstrap, before this app's custom fields
	exist — a fresh-DB install must not crash here.
	"""
	try:
		if not frappe.db.has_column("Opportunity", "custom_handoff_meeting_held"):
			return None
	except Exception:
		return None

	row = frappe.db.get_value(
		"Opportunity",
		opportunity,
		[
			"custom_handoff_meeting_held",
			"custom_handoff_meeting_on",
			"custom_handoff_meeting_by",
			"custom_handoff_skip_reason",
		],
		as_dict=True,
	)
	if not row or not cint(row.custom_handoff_meeting_held):
		return None
	return {
		"on": get_datetime(row.custom_handoff_meeting_on) if row.custom_handoff_meeting_on else None,
		"by": row.custom_handoff_meeting_by,
		"skip_reason": row.custom_handoff_skip_reason,
	}


def _append_steps(doc):
	"""Copy enabled templates onto ``doc``; retro-complete anchored steps.

	Returns True if steps were appended.
	"""
	templates = _templates()
	if not templates:
		return False

	won_on = None
	handoff = None
	if doc.get("custom_opportunity"):
		won_on = frappe.db.get_value("Opportunity", doc.custom_opportunity, "custom_date_closed_won")
		handoff = _opportunity_handoff(doc.custom_opportunity)

	now = now_datetime()
	for template in templates:
		row = {
			"step_number": template.step_number,
			"step_title": template.step_title,
			"responsible_role": template.responsible_role,
			"sla_hours": cint(template.sla_hours),
			"sla_business_days": cint(template.get("sla_business_days")),
			"auto_anchor": template.auto_anchor or "",
			"status": "Pending",
		}
		if template.auto_anchor == ANCHOR_WON:
			row["status"] = "Completed"
			row["completed_on"] = get_datetime(won_on) if won_on else now
		elif cint(template.step_number) == HANDOFF_STEP_NUMBER and handoff:
			# The hand-off now happens on the Opportunity, before this Project
			# existed. Carry the real who/when across rather than re-stamping
			# "completed now by whoever created the project" — project-side
			# reporting is measuring how long the hand-off took, and a fresh
			# timestamp here would report every hand-off as instant.
			row["status"] = "Completed"
			row["completed_on"] = handoff.get("on") or now
			row["completed_by"] = handoff.get("by")
			if handoff.get("skip_reason"):
				row["notes"] = f"{SKIPPED_PREFIX} {handoff['skip_reason']}"
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


def _source_opportunity(doc):
	"""The Opportunity a Project came from, normalising the legacy in-memory link."""
	if not doc.get("custom_opportunity") and doc.get("custom_sales_opportunity"):
		doc.custom_opportunity = doc.get("custom_sales_opportunity")
	return doc.get("custom_opportunity")


def enforce_handoff_gate(doc, method=None):
	"""Project ``before_insert`` — refuse a project whose hand-off is unrecorded.

	**This must stay on ``before_insert``.** The obvious home is ``validate``, but
	the app's own creation path
	(``crm_enhancements.api.create_project_from_opportunity_background``) sets
	``flags.ignore_validate = True`` before inserting, and Frappe's
	``run_before_save_methods`` returns early on that flag — so a ``validate``
	gate would pass review and then silently never fire on the one path that
	creates most projects. ``before_insert`` runs regardless of both
	``ignore_validate`` and ``ignore_permissions``, which is exactly the coverage
	the audit's 8-of-28 side paths need.

	Registered *before* :func:`seed_process_steps` in the ``before_insert`` list:
	Frappe runs list entries in order, and a refused project must not have seeded
	its tracker rows first.
	"""
	from erpnext_enhancements.crm_enhancements.handoff import throw_if_handoff_missing

	opportunity = _source_opportunity(doc)
	if not opportunity:
		# A project with no source Opportunity has no hand-off to gate on. This
		# is not a loophole: the gate exists to stop a *won deal* skipping its
		# hand-off, and internal projects legitimately have no deal behind them.
		return
	throw_if_handoff_missing(opportunity)


def seed_process_steps(doc, method=None):
	"""Project ``before_insert`` — seed the tracker on opportunity-born projects.

	Also normalizes the source link: a doc arriving with only the legacy
	in-memory ``custom_sales_opportunity`` attribute (pre-v1.3.0 make_project
	mapping, possibly replayed from a stale client) gets it copied onto the
	persisted ``custom_opportunity`` field.
	"""
	if not _has_steps_field(doc) or doc.get(STEPS_FIELD):
		return
	if not _source_opportunity(doc):
		return
	if not process_automation_enabled():
		return
	_append_steps(doc)


def announce_seeded_steps(doc, method=None):
	"""Project ``after_insert`` — tell the first pending step's owner it's up."""
	if not _has_steps_field(doc) or not process_automation_enabled():
		return
	current = _first_pending(doc.get(STEPS_FIELD) or [])
	if current:
		_enqueue_step_notice(doc.name, "up")


def sync_process_steps(doc, method=None):
	"""Project ``before_save`` — anchors, completion stamps, due dates."""
	if not _has_steps_field(doc) or not process_automation_enabled():
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
	"""Project ``on_update`` — a step just completed: hand off to the new owner(s).

	"The next owner" can now be plural: completing step 4 can open both step 5
	(payment) and step 6 (tasks) at once, and each owner should hear their step
	is up exactly once — not step 5's owner re-pinged every time something
	downstream of it later changes. So this diffs the *actionable* sets before
	and after the save, using ``get_doc_before_save()``, and only notifies
	steps that newly became actionable.
	"""
	if not _has_steps_field(doc) or not process_automation_enabled():
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

	before_actionable = {row.name for row in _actionable_steps(before.get(STEPS_FIELD) or [])}
	after_actionable = _actionable_steps(steps)
	newly_actionable = [row for row in after_actionable if row.name not in before_actionable]

	for row in newly_actionable:
		_enqueue_step_notice(doc.name, "up", step_name=row.name)

	if not after_actionable:
		# Nothing Pending survives a non-empty _actionable_steps() input, so this
		# is exactly "every step is Completed or Skipped" — see its docstring.
		done = sum(1 for row in steps if row.status == "Completed")
		doc.add_comment(
			"Comment",
			_("Hand-off process complete — all {0} steps done (PRO-0204).").format(done),
		)


def _enqueue_step_notice(project, kind, step_name=None):
	frappe.enqueue(
		"erpnext_enhancements.process_steps.deliver_step_notice",
		project=project,
		kind=kind,
		step_name=step_name,
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


def _escalation_fallback():
	"""Configured address for escalations when a person has no manager on record."""
	try:
		return frappe.db.get_single_value(
			"ERPNext Enhancements Settings", "handoff_escalation_fallback"
		)
	except Exception:
		return None


def _managers_of(recipients):
	"""The managers to copy on an overdue step.

	``Employee.reports_to`` is the only manager field that actually exists here
	(``User`` has none), and it is maintained on 13 of 15 active employees — good
	enough to be the primary path, not good enough to be the only one. Anyone
	without it escalates to the configured fallback address instead, so "my
	manager isn't set up" can never mean "nobody hears about this".

	Returns recipient dicts shaped like ``_resolve_responsible``'s, so ``_deliver``
	handles them identically. Never raises: an escalation that fails to find a
	manager must still deliver to the responsible person.
	"""
	managers = []
	seen = set()
	fallback_needed = False

	for recipient in recipients:
		employee = recipient.get("name")
		manager = None
		if employee:
			try:
				manager = frappe.db.get_value("Employee", employee, "reports_to")
			except Exception:
				manager = None
		if not manager:
			fallback_needed = True
			continue
		if manager in seen:
			continue
		seen.add(manager)
		row = frappe.db.get_value(
			"Employee", manager, ["name", "employee_name", "cell_number", "user_id"], as_dict=True
		)
		if row:
			managers.append(row)
		else:
			fallback_needed = True

	if fallback_needed:
		fallback = _escalation_fallback()
		if fallback:
			# Synthetic recipient: an address with no Employee behind it. No
			# cell_number, so it is email + nothing else — which is the point.
			managers.append(frappe._dict(employee_name=fallback, cell_number=None, user_id=None, email=fallback))

	return managers


def _days_overdue(due_by):
	"""Whole days a due date is past, floored at 0."""
	if not due_by:
		return 0
	delta = now_datetime() - get_datetime(due_by)
	return max(delta.days, 0)


def _customer_label(project_row):
	"""Customer name for the subject line, falling back to the project label."""
	try:
		customer = frappe.db.get_value("Project", project_row.get("name"), "customer")
	except Exception:
		customer = None
	return customer or project_row.get("project_name") or project_row.get("name")


def deliver_step_notice(project, kind="up", step_name=None):
	"""Background job: notify the responsible person for a step.

	Targets ``step_name`` when given — an "up" notice for one newly actionable
	step, or an "overdue" notice, which always names one. Falls back to the
	project's first pending step when it isn't, for callers (just
	:func:`announce_seeded_steps`) that haven't got a specific row in hand.
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
	if step_name:
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
		# Escalate: the responsible person AND their manager, every day it stays
		# late. One person quietly ignoring a nag is how 17 of 17 hand-off
		# meetings went past SLA with nobody noticing.
		recipients = recipients + _managers_of(recipients)
		days_over = _days_overdue(step.due_by)
		customer = _customer_label(doc)
		message = (
			f"OVERDUE on {label}: step {step.step_number}/{total} - {step.step_title} "
			f"(due {frappe.utils.format_datetime(step.due_by)}, {days_over} day(s) over).\n{link}"
		)
		# The subject is the whole notification for anyone triaging on a phone:
		# who it is about, which step, and how late. Not just "overdue step".
		subject = _("{0} days overdue — {1}: {2}").format(days_over, customer or label, step.step_title)
		_deliver(
			recipients,
			message,
			subject=subject,
			reference_doctype="Project",
			reference_docname=doc.name,
			email=True,
		)
		return

	due = f" Due {frappe.utils.format_datetime(step.due_by)}." if step.due_by else ""
	message = f"Your step on {label}: {step.step_number}/{total} - {step.step_title}.{due}\n{link}"
	subject = _("Hand-off step {0} of {1} is up on {2}: {3}").format(
		step.step_number, total, label, step.step_title
	)

	_deliver(recipients, message, subject=subject, reference_doctype="Project", reference_docname=doc.name)


def escalate_overdue_steps():
	"""Daily scheduler — re-nag every actionable step's owner and their manager.

	Was "only the current step of each active project"; now every step
	:func:`_actionable_steps` returns, since step 5 (payment) no longer stalls
	6 and 7 behind it — an overdue "Outline Tasks" must still escalate even
	while payment is still outstanding. At most once per day per step. Repeats
	every day the step stays late — ``last_reminded_on`` de-dupes by calendar
	date, not by "already nagged once".

	Step 5 (Initial Payment From Customer) itself is still excluded from escalating:
	when a customer pays is outside our control, and nagging our own AR about it
	daily teaches people to filter these emails, which costs us the steps that
	*are* ours. Step 4 — sending the invoice — is ours and stays in.

	Also sweeps the hand-off step while it still lives on the Opportunity, where
	no ``Project Process Step`` row exists yet to be found by the query below.
	"""
	if not process_automation_enabled():
		return
	escalate_overdue_handoffs()
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

	by_project = {}
	for row in rows:
		if row.parent in active:
			by_project.setdefault(row.parent, []).append(row)

	for project, project_rows in by_project.items():
		for row in _actionable_steps(project_rows):
			if cint(row.step_number) == PAYMENT_STEP_NUMBER:
				continue  # customer payment timing is not ours to be nagged about
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


def escalate_overdue_handoffs():
	"""Daily — nag on hand-off meetings that are past SLA on the Opportunity.

	The counterpart to :func:`escalate_overdue_steps`, and it has to be separate:
	step 2 now happens *before* a Project exists, so there is no
	``Project Process Step`` row for the query above to find. Without this, the
	step the whole change is about would be the one step nobody chases — which is
	exactly the state the audit found (17 of 17 hand-offs past SLA, silently).

	Same cadence and same escalation shape as the project-side sweep: responsible
	person plus their manager, at most once per calendar day, repeating daily
	while it stays late.
	"""
	if not handoff_gate_enabled():
		return
	try:
		if not frappe.db.has_column("Opportunity", "custom_handoff_due_by"):
			return
	except Exception:
		return

	now = now_datetime()
	rows = frappe.get_all(
		"Opportunity",
		filters={
			"status": "Closed Won",
			"custom_handoff_gate_applies": 1,
			"custom_handoff_meeting_held": 0,
			"custom_handoff_due_by": ("<", now),
		},
		fields=[
			"name",
			"customer_name",
			"party_name",
			"opportunity_owner",
			"custom_handoff_due_by",
			"custom_handoff_last_reminded_on",
		],
		order_by="custom_handoff_due_by asc",
	)

	for row in rows:
		if (
			row.custom_handoff_last_reminded_on
			and get_datetime(row.custom_handoff_last_reminded_on).date() == now.date()
		):
			continue
		try:
			_deliver_handoff_overdue(row, now)
			frappe.db.set_value(
				"Opportunity",
				row.name,
				"custom_handoff_last_reminded_on",
				now,
				update_modified=False,
			)
		except Exception:
			frappe.log_error(
				f"Hand-off escalation for {row.name} failed:\n{frappe.get_traceback()}",
				"Hand-Off Escalation Error",
			)


def _deliver_handoff_overdue(row, now):
	"""One overdue-hand-off notice: the AE and their manager, email + in-app."""
	recipients = []
	if row.opportunity_owner:
		employee = frappe.db.get_value(
			"Employee",
			{"user_id": row.opportunity_owner, "status": "Active"},
			["name", "employee_name", "cell_number", "user_id"],
			as_dict=True,
		)
		recipients.append(
			employee
			or frappe._dict(
				employee_name=row.opportunity_owner,
				cell_number=None,
				user_id=row.opportunity_owner,
			)
		)
	recipients = recipients + _managers_of(recipients)
	if not recipients:
		# Nobody resolves — fall back rather than dropping it entirely.
		fallback = _escalation_fallback()
		if not fallback:
			return
		recipients = [frappe._dict(employee_name=fallback, cell_number=None, user_id=None, email=fallback)]

	label = row.customer_name or row.party_name or row.name
	days_over = _days_overdue(row.custom_handoff_due_by)
	link = get_url_to_form("Opportunity", row.name)
	message = (
		f"OVERDUE hand-off meeting for {label}: due "
		f"{frappe.utils.format_datetime(row.custom_handoff_due_by)}, {days_over} day(s) over.\n"
		f"No project can be created until the hand-off is recorded.\n{link}"
	)
	subject = _("{0} days overdue — {1}: Hold Hand-Off Meeting").format(days_over, label)
	_deliver(
		recipients,
		message,
		subject=subject,
		reference_doctype="Opportunity",
		reference_docname=row.name,
		email=True,
	)


# ---------------------------------------------------------------------------
# Weekly compliance digest
# ---------------------------------------------------------------------------


def _digest_recipients():
	try:
		rows = frappe.get_single("ERPNext Enhancements Settings").get("handoff_report_recipients") or []
	except Exception:
		return []
	return [row.email for row in rows if row.get("email")]


def send_weekly_sla_digest():
	"""Friday morning — one email summarising hand-off SLA compliance.

	Renders the same numbers as the **Hand-Off SLA Compliance** report, from the
	report's own code, so the email and the desk view can never disagree. The
	report is the detail; this is the page-one summary that makes somebody open
	it.

	Sent even when everything is green, unlike the e-sign digest: the whole
	problem this change addresses is that nobody was looking, and a report that
	only appears when it is bad trains people to read the silence as "fine"
	rather than "not measured".
	"""
	if not process_automation_enabled():
		return
	recipients = _digest_recipients()
	if not recipients:
		return

	try:
		# Package name is scrub("Hand-Off SLA Compliance") — Frappe derives a script
		# report's module path from the report name, so the hyphen becomes its own
		# underscore. Do not "tidy" this to handoff_*; the report stops loading.
		from erpnext_enhancements.project_enhancements.report.hand_off_sla_compliance import (
			hand_off_sla_compliance as report,
		)

		filters = frappe._dict({"from_date": report.DEFAULT_FROM_DATE})
		data = report.get_data(filters)
		summary = report.get_report_summary(data) or []
		prose = report.get_summary_message(data, filters)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hand-off SLA digest: report failed")
		return

	kpis = "".join(
		f"<td style='padding:8px 14px;border:1px solid #ddd'>"
		f"<div style='font-size:11px;color:#666'>{frappe.utils.escape_html(str(card.get('label')))}</div>"
		f"<div style='font-size:20px;font-weight:700'>{frappe.utils.escape_html(str(card.get('value')))}</div>"
		f"</td>"
		for card in summary
	)

	overdue = [row for row in data if row.get("_state_raw") == "Overdue"]
	overdue.sort(key=lambda row: row.get("days_over") or 0, reverse=True)
	rows_html = "".join(
		"<tr>"
		f"<td>{frappe.utils.escape_html(str(row.get('project') or ''))}</td>"
		f"<td>{frappe.utils.escape_html(str(row.get('customer') or ''))}</td>"
		f"<td>{frappe.utils.escape_html(str(row.get('step_number') or ''))}. "
		f"{frappe.utils.escape_html(str(row.get('step_title') or ''))}</td>"
		f"<td>{frappe.utils.escape_html(str(row.get('responsible_role') or ''))}</td>"
		f"<td style='text-align:right'>{row.get('days_over') or 0}</td>"
		"</tr>"
		for row in overdue[:DIGEST_ROW_LIMIT]
	)
	truncated = (
		f"<p><i>{frappe.utils.escape_html(_('Showing the {0} most overdue of {1}.').format(DIGEST_ROW_LIMIT, len(overdue)))}</i></p>"
		if len(overdue) > DIGEST_ROW_LIMIT
		else ""
	)
	detail = (
		"<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
		"<tr><th>Project</th><th>Customer</th><th>Step</th><th>Responsible</th><th>Days over</th></tr>"
		f"{rows_html}</table>"
		if overdue
		else f"<p>{frappe.utils.escape_html(_('Nothing is overdue. Good week.'))}</p>"
	)

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=_("Hand-Off SLA Compliance — week ending {0}").format(
				frappe.utils.format_date(now_datetime())
			),
			message=(
				f"<table style='border-collapse:collapse'><tr>{kpis}</tr></table>"
				f"<p>{prose}</p>"
				f"{truncated}{detail}"
				f"<p><a href='{get_url_to_report('Hand-Off SLA Compliance')}'>"
				f"{frappe.utils.escape_html(_('Open the full report'))}</a></p>"
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hand-off SLA digest: send failed")


def get_url_to_report(report_name):
	return frappe.utils.get_url(f"/app/query-report/{frappe.utils.quoted(report_name)}")


# ---------------------------------------------------------------------------
# Manual kick-off for in-flight projects
# ---------------------------------------------------------------------------


def _may_complete(project_row, role):
	"""True when the session user owns ``role`` on this project, or is an admin.

	Membership is checked against the same resolution the notifications use, so
	"who gets nagged" and "who may tick it" can never drift apart. System Manager
	always passes — somebody has to be able to unstick a process when the named
	owner is on holiday.
	"""
	roles = set(frappe.get_roles())
	if "System Manager" in roles or "Projects Manager" in roles:
		return True
	user = frappe.session.user
	for recipient in _resolve_responsible(project_row, role):
		if recipient.get("user_id") == user:
			return True
	return False


@frappe.whitelist()
def complete_step(project, step_name, notes=None):
	"""Mark one hand-off step complete. **The only supported way to do it.**

	The timestamps are generated *here*, on the server, from the server clock and
	the server session — never accepted from the client. The 2026-08-06 audit
	found retroactive box-checking, and the old client path
	(``frappe.model.set_value(row, "status", "Completed"); frm.save()``) let the
	browser propose ``completed_on`` freely.

	Permission is the step's own responsible role (or System Manager), not merely
	write access to the Project: the process only means something if the person
	who ticks a step is the person accountable for it.
	"""
	throw_if_process_automation_disabled()

	doc = frappe.get_doc("Project", project)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Project."), frappe.PermissionError)
	if not _has_steps_field(doc):
		frappe.throw(_("The process steps field is not available yet — run migrations first."))

	row = next((r for r in (doc.get(STEPS_FIELD) or []) if r.name == step_name), None)
	if not row:
		frappe.throw(_("That step is not on this project."))
	if row.status == "Completed":
		return {"already": True, "step_number": row.step_number}

	if not _may_complete(doc, row.responsible_role):
		frappe.throw(
			_("Only the {0} for this project (or a System Manager) can complete this step.").format(
				row.responsible_role or _("responsible role")
			),
			frappe.PermissionError,
		)

	row.status = "Completed"
	row.completed_on = now_datetime()
	row.completed_by = frappe.session.user
	if notes:
		row.notes = notes

	# save() runs the normal before_save chain, so _refresh_due starts the next
	# step's clock and on_update notifies its owner — the whole point of the step
	# completing. Nothing here bypasses that.
	doc.save()
	return {"already": False, "step_number": row.step_number}


@frappe.whitelist()
def start_process(project):
	"""Seed the hand-off tracker on an existing Project (form button).

	Per the Jun 9 meeting, in-flight projects are not back-filled
	automatically — this is their explicit opt-in. Anchored steps complete
	retroactively (won date, project creation, payment if already received).
	"""
	throw_if_process_automation_disabled()
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


def _payment_step_row(doc):
	"""The payment step's child row on a project, or ``None``."""
	for row in doc.get(STEPS_FIELD) or []:
		if cint(row.get("step_number")) == PAYMENT_STEP_NUMBER:
			return row
	return None


def _followup_owner(doc):
	"""(user, employee_name) for the person the payment chase belongs to.

	Resolved through :func:`_resolve_responsible` rather than a role check, so
	"who gets reminded" cannot drift from "who gets nagged" and "who may tick the
	step" — all three read the same setting.
	"""
	for recipient in _resolve_responsible(doc, ROLE_AR):
		if recipient.get("user_id"):
			return recipient["user_id"], recipient.get("employee_name") or recipient["user_id"]
	return None, None


def _pending_followups(project, user):
	"""Un-fired reminders this feature created for a project, newest first.

	Filtered on ``notified = 0``: a reminder that has already popped is history
	and must survive, or rescheduling would quietly erase the record of every
	chase already made.
	"""
	return frappe.get_all(
		"Reminder",
		filters={
			"reminder_doctype": "Project",
			"reminder_docname": project,
			"user": user,
			"notified": 0,
		},
		fields=["name", "remind_at"],
		order_by="remind_at desc",
	)


@frappe.whitelist()
def get_payment_followup(project):
	"""What the button should show: the next scheduled chase, and the default.

	Returns ``{"remind_at", "user", "employee_name", "default_remind_at",
	"business_days"}``. ``remind_at`` is ``None`` when nothing is scheduled.
	Read-only; safe to call on every form refresh.
	"""
	doc = frappe.get_doc("Project", project)
	if not doc.has_permission("read"):
		frappe.throw(_("Not permitted to read this Project."), frappe.PermissionError)

	user, employee_name = _followup_owner(doc)
	pending = _pending_followups(project, user) if user else []
	return {
		"remind_at": pending[0].remind_at if pending else None,
		"user": user,
		"employee_name": employee_name,
		"default_remind_at": add_working_days(
			now_datetime(), PAYMENT_FOLLOWUP_BUSINESS_DAYS, holiday_list=_handoff_holiday_list()
		),
		"business_days": PAYMENT_FOLLOWUP_BUSINESS_DAYS,
	}


@frappe.whitelist()
def schedule_payment_followup(project, remind_at=None):
	"""Schedule the next chase on an unpaid initial payment.

	Creates a core **Reminder** — the same record the bell icon's "Remind Me"
	makes, so it fires through Frappe's own reminder cron and needs no delivery
	machinery of ours.

	``remind_at`` defaults to :data:`PAYMENT_FOLLOWUP_BUSINESS_DAYS` business days
	out, counted the same way step due dates are (Mon-Fri minus the hand-off
	Holiday List), so a Thursday click lands Monday rather than Saturday.

	The reminder is addressed to the **Finance & Accounting Manager** resolved for
	this project, which is usually not the person clicking — a PM chasing a
	project should be able to put it on the right person's list. That is why this
	inserts with ``ignore_permissions``: the gate is write access on the Project,
	checked here, not the session user's rights over somebody else's Reminder.
	"""
	throw_if_process_automation_disabled()
	doc = frappe.get_doc("Project", project)
	if not doc.has_permission("write"):
		frappe.throw(_("Not permitted to modify this Project."), frappe.PermissionError)

	row = _payment_step_row(doc)
	if not row:
		frappe.throw(_("This project has no payment step to follow up on."))
	if row.status == "Completed":
		frappe.throw(_("The initial payment is already recorded — nothing to chase."))

	user, employee_name = _followup_owner(doc)
	if not user:
		frappe.throw(
			_(
				"No user to remind. Set the Finance & Accounting Manager in "
				"ERPNext Enhancements Settings → Hand-Off Process, and make sure that "
				"Employee record has a User ID."
			)
		)

	when = get_datetime(remind_at) if remind_at else add_working_days(
		now_datetime(), PAYMENT_FOLLOWUP_BUSINESS_DAYS, holiday_list=_handoff_holiday_list()
	)
	if when <= now_datetime():
		frappe.throw(_("Pick a reminder time in the future."))

	# One pending chase at a time. Rescheduling replaces the outstanding one
	# rather than stacking, which is what "set the next reminder" means — but
	# only un-fired ones, so the history of chases already made is untouched.
	superseded = _pending_followups(project, user)
	for existing in superseded:
		frappe.delete_doc("Reminder", existing.name, ignore_permissions=True, delete_permanently=True)

	label = doc.get("project_name") or project
	customer = doc.get("customer")
	reminder = frappe.get_doc(
		{
			"doctype": "Reminder",
			"user": user,
			"remind_at": when,
			"reminder_doctype": "Project",
			"reminder_docname": project,
			"description": _("Follow up on the initial payment for {0}{1}. Set the next reminder from the project's process steps.").format(
				label, f" ({customer})" if customer else ""
			),
		}
	)
	reminder.insert(ignore_permissions=True)

	return {
		"remind_at": when,
		"user": user,
		"employee_name": employee_name,
		"replaced": len(superseded),
	}
