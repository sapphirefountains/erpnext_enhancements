# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What this rung asks for, and how close somebody is to the next one.

WI-072 built a ladder whose tier **is** the sign-off authority, and no route up
it. On prod that is not an HR nicety: there are four Junior Technicians and one
Senior, so one person is the only one in the company who can attest for any of
them. If nobody is ever promoted, the gate rests on one man's availability.

So this answers two questions, from live records rather than from anything stored:

* **"what am I short of for the job I do now?"** — because a rung's requirements
  are also what makes somebody's Skills Matrix row mean anything;
* **"what stands between me and the next rung?"** — which is the promotion
  conversation, and today happens from memory.

**Nothing here is cached or stamped.** Held-or-not is recomputed against
`Training Completion`, `Employee Credential` and `Training Signoff` every time it
is asked. A stored "85% ready" would be wrong the day after a credential lapsed,
and readiness that is wrong in the optimistic direction is exactly the number
somebody would make a decision on.

**The empty case refuses rather than renders.** A rung with no requirements
returns `configured = False`, and every caller is expected to say so out loud
instead of drawing an empty checklist. This release has been bitten three times by
a check that reports clean because it was looking at nothing — the dispatch
advisory that could never fire, the whitespace queries that passed vacuously, the
backfill that recorded success having written nothing. A blank promotion checklist
that everyone signs is the same bug wearing a different hat.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, today

COURSE = "Training Course"
CREDENTIAL = "Credential"
SIGNOFF = "Sign-off"

HELD = "Held"
EXPIRING = "Expiring"
MISSING = "Missing"

#: Matches the Skills Matrix, deliberately. Two forward views that disagree about
#: what "soon" means would be worse than either alone.
HORIZON_DAYS = 90


# --------------------------------------------------------------------- reading


def requirements_for(position):
	"""The rows on *position*, or an empty list. Never raises on a missing rung."""
	if not position or not frappe.db.exists("Position", position):
		return []
	doc = frappe.get_cached_doc("Position", position)
	return list(doc.get("requirements") or [])


def next_rung(position):
	"""The rung immediately above *position* on the same ladder, or None.

	"Immediately above" is the lowest tier strictly greater than this one within
	the same ``job_family`` — not simply tier+1, because a ladder is allowed to
	skip numbers and a family is allowed to have gaps. Groups are excluded: a
	group is a container, not somewhere a person stands.
	"""
	if not position:
		return None
	mine = frappe.db.get_value(
		"Position", position, ["job_family", "tier", "is_active", "is_group"], as_dict=True
	)
	if not mine or not mine.job_family or cint(mine.is_group):
		return None

	above = frappe.get_all(
		"Position",
		filters={
			"job_family": mine.job_family,
			"tier": [">", cint(mine.tier)],
			"is_active": 1,
			"is_group": 0,
		},
		fields=["name", "tier"],
		order_by="tier asc",
		limit=1,
	)
	return above[0].name if above else None


def readiness(user, position=None):
	"""How *user* stands against *position* — their own rung by default.

	Returns a dict with ``configured``, ``position``, ``lines`` and counts. When
	``configured`` is False the caller must say "nobody has written down what this
	rung asks for" rather than draw an empty list; see the module docstring.
	"""
	position = position or position_of(user)
	rows = requirements_for(position)
	if not rows:
		return {
			"configured": False,
			"position": position,
			"lines": [],
			"held": 0,
			"total": 0,
			"missing": 0,
		}

	lines = [_evaluate(user, row) for row in rows]
	held = sum(1 for line in lines if line["state"] == HELD)
	return {
		"configured": True,
		"position": position,
		"position_label": frappe.db.get_value("Position", position, "position_name") or position,
		"lines": lines,
		"held": held,
		"total": len(lines),
		"missing": sum(1 for line in lines if line["state"] == MISSING and line["mandatory"]),
	}


def route_to_next(user):
	"""Readiness against the rung above, plus which rung that is.

	The shape the profile panel draws: "you are here, that is next, this is what is
	between them". ``next_position`` is None at the top of a ladder and on a
	one-rung family, and that is not an error — most Designations here are their
	own single rung by construction.
	"""
	here = position_of(user)
	nxt = next_rung(here)
	if not nxt:
		return {"position": here, "next_position": None, "readiness": None}
	return {"position": here, "next_position": nxt, "readiness": readiness(user, nxt)}


def position_of(user):
	"""The Position on this person's Employee record, or None.

	Guarded, because ``custom_position`` is a fixture Custom Field and there is a
	real window during a migrate where the DocType exists and the column does not.
	``has_column`` takes a DOCTYPE and **raises** on an unknown table rather than
	returning False, so the try is doing real work here.
	"""
	if not user:
		return None
	try:
		if not frappe.db.has_column("Employee", "custom_position"):
			return None
	except Exception:
		return None
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "custom_position")


# ------------------------------------------------------------------ per-row


def _evaluate(user, row):
	"""One requirement, against live records."""
	if row.requirement_type == CREDENTIAL:
		return _credential_line(user, row)
	if row.requirement_type == SIGNOFF:
		return _signoff_line(user, row)
	return _course_line(user, row)


def _course_line(user, row):
	title = frappe.db.get_value(COURSE, row.training_course, "course_title") or row.training_course
	completion = frappe.db.get_value(
		"Training Completion",
		{"user": user, "course": row.training_course, "docstatus": 1, "status": "Valid"},
		["name", "expires_on"],
		as_dict=True,
	)
	state = MISSING
	detail = _("Not completed")
	if completion:
		state, detail = _date_state(completion.expires_on, _("Completed"))
	return _line(row, _("Course"), title, state, detail)


def _credential_line(user, row):
	held = frappe.db.get_value(
		"Employee Credential",
		{"user": user, "credential_type": row.credential_type, "status": ["in", ("Valid", "Expiring")]},
		["name", "expires_on"],
		as_dict=True,
	)
	state = MISSING
	detail = _("Not on file")
	if held:
		state, detail = _date_state(held.expires_on, _("On file"))
	return _line(row, _("Credential"), row.credential_type, state, detail)


def _signoff_line(user, row):
	"""Signed off as competent on a course, by somebody who was allowed to.

	Reads the record rather than re-deriving authority: `Training Signoff` already
	refuses a sign-off from somebody without standing, on submit, so a submitted
	Competent row IS the attestation. Re-checking the signer's authority here would
	mean a supervisor who has since changed position retroactively un-signs
	everybody they ever signed, which is not what an attestation means.
	"""
	title = frappe.db.get_value(COURSE, row.training_course, "course_title") or row.training_course
	signoff = frappe.db.get_value(
		"Training Signoff",
		{"user": user, "course": row.training_course, "docstatus": 1, "outcome": "Competent"},
		["name", "signed_on"],
		as_dict=True,
	)
	if not signoff:
		return _line(row, _("Sign-off"), title, MISSING, _("Not signed off"))
	return _line(row, _("Sign-off"), title, HELD, _("Signed off"))


def _date_state(expires_on, verb):
	"""Held / Expiring / Missing from an expiry date.

	`Expiring` is still **held** for readiness — somebody inside the horizon is
	qualified today, and reading it as a gap would make the horizon do the opposite
	of its job. Same wording and the same 90 days as the Skills Matrix, on purpose.
	"""
	if not expires_on:
		return HELD, verb
	due = getdate(expires_on)
	now = getdate(today())
	if due < now:
		return MISSING, _("Lapsed {0}").format(due)
	if (due - now).days <= HORIZON_DAYS:
		return EXPIRING, _("{0}, expires {1}").format(verb, due)
	return HELD, _("{0}, valid to {1}").format(verb, due)


def _line(row, kind, label, state, detail):
	return {
		"kind": kind,
		"label": label,
		"state": state,
		"detail": detail,
		"mandatory": bool(cint(row.is_mandatory)),
		"note": row.note or "",
	}


# ------------------------------------------------------------------ reviewers


def eligible_reviewers(user):
	"""Who may run a tier review for *user*.

	The same predicate as sign-off authority and read from the same place, so the
	two cannot drift: anybody whose Position outranks theirs on the same ladder.
	Returns users, not positions.

	Note what this does NOT do: it does not fall back to "any manager" when the
	list is empty. An empty list is the true answer and the useful one — on prod
	today a Junior Technician's only eligible reviewer is the single Senior, and a
	fallback would hide exactly the fact this whole deliverable exists to surface.
	"""
	mine = position_of(user)
	if not mine:
		return []
	# NOT `positions_outranked_by(mine)` -- that returns what mine outranks, and we
	# want the inverse. Same three clauses as `outranks`, same direction of failure
	# (an unknown ladder is not authority), read the same way: one indexed lookup
	# then one filtered list.
	row = frappe.db.get_value(
		"Position", mine, ["job_family", "tier", "is_active", "is_group"], as_dict=True
	)
	if not row or not row.job_family or not cint(row.is_active) or cint(row.is_group):
		return []
	above = frappe.get_all(
		"Position",
		filters={
			"job_family": row.job_family,
			"tier": [">", cint(row.tier)],
			"is_active": 1,
			"is_group": 0,
		},
		pluck="name",
	)
	if not above:
		return []
	try:
		if not frappe.db.has_column("Employee", "custom_position"):
			return []
	except Exception:
		return []
	return frappe.get_all(
		"Employee",
		filters={"custom_position": ["in", above], "status": "Active", "user_id": ["is", "set"]},
		pluck="user_id",
	)
