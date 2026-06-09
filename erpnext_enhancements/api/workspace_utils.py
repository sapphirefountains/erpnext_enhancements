"""Workspace shortcut helpers.

Whitelisted API called from ``public/js/erpnext_enhancements.js`` to list the
user's public Workspaces and append a shortcut to a chosen Workspace (the
"add to workspace" UI).

Security: standard authenticated whitelist. ``add_shortcut_to_workspace`` saves
the Workspace with normal permissions (no ``ignore_permissions``), so it
requires Workspace write access. No external services.
"""

import frappe
from frappe import _

@frappe.whitelist()
def add_shortcut_to_workspace(workspace, label, type, link_to, doc_type=None):
    """Append a shortcut to a Workspace's ``shortcuts`` child table.

    Args:
        workspace (str): Target Workspace name (required; else ``frappe.throw``).
        label (str): Shortcut label; must be unique within the workspace
            (duplicate label -> ``frappe.throw``).
        type (str): Shortcut type, e.g. "DocType". For "DocType" the
            ``doc_view`` is set to "List".
        link_to (str): The link target (e.g. the DocType / report / page).
        doc_type (str, optional): Unused by the current implementation.

    Returns:
        str: The saved Workspace name.

    Side effects: saves the Workspace document (requires write permission).
    """
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
    """List public Workspaces for the workspace-picker UI.

    Returns:
        list[dict]: ``[{name, label, title}, ...]`` for all Workspaces with
        ``public = 1``. NOTE: despite the comment, only public workspaces are
        returned (private ones are not currently included). Read-only.
    """
    # Return workspaces visible to the user
    # Simplified logic: public + private
    return frappe.get_all("Workspace", filters={"public": 1}, fields=["name", "label", "title"])
