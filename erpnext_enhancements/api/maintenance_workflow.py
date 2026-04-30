import frappe
from frappe import _
from frappe.utils import flt, nowdate, get_datetime

def process_maintenance_submission(record_name):
    """
    Background job to process Sapphire Maintenance Record submission.
    Ensures each step is independent; a failure in one doesn't halt the others.
    """
    try:
        doc = frappe.get_doc("Sapphire Maintenance Record", record_name)
        
        # Ensure it's in the right state/docstatus
        if doc.docstatus != 1:
            return

        steps = [
            ("Stock Entry Generation", create_stock_entry),
            ("Job Costing (Timesheet)", create_timesheet),
            ("Warranty RMA Hook", check_warranty_and_rma),
            ("Sales Invoice Generation", create_sales_invoice)
        ]

        failures = []

        for step_name, step_func in steps:
            try:
                step_func(doc)
            except Exception as e:
                error_msg = f"{step_name} failed: {str(e)}"
                frappe.log_error(frappe.get_traceback(), _("Maintenance Submission Step Failed"))
                doc.add_comment("Comment", _(error_msg))
                failures.append(error_msg)

        if not failures:
            doc.add_comment("Comment", _("Background processing completed successfully."))
        else:
            # Notify owner about partial failures
            owner = frappe.db.get_value("Sapphire Maintenance Record", record_name, "owner")
            frappe.publish_realtime("msgprint", {
                "message": _("Maintenance submission processed with some errors. Please check comments on {0}.").format(record_name),
                "indicator": "orange"
            }, user=owner)

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), _("Maintenance Submission Processing Failed"))
        # Notify Project Manager of critical failure (e.g. could not load doc)
        owner = frappe.db.get_value("Sapphire Maintenance Record", record_name, "owner")
        frappe.publish_realtime("msgprint", {
            "message": _("Background processing critically failed for {0}: {1}").format(record_name, str(e)),
            "indicator": "red"
        }, user=owner)

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

    stock_entry.insert()
    stock_entry.submit()
    doc.add_comment("Comment", _("Stock Entry {0} created for consumables.").format(
        frappe.get_link_to_form("Stock Entry", stock_entry.name)
    ))

def create_timesheet(doc):
    if not doc.clock_in_time or not doc.clock_out_time:
        return

    # Calculate Duration manually using datetime objects
    start_time = get_datetime(doc.clock_in_time)
    end_time = get_datetime(doc.clock_out_time)
    total_seconds = (end_time - start_time).total_seconds()
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
        frappe.get_link_to_form("Timesheet", timesheet.name), hours
    ))

def check_warranty_and_rma(doc):
    if not doc.serial_no:
        return

    # Check if serial_no is under warranty
    # Note: Standard Serial No uses warranty_expiry_date
    serial_no_doc = frappe.get_doc("Serial No", doc.serial_no)
    expiry_date = serial_no_doc.get("warranty_expiry_date")
    
    if not expiry_date or get_datetime(expiry_date).date() < get_datetime(nowdate()).date():
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
            item_code = frappe.db.get_value("Item", {"item_name": part}, "name") or "WARRANTY-RETURN-PENDING"
            
            mr.append("items", {
                "item_code": item_code,
                "qty": 1,
                "uom": "Nos",
                "description": _("Warranty Return for {0} (Serial No: {1})").format(part, doc.serial_no)
            })
            
        mr.insert()
        doc.add_comment("Comment", _("Warranty RMA Triggered. Draft Material Request {0} created.").format(
            frappe.get_link_to_form("Material Request", mr.name)
        ))

def create_sales_invoice(doc):
    """
    Generates a draft Sales Invoice for the Maintenance Record.
    Includes base maintenance fee and consumables.
    """
    # Load settings
    settings = frappe.get_single("ERPNext Enhancements Settings")
    fee_item = settings.maintenance_fee_item or "MNT-FEE"
    services_group = settings.maintenance_services_group or "Maintenance Services"

    # 1. Get Parent Sales Order
    so_name = frappe.db.get_value("Sales Order Item", {"custom_serial_no": doc.serial_no, "docstatus": 1}, "parent")
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
    base_item = frappe.db.sql("""
        SELECT item_code, rate, qty FROM `tabSales Order Item`
        WHERE parent = %s AND item_group = %s
        LIMIT 1
    """, (so_name, services_group), as_dict=True)

    if base_item:
        invoice.append("items", {
            "item_code": base_item[0].item_code,
            "qty": 1,
            "rate": base_item[0].rate,
            "sales_order": so_name
        })
    else:
        # Fallback to fee_item from settings
        rate = frappe.db.get_value("Item Price", {"item_code": fee_item, "price_list": "Standard Selling"}, "price_list_rate") or 0
        invoice.append("items", {
            "item_code": fee_item,
            "qty": 1,
            "rate": rate
        })

    # 3. Add Consumables
    for item in doc.consumables:
        invoice.append("items", {
            "item_code": item.item,
            "qty": item.qty,
            "rate": frappe.db.get_value("Item Price", {"item_code": item.item, "price_list": "Standard Selling"}, "price_list_rate") or 0
        })

    invoice.set_missing_values()
    invoice.insert()
    doc.db_set("sales_invoice", invoice.name)
    doc.add_comment("Comment", _("Draft Sales Invoice {0} created.").format(
        frappe.get_link_to_form("Sales Invoice", invoice.name)
    ))
