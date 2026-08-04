# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Job Photo Library — the marketing-facing browse view over field photography.

The acceptance criterion for WP-3 is that a marketing user who knows nothing
about a job can find its photos. That rules out the obvious implementations:
the File list is unfiltered by anything they care about, and the Job Interval
list requires knowing which technician clocked onto which project on which day.

So this is a flat, filterable index — project, customer, value stream, date range
— with a thumbnail in every row. No knowledge of the field operation required.

**Value stream filtering is the awkward part**, and the awkwardness is inherited.
The master taxonomy is the doctype called ``Value Streams`` (plural, 6 rows:
Design, Build, Service, Events, Delivery, Products) and the child table that
attaches it to records is called ``Value Stream`` (singular, ~1,460 rows). The
names are inverted from frappe convention and both are in daily use, so the join
below reads backwards on purpose — filtering ``Project.custom_value_stream``
means selecting rows from the SINGULAR child table whose ``value_stream`` Link
points at a PLURAL master record. Do not "fix" the join without reading the
duplication findings in ``docs/``.

Only photos whose bytes have actually arrived are listed: a Pending row has
nothing to show, and a library that renders broken thumbnails teaches people it
is broken. ``Job Photo Compliance`` is where pending uploads are visible.
"""

import frappe
from frappe import _
from frappe.utils import add_days, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not _photo_table_exists():
		return get_columns(), [], _("Job photo storage is not installed on this site yet."), None
	return get_columns(), get_data(filters), None, None


def _photo_table_exists():
	try:
		return frappe.db.table_exists("Job Interval Photo")
	except Exception:
		return False


def get_columns():
	return [
		{"label": _("Photo"), "fieldname": "thumbnail", "fieldtype": "HTML", "width": 110},
		{"label": _("Captured"), "fieldname": "captured_on", "fieldtype": "Datetime", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 200},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 200},
		{"label": _("Value Streams"), "fieldname": "value_streams", "fieldtype": "Data", "width": 180},
		{"label": _("Caption"), "fieldname": "caption", "fieldtype": "Data", "width": 220},
		{"label": _("Photographer"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
		{"label": _("File"), "fieldname": "file_doc", "fieldtype": "Link", "options": "File", "width": 130},
	]


def get_data(filters):
	conditions = ["ph.upload_status = 'Uploaded'", "ph.parenttype = 'Job Interval'"]
	values = {}

	values["from_date"] = filters.get("from_date") or add_days(nowdate(), -365)
	conditions.append("COALESCE(ph.captured_on, ji.start_time) >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("DATE(COALESCE(ph.captured_on, ji.start_time)) <= %(to_date)s")
		values["to_date"] = filters.to_date
	if filters.get("customer"):
		conditions.append("p.customer = %(customer)s")
		values["customer"] = filters.customer
	if filters.get("project"):
		conditions.append("ji.project = %(project)s")
		values["project"] = filters.project
	if filters.get("value_stream"):
		# See the module docstring: SINGULAR child table, PLURAL master.
		conditions.append(
			"""EXISTS (SELECT 1 FROM `tabValue Stream` vs
			           WHERE vs.parent = ji.project AND vs.parenttype = 'Project'
			             AND vs.value_stream = %(value_stream)s)"""
		)
		values["value_stream"] = filters.value_stream
	if filters.get("featured_only"):
		conditions.append("COALESCE(p.custom_feature_this_job, 0) = 1")

	rows = frappe.db.sql(
		f"""
		SELECT
			ph.image,
			ph.file_doc,
			ph.caption,
			COALESCE(ph.captured_on, ji.start_time) AS captured_on,
			ji.project,
			p.customer,
			e.employee_name
		FROM `tabJob Interval Photo` ph
		INNER JOIN `tabJob Interval` ji ON ji.name = ph.parent
		LEFT JOIN `tabProject` p ON p.name = ji.project
		LEFT JOIN `tabEmployee` e ON e.name = ji.employee
		WHERE {" AND ".join(conditions)}
		ORDER BY captured_on DESC
		LIMIT 500
		""",
		values,
		as_dict=True,
	)

	streams = _streams_by_project({r.project for r in rows if r.project})
	for row in rows:
		row["value_streams"] = ", ".join(streams.get(row.project, []))
		row["thumbnail"] = _thumbnail(row.image)
	return rows


def _streams_by_project(projects):
	"""One query for every project on the page rather than one per row."""
	if not projects:
		return {}
	rows = frappe.get_all(
		"Value Stream",
		filters={"parent": ["in", list(projects)], "parenttype": "Project"},
		fields=["parent", "value_stream"],
		limit=2000,
	)
	out = {}
	for row in rows:
		if row.value_stream:
			out.setdefault(row.parent, []).append(row.value_stream)
	return out


def _thumbnail(image_url):
	if not image_url:
		return ""
	escaped = frappe.utils.escape_html(image_url)
	return (
		f'<a href="{escaped}" target="_blank" rel="noopener">'
		f'<img src="{escaped}" style="height:64px;width:64px;object-fit:cover;border-radius:4px;" '
		f'loading="lazy" alt=""></a>'
	)
