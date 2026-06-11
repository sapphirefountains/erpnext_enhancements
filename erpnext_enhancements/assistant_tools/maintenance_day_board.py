"""maintenance_day_board — live maintenance operations board (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool


class MaintenanceDayBoard(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "maintenance_day_board"  # must match module filename
        self.description = (
            "Live maintenance operations board for fountain/water-feature field service. "
            "Returns four columns: scheduled visit drafts (unsubmitted Sapphire Maintenance "
            "Records), technicians currently clocked into maintenance projects, visits "
            "submitted today, and flagged visits from the last 7 days (out-of-range "
            "chemistry readings or warranty/RMA claims). Every row carries project_title "
            "and technician. Use this first for any 'what is happening in maintenance "
            "today' question, then drill into specific visits with maintenance_visit_history. "
            "Requires the Maintenance Supervisor, Projects Manager, or System Manager role."
        )
        self.category = "Maintenance Operations"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Sapphire Maintenance Record"
        self.inputSchema = {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # Enforces its own supervisor-role gate; a frappe.PermissionError from it
        # surfaces through BaseTool._safe_execute as a permission denial.
        from erpnext_enhancements.api.maintenance_board import get_day_board_data

        return {
            "success": True,
            "as_of": frappe.utils.now(),
            "board": get_day_board_data(),
        }


__all__ = ["MaintenanceDayBoard"]
