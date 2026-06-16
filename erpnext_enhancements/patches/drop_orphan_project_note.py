"""Drop the orphaned 'Project Note' child-table doctype.

'Project Note' (singular, ``istable``) was a leftover child table: no Table field
anywhere references it (a repo-wide grep for ``"options": "Project Note"`` is
empty). The in-use project-notes child table is 'Project Notes' (plural), on the
Project Custom Field. This removes the dead DocType and its table now that the
JSON has been deleted from the app.

Defensive: a child table with no parent Table field cannot receive data through
the UI, but if ``tabProject Note`` somehow holds rows we skip and log rather than
silently drop them. Idempotent: a no-op once the DocType is gone (including fresh
installs, where the JSON was removed so it was never created).
"""
import frappe

ORPHAN = "Project Note"


def execute():
    if not frappe.db.exists("DocType", ORPHAN):
        return
    if frappe.db.table_exists(ORPHAN) and frappe.db.count(ORPHAN):
        frappe.log_error(
            f"Skipped dropping orphan DocType '{ORPHAN}': tab{ORPHAN} has rows; "
            "investigate before removing.",
            "Project Note cleanup",
        )
        return
    frappe.delete_doc("DocType", ORPHAN, force=True, ignore_permissions=True)
    frappe.db.commit()
