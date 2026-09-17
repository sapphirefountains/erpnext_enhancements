# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The 06:45 supervisor digest: yesterday, per team, in one email.

Every user who is the ``reports_to`` of an active Employee gets their team; every enabled
HR Manager gets the whole company. Content is YESTERDAY (site date):

* hours per person — Completed intervals, net of pauses;
* intervals the sweeper auto-closed (the hours are an estimate);
* intervals whose tracking health is ``Gaps`` or ``None`` (the phone was not reporting);
* off-site clock-ins (``offsite_start``);
* the team's Time Correction Requests still ``Requested`` — whatever day they are about.

A recipient whose team had no intervals AND no pending requests gets nothing: an empty
digest trains people to delete the digest. Gated by
``Time Kiosk Settings.send_supervisor_digest``; cron ``45 6 * * *`` in hooks.py, after the
06:00 dispatch digest and before the 06:30 briefing has anything to say about it.

``email_style`` throughout — no container of this module's own.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_url, getdate, nowdate

from erpnext_enhancements import email_style
from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
	get_settings,
)

TIMELINE_URL = "/app/location-timeline"
REQUESTS_URL = "/app/time-correction-request?status=Requested"


def send_supervisor_digests(day=None):
	"""Cron entry. Returns the number of digests sent."""
	settings = get_settings()
	if not cint(settings.get("send_supervisor_digest")):
		return 0

	day = getdate(day) if day else add_days(getdate(nowdate()), -1)
	intervals = _intervals_for(day)
	requests = _pending_requests()
	employees = _active_employees()

	sent = 0
	for recipient, team in _recipients(employees).items():
		team_intervals = [i for i in intervals if i.employee in team]
		team_requests = [r for r in requests if r.employee in team]
		if not team_intervals and not team_requests:
			continue
		try:
			if _send_one(recipient, day, team_intervals, team_requests, employees):
				sent += 1
		except Exception:
			frappe.log_error(title=f"Supervisor digest: {recipient}")
	return sent


# ------------------------------------------------------------------ data


def _active_employees():
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "user_id", "reports_to"],
		limit=1000,
	)
	return {r.name: r for r in rows}


def _recipients(employees):
	"""``{user: set(employee names they see)}`` — supervisors get their reports, HR
	Managers the company. A supervisor who is also an HR Manager gets one email."""
	teams = {}
	for emp in employees.values():
		if not emp.reports_to:
			continue
		supervisor = employees.get(emp.reports_to)
		user = supervisor.user_id if supervisor else frappe.db.get_value("Employee", emp.reports_to, "user_id")
		if not user:
			continue
		teams.setdefault(user, set()).add(emp.name)

	hr_users = frappe.get_all(
		"Has Role", filters={"role": "HR Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	everyone = set(employees)
	for user in hr_users:
		if user == "Administrator":
			continue
		if frappe.db.get_value("User", user, "enabled"):
			teams[user] = set(everyone)
	return teams


def _intervals_for(day):
	start, end = f"{day} 00:00:00", f"{day} 23:59:59.999999"
	return frappe.get_all(
		"Job Interval",
		filters={"start_time": ["between", [start, end]]},
		fields=["name", "employee", "project", "start_time", "end_time", "status", "total_paused_seconds",
		        "auto_closed", "tracking_health", "tracking_coverage_pct", "offsite_start", "start_distance_m"],
		order_by="employee asc, start_time asc",
	)


def _pending_requests():
	try:
		return frappe.get_all(
			"Time Correction Request",
			filters={"status": "Requested"},
			fields=["name", "employee", "employee_name", "request_type", "requested_on", "job_interval"],
			order_by="requested_on asc",
		)
	except Exception:
		return []


def _net_hours(interval):
	if not interval.end_time or interval.status != "Completed":
		return 0.0
	seconds = (interval.end_time - interval.start_time).total_seconds() - flt(interval.total_paused_seconds)
	return max(seconds, 0.0) / 3600.0


# ------------------------------------------------------------------ email


def _send_one(user, day, intervals, requests, employees):
	row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
	if not row or not row.enabled or not row.email:
		return False

	def name_of(employee):
		emp = employees.get(employee)
		return (emp.employee_name if emp else None) or employee

	project_titles = {}
	projects = sorted({i.project for i in intervals if i.project})
	if projects:
		project_titles = dict(
			frappe.get_all("Project", filters={"name": ["in", projects]}, fields=["name", "project_name"], as_list=True)
		)

	hours = {}
	for i in intervals:
		hours[i.employee] = hours.get(i.employee, 0.0) + _net_hours(i)
	auto_closed = [i for i in intervals if cint(i.auto_closed)]
	gaps = [i for i in intervals if i.tracking_health in ("Gaps", "None")]
	offsite = [i for i in intervals if cint(i.offsite_start)]
	still_open = [i for i in intervals if i.status in ("Open", "Paused")]

	body = email_style.kpis([
		{"label": _("People"), "value": str(len(hours)), "tone": "info"},
		{"label": _("Hours"), "value": f"{sum(hours.values()):.1f}", "tone": "info"},
		{"label": _("Auto-closed"), "value": str(len(auto_closed)), "tone": "warning" if auto_closed else "success"},
		{"label": _("Tracking gaps"), "value": str(len(gaps)), "tone": "warning" if gaps else "success"},
	])

	if hours:
		body += email_style.h(_("Hours yesterday"))
		body += email_style.table(
			[_("Employee"), _("Hours"), _("Jobs"), _("Sites")],
			[
				[
					name_of(emp),
					f"{total:.2f}",
					str(sum(1 for i in intervals if i.employee == emp)),
					", ".join(sorted({project_titles.get(i.project) or i.project for i in intervals if i.employee == emp and i.project}))[:120],
				]
				for emp, total in sorted(hours.items(), key=lambda kv: name_of(kv[0]))
			],
		)

	if still_open:
		body += email_style.callout(
			_("{0} clock-in(s) from yesterday are still open and will be closed by the sweeper.").format(len(still_open)),
			"warning",
		)

	if auto_closed:
		body += email_style.h(_("Auto-closed (hours are estimates)"))
		body += email_style.table(
			[_("Employee"), _("Project"), _("Started"), _("Closed at")],
			[
				[name_of(i.employee), project_titles.get(i.project) or i.project,
				 frappe.utils.format_datetime(i.start_time), frappe.utils.format_datetime(i.end_time)]
				for i in auto_closed
			],
		)

	if gaps:
		body += email_style.h(_("Tracking gaps"))
		body += email_style.table(
			[_("Employee"), _("Project"), _("Health"), _("Coverage")],
			[
				[name_of(i.employee), project_titles.get(i.project) or i.project, i.tracking_health,
				 f"{flt(i.tracking_coverage_pct):.0f}%"]
				for i in gaps
			],
		)

	if offsite:
		body += email_style.h(_("Off-site clock-ins"))
		body += email_style.table(
			[_("Employee"), _("Project"), _("Distance"), _("Started")],
			[
				[name_of(i.employee), project_titles.get(i.project) or i.project,
				 f"{cint(i.start_distance_m):,} m", frappe.utils.format_datetime(i.start_time)]
				for i in offsite
			],
		)

	if requests:
		body += email_style.h(_("Correction requests waiting"))
		body += email_style.table(
			[_("Request"), _("Employee"), _("Type"), _("Requested")],
			[
				[r.name, r.employee_name or name_of(r.employee), r.request_type, frappe.utils.format_datetime(r.requested_on)]
				for r in requests
			],
		)

	body += email_style.links([
		(get_url(TIMELINE_URL), _("Location Timeline")),
		(get_url(REQUESTS_URL), _("Time Correction Requests")),
	])

	frappe.sendmail(
		recipients=[row.email],
		subject=_("Time Kiosk digest for {0}").format(frappe.utils.format_date(day)),
		message=email_style.wrap(
			body,
			title=_("Yesterday on the clock"),
			eyebrow=_("Supervisor digest — {0}").format(frappe.utils.format_date(day)),
		),
	)
	return True
