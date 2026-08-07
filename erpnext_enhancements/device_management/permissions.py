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
	#
	# `not doc.get("creation")` rather than `doc.is_new()`: Frappe calls
	# has_permission hooks with a plain dict on some paths (anything that reaches
	# `frappe.has_permission(doctype, doc=...)` with a `db.get_value(...,
	# as_dict=True)` row), and a dict has no `.is_new()` — it raises
	# `AttributeError: 'dict' object has no attribute 'is_new'` from inside a
	# permission check, which surfaces as an unrelated-looking save failure.
	#
	# This is NOT a drop-in restatement of `is_new()`, and v1.254.0's comment
	# claiming it was is simply wrong. On Frappe v16 `is_new()` is
	# `bool(self.get("__islocal"))` (base_document.py:631) — a different
	# predicate. "Has no creation timestamp" is the right test *for this hook*
	# because the only thing it needs to answer is "is there a saved row whose
	# owner I should compare against"; an unsaved doc has nothing to compare and
	# is governed by the create DocPerm instead. Do not propagate the equivalence
	# claim to somewhere it actually matters.
	if ptype == "create" or not doc.get("creation"):
		return True
	return doc.get("assigned_to_user") == user
