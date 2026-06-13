"""Row-level access for Managed Device — employees see only their own device.

Wired in hooks.py via ``permission_query_conditions`` (list views, link
searches, reports) and ``has_permission`` (form open). Device Managers / HR
Managers / System Managers see the whole fleet; everyone else is scoped to the
device currently assigned to them (``assigned_to_user``). This is the BYOD-
privacy backstop that complements the ``permlevel: 1`` hardware-identifier
fields: an employee can see and attest their own phone but not browse the fleet
or read other people's serials.

Note frappe semantics: a ``has_permission`` hook can only *restrict* what the
role DocPerms already grant. Managed Device grants the Employee role read-only;
this narrows that to their own device.
"""

import frappe

# Roles that see the entire fleet.
VIEW_ALL_ROLES = {"System Manager", "Device Manager", "HR Manager"}


def _sees_all(user):
	return user == "Administrator" or bool(VIEW_ALL_ROLES & set(frappe.get_roles(user)))


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _sees_all(user):
		return ""
	return f"`tabManaged Device`.`assigned_to_user` = {frappe.db.escape(user)}"


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _sees_all(user):
		return True
	# Creates are governed by the DocPerm (employees have none); never block here.
	if ptype == "create" or doc.is_new():
		return True
	return doc.get("assigned_to_user") == user
