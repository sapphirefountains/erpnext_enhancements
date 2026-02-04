import frappe
from frappe import _

@frappe.whitelist()
def search_global_docs(txt):
    """
    Search for documents in the Global Search table.
    Returns a list of dicts suitable for the AwesomeBar.
    """
    if not txt:
        return []

    # Query the Global Search table
    # We use 'content' like %txt%
    # We limit initial fetch to 20 to ensure responsiveness
    results = frappe.db.sql("""
        SELECT doctype, name, title, route
        FROM `__global_search`
        WHERE content LIKE %s
        LIMIT 20
    """, (f"%{txt}%",), as_dict=True)

    out = []
    for r in results:
        try:
            # Verify Read Permission for the user
            if frappe.has_permission(r.doctype, "read", r.name):

                # Determine Route
                # If stored in Global Search, use it.
                # Otherwise, construct the standard Form route.
                route = r.route
                if not route:
                    # In Frappe Desk, routes are typically "Form/DocType/Name" or "list/DocType"
                    # But frappe.set_route takes array: ["Form", doctype, name]
                    # Here we return a string or object that the frontend can handle.
                    # Standard AwesomeBar expects 'route' to be used in set_route.
                    pass

                # Prepare Label
                # We want: "[DocType] Name (Title)"

                # Check if Title is useful (not same as name)
                doc_identifier = r.name
                if r.title and r.title != r.name:
                    doc_identifier += f" ({r.title})"

                label = f"{_(r.doctype)}: {doc_identifier}"

                out.append({
                    "label": label,
                    "value": label, # Value put in input
                    "route": ["Form", r.doctype, r.name],
                    "match": label, # Match against the full label so highlighting works
                    "index": 150 # Higher than standard doctypes (usually ~100?)
                })
        except Exception:
            continue

    return out
