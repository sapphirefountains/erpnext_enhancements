import frappe

def execute():
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
