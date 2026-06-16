"""One-time migration: reassign the Time Kiosk surfaces to the Workforce module.

PR 4 of the module reorganization moves two doctypes (Time Kiosk Log, Time Kiosk
Settings) and two desk pages (time-kiosk, location-timeline) out of Enhancements
Core into the new Workforce module. The JSONs now declare ``module: Workforce``
and are synced from ``workforce/``, so model sync already reassigns them; this
patch is an explicit, idempotent backstop that guarantees the final module on
existing installs (doctypes and Pages alike).

No data moves -- records are keyed by name, only the ``module`` Link changes.
Idempotent: a no-op once everything already reads "Workforce" (and on fresh
installs, where the records are created under Workforce directly).
"""
import frappe

DOCTYPES = ("Time Kiosk Log", "Time Kiosk Settings")
PAGES = ("time-kiosk", "location-timeline")
NEW = "Workforce"


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
