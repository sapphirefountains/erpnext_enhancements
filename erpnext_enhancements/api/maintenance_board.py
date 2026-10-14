"""Data feed for the Maintenance Day Board desk page.

Whitelisted. ``get_day_board_data`` powers
``sapphire_maintenance/page/maintenance_day_board`` — a supervisor's live view
of today's maintenance operation in four columns: scheduled visit drafts,
technicians currently clocked into maintenance projects, visits submitted
today, and recent flagged visits (out-of-range chemistry / warranty claims).

Read-only; restricted to the page's roles (System Manager, Maintenance
Supervisor, Projects Manager) — enforced here as well as on the page, since
whitelisted methods are callable directly.

Another day (v1.662.0). The page keeps the day it shows in its address, so
``date`` (YYYY-MM-DD) asks for that day's board. Today, named or not, is
answered exactly as before. Another day is the same four columns read as of
that day: the drafts scheduled for it (``scheduled_visit_date``), the
maintenance clock-ins that started on it, the visits submitted on it, and the
flagged visits of the seven days up to it.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

BOARD_ROLES = {"System Manager", "Maintenance Supervisor", "Projects Manager"}


def _board_day(date):
    """``date`` as a datetime.date, strictly YYYY-MM-DD; None for no date."""
    if not date:
        return None
    try:
        return datetime.date.fromisoformat(str(date).strip()[:10])
    except ValueError:
        frappe.throw(
            _("{0} is not a date in the form YYYY-MM-DD.").format(
                frappe.bold(frappe.utils.escape_html(str(date)))
            )
        )


@frappe.whitelist()
def get_day_board_data(date=None):
    """Return the four board columns for today, or for ``date``. See module docstring."""
    if not BOARD_ROLES & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    today = nowdate()
    day = _board_day(date)
    if day is None or day == getdate(today):
        return _board(today, None)
    return _board(str(day), str(add_days(day, 1)))


def _board(day, next_day):
    """The four columns as of ``day``. ``next_day`` is None for today: the live
    board, whose drafts are every open draft and whose clock-ins are the ones
    still open; otherwise the first day after ``day``, which bounds the rest."""
    scheduled_filters = [["docstatus", "=", 0]]
    if next_day:
        scheduled_filters.append(["scheduled_visit_date", "=", day])
    scheduled = frappe.get_all(
        "Sapphire Maintenance Record",
        filters=scheduled_filters,
        fields=[
            "name", "project", "serial_no", "visit_label", "technician",
            "completion_percent", "modified",
        ],
        order_by="creation asc",
        limit=50,
    )

    # Today: the clock-ins still open. Another day: every one that started on it.
    when = (
        "ji.status IN ('Open', 'Paused')"
        if not next_day
        else "ji.start_time >= %(day)s AND ji.start_time < %(next_day)s"
    )
    in_progress = frappe.db.sql(f"""
        SELECT ji.name, ji.project, ji.start_time, emp.employee_name
        FROM `tabJob Interval` ji
        JOIN `tabEmployee` emp ON ji.employee = emp.name
        WHERE {when}
          AND ji.project IN (
                SELECT project FROM `tabSapphire Maintenance Contract`
                WHERE status = 'Active' AND IFNULL(project, '') != ''
          )
        ORDER BY ji.start_time ASC
    """, {"day": day, "next_day": next_day}, as_dict=True)

    # Bounded above by the next day only for another day: today's board has no
    # upper bound, as it never had.
    until = [["modified", "<", next_day]] if next_day else []

    submitted_today = frappe.get_all(
        "Sapphire Maintenance Record",
        filters=[["docstatus", "=", 1], ["modified", ">=", day], *until],
        fields=[
            "name", "project", "serial_no", "visit_label", "technician",
            "completion_percent", "has_out_of_range_readings", "warranty_rma_flag",
        ],
        order_by="modified desc",
        limit=50,
    )

    flagged = frappe.get_all(
        "Sapphire Maintenance Record",
        filters=[["docstatus", "=", 1], ["modified", ">=", str(add_days(day, -7))], *until],
        or_filters=[["has_out_of_range_readings", "=", 1], ["warranty_rma_flag", "=", 1]],
        fields=[
            "name", "project", "serial_no", "technician", "modified",
            "has_out_of_range_readings", "warranty_rma_flag",
        ],
        order_by="modified desc",
        limit=50,
    )

    # one title lookup for every project on the board
    projects = {row.project for rows in (scheduled, in_progress, submitted_today, flagged)
                for row in rows if row.get("project")}
    titles = {}
    if projects:
        titles = dict(frappe.get_all(
            "Project",
            filters={"name": ["in", list(projects)]},
            fields=["name", "project_name"],
            as_list=True,
        ))
    for rows in (scheduled, in_progress, submitted_today, flagged):
        for row in rows:
            row["project_title"] = titles.get(row.get("project")) or row.get("project")

    return {
        "scheduled": scheduled,
        "in_progress": in_progress,
        "submitted_today": submitted_today,
        "flagged": flagged,
    }
