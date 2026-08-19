# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supervisor sign-off — routing the request, and recording the verdict.

Some things are not knowledge. "Can drain and refill a basin unsupervised" is not
a question with four options; somebody has to watch it happen. A course with
``require_supervisor_signoff`` therefore does not complete on a passing score
alone — it completes when a named supervisor has submitted a ``Training Signoff``
saying they saw the learner do the thing.

This module is the *routing and endpoint* half of that. The attestation's own
rules — supervisor_user re-derived from the Employee record, no self-sign-off, no
"Needs More Practice" without a note, no learner submitting — live on
``TrainingSignoff`` where they hold whoever writes the document, Desk included.
Nothing here re-implements them.

What is decided here:

**Routing always resolves to somebody.** ``Reports To`` is the usual source and is
empty far more often than anyone expects — new starters, people whose manager has
left, anybody whose Employee record was created in a hurry. An unroutable request
is worse than an imprecise one: it sits there, the assignment stays in Awaiting
Sign-off, and nobody finds out until the course is overdue. So every source falls
back to a Training Manager who has an Employee record, and the resolved supervisor
is *stored on the draft* rather than recomputed at sign time — otherwise a
reporting-line change between the request and the sign-off would silently move who
is allowed to sign.

**:func:`record_signoff` refuses the learner outright, whatever roles they hold.**
The controller is deliberately more permissive: it lets a Training Manager submit
a sign-off naming somebody else as supervisor, because sign-offs really do get
relayed over the radio and typed up afterwards. That path stays available in the
Desk, where the document plainly names who attested and the version history shows
who pressed submit. It is not available through a one-call endpoint, because
"Training Manager signs off own training" is exactly the line an auditor reads out.

**The completion gate is decided here and enforced on the controller.**
:func:`signoff_outstanding` is the one implementation of "this course wants a
sign-off and does not have one". ``Training Completion`` calls it from
``validate`` and ``before_submit``; ``api.training.finish_attempt`` calls it too,
to *report* the requirement to the learner rather than throw. One predicate, two
readings of it, so the gate and the screen explaining the gate cannot disagree.

That arrangement is younger than this module. Until v1.333.0 the docstring here
claimed the gate lived on ``Training Completion.validate`` and that
:func:`has_competent_signoff` was its one implementation. Neither was true:
``training_completion.py`` contained no sign-off logic at all, the only
enforcement was a second, independent copy inside ``api/training.py``, and
:func:`has_competent_signoff` was dead code. So a completion created by hand in
the Desk -- on a course requiring hands-on attestation -- was issued without one.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url

from erpnext_enhancements.training import notifications
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled
from erpnext_enhancements.training.doctype.training_signoff.training_signoff import (
	COMPETENT,
	NEEDS_PRACTICE,
)

SIGNOFF_DOCTYPE = "Training Signoff"
SIGNOFF_ROUTE = "training-signoff"

MANAGER_ROLES = {"Training Manager", "System Manager"}

OUTCOMES = (COMPETENT, NEEDS_PRACTICE)

# Assignment status while a request is outstanding. The option exists on Training
# Assignment already; naming it here keeps the two files from drifting silently.
AWAITING = "Awaiting Sign-off"

# Assignment states a sign-off request should not disturb.
CLOSED_ASSIGNMENT_STATUSES = ("Completed", "Cancelled", "Waived")


def _in_maintenance_context():
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _session_user():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


def _is_manager(user=None):
	return bool(MANAGER_ROLES & set(frappe.get_roles(user or frappe.session.user)))


# ------------------------------------------------------------------- routing


def _employee_of_user(user):
	return frappe.db.get_value("Employee", {"user_id": user}, "name") if user else None


def _enabled_user_of_employee(employee):
	user = frappe.db.get_value("Employee", employee, "user_id") if employee else None
	return user if user and frappe.db.get_value("User", user, "enabled") else None


def _fallback_supervisor(learner):
	"""A Training Manager who has an Employee record, excluding the learner.

	The Employee record is not optional: ``Training Signoff.supervisor`` is a Link
	to Employee and the controller re-derives ``supervisor_user`` from it, so a
	Training Manager who is only a login cannot be named as one.
	"""
	for user in frappe.get_all(
		"Has Role",
		filters={"role": "Training Manager", "parenttype": "User"},
		pluck="parent",
		distinct=True,
	):
		if user == learner or not frappe.db.get_value("User", user, "enabled"):
			continue
		employee = _employee_of_user(user)
		if employee:
			return employee
	return None


def resolve_supervisor(course, learner):
	"""``(employee, source)`` — who should sign this off, and why them.

	Returns ``(None, source)`` only when the site has no Training Manager with an
	Employee record at all, which is a configuration problem the caller should
	report rather than route around.

	The learner is excluded from every branch. Somebody who reports to themselves
	on paper — it happens, an owner-operator's Employee record — must not become
	their own supervisor by accident, and finding that out from the controller's
	self-sign-off refusal would be finding it out at the wrong moment.
	"""
	course_row = frappe.db.get_value(
		"Training Course",
		course,
		["signoff_supervisor_source", "signoff_supervisor"],
		as_dict=True,
	) or frappe._dict()
	source = course_row.get("signoff_supervisor_source") or "Reports To"
	learner_employee = _employee_of_user(learner)

	candidate = None
	if source == "Named Supervisor":
		candidate = course_row.get("signoff_supervisor")
	elif source == "Reports To":
		candidate = frappe.db.get_value("Employee", learner_employee, "reports_to") if learner_employee else None

	if candidate and candidate != learner_employee and _enabled_user_of_employee(candidate):
		return candidate, source

	# Fallback, and it is not a nicety — see the module docstring. An unroutable
	# request is the failure mode this function exists to avoid.
	return _fallback_supervisor(learner), "Any Training Manager"


# ------------------------------------------------------------------- request


@frappe.whitelist()
def request_signoff(course, user=None, assignment=None, attempt=None):
	"""Open a draft sign-off for a learner and tell the supervisor.

	A draft, not a submission: raising the request is the learner's action ("I'm
	ready"), and ``TrainingSignoff.before_submit`` is scoped precisely so that
	stays legal while the attestation itself does not.

	``user`` is honoured only for a Training Manager raising a request on somebody
	else's behalf; a learner asking for their own gets their session identity
	whatever they send.
	"""
	caller = _session_user()
	if _in_maintenance_context():
		return None
	if not is_enabled("training_enabled"):
		frappe.throw(_("Training is not available yet."))

	learner = user if (user and _is_manager(caller)) else caller

	course_row = frappe.db.get_value(
		"Training Course",
		course,
		["name", "course_title", "require_supervisor_signoff", "signoff_instructions", "current_version"],
		as_dict=True,
	)
	if not course_row:
		frappe.throw(_("No such course."))
	if not cint(course_row.require_supervisor_signoff):
		frappe.throw(_("{0} does not require a supervisor sign-off.").format(course_row.course_title))

	existing = frappe.db.exists(
		SIGNOFF_DOCTYPE, {"course": course, "user": learner, "docstatus": 0}
	)
	if existing:
		# Not an error. A learner pressing the button twice should be told the
		# request is already with somebody, not shown a failure.
		_mark_assignment_awaiting(assignment, course, learner)
		return {"signoff": existing, "created": False, "supervisor": None, "notified": False}

	supervisor, source = resolve_supervisor(course, learner)
	if not supervisor:
		frappe.throw(
			_("There is nobody who can sign this off: {0} has no supervisor, and no Training Manager on "
			  "this site has an Employee record to name as one.").format(learner)
		)

	doc = frappe.get_doc(
		{
			"doctype": SIGNOFF_DOCTYPE,
			"course": course,
			"course_version": course_row.current_version,
			"attempt": attempt,
			"user": learner,
			# supervisor_user is deliberately not set — the controller re-derives it
			# from this Employee, and that derivation is what the submit-time
			# identity check is worth anything against.
			"supervisor": supervisor,
		}
	)
	doc.insert(ignore_permissions=True)

	_mark_assignment_awaiting(assignment, course, learner)
	notified = _notify(
		doc.supervisor_user,
		_("Training sign-off needed: {0}").format(course_row.course_title or course),
		f"""
			<p>{frappe.utils.escape_html(frappe.db.get_value("User", learner, "full_name") or learner)}
			has finished <b>{frappe.utils.escape_html(course_row.course_title or course)}</b> and needs
			your sign-off before it counts as complete.</p>
			<p><b>What to verify:</b>
			{frappe.utils.escape_html(course_row.signoff_instructions or "—")}</p>
			<p><a href="{get_url(f'/app/{SIGNOFF_ROUTE}/{doc.name}')}">Record the sign-off</a></p>
		""",
	)

	return {
		"signoff": doc.name,
		"created": True,
		"supervisor": doc.supervisor_user,
		"supervisor_source": source,
		"notified": notified,
	}


def _mark_assignment_awaiting(assignment, course, learner):
	"""Park the assignment in Awaiting Sign-off so it stops reading as unstarted.

	Resolved from the course when the caller did not name one, because the player
	knows the course and the desk knows the assignment, and both reach this.
	"""
	name = assignment or frappe.db.get_value(
		"Training Assignment",
		{"course": course, "user": learner, "status": ["not in", CLOSED_ASSIGNMENT_STATUSES]},
		"name",
		order_by="creation desc",
	)
	if not name:
		return
	frappe.db.set_value("Training Assignment", name, "status", AWAITING, update_modified=False)


# -------------------------------------------------------------------- record


@frappe.whitelist()
def record_signoff(signoff, outcome, competency_notes=None, signature_image=None):
	"""Record a supervisor's verdict and submit it.

	Submitted because it is an attestation: once made it is evidence, and editing
	evidence is a cancel-and-reissue, not a save.
	"""
	caller = _session_user()
	if outcome not in OUTCOMES:
		frappe.throw(_("A sign-off is either {0} or {1}.").format(COMPETENT, NEEDS_PRACTICE))

	doc = frappe.get_doc(SIGNOFF_DOCTYPE, signoff)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("That sign-off has already been recorded."))

	_assert_may_sign(doc, caller)

	doc.outcome = outcome
	if competency_notes is not None:
		doc.competency_notes = (competency_notes or "").strip() or None
	if signature_image:
		doc.signature_image = signature_image

	# submit() writes as part of the transition, so there is no save() above it —
	# two writes would fire the controller's validate twice on one change.
	doc.flags.ignore_permissions = True
	doc.submit()

	notified = _notify(
		doc.user,
		_("Your training sign-off: {0}").format(outcome),
		f"""
			<p>{frappe.utils.escape_html(frappe.db.get_value("User", caller, "full_name") or caller)}
			has recorded your sign-off for
			<b>{frappe.utils.escape_html(frappe.db.get_value("Training Course", doc.course, "course_title") or doc.course or "")}</b>
			as <b>{frappe.utils.escape_html(outcome)}</b>.</p>
			<p>{frappe.utils.escape_html((doc.competency_notes or "").strip())}</p>
			<p><a href="{get_url('/training')}">Open your training</a></p>
		""",
	)

	return {"signoff": doc.name, "outcome": outcome, "notified": notified}


def _assert_may_sign(doc, caller):
	"""Never the learner. Then: the named supervisor, or a Training Manager.

	The learner check is first and unconditional, and it is stricter than
	``TrainingSignoff.before_submit`` on purpose — see the module docstring.
	"""
	if doc.user == caller:
		frappe.throw(
			_("You cannot sign off your own training. Ask your supervisor or a Training Manager."),
			frappe.PermissionError,
		)
	if doc.supervisor_user == caller or _is_manager(caller):
		return
	frappe.throw(
		_("Only {0} or a Training Manager can record this sign-off.").format(
			doc.supervisor_user or _("the named supervisor")
		),
		frappe.PermissionError,
	)


# -------------------------------------------------------------------- queries


def competent_signoff_name(course, learner_user):
	"""The submitted ``Competent`` sign-off backing this learner, if any.

	The single expression of what "signed off" means, because every caller wants
	the same four-clause filter and previously three of them wrote it out
	separately. ``docstatus 1`` rather than "not 2": a draft sign-off is a request,
	not an attestation, and a cancelled one is a withdrawal.
	"""
	if not frappe.db.exists("DocType", SIGNOFF_DOCTYPE):
		return None
	return frappe.db.get_value(
		SIGNOFF_DOCTYPE,
		{
			"course": course,
			"user": learner_user,
			"outcome": COMPETENT,
			"docstatus": 1,
		},
		"name",
	)


def has_competent_signoff(course, learner_user):
	"""Whether a submitted ``Competent`` sign-off exists."""
	return bool(competent_signoff_name(course, learner_user))


def signoff_outstanding(course, learner_user):
	"""Whether *course* demands a supervisor sign-off that *learner_user* lacks.

	The one place that rule is written. Both the completion gate and the player's
	"here is what is left" list read it, because two implementations of a
	compliance check drift, and the way this one drifted was that only one of them
	existed on the path an auditor would care about.

	Matched on the course rather than the course version on purpose: somebody who
	was watched draining a basin last month has been watched draining a basin. A
	material content change supersedes their completion and sends them back through
	the material, which is the right lever -- re-observing them physically because a
	paragraph was rewritten is not.

	Returns False when Training Signoff is not migrated, so a site part-way through
	Phase 4 can still finish courses rather than being unable to complete anything
	at all. That is logged rather than silent: a requirement that cannot be enforced
	must not also be invisible.
	"""
	if not cint(frappe.db.get_value("Training Course", course, "require_supervisor_signoff")):
		return False
	if not frappe.db.exists("DocType", SIGNOFF_DOCTYPE):
		frappe.log_error(
			f"{course} requires a supervisor sign-off but {SIGNOFF_DOCTYPE} is not migrated "
			"on this site; the requirement cannot be enforced.",
			"Training sign-off",
		)
		return False
	return not has_competent_signoff(course, learner_user)


@frappe.whitelist()
def get_signoff_queue():
	"""Outstanding requests this person may act on, for a supervisor's dashboard."""
	caller = _session_user()
	filters = {"docstatus": 0}
	if not _is_manager(caller):
		filters["supervisor_user"] = caller

	rows = frappe.get_all(
		SIGNOFF_DOCTYPE,
		filters=filters,
		fields=["name", "course", "course_version", "user", "supervisor_user", "creation"],
		order_by="creation asc",
	)
	courses = {}
	for row in rows:
		detail = courses.get(row.course)
		if detail is None:
			detail = frappe.db.get_value(
				"Training Course", row.course, ["course_title", "signoff_instructions"], as_dict=True
			) or frappe._dict()
			courses[row.course] = detail
		row["course_title"] = detail.get("course_title") or row.course
		row["instructions"] = detail.get("signoff_instructions")
	# A manager's own requests are dropped here rather than in SQL, so the
	# manager/supervisor branches above stay readable.
	return [row for row in rows if row.get("user") != caller]


def _notify(user, subject, body_html):
	"""Best-effort email + Notification Log; returns whether anything went out.

	Gated on ``Training Settings → Send Notifications`` like the rest of the
	module's mail. The boolean comes back so a caller can say "recorded, but
	notifications are off" instead of implying somebody was told.
	"""
	if not user or _in_maintenance_context() or not notifications._enabled():
		return False
	recipient = notifications._recipient(user)
	return bool(recipient) and notifications._send(recipient, subject, body_html)
