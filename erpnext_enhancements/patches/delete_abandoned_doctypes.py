"""Delete abandoned DB-only custom DocTypes (and a superseded Client Script).

One-shot cleanup approved alongside the v0.8.0 doctype ports. The three
DocTypes were created via the UI, exist in no app, and are referenced by
nothing — no DocField, Custom Field, script, or repo code points at them:

* "Materials" (child table, 0 rows)
* "Rental Status" (0 rows)
* "Water Feature Types" (child table, 1 orphan row; superseded by the Serial
  No fields introduced in the migrate_assets_to_serial_no migration)

Note: on Frappe 16, deleting a DocType removes only its metadata (tabDocType
row + DocField/DocPerm children, with a Deleted Document record keeping the
meta JSON); the data table is left behind orphaned, recoverable until a
``bench trim-database`` removes it. Sign-off recorded in the v0.8.0 CHANGELOG
entry. Also deletes the disabled "Mermaid.js Render" Client Script, superseded
by the app's public/js/process_document.js.

Idempotent and fresh-install-safe: every target is guarded by an existence
check (none of these ever exist on a new site).
"""

import frappe


def execute():
	for name in ("Materials", "Rental Status", "Water Feature Types"):
		if frappe.db.exists("DocType", name):
			frappe.delete_doc("DocType", name, force=True, ignore_permissions=True)

	if frappe.db.exists("Client Script", "Mermaid.js Render"):
		frappe.delete_doc("Client Script", "Mermaid.js Render", force=True, ignore_permissions=True)
