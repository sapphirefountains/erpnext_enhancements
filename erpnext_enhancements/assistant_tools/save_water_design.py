"""save_water_design — create or update a Water Feature Design (gated).

Saves a Fountain Designer design the way the page does (``api/designer.save_design``: the same
permission checks, the same server-side derive, the same refusal of an issued design) and returns
what the page would then show: the verdict, the rollups, the issue counts, the designer link and the
print and packet links. The write companion to ``water_calc`` / ``water_design_status``.

Two payloads (v1.771.0, phase H of the rebuild):

* ``model``: a schema-2 design model (``docs/fountain-designer/PLAN.md`` 3.3), the whole design.
* the old tables, ``basins`` / ``features`` / ``pipe_segments`` / ``pumps`` with ``global_inputs``:
  laid onto the stored model through ``engine.model.migrate`` and ``api/designer.merge_legacy_payload``,
  each table sent replacing that section and pinning it ``custom``. Whatever has no place in a
  schema-2 model comes back under ``ignored``, never dropped in silence.

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

# The exact fitting/valve and equipment catalog the engine's TDH math keys on
# (engine/data/fittings.py). Generated here so the AI is told the valid
# `type` values for fittings_json / components_json — it can't hallucinate a name
# the engine would silently ignore, and the list can never drift from the math.
from erpnext_enhancements.water_engineering.engine.data.fittings import (
    COMPONENT_COEFF,
    FITTING_K,
)

_FITTING_TYPES = ", ".join(FITTING_K)
_COMPONENT_TYPES = ", ".join(COMPONENT_COEFF)

# The old payload's tables, passed on as they came.
LEGACY_TABLES = ("basins", "features", "pipe_segments", "pumps", "electrical_loads")
# The record's own fields (api/designer.DESIGN_FIELDS), top-level or inside the old global_inputs.
RECORD_FIELDS = ("design_title", "project", "opportunity", "customer")


class SaveWaterDesign(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "save_water_design"  # must match module filename
        self.description = (
            "Create or update a Fountain Designer design (a Water Feature Design), save it the way the "
            "Fountain Designer page does (the server re-derives it), and return what the page then shows: "
            "the verdict (good/warn/bad with its headline), the rollups (flows, heads, pump, power, basin "
            "gallons, budget), the issue counts, 'designer_url' (open it in the Fountain Designer), and the "
            "print and packet links. Provide 'design' to update one (omit to create), and 'design_title', "
            "'project', 'opportunity', 'customer' for the record. Send EITHER 'model', a whole schema-2 "
            "design model (the one water_design_status and the page read), which replaces the stored one; "
            "OR the older row lists 'basins', 'features', 'pipe_segments', 'pumps' with 'global_inputs', "
            "each list REPLACING that part of the design (parts not sent are kept, and each part sent is "
            "pinned as the designer's own so the next derive keeps it). 'pipe_segments' is laid from the "
            "features, so send it with 'features'. What has no place in the design (electrical_loads, "
            "turnover_per_hr, hazen_williams_c, static_lift_ft, status) comes back listed under 'ignored'. "
            "An issued design cannot change. This tool WRITES: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and saves nothing until a human "
            "confirms in ERPNext — then call check_ai_pending_action with that action_id. Never claim the "
            "design was saved from the envelope alone. Use water_calc to compute and water_design_status "
            "to read."
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
                "design_title": {"type": "string", "description": "What the customer calls it."},
                "project": {
                    "type": "string",
                    "description": "Project to link the design to (recommended when creating).",
                },
                "opportunity": {"type": "string", "description": "Opportunity to link the design to."},
                "customer": {"type": "string", "description": "Customer to link the design to."},
                "model": {
                    "type": "object",
                    "description": (
                        "A whole schema-2 Fountain Designer model ({schema: 2, mode, kit, site, features, "
                        "basins, pump_room, network, equipment, setup, ...}), e.g. the 'model' of a design "
                        "read with water_design_status and edited. Replaces the stored model; the row "
                        "lists and global_inputs are then ignored."
                    ),
                },
                "global_inputs": {
                    "type": "object",
                    "description": (
                        "With the row lists: design_title, venue_type, governing_code, pipe_material. "
                        "turnover_per_hr, hazen_williams_c, static_lift_ft and status have no place in a "
                        "Fountain Designer model and come back under 'ignored'."
                    ),
                },
                "basins": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Basin rows {shape, length_in, width_in, height_in, diameter_in} (replaces the basins).",
                },
                "features": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Feature rows {feature_type, weir_length_ft, head_in, contractions, nozzle_count, gpm_each} (replaces the features).",
                },
                "pipe_segments": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Segment rows {segment_label, line_type: Discharge|Suction, "
                        "system: Circulation|Therapy-Jet, flow_gpm, "
                        "material: SCH40 PVC|SCH80 PVC|Type K Copper, nominal_size, pipe_length_ft, "
                        "fittings_json, components_json} (replaces the piping; send with 'features'). "
                        "Leave flow_gpm blank/0 to carry the full design flow. fittings_json and "
                        'components_json are JSON-encoded lists of {"type": <exact catalog name>, "qty": <int>} '
                        "— a type not in the catalog is silently ignored, so use these names verbatim. "
                        f"Valid fittings_json types: {_FITTING_TYPES}. "
                        f"Valid components_json types: {_COMPONENT_TYPES}."
                    ),
                },
                "pumps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Pump rows {pump_item, part_number, rated_gpm, rated_tdh_ft}: the first pump_item becomes the design's pump, solved on its own curve.",
                },
                "electrical_loads": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Accepted for older callers and listed under 'ignored': the Fountain Designer derives the electrical loads.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        name = args.get("design") or None
        if name == "new":
            name = None
        if not frappe.has_permission("Water Feature Design", "write" if name else "create"):
            frappe.throw(
                _("You do not have permission to create or edit Water Feature Designs."),
                frappe.PermissionError,
            )

        inputs = dict(args.get("global_inputs") or {})
        # The record's fields, from the top level first, else the old global_inputs.
        fields = {k: inputs.pop(k) for k in RECORD_FIELDS if k in inputs}
        fields.update({k: args[k] for k in RECORD_FIELDS if args.get(k) is not None})
        status = inputs.pop("status", None)
        tables = {t: args[t] for t in LEGACY_TABLES if args.get(t) is not None}

        from erpnext_enhancements.water_engineering.api import designer

        saved, ignored = designer.tool_save(name, args.get("model"), tables, inputs, fields)
        if status is not None:
            ignored.append("status (it follows the design's derive; review and issue it in ERPNext)")
        frappe.db.commit()
        state = designer.design_status(saved, include_results=False)
        return {"success": True, "created": not name, "ignored": ignored, **state}


__all__ = ["SaveWaterDesign"]
