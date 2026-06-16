"""One-time migration: retire the legacy "QuickBooks Time Integration" module
(post_model_sync).

The QuickBooks module was renamed to "QuickBooks Online" (plus a thin "QuickBooks
Time" module). App-owned (non-custom) Module Defs **cannot be renamed** via the
controller (``frappe.rename_doc`` raises "Only Custom Modules can be renamed"), so
the original rename approach failed at migrate.

Instead this runs AFTER model sync -- by which point sync has created the
"QuickBooks Online" Module Def from modules.txt and the four QBO doctypes + the
dashboard Page have reconciled to it via their JSON ``module`` field -- and simply
drops the orphaned legacy Module Def (reassigning any stragglers first).

No data is lost: doctypes are keyed by name; only the module link changes (done by
sync). The QuickBooks Online Settings Single (OAuth tokens etc.) carries across.
Idempotent: a no-op on fresh installs and on re-run.
"""
import frappe

OLD = "QuickBooks Time Integration"
NEW = "QuickBooks Online"


def execute():
    if not frappe.db.exists("Module Def", OLD):
        return
    if not frappe.db.exists("Module Def", NEW):
        # NEW is created from modules.txt during model sync; if it isn't there yet,
        # leave OLD in place rather than orphan its doctypes (a later migrate
        # completes it).
        return

    # Backstop: reassign anything still pointing at the legacy module (model sync
    # already moved the QBO doctypes/page via their JSON).
    for dt in ("DocType", "Page", "Report", "Workspace"):
        for name in frappe.get_all(dt, filters={"module": OLD}, pluck="name"):
            frappe.db.set_value(dt, name, "module", NEW)

    # Drop the orphaned legacy Module Def. App-owned modules can't be renamed or
    # removed via the controller (and its on_trash would try to delete the already-
    # renamed folder), so delete the row directly.
    frappe.db.delete("Module Def", {"name": OLD})
    frappe.clear_cache()
