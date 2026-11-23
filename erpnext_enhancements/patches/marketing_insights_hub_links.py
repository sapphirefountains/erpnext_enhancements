"""Re-import the Marketing Hub for v1.757.0's reports and records.

Its Reports card gains the six marketing reports (Inquiry Sources, Search Queries, Near Page One,
Fading Pages, Service Line Funnel, Missed Calls) and its Campaigns card Marketing Event, Tracked Link
and Business Profile Review. The JSON's ``modified`` is bumped, which is enough where the stored row is
older; ``import_file_by_path(force=True)`` covers a row somebody edited more recently. Not
``reload_doc``: its first argument is a module, and the hub declares none. Logs a failure, never
raises, safe twice.
"""

import frappe


def execute():
	from frappe.modules.import_file import import_file_by_path

	try:
		path = frappe.get_app_path(
			"erpnext_enhancements", "enhancements_core", "workspace", "marketing_hub", "marketing_hub.json"
		)
		import_file_by_path(path, force=True, reset_permissions=False)
		frappe.db.commit()
		frappe.clear_cache()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title="Marketing insights",
			message=f"Could not re-import the marketing_hub workspace\n{frappe.get_traceback()}",
		)
