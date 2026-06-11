"""project_status_overview — portfolio / project / master-project status (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import require_doc_read

_PROJECT_FIELDS = [
    "name", "project_name", "status", "is_active", "project_type",
    "percent_complete", "expected_start_date", "expected_end_date",
    "custom_project_priority", "custom_company_priority",
    "custom_project_dollar_amount", "estimated_costing", "custom_master_project",
    "customer",
]


class ProjectStatusOverview(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "project_status_overview"  # must match module filename
        self.description = (
            "Project status at three zoom levels via 'scope': (1) 'portfolio' (default) — "
            "every non-cancelled project with task counts, assignees, priorities, and "
            "dollar amounts (this is the shared Project Dashboard view; access is gated "
            "by the Project Dashboard page role, not per-project permissions); "
            "(2) 'project' — one project's core fields plus health metrics (overdue/"
            "high-priority-overdue task counts, schedule_health %), the 7-step "
            "opportunity-to-project hand-off state (first Pending step = current step, "
            "due_by = SLA deadline), and optionally the full task list in Gantt form "
            "with dependencies and assignees; (3) 'master_project' — the member "
            "projects of a Master Project (program) with completion rollups. For "
            "procurement detail use project_procurement_status."
        )
        self.category = "Project Management"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Project"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["portfolio", "project", "master_project"],
                    "default": "portfolio",
                    "description": "Zoom level (see tool description)",
                },
                "project": {"type": "string", "description": "Project docname — required for scope=project"},
                "master_project": {
                    "type": "string",
                    "description": "Master Project docname — required for scope=master_project",
                },
                "is_active": {
                    "type": "string",
                    "enum": ["Yes", "No"],
                    "description": "Portfolio scope: filter on the project's Is Active flag",
                },
                "include_tasks": {
                    "type": "boolean",
                    "default": False,
                    "description": "Project scope: include all tasks (Gantt rows with dependencies)",
                },
                "include_health": {
                    "type": "boolean",
                    "default": True,
                    "description": "Project scope: include health metrics",
                },
                "include_process_steps": {
                    "type": "boolean",
                    "default": True,
                    "description": "Project scope: include hand-off process step state",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scope = arguments.get("scope") or "portfolio"
        if scope == "portfolio":
            return self._portfolio(arguments)
        if scope == "project":
            return self._project(arguments)
        if scope == "master_project":
            return self._master_project(arguments)
        frappe.throw(_("Unknown scope: {0}").format(scope), frappe.ValidationError)

    def _portfolio(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
            get_project_data,
        )

        projects = get_project_data(is_active=arguments.get("is_active"))
        if isinstance(projects, dict) and projects.get("error"):
            # get_project_data signals a failed page-role check (or an internal
            # error) with an {"error": ...} dict rather than raising.
            frappe.throw(projects["error"], frappe.PermissionError)
        return {"success": True, "scope": "portfolio", "projects": projects}

    def _project(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = arguments.get("project")
        if not project:
            frappe.throw(_("'project' is required when scope=project"), frappe.ValidationError)
        require_doc_read("Project", project)

        result: dict[str, Any] = {
            "success": True,
            "scope": "project",
            "project": frappe.db.get_value("Project", project, _PROJECT_FIELDS, as_dict=True),
        }

        if arguments.get("include_health", True):
            from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
                get_project_health_metrics,
            )

            result["health"] = get_project_health_metrics(project)

        if arguments.get("include_process_steps", True):
            steps = frappe.get_all(
                "Project Process Step",
                filters={"parent": project, "parenttype": "Project"},
                fields=[
                    "step_number", "step_title", "responsible_role", "status",
                    "due_by", "completed_on", "completed_by", "sla_hours", "notes",
                ],
                order_by="step_number asc",
            )
            current = next((s for s in steps if s.get("status") == "Pending"), None)
            result["process_steps"] = {
                "current_step": current.get("step_title") if current else None,
                "steps": steps,
            }

        if arguments.get("include_tasks"):
            from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
                get_gantt_tasks_for_project,
            )

            tasks = get_gantt_tasks_for_project(project)
            if isinstance(tasks, dict) and tasks.get("error"):
                result["tasks_error"] = tasks["error"]
            else:
                result["tasks"] = tasks

        return result

    def _master_project(self, arguments: dict[str, Any]) -> dict[str, Any]:
        master_project = arguments.get("master_project")
        if not master_project:
            frappe.throw(
                _("'master_project' is required when scope=master_project"), frappe.ValidationError
            )
        require_doc_read("Master Project", master_project)

        from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
            get_master_project_projects,
        )

        projects = get_master_project_projects(master_project)
        if isinstance(projects, dict) and projects.get("error"):
            frappe.throw(projects["error"], frappe.ValidationError)

        completed = [p for p in projects if p.get("status") == "Completed"]
        return {
            "success": True,
            "scope": "master_project",
            "master_project": master_project,
            "summary": {
                "total_projects": len(projects),
                "completed_projects": len(completed),
                "average_percent_complete": round(
                    sum(p.get("percent_complete") or 0 for p in projects) / len(projects), 1
                ) if projects else 0,
            },
            "projects": projects,
        }


__all__ = ["ProjectStatusOverview"]
