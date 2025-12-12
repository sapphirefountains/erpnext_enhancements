import frappe


def execute():
	"""
	Fixes Property Setters for ToDo date field that were incorrectly created with property_type='Date'.
	This causes '0 is not a valid date string' error because the values are '0' (for reqd/hidden/etc).
	"""

	# 1. Fix known boolean properties that might be mistyped as Date
	frappe.db.sql("""
        UPDATE `tabProperty Setter`
        SET property_type = 'Check'
        WHERE doc_type = 'ToDo'
        AND field_name = 'date'
        AND property IN ('reqd', 'hidden', 'in_list_view', 'print_hide', 'report_hide')
        AND property_type IN ('Date', 'Datetime')
    """)

	# 2. Fix any Property Setter on ToDo that has value '0' but type 'Date'/'Datetime'
	# This acts as a catch-all for the specific error reported.
	frappe.db.sql("""
        UPDATE `tabProperty Setter`
        SET property_type = 'Check'
        WHERE doc_type = 'ToDo'
        AND value = '0'
        AND property_type IN ('Date', 'Datetime')
    """)
