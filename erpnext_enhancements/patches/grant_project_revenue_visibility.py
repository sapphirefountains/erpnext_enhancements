"""Permission level 3 on Project for Accounts Manager and System Manager: ERPNext's money totals.

v1.845.3 puts ERPNext's eight revenue, cost and margin totals on Project (billed, billable,
Sales Order value, timesheet cost, purchase cost, material cost, gross margin, margin %) at
**permlevel 3**, through fixture Property Setters (``project_revenue.PROTECTED_FIELDS``). Owner
decision, 2026-11-19, extended to these fields on 2026-11-27: revenue and margin go to Accounts
Manager and System Manager only. This grants the two roles ``read`` and ``write`` at that level.

**Why level 3.** Level 1 on Project is read by Desk User, which every desk user holds
(ERPNext's own ``project.json``). Level 2 is the labor-forecast level, held by five cost roles
(``grant_labor_forecast_visibility``). Level 3 was held by nobody on production (read
2026-11-27), so these two rows are the whole of who may read the totals.

**Why write as well as read.** ERPNext computes the totals on every save. Frappe resets a field
above the caller's write level to its stored value before ``validate`` runs, and ERPNext's
``validate`` recomputes it afterwards, so the values stay current either way; write keeps an
Accounts Manager's save from going through the reset at all.

**Why ``setup_custom_perms`` runs first** (as in ``seed_employee_pay_visibility`` and
``grant_labor_forecast_visibility``). The moment any Custom DocPerm row exists for a doctype, the
Custom DocPerm rows are its whole permission set and the standard DocPerms are ignored, so
inserting two level-3 rows into an empty set would leave Project with no level-0 permissions.
``frappe.permissions.setup_custom_perms`` copies the standard rows across first, when and only
when none exist; production has been on Custom DocPerm for Project since before 2026-10-09, so
there it does nothing. A patch and not a ``custom_docperm.json`` fixture, because that fixture
captures a doctype's rows wholesale and Project's permission set is site-owned.

**Order on a deploy.** Patches run before fixtures sync, so for a moment the two level-3 rows
exist while the eight fields are still at level 0. Nothing changes for anyone in that moment.
If this patch failed and the Property Setters still landed, the totals would fail closed: hidden
from everyone but Administrator until the rows exist.

Insert-only, keyed on ``(parent = "Project", role, permlevel = 3)``, so a human's later edit to
a row survives. Never raises. Safe twice.
"""

import frappe

PARENT = "Project"
PERMLEVEL = 3
#: project_revenue.ROLES and kpi_dashboards.money.REVENUE_ROLES, restated so a patch never
#: imports app code. tests/test_project_revenue_fields.py holds the three equal.
ROLES = ("Accounts Manager", "System Manager")


def execute() -> None:
	try:
		created = grant_project_revenue_visibility()
		print(f"Project permlevel-{PERMLEVEL} read/write (revenue, cost, margin totals): {created} row(s) created")
	except Exception:
		frappe.log_error(title="grant_project_revenue_visibility", message=frappe.get_traceback())


def grant_project_revenue_visibility() -> int:
	if not frappe.db.exists("DocType", PARENT):
		return 0

	from frappe.permissions import setup_custom_perms

	roles = [role for role in ROLES if frappe.db.exists("Role", role)]
	if not roles:
		return 0
	setup_custom_perms(PARENT)

	created = 0
	for role in roles:
		if frappe.db.exists("Custom DocPerm", {"parent": PARENT, "role": role, "permlevel": PERMLEVEL}):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": PARENT,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": PERMLEVEL,
				"read": 1,
				"write": 1,
			}
		).insert(ignore_permissions=True)
		created += 1

	if created:
		frappe.clear_cache(doctype=PARENT)
	return created
