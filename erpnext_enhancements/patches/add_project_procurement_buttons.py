import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    """
    Adds a 'Procurement' section break and buttons for creating procurement documents
    to the Project DocType, located at the bottom of the Budget tab.
    """

    custom_fields = {
        "Project": [
            {
                "fieldname": "custom_procurement_section",
                "label": "Procurement",
                "fieldtype": "Section Break",
                "insert_after": "custom_material_request_feed",
                "collapsible": 0
            },
            {
                "fieldname": "custom_btn_material_request",
                "label": "+ Material Request",
                "fieldtype": "Button",
                "insert_after": "custom_procurement_section"
            },
            {
                "fieldname": "custom_btn_request_quote",
                "label": "+ Request Quote",
                "fieldtype": "Button",
                "insert_after": "custom_btn_material_request"
            },
            {
                "fieldname": "custom_btn_supplier_quotation",
                "label": "+ Supplier Quotation",
                "fieldtype": "Button",
                "insert_after": "custom_btn_request_quote"
            },
            {
                "fieldname": "custom_btn_purchase_order",
                "label": "+ Purchase Order",
                "fieldtype": "Button",
                "insert_after": "custom_btn_supplier_quotation"
            },
            {
                "fieldname": "custom_btn_purchase_receipt",
                "label": "+ Purchase Receipt",
                "fieldtype": "Button",
                "insert_after": "custom_btn_purchase_order"
            },
            {
                "fieldname": "custom_btn_purchase_invoice",
                "label": "+ Purchase Invoice",
                "fieldtype": "Button",
                "insert_after": "custom_btn_purchase_receipt"
            }
        ]
    }

    create_custom_fields(custom_fields, update=True)
