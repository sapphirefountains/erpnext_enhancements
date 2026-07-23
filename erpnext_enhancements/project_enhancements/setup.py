"""Idempotent custom-field provisioning for Project Enhancements.

Registered in ``after_migrate`` (hooks.py). Adds the Project "Timeline" tab —
a Tab Break plus an HTML host field for the embeddable Gantt widget
(``public/js/gantt_widget/``, mounted by
``public/js/project_enhancements/project_timeline_gantt.js``). The legacy
interactive frappe-gantt on the Schedule tab (``custom_gantt_chart_html``) is
deliberately left untouched — this is a parallel, read-only surface.

Insert-only — it never rewrites an existing field — mirroring
``device_management.setup.create_device_employee_fields``; if the fields are
later exported to ``fixtures/custom_field.json`` the fixture owns them and
this becomes a no-op.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_project_timeline_fields():
	"""``after_migrate`` entry point: add the Project "Timeline" tab + Gantt host."""
	if not frappe.db.exists("DocType", "Project"):
		return

	meta = frappe.get_meta("Project")
	# Append as the last tab: anchoring the Tab Break after the form's current
	# last field keeps every existing tab's field grouping intact.
	anchor = meta.fields[-1].fieldname if meta.fields else None

	fields = [
		{
			"fieldname": "custom_timeline_tab",
			"label": "Timeline",
			"fieldtype": "Tab Break",
			"insert_after": anchor,
		},
		{
			"fieldname": "custom_timeline_gantt_html",
			"label": "Project Timeline",
			"fieldtype": "HTML",
			"insert_after": "custom_timeline_tab",
		},
	]
	to_create = [f for f in fields if not meta.has_field(f["fieldname"])]
	if to_create:
		create_custom_fields({"Project": to_create}, update=True)
