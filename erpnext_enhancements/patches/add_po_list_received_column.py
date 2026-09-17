"""Add `% Received` to the pinned Purchase Order list columns, after the status pill (v1.479.0).

ER-2026-458194 asked to "keep track of what we're still waiting for", and the list is where
the buyer does that: their saved views filter on `status = To Receive and Bill` and a stage
other than Received. The pill says an order is still to receive; `per_received` says how much.

**Why a new patch and not a re-run of `seed_po_list_columns`.** That patch, by design, leaves
an existing `List View Settings` row alone once it names the stage — the row is someone's
answer, and rewriting it wholesale would erase columns a System Manager added through the
Desk. Production has exactly that row (written by the seed in v1.336.0), so re-running the
seed is a no-op there and the column reaches nobody. This is the same shape, one column
narrower in what it claims: insert `per_received` directly after `status_field` only when the
row does not already carry it, and touch nothing else. Appended if there is no pill to sit
beside — last is still visible, and guessing a better position in a column set nobody here
chose would be worse than the honest fallback.

A site with no row at all gets the full spec from `po_order_stage.list_view_columns()`, which
already carries the column. On a fresh install the seed runs first and writes that, so this
branch is completeness rather than a path production takes. Safe twice.
"""

import json

import frappe

from erpnext_enhancements.po_order_stage import RECEIVED_COLUMN, list_view_columns

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
	if any(column.get("fieldname") == RECEIVED_COLUMN for column in existing):
		frappe.logger().info("Purchase Order list columns already carry % Received; left alone")
		return

	received = next(column for column in columns if column["fieldname"] == RECEIVED_COLUMN)
	position = next(
		(index + 1 for index, column in enumerate(existing) if column.get("fieldname") == "status_field"),
		len(existing),
	)
	existing.insert(position, received)
	doc.fields = json.dumps(existing)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.logger().info(f"% Received added to the existing Purchase Order list columns at {position}")
