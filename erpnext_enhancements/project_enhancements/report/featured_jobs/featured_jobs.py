# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Featured Jobs — work flagged for story-based marketing content.

The point of this report is **timing**, not inventory. Marketing's problem is not
that good jobs are unrecorded; it is that nobody knows a job is worth
photographing until after the crew has left and the site is finished. A story
needs the before, the mess in the middle and the after, and two of those three
are unrecoverable the moment the truck pulls away.

So the default sort is by **start date ascending, upcoming first** — the report
answers "what should a photographer be booked for", not "what did we do". Jobs
already under way appear at the top of the past section rather than being hidden,
because a job in progress can still be caught halfway.

The photo count column is the reality check: a job flagged three weeks ago with
zero photos means the arrangement did not happen, and that is exactly the thing
worth seeing on a Monday morning.

Flagged via ``Project.custom_feature_this_job`` (WP-3). Both that field and the
photo table may be absent on a bench that has not migrated, so both are probed
before use rather than assumed.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not _flag_exists():
		return get_columns(), [], _("The Feature This Job field is not installed on this site yet."), None
	return get_columns(), get_data(filters), None, None


def _flag_exists():
	try:
		return frappe.db.has_column("Project", "custom_feature_this_job")
	except Exception:
		return False


def _photos_available():
	try:
		return frappe.db.table_exists("Job Interval Photo")
	except Exception:
		return False


def get_columns():
	return [
		{"label": _("When"), "fieldname": "timing", "fieldtype": "Data", "width": 110},
		{"label": _("Project"), "fieldname": "name", "fieldtype": "Link", "options": "Project", "width": 200},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 200},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Starts"), "fieldname": "start_date", "fieldtype": "Date", "width": 110},
		{"label": _("Ends"), "fieldname": "end_date", "fieldtype": "Date", "width": 110},
		{"label": _("Photos So Far"), "fieldname": "photo_count", "fieldtype": "Int", "width": 120},
		{"label": _("Feature Note"), "fieldname": "custom_feature_note", "fieldtype": "Data", "width": 340},
	]


def get_data(filters):
	conditions = ["COALESCE(p.custom_feature_this_job, 0) = 1"]
	values = {"today": nowdate()}

	if filters.get("customer"):
		conditions.append("p.customer = %(customer)s")
		values["customer"] = filters.customer
	if filters.get("upcoming_only"):
		conditions.append(
			"(COALESCE(p.expected_end_date, p.expected_start_date) IS NULL "
			" OR COALESCE(p.expected_end_date, p.expected_start_date) >= %(today)s)"
		)

	photo_select = (
		"""(SELECT COUNT(*) FROM `tabJob Interval Photo` ph
		    INNER JOIN `tabJob Interval` ji ON ji.name = ph.parent
		    WHERE ji.project = p.name AND ph.parenttype = 'Job Interval'
		      AND ph.upload_status IN ('Pending', 'Uploaded')) AS photo_count"""
		if _photos_available()
		else "0 AS photo_count"
	)

	note_select = (
		"p.custom_feature_note"
		if frappe.db.has_column("Project", "custom_feature_note")
		else "'' AS custom_feature_note"
	)

	rows = frappe.db.sql(
		f"""
		SELECT
			p.name,
			p.customer,
			p.status,
			COALESCE(p.custom_project_start_date, p.expected_start_date) AS start_date,
			COALESCE(p.custom_project_end_date, p.expected_end_date) AS end_date,
			{note_select},
			{photo_select}
		FROM `tabProject` p
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)

	today = getdate(nowdate())
	for row in rows:
		row["timing"] = _timing(row, today)

	# Upcoming first (soonest at the top), then in-progress, then finished
	# (most recent first). A plain date sort would bury next week's job under a
	# decade of completed work.
	def sort_key(row):
		bucket = {_("Upcoming"): 0, _("In progress"): 1}.get(row["timing"], 2)
		date = row.get("start_date") or row.get("end_date")
		if bucket == 2:
			return (bucket, -(getdate(date).toordinal() if date else 0))
		return (bucket, getdate(date).toordinal() if date else 0)

	rows.sort(key=sort_key)
	return rows


def _timing(row, today):
	start = getdate(row["start_date"]) if row.get("start_date") else None
	end = getdate(row["end_date"]) if row.get("end_date") else None

	if start and start > today:
		return _("Upcoming")
	if start and start <= today and (not end or end >= today):
		return _("In progress")
	if end and end < today:
		return _("Finished")
	return _("Undated")
