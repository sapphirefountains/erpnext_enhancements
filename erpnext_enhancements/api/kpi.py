"""Whitelisted endpoints for the department KPI dashboards.

The KPI Cockpit Custom HTML Block reads the latest precomputed **KPI Snapshot**
through these; the heavy aggregation runs nightly (see
``kpi_dashboards.snapshots``), so the desk render path is a cheap doctype read.
Visibility is role-gated per department (System Manager sees all).
"""

import frappe

from erpnext_enhancements.kpi_dashboards import snapshots

# Departments that have a snapshot aggregator — single source of truth is the
# engine's registry, so this can never drift from what actually produces data.
AVAILABLE_DEPARTMENTS = tuple(snapshots.AGGREGATORS)

# department -> roles allowed to view it (System Manager always allowed).
DEPARTMENT_ROLES = {
	"Finance": {"Accounts Manager", "Accounts User"},
	"Sales": {"Sales Manager", "Sales User"},
	"Operations": {"Projects Manager", "Maintenance Manager", "Projects User"},
	"Marketing": {"Sales Manager", "Marketing Manager"},
	"Design": {"Projects Manager"},
	"Production": {"Projects Manager"},
	"Product": {"Item Manager", "Stock Manager", "Sales Manager"},
	# HR Team is an instance-defined role (seeded by patches.seed_hr_team_role);
	# deliberately NOT "HR User", which every employee on this site holds.
	"HR": {"HR Manager", "HR Team"},
	"Executive": {"Accounts Manager", "Sales Manager"},
}


def _can_view(department):
	roles = set(frappe.get_roles())
	if "System Manager" in roles:
		return True
	return bool(DEPARTMENT_ROLES.get(department, set()) & roles)


def _serialize(doc):
	return {
		"department": doc.department,
		"period": doc.period,
		"snapshot_date": str(doc.snapshot_date),
		"generated_at": str(doc.generated_at) if doc.generated_at else None,
		"generated_by": doc.generated_by,
		"values": [
			{
				"kpi_key": v.kpi_key,
				"label": v.label,
				"value": v.value,
				"value_text": v.value_text,
				"unit": v.unit,
				"target_value": v.target_value,
				"status": v.status,
				"direction": v.direction,
				"trend_pct": v.trend_pct,
				"source": v.source,
				"is_stale": v.is_stale,
			}
			for v in doc.values
		],
	}


@frappe.whitelist()
def visible_departments():
	"""Departments the session user may view — drives the cockpit's selector."""
	return [d for d in AVAILABLE_DEPARTMENTS if _can_view(d)]


@frappe.whitelist()
def get_kpi_dashboard(department, period="Daily"):
	"""Latest snapshot for ``department`` (role-gated)."""
	if department not in AVAILABLE_DEPARTMENTS:
		return {"available": False, "reason": f"No KPI dashboard is configured for {department} yet."}
	if not _can_view(department):
		frappe.throw(frappe._("You do not have permission to view the {0} KPIs.").format(department), frappe.PermissionError)
	if not snapshots.kpi_enabled():
		return {"available": False, "reason": "KPI Dashboards are disabled in ERPNext Enhancements Settings."}

	name = frappe.get_all(
		"KPI Snapshot",
		filters={"department": department, "period": period},
		order_by="snapshot_date desc",
		limit=1,
		pluck="name",
	)
	if not name:
		return {
			"available": True,
			"snapshot": None,
			"reason": "No snapshot yet — it generates overnight, or press Refresh.",
		}
	return {"available": True, "snapshot": _serialize(frappe.get_doc("KPI Snapshot", name[0]))}


@frappe.whitelist()
def refresh_kpi_dashboard(department, period="Daily"):
	"""Rebuild ``department``'s snapshot now (role-gated). Synchronous — the
	aggregations are cheap SQL; the button shows a spinner."""
	if department not in AVAILABLE_DEPARTMENTS:
		frappe.throw(frappe._("No KPI dashboard is configured for {0}.").format(department))
	if not _can_view(department):
		frappe.throw(frappe._("You do not have permission to refresh the {0} KPIs.").format(department), frappe.PermissionError)
	if not snapshots.kpi_enabled():
		return {"available": False, "reason": "KPI Dashboards are disabled in ERPNext Enhancements Settings."}

	doc = snapshots.build_department_snapshot(department, period=period, generated_by=f"Manual: {frappe.session.user}")
	frappe.db.commit()
	return {"available": True, "snapshot": _serialize(doc)}
