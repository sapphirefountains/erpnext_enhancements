# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Completion Matrix — one row per person, one column per course.

The question this answers is not "who passed what". It is the one a client or an
insurer asks: *is everybody who might turn up on a site currently certified for
the work?* That word — currently — is the whole report, and it is why this is a
Script Report rather than a Query Report.

A completion is a historical fact and stays true forever. Whether it still
*counts* is four separate things, none of which is a column you can select:

* it may have **expired** — ``expires_on`` is a date, and whether it has passed
  depends on today, not on whether the nightly sweep has run;
* it may have been **superseded** — the course changed materially since, so the
  pass is real history but no longer proves the person knows the current
  material;
* it may have been **revoked** — cancelled with a reason, which leaves the row
  visible on purpose;
* it may sit on an **older version** that was only a minor edit, which keeps the
  completion valid. That last one is the interesting case, and it is behind the
  *Current Version Only* filter rather than always applied: flagging every minor
  typo fix would bury the material changes that matter.

Deliberately shows people with **nothing** in a column. A compliance matrix whose
rows are "people who have completions" answers a question nobody asked; the gaps
are the report.

Staff and customer contacts are never mixed by default (``Learner Type``). They
are measured against different courses and read by different audiences, and a row
of client contacts in the middle of a crew compliance sheet is how a manager
mistakes one for the other.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

# Cell vocabulary. Kept as constants because the .js conditional formatting and
# the compliance percentage both key off these exact strings.
VALID = "Valid"
EXPIRING = "Expiring Soon"
EXPIRED = "Expired"
SUPERSEDED = "Superseded"
REVOKED = "Revoked"
OLDER_VERSION = "Older Version"
NOT_APPLICABLE = "—"

# What counts as compliant when the percentage is worked out. "Expiring Soon" is
# in: the certificate is still valid today, and a report that marked somebody
# non-compliant a month early would be crying wolf.
COMPLIANT = (VALID, EXPIRING)

# Assignment statuses that mean the obligation is still open, in the order they
# should win if somehow several rows exist.
OPEN_ASSIGNMENT_STATUSES = ("Overdue", "Awaiting Sign-off", "In Progress", "Not Started")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	courses = _courses_in_scope(filters)
	if not courses:
		return _columns([], filters), []

	learners = _learners_in_scope(filters, courses)
	if not learners:
		return _columns(courses, filters), []

	users = [row["user"] for row in learners]
	completions = _latest_completions(users, [c.name for c in courses])
	assignments = _open_assignments(users, [c.name for c in courses])

	horizon = getdate(add_days(nowdate(), cint(filters.get("expiring_within_days") or 30)))
	today = getdate(nowdate())

	data = []
	for learner in learners:
		row = dict(learner)
		applicable = compliant = 0
		for course in courses:
			cell = _cell(
				completion=completions.get((learner["user"], course.name)),
				assignment=assignments.get((learner["user"], course.name)),
				course=course,
				today=today,
				horizon=horizon,
				current_version_only=cint(filters.get("current_version_only")),
			)
			row[course.fieldname] = cell
			if cell != NOT_APPLICABLE:
				applicable += 1
				if cell in COMPLIANT:
					compliant += 1
		row["applicable"] = applicable
		row["compliant"] = compliant
		row["compliance_percent"] = round(compliant * 100.0 / applicable, 1) if applicable else 0.0
		data.append(row)

	# Least compliant first. A matrix sorted alphabetically is a matrix nobody
	# scrolls to the bottom of, and the bottom is where the problem is.
	data.sort(key=lambda r: (r["compliance_percent"], r["learner_name"].lower()))
	return _columns(courses, filters), data


# ------------------------------------------------------------------ columns


def _columns(courses, filters):
	scope = filters.get("learner_type") or "Employee"
	columns = [
		{"label": _("Learner"), "fieldname": "learner_name", "fieldtype": "Data", "width": 180},
		{"label": _("Login"), "fieldname": "user", "fieldtype": "Link", "options": "User", "width": 190},
	]
	if scope != "Website User":
		columns.append(
			{"label": _("Department"), "fieldname": "department", "fieldtype": "Link",
			 "options": "Department", "width": 150}
		)
	if scope != "Employee":
		columns.append(
			{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link",
			 "options": "Customer", "width": 160}
		)

	columns.extend(
		{"label": course.label, "fieldname": course.fieldname, "fieldtype": "Data", "width": 130}
		for course in courses
	)
	columns.extend(
		[
			{"label": _("Current"), "fieldname": "compliant", "fieldtype": "Int", "width": 80},
			{"label": _("Applies To Them"), "fieldname": "applicable", "fieldtype": "Int", "width": 120},
			{"label": _("Compliant %"), "fieldname": "compliance_percent", "fieldtype": "Percent", "width": 110},
		]
	)
	return columns


# -------------------------------------------------------------------- scope


def _courses_in_scope(filters):
	course_filters = {"status": "Published"}
	if filters.get("category"):
		course_filters["category"] = filters.category
	weight = filters.get("weight") or "Required"
	if weight != "All":
		course_filters["weight"] = weight
	if filters.get("course"):
		course_filters["name"] = filters.course

	rows = frappe.get_all(
		"Training Course",
		filters=course_filters,
		fields=["name", "course_title", "current_version", "audience"],
		order_by="course_title asc",
	)
	for row in rows:
		# Scrubbed docname, not the title: titles collide, contain punctuation, and
		# get renamed, and the fieldname is what the client-side formatting keys off.
		row.fieldname = frappe.scrub(row.name)
		row.label = row.course_title or row.name
	return rows


def _learners_in_scope(filters, courses):
	"""Everybody the matrix should have a row for, including people with nothing.

	Staff come from the Employee list rather than from training records, which is
	the point — an employee with no assignment and no completion is exactly the
	row somebody needs to see.
	"""
	scope = filters.get("learner_type") or "Employee"
	course_names = [c.name for c in courses]
	learners = {}

	if scope in ("Employee", "All"):
		employee_filters = {"status": "Active"}
		if filters.get("department"):
			employee_filters["department"] = filters.department
		for row in frappe.get_all(
			"Employee",
			filters=employee_filters,
			fields=["name", "employee_name", "user_id", "department"],
		):
			if not row.user_id:
				continue
			learners[row.user_id] = {
				"user": row.user_id,
				"learner_name": row.employee_name or row.user_id,
				"learner_type": "Employee",
				"department": row.department,
				"customer": None,
			}

	if scope in ("Website User", "All"):
		for user, customer in _customer_learners(filters, course_names).items():
			if user in learners:
				continue
			learners[user] = {
				"user": user,
				"learner_name": frappe.db.get_value("User", user, "full_name") or user,
				"learner_type": "Website User",
				"department": None,
				"customer": customer,
			}

	return sorted(learners.values(), key=lambda r: r["learner_name"].lower())


def _customer_learners(filters, course_names):
	"""``{user: customer}`` for the portal side.

	With a Customer named, every contact of theirs who has a login — gaps included,
	because a client wants to see which of their own people have not done it. With
	no Customer named, only people who already appear in training records, since
	"every contact of every customer" is a list nobody wants to read.
	"""
	from erpnext_enhancements.training.portal import portal_users_for_customer

	if filters.get("customer"):
		return {user: filters.customer for user in portal_users_for_customer(filters.customer)}

	out = {}
	for doctype in ("Training Assignment", "Training Completion"):
		for row in frappe.get_all(
			doctype,
			filters={"course": ["in", course_names], "customer": ["is", "set"]},
			fields=["user", "customer"],
		):
			if row.user:
				out.setdefault(row.user, row.customer)
	return out


# ------------------------------------------------------------------- records


def _latest_completions(users, course_names):
	"""``{(user, course): row}`` — the newest submitted completion per pair.

	Ascending order with a plain overwrite, so the last write per pair wins and
	the newest survives. Cancelled completions are excluded by ``docstatus 1``:
	a revoked pass must not read as compliant, which is the whole reason the
	cancel path exists.
	"""
	if not users or not course_names:
		return {}
	out = {}
	for row in frappe.get_all(
		"Training Completion",
		filters={"user": ["in", users], "course": ["in", course_names], "docstatus": 1},
		fields=["name", "user", "course", "course_version", "status", "completed_on", "expires_on"],
		order_by="completed_on asc, creation asc",
	):
		out[(row.user, row.course)] = row
	return out


def _open_assignments(users, course_names):
	"""``{(user, course): status}`` for obligations that are still open."""
	if not users or not course_names:
		return {}
	rank = {status: index for index, status in enumerate(OPEN_ASSIGNMENT_STATUSES)}
	out = {}
	for row in frappe.get_all(
		"Training Assignment",
		filters={
			"user": ["in", users],
			"course": ["in", course_names],
			"status": ["in", [*OPEN_ASSIGNMENT_STATUSES, "Waived"]],
		},
		fields=["user", "course", "status"],
	):
		key = (row.user, row.course)
		current = out.get(key)
		if current is None or rank.get(row.status, 99) < rank.get(current, 99):
			out[key] = row.status
	return out


# ---------------------------------------------------------------------- cell


def _cell(completion, assignment, course, today, horizon, current_version_only):
	"""The one string that says where this person stands on this course.

	Order is the point. Expiry is tested before the stored status because expiry
	is a function of today and the stored status is a function of whenever the
	sweep last ran; trusting the stored value would report somebody as certified
	on the morning their certificate lapsed.
	"""
	if completion:
		if completion.status == "Revoked":
			return REVOKED
		if completion.expires_on and getdate(completion.expires_on) < today:
			return EXPIRED
		if completion.status == "Superseded":
			return SUPERSEDED
		if (
			current_version_only
			and course.current_version
			and completion.course_version != course.current_version
		):
			# A minor edit keeps completions valid, so this is off by default —
			# see the module docstring.
			return OLDER_VERSION
		if completion.expires_on and getdate(completion.expires_on) <= horizon:
			return EXPIRING
		return VALID

	if assignment:
		return assignment

	return NOT_APPLICABLE
