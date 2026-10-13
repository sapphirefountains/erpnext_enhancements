"""Let Projects Manager edit Project Templates: the planner's phase templates and bundles (T3, v1.608.0).

Nik, 2026-10-12: the planner's templates are edited in ERPNext as Project Templates, by a System
Manager or a Projects Manager. ERPNext v16's own Project Template permissions give System Manager
everything but Projects Manager (and Projects User) only *select*, so a Projects Manager could pick a
template and never change one.

This grants Projects Manager read, write, create and delete (with report, export, print, email and
share, as System Manager has) on Project Template at permlevel 0. The template's tasks are ordinary
template Tasks, which Projects User can already edit under ERPNext's standard Task permissions.

**Why ``setup_custom_perms`` runs first** (as in ``grant_labor_forecast_visibility``):
``Meta.set_custom_permissions`` replaces a doctype's standard DocPerms with its Custom DocPerm rows the
moment any exist, so ``setup_custom_perms`` copies the standard rows across first, when and only when
none exist; on a site already on Custom DocPerm for Project Template it does nothing. A patch and not a
``custom_docperm.json`` fixture, because that fixture captures a doctype's rows wholesale.

Only widens: the Projects Manager row (copied from the standard *select* row, or inserted) gains the
rights it lacks; nothing is ever taken away from any role. Skipped when the role does not exist. Never
raises. Safe twice.
"""

import frappe

PARENT = "Project Template"
ROLE = "Projects Manager"
RIGHTS = ("select", "read", "write", "create", "delete", "report", "export", "print", "email", "share")


def execute() -> None:
	try:
		changed = grant_project_template_editing()
		print(f"Project Template editing for {ROLE}: {changed}")
	except Exception:
		frappe.log_error(title="grant_project_template_editing", message=frappe.get_traceback())


def grant_project_template_editing() -> str:
	if not frappe.db.exists("DocType", PARENT) or not frappe.db.exists("Role", ROLE):
		return "skipped"

	from frappe.permissions import setup_custom_perms

	setup_custom_perms(PARENT)
	rows = frappe.get_all(
		"Custom DocPerm",
		filters={"parent": PARENT, "role": ROLE, "permlevel": 0, "if_owner": 0},
		fields=["name", *RIGHTS],
		limit_page_length=1,
	)
	if rows:
		missing = {right: 1 for right in RIGHTS if not rows[0].get(right)}
		if not missing:
			return "already granted"
		frappe.db.set_value("Custom DocPerm", rows[0].get("name"), missing, update_modified=False)
		result = "widened"
	else:
		frappe.get_doc(
			dict(
				{
					"doctype": "Custom DocPerm",
					"parent": PARENT,
					"parenttype": "DocType",
					"parentfield": "permissions",
					"role": ROLE,
					"permlevel": 0,
				},
				**dict.fromkeys(RIGHTS, 1),
			)
		).insert(ignore_permissions=True)
		result = "granted"
	frappe.clear_cache(doctype=PARENT)
	return result
