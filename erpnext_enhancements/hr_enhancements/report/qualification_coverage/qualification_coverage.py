# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Skills Matrix turned sideways: how many people hold each thing.

The matrix answers *"who can I send?"* — one row per person. This answers the
question underneath it, which nothing in the app could ask: **"how many of us can
do this at all?"**

That number is the shop's real exposure. Sign-off authority is strictly-higher tier
within the same job family, so the count that matters is how many people sit ABOVE
a rung, not on it.

Measured on prod 2026-09-11: three Junior Technicians (tier 1) and **two** Senior
Technicians (tier 2), no Master. So two people can attest that a junior may work
alone. That still trips the `THIN` threshold of 2 and still renders — which is the
point of the threshold — but it is no longer the single point of failure this
docstring described until v1.404.0, when it read "four Junior Technicians and one
Senior". It was true when written and quietly stopped being true. Restated with a
date because the next reader will otherwise take it as current ground truth, which
is exactly how WI-072's three deferred items acquired their wrong reasons.

The deeper gap the numbers hide: `tabPosition Requirement` holds **zero** rows
across all twenty Positions, and `tabEmployee Credential` holds zero, against a
seeded taxonomy of fifteen Credential Types. So the credential and course halves of
this grid are correctly reporting that nobody holds anything, because nothing has
ever said what a position requires. Nothing surfaced any of this before, because
every existing view is per-person and a per-person view cannot show you a count of
one — or of none.

Three sources, deliberately in one grid rather than three reports: an internal
course, an external credential, and **sign-off authority itself**, which is the
one nobody would think to look at and the one that has the smallest number
against it.

`Only where cover is thin` is on by default. A report that opens on forty rows of
"eight people hold this" buries the three rows that matter, and the rows that
matter are the whole point.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

HORIZON_DAYS = 90

COURSE = "Course"
CREDENTIAL = "Credential"
AUTHORITY = "Sign-off authority"

#: At or below this, the row is drawn as thin cover. Two is the honest line for a
#: sixteen-person shop: one is a single point of failure and two is one holiday
#: away from being one.
THIN = 2


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = []
	rows.extend(_courses(filters))
	rows.extend(_credentials(filters))
	rows.extend(_authority(filters))

	if not cint(filters.get("show_all")):
		rows = [r for r in rows if r["current"] <= THIN]

	# Thinnest first, then by kind, then by name -- so the top of the screen is
	# always the thing most likely to strand a job.
	rows.sort(key=lambda r: (r["current"], r["kind"], r["qualification"]))
	return _columns(), rows, _message(rows, filters)


def _message(rows, filters):
	if not rows:
		if not cint(filters.get("show_all")):
			return _(
				"Nothing is held by {0} or fewer people. Tick <b>Show everything</b> to see the "
				"full picture."
			).format(THIN)
		return _("Nothing to count yet — no Published and Required courses, and no credentials.")
	alone = [r for r in rows if r["current"] == 1]
	nobody = [r for r in rows if r["current"] == 0]
	parts = []
	if nobody:
		parts.append(
			_("<b>{0}</b> thing(s) nobody currently holds.").format(len(nobody))
		)
	if alone:
		parts.append(
			_(
				"<b>{0}</b> thing(s) exactly one person holds — each of those is a job that "
				"cannot happen when they are off."
			).format(len(alone))
		)
	return " ".join(str(p) for p in parts) or None


# ---------------------------------------------------------------------- sources


def _courses(filters):
	"""Required, Published courses, counted by valid completion."""
	out = []
	horizon = getdate(add_days(today(), HORIZON_DAYS))
	staff = _staff_users()
	for course in frappe.get_all(
		"Training Course",
		filters={"status": "Published", "weight": "Required"},
		fields=["name", "course_title"],
	):
		current = expiring = 0
		for row in frappe.get_all(
			"Training Completion",
			filters={"course": course.name, "docstatus": 1, "status": "Valid"},
			fields=["user", "expires_on"],
		):
			if row.user not in staff:
				continue
			if row.expires_on and getdate(row.expires_on) < getdate(today()):
				continue
			current += 1
			if row.expires_on and getdate(row.expires_on) <= horizon:
				expiring += 1
		out.append(_row(COURSE, course.course_title or course.name, current, expiring, staff))
	return out


def _credentials(filters):
	if not frappe.db.exists("DocType", "Employee Credential"):
		return []
	staff = _staff_users()
	out = []
	for kind in frappe.get_all("Credential Type", filters={"is_active": 1}, pluck="name"):
		held = frappe.get_all(
			"Employee Credential",
			filters={"credential_type": kind, "status": ["in", ("Valid", "Expiring")]},
			fields=["employee", "status"],
		)
		# By EMPLOYEE, deduplicated: somebody who renewed keeps both rows, and
		# counting both would report cover of two where there is one person.
		by_employee = {}
		for row in held:
			by_employee.setdefault(row.employee, set()).add(row.status)
		current = len(by_employee)
		expiring = sum(1 for statuses in by_employee.values() if statuses == {"Expiring"})
		out.append(_row(CREDENTIAL, kind, current, expiring, staff))
	return out


def _authority(filters):
	"""How many people can sign off each rung — the count nobody thinks to take.

	Counted as "people whose Position outranks this rung on the same ladder", which
	is the predicate `training/authority.py` actually uses, rather than as a head
	count of seniors. A Master Technician covers a Junior; a Senior Designer does
	not.
	"""
	if not frappe.db.exists("DocType", "Position"):
		return []
	try:
		if not frappe.db.has_column("Employee", "custom_position"):
			return []
	except Exception:
		return []

	holders = {}
	for row in frappe.get_all(
		"Employee",
		filters={"status": "Active", "custom_position": ["is", "set"]},
		fields=["name", "custom_position"],
	):
		holders.setdefault(row.custom_position, []).append(row.name)

	out = []
	for rung in frappe.get_all(
		"Position",
		filters={"is_active": 1, "is_group": 0},
		fields=["name", "position_name", "job_family", "tier"],
	):
		if not holders.get(rung.name):
			# Nobody stands on this rung, so nobody needs signing off on it. Counting
			# its cover would fill the report with empty ladders.
			continue
		above = frappe.get_all(
			"Position",
			filters={
				"job_family": rung.job_family,
				"tier": [">", cint(rung.tier)],
				"is_active": 1,
				"is_group": 0,
			},
			pluck="name",
		)
		signers = sum(len(holders.get(p) or []) for p in above)
		out.append(
			{
				"kind": AUTHORITY,
				"qualification": _("Sign off a {0}").format(rung.position_name or rung.name),
				"current": signers,
				"expiring": 0,
				"of_staff": len(holders.get(rung.name) or []),
				"note": _("{0} person(s) stand on that rung").format(len(holders.get(rung.name) or [])),
			}
		)
	return out


# ----------------------------------------------------------------------- pieces


def _row(kind, label, current, expiring, staff):
	note = ""
	if current == 0:
		note = _("Nobody holds this")
	elif current == 1:
		note = _("One person. A job needing this cannot happen when they are off.")
	elif expiring and expiring >= current:
		note = _("Everybody who holds it is inside the renewal horizon")
	return {
		"kind": kind,
		"qualification": label,
		"current": current,
		"expiring": expiring,
		"of_staff": len(staff),
		"note": note,
	}


def _staff_users():
	"""Active employees with a login, as a set of users.

	The denominator, and the filter: a completion belonging to a customer contact
	is not company cover.
	"""
	return {
		u
		for u in frappe.get_all("Employee", filters={"status": "Active"}, pluck="user_id")
		if u
	}


def _columns():
	return [
		{"fieldname": "kind", "label": _("Kind"), "fieldtype": "Data", "width": 140},
		{"fieldname": "qualification", "label": _("Qualification"), "fieldtype": "Data", "width": 260},
		{"fieldname": "current", "label": _("How many"), "fieldtype": "Int", "width": 100},
		{"fieldname": "expiring", "label": _("Of those, expiring"), "fieldtype": "Int", "width": 140},
		{"fieldname": "of_staff", "label": _("Staff"), "fieldtype": "Int", "width": 80},
		{"fieldname": "note", "label": _("Note"), "fieldtype": "Data", "width": 400},
	]
