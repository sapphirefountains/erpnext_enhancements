"""One-time migration patch (post_model_sync; listed in patches.txt).

Creates the public, standard "Home" Workspace (sequence 1, home icon) if it does
not already exist, so every site has a landing workspace. Idempotent — skips
when the workspace is already present.
"""
import frappe

def execute():
    """Create the standard public "Home" Workspace when missing."""
    if not frappe.db.exists("Workspace", "Home"):
        doc = frappe.new_doc("Workspace")
        doc.name = "Home"
        doc.label = "Home"
        doc.public = 1
        doc.is_standard = 1
        doc.sequence_id = 1
        doc.icon = "home"
        doc.title = "Home"
        doc.insert(ignore_permissions=True)
