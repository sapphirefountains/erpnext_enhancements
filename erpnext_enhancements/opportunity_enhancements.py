import frappe
from erpnext.crm.doctype.opportunity.opportunity import make_project as original_make_project

@frappe.whitelist()
def make_project(source_name, target_doc=None):
    """
    Override to set custom_sales_opportunity on the new Project.
    """
    target = original_make_project(source_name, target_doc)
    
    # Set the custom field
    target.custom_sales_opportunity = source_name
    
    return target
