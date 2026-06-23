"""water_calc — stateless water-feature hydraulic calculator (read-only).

Runs one Phase-1 calculation through the pure ``water_engineering.engine`` and
returns the result WITH its math — value, formula, ordered steps, source
citations, warnings, and any A/B/C options the user must still choose — so the
assistant can show its work and the user can decide. No database writes; the
exact same dispatch backs the desk wizard's run_calc endpoint.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

_CALCS = (
    "basin_volume",
    "turnover_gpm",
    "weir_flow",
    "nozzle_array_flow",
    "nozzle_flow",
    "size_pipe",
    "hazen_williams_loss",
    "total_dynamic_head",
    "select_pump",
)


class WaterCalc(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "water_calc"  # must match module filename
        self.description = (
            "Run one fountain hydraulic calculation and return the result WITH the "
            "math behind it (formula, step-by-step working, source citations, "
            "warnings, and any A/B/C options to choose). Read-only. Pick 'calc' and "
            "pass its 'inputs': "
            "basin_volume {shape:rectangular|cylindrical, length_in, width_in, "
            "height_in | diameter_in}; turnover_gpm {volume_gal, turnovers_per_hr}; "
            "weir_flow {length_ft, head_in, contractions}; nozzle_array_flow "
            "{nozzle_count, gpm_each}; size_pipe {flow_gpm, length_ft, material, "
            "line:discharge|suction} (returns every size with velocity/status in "
            "options); hazen_williams_loss {flow_gpm, length_ft, id_in}; "
            "total_dynamic_head {segments:[...], static_lift_ft}; select_pump "
            "{flow_gpm, tdh_ft, candidates:[...]}. Orifice nozzle_flow is a stub "
            "(no Cd in the source data) — use nozzle_array_flow with a rated GPM."
        )
        self.category = "Water Engineering"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Water Feature Design"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "calc": {
                    "type": "string",
                    "enum": list(_CALCS),
                    "description": "Which Phase-1 hydraulic calculation to run.",
                },
                "inputs": {
                    "type": "object",
                    "description": "Calc-specific inputs (see the tool description for each calc's keys).",
                },
            },
            "required": ["calc"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        calc = (args.get("calc") or "").strip()
        if not calc:
            frappe.throw(_("A 'calc' name is required."), frappe.ValidationError)
        if not frappe.has_permission("Water Feature Design", "read"):
            frappe.throw(_("You do not have access to Water Feature Designs."), frappe.PermissionError)

        from erpnext_enhancements.water_engineering.api.water_design import _run_calc

        result = _run_calc(calc, args.get("inputs") or {})
        return {
            "success": True,
            "calc": calc,
            "result": result,
            "options": result.get("options") or [],
            "warnings": result.get("warnings") or [],
        }


__all__ = ["WaterCalc"]
