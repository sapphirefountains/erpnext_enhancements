"""One-time migration patch (post_model_sync; listed in patches.txt).

Removes the ``in_list_view`` Property Setters from the travel child tables (Trip
Agenda / Flight / Accommodation / Ground Transport) so their grid columns revert
to the doctype-defined defaults. Idempotent — re-running deletes nothing once the
property setters are gone.
"""
import frappe

def execute():
    """Delete the ``in_list_view`` Property Setters on the four trip child tables."""
    child_tables = [
        "Trip Agenda",
        "Trip Flight",
        "Trip Accommodation",
        "Trip Ground Transport"
    ]

    for doctype in child_tables:
        frappe.db.delete("Property Setter", {
            "doc_type": doctype,
            "property": "in_list_view"
        })
