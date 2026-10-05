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

import datetime

import frappe
from frappe.utils import add_days, add_months, getdate, nowdate

# Load-bearing strings, kept in step with the contract controller's
# WINTERIZATION_LABEL / MONTHS (draft dedup and the cadence skip key on them).
WINTERIZATION_LABEL = "Winterization"
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def update_next_visit_dates(doc, method):
    """
    Triggered on submission of Sapphire Maintenance Record (hooks.py doc-event).
    Rolls the visited features' last/next visit dates forward on the Active
    Maintenance Contract, and mirrors them onto the Sales Order Item rows.

    Seasonal visits (``visit_label`` set — startup/winterization) are annual
    one-offs outside the regular cadence, so they don't advance it. The one
    exception: finishing the Winterization visit on a contract that pauses over
    winter parks every feature's next visit at the spring startup (see
    ``defer_for_winter``).
    """
    if doc.get("visit_label"):
        if doc.get("visit_label") == WINTERIZATION_LABEL:
            _park_until_spring(doc)
        return

    serials = _visited_serials(doc)
    # The day the work happened, not the day the form was submitted. These are
    # the same for a visit filled in on site; they differ for a backfilled form
    # (api.maintenance_visit.create_visit stamps visit_date), and there the
    # schedule must roll from the actual service date or a Friday backfill of
    # Tuesday's work would push every later visit three days late.
    completion_date = getdate(doc.get("visit_date") or nowdate())

    contract = _resolve_contract(doc)
    if contract:
        for row in contract.covered_features:
            if serials and row.serial_no not in serials:
                continue
            if not serials and doc.serial_no and row.serial_no != doc.serial_no:
                continue
            next_visit = defer_for_winter(contract, calculate_next_date(completion_date, row.frequency))
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


def _winter_months(contract):
    """(winterization month, startup month) as 1-12, or None when not pausing."""
    if not contract or not contract.get("pause_over_winter"):
        return None
    stop = contract.get("winterization_month")
    resume = contract.get("startup_month")
    if stop not in MONTHS or resume not in MONTHS or stop == resume:
        return None
    return MONTHS.index(stop) + 1, MONTHS.index(resume) + 1


def spring_resume_date(contract, after):
    """The 1st of the contract's startup month, the first one after ``after``.

    The Spring Startup visit is drafted on the first scheduler run of that
    month, and regular visits restart the same day (decided 2026-10-05).
    """
    months = _winter_months(contract)
    if not months:
        return None
    resume = months[1]
    after = getdate(after)
    year = after.year if after.month < resume else after.year + 1
    return datetime.date(year, resume, 1)


def defer_for_winter(contract, next_visit):
    """Move a regular visit that would fall in the off-season to spring.

    On a contract with ``pause_over_winter``, the off-season runs from the 1st
    of the winterization month up to (not including) the startup month,
    wrapping the year end. A rolled-forward visit landing in it is moved to the
    1st of the startup month. Starting the window at the winterization month,
    not after it, is deliberate: the winterization visit stands in for that
    month's regular visit (the old calendar booked both, two days apart), and a
    weekly site like Highlands must stop at its last September/early-October
    visit, not keep drafting until the drain-down.

    Only rolled-forward dates pass through here — a next visit typed onto the
    contract by hand is left as entered.
    """
    if not next_visit:
        return next_visit
    months = _winter_months(contract)
    if not months:
        return next_visit
    stop, resume = months
    month = getdate(next_visit).month
    in_window = stop <= month < resume if stop < resume else (month >= stop or month < resume)
    if not in_window:
        return next_visit
    return spring_resume_date(contract, next_visit)


def _park_until_spring(doc):
    """After the Winterization visit, every feature waits for spring.

    Covers the case the roll-forward cannot: a regular visit still pending
    after the fountain has been drained (winterization done before the
    month's regular visit), which would otherwise be drafted for a dry basin.
    """
    contract = _resolve_contract(doc)
    completion_date = getdate(doc.get("visit_date") or nowdate())
    resume = spring_resume_date(contract, completion_date)
    if not resume:
        return
    for row in contract.covered_features:
        frappe.db.set_value("Sapphire Contract Feature", row.name, "next_visit_date", resume)


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
