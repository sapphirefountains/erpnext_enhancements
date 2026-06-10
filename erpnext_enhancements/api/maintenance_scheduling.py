"""Predictive maintenance scheduling on maintenance-record submission.

Not whitelisted. ``update_next_visit_dates`` is registered in hooks.py as the
``on_submit`` doc-event for "Sapphire Maintenance Record". The Sapphire
Maintenance Contract's feature rows are the scheduling source of truth: each
visited water feature's ``last_visit_date``/``next_visit_date`` roll forward by
the row's frequency. The same dates are *mirrored* to the legacy Sales Order
Item custom fields (``custom_last_visit_date`` / ``custom_next_predictive_visit``)
so existing reports keep working — the scheduler no longer reads them for
contract-covered projects (see ``tasks.generate_predictive_maintenance_records``).

Side effects: writes to Sapphire Contract Feature and Sales Order Item rows.
No external services.
"""

import frappe
from frappe.utils import add_days, add_months, getdate, nowdate


def update_next_visit_dates(doc, method):
    """
    Triggered on submission of Sapphire Maintenance Record (hooks.py doc-event).
    Rolls the visited features' last/next visit dates forward on the Active
    Maintenance Contract, and mirrors them onto the Sales Order Item rows.

    Seasonal visits (``visit_label`` set — startup/winterization) are annual
    one-offs outside the regular cadence, so they don't advance it.
    """
    if doc.get("visit_label"):
        return

    serials = _visited_serials(doc)
    completion_date = getdate(nowdate())

    contract = _resolve_contract(doc)
    if contract:
        for row in contract.covered_features:
            if serials and row.serial_no not in serials:
                continue
            if not serials and doc.serial_no and row.serial_no != doc.serial_no:
                continue
            next_visit = calculate_next_date(completion_date, row.frequency)
            updates = {"last_visit_date": completion_date}
            if next_visit:
                updates["next_visit_date"] = next_visit
            frappe.db.set_value("Sapphire Contract Feature", row.name, updates)

    # Mirror to the Sales Order Item custom fields (legacy reports/views).
    for serial_no in serials or ({doc.serial_no} if doc.serial_no else set()):
        _mirror_to_sales_order(doc.project, serial_no, completion_date)


def _visited_serials(doc):
    """Distinct water features this record touched (header + section rows)."""
    serials = set()
    if doc.serial_no:
        serials.add(doc.serial_no)
    for table in ("maintenance_results", "chemistry_readings", "cleaning_tasks", "consumables"):
        for row in doc.get(table, []):
            if row.get("serial_no"):
                serials.add(row.get("serial_no"))
    return serials


def _resolve_contract(doc):
    """The record's contract, falling back to the project's Active one."""
    if doc.get("maintenance_contract"):
        return frappe.get_doc("Sapphire Maintenance Contract", doc.maintenance_contract)
    if doc.project:
        name = frappe.db.get_value(
            "Sapphire Maintenance Contract", {"project": doc.project, "status": "Active"}, "name"
        )
        if name:
            return frappe.get_doc("Sapphire Maintenance Contract", name)
    return None


def _mirror_to_sales_order(project, serial_no, completion_date):
    """Write last/next visit dates onto the matching submitted SO Item row."""
    if not project or not serial_no:
        return

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
    """, (project, serial_no), as_dict=True)

    if not so_item:
        return

    so_item = so_item[0]
    next_visit = calculate_next_date(completion_date, so_item.custom_maintenance_frequency)

    updates = {"custom_last_visit_date": completion_date}
    if next_visit:
        updates["custom_next_predictive_visit"] = next_visit
    frappe.db.set_value("Sales Order Item", so_item.name, updates)


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
