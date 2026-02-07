import frappe
from frappe import _

@frappe.whitelist()
def add_shortcut_to_workspace(workspace, label, type, link_to, doc_type=None):
    if not workspace:
        frappe.throw(_("Workspace is required"))
        
    doc = frappe.get_doc("Workspace", workspace)
    
    # Check duplicate
    for s in doc.shortcuts:
        if s.label == label:
            frappe.throw(_("Shortcut with this label already exists"))

    doc.append("shortcuts", {
        "label": label,
        "type": type,
        "link_to": link_to,
        "doc_view": "List" if type == "DocType" else ""
    })
    
    doc.save()
    return doc.name

@frappe.whitelist()
def get_workspaces_for_user():
    # Return workspaces visible to the user
    # Simplified logic: public + private
    return frappe.get_all("Workspace", filters={"public": 1}, fields=["name", "label", "title"])
