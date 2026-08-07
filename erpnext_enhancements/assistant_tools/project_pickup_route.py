"""project_pickup_route — the physical collection run for a project (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

**Two keys are stripped from the underlying reply and it is not optional.**
``get_pickup_route_data`` returns ``api_key`` — a live, billable Google Maps
*browser* key — because its real caller is a dialog that has to draw a map. An
MCP client is not a browser: the key would land in a chat transcript, be quoted
back, logged, and eventually pasted somewhere it can be used at our expense.
``use_routes_api`` goes with it as the other half of a client rendering
instruction, meaningless to a reader and misleading if quoted.

Suppliers with no address are kept rather than filtered out. "These four vendor
records have no address so the run cannot include them" is part of the answer,
not noise to be tidied away — dropping them produces a shorter route that is
quietly wrong.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import require_doc_read
from erpnext_enhancements.assistant_tools._gate import annotations_for

# Stripped from every reply. Asserted by tests/test_assistant_tools_redaction.py.
NEVER_RETURN = ("api_key", "use_routes_api")


class ProjectPickupRoute(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "project_pickup_route"  # must match module filename
        self.description = (
            "The physical collection run for one project: which suppliers have goods "
            "waiting, where they are, and what is on each Purchase Order line. Use "
            "this for 'what do we need to go and pick up for project X' and for "
            "planning a driver's route. Scope defaults to 'outstanding' (submitted "
            "POs whose goods have not arrived); 'submitted' includes received ones "
            "and 'all' adds drafts, for planning a run before the orders are placed. "
            "Suppliers with no address on file are reported rather than dropped — "
            "they cannot be routed to, and that is usually a vendor record needing "
            "fixed rather than a stop that does not exist. This is the physical run; "
            "for where each item sits in the Material Request -> PO -> Receipt chain, "
            "use project_procurement_status."
        )
        self.category = "Project Management"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Project"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project docname (e.g. 'PROJ-0001')"},
                "scope": {
                    "type": "string",
                    "enum": ["outstanding", "submitted", "all"],
                    "default": "outstanding",
                    "description": "Which Purchase Orders to include",
                },
            },
            "required": ["project"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = arguments["project"]
        # The endpoint checks Project read itself, but doing it here too keeps the
        # refusal identical whether or not that ever changes.
        require_doc_read("Project", project)

        from erpnext_enhancements.api.pickup_routing import get_pickup_route_data

        data = dict(get_pickup_route_data(project, scope=arguments.get("scope") or "outstanding"))
        for key in NEVER_RETURN:
            data.pop(key, None)

        stops = data.get("stops") or []
        unroutable = [
            {"supplier": s.get("supplier"), "supplier_name": s.get("supplier_name")}
            for s in stops
            if not s.get("address")
        ]
        data["stop_count"] = len(stops)
        data["unroutable_stops"] = unroutable
        if unroutable:
            data["note"] = (
                f"{len(unroutable)} of {len(stops)} suppliers have no address on file and "
                "cannot be routed to. Their goods still need collecting — the supplier "
                "records need an address before the run can include them."
            )
        return data


__all__ = ["ProjectPickupRoute"]
