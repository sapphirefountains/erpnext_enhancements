"""Seed the "Estimator" role (v1.836.0, estimating milestone 1).

Decided 2026-09-21 (TASK-2026-02149): a new Estimator role, which with Finance Team, Executive Team and
System Manager is the audience that sees cost and margin. The Estimate, Estimate Template and Estimate
Rate Card doctypes name it in their permissions, so this runs in ``pre_model_sync`` and the role exists
before their permission rows sync. ``planner_tracking.COST_ROLES`` and
``grant_labor_forecast_visibility`` already list it and filter to roles that exist, so from this
release an Estimator also sees the Project's labor forecast, as that rule intended.

Insert-only and idempotent. It grants the role to nobody: Nik assigns it in the Desk.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Estimator"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Estimator"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
