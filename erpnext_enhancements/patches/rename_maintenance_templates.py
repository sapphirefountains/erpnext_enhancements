"""Rename Sapphire Maintenance Templates from hash IDs to their friendly name.

The doctype originally had no ``autoname``, so every template got an opaque
``hash`` name (e.g. ``a1b2c3d4e5``) — unreadable in link fields, the contract
form, and service plans. The doctype now uses ``autoname: field:template_name``;
this one-time patch renames the pre-existing rows to match. ``frappe.rename_doc``
cascades the new name through every link field (contract ``default_template``,
feature ``template``, seasonal templates, service-plan templates, the record's
``template``), so nothing is orphaned.

Idempotent: a row already named after its template_name, or one whose target
name is taken, is left untouched.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Sapphire Maintenance Template"):
		return

	for name, template_name in frappe.get_all(
		"Sapphire Maintenance Template", fields=["name", "template_name"], as_list=True
	):
		if not template_name or name == template_name:
			continue
		if frappe.db.exists("Sapphire Maintenance Template", template_name):
			# A collision (two templates share a name) — leave the hash name so
			# the rename can't merge two distinct templates. Resolve by hand.
			frappe.log_error(
				f"Cannot rename template {name} to {template_name!r}: name already taken.",
				"Maintenance Template rename skipped",
			)
			continue
		frappe.rename_doc(
			"Sapphire Maintenance Template", name, template_name, force=True, show_alert=False
		)
