"""Seed the "Estimator" role (v1.832.0, estimating milestone 1).

Decided 2026-09-21 (TASK-2026-02149): a new Estimator role, which with Finance Team, Executive Team and
System Manager is the audience that sees cost and margin. The Estimate, Estimate Template and Estimate
Rate Card doctypes name it in their permissions, so this runs in ``pre_model_sync`` and the role exists
before their permission rows sync.

What else names the role, and what that does from this release:

* ``planner_tracking.COST_ROLES`` is read on every request, so the Project Planner's
  ``get_labor_forecast`` (``api/project_planner.FORECAST_ROLES``) answers an Estimator **with the
  money** (rates and costs), as it does Finance Team.
* ``grant_labor_forecast_visibility`` is a one-shot patch that already ran on prod, before this role
  existed, so it granted nothing to it: the Project form's ``labor_forecast`` field (permlevel 2)
  stays **hidden** from an Estimator until somebody grants that permlevel by hand or a new patch does.

Insert-only and idempotent. It grants the role to nobody: Nik assigns it in the Desk. It cannot
raise (``estimating.seed.run``): a failure is an Error Log row, and the doctypes then sync without
the Estimator permission rows until the role is made by hand and the doctypes are reloaded.
"""

import frappe

from erpnext_enhancements.estimating import seed


def _role():
	if frappe.db.exists("Role", "Estimator"):
		return "already there"
	role = frappe.new_doc("Role")
	role.role_name = "Estimator"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
	return "created"


def execute():
	seed.run(
		"seed_estimator_role", "Estimator role", _role, 'create the Role "Estimator" with Desk Access ticked.'
	)
