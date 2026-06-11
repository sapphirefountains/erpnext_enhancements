"""Data feed for the Maintenance Day Board desk page.

Whitelisted. ``get_day_board_data`` powers
``sapphire_maintenance/page/maintenance_day_board`` — a supervisor's live view
of today's maintenance operation in four columns: scheduled visit drafts,
technicians currently clocked into maintenance projects, visits submitted
today, and recent flagged visits (out-of-range chemistry / warranty claims).

Read-only; restricted to the page's roles (System Manager, Maintenance
Supervisor, Projects Manager) — enforced here as well as on the page, since
whitelisted methods are callable directly.
"""

import frappe
from frappe import _
from frappe.utils import add_days, nowdate

BOARD_ROLES = {"System Manager", "Maintenance Supervisor", "Projects Manager"}


@frappe.whitelist()
def get_day_board_data():
    """Return the four board columns. See module docstring."""
    if not BOARD_ROLES & set(frappe.get_roles()):
        frappe.throw(_("Not permitted."), frappe.PermissionError)

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
