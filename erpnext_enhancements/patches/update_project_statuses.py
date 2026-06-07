import frappe


def execute():
	# Update existing projects from 'Open' to 'Active'
	frappe.db.sql("""
        UPDATE `tabProject`
        SET status = 'Active'
        WHERE status = 'Open'
    """)
