# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for KPI Snapshot.

The doctype's DocPerms were System-Manager-only, which made
``kpi_dashboard_status`` unbuildable: a tool's ``requires_permission`` controls
per-user *visibility* as well as execution, so the tool would have been invisible
to exactly the department managers who own the numbers (TASK-2026-01187).

Widening those DocPerms to the ``DEPARTMENT_ROLES`` roles is what that task asked
for, and on its own it would have done something nobody asked for: a DocPerm is
doctype-wide. An Accounts Manager granted ``read`` on KPI Snapshot can read
*every* snapshot — including Sales and HR — through the desk list view or
``/api/resource/KPI Snapshot``, whatever ``api/kpi.py::_can_view`` says, because
that function guards two whitelisted endpoints and not the doctype.

So the row filter lives here. ``_can_view`` stays the authority on *which*
departments; this file is the same rule expressed where Frappe enforces reads.

**Frappe semantics worth remembering** (documented the same way in
``travel_management/permissions.py`` and ``training/permissions.py``): a
``has_permission`` hook can only **restrict** what role DocPerms already grant. It
can never widen access, so the role rows in ``kpi_snapshot.json`` remain the outer
bound and this is strictly a narrowing.

**Revenue and margin figures** (owner decision, 2026-11-19; ``kpi_dashboards/money.py``):
a caller without Accounts Manager or System Manager reads no revenue or margin figure.

- A snapshot document read (desk form, ``/api/resource/KPI Snapshot/<name>``,
  ``frappe.client.get``) opens with those rows left out
  (``KPISnapshot.apply_fieldlevel_read_permissions``).
- A list, report or count of ``KPI Snapshot``, including one that selects, filters, sorts
  or totals by ``KPI Snapshot Value`` columns, returns only snapshots that hold none of
  those keys (``get_permission_query_conditions``). Frappe resolves a query through the
  parent table, and a child-table query made with ``parent_doctype``, by the parent's
  condition, so this one condition is what such a query obeys.
- A query whose own doctype is ``KPI Snapshot Value`` leaves those rows out
  (``get_value_permission_query_conditions``).
- ``KPI Target`` rows for those keys are listed, read and edited by Accounts Manager and
  System Manager only (``get_target_permission_query_conditions``, ``target_has_permission``).

The dashboards read through ``frappe.get_all`` and ``frappe.get_doc`` with their own role
gate and filter (``api/kpi.py``, ``api/executive_dashboard.py``, ``api/task_dashboard.py``),
never through a permission-applied list, so the list rule above narrows the desk list and
the REST list only.
"""

import frappe


def _visible_departments():
	"""Departments this session may see, or ``None`` for "all of them".

	``None`` rather than the full list, so the caller can skip emitting a
	condition entirely for a System Manager instead of writing a WHERE clause
	naming all nine departments on every KPI query.
	"""
	# Imported inside the function, not at module scope: hooks are loaded during
	# `bench migrate` on a fresh site, and `api.kpi` imports the snapshot engine,
	# which reaches for doctypes that may not exist yet.
	from erpnext_enhancements.api.kpi import DEPARTMENT_ROLES

	roles = set(frappe.get_roles())
	if "System Manager" in roles:
		return None
	return {
		department
		for department, allowed in DEPARTMENT_ROLES.items()
		if allowed & roles
	}


def get_permission_query_conditions(user=None):
	"""SQL for list views, reports and ``/api/resource``: the caller's departments, and
	for a caller who may not see revenue and margin, only snapshots that hold none."""
	visible = _visible_departments()
	if visible is not None and not visible:
		# Holds a read DocPerm through some other role but owns no department.
		# An empty IN () is a syntax error in MariaDB, so say it explicitly.
		return "1 = 0"
	conditions = []
	if visible is not None:
		quoted = ", ".join(frappe.db.escape(department) for department in sorted(visible))
		conditions.append(f"`tabKPI Snapshot`.`department` IN ({quoted})")
	if not _can_see_revenue(user):
		conditions.append(_no_restricted_values_condition())
	return " AND ".join(conditions)


def _can_see_revenue(user=None):
	from erpnext_enhancements.kpi_dashboards import money

	return money.can_see_revenue(frappe.get_roles(user) if user else None)


def _restricted_keys_sql():
	from erpnext_enhancements.kpi_dashboards import money

	return ", ".join(frappe.db.escape(key) for key in sorted(money.RESTRICTED_KPIS))


def _no_restricted_values_condition():
	"""A snapshot qualifies only when none of its rows is a revenue or margin figure.

	Keyed on ``parent`` against the outer ``tabKPI Snapshot`` row. The inner table has its
	own alias so the condition reads the same when the outer query is on the child table
	(Frappe joins ``tabKPI Snapshot`` in for a child-table query made with ``parent_doctype``).
	"""
	return (
		"NOT EXISTS (SELECT 1 FROM `tabKPI Snapshot Value` AS `kpi_restricted_value`"
		" WHERE `kpi_restricted_value`.`parent` = `tabKPI Snapshot`.`name`"
		" AND `kpi_restricted_value`.`parenttype` = 'KPI Snapshot'"
		f" AND `kpi_restricted_value`.`kpi_key` IN ({_restricted_keys_sql()}))"
	)


def has_permission(doc, ptype=None, user=None):
	"""The same rule for a single document read."""
	visible = _visible_departments()
	if visible is None:
		return True
	return getattr(doc, "department", None) in visible


def get_value_permission_query_conditions(user=None):
	"""SQL for a query whose own doctype is KPI Snapshot Value: no revenue or margin figure
	unless the caller may see them."""
	if _can_see_revenue(user):
		return ""
	return f"`tabKPI Snapshot Value`.`kpi_key` NOT IN ({_restricted_keys_sql()})"


def get_target_permission_query_conditions(user=None):
	"""SQL for KPI Target lists, reports and ``/api/resource``: no target for a revenue or
	margin figure unless the caller may see them."""
	if _can_see_revenue(user):
		return ""
	return f"COALESCE(`tabKPI Target`.`kpi_key`, '') NOT IN ({_restricted_keys_sql()})"


def target_has_permission(doc, ptype=None, user=None):
	"""The same rule for one KPI Target, for every permission type (read, write, create,
	delete): a target for a revenue or margin figure is Accounts Manager's and System
	Manager's."""
	from erpnext_enhancements.kpi_dashboards import money

	if not money.is_restricted_kpi(getattr(doc, "kpi_key", None)):
		return True
	return _can_see_revenue(user)
