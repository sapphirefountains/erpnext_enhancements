"""One-time migration: rename the QuickBooks module (pre_model_sync).

The module historically named "QuickBooks Time Integration" is really the
QuickBooks **Online** (QBO) accounting integration. This renames the Module Def
"QuickBooks Time Integration" -> "QuickBooks Online" before the model sync, so
the four QBO doctypes + the dashboard Page (whose JSON now declares module
"QuickBooks Online") reconcile onto the existing records instead of orphaning the
old module. ``frappe.rename_doc`` cascades the ``module`` Link on
tabDocType / tabPage / tabWorkspace.

The new "QuickBooks Time" module (the timesheet webhook) has no doctypes/pages,
so its Module Def is created normally from modules.txt during sync -- nothing to
do here for it.

Idempotent: a no-op on fresh installs (old module absent) and on re-run (already
renamed). The doctype data itself (e.g. the QuickBooks Online Settings Single
with stored OAuth tokens) is keyed by doctype name, which does not change, so no
configuration is lost.
"""
import frappe

OLD = "QuickBooks Time Integration"
NEW = "QuickBooks Online"


def execute():
    if not frappe.db.exists("Module Def", OLD):
        # Fresh install or already renamed.
        return

    if frappe.db.exists("Module Def", NEW):
        # Both present (e.g. NEW was already created by a prior partial sync).
        # Reassign anything still pointing at OLD, then drop the orphan.
        for dt in ("DocType", "Page", "Workspace", "Report"):
            for name in frappe.get_all(dt, filters={"module": OLD}, pluck="name"):
                frappe.db.set_value(dt, name, "module", NEW)
        frappe.delete_doc("Module Def", OLD, force=True, ignore_permissions=True)
        frappe.clear_cache()
        return

    # Clean rename: carries the four QBO doctypes + dashboard Page across.
    frappe.rename_doc("Module Def", OLD, NEW, force=True)
    frappe.clear_cache()
