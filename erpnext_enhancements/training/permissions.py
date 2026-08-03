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


def attempt_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Attempt", _resolve(user))


def attempt_question_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Attempt Question", _resolve(user))


def completion_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Completion", _resolve(user))


def certificate_query_conditions(user=None):
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Certificate", _resolve(user))


def signoff_query_conditions(user=None):
	"""A sign-off is scoped to the learner it is about.

	The supervisor reaches it through the direct-reports arm of
	``_own_rows_condition`` — which is exactly what makes the sign-off queue a
	plain filtered list view rather than a bespoke endpoint.
	"""
	if _is_unscoped(user):
		return ""
	return _own_rows_condition("Training Signoff", _resolve(user))


# -------------------------------------------------------------------- has_permission


def assignment_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def attempt_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def attempt_question_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def completion_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def certificate_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	return _own_row(doc, _resolve(user))


def signoff_has_permission(doc, ptype=None, user=None):
	if _is_unscoped(user):
		return True
	# The supervisor named on the sign-off can always see it, even when the
	# learner is not one of their Employee.reports_to (a Named Supervisor or a
	# stand-in Training Manager) — otherwise they cannot action their own queue.
	if doc.get("supervisor_user") == _resolve(user):
		return True
	return _own_row(doc, _resolve(user))
