# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Operations Dashboard widgets.

Device fleet compliance, live. The three maintenance worklists that used to sit
here (today's visit board, out-of-range chemistry, labour-capture gaps) moved to
``api/service_dashboard.py`` in v1.530.0, when maintenance became its own Service
department and Operations became the inventory dashboard.

Gating is the shared department contract in ``api/dashboard_widgets.py``:
Operations roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.

``Managed Device`` only exists where the MDM integration is installed, so it is
guarded with ``frappe.db.exists("DocType", ...)`` and the widget reports "not in
use" instead of exploding a dashboard — the same defensive stance the KPI
aggregators take.
"""

import frappe
from frappe.utils import add_days, now_datetime

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 25

# A device whose provider check-in is older than this is treated as out of
# contact — matching the Device Provider Sync Freshness KPI's intent.
DEVICE_STALE_DAYS = 7


def _doctype_exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


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
