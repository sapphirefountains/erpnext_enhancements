import frappe

def execute():
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
