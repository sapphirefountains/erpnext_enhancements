"""maintenance_visit_history — submitted visit records + chemistry trends (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, project_title_map, strip_meta

_LIST_FIELDS = [
    "name", "project", "serial_no", "visit_label", "technician",
    "completion_percent", "has_out_of_range_readings", "warranty_rma_flag",
    "clock_in_time", "clock_out_time", "creation",
]

_DETAIL_HEADER_FIELDS = [
    "name", "customer", "project", "maintenance_contract", "serial_no",
    "template", "visit_label", "technician", "clock_in_time", "clock_out_time",
    "paused_duration", "total_labor_cost", "completion_percent",
    "warranty_rma_flag", "has_out_of_range_readings", "sales_invoice",
    "workflow_state", "visit_notes", "docstatus",
]

_CHILD_TABLES = ["chemistry_readings", "cleaning_tasks", "consumables", "maintenance_results"]


class MaintenanceVisitHistory(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "maintenance_visit_history"  # must match module filename
        self.description = (
            "Submitted Sapphire Maintenance Records (completed field-service visits). "
            "Two modes: (1) list mode (default) — filter by project, technician, date "
            "range, or flagged_only (out-of-range chemistry / warranty-RMA visits); "
            "(2) detail mode — pass 'record' to get one visit's full payload: chemistry "
            "readings with allowed ranges, cleaning tasks, consumables used, inspection "
            "results, and visit notes. Set include_trends with a project (or in detail "
            "mode) to also get per-reading chemistry history across the last 5 visits "
            "for trend analysis. Dates filter on record creation. Use "
            "maintenance_day_board for today's live picture."
        )
        self.category = "Maintenance Operations"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Sapphire Maintenance Record"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "record": {
                    "type": "string",
                    "description": "A Sapphire Maintenance Record docname — switches to detail mode",
                },
                "project": {"type": "string", "description": "Filter by Project docname"},
                "technician": {"type": "string", "description": "Filter by technician (User id)"},
                "from_date": {"type": "string", "description": "Earliest creation date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "Latest creation date (YYYY-MM-DD)"},
                "flagged_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only visits with out-of-range readings or a warranty/RMA flag",
                },
                "include_trends": {
                    "type": "boolean",
                    "default": False,
                    "description": "Add chemistry trends (needs 'project', or implied by detail mode)",
                },
                "serial_no": {
                    "type": "string",
                    "description": "Narrow list/trends to one water feature (Serial No)",
                },
                "limit": {"type": "integer", "default": 20, "description": "Max rows in list mode (cap 100)"},
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if arguments.get("record"):
            return self._detail(arguments)
        return self._list(arguments)

    def _list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        filters = {"docstatus": 1}
        if arguments.get("project"):
            filters["project"] = arguments["project"]
        if arguments.get("serial_no"):
            filters["serial_no"] = arguments["serial_no"]
        if arguments.get("technician"):
            filters["technician"] = arguments["technician"]
        if arguments.get("from_date") and arguments.get("to_date"):
            filters["creation"] = ["between", [arguments["from_date"], arguments["to_date"]]]
        elif arguments.get("from_date"):
            filters["creation"] = [">=", arguments["from_date"]]
        elif arguments.get("to_date"):
            filters["creation"] = ["<=", arguments["to_date"]]

        or_filters = None
        if arguments.get("flagged_only"):
            or_filters = [["has_out_of_range_readings", "=", 1], ["warranty_rma_flag", "=", 1]]

        # frappe.get_list enforces role + user permissions.
        visits = frappe.get_list(
            "Sapphire Maintenance Record",
            filters=filters,
            or_filters=or_filters,
            fields=_LIST_FIELDS,
            order_by="creation desc",
            limit=clamp_limit(arguments.get("limit"), 20, 100),
        )
        titles = project_title_map(v.get("project") for v in visits)
        for visit in visits:
            visit["project_title"] = titles.get(visit.get("project")) or visit.get("project")

        result = {"success": True, "mode": "list", "visits": visits}
        if arguments.get("include_trends") and arguments.get("project"):
            result["trends"] = self._trends(arguments["project"], arguments.get("serial_no"))
        return result

    def _detail(self, arguments: dict[str, Any]) -> dict[str, Any]:
        doc = frappe.get_doc("Sapphire Maintenance Record", arguments["record"])
        # get_doc does not check permissions on its own.
        if not doc.has_permission("read"):
            frappe.throw(
                frappe._("No read permission for Sapphire Maintenance Record {0}").format(doc.name),
                frappe.PermissionError,
            )

        visit = {field: doc.get(field) for field in _DETAIL_HEADER_FIELDS}
        visit["project_title"] = frappe.db.get_value("Project", doc.project, "project_name") or doc.project
        for table in _CHILD_TABLES:
            visit[table] = [strip_meta(row.as_dict()) for row in (doc.get(table) or [])]

        result = {"success": True, "mode": "detail", "visit": visit}
        if arguments.get("include_trends"):
            result["trends"] = self._trends(doc.project, doc.serial_no)
        return result

    @staticmethod
    def _trends(project, serial_no=None):
        from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_record.sapphire_maintenance_record import (
            _chemistry_trends,
        )

        return _chemistry_trends(project, serial_no)


__all__ = ["MaintenanceVisitHistory"]
