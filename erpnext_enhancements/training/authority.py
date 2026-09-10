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
	if not frappe.db.has_column("tabEmployee", "custom_position"):
		# The custom field has not migrated yet. Tier authority is simply
		# unavailable until it does; the other bases still work, so a site
		# part-way through this release can still record sign-offs.
		return None
	return frappe.db.get_value("Employee", {"user_id": user}, "custom_position")


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
	"""Stamp the two positions and the basis onto a sign-off being submitted.

	A link resolves to *today*; an attestation is about what was true when it was
	made. The completion record already works this way — it snapshots the course
	title, the version number and the content hash — and for the same reason: the
	question an audit asks in 2029 is "on what basis did this person attest?", and
	"they were a Senior Technician then" is not recoverable from a Position link
	that has since been retitled or a tier that has since been renumbered.
	"""
	basis = authority_basis(doc, user)
	values = {
		"authority_basis": basis or "",
		"supervisor_position": _position_of_user(user) or "",
		"learner_position": _position_of_user(doc.get("user")) or "",
	}
	for field, value in values.items():
		if doc.meta.has_field(field):
			doc.set(field, value)
	return basis


def tier_of_user(user):
	"""The tier integer behind this user's position. Display and eligibility only."""
	name = _position_of_user(user)
	if not name:
		return 0
	return cint(frappe.db.get_value("Position", name, "tier"))
