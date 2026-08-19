"""Pin the Purchase Order list columns, with Order Stage beside the status pill (v1.336.0).

ER-2026-276347 asked for the stage to be readable *in the list*, in the column the reporter
circled. `in_list_view` was already `1` on the field and had been since v1.328.0 — it is
necessary and it is not sufficient. Frappe derives the default column set from **field
order**, and a custom field sorts after every standard one, so the flag alone put Order
Stage last, behind Grand Total and the two percent columns. Nothing about the flag can move
it; the only lever on column order is a `List View Settings` row.

`Purchase Order` had none, so this writes one from `po_order_stage.list_view_columns()`.

**Writing that row is subtractive as well as ordering.** `reorder_listview_fields` keeps
only the columns the row names and drops every other one, so the list is now exactly what
the spec says — which is why the spec reproduces the five columns the list already showed
and adds a sixth. This is meant to add Order Stage and take nothing away.

**It does not overwrite a row somebody else configured.** There is none today (checked on
production: the only `List View Settings` on the site is `Item`), but this app is not the
only thing that writes them — the Desk's own List Settings dialog does, from a menu any
System Manager can reach. If a row exists and already names the stage, that arrangement is
someone's answer and is left alone; if it exists without the stage, the stage is inserted
before the status pill rather than the whole row being replaced. Safe twice.
"""

import json

import frappe

from erpnext_enhancements.po_order_stage import FIELD, list_view_columns

SETTINGS = "List View Settings"
DOCTYPE = "Purchase Order"


def execute():
	columns = list_view_columns()

	if not frappe.db.exists(SETTINGS, DOCTYPE):
		doc = frappe.new_doc(SETTINGS)
		doc.name = DOCTYPE
		doc.fields = json.dumps(columns)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info(f"Purchase Order list columns seeded: {[c['fieldname'] for c in columns]}")
		return

	doc = frappe.get_doc(SETTINGS, DOCTYPE)
	existing = frappe.parse_json(doc.fields or "[]") or []
	if any(column.get("fieldname") == FIELD for column in existing):
		frappe.logger().info("Purchase Order list columns already name the order stage; left alone")
		return

	stage = next(column for column in columns if column["fieldname"] == FIELD)
	# Beside the status pill, which is what was asked for. Appended only if this list has
	# no pill at all -- last is still visible, and guessing a better position from a column
	# set nobody here chose would be worse than the honest fallback.
	position = next(
		(index for index, column in enumerate(existing) if column.get("fieldname") == "status_field"),
		len(existing),
	)
	existing.insert(position, stage)
	doc.fields = json.dumps(existing)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.logger().info(f"Order stage added to the existing Purchase Order list columns at {position}")
