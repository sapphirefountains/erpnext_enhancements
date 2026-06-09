"""Predictive maintenance scheduling on maintenance-record submission.

Not whitelisted. ``update_sales_order_next_visit`` is registered in hooks.py as
the ``on_submit`` doc-event for "Sapphire Maintenance Record"; it back-fills the
servicing dates on the originating Sales Order so the next visit can be
predicted from the configured maintenance frequency.

Side effects: writes to Sales Order Item rows and shows a ``msgprint`` to the
submitting user. No external services.
"""

import frappe
from frappe.utils import add_days, add_months, getdate, nowdate

def update_sales_order_next_visit(doc, method):
    """
    Triggered on submission of Sapphire Maintenance Record.
    Updates the corresponding Sales Order Item row with last visit date
    and calculates the next predictive visit date.
    """
    if not doc.serial_no or not doc.project:
        return

    # 1. Find the Sales Order Item row
    # Join with Sales Order to ensure it's an active Maintenance order
    so_item = frappe.db.sql("""
        SELECT 
            item.name, item.parent, item.custom_maintenance_frequency
        FROM 
            `tabSales Order Item` item
        JOIN 
            `tabSales Order` so ON item.parent = so.name
        WHERE 
            so.project = %s 
            AND item.custom_serial_no = %s
            AND so.docstatus = 1
            AND so.status NOT IN ('Closed', 'Completed')
        LIMIT 1
    """, (doc.project, doc.serial_no), as_dict=True)

    if not so_item:
        return

    so_item = so_item[0]
    
    # 2. Update Last Visit Date
    completion_date = getdate(nowdate()) # Default to today
    
    # 3. Calculate Next Visit Date
    next_visit = calculate_next_date(completion_date, so_item.custom_maintenance_frequency)
    
    if next_visit:
        frappe.db.set_value("Sales Order Item", so_item.name, {
            "custom_last_visit_date": completion_date,
            "custom_next_predictive_visit": next_visit
        })
        frappe.msgprint(f"Updated Sales Order {so_item.parent} with next visit date: {next_visit}")

def calculate_next_date(base_date, frequency):
    """Add one maintenance interval to ``base_date`` based on ``frequency``.

    Args:
        base_date: Date to offset from (typically the last visit date).
        frequency (str): One of "Daily", "Weekly", "Bi-Weekly", "Monthly",
            "Quarterly", "Yearly".

    Returns:
        The computed next date, or ``None`` if ``frequency`` is empty or
        unrecognised. Pure function — no DB or side effects.
    """
    if not frequency:
        return None
        
    if frequency == "Daily":
        return add_days(base_date, 1)
    elif frequency == "Weekly":
        return add_days(base_date, 7)
    elif frequency == "Bi-Weekly":
        return add_days(base_date, 14)
    elif frequency == "Monthly":
        return add_months(base_date, 1)
    elif frequency == "Quarterly":
        return add_months(base_date, 3)
    elif frequency == "Yearly":
        return add_months(base_date, 12)
        
    return None
