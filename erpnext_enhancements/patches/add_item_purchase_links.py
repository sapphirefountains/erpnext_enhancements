"""One-time migration patch (post_model_sync; listed in patches.txt).

Adds purchase-link custom fields: a ``purchase_url`` Data field on Item Supplier,
and a read-only ``purchase_links`` HTML "Buy" column on Purchase Order Item and
Material Request Item (rendered client-side from the supplier purchase URLs).
Idempotent via ``create_custom_fields(..., update=True)``.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    """Create the Item Supplier / PO Item / MR Item purchase-link custom fields."""
    custom_fields = {
        "Item Supplier": [
            {
                "fieldname": "purchase_url",
                "label": "Purchase URL",
                "fieldtype": "Data",
                "in_list_view": 1,
                "columns": 4
            }
        ],
        "Purchase Order Item": [
            {
                "fieldname": "purchase_links",
                "label": "Buy",
                "fieldtype": "HTML",
                "read_only": 1,
                "in_list_view": 1,
                "print_hide": 1,
                "columns": 2
            }
        ],
        "Material Request Item": [
            {
                "fieldname": "purchase_links",
                "label": "Buy",
                "fieldtype": "HTML",
                "read_only": 1,
                "in_list_view": 1,
                "print_hide": 1,
                "columns": 2
            }
        ]
    }
    create_custom_fields(custom_fields, update=True)
