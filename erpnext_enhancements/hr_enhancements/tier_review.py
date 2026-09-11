# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Opening, routing and deciding a tier review.

Every transition lives here, and the controller refuses any status or decision
change that did not come through one of these functions. That is not belt and
braces: `Employee` holds write on `Tier Review` — it has to, somebody fills in
their own self-assessment — and a promotion is a permission grant, so an editable
`decision` field would let any of the sixteen authorise their own.

**Reachability is checked, not assumed.** This app has shipped three correct
server sides with nothing calling them: `record_signoff` (v1.334.0), the visual
editor's entry point, and the entire time-off approval flow. Every endpoint below
has a caller in `tier_review.js`, and a test fails the build if one stops having
one.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_enhancements.hr_enhancements import progression
from erpnext_enhancements.hr_enhancements.doctype.tier_review.tier_review import (
	CANCELED,
	DECIDED,
	DRAFT,
	NOT_YET,
	PROMOTE,
	WITH_REVIEWER,
)

DOCTYPE = "Tier Review"

#: Who may decide a review without being the named reviewer. Same set as the
#: time-off decider and the sign-off delegate, and named here rather than
#: imported so the three can diverge if they ever should.
DECIDER_ROLES = {"HR Manager", "System Manager"}

#: What the snapshot records as having been on file when the review opened.
#: Copied from `progression`, not re-spelled, so the two cannot drift.
HELD = progression.HELD


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def open_review(employee, to_position=None):
	"""Start a review for *employee* against the rung above their own.

	Refuses when the target rung lists no requirements. That is the guardrail, not
	a convenience: a review with no lines is an empty checklist that everybody
	signs, and the promotion it authorises would have a paper trail proving
	nothing. Better to say "nobody has written down what a Senior Technician must
	hold" than to produce a document that looks like diligence.
	"""
	me = _me()
	user = frappe.db.get_value("Employee", employee, "user_id")
	if not user:
		frappe.throw(_("That employee has no login, so there is nothing to review against."))

	# Who may START one. The form button is drawn for HR only, but a client-side
	# check decides what to DRAW and never what is allowed -- without this, any of
	# the sixteen could POST a review into existence for anybody, and a review
	# document naming somebody's gaps is not a neutral thing to be able to create.
	#
	# Three answers, and the third matters on this site: yourself, HR, or somebody
	# who already outranks them on that ladder. The last one is how a Senior
	# Technician starts the conversation about a Junior without going through HR --
	# which, with four Juniors and one Senior, is the conversation that has to
	# happen for the ladder to move at all.
	if not (
		me == user
		or (DECIDER_ROLES & set(frappe.get_roles(me)))
		or me in set(progression.eligible_reviewers(user))
	):
		frappe.throw(
			_("Only {0}, somebody senior to them on that ladder, or HR can open this.").format(
				frappe.db.get_value("Employee", employee, "employee_name") or employee
			),
			frappe.PermissionError,
		)

	here = progression.position_of(user)
	if not here:
		frappe.throw(_("Put them on the Position ladder first — there is no rung to review from."))

	target = to_position or progression.next_rung(here)
	if not target:
		frappe.throw(
			_("There is no rung above {0} on that ladder, so there is nothing to review for.").format(
				here
			)
		)

	readiness = progression.readiness(user, target)
	if not readiness.get("configured"):
		frappe.throw(
			_("Nobody has written down what {0} asks for. Fill in <b>What this rung asks for</b> "
			  "on that Position first — a review with no requirements is a form, not evidence.").format(
				target
			)
		)

	existing = frappe.db.exists(
		DOCTYPE,
		{"employee": employee, "to_position": target, "status": ["in", (DRAFT, WITH_REVIEWER)]},
	)
	if existing:
		frappe.throw(
			_("There is already an open review for {0} against {1}.").format(employee, target)
		)

	doc = frappe.new_doc(DOCTYPE)
	doc.employee = employee
	doc.from_position = here
	doc.to_position = target
	doc.status = DRAFT
	for line in readiness["lines"]:
		doc.append(
			"lines",
			{
				"requirement_kind": line["kind"],
				"requirement_label": line["label"],
				# The snapshot, taken once. `line["state"]` is what the system could
				# see at this moment; it is never recomputed, so the review keeps
				# saying what it was measured against.
				"held_at_open": line["state"],
			},
		)
	doc.flags.tier_transition = True
	doc.insert(ignore_permissions=True)

	_notify(
		user,
		_("A review for {0}").format(target),
		_("{0} opened a tier review for you against <b>{1}</b>. Fill in your own account of "
		  "where you are, then send it to your reviewer.").format(
			frappe.db.get_value("User", me, "full_name") or me, target
		),
		doc,
	)
	return {"name": doc.name, "lines": len(doc.lines)}


@frappe.whitelist()
def send_to_reviewer(review, reviewer):
	"""The person's own action: "this is where I think I am."

	The reviewer must be somebody whose Position outranks theirs on the same
	ladder — the same predicate as sign-off authority, read from the same place, so
	the two cannot come to disagree about who has standing.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, review)
	if doc.user != me and not (DECIDER_ROLES & set(frappe.get_roles(me))):
		frappe.throw(_("That is not your review."), frappe.PermissionError)
	if doc.status != DRAFT:
		frappe.throw(_("That review has already been sent."))

	reviewer_user = frappe.db.get_value("Employee", reviewer, "user_id")
	if not reviewer_user:
		frappe.throw(_("That reviewer has no login."))
	if reviewer_user == doc.user:
		frappe.throw(_("Somebody else has to review this."), frappe.PermissionError)

	eligible = set(progression.eligible_reviewers(doc.user))
	if reviewer_user not in eligible:
		# Deliberately not softened to a warning. The list being empty is a real
		# and important state on this site -- four Junior Technicians share one
		# eligible reviewer -- and quietly accepting anybody would hide it.
		frappe.throw(
			_("{0} does not outrank {1} on that ladder, so they cannot review it.").format(
				reviewer_user, doc.from_position or _("this position")
			),
			frappe.PermissionError,
		)

	doc.reviewer = reviewer
	doc.reviewer_user = reviewer_user
	doc.status = WITH_REVIEWER
	doc.self_submitted_on = now_datetime()
	doc.flags.tier_transition = True
	doc.save(ignore_permissions=True)

	_notify(
		reviewer_user,
		_("Tier review: {0}").format(doc.employee_name or doc.employee),
		_("{0} has asked you to review them for <b>{1}</b>.").format(
			doc.employee_name or doc.employee, doc.to_position
		),
		doc,
	)
	return {"status": doc.status}


@frappe.whitelist()
def decide(review, decision, note=None):
	"""Promote, or not yet. The reviewer's action, or a stand-in's.

	`Not yet` is a real answer and it is required to carry a reason — a refusal
	with no reason leaves somebody nothing to work on, the same rule as a declined
	time-off request and a non-competent sign-off.
	"""
	me = _me()
	if decision not in (PROMOTE, NOT_YET):
		frappe.throw(_("A decision is either {0} or {1}.").format(PROMOTE, NOT_YET))

	doc = frappe.get_doc(DOCTYPE, review)
	if doc.user == me:
		frappe.throw(_("You cannot decide your own review."), frappe.PermissionError)
	if doc.reviewer_user != me and not (DECIDER_ROLES & set(frappe.get_roles(me))):
		frappe.throw(_("Only the reviewer or HR can decide this."), frappe.PermissionError)
	if doc.status != WITH_REVIEWER:
		frappe.throw(_("That review is {0}, so there is nothing to decide.").format(doc.status))

	note = (note or "").strip()
	if decision == NOT_YET and not note:
		frappe.throw(_("Say what is still missing. A “not yet” with no reason is not usable."))

	doc.decision = decision
	doc.decision_note = note or None
	doc.decided_by = me
	doc.decided_on = now_datetime()
	doc.reviewed_on = now_datetime()
	doc.status = DECIDED
	doc.flags.tier_transition = True
	doc.save(ignore_permissions=True)

	_notify(
		doc.user,
		_("Your tier review: {0}").format(decision),
		_("Your review for <b>{0}</b> was decided: <b>{1}</b>.{2}").format(
			doc.to_position, decision, f"<p>{frappe.utils.escape_html(note)}</p>" if note else ""
		),
		doc,
	)
	return {"status": doc.status, "decision": doc.decision}


@frappe.whitelist()
def apply_promotion(review):
	"""Actually move them. A separate, deliberate act.

	WI-072 decision 6 — the system proposes, a human promotes — and here that is
	more than a slogan: the position carries sign-off authority over other people,
	so the moment of granting it should be somebody pressing a button, not a side
	effect of saving a form.

	Written through `set_value` on the document rather than `db.set_value`, so core
	`Version` records the change and the audit trail is the framework's. Also
	writes the fetched tier, which `db.set_value` would not populate.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, review)
	if not (DECIDER_ROLES & set(frappe.get_roles(me))) and doc.reviewer_user != me:
		frappe.throw(_("Only the reviewer or HR can apply this."), frappe.PermissionError)
	if doc.decision != PROMOTE:
		frappe.throw(_("That review did not decide to promote."))
	if doc.applied_on:
		frappe.throw(_("That promotion has already been applied."))
	if doc.user == me:
		frappe.throw(_("You cannot promote yourself."), frappe.PermissionError)

	employee = frappe.get_doc("Employee", doc.employee)
	employee.custom_position = doc.to_position
	tier = frappe.db.get_value("Position", doc.to_position, "tier") or 0
	if employee.meta.get_field("custom_position_tier"):
		employee.custom_position_tier = tier
	employee.save(ignore_permissions=True)

	doc.applied_on = now_datetime()
	doc.flags.tier_transition = True
	doc.save(ignore_permissions=True)

	_notify(
		doc.user,
		_("You are now {0}").format(doc.to_position),
		_("Your position is now <b>{0}</b>.").format(doc.to_position),
		doc,
	)
	return {"applied_on": str(doc.applied_on), "position": doc.to_position}


@frappe.whitelist()
def cancel_review(review, note=None):
	"""Withdraw it. Either side, any time before it is applied."""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, review)
	if doc.user != me and doc.reviewer_user != me and not (
		DECIDER_ROLES & set(frappe.get_roles(me))
	):
		frappe.throw(_("That is not your review."), frappe.PermissionError)
	if doc.applied_on:
		frappe.throw(_("That promotion has already been applied; cancelling the review now would "
		               "say something untrue about a position change that happened."))
	if doc.status == CANCELED:
		frappe.throw(_("That review is already canceled."))

	doc.status = CANCELED
	doc.decision_note = (note or "").strip() or doc.decision_note
	doc.flags.tier_transition = True
	doc.save(ignore_permissions=True)
	return {"status": doc.status}


@frappe.whitelist()
def reviewers_for(employee):
	"""Who may review this person. Drives the picker, and tells the truth when the
	answer is nobody.

	On prod today a Junior Technician has exactly one eligible reviewer, and an
	empty or one-name list is the single most useful thing this feature can show
	somebody — it is the fact the whole deliverable exists to surface.
	"""
	user = frappe.db.get_value("Employee", employee, "user_id")
	if not user:
		return []
	users = progression.eligible_reviewers(user)
	if not users:
		return []
	return frappe.get_all(
		"Employee",
		filters={"user_id": ["in", users], "status": "Active"},
		fields=["name", "employee_name", "custom_position"],
	)


def _notify(user, subject, body_html, doc):
	"""One email, best effort. A review must not fail to record because mail did.

	Routed through training's mailer, exactly as `hr_enhancements/timeoff.py`
	does, rather than calling `frappe.sendmail` here. That is not tidiness: the
	shared sender wraps the body in `templates/emails/_shell.html`, and a bare
	`sendmail` would ship an unstyled, container-less message that looks nothing
	like the rest of the app's mail. `tests/test_email_design.py` fails the build
	on a raw `sendmail` for that reason, and it caught this on the first run.

	It also already resolves an Employee's preferred address, honours the
	notification switch, and writes a Notification Log entry beside the email.

	Never raises: a decision that recorded but failed to email is a decision that
	recorded.
	"""
	if not user:
		return False
	try:
		from erpnext_enhancements.training import notifications

		if not notifications._enabled():
			return False
		recipient = notifications._recipient(user)
		if not recipient:
			return False
		link = frappe.utils.get_url_to_form(DOCTYPE, doc.name)
		body = f"{body_html}<p><a href='{link}'>{_('Open the review')}</a></p>"
		return bool(notifications._send(recipient, subject, body))
	except Exception:
		frappe.log_error(
			f"Tier review {doc.name}: could not notify {user}\n{frappe.get_traceback()}",
			"Tier review",
		)
		return False
