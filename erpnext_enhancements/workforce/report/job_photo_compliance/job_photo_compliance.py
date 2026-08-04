# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Job Photo Compliance — which closed jobs have photos, and which do not.

The operational counterpart to the capture gate. The gate stops an interval
closing without either a photo or a reason; this report is how anybody finds out
what actually happened afterwards, across the crew and over time.

Three states worth separating, because they call for three different responses:

* **Missing** — closed with no photo and no reason. Only possible for intervals
  that closed while the gate was off, or before it existed. A process gap.
* **Skipped** — closed with a recorded reason. Not a failure; a data point. If
  the same reason recurs across one site or one technician, that is worth
  knowing, which is why the reason text is a column rather than a flag.
* **Pending upload** — photographed, bytes still on the device. Not a compliance
  problem at all, and deliberately not counted as one. It is here so somebody can
  tell the difference between "no photo was taken" and "a photo was taken and the
  site has no signal" — a distinction that decides whether you talk to the
  technician or to the carrier.

Default window is the last 30 days, so opening the report costs one indexed
range scan rather than a full-table sweep.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not _photo_fields_exist():
		# Fixtures not applied on this bench. An empty report beats a 500.
		return get_columns(), [], _("Job photo fields are not installed on this site yet."), None

	data = get_data(filters)
	return get_columns(), data, None, get_chart(data)


def _photo_fields_exist():
	try:
		return frappe.db.has_column("Job Interval", "photo_status")
	except Exception:
		return False


def get_columns():
	return [
		{"label": _("Result"), "fieldname": "result", "fieldtype": "Data", "width": 130},
		{"label": _("Interval"), "fieldname": "name", "fieldtype": "Link", "options": "Job Interval", "width": 150},
		{"label": _("Employee"), "fieldname": "employee_name", "fieldtype": "Data", "width": 170},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 170},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 180},
		{"label": _("Closed"), "fieldname": "end_time", "fieldtype": "Datetime", "width": 160},
		{"label": _("Photos"), "fieldname": "photo_count", "fieldtype": "Int", "width": 80},
		{"label": _("Pending"), "fieldname": "pending_count", "fieldtype": "Int", "width": 80},
		{"label": _("Skip Reason"), "fieldname": "photo_skip_reason", "fieldtype": "Data", "width": 300},
	]


def get_data(filters):
	conditions = ["ji.status = 'Completed'"]
	values = {}

	values["from_date"] = filters.get("from_date") or add_days(nowdate(), -30)
	conditions.append("ji.end_time >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("DATE(ji.end_time) <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("employee"):
		conditions.append("ji.employee = %(employee)s")
		values["employee"] = filters.employee
	if filters.get("project"):
		conditions.append("ji.project = %(project)s")
		values["project"] = filters.project

	rows = frappe.db.sql(
		f"""
		SELECT
			ji.name,
			ji.employee,
			e.employee_name,
			ji.project,
			p.customer,
			ji.end_time,
			ji.photo_status,
			ji.photo_skip_reason,
			(SELECT COUNT(*) FROM `tabJob Interval Photo` ph
			  WHERE ph.parent = ji.name AND ph.parenttype = 'Job Interval'
			    AND ph.upload_status IN ('Pending', 'Uploaded')) AS photo_count,
			(SELECT COUNT(*) FROM `tabJob Interval Photo` ph
			  WHERE ph.parent = ji.name AND ph.parenttype = 'Job Interval'
			    AND ph.upload_status IN ('Pending', 'Failed')) AS pending_count
		FROM `tabJob Interval` ji
		LEFT JOIN `tabEmployee` e ON e.name = ji.employee
		LEFT JOIN `tabProject` p ON p.name = ji.project
		WHERE {" AND ".join(conditions)}
		ORDER BY ji.end_time DESC
		""",
		values,
		as_dict=True,
	)

	out = []
	for row in rows:
		row["result"] = _classify(row)
		if filters.get("only_problems") and row["result"] not in (_("Missing"),):
			continue
		out.append(row)
	return out


def _classify(row):
	if cint(row.get("photo_count")):
		return _("Pending upload") if cint(row.get("pending_count")) else _("Photographed")
	if (row.get("photo_status") or "") == "Skipped":
		return _("Skipped")
	return _("Missing")


def get_chart(data):
	counts = {}
	for row in data:
		counts[row["result"]] = counts.get(row["result"], 0) + 1
	labels = sorted(counts)
	return {
		"data": {"labels": labels, "datasets": [{"name": _("Intervals"), "values": [counts[l] for l in labels]}]},
		"type": "percentage",
	}
