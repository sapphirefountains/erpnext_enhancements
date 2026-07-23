"""Remove the Project "Timeline" tab custom fields shipped in v1.163.0.

v1.163.0's ``after_migrate`` setup created a "Timeline" tab on Project
(``custom_timeline_tab`` Tab Break + ``custom_timeline_gantt_html`` HTML
host) for the embeddable Gantt widget's first embed. Review moved that embed
into the Schedule tab's existing ``custom_gantt_chart_html`` field, so the
tab is retired: the setup module is deleted and this patch drops the two
fields the v1.163.0 deploy already created on prod/test. Idempotent — a
bench that never ran v1.163.0 simply no-ops. The fields were created
``is_system_generated``, so the Custom Field fixtures never exported them
and cannot recreate them.
"""

import frappe

FIELD_NAMES = (
	"Project-custom_timeline_tab",
	"Project-custom_timeline_gantt_html",
)


def execute():
	for name in FIELD_NAMES:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
	frappe.clear_cache(doctype="Project")
