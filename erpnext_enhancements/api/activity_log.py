import frappe

@frappe.whitelist()
def get_activity_counts(doctype, docname):
    """
    Returns the total count of activities (Comments, Communications, Versions)
    for the given document.
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
