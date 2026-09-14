# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Noticing that an inspection has come round — WI-075 sub-phase I.

Sub-phase C seeded seventeen milestones, each carrying a ``trigger_basis``, and **nothing read
it**. A Build project could reach QA and sit there, and the pre-final commissioning check — the
one `docs/KPI_DASHBOARD_DESIGN.md` calls the biggest fountain-specific gap — would come round
only if somebody happened to remember. This module is what remembers.

It notices; it never acts
--------------------------

The sweep reports that an inspection is due. It does **not** generate one, for the same reason
:mod:`erpnext_enhancements.quality.routing` never guesses severity: a generated inspection reads
as though a person decided to inspect, and one that appeared on its own would be a draft nobody
owns sitting in a list, aging, looking like work in progress.

The judgement is not here
--------------------------

Whether a milestone is due lives in :mod:`erpnext_enhancements.quality.due`, which imports
nothing and is asserted on every push — including the case that motivates the whole design, a
project that jumps past its trigger status in one save and would never be caught by an equality
test.

Two things it says out loud that a tidier implementation would hide
--------------------------------------------------------------------

**A due milestone with no checklist is still reported**, flagged as blocked. Only the Build
commissioning checklist has ever been written down; the rest are Sapphire's standard of care and
live in people's heads. A list that quietly omitted them would turn a gap in what the company has
recorded into a gap nobody can see — which is the failure this entire programme exists to end.

**A project whose build status cannot be placed on the scale is reported too.** Answering "not
due" for a renamed or blank status would make this sweep report clean forever, on every project,
with nothing to investigate.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, nowdate

from erpnext_enhancements import email_style
from erpnext_enhancements.quality import due
from erpnext_enhancements.quality.critical_alerts import project_manager_user
from erpnext_enhancements.quality.doctype.quality_settings.quality_settings import is_enabled

#: Marker embedded in the notice comment so the next sweep can find its own last mention.
#: A marker rather than a field: this is a prompt, and a prompt does not deserve a column.
MARKER = "quality-due-notice"

#: How often one milestone on one project may be mentioned again. The due list is the durable
#: record and is always there to read; this is only the nudge, and a daily nudge about something
#: somebody has already decided to leave is how a mailbox rule gets written.
NOTICE_EVERY_DAYS = 7

#: Projects examined per sweep. A sweep that tries to walk all 654 in one job is a sweep that
#: times out and walks none of them.
SWEEP_LIMIT = 200

#: Project statuses worth looking at. A cancelled project owes nobody an inspection.
ACTIVE_STATUSES = ("Open",)


# ---------------------------------------------------------------------------
# Reading the scale
# ---------------------------------------------------------------------------


def build_status_order():
	"""The ordered options of ``Project.custom_build_status``, blanks dropped.

	Read from meta rather than hardcoded. The order **is** the seniority scale that
	:func:`due.status_reached` compares on, and a copy here would be a second definition of the
	company's build sequence that nothing keeps in step with the first.
	"""
	try:
		field = frappe.get_meta("Project").get_field("custom_build_status")
		if not field or not field.options:
			return ()
	except Exception:
		return ()
	return tuple(opt.strip() for opt in field.options.split("\n") if opt.strip())


def _milestones_for(project_type):
	if not project_type:
		return []
	return frappe.get_all(
		"Inspection Milestone",
		filters={"project_type": project_type},
		fields=[
			"name",
			"milestone_key",
			"milestone_title",
			"sequence",
			"gate_kind",
			"trigger_basis",
			"trigger_value",
			"interval_days",
			"multi_day_only",
			"disabled",
		],
		order_by="sequence asc",
	)


def _active_template_keys(project_type):
	"""Milestones that actually have an Active master template behind them."""
	rows = frappe.get_all(
		"Project Inspection Template",
		filters={"project_type": project_type, "status": "Active"},
		pluck="milestone",
	)
	return set(rows or ())


def _inspection_facts(project):
	"""``{milestone: (count, last_date)}`` for every inspection on this project.

	Cancelled inspections are excluded — a cancelled inspection is one that did not happen, and
	counting it would mark a milestone done on the strength of a record somebody withdrew.
	"""
	facts = {}
	for row in frappe.get_all(
		"Project Quality Inspection",
		filters={"project": project, "docstatus": ["!=", 2]},
		fields=["milestone", "inspection_date", "creation", "docstatus"],
	):
		milestone = row.get("milestone")
		count, last = facts.get(milestone, (0, None))
		count += 1
		# Only a SUBMITTED inspection restarts a calendar cadence. A draft somebody opened and
		# abandoned would otherwise buy another ninety days of silence.
		if row.get("docstatus") == 1:
			when = row.get("inspection_date") or row.get("creation")
			if when and (last is None or str(when) > str(last)):
				last = when
		facts[milestone] = (count, last)
	return facts


def assess_project(project_row, status_order=None):
	"""Every milestone for this project, with its verdict. Returns ``[(milestone, verdict), ...]``.

	``project_row`` needs ``name``, ``project_type``, ``custom_build_status``,
	``expected_start_date`` and ``expected_end_date``.
	"""
	milestones = _milestones_for(project_row.get("project_type"))
	if not milestones:
		return []

	active = _active_template_keys(project_row.get("project_type"))
	facts = _inspection_facts(project_row.get("name"))
	order = status_order if status_order is not None else build_status_order()
	today = nowdate()
	multi_day = _is_multi_day(project_row)

	out = []
	for milestone in milestones:
		count, last = facts.get(milestone["name"], (0, None))
		context = {
			"build_status": project_row.get("custom_build_status"),
			"status_order": order,
			"inspection_count": count,
			"last_inspection_on": last,
			"is_multi_day": multi_day,
			"has_active_template": milestone["name"] in active,
			"today": today,
		}
		out.append((milestone, due.assess(milestone, context)))
	return out


def _is_multi_day(project_row):
	"""``True``, ``False``, or ``None`` when the project has no dates to tell from.

	``None`` is not a polite ``False``. An event with no dates recorded is a different problem
	from a one-day event, and :func:`due.assess` says which one it is in its reason.
	"""
	start = project_row.get("expected_start_date")
	end = project_row.get("expected_end_date")
	if not start or not end:
		return None
	return str(start) != str(end)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def sweep():
	"""Daily: tell each project manager what has come round on their jobs.

	Never raises. A missed notice is a nuisance; a scheduler job that dies takes every other
	daily job in its queue with it.
	"""
	if not is_enabled() or not _notifications_enabled():
		return
	try:
		_notify_due()
	except Exception:
		frappe.log_error(title="Quality: due-inspection sweep failed", message=frappe.get_traceback())


def _notify_due():
	order = build_status_order()
	today = nowdate()
	by_manager = {}

	for project_row in frappe.get_all(
		"Project",
		filters={"status": ["in", ACTIVE_STATUSES]},
		fields=[
			"name",
			"project_name",
			"project_type",
			"custom_build_status",
			"custom_project_owner",
			"expected_start_date",
			"expected_end_date",
		],
		order_by="modified desc",
		limit=SWEEP_LIMIT,
	):
		try:
			verdicts = assess_project(project_row, status_order=order)
		except Exception:
			# One unreadable project must not stop the other 199 being told about.
			frappe.log_error(
				title="Quality: could not assess a project for due inspections",
				message=f"project={project_row['name']}\n\n{frappe.get_traceback()}",
			)
			continue

		items = [
			(milestone, verdict)
			for milestone, verdict in verdicts
			if verdict["state"] in (due.STATE_DUE, due.STATE_UNKNOWN)
		]
		if not items:
			continue

		fresh = [
			(milestone, verdict)
			for milestone, verdict in items
			if due.notice_due(
				_last_noticed(project_row["name"], milestone["milestone_key"]), today, NOTICE_EVERY_DAYS
			)
		]
		if not fresh:
			continue

		manager = project_manager_user(project_row["name"])
		if manager:
			by_manager.setdefault(manager, []).append((project_row, fresh))
		for milestone, _verdict in fresh:
			_record_notice(project_row["name"], milestone["milestone_key"])

	for manager, entries in by_manager.items():
		_send_digest(manager, entries)
	frappe.db.commit()


def _last_noticed(project, milestone_key):
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Project",
			"reference_name": project,
			"content": ["like", f"%{MARKER}:{milestone_key}%"],
		},
		fields=["creation"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0]["creation"] if rows else None


def _record_notice(project, milestone_key):
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Comment",
				"reference_doctype": "Project",
				"reference_name": project,
				"content": "<!-- {}:{} -->{}".format(
					MARKER,
					escape_html(milestone_key),
					_("An inspection milestone came round on this project and the project manager was told."),
				),
			}
		).insert(ignore_permissions=True)
	except Exception:
		# The notice going out matters more than the record of it having gone out, and the due
		# list is recomputed from scratch every sweep regardless.
		pass


def _send_digest(manager, entries):
	"""One email per manager per sweep, listing every project rather than one mail each.

	A manager with four jobs reaching QA in the same week should get one message about four
	jobs, not four messages. The second shape is how a filter gets written.
	"""
	try:
		email = frappe.db.get_value("User", manager, "email") or manager
		if not email or "@" not in email:
			return
		blocks = []
		for project_row, items in entries:
			link = frappe.utils.get_url_to_form("Project", project_row["name"])
			blocks.append(
				email_style.h(project_row.get("project_name") or project_row["name"])
				+ email_style.bullets([_digest_line(v, m) for m, v in items])
				+ email_style.button_fallback(link)
			)
		count = sum(len(items) for _p, items in entries)
		subject = _("{0} inspection milestones have come round").format(count)
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			message=email_style.wrap(
				email_style.p(
					_("These milestones are ready to be inspected. Nothing has been created — generating an inspection is still a deliberate act.")
				)
				+ "".join(blocks),
				title=subject,
				eyebrow=_("Quality"),
				preheader=_("Inspection milestones that are ready on your projects."),
			),
		)
	except Exception:
		frappe.log_error(
			title="Quality: due-inspection digest failed",
			message=f"manager={manager}\n\n{frappe.get_traceback()}",
		)


def _digest_line(verdict, milestone):
	title = milestone.get("milestone_title") or milestone.get("milestone_key") or ""
	if verdict["state"] == due.STATE_UNKNOWN:
		return _("{0} — cannot tell whether this is due: {1}").format(title, verdict["reason"])
	if verdict.get("blocked"):
		# Said plainly rather than filtered out. The checklist not existing is the finding.
		return _("{0} — due, but no checklist has been written for it yet").format(title)
	return _("{0} — {1}").format(title, verdict["reason"])


def _notifications_enabled():
	try:
		return bool(frappe.db.get_single_value("Quality Settings", "notifications_enabled"))
	except Exception:
		return False
