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
	"""SQL for list views, reports and ``/api/resource``."""
	visible = _visible_departments()
	if visible is None:
		return ""
	if not visible:
		# Holds a read DocPerm through some other role but owns no department.
		# An empty IN () is a syntax error in MariaDB, so say it explicitly.
		return "1 = 0"
	quoted = ", ".join(frappe.db.escape(department) for department in sorted(visible))
	return f"`tabKPI Snapshot`.`department` IN ({quoted})"


def has_permission(doc, ptype=None, user=None):
	"""The same rule for a single document read."""
	visible = _visible_departments()
	if visible is None:
		return True
	return getattr(doc, "department", None) in visible
