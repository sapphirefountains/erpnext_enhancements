# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who owes which training, and how that gets decided without anyone remembering to.

A Required course carries ``Training Assignment Rule`` rows — "everyone in
Production", "every Senior Technician", "anyone on the Technician role profile".
This module turns those rows into people, and raises a ``Training Assignment``
for each person who does not already have one open.

It runs from three places, and the guards on each matter more than the resolution
logic does:

* **Employee ``after_insert``** — a new hire picks up everything they owe on day
  one, which is the entire point of the feature.
* **Employee ``on_update``** — but *only* when department, designation, grade,
  employment type or status actually changed. Without that comparison every
  Employee save enqueues a full rule sweep, and Employee is saved often.
* **User ``on_update``** — a Role Profile change rewrites a user's roles wholesale
  and can bring a course into scope for someone the Employee hook never sees.
  Guarded on a roles diff and enqueued after commit, because this fires during
  login-adjacent writes and a slow sweep must never delay one.

Everything is gated on ``Training Settings`` — master switch plus
``auto_assign_enabled`` — so the module can ship dormant and be turned on
deliberately once a first course exists.
"""

import frappe
from frappe.utils import add_days, cint, today

from erpnext_enhancements.training import roles
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

# The Employee fields a rule can key off. A save that touches none of them cannot
# change what anybody owes, so it is not worth a sweep.
EMPLOYEE_TRIGGER_FIELDS = ("department", "designation", "grade", "employment_type", "status")

# Rule targets that resolve against a field on Employee.
EMPLOYEE_FIELD_FOR_RULE = {
	"Department": "department",
	"Designation": "designation",
	"Employee Grade": "grade",
	"Employment Type": "employment_type",
}


# --------------------------------------------------------------------- doc events


def on_employee_insert(doc, method=None):
	"""A new hire owes whatever the rules say they owe."""
	if not _active():
		return
	if not (doc.get("user_id") or "").strip():
		# Employees are routinely created before a login exists. Nothing to assign
		# to yet; the on_update hook picks it up when user_id is filled in.
		return
	# Assigning a course to somebody who cannot open /training reads as the
	# feature being broken, so the role comes first.
	roles.grant_learner_role(doc.user_id)
	frappe.enqueue(
		"erpnext_enhancements.training.assignment.sync_employee",
		queue="short",
		enqueue_after_commit=True,
		employee=doc.name,
	)


def on_employee_update(doc, method=None):
	"""Re-evaluate only when something a rule can key off actually moved."""
	if not _active():
		return
	before = doc.get_doc_before_save()
	if not before:
		return

	changed = any(
		(before.get(field) or "") != (doc.get(field) or "") for field in EMPLOYEE_TRIGGER_FIELDS
	)
	# Filling in user_id for the first time is the other thing worth reacting to —
	# it is what makes a previously unassignable employee assignable.
	gained_login = not (before.get("user_id") or "") and (doc.get("user_id") or "")
	if not changed and not gained_login:
		return

	if gained_login:
		roles.grant_learner_role(doc.user_id)

	frappe.enqueue(
		"erpnext_enhancements.training.assignment.sync_employee",
		queue="short",
		enqueue_after_commit=True,
		employee=doc.name,
	)


def on_user_roles_changed(doc, method=None):
	"""A Role Profile change can bring a role-targeted course into scope.

	User is written on paths adjacent to login, so this bails out as early and as
	cheaply as it can and never does work inline.
	"""
	if not _active():
		return
	before = doc.get_doc_before_save()
	if not before:
		return

	old_roles = {row.role for row in (before.get("roles") or [])}
	new_roles = {row.role for row in (doc.get("roles") or [])}
	if old_roles == new_roles:
		return

	frappe.enqueue(
		"erpnext_enhancements.training.assignment.sync_user",
		queue="short",
		enqueue_after_commit=True,
		user=doc.name,
	)


# ------------------------------------------------------------------- background


def sync_employee(employee):
	"""Raise any missing assignments for one employee."""
	user = frappe.db.get_value("Employee", employee, "user_id")
	if user:
		sync_user(user)


def sync_user(user):
	"""Raise any missing assignments for one user, across every auto-assign course."""
	if not _active() or not frappe.db.exists("User", user):
		return

	created = 0
	for course in _auto_assign_courses():
		rule = _matching_rule(course, user)
		if not rule:
			continue
		if _assign(course.name, user, rule):
			created += 1
	if created:
		frappe.db.commit()


def sync_course(course_name):
	"""Raise assignments for everybody a course's rules currently match.

	Used when a course is first published, or when its rules are edited. Runs over
	active employees only — a customer never picks up a course from a rule, since
	rules target org structure the customer has none of.
	"""
	if not _active():
		return

	course = frappe.get_cached_doc("Training Course", course_name)
	if course.weight != "Required" or not cint(course.auto_assign):
		return

	created = 0
	for user in _candidate_users():
		rule = _matching_rule(course, user)
		if rule and _assign(course.name, user, rule):
			created += 1
	if created:
		frappe.db.commit()
	return created


# ---------------------------------------------------------------------- helpers


def _active():
	"""Auto-assignment needs the master switch *and* its own switch, and must never
	run while the site is mid-migrate — the doctypes may not be in place yet, and a
	migrate is not a moment to email fifteen people."""
	if (
		frappe.flags.in_migrate
		or frappe.flags.in_install
		or frappe.flags.in_patch
		or frappe.flags.in_import
		or frappe.flags.in_test
	):
		return False
	return is_enabled("auto_assign_enabled")


def _auto_assign_courses():
	"""Published, Required courses that have asked to be assigned automatically."""
	names = frappe.get_all(
		"Training Course",
		filters={"status": "Published", "weight": "Required", "auto_assign": 1},
		pluck="name",
	)
	return [frappe.get_cached_doc("Training Course", name) for name in names]


def _candidate_users():
	"""Users of active employees. All 15 active employees on this site have one;
	an employee without a login simply cannot be assigned to yet."""
	return frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"]},
		pluck="user_id",
	)


def _matching_rule(course, user):
	"""The first enabled rule on ``course`` that matches ``user``, or None.

	First rather than all, deliberately: a person matched by two rules is still
	assigned once, and the first match is the one recorded so the assignment can
	explain itself later.
	"""
	employee = frappe.db.get_value(
		"Employee",
		{"user_id": user, "status": "Active"},
		["name", "department", "designation", "grade", "employment_type"],
		as_dict=True,
	)
	roles = None

	for rule in course.assign_rules or []:
		if not cint(rule.enabled):
			continue

		if rule.applies_to == "All Employees":
			if employee:
				return rule
			continue

		if rule.applies_to == "Role":
			if roles is None:
				roles = set(frappe.get_roles(user))
			if rule.applies_to_value in roles:
				return rule
			continue

		if rule.applies_to == "Role Profile":
			# Against the child table, NOT the scalar ``User.role_profile_name``.
			# Users here hold several profiles at once (Brian is Sales + Sales
			# Team; Lian is Production Team + Technician), and the scalar only
			# ever reflects one of them — so matching on it would silently assign
			# nobody for every secondary profile. This module makes that worse by
			# design: ``training/roles.py`` adds a "Training Learner" profile
			# alongside everybody's real one, so secondary profiles are the norm
			# on this site rather than the exception.
			if _has_role_profile(user, rule.applies_to_value):
				return rule
			continue

		field = EMPLOYEE_FIELD_FOR_RULE.get(rule.applies_to)
		if not field or not employee:
			continue
		# Employee Grade and Employment Type are ERPNext core but not guaranteed to
		# be populated; a rule against a field this site does not use simply never
		# matches rather than erroring.
		if not frappe.db.has_column("Employee", field):
			continue
		if (employee.get(field) or "") == rule.applies_to_value:
			return rule

	return None


def _has_role_profile(user, role_profile):
	"""True when ``user`` holds ``role_profile``, by any route.

	Checks the ``User Role Profile`` child rows first — that is the real,
	multi-valued answer — and falls back to the legacy scalar for a site or a
	fixture that only ever set that.
	"""
	if not role_profile:
		return False
	if frappe.db.exists(
		"User Role Profile",
		{"parent": user, "parenttype": "User", "role_profile": role_profile},
	):
		return True
	return frappe.db.get_value("User", user, "role_profile_name") == role_profile


def _assign(course, user, rule):
	"""Create an assignment unless one is already open. Returns True if it made one.

	Never raises: one bad row must not abort a sweep across fifteen people, and the
	duplicate check in ``Training Assignment.validate`` is the authority — this is
	only the cheap pre-check that avoids the exception in the common case.
	"""
	from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
		OPEN_STATUSES,
	)

	if frappe.db.exists(
		"Training Assignment", {"course": course, "user": user, "status": ["in", OPEN_STATUSES]}
	):
		return False

	due_days = cint(rule.due_days) or cint(frappe.db.get_value("Training Course", course, "due_days"))
	if not due_days:
		due_days = cint(frappe.db.get_single_value("Training Settings", "default_due_days")) or 14

	try:
		doc = frappe.get_doc(
			{
				"doctype": "Training Assignment",
				"course": course,
				"user": user,
				"status": "Not Started",
				"assigned_on": today(),
				"due_date": add_days(today(), due_days),
				"assignment_source": "Auto Rule",
				"source_rule": f"{rule.applies_to}: {rule.applies_to_value or '*'}",
			}
		)
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			f"Auto-assign failed for {user} on {course}\n{frappe.get_traceback()}",
			"Training auto-assignment",
		)
		return False

	from erpnext_enhancements.training import notifications

	notifications.notify_assigned(doc)
	return True
