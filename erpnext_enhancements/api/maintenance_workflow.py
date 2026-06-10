"""Post-submission automation for Sapphire Maintenance Record.

Not whitelisted. ``process_maintenance_submission`` is the background worker
enqueued from the Sapphire Maintenance Record controller's ``on_submit`` (see
``sapphire_maintenance/doctype/sapphire_maintenance_record``). It chains four
independent downstream actions, each isolated so one failure does not abort the
others:
        1. Stock Entry (Material Issue) for consumables (chemical dosing).
        2. Timesheet for labour (job costing).
        3. Warranty check -> native Warranty Claim per failed in-warranty serial.
        4. Draft Sales Invoice (maintenance fee + consumables) — only when the
           contract bills "Per Visit".

It also adds a Comment listing any out-of-range chemistry readings (the
supervisor email itself is the "Maintenance Reading Out of Range" Notification
fixture, firing on submit off ``has_out_of_range_readings``).

Side effects: inserts/submits Stock Entry, Timesheet, Warranty Claim, and
Sales Invoice documents; adds Comments to the record; uses
``frappe.publish_realtime`` to notify the record owner of partial/critical
failures. Reads item/fee config from "ERPNext Enhancements Settings". No
external services. No item codes are hardcoded — items come from document
links and Settings only.
"""

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
            ("Warranty Claim Hook", check_warranty_and_rma),
            ("Sales Invoice Generation", create_sales_invoice),
            ("Out-of-Range Reading Log", log_out_of_range_readings),
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


def resolve_consumable_warehouse(feature_warehouse=None, technician=None):
    """Resolve the default source warehouse for a consumable.

    Fallback order (always overridable on the consumable row itself):
      1. The contract feature's chemical warehouse (on-site store).
      2. The technician's vehicle warehouse
         (``Employee.custom_default_vehicle_warehouse``, Employee resolved
         from the technician User).
      3. ``ERPNext Enhancements Settings.default_consumables_warehouse``.

    Returns a warehouse name or None.
    """
    if feature_warehouse:
        return feature_warehouse

    if technician:
        employee = frappe.db.get_value("Employee", {"user_id": technician}, "name")
        if not employee and frappe.db.exists("Employee", technician):
            employee = technician
        if employee:
            vehicle_warehouse = frappe.db.get_value(
                "Employee", employee, "custom_default_vehicle_warehouse"
            )
            if vehicle_warehouse:
                return vehicle_warehouse

    return frappe.db.get_single_value("ERPNext Enhancements Settings", "default_consumables_warehouse")


def _contract_feature_warehouses(doc):
    """Map serial_no -> chemical warehouse from the record's contract features."""
    if not doc.get("maintenance_contract"):
        return {}
    rows = frappe.get_all(
        "Sapphire Contract Feature",
        filters={"parent": doc.maintenance_contract, "parenttype": "Sapphire Maintenance Contract"},
        fields=["serial_no", "default_warehouse"],
    )
    return {row.serial_no: row.default_warehouse for row in rows if row.default_warehouse}


def build_stock_entry_rows(doc):
    """Build Stock Entry item rows from the record's consumables.

    Rows with qty <= 0 are untouched dosing-template prefills and are skipped.
    Rows without a warehouse get one resolved per the fallback chain in
    :func:`resolve_consumable_warehouse` (feature -> vehicle -> settings).
    Returns a list of dicts ready to append to a "Material Issue" Stock Entry.
    """
    feature_warehouses = _contract_feature_warehouses(doc)
    rows = []
    for item in doc.consumables:
        if flt(item.qty) <= 0:
            continue
        warehouse = item.warehouse or resolve_consumable_warehouse(
            feature_warehouse=feature_warehouses.get(item.serial_no or doc.serial_no),
            technician=doc.technician,
        )
        rows.append({
            "item_code": item.item,
            "s_warehouse": warehouse,
            "qty": flt(item.qty),
            "serial_and_batch_bundle": item.serial_and_batch_bundle,
            "project": doc.project,
        })
    return rows


def create_stock_entry(doc):
    """Issue the record's consumed consumables via a "Material Issue" Stock Entry.

    Args:
        doc: The Sapphire Maintenance Record document.

    No-op if no consumable row has qty > 0 (dosing sections prefill rows at
    qty 0; untouched chemicals must not move stock). Company is taken from the
    linked Project (falling back to the global default). Inserts and submits a
    Stock Entry and adds a Comment linking to it. May raise on stock errors
    (e.g. insufficient quantity) — caught by the per-step handler in the caller.
    """
    rows = build_stock_entry_rows(doc)
    if not rows:
        return

    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.purpose = "Material Issue"
    stock_entry.company = frappe.db.get_value("Project", doc.project, "company") or frappe.defaults.get_global_default("company")

    for row in rows:
        stock_entry.append("items", row)

    stock_entry.insert()
    stock_entry.submit()
    doc.add_comment("Comment", _("Stock Entry {0} created for consumables.").format(
        frappe.get_link_to_form("Stock Entry", stock_entry.name)
    ))

def create_timesheet(doc):
    """Create and submit a Timesheet for the technician's logged labour.

    Args:
        doc: The Sapphire Maintenance Record document.

    No-op if clock-in/out times are absent. Worked hours = (clock_out -
    clock_in) minus ``paused_duration``; no timesheet is created if that is
    <= 0. Resolves the technician to an Employee via ``user_id`` (falling back
    to treating ``technician`` as an Employee id). Inserts + submits a
    Timesheet, writes ``total_labor_cost`` back onto the record via ``db_set``,
    and adds a linking Comment.
    """
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
    """Raise native Warranty Claims for failed checks on in-warranty features.

    Args:
        doc: The Sapphire Maintenance Record document.

    Groups ``maintenance_results`` rows marked "Fail"/"Replace" by water
    feature (the row's ``serial_no`` for Per Site Visit records, else the
    record header's). For each in-warranty serial (``warranty_expiry_date``
    today or later) it sets ``warranty_rma_flag`` and inserts one draft
    **Warranty Claim** — the native support document, so the claim flows into
    standard ERPNext reporting — whose complaint lists the failed checks.
    Adds a linking Comment per claim.
    """
    failed_by_serial = {}
    for row in doc.maintenance_results:
        if row.selection in ["Fail", "Replace"]:
            serial = row.serial_no or doc.serial_no
            if serial:
                failed_by_serial.setdefault(serial, []).append(row.question)

    if not failed_by_serial:
        return

    company = frappe.db.get_value("Project", doc.project, "company") or frappe.defaults.get_global_default("company")
    claims = []
    for serial, failed_parts in failed_by_serial.items():
        expiry_date = frappe.db.get_value("Serial No", serial, "warranty_expiry_date")
        if not expiry_date or get_datetime(expiry_date).date() < get_datetime(nowdate()).date():
            continue

        claim = frappe.new_doc("Warranty Claim")
        claim.status = "Open"
        claim.complaint_date = nowdate()
        claim.customer = doc.customer
        claim.company = company
        claim.serial_no = serial
        claim.item_code = frappe.db.get_value("Serial No", serial, "item_code")
        claim.complaint = _("Failed checks during maintenance visit {0}:\n{1}").format(
            doc.name, "\n".join(f"- {part}" for part in failed_parts)
        )
        claim.insert()
        claims.append(claim.name)

    if claims:
        doc.db_set("warranty_rma_flag", 1)
        for name in claims:
            doc.add_comment("Comment", _("Warranty Claim {0} created for failed checks.").format(
                frappe.get_link_to_form("Warranty Claim", name)
            ))

def log_out_of_range_readings(doc):
    """Add an audit Comment listing the visit's out-of-range chemistry readings.

    The supervisor email is handled by the "Maintenance Reading Out of Range"
    Notification fixture; this Comment keeps the detail on the record's
    timeline.
    """
    flagged = [row for row in doc.get("chemistry_readings", []) if row.out_of_range]
    if not flagged:
        return

    lines = [
        _("{0}: {1} {2} (target {3}–{4})").format(
            f"{row.reading} ({row.serial_no})" if row.serial_no and not doc.serial_no else row.reading,
            row.reading_value,
            row.uom or "",
            row.min_value,
            row.max_value,
        )
        for row in flagged
    ]
    doc.add_comment("Comment", _("Out-of-range water chemistry readings:\n{0}").format("\n".join(lines)))

def create_sales_invoice(doc):
    """
    Generates a draft Sales Invoice for the Maintenance Record.
    Includes base maintenance fee and consumed consumables (qty > 0).

    Skipped (with a Comment) when the contract's invoicing frequency is not
    "Per Visit" — monthly/quarterly/annual billing runs off the Sales Order
    instead. The parent Sales Order is found via the contract first, then the
    legacy serial-no / project lookups. No item codes are hardcoded: the fee
    item and services group come from ERPNext Enhancements Settings.
    """
    # 0. Respect the contract's invoicing cadence
    invoicing_frequency = "Per Visit"
    contract_so = None
    if doc.get("maintenance_contract"):
        contract = frappe.db.get_value(
            "Sapphire Maintenance Contract",
            doc.maintenance_contract,
            ["invoicing_frequency", "sales_order"],
            as_dict=True,
        )
        if contract:
            invoicing_frequency = contract.invoicing_frequency or "Per Visit"
            contract_so = contract.sales_order

    if invoicing_frequency != "Per Visit":
        doc.add_comment("Comment", _("Sales Invoice not drafted: contract bills {0}.").format(invoicing_frequency))
        return

    # Load settings
    settings = frappe.get_single("ERPNext Enhancements Settings")
    fee_item = settings.maintenance_fee_item
    services_group = settings.maintenance_services_group

    # 1. Get Parent Sales Order: contract link, then legacy lookups
    so_name = contract_so
    if not so_name and doc.serial_no:
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
    base_item = None
    if services_group:
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
    elif fee_item:
        # Fallback to fee_item from settings
        rate = frappe.db.get_value("Item Price", {"item_code": fee_item, "price_list": "Standard Selling"}, "price_list_rate") or 0
        invoice.append("items", {
            "item_code": fee_item,
            "qty": 1,
            "rate": rate
        })
    else:
        doc.add_comment("Comment", _("No maintenance fee line added: set the Default Maintenance Fee Item or Maintenance Services Item Group in ERPNext Enhancements Settings."))

    # 3. Add Consumables actually used
    for item in doc.consumables:
        if flt(item.qty) <= 0:
            continue
        invoice.append("items", {
            "item_code": item.item,
            "qty": item.qty,
            "rate": frappe.db.get_value("Item Price", {"item_code": item.item, "price_list": "Standard Selling"}, "price_list_rate") or 0
        })

    if not invoice.items:
        doc.add_comment("Comment", _("Sales Invoice not created: nothing to bill."))
        return

    invoice.set_missing_values()
    invoice.insert()
    doc.db_set("sales_invoice", invoice.name)
    doc.add_comment("Comment", _("Draft Sales Invoice {0} created.").format(
        frappe.get_link_to_form("Sales Invoice", invoice.name)
    ))
