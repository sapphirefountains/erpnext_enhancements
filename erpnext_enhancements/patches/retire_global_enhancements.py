"""One-time migration: retire the Global Enhancements module.

By the end of the module reorganization, Global Enhancements held only two
doctypes -- **Additional Supplier Group** (child table) and **Directory Link
Exclusion** -- after the Triton doctypes left for AI Governance (v1.44.0). PR 13
moves those two into Enhancements Core and deletes the now-empty module.

The doctype JSONs declare ``module: Enhancements Core`` and sync from
``enhancements_core/``, so model sync already reassigns them; this patch is the
explicit backstop, then it reassigns any stragglers still pointing at the old
module and deletes the orphaned ``Module Def``.

No data moves -- records are keyed by name. Idempotent: a no-op once the module
is gone (including fresh installs, where it was never created).
"""
import frappe

MOVED = ("Additional Supplier Group", "Directory Link Exclusion")
OLD_MODULE = "Global Enhancements"
NEW_MODULE = "Enhancements Core"


def execute():
    for dt in MOVED:
        if frappe.db.exists("DocType", dt):
            frappe.db.set_value("DocType", dt, "module", NEW_MODULE)

    # Reassign anything else still pointing at the retired module (defensive --
    # nothing should remain after Triton left and the two doctypes moved).
    for dt in ("DocType", "Page", "Report", "Workspace"):
        for name in frappe.get_all(dt, filters={"module": OLD_MODULE}, pluck="name"):
            frappe.db.set_value(dt, name, "module", NEW_MODULE)

    if frappe.db.exists("Module Def", OLD_MODULE):
        frappe.delete_doc("Module Def", OLD_MODULE, force=True, ignore_permissions=True)

    frappe.clear_cache()
