import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    """
    Reorders 'Procurement' buttons in the Project DocType to be in 3 columns and 2 rows.
    This patch ensures the layout is correct even if fields were previously created in a different order.
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
            # Column 1
            {
                "fieldname": "custom_btn_material_request",
                "label": "+ Material Request",
                "fieldtype": "Button",
                "insert_after": "custom_procurement_section"
            },
            {
                "fieldname": "custom_btn_purchase_order",
                "label": "+ Purchase Order",
                "fieldtype": "Button",
                "insert_after": "custom_btn_material_request"
            },
            # Column 2
            {
                "fieldname": "custom_col_break_1",
                "fieldtype": "Column Break",
                "insert_after": "custom_btn_purchase_order"
            },
            {
                "fieldname": "custom_btn_request_quote",
                "label": "+ Request Quote",
                "fieldtype": "Button",
                "insert_after": "custom_col_break_1"
            },
            {
                "fieldname": "custom_btn_purchase_receipt",
                "label": "+ Purchase Receipt",
                "fieldtype": "Button",
                "insert_after": "custom_btn_request_quote"
            },
            # Column 3
            {
                "fieldname": "custom_col_break_2",
                "fieldtype": "Column Break",
                "insert_after": "custom_btn_purchase_receipt"
            },
            {
                "fieldname": "custom_btn_supplier_quotation",
                "label": "+ Supplier Quotation",
                "fieldtype": "Button",
                "insert_after": "custom_col_break_2"
            },
            {
                "fieldname": "custom_btn_purchase_invoice",
                "label": "+ Purchase Invoice",
                "fieldtype": "Button",
                "insert_after": "custom_btn_supplier_quotation"
            }
        ]
    }

    create_custom_fields(custom_fields, update=True)
