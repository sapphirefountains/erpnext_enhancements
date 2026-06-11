"""maintenance_site_briefing — pre-visit briefing for one site (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import require_doc_read


class MaintenanceSiteBriefing(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "maintenance_site_briefing"  # must match module filename
        self.description = (
            "Everything a technician needs before going on-site for a maintenance "
            "visit: safety instructions and access codes from the Sapphire Maintenance "
            "Profile, site instructions for the water feature (Serial No), gate code / "
            "key location / preferred days from the signed Project Contract, the last 3 "
            "submitted visits, the project's service scope (customer requests and "
            "deliverables), chemistry trends from the last 5 visits, and any open visit "
            "drafts. NOTE: the response contains site access codes — the same data the "
            "desk technician widget shows — so only surface them when the user actually "
            "needs site access. Requires read permission on the Project and on Sapphire "
            "Maintenance Records."
        )
        self.category = "Maintenance Operations"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Sapphire Maintenance Record"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project docname (e.g. 'PROJ-0001')"},
                "serial_no": {
                    "type": "string",
                    "description": "Serial No of the water feature. Omit for Per Site Visit contracts.",
                },
            },
            "required": ["project"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = arguments["project"]
        # get_dashboard_context reads via frappe.db.get_value / get_all without
        # permission checks, so gate on the Project document explicitly.
        require_doc_read("Project", project)

        from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
            get_dashboard_context,
        )

        briefing = get_dashboard_context(project, arguments.get("serial_no"))
        briefing["open_drafts"] = frappe.get_list(
            "Sapphire Maintenance Record",
            filters={"project": project, "docstatus": 0},
            fields=["name", "serial_no", "visit_label", "technician", "completion_percent", "modified"],
            order_by="modified desc",
            limit=20,
        )

        return {
            "success": True,
            "project": project,
            "project_title": frappe.db.get_value("Project", project, "project_name") or project,
            "briefing": briefing,
        }


__all__ = ["MaintenanceSiteBriefing"]
