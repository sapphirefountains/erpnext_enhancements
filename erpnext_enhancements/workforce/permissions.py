# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level access for Job Interval and Time Correction Request.

Both doctypes grant ``read`` to the ``Employee`` role, which every staff account holds,
and a DocPerm is doctype-wide: without a scoping hook every technician could browse
everybody else's clock-ins and correction requests. So the hook ships in the same
release as the DocPerm — the lesson this app paid for three times in the Training
module (v1.386.0) — wired in ``hooks.py`` under ``permission_query_conditions`` (list
views, link searches, reports) and ``has_permission`` (a direct ``get_doc``). Parity
between the two registers is the house doctrine: a query condition filters lists and
says nothing about ``frappe.get_doc()``.

Who sees what
-------------
* **Job Interval** — System Manager, HR Manager, Accounts Manager and Projects Manager
  see every row (``JOB_INTERVAL_VIEW_ALL_ROLES``). Everyone else sees rows where
  ``employee`` is their own session Employee. The permlevel-1 pay block on the row is a
  separate gate (Custom DocPerm permlevel 1 in the DocType JSON) and is never widened
  here.
* **Time Correction Request** — HR Manager, Projects Manager and System Manager see all
  (``TCR_MANAGER_ROLES``). An employee sees their own. The employee's ``reports_to``
  user also sees the request — read only — because they are allowed to approve it
  (``can_review``) and a reviewer who cannot open the row cannot review it.

Note frappe semantics: a ``has_permission`` hook can only *restrict* what the role
DocPerms already grant, and on v16 a hook that returns ``None`` DENIES, so every path
returns an explicit bool.
"""

import frappe

#: Roles that see every Job Interval. Accounts Manager is here for job costing.
JOB_INTERVAL_VIEW_ALL_ROLES = {"System Manager", "HR Manager", "Accounts Manager", "Projects Manager"}

#: Roles that may review (approve / decline) any Time Correction Request.
TCR_MANAGER_ROLES = {"System Manager", "HR Manager", "Projects Manager"}


def _resolve(user):
	return user or frappe.session.user


def _session_employee(user):
	return frappe.db.get_value("Employee", {"user_id": user}, "name")


def _has_any(roles, user):
	return user == "Administrator" or bool(roles & set(frappe.get_roles(user)))


def _reports_to_user(employee):
	"""The login of the employee's ``reports_to``, or None."""
	if not employee:
		return None
	supervisor = frappe.db.get_value("Employee", employee, "reports_to")
	if not supervisor:
		return None
	return frappe.db.get_value("Employee", supervisor, "user_id") or None


# ------------------------------------------------------------------ Job Interval


def job_interval_query_conditions(user=None):
	user = _resolve(user)
	if _has_any(JOB_INTERVAL_VIEW_ALL_ROLES, user):
		return ""
	employee = _session_employee(user) or ""
	return f"`tabJob Interval`.`employee` = {frappe.db.escape(employee)}"


def job_interval_has_permission(doc, ptype=None, user=None):
	user = _resolve(user)
	if _has_any(JOB_INTERVAL_VIEW_ALL_ROLES, user):
		return True
	# An unsaved doc has nothing to compare against and is governed by the create
	# DocPerm (employees have none). `doc.get("creation")` rather than `is_new()`
	# because frappe hands this hook a plain dict on some paths.
	if ptype == "create" or not doc.get("creation"):
		return True
	employee = _session_employee(user)
	return bool(employee) and doc.get("employee") == employee


# ------------------------------------------------------------------ Time Correction Request


def can_review(employee, user=None):
	"""May ``user`` decide a request filed by ``employee``? Manager roles, or the
	employee's ``reports_to`` login. The endpoint gate and the form's button test."""
	user = _resolve(user)
	if _has_any(TCR_MANAGER_ROLES, user):
		return True
	return bool(employee) and _reports_to_user(employee) == user


def time_correction_request_query_conditions(user=None):
	user = _resolve(user)
	if _has_any(TCR_MANAGER_ROLES, user):
		return ""
	employee = _session_employee(user)
	if not employee:
		return "1=0"
	own = f"`tabTime Correction Request`.`employee` = {frappe.db.escape(employee)}"
	team = (
		"`tabTime Correction Request`.`employee` in "
		f"(select `name` from `tabEmployee` where `reports_to` = {frappe.db.escape(employee)})"
	)
	return f"({own} or {team})"


def time_correction_request_has_permission(doc, ptype=None, user=None):
	user = _resolve(user)
	if _has_any(TCR_MANAGER_ROLES, user):
		return True
	if ptype == "create" or not doc.get("creation"):
		return True
	employee = _session_employee(user)
	if not employee:
		return False
	if doc.get("employee") == employee:
		return True
	# The supervisor: read only. Approve/decline go through the endpoint, which
	# writes with ignore_permissions after its own can_review check.
	if ptype in (None, "read", "print", "email", "report", "export", "share", "select"):
		return _reports_to_user(doc.get("employee")) == user
	return False
