# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Operations Dashboard widgets.

Field-service worklists, live: today's visit board, device fleet compliance,
out-of-range chemistry, and visits that closed without any labour captured.

Gating is the shared department contract in ``api/dashboard_widgets.py``:
Operations roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.

Two doctypes here are optional. ``Managed Device`` only exists where the MDM
integration is installed, and ``Job Interval`` only where the time kiosk is; both
are guarded with ``frappe.db.exists("DocType", ...)`` so the widget reports "not
in use" instead of exploding a dashboard — the same defensive stance the KPI
aggregators take.
"""

import frappe
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 25

# workflow_state values that mean the visit is finished. 'Final/Submitted' is the
# terminal state used by the maintenance workflow (see sapphire_maintenance/).
COMPLETED_STATE = "Final/Submitted"

# How far back the chemistry and labour-gap widgets look.
LOOKBACK_DAYS = 14

# A device whose provider check-in is older than this is treated as out of
# contact — matching the Device Provider Sync Freshness KPI's intent.
DEVICE_STALE_DAYS = 7


def _doctype_exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _user_names(user_ids):
	"""Map user id -> full name, for the technician columns (technician is a Link
	to User on Sapphire Maintenance Record, not to Employee)."""
	ids = {u for u in user_ids if u}
	if not ids:
		return {}
	return dict(
		fetch_all("User", filters={"name": ("in", list(ids))}, fields=["name", "full_name"], as_list=True)
	)


@frappe.whitelist()
@widget_feed("Operations", "day_board")
def get_day_board():
	"""Every maintenance visit scheduled for today, with technician and progress.

	Ordered by state so the unfinished work sits at the top of the block: anything
	still open first, completed visits last.
	"""
	today = getdate(nowdate())
	rows = fetch_all(
		"Sapphire Maintenance Record",
		filters={"scheduled_visit_date": today},
		fields=[
			"name",
			"customer",
			"project",
			"visit_label",
			"technician",
			"completion_percent",
			"workflow_state",
			"clock_in_time",
			"clock_out_time",
		],
		order_by="workflow_state asc, technician asc",
		limit=ROW_LIMIT,
	)

	names = _user_names([r.technician for r in rows])
	visits = []
	done = 0
	for r in rows:
		complete = r.workflow_state == COMPLETED_STATE
		done += 1 if complete else 0
		visits.append(
			{
				"name": r.name,
				"title": r.visit_label or r.customer or r.name,
				"customer": r.customer or "",
				"project": r.project or "",
				"technician": names.get(r.technician) or r.technician or "",
				"state": r.workflow_state or "",
				"percent": flt(r.completion_percent),
				"complete": complete,
				"on_site": bool(r.clock_in_time and not r.clock_out_time),
			}
		)
	return {"visits": visits, "completed": done, "total": len(visits)}


@frappe.whitelist()
@widget_feed("Operations", "fleet_health")
def get_fleet_health():
	"""Device compliance counts plus the devices actually out of contact.

	``mdm_last_seen`` is only written by the provider sync, so a device that has
	never synced has a null rather than an old date. Those are reported in their
	own bucket instead of being folded in with genuinely stale ones — "never
	checked in" and "stopped checking in" need different follow-up.
	"""
	if not _doctype_exists("Managed Device"):
		return {"unavailable": "Device management is not installed on this site."}

	# Retired / lost devices are not a compliance problem to chase.
	active_filter = {"status": ("not in", ["Retired", "Lost/Stolen"])}
	rows = fetch_all(
		"Managed Device",
		filters=active_filter,
		fields=["name", "device_name", "compliance_status", "mdm_last_seen", "assigned_to_employee", "status"],
		order_by="mdm_last_seen asc",
		limit=500,
	)

	counts = {"compliant": 0, "non_compliant": 0, "unknown": 0}
	stale_cutoff = add_days(now_datetime(), -DEVICE_STALE_DAYS)
	stale = []
	never = 0
	for r in rows:
		status = (r.compliance_status or "Unknown").lower().replace("-", "_")
		if status == "compliant":
			counts["compliant"] += 1
		elif status == "non_compliant":
			counts["non_compliant"] += 1
		else:
			counts["unknown"] += 1

		if not r.mdm_last_seen:
			never += 1
		elif r.mdm_last_seen < stale_cutoff:
			stale.append(
				{
					"name": r.name,
					"title": r.device_name or r.name,
					"last_seen": str(r.mdm_last_seen),
					"days": (now_datetime() - r.mdm_last_seen).days,
					"compliance": r.compliance_status or "Unknown",
				}
			)

	total = len(rows)
	return {
		"total": total,
		"counts": counts,
		"compliant_pct": (counts["compliant"] / total * 100.0) if total else None,
		"never_seen": never,
		"stale": stale[:ROW_LIMIT],
		"stale_days": DEVICE_STALE_DAYS,
	}


@frappe.whitelist()
@widget_feed("Operations", "chemistry_alerts")
def get_chemistry_alerts():
	"""Recent visits flagged with out-of-range chemistry readings, newest first.

	``has_out_of_range_readings`` is set by the visit's own chemistry validation,
	so this is the same flag the Out-of-Range Rate KPI counts — the widget is that
	KPI's worklist.
	"""
	since = add_days(getdate(nowdate()), -LOOKBACK_DAYS)
	rows = fetch_all(
		"Sapphire Maintenance Record",
		filters={"has_out_of_range_readings": 1, "scheduled_visit_date": (">=", since)},
		fields=[
			"name",
			"customer",
			"project",
			"visit_label",
			"technician",
			"scheduled_visit_date",
			"workflow_state",
		],
		order_by="scheduled_visit_date desc",
		limit=ROW_LIMIT,
	)

	names = _user_names([r.technician for r in rows])
	visits = [
		{
			"name": r.name,
			"title": r.visit_label or r.customer or r.name,
			"customer": r.customer or "",
			"technician": names.get(r.technician) or r.technician or "",
			"visit_date": str(getdate(r.scheduled_visit_date)) if r.scheduled_visit_date else None,
			"state": r.workflow_state or "",
			"open": r.workflow_state != COMPLETED_STATE,
		}
		for r in rows
	]
	return {"visits": visits, "lookback_days": LOOKBACK_DAYS}


@frappe.whitelist()
@widget_feed("Operations", "labor_capture")
def get_labor_capture():
	"""Completed visits carrying no labour — the under-billing leak.

	A visit counts as a gap when it reached the terminal workflow state without a
	clock-in *and* without any labour cost. Both conditions are required on
	purpose: a visit billed at a flat rate with no kiosk clock-in is legitimate,
	and so is a clocked visit whose cost has not yet been rolled up.
	"""
	since = add_days(getdate(nowdate()), -LOOKBACK_DAYS)
	rows = frappe.db.sql(
		"""
		select name, customer, project, visit_label, technician, scheduled_visit_date
		from `tabSapphire Maintenance Record`
		where workflow_state = %(state)s
		  and scheduled_visit_date >= %(since)s
		  and clock_in_time is null
		  and coalesce(total_labor_cost, 0) = 0
		order by scheduled_visit_date desc
		limit %(limit)s
		""",
		{"state": COMPLETED_STATE, "since": since, "limit": ROW_LIMIT},
		as_dict=True,
	)

	names = _user_names([r.technician for r in rows])
	visits = [
		{
			"name": r.name,
			"title": r.visit_label or r.customer or r.name,
			"customer": r.customer or "",
			"technician": names.get(r.technician) or r.technician or "",
			"visit_date": str(getdate(r.scheduled_visit_date)) if r.scheduled_visit_date else None,
		}
		for r in rows
	]
	return {"visits": visits, "lookback_days": LOOKBACK_DAYS}
