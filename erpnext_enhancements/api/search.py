import frappe
from frappe import _
from frappe.utils import escape_html
import re

@frappe.whitelist()
def search_global_docs(txt):
    """
    Search for documents in the Global Search table.
    Returns a list of dicts suitable for the AwesomeBar.
    """
    if not txt:
        return []

    # Compile regex for highlighting
    pattern = re.compile(re.escape(txt), re.IGNORECASE)

    # Query the Global Search table
    # We use 'content' like %txt%
    # We limit initial fetch to 20 to ensure responsiveness
    results = frappe.db.sql("""
        SELECT doctype, name, title, route
        FROM `__global_search`
        WHERE content LIKE %s
        LIMIT 20
    """, (f"%{txt}%",), as_dict=True)

    # Group by doctype to check permissions efficiently
    doctype_names = {}
    for r in results:
        doctype_names.setdefault(r.doctype, []).append(r.name)

    permitted_docs = {}
    for doctype, names in doctype_names.items():
        try:
            if not frappe.has_permission(doctype, "read"):
                permitted_docs[doctype] = set()
                continue

            is_single = frappe.get_meta(doctype).issingle
            if is_single:
                permitted_docs[doctype] = set(names)
                continue

            # get_all safely applies user permissions without throwing UI errors for unauthorized rows
            valid_names = frappe.get_all(
                doctype,
                filters={"name": ("in", names)},
                pluck="name",
                ignore_permissions=False
            )
            permitted_docs[doctype] = {str(n) for n in valid_names}
        except Exception:
            permitted_docs[doctype] = set()

    out = []
    for r in results:
        try:
            # Check if document still exists and the user has permission to read it
            if r.name not in permitted_docs.get(r.doctype, set()):
                continue

            if not frappe.db.exists(r.doctype, r.name):
                continue

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

            # Highlight the matching part in the label safely
            parts = []
            last_end = 0
            for match in pattern.finditer(label):
                parts.append(escape_html(label[last_end:match.start()]))
                parts.append(f"<b>{escape_html(match.group(0))}</b>")
                last_end = match.end()
            parts.append(escape_html(label[last_end:]))
            highlighted_label = "".join(parts)

            out.append({
                "label": highlighted_label,
                "value": label, # Value put in input
                "route": ["Form", r.doctype, r.name],
                "match": label, # Match against the full label so highlighting works
                "index": 150 # Higher than standard doctypes (usually ~100?)
            })
        except Exception:
            continue

    return out
