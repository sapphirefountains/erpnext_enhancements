"""One-time migration patch (post_model_sync; listed in patches.txt).

Adds the **Contracts** tab to the Customer form — the same HTML host the Project
form carries, rendered by ``public/js/project_enhancements/contracts_tab.js``
into the list of every contract this customer is a party to (agreements and the
operational maintenance contracts beside them), each openable in place.

Placed after the Accounting tab, where the commercial reading of a customer
already lives. The anchor is resolved from the live meta, so a site whose
Accounting tab has been rearranged still lands the tab in the right place rather
than at the end of the form.

Sibling of ``add_project_contracts_tab``; separate because that one has already
run on live sites and a patch does not run twice.

Idempotent via ``create_custom_fields(..., update=True)``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

HOST_TAB = "accounting_tab"


def _anchor():
	"""The fieldname the Contracts tab should follow."""
	meta = frappe.get_meta("Customer")
	fields = meta.fields
	start = None
	for i, df in enumerate(fields):
		if df.fieldname == HOST_TAB:
			start = i
			break
	if start is None:
		return fields[-1].fieldname if fields else None

	# Walk to the field just before the next tab.
	end = len(fields)
	for i in range(start + 1, len(fields)):
		if fields[i].fieldtype == "Tab Break":
			end = i
			break
	return fields[end - 1].fieldname


def execute():
	if not frappe.db.exists("DocType", "Customer"):
		return

	anchor = _anchor()
	if not anchor:
		return

	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": "custom_contracts_tab",
					"label": "Contracts",
					"fieldtype": "Tab Break",
					"insert_after": anchor,
				},
				{
					"fieldname": "custom_contracts_html",
					"label": "Contracts",
					"fieldtype": "HTML",
					"insert_after": "custom_contracts_tab",
				},
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Customer")
