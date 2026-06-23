"""control_panel_status — read a Control Panel Design's state (read-only).

Returns a fountain control panel's submittal state for the assistant to flesh
out or report: nameplate/power, the UI screens, the I/O list, the interlock
checklist, and the sizing rollups (lighting watts/amps/relays, solenoid relays).
With no ``panel`` it lists recent Control Panel Designs (optionally by project).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class ControlPanelStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "control_panel_status"  # must match module filename
        self.description = (
            "Read a fountain Control Panel Design (the controller submittal): power/"
            "nameplate (line voltage, phase, FLA, main breaker), the UI screens, the "
            "I/O point list, the interlock checklist (low water / high wind / circ-"
            "pump / E-stop / thermal / safe-state), and the sizing rollups (lighting "
            "total watts / current / relay count, solenoid relay count). Pass 'panel' "
            "for one design; omit it to LIST recent panels (optionally filter by "
            "'project'). Read-only — use it to report on or continue building a "
            "control panel. Size lighting/solenoids with water_calc (calc_lighting / "
            "calc_solenoid_relays)."
        )
        self.category = "Water Engineering"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Control Panel Design"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "panel": {
                    "type": "string",
                    "description": "Control Panel Design docname (e.g. CPD-2026-00001). Omit to list panels.",
                },
                "project": {
                    "type": "string",
                    "description": "Optional Project docname to filter the panel list.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        if not frappe.has_permission("Control Panel Design", "read"):
            frappe.throw(_("You do not have access to Control Panel Designs."), frappe.PermissionError)

        from erpnext_enhancements.water_engineering.api.water_design import control_panel_state

        return {"success": True, **control_panel_state(args.get("panel"), args.get("project"))}


__all__ = ["ControlPanelStatus"]
