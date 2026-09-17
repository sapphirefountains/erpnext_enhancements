"""workforce_clock_out — close a Time Kiosk session, for yourself or a crew member.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Two shapes, and the difference is the whole point:

* **Self-service** (no ``employee``) — a person closing their own time record,
  the same authority the Time Kiosk already gives them by tapping a button.
* **On behalf** (an ``employee``) — authority over someone else's record and
  someone else's pay. Restricted to ``TIMELINE_MANAGER_ROLES`` and stamped with
  who asked for it.

Per ADR 0014 that distinction is also what decides whether the AI write gate
asks a human: self-service executes, on-behalf becomes an AI Pending Action.
The gate is the human-in-the-loop layer and NOT the authorization — it ships
dormant, so the role check that actually refuses an unauthorised call lives in
``api.time_kiosk.close_interval_for_employee``.

The close is always unanchored: nothing here invents a coordinate for somebody
whose phone was not consulted.
"""

from typing import Any
import frappe
from frappe_assistant_core.core.base_tool import BaseTool
from erpnext_enhancements.assistant_tools._gate import annotations_for

class WorkforceClockOut(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "workforce_clock_out"
        self.description = (
            "Clock out of an active job interval. "
            "Omitting 'employee' clocks the caller out. "
            "Naming another employee requires a supervisor role and is recorded against the requester. "
            "The clock-out is unanchored (no GPS). "
            "When the job-photo gate is unmet a 'skip_reason' is recorded verbatim. "
            "ASK the user for a real reason rather than inventing one."
        )
        self.category = "Time Tracking"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Job Interval"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "employee": {
                    "type": "string",
                    "description": "Optional Employee to clock out (supervisor role required)."
                },
                "skip_reason": {
                    "type": "string",
                    "description": "Optional reason for skipping the photo."
                }
            },
            "additionalProperties": False
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.api.time_kiosk import close_interval_for_employee
        args = arguments or {}
        # Surface the server's refusal text unchanged when it throws.
        result = close_interval_for_employee(
            employee=args.get("employee"),
            skip_reason=args.get("skip_reason"),
            source="Triton"
        )
        return {"success": True, "result": result}

__all__ = ["WorkforceClockOut"]
