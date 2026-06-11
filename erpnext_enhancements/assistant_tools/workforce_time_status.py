"""workforce_time_status — Time Kiosk clock status and hour rollups (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Deliberately excludes GPS data: Time Kiosk Log is privacy-sensitive and stays
behind the desk Location Timeline's role gate.
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime, nowdate, time_diff_in_seconds
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, project_title_map

_INTERVAL_FIELDS = [
    "name", "employee", "project", "task", "status", "time_category",
    "start_time", "end_time", "total_paused_seconds", "last_pause_time",
    "description", "sync_status",
]


def _elapsed_seconds(row, ref=None) -> int:
    """Worked seconds for an interval: wall clock minus accumulated pauses,
    minus the still-running pause when the interval is currently Paused."""
    if not row.get("start_time"):
        return 0
    ref = ref or now_datetime()
    end = get_datetime(row["end_time"]) if row.get("end_time") else ref
    seconds = time_diff_in_seconds(end, get_datetime(row["start_time"]))
    seconds -= row.get("total_paused_seconds") or 0
    if row.get("status") == "Paused" and row.get("last_pause_time"):
        seconds -= time_diff_in_seconds(end, get_datetime(row["last_pause_time"]))
    return max(0, int(seconds))


class WorkforceTimeStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "workforce_time_status"  # must match module filename
        self.description = (
            "Field-crew time tracking from the Time Kiosk (Job Intervals). Modes: "
            "'now' (default) — every interval currently Open or Paused, i.e. who is "
            "clocked in, where, and for how long (worked_hours excludes paused time); "
            "'me' — the calling user's own clock status with task and attachments; "
            "'day_summary' — one day's intervals (default today) with hour rollups per "
            "employee and per project; 'history' — the same rollups over a date range "
            "(default last 7 days) plus Timesheet sync health (sync_status='Failed' "
            "means the interval never reached its draft Timesheet — flag those). "
            "Visibility follows Job Interval permissions: regular employees may only "
            "see their own intervals; supervisors see everyone. GPS/location data is "
            "intentionally not available through this tool."
        )
        self.category = "Time Tracking"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Job Interval"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["now", "me", "day_summary", "history"],
                    "default": "now",
                    "description": "See tool description",
                },
                "employee": {"type": "string", "description": "Filter by Employee docname"},
                "project": {"type": "string", "description": "Filter by Project docname"},
                "date": {"type": "string", "description": "day_summary mode: the day (YYYY-MM-DD, default today)"},
                "from_date": {"type": "string", "description": "history mode: range start (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "history mode: range end (YYYY-MM-DD)"},
                "limit": {"type": "integer", "default": 50, "description": "Max intervals returned (cap 200)"},
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mode = arguments.get("mode") or "now"
        if mode == "me":
            return self._me()
        if mode == "now":
            filters = {"status": ["in", ["Open", "Paused"]]}
            window = None
        elif mode == "day_summary":
            day = arguments.get("date") or nowdate()
            window = (day, day)
            filters = {}
        elif mode == "history":
            to_date = arguments.get("to_date") or nowdate()
            from_date = arguments.get("from_date") or add_days(to_date, -7)
            window = (from_date, to_date)
            filters = {}
        else:
            frappe.throw(_("Unknown mode: {0}").format(mode), frappe.ValidationError)

        if window:
            # Compare date strings against the Datetime column: ">= D" and
            # "< D+1" brackets the whole end day without time arithmetic.
            filters["start_time"] = ["between", [window[0], add_days(window[1], 1)]]
        if arguments.get("employee"):
            filters["employee"] = arguments["employee"]
        if arguments.get("project"):
            filters["project"] = arguments["project"]

        # frappe.get_list enforces role + user permissions (regular employees
        # are narrowed to their own intervals by the site's user permissions).
        intervals = frappe.get_list(
            "Job Interval",
            filters=filters,
            fields=_INTERVAL_FIELDS,
            order_by="start_time asc",
            limit=clamp_limit(arguments.get("limit"), 50, 200),
        )
        self._decorate(intervals)

        result: dict[str, Any] = {
            "success": True,
            "mode": mode,
            "as_of": str(now_datetime()),
            "intervals": intervals,
        }
        if window:
            result["window"] = {"from_date": str(window[0]), "to_date": str(window[1])}
            result["totals"] = self._totals(intervals)
            failed = [i["name"] for i in intervals if i.get("sync_status") == "Failed"]
            result["timesheet_sync"] = {
                "failed_count": len(failed),
                "failed_intervals": failed[:20],
            }
        return result

    def _me(self) -> dict[str, Any]:
        from erpnext_enhancements.api.time_kiosk import get_current_status

        status = get_current_status()
        if status is None:
            return {
                "success": True,
                "mode": "me",
                "status": None,
                "note": "No Employee record is linked to the current user.",
            }
        return {"success": True, "mode": "me", "status": status}

    @staticmethod
    def _decorate(intervals: list[dict[str, Any]]) -> None:
        """Attach employee_name, project_title, and worked hours to each row."""
        employees = sorted({i["employee"] for i in intervals if i.get("employee")})
        employee_names = {}
        if employees:
            employee_names = dict(
                frappe.get_all(
                    "Employee",
                    filters={"name": ["in", employees]},
                    fields=["name", "employee_name"],
                    as_list=True,
                )
            )
        titles = project_title_map(i.get("project") for i in intervals)
        ref = now_datetime()
        for interval in intervals:
            interval["employee_name"] = employee_names.get(interval.get("employee")) or interval.get("employee")
            interval["project_title"] = titles.get(interval.get("project")) or interval.get("project")
            interval["worked_hours"] = round(_elapsed_seconds(interval, ref) / 3600, 2)
            for key in ("start_time", "end_time", "last_pause_time"):
                if interval.get(key):
                    interval[key] = str(interval[key])

    @staticmethod
    def _totals(intervals: list[dict[str, Any]]) -> dict[str, Any]:
        by_employee: dict[str, float] = {}
        by_project: dict[str, float] = {}
        for interval in intervals:
            hours = interval.get("worked_hours") or 0
            employee = interval.get("employee_name") or interval.get("employee") or "Unknown"
            project = interval.get("project_title") or interval.get("project") or "No project"
            by_employee[employee] = round(by_employee.get(employee, 0) + hours, 2)
            by_project[project] = round(by_project.get(project, 0) + hours, 2)
        return {
            "total_hours": round(sum(by_employee.values()), 2),
            "by_employee": by_employee,
            "by_project": by_project,
        }


__all__ = ["WorkforceTimeStatus"]
