"""Activity-timeline count endpoint.

Whitelisted API backing the timeline badge numbers shown on Desk forms.
Called from ``public/js/activity_log_numbering.js`` via
``erpnext_enhancements.api.activity_log.get_activity_counts`` to display how
many Comments / Communications / Versions are linked to the open document.

Security: requires an authenticated session and performs an explicit
``frappe.has_permission`` read check on the target document before counting.
"""

import frappe

@frappe.whitelist()
def get_activity_counts(doctype, docname):
    """
    Returns the total count of activities (Comments, Communications, Versions)
    for the given document.

    Args:
        doctype (str): Reference DocType of the document.
        docname (str): Name (id) of the document.

    Returns:
        dict: ``{"Comment": int, "Communication": int, "Version": int}`` on
        success, or ``{"error": <reason>}`` if arguments are missing or the
        user lacks read permission on the document.

    Side effects: read-only (count queries only). Permission-gated by an
    explicit ``frappe.has_permission(doctype, "read", docname)`` check.
    """
    if not doctype or not docname:
        return {"error": "Missing arguments"}

    # Check permission
    if not frappe.has_permission(doctype, "read", docname):
        return {"error": "No permission"}

    # Count Comments
    # We might want to filter comment_type="Comment" or include all?
    # The timeline shows various types (Created, Assigned, etc).
    # We should probably count all linked comments as the timeline usually shows them.
    # But "Comment" Doctype includes "Comment" type, "Workflow", "Label", etc.
    # We'll count all for now.
    comments = frappe.db.count("Comment", {
        "reference_doctype": doctype,
        "reference_name": docname
    })

    # Count Communications
    communications = frappe.db.count("Communication", {
        "reference_doctype": doctype,
        "reference_name": docname,
        "communication_type": "Communication" # Exclude automated? Timeline usually shows all.
        # But 'Communication' table includes emails.
    })

    # Count Versions
    # Version uses ref_doctype and docname usually
    versions = frappe.db.count("Version", {
        "ref_doctype": doctype,
        "docname": docname
    })

    return {
        "Comment": comments,
        "Communication": communications,
        "Version": versions
    }
