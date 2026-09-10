# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for HR Enhancements.

One doctype needs it and it needs it badly. ``Employee Credential`` grants
``read`` to the ``Employee`` role, which every staff account on this site holds —
that grant is deliberate (it is what puts a technician's own forklift ticket on
their own profile, and it is what keeps the whole module reachable through
``allow_modules``) and without a scoping hook it would put **everybody's** driving
licence number, medical card and certificate numbers in front of everybody.

DocPerms with no scoping hook is the one combination that leaks, and this app has
just finished paying for that lesson three times over in the Training module
(``Training Badge Award``, ``Training Learner Stat`` and ``Training Question
Thread``, all of them readable by customer contacts until v1.386.0). So the hook
ships in the same commit as the DocPerm, not after it.

Who sees whose
--------------
Deliberately the same three arms as ``training/permissions.py``, because a
supervisor who can see somebody's training and not their tickets has half an
answer to "can I send this person":

* **your own**, always;
* **your direct reports**, via ``Employee.reports_to``;
* **the people you outrank on your own ladder**, via ``Position`` — the arm that
  exists because on this site the Senior Technician has zero direct reports and
  the four Junior Technicians he is responsible for all report to the Project
  Manager.

Unscoped for the managers who administer this: System Manager, HR Manager, HR
User. HR User is on that list and Training's equivalent list does not carry it —
the difference is intentional. Training records are performance data; a credential
register is the filing cabinet, and the person filing is exactly who needs to see
all of it.

Note what a ``has_permission`` hook can and cannot do (same as everywhere else in
this app): it can only **restrict** what role DocPerms already grant. The role
rows in the doctype JSON remain the outer bound.
"""

import frappe

UNSCOPED_ROLES = {"System Manager", "HR Manager", "HR User"}


def _resolve(user):
	return user or frappe.session.user


def _is_unscoped(user):
	return bool(UNSCOPED_ROLES & set(frappe.get_roles(_resolve(user))))


def _visible_users(user):
	"""Every login whose credentials *user* may read. Always includes themselves."""
	allowed = {user}

	manager = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if manager:
		allowed.update(
			u
			for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
			if u
		)

	try:
		from erpnext_enhancements.training import authority

		allowed.update(authority.signable_learner_users(user))
	except Exception:
		# The ladder is unavailable (custom field not migrated, Position not yet
		# installed). Degrade to own + direct reports rather than failing the read:
		# a person must always be able to see their own licence.
		pass

	return sorted(a for a in allowed if a)


def credential_query_conditions(user=None):
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return ""
	joined = ", ".join(frappe.db.escape(u) for u in _visible_users(resolved))
	return f"`tabEmployee Credential`.`user` in ({joined})"


def credential_has_permission(doc, ptype=None, user=None):
	"""The single-document twin. A query condition filters lists and says nothing
	about ``frappe.get_doc()``, so shipping one without the other leaves the hole
	in whichever half you skipped."""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return True
	return doc.get("user") in _visible_users(resolved)
