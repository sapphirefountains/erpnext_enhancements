# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for the learner-owned Training doctypes.

**Scope note, and it is the important part of this file:** course *content* is not
protected here. Learner roles hold no DocPerm at all on ``Training Course``,
``Training Course Version``, ``Training Lesson``, ``Training Content Block``,
``Training Checkpoint``, ``Training Question`` or ``Training Answer Option``, so
``/api/resource/Training Question`` refuses them outright — a defence that holds
even if a future endpoint is careless with ``fields=["*"]``. The learner runtime
(Phase 2) computes visibility itself and reads with ``ignore_permissions=True``
after its own gate, because visibility is a predicate — audience × roles ×
customer × assignment × published state — that reads as fifteen lines of Python
and as a stack of correlated subqueries in SQL.

What *is* here is the scoping for the records a learner owns: their assignments,
and later their attempts, completions and certificates. Those need DocPerms
because they show up in desk list views and in the learner's own transcript.

Frappe semantics worth remembering (documented the same way in
``travel_management/permissions.py``): a ``has_permission`` hook can only
**restrict** what role DocPerms already grant. It can never widen access, so the
role rows in the doctype JSON remain the outer bound.
"""

import frappe

# Roles that see everything. Training Author is deliberately absent: an author
# needs to see completion *statistics*, which the reports give them, not the
# individual records of who failed what.
UNSCOPED_ROLES = {"System Manager", "Training Manager", "HR Manager"}


def _resolve(user):
	return user or frappe.session.user


def _is_unscoped(user):
	return bool(UNSCOPED_ROLES & set(frappe.get_roles(_resolve(user))))


def _direct_report_users(user):
	"""Logins of the people who report to ``user``, via ``Employee.reports_to``.

	Empty for almost everybody, which is the common case and costs one indexed
	lookup.
	"""
	manager = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not manager:
		return []
	return [
		u
		for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
		if u
	]


def _own_rows_condition(doctype, user):
	"""SQL restricting ``doctype`` to rows this user owns.

	Supervisors additionally see their direct reports — that is what makes the
	sign-off queue a plain filtered list view rather than a bespoke endpoint.
	"""
	table = f"`tab{doctype}`"
	allowed = [user] + _direct_report_users(user)
	joined = ", ".join(frappe.db.escape(u) for u in allowed)
	return f"{table}.`user` in ({joined})"


def _own_row(doc, user):
	if doc.get("user") == user:
		return True
	return doc.get("user") in _direct_report_users(user)


# ------------------------------------------------------- permission_query_conditions


def assignment_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Assignment", _resolve(user))


# -------------------------------------------------------------------- has_permission


def assignment_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))
