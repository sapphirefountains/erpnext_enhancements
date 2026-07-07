# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Backfill the v1.142.0 issue/readiness fields on existing Water Feature Designs.

The typed-issues mapper derives everything from PERSISTED state (child-row
velocity statuses + the calc_results audit rows), so no engine re-run happens —
submitted designs keep their numbers byte-identical, and ``db_set`` writes the
denormalized counters without touching ``modified`` or firing validate.

Per-row ``pressure_status`` cells stay empty until a design's next real save;
the doc-level pressure envelope in calc_results still yields the issue, so the
counters are correct from day one.
"""

import json

import frappe


def execute():
	if not frappe.db.exists("DocType", "Water Feature Design"):
		return
	if not frappe.db.has_column("Water Feature Design", "design_issues_json"):
		return

	from erpnext_enhancements.water_engineering.issues import (
		build_issues,
		build_readiness,
		summarize,
	)

	for name in frappe.get_all("Water Feature Design", pluck="name"):
		try:
			doc = frappe.get_doc("Water Feature Design", name)
			issues = build_issues(doc)
			readiness = build_readiness(doc, issues)
			counts = summarize(issues)
			doc.db_set(
				{
					"design_issues_json": json.dumps(issues),
					"readiness_json": json.dumps(readiness),
					"blocker_count": counts["blocker_count"],
					"warning_count": counts["warning_count"],
					"issue_summary": counts["summary"],
					"issue_ready": 1 if readiness.get("issue_ready") else 0,
					"has_warnings": 1 if (counts["blocker_count"] or counts["warning_count"]) else 0,
				},
				update_modified=False,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"WFD issue backfill: {name}")
	frappe.db.commit()
