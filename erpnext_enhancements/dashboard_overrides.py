"""Doctype dashboard ("connections") overrides for standard ERPNext doctypes.

Frappe builds the "Connections" dashboard on a form (the grid of linked-document
counts) from a per-doctype data dict. ``override_doctype_dashboards`` in hooks.py
lets an app post-process that dict. This module supplies the Employee override:

    override_doctype_dashboards = {
        "Employee": "erpnext_enhancements.dashboard_overrides.get_data",
    }

(The Project dashboard override is ``erpnext_enhancements.project_enhancements.get_dashboard_data``.)
"""

from frappe import _

def get_data(data):
    """Add a "Travel" connections group (linking Travel Trip) to the Employee dashboard.

    Wired via ``override_doctype_dashboards["Employee"]`` in hooks.py. Frappe calls
    this with the standard Employee dashboard data dict; we append a new transactions
    group exposing the custom ``Travel Trip`` doctype as a linked document, then return
    the mutated dict.

    Args:
        data (dict): The standard Employee dashboard data (has a ``transactions`` list).

    Returns:
        dict: The same dict with the extra "Travel" group appended.
    """
    data["transactions"].append(
        {
            "label": _("Travel"),
            "items": ["Travel Trip"],
        }
    )
    return data
