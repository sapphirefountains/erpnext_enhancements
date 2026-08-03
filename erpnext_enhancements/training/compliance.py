# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Uncertified-dispatch advisory — the check that must never block a save.

Fires on ``Sapphire Maintenance Record`` and ``Task`` validate and says, quietly,
"the person you just put on this job is not current on required training".

**It is an advisory and nothing else, and that is the load-bearing property of
this file.** By the time it runs a truck is usually already at a site. A hard stop
here does not prevent the uncertified visit — it prevents the *record* of it. The
visit happens anyway, off the books, with no chemistry readings and no client
sign-off, which is strictly worse than the thing being flagged. So **nothing in
this module ever throws** — the word does not appear here, and both test suites
grep for it — and every entry point goes through ``_advisory``, which swallows and
logs. A compliance warning that breaks dispatch is a self-inflicted outage.
``tests/test_training_compliance.py`` pins both properties, including that a
raising helper does not propagate.

Gated on ``Training Settings → warn_on_uncertified_dispatch``, off by default.

The warning has two halves. The inline half is a ``msgprint`` the person saving
sees. The durable half — timeline comment plus an email to the supervisor — is
enqueued ``after_commit``, for two reasons: on a brand-new record the document
does not exist yet at validate time (name is set before validate, the row is
not), so a Comment pointing at it would fail its own link validation; and a slow
SMTP must never sit inside a save.

Repeat saves are deduped on a fingerprint of the (learner, course) pairs, embedded
in the comment as an HTML comment. Re-saving a visit five times leaves one
timeline entry and mails the supervisor once, which is the difference between a
warning people read and a warning people filter.

"Uncertified" here means: a **required-weight** course that this learner has no
currently-valid Training Completion for. A live valid completion always wins, so
an open *recertification* assignment on someone whose current certificate has not
lapsed yet is not a finding — flagging that would train everyone to ignore the box.
"""

import functools
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_url_to_form, nowdate

from erpnext_enhancements.training import notifications
from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
	OPEN_STATUSES,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

# Embedded in the timeline comment so a re-save can recognise its own previous
# warning. A marker rather than a custom field: this fires on two doctypes we do
# not own, and neither should grow a field for an advisory.
MARKER = "training-compliance"

# A save must stay cheap. Nothing dispatches twenty people at once, and if
# something ever does, a truncated advisory beats a slow form.
MAX_LEARNERS_PER_CHECK = 20


# ----------------------------------------------------------------------- guards


def _log_quietly(where):
	"""Log a swallowed failure, and swallow a failure to log.

	``log_error`` writes a document. Inside a transaction that is already unhappy
	that write can fail too, and an advisory that raises while reporting that it
	raised is the same outage twice.
	"""
	try:
		frappe.log_error(
			title="Training compliance advisory failed",
			message=f"{where}\n{frappe.get_traceback()}",
		)
	except Exception:
		pass


def _should_run():
	flags = frappe.flags
	if flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import:
		return False
	return bool(is_enabled("warn_on_uncertified_dispatch"))


def _advisory(fn):
	"""Gate + swallow. Every public function in this module wears it.

	The gate lives here rather than in each function so that "did anyone remember
	to check the setting" has one answer instead of three. The swallow is the
	point of the file: a bug in a compliance *warning* must not be able to fail a
	dispatcher's save.
	"""

	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		try:
			if not _should_run():
				return None
			return fn(*args, **kwargs)
		except Exception:
			_log_quietly(fn.__name__)
		return None

	return wrapper


# ------------------------------------------------------------------ entry points


@_advisory
def warn_uncertified_technician(doc, method=None):
	"""``Sapphire Maintenance Record`` validate — warn about the assigned tech."""
	if cint(getattr(doc, "docstatus", 0)) == 2:
		return
	_check(doc, [getattr(doc, "technician", None)])


@_advisory
def warn_uncertified_assignee(doc, method=None):
	"""``Task`` validate — warn about whoever the task is assigned to.

	Task has no technician field; assignment lives in ``_assign``, the JSON list
	the framework maintains from ToDo rows.
	"""
	if (getattr(doc, "status", "") or "") in ("Completed", "Cancelled"):
		return
	if cint(getattr(doc, "is_template", 0)):
		return
	_check(doc, _assignees(doc))


@_advisory
def record_advisory(doctype=None, name=None, findings=None, fingerprint=None):
	"""The durable half, run after commit: timeline comment + supervisor email.

	Enqueued rather than inline — see the module docstring. Deduped on
	``fingerprint`` so five saves of the same visit leave one comment.
	"""
	if not (doctype and name and findings):
		return
	if _already_recorded(doctype, name, fingerprint):
		return

	target = frappe.get_doc(doctype, name)
	target.add_comment("Comment", _comment_html(findings, fingerprint))

	# The email half — and only the email half — also respects the module's
	# master notification switch. A site that has deliberately silenced Training
	# email should not start sending it through the back door; the comment and the
	# msgprint still happen, so nothing is lost from the record.
	if is_enabled("notifications_enabled"):
		_notify_supervisors(doctype, name, findings)


# ---------------------------------------------------------------------- helpers


def _assignees(doc):
	"""Users in ``_assign``. Malformed JSON reads as "nobody", never as an error."""
	raw = getattr(doc, "_assign", None)
	if not raw:
		return []
	if isinstance(raw, (list, tuple)):
		return [u for u in raw if u]
	try:
		parsed = json.loads(raw)
	except (TypeError, ValueError):
		return []
	return [u for u in parsed if u] if isinstance(parsed, list) else []


def _check(doc, users):
	users = [u for u in dict.fromkeys([u for u in users if u])][:MAX_LEARNERS_PER_CHECK]
	if not users:
		return

	required = _required_courses()
	if not required:
		return

	findings = []
	for user in users:
		findings.extend(_uncertified(user, required))
	if not findings:
		return

	_msgprint(findings)

	fingerprint = _fingerprint(findings)
	frappe.enqueue(
		"erpnext_enhancements.training.compliance.record_advisory",
		queue="short",
		enqueue_after_commit=True,
		doctype=doc.doctype,
		name=doc.name,
		findings=findings,
		fingerprint=fingerprint,
	)


def _required_courses():
	"""Published required-weight courses. Optional courses are never a finding."""
	return set(
		frappe.get_all(
			"Training Course",
			filters={"weight": "Required", "status": ["!=", "Retired"]},
			pluck="name",
		)
	)


def _uncertified(user, required):
	"""Required courses this learner is not currently certified in.

	Three sources, merged by course and in this priority order: an open assignment,
	a completion that has been expired or revoked, and a completion whose
	``expires_on`` has passed but which the nightly sweep has not restatused yet.
	The third exists because the advisory is read at dispatch time and must not
	depend on a scheduler run having happened this morning.
	"""
	valid = _currently_valid_courses(user)
	learner_name = frappe.db.get_value("User", user, "full_name") or user
	found = {}

	def add(course, title, reason):
		if course not in required or course in valid or course in found:
			return
		found[course] = {
			"user": user,
			"learner_name": learner_name,
			"course": course,
			"course_title": title or course,
			"reason": reason,
		}

	for row in frappe.get_all(
		"Training Assignment",
		filters={"user": user, "status": ["in", OPEN_STATUSES]},
		fields=["course", "course_title", "status"],
	):
		add(
			row.get("course"),
			row.get("course_title"),
			_("Overdue") if row.get("status") == "Overdue" else _("Not completed"),
		)

	for row in frappe.get_all(
		"Training Completion",
		filters={"user": user, "docstatus": 1, "status": ["in", ("Expired", "Revoked")]},
		fields=["course", "course_title_snapshot", "status"],
	):
		add(
			row.get("course"),
			row.get("course_title_snapshot"),
			_("Certification expired") if row.get("status") == "Expired" else _("Certification revoked"),
		)

	for row in frappe.get_all(
		"Training Completion",
		filters={
			"user": user,
			"docstatus": 1,
			"status": "Valid",
			"expires_on": ["<", nowdate()],
		},
		fields=["course", "course_title_snapshot"],
	):
		add(row.get("course"), row.get("course_title_snapshot"), _("Certification lapsed"))

	return list(found.values())


def _currently_valid_courses(user):
	"""Courses with a live completion — these silence every other source.

	A completion with no ``expires_on`` never lapses, which is how a course with
	no ``certificate_valid_months`` is meant to behave.
	"""
	valid = set()
	for row in frappe.get_all(
		"Training Completion",
		filters={"user": user, "docstatus": 1, "status": "Valid"},
		fields=["course", "expires_on"],
	):
		expires = row.get("expires_on")
		if not expires or str(expires) >= nowdate():
			valid.add(row.get("course"))
	return valid


def _lines(findings):
	return "".join(
		"<li>{0} — {1} <i>({2})</i></li>".format(
			escape_html(f.get("learner_name") or f.get("user")),
			escape_html(f.get("course_title") or f.get("course")),
			escape_html(f.get("reason") or ""),
		)
		for f in findings
	)


def _msgprint(findings):
	"""The inline half. ``msgprint`` and never ``throw`` — the save continues.

	Worded as information rather than as a refusal, because the dispatcher reading
	it usually cannot do anything about it in the next thirty seconds and needs to
	know that they are still allowed to save.
	"""
	frappe.msgprint(
		"<p>{0}</p><ul>{1}</ul><p>{2}</p>".format(
			_("Required training is not current for:"),
			_lines(findings),
			_("This has been noted on the record and the supervisor informed. Saving is not blocked."),
		),
		title=_("Training not current"),
		indicator="orange",
	)


def _fingerprint(findings):
	"""Stable digest of the (learner, course) pairs, order-independent."""
	pairs = sorted(f"{f.get('user')}::{f.get('course')}" for f in findings)
	return hashlib.sha1("|".join(pairs).encode("utf-8")).hexdigest()[:12]


def _already_recorded(doctype, name, fingerprint):
	if not fingerprint:
		return False
	return bool(
		frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": doctype,
				"reference_name": name,
				"comment_type": "Comment",
				"content": ["like", f"%{MARKER}:{fingerprint}%"],
			},
			limit=1,
		)
	)


def _comment_html(findings, fingerprint):
	return "<!-- {0}:{1} --><p><b>{2}</b></p><ul>{3}</ul>".format(
		MARKER,
		fingerprint or "",
		_("Required training is not current for the people assigned to this work"),
		_lines(findings),
	)


def _supervisors(findings):
	"""Who hears about it: each flagged person's ``reports_to``, else the
	escalation role from Training Settings.

	The flagged learners themselves are excluded even when they hold the role —
	they have their own reminders, and being escalated about yourself reads as a
	reprimand rather than a nudge (same rule as ``notifications.send_escalation``).
	"""
	learners = {f.get("user") for f in findings}
	recipients = set()

	for user in learners:
		employee = frappe.db.get_value("Employee", {"user_id": user}, ["name", "reports_to"], as_dict=True)
		if not employee or not employee.get("reports_to"):
			continue
		supervisor = frappe.db.get_value("Employee", employee.get("reports_to"), "user_id")
		if supervisor:
			recipients.add(supervisor)

	if not recipients:
		role = frappe.db.get_single_value("Training Settings", "escalation_role")
		if role:
			recipients.update(
				frappe.get_all(
					"Has Role",
					filters={"role": role, "parenttype": "User"},
					pluck="parent",
					distinct=True,
				)
			)

	return sorted(recipients - learners)


def _notify_supervisors(doctype, name, findings):
	"""One email per supervisor. Reuses ``notifications`` for address resolution
	and for its swallow-and-log delivery, so a bad address on one supervisor
	cannot stop the others hearing about it."""
	link = get_url_to_form(doctype, name)
	for user in _supervisors(findings):
		recipient = notifications._recipient(user)
		if not recipient:
			continue
		body = "".join(
			[
				f"<p>Hi {escape_html(recipient.full_name)},</p>",
				"<p>{0}</p>".format(
					_("{0} <b>{1}</b> was saved with people whose required training is not current:").format(
						escape_html(_(doctype)), escape_html(name)
					)
				),
				f"<ul>{_lines(findings)}</ul>",
				'<p><a href="{0}">{1}</a></p>'.format(link, _("Open the record")),
				"<p><i>{0}</i></p>".format(
					_("The work was not blocked — this is an advisory so the visit still gets recorded.")
				),
			]
		)
		notifications._send(recipient, _("Training not current on {0}").format(name), body)
