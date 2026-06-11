"""Delete the orphaned "Sapphire Historical Visit" virtual child DocType.

The Maintenance Record's ``historical_visits`` field was a Table pointing at
this ``is_virtual`` + ``istable`` child doctype, with rows computed on read by a
``cached_property``. That hack did not reliably shadow Frappe's child-table
loader: ``has_permission(..., docname)`` -> ``has_user_permission`` walks
``get_all_children(include_computed=True)`` and SQL-loaded the field, hitting
``Table 'tabSapphire Historical Visit' doesn't exist`` for any user with User
Permissions. The recent-visits panel is already served independently by
``get_dashboard_context().visits``, so the field and doctype were removed.

Removing the field from the parent is what stops the crash; this post-model-sync
patch clears the now-orphaned child-doctype metadata. The doctype is virtual, so
there is no data table to drop.

Idempotent and fresh-install-safe: guarded by an existence check (the doctype
never exists on a new site).
"""

import frappe


def execute():
	if frappe.db.exists("DocType", "Sapphire Historical Visit"):
		frappe.delete_doc("DocType", "Sapphire Historical Visit", force=True, ignore_permissions=True)
