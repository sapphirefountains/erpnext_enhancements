# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level access for Travel Trip: crew members see and edit the trips they
are on; coordinators see everything.

Wired in hooks.py via ``permission_query_conditions`` (list views, link
searches, reports) and ``has_permission`` (form open, writes). Access tracks
the ``travelers`` child table live — adding a traveler grants access on the
next request, removing one revokes it — which is why this is hook-based
instead of ``frappe.share`` records (shares would orphan when the crew
changes).

Note frappe semantics: a ``has_permission`` hook can only *restrict* what the
role DocPerms already grant. The Employee role gets read/write/create on
Travel Trip at the role level; this module narrows that to own/crew trips.
"""

import frappe

from erpnext_enhancements.travel_management import TRAVEL_COORDINATOR_ROLES


def _is_coordinator(user):
	return user == "Administrator" or bool(
		TRAVEL_COORDINATOR_ROLES & set(frappe.get_roles(user))
	)


def _session_employee(user):
	return frappe.db.get_value("Employee", {"user_id": user}, "name")


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_coordinator(user):
		return ""

	conditions = [f"`tabTravel Trip`.`owner` = {frappe.db.escape(user)}"]
	employee = _session_employee(user)
	if employee:
		conditions.append(
			"exists (select 1 from `tabTrip Traveler` tt"
			" where tt.parenttype = 'Travel Trip'"
			" and tt.parent = `tabTravel Trip`.`name`"
			f" and tt.employee = {frappe.db.escape(employee)})"
		)
	return "(" + " or ".join(conditions) + ")"


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _is_coordinator(user):
		return True
	if ptype == "create" or doc.is_new():
		return True  # creating their own trip; validate() handles the rest
	if doc.owner == user:
		return True

	employee = _session_employee(user)
	if employee and any(t.employee == employee for t in doc.get("travelers", [])):
		return True  # crew members read AND edit collaboratively
	return False
