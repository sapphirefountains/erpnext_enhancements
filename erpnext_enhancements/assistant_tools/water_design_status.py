"""water_design_status — read a Water Feature Design the way the Fountain Designer shows it (read-only).

Returns where a design stands so the assistant knows what to say and what to ask next: the same
verdict the page's right panel shows, the rollups of the last derive, the typed issues with their fix
hints and remedies, the per-section readiness, the counts, the canvas the Triton chat draws, and the
links: ``designer_url`` (the Fountain Designer on this design), the form, every paper's print link and
the submittal packet's. With ``include_model`` it adds the schema-2 ``model`` itself, the payload
``save_water_design`` takes back after an edit. With no ``design`` it lists the recent designs the
user can see (optionally filtered by ``project``), the resume entry point.

Since v1.771.0 (phase H) it reads the record ``api/designer.design_status`` builds from
``results_json``, ``design_issues_json`` and ``readiness_json``; until then it read
``api/water_design.design_state`` and the schema-1 counts.

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
            "Read a Fountain Designer design (a Water Feature Design) as the page shows it: the 'verdict' "
            "(level good/warn/bad and its headline, the same as the page's verdict panel), the 'rollups' "
            "of the last derive (design and operating flow, TDH, selected pump, pump kW, basin gallons, "
            "budget, O&M a year), typed 'issues' (severity blocker/warning/info, with fix hints, remedies, "
            "citations and whether a warning is signed off), per-section 'readiness' with the calc-ready "
            "and issue-ready gates, 'next_inputs_needed', counts, 'designer_url' (open it in the Fountain "
            "Designer), 'form_url', and the print links: 'prints' (each paper) and 'packet' (the "
            "submittal packet). With include_model it also returns 'model', the schema-2 design itself, "
            "to edit and send back through save_water_design. Pass 'design' for one design; omit it to LIST recent designs (optionally "
            "filter by 'project') to resume or start one. Read-only. Always call this first in a design "
            "conversation; report any BLOCKER issues before asking the next question (blockers stop "
            "Reviewed and Issued), then ask only for what is still needed."
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
                    "description": "Include each engine calculation of the last derive (value, formula, steps, citations).",
                },
                "include_model": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include the schema-2 'model' (large): only to edit the design and save it back.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}
        if not frappe.has_permission("Water Feature Design", "read"):
            frappe.throw(_("You do not have access to Water Feature Designs."), frappe.PermissionError)

        from erpnext_enhancements.water_engineering.api import designer

        if args.get("design"):
            state = designer.design_status(
                args["design"],
                include_results=args.get("include_results", True) is not False,
                include_model=args.get("include_model") is True,
            )
        else:
            state = designer.design_list(args.get("project"))
        return {"success": True, **state}


__all__ = ["WaterDesignStatus"]
