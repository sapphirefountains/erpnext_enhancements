# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The profile: what one person has done, and what colleagues may see of it.

Two audiences, two payloads, and **the difference between them is the whole
design**. Your own profile is a working record — what you owe, what is expiring,
what you scored. A colleague's is a directory entry: what they are qualified for.

The rule, stated once so every field below can be checked against it:

    Your own profile answers "what do I need to do?".
    A colleague's answers "who around here knows how to do this?".

So a colleague sees badges, completed course *titles*, and which qualifications
someone currently holds. A colleague never sees a **score**, an **attempt count**,
a **failure**, a **due date**, a **licence number**, or anything about a course
somebody is part-way through. Those are either performance data or personal
documents, and neither is a directory question.

That is not a filter applied at the end. ``colleague_profile`` builds its payload
by **selecting** ``PUBLIC_FIELDS`` out of the shared dict; there is no "full
profile minus some keys" path that a later field addition could silently widen,
because the self-only panels are added by ``my_profile`` *after* ``_shared``
returns and never exist on a colleague payload at all. The gamification module made the same
choice for the same reason and wrote it down: *"an optional privacy filter is a
privacy filter somebody eventually leaves out."*

Customers see none of this
--------------------------
``Training Learner`` is held by customer Website Users as well as staff. Every
entry point here refuses anybody without an ``Employee`` record — not by checking
a role, which can be granted by accident, but by requiring the employment record
the whole idea of a colleague depends on. A customer contact asking for the
directory gets an empty one.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, today

#: What a colleague may see. Kept as an explicit tuple rather than an exclusion
#: list, so a field added to the profile later is invisible to colleagues until
#: somebody deliberately adds it here.
PUBLIC_FIELDS = (
	"user",
	"full_name",
	"employee",
	"designation",
	"position",
	"department",
	"badges",
	"completed",
	"qualifications",
	"points",
	"started_on",
	"years_of_service",
)


def _employee_of(user):
	if not user:
		return None
	return frappe.db.get_value(
		"Employee",
		{"user_id": user, "status": "Active"},
		[
			"name",
			"employee_name",
			"designation",
			"department",
			"reports_to",
			"date_of_joining",
			"cell_number",
			"company_email",
		],
		as_dict=True,
	)


def _is_staff(user):
	"""Employment, not a role.

	A role can be granted by accident — ``Training Learner`` is on every customer
	contact — and the question "may this person browse the staff directory?" is
	really "does this person work here?".
	"""
	return bool(frappe.db.exists("Employee", {"user_id": user, "status": "Active"}))


def my_profile(user):
	"""Everything about *user*, for *user*. The working record."""
	employee = _employee_of(user)
	profile = _shared(user, employee)
	profile.update(
		{
			"is_self": True,
			"manager": _manager_name(employee),
			"assigned": _assigned(user),
			"expiring": _expiring(user, employee),
			"devices": _devices(employee),
			"work_anniversary": _anniversary(employee),
		}
	)
	return profile


def colleague_profile(viewer, user):
	"""What *viewer* may see of *user*. Refuses unless both actually work here."""
	if not _is_staff(viewer):
		frappe.throw(_("Only staff can look up colleagues."), frappe.PermissionError)
	if user == viewer:
		return my_profile(user)
	if not _is_staff(user):
		frappe.throw(_("No such colleague."))

	employee = _employee_of(user)
	full = _shared(user, employee, viewer=viewer)
	# Built by SELECTING the public keys, never by deleting private ones. A profile
	# that grows a field must not become a profile that leaks one.
	profile = {key: full.get(key) for key in PUBLIC_FIELDS}
	profile["is_self"] = False
	return profile


def _shared(user, employee, viewer=None):
	"""The half both audiences see. Nothing in here is performance data."""
	employee = employee or frappe._dict()
	return {
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name") or user,
		"employee": employee.get("name"),
		"designation": employee.get("designation") or "",
		"position": _position(employee),
		"department": employee.get("department") or "",
		"started_on": str(employee.get("date_of_joining") or "") or None,
		"years_of_service": _tenure(employee),
		"badges": _badges(user),
		"completed": _completed(user),
		"qualifications": _qualifications(employee),
		"points": _points(user, viewer=viewer),
	}


def _position(employee):
	name = employee.get("custom_position") if employee else None
	if not name and employee and employee.get("name"):
		name = frappe.db.get_value("Employee", employee.get("name"), "custom_position") if (
			frappe.db.has_column("Employee", "custom_position")
		) else None
	if not name:
		return None
	row = frappe.db.get_value("Position", name, ["name", "tier_label", "job_family"], as_dict=True)
	if not row:
		return None
	return {"name": row.name, "tier_label": row.tier_label or "", "job_family": row.job_family or ""}


def _manager_name(employee):
	if not employee or not employee.get("reports_to"):
		return None
	return frappe.db.get_value("Employee", employee.reports_to, "employee_name")


def _tenure(employee):
	joined = employee.get("date_of_joining") if employee else None
	if not joined:
		return None
	return round(date_diff(today(), getdate(joined)) / 365.25, 1)


def _anniversary(employee):
	"""The next one, as a date string. ``None`` when there is no joining date.

	Computed rather than stored: an anniversary that is a field is an anniversary
	somebody has to remember to roll over.
	"""
	joined = employee.get("date_of_joining") if employee else None
	if not joined:
		return None
	joined = getdate(joined)
	now = getdate(today())
	try:
		nxt = joined.replace(year=now.year)
	except ValueError:
		# 29 February. Treat it as the 28th rather than skipping three years in four.
		nxt = joined.replace(year=now.year, day=28)
	if nxt < now:
		try:
			nxt = nxt.replace(year=now.year + 1)
		except ValueError:
			nxt = nxt.replace(year=now.year + 1, day=28)
	return str(nxt)


def _badges(user):
	if not frappe.db.exists("DocType", "Training Badge Award"):
		return []
	rows = frappe.get_all(
		"Training Badge Award",
		filters={"user": user},
		fields=["badge", "awarded_on", "points"],
		order_by="awarded_on desc",
	)
	for row in rows:
		detail = frappe.db.get_value(
			"Training Badge", row.badge, ["description", "image"], as_dict=True
		) or frappe._dict()
		row["description"] = detail.get("description") or ""
		row["image"] = detail.get("image") or ""
	return rows


def _completed(user):
	"""Courses finished, titles only.

	No score, no attempt, no coverage, and **no `Superseded` or `Expired` rows**.
	A colleague asking "can they do this?" is asking about now; a lapsed
	certification is the holder's business and their supervisor's, and it shows on
	their own profile under `expiring`.
	"""
	if not frappe.db.exists("DocType", "Training Completion"):
		return []
	return frappe.get_all(
		"Training Completion",
		filters={"user": user, "docstatus": 1, "status": "Valid"},
		fields=["course", "course_title_snapshot as title", "completed_on"],
		order_by="completed_on desc",
	)


def _qualifications(employee):
	"""External credentials, **without the numbers**.

	A colleague may know somebody holds a forklift ticket. A colleague has no
	business with the certificate number, the issuing body or the scan of the card
	— those are the personal document, and this returns what is on the wall rather
	than what is in the wallet.
	"""
	if not employee or not employee.get("name"):
		return []
	if not frappe.db.exists("DocType", "Employee Credential"):
		return []
	return frappe.get_all(
		"Employee Credential",
		filters={"employee": employee.name, "status": ["in", ("Valid", "Expiring")]},
		fields=["credential_type as name", "status"],
		order_by="credential_type asc",
	)


def _points(user, viewer=None):
	"""Points, unless this person has taken themselves off the board.

	The opt-out has to hold here too. Somebody who left the leaderboard and then
	found their score on their own profile card, visible to every colleague, would
	reasonably conclude the setting did nothing — and they would be right. Their
	own view still shows it; it is the *public* number that goes.
	"""
	if not frappe.db.exists("DocType", "Training Learner Stat"):
		return 0
	if viewer and viewer != user:
		try:
			from erpnext_enhancements.training import social

			if not social.get_preferences(user).get("show_on_leaderboard"):
				return None
		except Exception:
			# Preferences unavailable (doctype not migrated). Fail closed on a
			# number nobody needs rather than publish one somebody may have opted
			# out of.
			return None
	return cint(frappe.db.get_value("Training Learner Stat", {"user": user}, "total_points"))


def _assigned(user):
	"""Open assignments. **Self only** — a due date is a to-do list, not a fact
	about somebody, and a colleague reading one is reading over their shoulder."""
	if not frappe.db.exists("DocType", "Training Assignment"):
		return []
	from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
		OPEN_STATUSES,
	)

	return frappe.get_all(
		"Training Assignment",
		filters={"user": user, "status": ["in", OPEN_STATUSES]},
		fields=["course", "course_title", "status", "due_date"],
		order_by="due_date asc",
	)


def _expiring(user, employee):
	"""What lapses soon, from both sources, self only.

	Merged deliberately: the person does not care whether the thing about to run
	out came out of an internal course or a state DMV, only that it runs out.
	"""
	out = []
	if frappe.db.exists("DocType", "Training Completion"):
		for row in frappe.get_all(
			"Training Completion",
			filters={"user": user, "docstatus": 1, "status": ["in", ("Valid", "Expired")],
					 "expires_on": ["is", "set"]},
			fields=["course_title_snapshot as title", "expires_on", "status"],
			order_by="expires_on asc",
		):
			out.append({"title": row.title, "expires_on": str(row.expires_on), "kind": "Course"})

	if employee and employee.get("name") and frappe.db.exists("DocType", "Employee Credential"):
		for row in frappe.get_all(
			"Employee Credential",
			filters={"employee": employee.name, "status": ["in", ("Expiring", "Expired")]},
			fields=["credential_type", "expires_on", "status"],
			order_by="expires_on asc",
		):
			out.append(
				{
					"title": row.credential_type,
					"expires_on": str(row.expires_on or ""),
					"kind": "Credential",
				}
			)
	return sorted(out, key=lambda r: r["expires_on"] or "9999")


def _devices(employee):
	"""Kit signed out to this person. Self only, and best-effort."""
	if not employee or not employee.get("name"):
		return []
	if not frappe.db.exists("DocType", "Managed Device"):
		return []
	try:
		return frappe.get_all(
			"Managed Device",
			filters={"employee": employee.name},
			fields=["name", "device_name", "model"],
			order_by="device_name asc",
		)
	except Exception:
		# The device module's field names are its own business and may move.
		# A profile must not fail to render because a panel could not be filled.
		return []


def directory(viewer):
	"""Everybody the viewer may browse. Staff only, both ends.

	Ordered by name rather than by points: this is a directory, and opening it on
	a ranking would make it a leaderboard, which is a different thing with
	different consequences in a sixteen-person company.
	"""
	if not _is_staff(viewer):
		return []
	rows = frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"]},
		fields=["user_id as user", "employee_name as full_name", "designation", "department"],
		order_by="employee_name asc",
	)
	for row in rows:
		row["badge_count"] = (
			frappe.db.count("Training Badge Award", {"user": row.user})
			if frappe.db.exists("DocType", "Training Badge Award")
			else 0
		)
	return rows
