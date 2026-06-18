"""Backfill ``sla_business_days`` from the legacy calendar ``sla_hours``.

v1.66.0 moves the PRO-0204 hand-off SLAs from calendar hours to business days
(Mon-Fri, skipping holidays). This converts the existing Process Step Templates
and any already-seeded Project Process Step rows: ``ceil(sla_hours / 24)``
(0 -> 0, 24 -> 1, 48 -> 2). Runs once (post_model_sync) right after the column is
synced, so any site-side SLA tuning happens only afterward and is never clobbered.
"""

import frappe
from frappe.utils import cint


def _to_business_days(hours):
	return (cint(hours) + 23) // 24  # ceil for non-negative


def execute():
	for doctype in ("Process Step Template", "Project Process Step"):
		if not frappe.db.has_column(doctype, "sla_business_days"):
			continue
		for row in frappe.get_all(doctype, fields=["name", "sla_hours"]):
			frappe.db.set_value(
				doctype,
				row.name,
				"sla_business_days",
				_to_business_days(row.sla_hours),
				update_modified=False,
			)
