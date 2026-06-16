"""One-time migration: reassign the Google Drive surfaces to the new Google
Drive module (post_model_sync backstop).

PR 6 of the module reorganization moves the Drive doctypes (Drive Link Candidate,
Drive Sync Log, Drive Folder Template Item, Project Folder Google Drive Settings)
and the Drive Link Manager desk page out of CRM Enhancements into the new
``google_drive`` module. The JSONs declare ``module: Google Drive`` and sync from
``google_drive/``, so model sync already reassigns them; this is the explicit,
idempotent backstop.

No data moves -- records are keyed by name; the Project Folder Google Drive
Settings Single (service-account JSON, shared drive id) carries across unchanged.
Idempotent: a no-op once everything already reads "Google Drive".
"""
import frappe

DOCTYPES = (
    "Drive Link Candidate",
    "Drive Sync Log",
    "Drive Folder Template Item",
    "Project Folder Google Drive Settings",
)
PAGES = ("drive-link-manager",)
NEW = "Google Drive"


def execute():
    if not frappe.db.exists("Module Def", NEW):
        # modules.txt sync creates the Module Def; nothing to reassign onto yet.
        return
    for dt in DOCTYPES:
        if frappe.db.exists("DocType", dt):
            frappe.db.set_value("DocType", dt, "module", NEW)
    for pg in PAGES:
        if frappe.db.exists("Page", pg):
            frappe.db.set_value("Page", pg, "module", NEW)
    frappe.clear_cache()
