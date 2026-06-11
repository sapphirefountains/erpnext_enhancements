"""maintenance_contract_status — contract terms + visit cadence (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.
"""

from typing import Any

import frappe
from frappe.utils import add_days, date_diff, nowdate
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, project_title_map


class MaintenanceContractStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "maintenance_contract_status"  # must match module filename
        self.description = (
            "Sapphire Maintenance Contracts with their visit cadence: covered water "
            "features (serial no, frequency, last/next visit date, overdue flag), "
            "seasonal one-off visits, invoicing frequency, and contract dates. Also "
            "returns a flat 'upcoming' list of feature visits due within upcoming_days "
            "(negative days_until = overdue). One Active contract per project is the "
            "norm. Seasonal visits are annual one-offs and do not advance the regular "
            "cadence. Use maintenance_site_briefing before dispatching a technician to "
            "a site, and maintenance_visit_history for what happened on past visits."
        )
        self.category = "Maintenance Operations"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Sapphire Maintenance Contract"
        self.default_config = {"max_contracts": 200}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Limit to one Project (docname, e.g. 'PROJ-0001')",
                },
                "customer": {
                    "type": "string",
                    "description": "Limit to one Customer (docname)",
                },
                "status": {
                    "type": "string",
                    "enum": ["Active", "Draft", "Expired", "Cancelled"],
                    "default": "Active",
                    "description": "Contract status filter (default Active)",
                },
                "upcoming_days": {
                    "type": "integer",
                    "default": 14,
                    "description": "Horizon in days for the 'upcoming' visit list",
                },
                "include_features": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include per-feature cadence and seasonal visit child rows",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        filters = {"status": arguments.get("status") or "Active"}
        if arguments.get("project"):
            filters["project"] = arguments["project"]
        if arguments.get("customer"):
            filters["customer"] = arguments["customer"]

        max_contracts = clamp_limit(self.get_config().get("max_contracts"), 200, 1000)
        # frappe.get_list enforces role + user permissions on the parent doctype;
        # child rows below are only fetched for parents that passed this filter.
        contracts = frappe.get_list(
            "Sapphire Maintenance Contract",
            filters=filters,
            fields=[
                "name", "project", "customer", "status", "visit_shape",
                "invoicing_frequency", "start_date", "end_date",
                "project_contract", "sales_order", "default_template",
            ],
            order_by="project asc",
            limit=max_contracts,
        )

        titles = project_title_map(c.get("project") for c in contracts)
        today = nowdate()
        upcoming_days = arguments.get("upcoming_days")
        upcoming_days = 14 if upcoming_days is None else int(upcoming_days)
        horizon = add_days(today, upcoming_days)
        upcoming = []

        include_features = arguments.get("include_features", True)
        features_by_contract = {}
        seasonal_by_contract = {}
        if contracts and include_features:
            names = [c["name"] for c in contracts]
            for row in frappe.get_all(
                "Sapphire Contract Feature",
                filters={"parent": ["in", names], "parenttype": "Sapphire Maintenance Contract"},
                fields=[
                    "parent", "serial_no", "frequency", "template",
                    "last_visit_date", "next_visit_date",
                ],
                order_by="parent asc, idx asc",
            ):
                contract = row.pop("parent")
                row["overdue"] = bool(row.get("next_visit_date") and str(row["next_visit_date"]) < today)
                features_by_contract.setdefault(contract, []).append(row)
                if row.get("next_visit_date") and str(row["next_visit_date"]) <= horizon:
                    upcoming.append({
                        "contract": contract,
                        "serial_no": row.get("serial_no"),
                        "frequency": row.get("frequency"),
                        "next_visit_date": str(row["next_visit_date"]),
                        "days_until": date_diff(row["next_visit_date"], today),
                    })
            for row in frappe.get_all(
                "Sapphire Seasonal Visit",
                filters={"parent": ["in", names], "parenttype": "Sapphire Maintenance Contract"},
                fields=["parent", "visit_label", "template", "target_month", "last_generated_year"],
                order_by="parent asc, idx asc",
            ):
                seasonal_by_contract.setdefault(row.pop("parent"), []).append(row)

        project_by_contract = {}
        for contract in contracts:
            contract["project_title"] = titles.get(contract.get("project")) or contract.get("project")
            project_by_contract[contract["name"]] = contract
            if include_features:
                contract["features"] = features_by_contract.get(contract["name"], [])
                contract["seasonal_visits"] = seasonal_by_contract.get(contract["name"], [])

        for visit in upcoming:
            parent = project_by_contract.get(visit["contract"]) or {}
            visit["project"] = parent.get("project")
            visit["project_title"] = parent.get("project_title")
        upcoming.sort(key=lambda v: v["next_visit_date"])

        return {
            "success": True,
            "as_of": today,
            "contracts": contracts,
            "upcoming": upcoming,
        }


__all__ = ["MaintenanceContractStatus"]
