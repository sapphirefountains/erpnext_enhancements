"""water_design_status — read a Water Feature Design's running state (read-only).

Returns where a design stands so the assistant knows what to ask next and never
re-asks: the headline rollups (basin gallons, circulation/design GPM, TDH,
selected pump), completion %, the calculations still missing inputs
(``next_inputs_needed``), child-row counts, and the calculation audit trail.
With no ``design`` it lists the recent designs the user can see (optionally
filtered by ``project``) — the resume entry point for a design session.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class WaterDesignStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "water_design_status"  # must match module filename
        self.description = (
            "Read a Water Feature Design's running state to drive a calculation "
            "session: rollups (basin gallons, circulation GPM, design flow, TDH, "
            "selected pump), completion %, what inputs are still needed "
            "(next_inputs_needed), child-row counts, and the calculation audit "
            "trail (each calc's value, formula, steps, citations). Pass 'design' "
            "for one design; omit it to LIST recent designs (optionally filter by "
            "'project') to resume or start one. Read-only. Always call this first "
            "in a design conversation, then ask only for the missing inputs."
        )
        self.category = "Water Engineering"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Water Feature Design"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "design": {
                    "type": "string",
                    "description": "Water Feature Design docname (e.g. WFD-2026-00001). Omit to list designs.",
                },
                "project": {
                    "type": "string",
                    "description": "Optional Project docname to filter the design list.",
                },
                "include_results": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include the per-calculation audit trail for a specific design.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        if not frappe.has_permission("Water Feature Design", "read"):
            frappe.throw(_("You do not have access to Water Feature Designs."), frappe.PermissionError)

        from erpnext_enhancements.water_engineering.api.water_design import design_state

        state = design_state(
            design=args.get("design"),
            project=args.get("project"),
            include_results=args.get("include_results", True),
        )
        return {"success": True, **state}


__all__ = ["WaterDesignStatus"]
