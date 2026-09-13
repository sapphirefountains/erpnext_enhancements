# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who may sign whom — one predicate, read from every path that needs it.

Before this module there was one rule, in one place: ``signoff._assert_may_sign``.
That was enough while authority meant "the named supervisor, or a Training
Manager", because both are properties of the document. It stops being enough the
moment authority is a property of the *organisation*, and it stops being enough
in a way that is easy to miss.

**``record_signoff`` is not the only way a sign-off gets submitted.** It sets
``ignore_permissions = True`` and calls ``submit()``; the Desk form's own Submit
button calls ``submit()`` directly and never touches the endpoint at all. So a
tier rule that lives only in the endpoint is not a rule — it is a suggestion that
one of the two doors happens to make. That is exactly the shape of the defect
this module's neighbours keep hitting: a check that exists, is correct, and sits
on a path nobody uses.

So the predicate lives here and is read from **five** places:

1. ``signoff._assert_may_sign`` — the endpoint, which throws.
2. ``TrainingSignoff.before_submit`` — the Desk door, which also throws. This one
   is load-bearing; without it the other four are decoration.
3. ``permissions.signoff_query_conditions`` — list views.
4. ``permissions.signoff_has_permission`` — ``frappe.get_doc`` of one row.
5. ``signoff.get_signoff_queue`` — the supervisor's worklist.

The four bases, in the order they are tried
-------------------------------------------

``None`` first, always: **the learner never signs their own competence**,
whatever roles or position they hold. A self-attestation is not a weaker
attestation, it is no attestation at all.

* ``Observed Supervisor`` — the ``supervisor_user`` named on the document. The
  request froze this at routing time on purpose (see ``signoff.resolve_supervisor``):
  a reporting-line change between the request and the sign-off must not silently
  move who is allowed to sign.
* ``Position Tier`` — the caller's ``Position`` strictly outranks the learner's,
  **inside the same job family**. Never a same-tier peer. This is the rule Nik
  asked for, and it exists because on this site the reporting tree cannot express
  it: the one Senior Technician has zero direct reports, and the four Junior
  Technicians he would be attesting for all report to the Project Manager.
* ``Manager Delegate`` — Training Manager, System Manager or HR Manager. Sign-offs
  really are relayed over the radio and typed up afterwards, and the audit value
  sits in the *named supervisor* on the document rather than in which login
  pressed submit.

Tier eligibility is evaluated **live**, against positions as they are now, while
the addressee stays frozen. Both are then snapshotted onto the submitted document,
matching the completion's own doctrine: what mattered is what was true when the
attestation was made, and a link resolves to today.

Two identities, and conflating them misnames a person on an insurer document
-----------------------------------------------------------------------------

A submitted sign-off carries **two** people, and only under ``Observed Supervisor``
are they the same one:

* the **named supervisor** — ``supervisor`` / ``supervisor_user`` on the document.
  This is the attestation. The paragraph above says so in as many words: the audit
  value sits in the named supervisor rather than in which login pressed submit.
* the **recorder** — ``frappe.session.user``, the login that pressed submit.

Until v1.424.0 :func:`snapshot_positions` froze ``supervisor_name_at_time`` and both
supervisor rung fields from the *recorder*, so under ``Manager Delegate`` — the
relayed-over-the-radio workflow the delegate arm exists for — the frozen name was
the typist, and ``crew_qualification_roster`` printed *Attested by <typist>* on a
sheet handed to an insurer. Nothing on the row disagreed, because every supervisor
field agreed with every other one: they were all the wrong person.

They are now frozen separately, and ``authority_basis`` is what says which of the
two did the attesting. **Swapping the two is not a fix** — under ``Position Tier``
the recorder is signing on their own rung and the named supervisor is only the
address the request was routed to, which is the exact inverse of the delegate case.
So both are recorded, always, and the reader picks by basis.

Fails closed everywhere. No Employee record, no Position, an unknown ladder, a
retired rung — all of them return ``None``, and ``None`` means refused.
"""

import frappe
from frappe.utils import cint

from erpnext_enhancements.hr_enhancements.doctype.position.position import (
	outranks,
	positions_outranked_by,
)

#: Roles that may attest for anybody. HR Manager joined in v1.386.0; see
#: ``signoff.MANAGER_ROLES``, which is the same set and the one this reads.
OBSERVED = "Observed Supervisor"
TIER = "Position Tier"
DELEGATE = "Manager Delegate"


def _position_of_user(user):
	"""The Position on this user's Employee record, or ``None``.

	Read through Employee rather than User because Position is an *employment*
	fact. A customer contact has a User and no Employee, and must never acquire
	authority over anybody — this returning ``None`` for them is the mechanism,
	not an accident.
	"""
	if not user:
		return None
	if not frappe.db.has_column("Employee", "custom_position"):
		# The custom field has not migrated yet. Tier authority is simply
		# unavailable until it does; the other bases still work, so a site
		# part-way through this release can still record sign-offs.
		return None
	return frappe.db.get_value("Employee", {"user_id": user}, "custom_position")


def _position_of_employee(employee):
	"""The Position on an Employee record, reached by employee name rather than login.

	The named supervisor on a sign-off is an **Employee** link, and an Employee does
	not have to have a login. ``Training Signoff.supervisor`` is mandatory;
	``supervisor_user`` is derived from it and comes back empty for an Employee with
	no ``user_id``. So the rung of the person a document names has to be reachable
	without a login, which this is and :func:`_position_of_user` is not — and it
	costs one indexed read instead of two.
	"""
	if not employee:
		return None
	if not frappe.db.has_column("Employee", "custom_position"):
		# Same reasoning as `_position_of_user`: the custom field has not migrated
		# yet, so no rung is recorded rather than the submit failing.
		return None
	return frappe.db.get_value("Employee", employee, "custom_position")


def _full_name(user):
	"""Display name for a login, falling back to the login itself.

	Never blank for a user that exists, because a blank in a frozen audit field
	reads as "nobody recorded this" rather than "the User row has no full_name".
	"""
	if not user:
		return ""
	return frappe.db.get_value("User", user, "full_name") or user


def _attester_name(doc):
	"""The named supervisor, frozen as text.

	``Employee.employee_name`` first: ``supervisor`` is the mandatory field and an
	Employee always carries a name, while ``supervisor_user`` is derived and can be
	empty. Falls through to the login and then to the Employee id, so a document that
	names somebody never freezes a blank.
	"""
	employee = doc.get("supervisor")
	name = frappe.db.get_value("Employee", employee, "employee_name") if employee else None
	return name or _full_name(doc.get("supervisor_user")) or employee or ""


def authority_basis(doc, user):
	"""Why *user* may sign *doc*, or ``None`` if they may not.

	Returns the basis rather than a bool so the caller can say *why* — the refusal
	message, the audit snapshot and the queue all want different things from the
	same decision, and computing it three times is how they come to disagree.
	"""
	learner = doc.get("user") if hasattr(doc, "get") else getattr(doc, "user", None)
	if not user or not learner:
		return None
	if learner == user:
		return None

	supervisor_user = doc.get("supervisor_user") if hasattr(doc, "get") else None
	if supervisor_user and supervisor_user == user:
		return OBSERVED

	mine = _position_of_user(user)
	theirs = _position_of_user(learner)
	if mine and theirs and outranks(mine, theirs):
		return TIER

	from erpnext_enhancements.training.signoff import MANAGER_ROLES

	if MANAGER_ROLES & set(frappe.get_roles(user)):
		return DELEGATE

	return None


def may_sign(doc, user):
	"""Bool form of :func:`authority_basis`, for callers that only need the gate."""
	return authority_basis(doc, user) is not None


def signable_learner_users(user):
	"""Logins whose sign-offs *user* may act on by **position tier alone**.

	The IN-list form, for the row-level filters and the queue. It answers a
	narrower question than :func:`authority_basis` on purpose: the observed-supervisor
	and manager-delegate arms are already expressed elsewhere in those callers
	(``supervisor_user = me``, and managers being unscoped), so folding them in here
	would double-count and make each filter harder to read than the rule it
	implements.

	Empty for almost everybody, which is the common case and costs two indexed
	reads.
	"""
	mine = _position_of_user(user)
	if not mine:
		return []
	beneath = positions_outranked_by(mine)
	if not beneath:
		return []
	return [
		row
		for row in frappe.get_all(
			"Employee",
			filters={"custom_position": ["in", beneath], "status": "Active"},
			pluck="user_id",
		)
		if row and row != user
	]


def snapshot_positions(doc, user):
	"""Stamp both identities, both rungs and the basis onto a sign-off being submitted.

	A link resolves to *today*; an attestation is about what was true when it was
	made. The completion record already works this way — it snapshots the course
	title, the version number and the content hash — and for the same reason: the
	question an audit asks in 2029 is "on what basis did this person attest?", and
	"they were a Senior Technician then" is not recoverable from a Position link
	that has since been retitled or a tier that has since been renumbered.

	``user`` is **the login recording the sign-off**, not the attester. The
	``supervisor_*`` fields come off the document and the ``recorded_by_*`` fields
	come off ``user``; the module docstring has the reasoning and the defect this
	separation fixes.

	Rows submitted before v1.424.0 carry no ``recorded_by_*`` at all, and their
	``supervisor_*`` fields hold whatever the *recorder* was. That is the same value
	under ``Observed Supervisor``, which is what every submitted row on this site
	actually is, so nothing needs correcting — but a reader must not assume a blank
	``recorded_by_at_time`` means "the same person".
	"""
	basis = authority_basis(doc, user)

	# The attestation half, read off the DOCUMENT. `user` is the login pressing
	# submit and is deliberately not consulted here -- see the module docstring.
	supervisor_position = (
		# The second arm is NOT dead, and an audit on 2026-09-13 claimed it was. Arm 1
		# returns falsy whenever the Employee row exists but its `custom_position` is
		# blank -- which is most of them, the field being days old (WI-072) -- so control
		# reaches arm 2 on the ordinary path, constantly. It usually returns the same
		# blank, because `supervisor_user` is derived from the row arm 1 just read.
		#
		# "Usually" is not "always": ERPNext's `validate_duplicate_user_id` only forbids
		# a shared `user_id` among **Active** Employees, so a Left row may hold the same
		# login as an Active one and the two arms can resolve differently. Keep both.
		_position_of_employee(doc.get("supervisor")) or _position_of_user(doc.get("supervisor_user")) or ""
	)
	learner_position = _position_of_user(doc.get("user")) or ""
	# The recording half, read off the SESSION.
	recorder_position = _position_of_user(user) or ""

	sup_title, sup_tier = _position_facts(supervisor_position)
	lrn_title, lrn_tier = _position_facts(learner_position)
	rec_title, rec_tier = _position_facts(recorder_position)

	values = {
		"authority_basis": basis or "",
		"supervisor_position": supervisor_position,
		"learner_position": learner_position,
		# The Links above resolve to TODAY, which is what the docstring says must not
		# happen. These four are the frozen copies: rename a rung or renumber a tier
		# and every historical attestation would otherwise silently restate itself.
		# `Safety Incident.job_title_at_time` is the precedent in this app.
		"supervisor_name_at_time": _attester_name(doc),
		"supervisor_position_title": sup_title,
		"supervisor_tier_at_time": sup_tier,
		"learner_position_title": lrn_title,
		"learner_tier_at_time": lrn_tier,
		# Who typed it up. A separate fact, in separate fields, always recorded even
		# when it is the same person -- a blank would be read as "nobody", and under
		# `Position Tier` the recorder rung is the thing that granted the authority,
		# so leaving it out would put a `Position Tier` basis next to two tiers that
		# demonstrate no rank difference at all.
		"recorded_by_at_time": _full_name(user),
		"recorded_by_position_title": rec_title,
		"recorded_by_tier_at_time": rec_tier,
	}
	for field, value in values.items():
		if doc.meta.has_field(field):
			doc.set(field, value)
	return basis


def _position_facts(position):
	"""``(title, tier)`` for a Position name, frozen at the moment of the call.

	Returns ``("", 0)`` for an unset or missing position rather than raising: a
	sign-off from somebody who is not on the ladder is a real case (the HR Manager
	blanket authority), and it must record "no rung" rather than abort the submit.
	"""
	if not position:
		return "", 0
	row = frappe.db.get_value("Position", position, ["position_name", "tier"], as_dict=True)
	if not row:
		return "", 0
	return row.get("position_name") or position, cint(row.get("tier"))


def tier_of_user(user):
	"""The tier integer behind this user's position. Display and eligibility only."""
	name = _position_of_user(user)
	if not name:
		return 0
	return cint(frappe.db.get_value("Position", name, "tier"))
