"""Data feed for the Maintenance Day Board desk page.

Whitelisted. ``get_day_board_data`` powers
``sapphire_maintenance/page/maintenance_day_board`` — a supervisor's live view
of today's maintenance operation in four columns: scheduled visit drafts,
technicians currently clocked into maintenance projects, visits submitted
today, and recent flagged visits (out-of-range chemistry / warranty claims).

Read-only; restricted to the page's roles (System Manager, Maintenance
Supervisor, Projects Manager, Maintenance Manager) — enforced here as well as on
the page, since whitelisted methods are callable directly.

Maintenance Manager joined in v1.680.0. The Production Hub, which links the
board, is gated on it, so the hub promised a page the role alone could not open;
the board answered only because, on prod on 2026-10-14, all 14 holders of
Maintenance Manager also held Projects Manager. Nobody's access changed that day.
Keep this set and the page JSON's roles the same (tests/test_s4a_hub_links.py).

Another day (v1.662.0). The page keeps the day it shows in its address, so
``date`` (YYYY-MM-DD) asks for that day's board. Today, named or not, runs
exactly the queries it always has (``_today_board``). A ``date`` that is not a
YYYY-MM-DD calendar day between 1900 and 2999 is treated as no date: the page
ignores one the same way, and a date the server cannot step a day past (9999-12-31)
would otherwise be a 500.

Another day (``_day_board``) is the same four columns read as of that day, and
it is dated by the visit, not by ``modified``: billing writes the Sales Invoice
link onto a submitted visit (``api/maintenance_billing.py``) and the sign-off
writes its stamps (``sapphire_maintenance/sign_off.py``), and both move
``modified``, so a visit made on the 1st and invoiced on the 3rd would sit on
the 3rd's board. The visit's day is its ``visit_date`` — the day the work was
done, which the move into review stamps when it is blank (v1.595.1) — and, for
an older visit that never had one, the day of its ``clock_in_time``. A visit
with neither is on no past day's board. So another day shows: the drafts
scheduled for it (``scheduled_visit_date``), the maintenance clock-ins that
started on it, the visits done on it, and the flagged visits done in the seven
days up to it.
"""

import datetime
import re

import frappe
from frappe import _
from frappe.utils import add_days, nowdate

BOARD_ROLES = {"System Manager", "Maintenance Supervisor", "Projects Manager", "Maintenance Manager"}

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _board_day(date):
    """``date`` as a datetime.date when it is a YYYY-MM-DD calendar day between
    1900 and 2999, else None (today). Never raises."""
    value = str(date or "").strip()
    if not _DAY.match(value):
        return None
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return day if 1900 <= day.year <= 2999 else None


@frappe.whitelist()
def get_day_board_data(date=None):
    """Return the four board columns for today, or for ``date``. See module docstring."""
    if not BOARD_ROLES & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    day = _board_day(date)
    if day is None or str(day) == str(nowdate()):
        return _today_board()
    return _day_board(str(day), str(day + datetime.timedelta(days=1)))


def _today_board():
    """Today's live board: every open draft, the clock-ins still open, and the
    visits submitted (by ``modified``) today and flagged in the last seven days."""
    today = nowdate()

    scheduled = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"docstatus": 0},
        fields=[
            "name", "project", "serial_no", "visit_label", "technician",
            "completion_percent", "modified",
        ],
        order_by="creation asc",
        limit=50,
    )

    in_progress = frappe.db.sql("""
        SELECT ji.name, ji.project, ji.start_time, emp.employee_name
        FROM `tabJob Interval` ji
        JOIN `tabEmployee` emp ON ji.employee = emp.name
        WHERE ji.status IN ('Open', 'Paused')
          AND ji.project IN (
                SELECT project FROM `tabSapphire Maintenance Contract`
                WHERE status = 'Active' AND IFNULL(project, '') != ''
          )
        ORDER BY ji.start_time ASC
    """, as_dict=True)

    submitted_today = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"docstatus": 1, "modified": [">=", today]},
        fields=[
            "name", "project", "serial_no", "visit_label", "technician",
            "completion_percent", "has_out_of_range_readings", "warranty_rma_flag",
        ],
        order_by="modified desc",
        limit=50,
    )

    flagged = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"docstatus": 1, "modified": [">=", add_days(today, -7)]},
        or_filters=[["has_out_of_range_readings", "=", 1], ["warranty_rma_flag", "=", 1]],
        fields=[
            "name", "project", "serial_no", "technician", "modified",
            "has_out_of_range_readings", "warranty_rma_flag",
        ],
        order_by="modified desc",
        limit=50,
    )

    return _with_titles(scheduled, in_progress, submitted_today, flagged)


# The day a submitted visit was done: its visit_date, else the day of its clock-in.
# %(from_day)s <= that day < %(next_day)s, both bound parameters.
_VISIT_DONE_BETWEEN = """
    (
        (visit_date >= %(from_day)s AND visit_date < %(next_day)s)
        OR (visit_date IS NULL AND clock_in_time >= %(from_day)s AND clock_in_time < %(next_day)s)
    )
"""


def _day_board(day, next_day):
    """Another day's board, ``day`` <= the visit's day < ``next_day``. See the
    module docstring for why the visit's own date, not ``modified``."""
    scheduled = frappe.get_all(
        "Sapphire Maintenance Record",
        filters={"docstatus": 0, "scheduled_visit_date": day},
        fields=[
            "name", "project", "serial_no", "visit_label", "technician",
            "completion_percent", "modified",
        ],
        order_by="creation asc",
        limit=50,
    )

    in_progress = frappe.db.sql("""
        SELECT ji.name, ji.project, ji.start_time, emp.employee_name
        FROM `tabJob Interval` ji
        JOIN `tabEmployee` emp ON ji.employee = emp.name
        WHERE ji.start_time >= %(day)s AND ji.start_time < %(next_day)s
          AND ji.project IN (
                SELECT project FROM `tabSapphire Maintenance Contract`
                WHERE status = 'Active' AND IFNULL(project, '') != ''
          )
        ORDER BY ji.start_time ASC
    """, {"day": day, "next_day": next_day}, as_dict=True)

    submitted = frappe.db.sql(f"""
        SELECT name, project, serial_no, visit_label, technician,
            completion_percent, has_out_of_range_readings, warranty_rma_flag
        FROM `tabSapphire Maintenance Record`
        WHERE docstatus = 1 AND {_VISIT_DONE_BETWEEN}
        ORDER BY COALESCE(visit_date, DATE(clock_in_time)) DESC, name DESC
        LIMIT 50
    """, {"from_day": day, "next_day": next_day}, as_dict=True)

    flagged = frappe.db.sql(f"""
        SELECT name, project, serial_no, technician, modified,
            has_out_of_range_readings, warranty_rma_flag
        FROM `tabSapphire Maintenance Record`
        WHERE docstatus = 1 AND {_VISIT_DONE_BETWEEN}
          AND (has_out_of_range_readings = 1 OR warranty_rma_flag = 1)
        ORDER BY COALESCE(visit_date, DATE(clock_in_time)) DESC, name DESC
        LIMIT 50
    """, {"from_day": str(add_days(day, -7)), "next_day": next_day}, as_dict=True)

    return _with_titles(scheduled, in_progress, submitted, flagged)


def _with_titles(scheduled, in_progress, submitted_today, flagged):
    """The four columns, each row with its project's title."""
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
