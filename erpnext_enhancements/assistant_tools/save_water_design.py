"""save_water_design — persist gathered inputs to a Water Feature Design (gated).

Creates or updates a Water Feature Design from the inputs the assistant has
gathered (global settings + basin / feature / pipe-segment / pump rows), saves
it (which re-runs the engine and refreshes the rollups + audit trail), and
returns the design's new state. The write companion to ``water_calc`` /
``water_design_status``.

WRITES — and therefore goes through the AI write-confirmation gate (``_gate.py``,
``APP_MUTATING`` + Low risk): when AI write gating is ON this returns an
``awaiting_user_confirmation`` envelope and an **AI Pending Action** instead of
saving; the design is written only after a human clicks Confirm & Execute (the
gate re-runs this ``execute`` as the confirming user), after which
``check_ai_pending_action`` returns the saved result. Never claim the design was
saved from the envelope alone.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class SaveWaterDesign(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "save_water_design"  # must match module filename
        self.description = (
            "Create or update a Water Feature Design from gathered inputs, then "
            "recompute and return its state. Provide 'design' to update an existing "
            "one (omit to create), 'project' to link it, 'global_inputs' "
            "{design_title, status, turnover_per_hr, hazen_williams_c, "
            "pipe_material, static_lift_ft}, and any of the row lists 'basins', "
            "'features', 'pipe_segments', 'pumps', 'electrical_loads' (each REPLACES "
            "that table). This tool WRITES: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and saves nothing "
            "until a human confirms in ERPNext — then call check_ai_pending_action "
            "with that action_id. Never claim the design was saved from the envelope "
            "alone. Use water_calc to compute and water_design_status to read."
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
                    "description": "Existing Water Feature Design docname to update; omit to create a new one.",
                },
                "project": {
                    "type": "string",
                    "description": "Project to link the design to (recommended when creating).",
                },
                "global_inputs": {
                    "type": "object",
                    "description": "Parent fields: design_title, status, turnover_per_hr, hazen_williams_c, pipe_material, static_lift_ft.",
                },
                "basins": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Basin rows {shape, length_in, width_in, height_in, diameter_in} (replaces the table).",
                },
                "features": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Feature rows {feature_type, weir_length_ft, head_in, contractions, nozzle_count, gpm_each} (replaces the table).",
                },
                "pipe_segments": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Segment rows {segment_label, line_type, flow_gpm, material, nominal_size, pipe_length_ft, fittings_json, components_json} (replaces the table).",
                },
                "pumps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Pump candidate rows {pump_item, part_number, rated_gpm, rated_tdh_ft} (replaces the table).",
                },
                "electrical_loads": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Electrical rows {pump_item, hp, phase, voltage, fla_amps, control_method} (replaces the table).",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        if not frappe.has_permission("Water Feature Design", "create"):
            frappe.throw(
                _("You do not have permission to create or edit Water Feature Designs."),
                frappe.PermissionError,
            )

        fields = dict(args.get("global_inputs") or {})
        if args.get("project"):
            fields["project"] = args["project"]
        payload = {"design": args.get("design"), "fields": fields}
        for table in ("basins", "features", "pipe_segments", "pumps", "electrical_loads"):
            if table in args and args[table] is not None:
                payload[table] = args[table]

        from erpnext_enhancements.water_engineering.api.water_design import _save_design, design_state

        doc = _save_design(payload)
        frappe.db.commit()
        return {"success": True, "design": doc.name, **design_state(doc.name)}


__all__ = ["SaveWaterDesign"]
