"""One-time migration: reassign Asset Booking to the Asset Management module.

PR 10 of the module reorganization moves the **Asset Booking** doctype (a
submittable booking doc with a calendar view) out of Enhancements Core into the
new ``asset_management`` module. The JSON declares ``module: Asset Management``
and syncs from ``asset_management/``, so model sync already reassigns it; this is
the explicit, idempotent backstop.

The app-level ``api/booking.py`` stays put (it creates Asset Booking docs by
name). No data moves -- records are keyed by name, and submitted bookings carry
across unchanged. Idempotent: a no-op once it already reads "Asset Management".
"""
import frappe


def execute():
    if frappe.db.exists("DocType", "Asset Booking") and frappe.db.exists(
        "Module Def", "Asset Management"
    ):
        frappe.db.set_value("DocType", "Asset Booking", "module", "Asset Management")
        frappe.clear_cache()
