import frappe
from frappe import _
from frappe.utils import flt, nowdate, get_datetime, time_diff_in_seconds

def process_maintenance_submission(record_name):
    """
    Background job to process Sapphire Maintenance Record submission.
    1. Generates Stock Entry for consumables.
    2. Creates Timesheet for technician.
    3. Handles Warranty/RMA logic.
    4. Generates Draft Sales Invoice.
    """
    try:
        doc = frappe.get_doc("Sapphire Maintenance Record", record_name)
        
        # Ensure it's in the right state/docstatus
        if doc.docstatus != 1:
            return

        # 1. Stock Entry Generation
        create_stock_entry(doc)

        # 2. Job Costing (Timesheet)
        create_timesheet(doc)

        # 3. Warranty RMA Hook
        check_warranty_and_rma(doc)

        # 4. Sales Invoice Generation
        create_sales_invoice(doc)

        doc.add_comment("Comment", _("Background processing completed successfully."))

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), _("Maintenance Submission Processing Failed"))
        # Notify Project Manager
        owner = frappe.db.get_value("Sapphire Maintenance Record", record_name, "owner")
        frappe.publish_realtime("msgprint", {
            "message": _("Background processing failed for {0}: {1}").format(record_name, str(e)),
            "indicator": "red"
        }, user=owner)
        
        # Also add a comment for visibility
        frappe.get_doc("Sapphire Maintenance Record", record_name).add_comment(
            "Comment", _("Background processing failed: {0}").format(str(e))
        )

def create_stock_entry(doc):
    if not doc.consumables:
        return

    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.purpose = "Material Issue"
    stock_entry.company = frappe.db.get_value("Project", doc.project, "company") or frappe.defaults.get_global_default("company")
    
    for item in doc.consumables:
        stock_entry.append("items", {
            "item_code": item.item,
            "s_warehouse": item.warehouse,
            "qty": item.qty,
            "serial_and_batch_bundle": item.serial_and_batch_bundle,
            "project": doc.project
        })

    try:
        stock_entry.insert()
        stock_entry.submit()
        doc.add_comment("Comment", _("Stock Entry {0} created for consumables.").format(
            frappe.utils.get_link_to_form("Stock Entry", stock_entry.name)
        ))
    except frappe.exceptions.ValidationError as e:
        raise Exception(_("Stock Entry failed: {0}").format(str(e)))

def create_timesheet(doc):
    if not doc.clock_in_time or not doc.clock_out_time:
        return

    # Calculate Duration
    total_seconds = time_diff_in_seconds(doc.clock_out_time, doc.clock_in_time)
    work_seconds = total_seconds - flt(doc.paused_duration)
    hours = work_seconds / 3600.0

    if hours <= 0:
        return

    # Find Employee
    employee = frappe.db.get_value("Employee", {"user_id": doc.technician}, "name")
    if not employee:
        # Fallback: Check if technician name is already an employee ID
        if frappe.db.exists("Employee", doc.technician):
            employee = doc.technician
        else:
            doc.add_comment("Comment", _("Could not find Employee for technician {0}. Timesheet not created.").format(doc.technician))
            return

    timesheet = frappe.new_doc("Timesheet")
    timesheet.employee = employee
    timesheet.append("time_logs", {
        "from_time": doc.clock_in_time,
        "to_time": doc.clock_out_time,
        "hours": hours,
        "project": doc.project,
        "description": _("Maintenance Visit: {0}").format(doc.name)
    })
    
    timesheet.insert()
    timesheet.submit()
    
    # Update total labor cost
    total_cost = timesheet.total_billable_amount or timesheet.total_costing_amount
    doc.db_set("total_labor_cost", total_cost)
    
    doc.add_comment("Comment", _("Timesheet {0} created. Total labor hours: {1:.2f}").format(
        frappe.utils.get_link_to_form("Timesheet", timesheet.name), hours
    ))

def check_warranty_and_rma(doc):
    if not doc.asset:
        return

    # Check if asset is under warranty
    asset = frappe.get_doc("Asset", doc.asset)
    is_under_warranty = asset.get("custom_under_warranty")
    expiry_date = asset.get("custom_warranty_expiry_date")
    
    if not is_under_warranty or (expiry_date and get_datetime(expiry_date).date() < get_datetime(nowdate()).date()):
        return

    # Check for failures in maintenance results
    failed_parts = []
    for row in doc.maintenance_results:
        if row.selection in ["Fail", "Replace"]:
            failed_parts.append(row.question) # Assuming 'question' describes the part or check

    if failed_parts:
        doc.db_set("warranty_rma_flag", 1)
        
        # Create draft Material Request (Return/Transfer)
        mr = frappe.new_doc("Material Request")
        mr.material_request_type = "Material Transfer" # Closest standard type for RMA/Return
        mr.customer = doc.customer
        mr.project = doc.project
        mr.schedule_date = nowdate()
        
        for part in failed_parts:
            # We might need to map 'part description' to an Item Code
            # For now, we'll add a generic row or search for an item matching the description
            item_code = frappe.db.get_value("Item", {"item_name": part}, "name") or "WARRANTY-RETURN-PENDING"
            
            mr.append("items", {
                "item_code": item_code,
                "qty": 1,
                "uom": "Nos",
                "description": _("Warranty Return for {0} (Asset: {1})").format(part, doc.asset)
            })
            
        mr.insert()
        doc.add_comment("Comment", _("Warranty RMA Triggered. Draft Material Request {0} created.").format(
            frappe.utils.get_link_to_form("Material Request", mr.name)
        ))

def create_sales_invoice(doc):
    """
    Generates a draft Sales Invoice for the Maintenance Record.
    Includes base maintenance fee and consumables.
    """
    # 1. Get Parent Sales Order
    so_name = frappe.db.get_value("Sales Order Item", {"custom_asset": doc.asset, "docstatus": 1}, "parent")
    if not so_name:
        # Fallback to any active maintenance SO for this project
        so_name = frappe.db.get_value("Sales Order", {"project": doc.project, "order_type": "Maintenance", "docstatus": 1}, "name")

    if not so_name:
        doc.add_comment("Comment", _("Could not find parent Sales Order. Sales Invoice not created."))
        return

    invoice = frappe.new_doc("Sales Invoice")
    invoice.customer = doc.customer
    invoice.project = doc.project
    invoice.posting_date = nowdate()
    invoice.due_date = nowdate()
    invoice.custom_maintenance_record = doc.name
    
    # 2. Add Base Maintenance Fee Item
    # Search SO items for 'Maintenance Services' group
    base_item = frappe.db.sql("""
        SELECT item_code, rate, qty FROM `tabSales Order Item`
        WHERE parent = %s AND item_group = 'Maintenance Services'
        LIMIT 1
    """, so_name, as_dict=True)

    if base_item:
        invoice.append("items", {
            "item_code": base_item[0].item_code,
            "qty": 1,
            "rate": base_item[0].rate,
            "sales_order": so_name
        })
    else:
        # Fallback to MNT-FEE
        rate = frappe.db.get_value("Item Price", {"item_code": "MNT-FEE", "price_list": "Standard Selling"}, "price_list_rate") or 0
        invoice.append("items", {
            "item_code": "MNT-FEE",
            "qty": 1,
            "rate": rate
        })

    # 3. Add Consumables
    for item in doc.consumables:
        invoice.append("items", {
            "item_code": item.item,
            "qty": item.qty,
            # Standard price fetching
            "rate": frappe.db.get_value("Item Price", {"item_code": item.item, "price_list": "Standard Selling"}, "price_list_rate") or 0
        })

    try:
        invoice.set_missing_values()
        invoice.insert()
        doc.db_set("sales_invoice", invoice.name)
        doc.add_comment("Comment", _("Draft Sales Invoice {0} created.").format(
            frappe.utils.get_link_to_form("Sales Invoice", invoice.name)
        ))
    except Exception as e:
        raise Exception(_("Sales Invoice generation failed: {0}").format(str(e)))
