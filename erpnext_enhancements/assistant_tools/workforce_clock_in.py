"""workforce_clock_in — start a Time Kiosk session for the calling user.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

**This tool deliberately accepts no coordinates and no employee.**

No coordinates, because a lat/lng arriving as a tool argument is a number the
language model typed: forgeable, hallucinable, and exactly what a geofence
anchor must never be. The real fix comes from the user's own browser, which
hands it to ``api.time_kiosk.stash_location_fix`` over their session; this tool
calls ``clock_in_with_stashed_fix``, which reads it back server-side for the
calling user and nobody else. A refusal here therefore means "no fresh fix",
and the honest answer is to ask the person to share their location — not to
retry, and not to supply a coordinate from anywhere else.

No employee, because on-behalf is clock-OUT only (``workforce_clock_out``): a
supervisor's phone is not the crew member's, and opening a job is precisely what
the geofence exists to prove. That restriction is enforced by the endpoint, not
by this schema alone.

Kept out of the ``description`` below on purpose — that string is sent to the
model on every turn, so it carries what the model must know and nothing else.
"""

from typing import Any
import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool
from erpnext_enhancements.assistant_tools._gate import annotations_for

class WorkforceClockIn(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "workforce_clock_in"
        self.description = (
            "Start a Time Kiosk session (clock in) for the CALLING USER on a project. "
            "Clocks in the caller only — it cannot clock anyone else in; to close "
            "someone else's session use workforce_clock_out. "
            "Requires a location the user's own browser has shared through the Triton "
            "widget in ERPNext, and takes no coordinates itself. If it reports that no "
            "recent location fix is available, tell the user to share their location in "
            "the widget and ask again — do not retry, and never supply a coordinate."
        )
        self.category = "Time Tracking"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Job Interval"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name to clock into. Required."
                },
                "task": {
                    "type": "string",
                    "description": "Optional Task name."
                },
                "time_category": {
                    "type": "string",
                    "description": "Optional Time Category / Activity Type."
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for the work."
                }
            },
            "required": ["project"],
            "additionalProperties": False
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.api.time_kiosk import clock_in_with_stashed_fix, NO_LOCATION_FIX_MSG
        
        args = arguments or {}
        project = (args.get("project") or "").strip()
        if not project:
            frappe.throw(_("Project is required."), frappe.ValidationError)
        
        try:
            result = clock_in_with_stashed_fix(
                project=project,
                task=args.get("task"),
                time_category=args.get("time_category"),
                description=args.get("description"),
                source="Triton"
            )
            return {"success": True, "result": result}
        except Exception as e:
            if NO_LOCATION_FIX_MSG in str(e):
                # Return a clear, actionable result the model can relay verbatim
                return {
                    "success": False,
                    "error": "The user must open the Triton widget in ERPNext and share their location, then ask again.",
                }
            raise e

__all__ = ["WorkforceClockIn"]
