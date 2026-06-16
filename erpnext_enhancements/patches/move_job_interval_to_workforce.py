"""One-time migration: reassign Job Interval to the Workforce module.

Follow-up to the Workforce module (PR 4): the **Job Interval** doctype -- the
Time Kiosk clock-in *session* -- moves ``enhancements_core`` -> ``workforce`` to
sit with the other time-tracking doctypes (Time Kiosk Log / Settings). The JSON
now declares ``module: Workforce`` and is synced from ``workforce/``, so model
sync already reassigns it; this is the explicit, idempotent backstop.

No data moves -- records are keyed by name, only the ``module`` Link changes.
Idempotent: a no-op once it already reads "Workforce".
"""
import frappe


def execute():
    if frappe.db.exists("DocType", "Job Interval") and frappe.db.exists("Module Def", "Workforce"):
        frappe.db.set_value("DocType", "Job Interval", "module", "Workforce")
        frappe.clear_cache()
