# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled evaluations — book a competency check, and turn its outcome into a
sign-off.

The scheduling record is ``Training Evaluation``; this module is its endpoint half.
:func:`record_evaluation` is the one that matters, and the thing it is careful *not*
to do is re-implement the sign-off. It creates a draft ``Training Signoff``
(supervisor = the evaluation's evaluator) and hands it to
``signoff.record_signoff``, which already owns the identity check, the submit, the
notification and the completion gate. So an evaluation's outcome is the *same*
attestation a manual sign-off produces — one competency record, one gate, whichever
door it came through. There is no second way to sign anything off.

:func:`notify_scheduled` is the auto-invite: the learner and the evaluator are told
a slot is booked. The calendar side (an ICS / Google Calendar event) is the native
half and is deferred, as in live classes.

Indentation is tabs, matching ``training/signoff.py``.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url

from erpnext_enhancements.training import notifications, signoff

EVALUATION_DOCTYPE = "Training Evaluation"
MANAGER_ROLES = {"Training Manager", "System Manager"}


def _session_user():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


def _is_manager(user=None):
	return bool(MANAGER_ROLES & set(frappe.get_roles(user or frappe.session.user)))


@frappe.whitelist()
def record_evaluation(evaluation, outcome, competency_notes=None, signature_image=None):
	"""Record an evaluation's outcome as a submitted ``Training Signoff``.

	The evaluator (or a Training Manager) only; never the learner. It books the
	observation and files the result under the same competency record the rest of
	the module reads — reusing ``signoff.record_signoff`` for the attestation itself.
	"""
	caller = _session_user()
	doc = frappe.get_doc(EVALUATION_DOCTYPE, evaluation)

	if doc.learner == caller:
		frappe.throw(
			_("You cannot record your own evaluation. The evaluator or a Training Manager does."),
			frappe.PermissionError,
		)
	if not (doc.evaluator_user == caller or _is_manager(caller)):
		frappe.throw(
			_("Only {0} or a Training Manager can record this evaluation.").format(
				doc.evaluator_user or _("the evaluator")
			),
			frappe.PermissionError,
		)
	if doc.signoff and cint(frappe.db.get_value(signoff.SIGNOFF_DOCTYPE, doc.signoff, "docstatus")) == 1:
		frappe.throw(_("This evaluation has already been recorded."))

	signoff_name = doc.signoff
	if not signoff_name:
		draft = frappe.get_doc(
			{
				"doctype": signoff.SIGNOFF_DOCTYPE,
				"course": doc.course,
				"course_version": frappe.db.get_value("Training Course", doc.course, "current_version"),
				"user": doc.learner,
				# supervisor_user is re-derived by the controller from this Employee,
				# which is the derivation record_signoff's identity check is worth
				# anything against — so it is not set here.
				"supervisor": doc.evaluator,
			}
		)
		draft.insert(ignore_permissions=True)
		signoff_name = draft.name

	# record_signoff asserts caller == supervisor_user (== evaluator_user) or a
	# manager, refuses the learner, submits, and notifies — the whole attestation.
	result = signoff.record_signoff(signoff_name, outcome, competency_notes, signature_image)

	frappe.db.set_value(
		EVALUATION_DOCTYPE, doc.name, {"signoff": signoff_name, "status": "Completed"}, update_modified=False
	)
	return {
		"evaluation": doc.name,
		"signoff": signoff_name,
		"outcome": outcome,
		"notified": result.get("notified"),
	}


# ---------------------------------------------------------------- auto-invite


def notify_scheduled(doc):
	"""Tell the learner and the evaluator a slot is booked. Best-effort, gated on
	``Training Settings → Send Notifications`` like the rest of the module's mail."""
	course_title = frappe.db.get_value("Training Course", doc.course, "course_title") or doc.course
	when = frappe.utils.format_datetime(doc.scheduled_on) if doc.scheduled_on else ""
	where = (doc.location or "").strip()
	esc = frappe.utils.escape_html

	_notify(
		doc.learner,
		_("Training evaluation booked: {0}").format(course_title),
		f"""<p>A practical evaluation of your <b>{esc(course_title)}</b> competency has been booked"""
		+ (f" for <b>{esc(when)}</b>" if when else "")
		+ (f", at {esc(where)}" if where else "")
		+ f""".</p><p><a href="{get_url('/training')}">Open your training</a></p>""",
	)

	if doc.evaluator_user:
		learner_name = frappe.db.get_value("User", doc.learner, "full_name") or doc.learner
		_notify(
			doc.evaluator_user,
			_("You are booked to evaluate {0}").format(learner_name),
			f"""<p>You are booked to evaluate <b>{esc(learner_name)}</b> on"""
			+ f""" <b>{esc(course_title)}</b>"""
			+ (f" on <b>{esc(when)}</b>" if when else "")
			+ (f", at {esc(where)}" if where else "")
			+ f""".</p><p><a href="{get_url(f'/app/training-evaluation/{doc.name}')}">Open the evaluation</a></p>""",
		)


def _notify(user, subject, body_html):
	if not user or not notifications._enabled():
		return False
	recipient = notifications._recipient(user)
	return bool(recipient) and notifications._send(recipient, subject, body_html)
