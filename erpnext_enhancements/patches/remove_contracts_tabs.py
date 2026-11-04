"""Delete the Contracts tab's Custom Fields from Project and Customer (v1.783.0).

Nikolas, 2026-11-03: "Remove the contract tab in Project DocType and just make that button the
new primary way to view and create contracts." The tab was two Custom Fields on each form — a
Tab Break and the HTML host ``contracts_tab.js`` rendered into — created by
``add_project_contracts_tab`` (v1.181.0) and ``add_customer_contracts_tab`` (v1.182.0). Neither
field was ever in ``fixtures/custom_field.json`` (``create_custom_fields`` marks them
``is_system_generated``, which the export skips), so stopping the patches is not enough: the
rows are on every site that migrated since, and only a delete removes them. The Contract
button (``public/js/project_enhancements/contract_button.js``) takes over.

Idempotent — a field already gone is skipped — and it **cannot raise**: a patch that raises
aborts ``bench migrate``, which on this repo is the deploy. A field that will not delete is
logged by name and the rest still go. The two source patches stay in ``patches.txt`` because a
patch never runs twice; this one is listed after them.
"""

import frappe

FIELD_NAMES = (
	"Project-custom_contracts_tab",
	"Project-custom_contracts_html",
	"Customer-custom_contracts_tab",
	"Customer-custom_contracts_html",
)


def execute():
	touched = set()
	for name in FIELD_NAMES:
		try:
			if not frappe.db.exists("Custom Field", name):
				continue
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			touched.add(name.split("-", 1)[0])
		except Exception:
			frappe.log_error(
				title=f"remove_contracts_tabs: {name} not deleted",
				message=frappe.get_traceback(),
			)
	for doctype in touched:
		frappe.clear_cache(doctype=doctype)
