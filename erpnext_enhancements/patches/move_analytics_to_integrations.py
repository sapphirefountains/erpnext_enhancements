"""One-time migration: reassign the GA4 / Integrations Health surfaces to the
new Integrations module (post_model_sync backstop).

PR 5 of the module reorganization moves the **GA4 Settings** doctype and the
**GA4 Dashboard** + **Integrations Health** desk pages out of Enhancements Core
into the new **Integrations** module. The JSONs declare ``module: Integrations``
and sync from ``integrations/``, so model sync already reassigns them; this is
the explicit, idempotent backstop.

No data moves -- records are keyed by name; the GA4 Settings Single's stored
property id / credentials carry across unchanged.
Idempotent: a no-op once everything already reads "Integrations".
"""
import frappe

DOCTYPES = ("GA4 Settings",)
PAGES = ("ga4-dashboard", "integrations-health")
NEW = "Integrations"


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
