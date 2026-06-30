# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Finance Dashboard widgets.

The Finance Dashboard Custom HTML Blocks read these. Visibility is role-gated to
the finance roles (System Manager always allowed), mirroring
``erpnext_enhancements/api/kpi.py``. Each widget has its own enable toggle on
``ERPNext Enhancements Settings`` (default OFF); a disabled widget returns
``{"enabled": False}`` and the block renders a muted notice.

Shared helpers (``_can_view`` / ``_require_finance`` / ``_widget_enabled``) are
imported by ``api/finance_calendar.py`` and ``api/horoscope.py`` so the gating is
uniform across the Finance Dashboard widgets.
"""

import frappe
from frappe import _
from frappe.utils import (
	cint,
	date_diff,
	get_datetime,
	now_datetime,
	nowdate,
	time_diff_in_seconds,
)

# Finance roles allowed to view the dashboard widgets (System Manager always).
FINANCE_ROLES = {"Accounts Manager", "Accounts User"}

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"


def _can_view() -> bool:
	roles = set(frappe.get_roles())
	return "System Manager" in roles or bool(FINANCE_ROLES & roles)


def _require_finance():
	if not _can_view():
		frappe.throw(_("Not permitted."), frappe.PermissionError)


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def _widget_enabled(field, settings=None) -> bool:
	"""True when a per-widget toggle is on. A missing Check reads as 0 (default OFF)."""
	settings = settings or _settings()
	return bool(cint(settings.get(field)))


@frappe.whitelist()
def get_finance_config():
	"""Per-widget enable flags + the weather coordinates (reused from the Wall
	settings). Drives which blocks render and the keyless Open-Meteo fetch."""
	_require_finance()
	settings = _settings()
	return {
		"enabled": {
			"new_jobs": _widget_enabled("finance_new_jobs_enabled", settings),
			"whos_working": _widget_enabled("finance_whos_working_enabled", settings),
			"weather": _widget_enabled("finance_weather_enabled", settings),
			"astrology": _widget_enabled("finance_astrology_enabled", settings),
			"calendar": _widget_enabled("finance_calendar_enabled", settings),
		},
		"weather": {
			"latitude": float(settings.get("weather_latitude") or 40.8894),
			"longitude": float(settings.get("weather_longitude") or -111.8808),
			"label": settings.get("weather_label") or "Bountiful, UT",
		},
	}


# Newest Active Projects shown in the queue.
NEW_JOBS_LIMIT = 15


@frappe.whitelist()
def get_new_jobs():
	"""Most recently created Active Projects (the 'new jobs' queue)."""
	_require_finance()
	settings = _settings()
	if not _widget_enabled("finance_new_jobs_enabled", settings):
		return {"enabled": False}

	# Role-gated above; fetch permission-free so per-user Project permissions can't
	# blank this shared board (same approach as the Task Dashboard).
	projects = frappe.get_all(
		"Project",
		filters={"status": "Active"},
		fields=[
			"name",
			"project_name",
			"customer",
			"custom_project_owner",
			"custom_opportunity",
			"creation",
		],
		order_by="creation desc",
		limit=NEW_JOBS_LIMIT,
		ignore_permissions=True,
	)

	owner_ids = {p["custom_project_owner"] for p in projects if p.get("custom_project_owner")}
	owner_names = {}
	if owner_ids:
		owner_names = dict(
			frappe.get_all(
				"Employee",
				filters={"name": ("in", list(owner_ids))},
				fields=["name", "employee_name"],
				as_list=True,
			)
		)

	today = nowdate()
	jobs = []
	for p in projects:
		created = str(p["creation"])[:10] if p.get("creation") else ""
		jobs.append(
			{
				"name": p["name"],
				"project_name": p.get("project_name") or p["name"],
				"customer": p.get("customer") or "",
				"owner": owner_names.get(p.get("custom_project_owner")) or "",
				"opportunity": p.get("custom_opportunity") or "",
				"created": created,
				"age_days": date_diff(today, created) if created else None,
			}
		)
	return {"enabled": True, "jobs": jobs, "generated_at": str(now_datetime())}


def _elapsed_seconds(row, ref) -> int:
	"""Worked seconds for a Job Interval: wall clock minus accumulated pauses,
	minus the still-running pause when currently Paused. (Local copy of the Time
	Kiosk logic — not imported, to keep this module free of the FAC dependency.)"""
	if not row.get("start_time"):
		return 0
	end = get_datetime(row["end_time"]) if row.get("end_time") else ref
	seconds = time_diff_in_seconds(end, get_datetime(row["start_time"]))
	seconds -= row.get("total_paused_seconds") or 0
	if row.get("status") == "Paused" and row.get("last_pause_time"):
		seconds -= time_diff_in_seconds(end, get_datetime(row["last_pause_time"]))
	return max(0, int(seconds))


def _elapsed_label(seconds: int) -> str:
	hours, remainder = divmod(int(seconds), 3600)
	minutes = remainder // 60
	if hours:
		return f"{hours}h {minutes}m"
	return f"{minutes}m"


@frappe.whitelist()
def get_whos_working():
	"""Employees currently clocked in (open/paused Job Intervals), admin scope."""
	_require_finance()
	settings = _settings()
	if not _widget_enabled("finance_whos_working_enabled", settings):
		return {"enabled": False}

	rows = frappe.db.sql(
		"""
		SELECT ji.name, ji.employee, ji.project, ji.task, ji.status,
		       ji.start_time, ji.end_time, ji.total_paused_seconds, ji.last_pause_time,
		       emp.employee_name
		FROM `tabJob Interval` ji
		JOIN `tabEmployee` emp ON ji.employee = emp.name
		WHERE ji.status IN ('Open', 'Paused')
		ORDER BY ji.start_time ASC
		""",
		as_dict=True,
	)

	# Resolve project titles + task subjects in bulk.
	project_ids = {r.project for r in rows if r.get("project")}
	task_ids = {r.task for r in rows if r.get("task")}
	project_titles = {}
	if project_ids:
		project_titles = dict(
			frappe.get_all(
				"Project",
				filters={"name": ("in", list(project_ids))},
				fields=["name", "project_name"],
				as_list=True,
			)
		)
	task_subjects = {}
	if task_ids:
		task_subjects = dict(
			frappe.get_all(
				"Task",
				filters={"name": ("in", list(task_ids))},
				fields=["name", "subject"],
				as_list=True,
			)
		)

	ref = now_datetime()
	workers = []
	for r in rows:
		seconds = _elapsed_seconds(r, ref)
		workers.append(
			{
				"employee_name": r.get("employee_name") or r.get("employee"),
				"project": r.get("project") or "",
				"project_title": project_titles.get(r.get("project")) or r.get("project") or "",
				"task": r.get("task") or "",
				"task_subject": task_subjects.get(r.get("task")) or "",
				"status": r.get("status"),
				"started": str(r.get("start_time")) if r.get("start_time") else "",
				"elapsed_seconds": seconds,
				"elapsed_label": _elapsed_label(seconds),
			}
		)
	return {"enabled": True, "workers": workers, "generated_at": str(now_datetime())}
