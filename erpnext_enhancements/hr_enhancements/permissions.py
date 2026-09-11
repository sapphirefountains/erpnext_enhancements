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


def timeoff_query_conditions(user=None):
	"""Your own time off, your reports', and — if you are the named approver — theirs.

	**No tier arm, deliberately.** Time off is "who plans your week", which is
	exactly what ``Employee.reports_to`` means and exactly what a Position tier
	does not: a Senior Technician outranks a Junior on competence and has no
	standing at all over their Thursday. Giving the ladder a say here would be
	borrowing an authority nobody granted it.
	"""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return ""
	allowed = {resolved}
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if manager:
		allowed.update(
			u
			for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
			if u
		)
	joined = ", ".join(frappe.db.escape(u) for u in sorted(a for a in allowed if a))
	own = frappe.db.escape(resolved)
	table = "`tabTime Off Request`"
	return f"({table}.`user` in ({joined}) or {table}.`approver_user` = {own})"


def timeoff_has_permission(doc, ptype=None, user=None):
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return True
	if doc.get("user") == resolved or doc.get("approver_user") == resolved:
		return True
	# `user` is derived in validate(), so it is still empty when the permission
	# check runs on a NEW document -- which meant an ordinary employee was refused
	# permission to create their own request. Fall back to the Employee they named,
	# which is populated from the form.
	if doc.get("employee") and frappe.db.get_value(
		"Employee", doc.get("employee"), "user_id"
	) == resolved:
		return True
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if not manager:
		return False
	return frappe.db.exists("Employee", {"user_id": doc.get("user"), "reports_to": manager})


def onboarding_query_conditions(user=None):
	"""Your own checklist and your reports'. Same reasoning as time off."""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return ""
	allowed = {resolved}
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if manager:
		allowed.update(
			u
			for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
			if u
		)
	joined = ", ".join(frappe.db.escape(u) for u in sorted(a for a in allowed if a))
	return f"`tabOnboarding Checklist`.`user` in ({joined})"


def onboarding_has_permission(doc, ptype=None, user=None):
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return True
	if doc.get("user") == resolved:
		return True
	# Same as time off: `user` is derived in validate() and is empty on a new row.
	if doc.get("employee") and frappe.db.get_value(
		"Employee", doc.get("employee"), "user_id"
	) == resolved:
		return True
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if not manager:
		return False
	return frappe.db.exists("Employee", {"user_id": doc.get("user"), "reports_to": manager})


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


def tier_review_query_conditions(user=None):
	"""Your own review, and reviews you are the named reviewer on. Nothing else.

	**Tighter than time off, on purpose.** A Time Off Request says somebody is
	away on Thursday; a Tier Review is a list of what somebody cannot yet do, in
	their own words and their reviewer's. It is the most performance-shaped record
	in the module, and a colleague reading it is reading a ranking.

	So there is no reports_to arm here either: a manager sees a review because
	they are named on it, not because of where they sit on the tree. HR Manager and
	System Manager are unscoped, as everywhere in this module — deliberately,
	because somebody has to be able to answer "why was this person promoted" a year
	later.
	"""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return ""
	own = frappe.db.escape(resolved)
	table = "`tabTier Review`"
	return f"({table}.`user` = {own} or {table}.`reviewer_user` = {own})"


def tier_review_has_permission(doc, ptype=None, user=None):
	"""The document-level twin.

	A query condition filters lists and says nothing about ``frappe.get_doc()``, so
	without this a colleague could read any review by name through
	``/api/resource`` — exactly the gap that made three Training doctypes readable
	by customers in v1.386.0.
	"""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return True
	if doc.get("user") == resolved or doc.get("reviewer_user") == resolved:
		return True
	# `user` and `reviewer_user` are both derived in validate(), so both are empty
	# when the permission check runs on a NEW document. Fall back to the Employee
	# named on the form, or nobody could open a review for somebody else -- the
	# same defect the WI-072 branch review found in time off and onboarding.
	for field in ("employee", "reviewer"):
		if doc.get(field) and frappe.db.get_value("Employee", doc.get(field), "user_id") == resolved:
			return True
	return False


def restriction_query_conditions(user=None):
	"""Your own restrictions, and your reports'. Nothing else.

	A `Work Restriction` deliberately carries no medical reason, but it is still
	the most personal thing in the module by inference: "no lifting, no ladders,
	until the 14th" says something about somebody's health even with the why left
	out. So it is scoped the way time off is -- yourself and the people whose week
	you plan -- rather than being readable by every colleague the way a Position is.

	The dispatch advisory does not read through this. It runs server-side inside a
	`validate` hook and calls `availability.reasons_unavailable` directly, which is
	correct: the scheduler needs to be told the technician is restricted even when
	they are not that technician's manager. What they are told is the SUMMARY, and
	the record has nowhere to hold anything more.
	"""
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return ""
	allowed = {resolved}
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if manager:
		allowed.update(
			u
			for u in frappe.get_all("Employee", filters={"reports_to": manager}, pluck="user_id")
			if u
		)
	joined = ", ".join(frappe.db.escape(u) for u in sorted(a for a in allowed if a))
	return f"`tabWork Restriction`.`user` in ({joined})"


def restriction_has_permission(doc, ptype=None, user=None):
	resolved = _resolve(user)
	if _is_unscoped(resolved):
		return True
	if doc.get("user") == resolved:
		return True
	# `user` is derived in validate(), so it is empty on a NEW row -- the same
	# defect the WI-072 review found in time off and onboarding.
	if doc.get("employee") and frappe.db.get_value(
		"Employee", doc.get("employee"), "user_id"
	) == resolved:
		return True
	manager = frappe.db.get_value("Employee", {"user_id": resolved}, "name")
	if not manager:
		return False
	subject = frappe.db.get_value("Employee", {"user_id": doc.get("user")}, "reports_to")
	return subject == manager
