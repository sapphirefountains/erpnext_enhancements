"""One-time migration patch (post_model_sync; listed in patches.txt).

Adds the Project form's **Contracts** tab — a single HTML host rendered by
``public/js/project_enhancements/project_contracts.js`` into the list of every
agreement issued on the job, each openable in place to its full legal text.

Placed between Budget and Costing (``insert_after`` the Budget tab's last
field), where the commercial reading of a project already lives.

Idempotent via ``create_custom_fields(..., update=True)``; the anchor is
resolved from the live meta so a site whose Budget tab has been rearranged
still lands the tab in the right place rather than at the end of the form.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Where the tab goes: immediately after the Budget tab, i.e. after its last
# field. Falls back to the tab break itself if the Budget tab is empty, and to
# the form's last field if the site has no Budget tab at all.
BUDGET_TAB = "custom_budget"


def _anchor():
	"""The fieldname the Contracts tab should follow."""
	meta = frappe.get_meta("Project")
	fields = meta.fields
	start = next((i for i, df in enumerate(fields) if df.fieldname == BUDGET_TAB), None)
	if start is None:
		return fields[-1].fieldname if fields else None
	# Walk to the field just before the next tab (usually costing_tab).
	end = next(
		(i for i in range(start + 1, len(fields)) if fields[i].fieldtype == "Tab Break"),
		len(fields),
	)
	return fields[end - 1].fieldname


def execute():
	if not frappe.db.exists("DocType", "Project"):
		return

	anchor = _anchor()
	if not anchor:
		return

	create_custom_fields(
		{
			"Project": [
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
	frappe.clear_cache(doctype="Project")
