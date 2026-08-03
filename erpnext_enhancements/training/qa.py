# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Ask-the-author — a learner's question on a lesson, routed to whoever wrote it.

The point of this is not support. A question asked twice is a defect in the
lesson, and the only place that becomes visible is a list of questions filed
against one lesson at one timestamp. So a thread is anchored to the lesson and to
the second of video it was asked from, and an answer the author marks public is
shown to every future learner of that lesson rather than mailed to one person and
lost.

Three things here are deliberate and look like omissions until you know why.

**Visibility is not re-derived.** Whether a learner may ask about a course is the
same predicate as whether they may take it — audience, roles, customer,
assignment and published state, all ANDed — and ``api/training._visible_course_names``
is the single implementation of it. This module calls that rather than writing a cheaper
approximation, because the cheap approximation is how an internal safety course
ends up answerable by a customer. The import is *lazy*: ``api.training`` imports
several ``training.*`` modules, and a module-scope import here would put this file
one careless future edit away from an import cycle.

**A public thread never carries the asker.** Questions arrive phrased personally —
"I still don't understand what backwash means" — and publishing one with a name on
it publishes somebody's confusion to every colleague and every other client taking
the course. The learner sees their own threads in full; everyone else sees the
question and the answer and no attribution. Deliberately not even initials: unlike
a certificate, nothing here needs to identify a person to be useful.

**Answering is restricted to the course author or a Training Manager, and the
check is on the server.** A learner holding the Training Learner role can call any
whitelisted method directly, whatever the desk chooses to show them.

State this module deliberately does **not** set, because
``TrainingQuestionThread.validate`` owns it and two writers would drift: ``course``
and ``course_version`` (denormalised from the lesson), ``user`` / ``asked_on``,
``answered_by`` / ``answered_on``, and ``status``. In particular ``is_public`` is
*clamped* there — a thread with no answer, or a hidden one, cannot be public —
so setting it here is a request, not a guarantee, and the value that comes back
from the save is the one that counts.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url

from erpnext_enhancements.training import notifications
from erpnext_enhancements.training.doctype.training_question_thread.training_question_thread import (
	ANSWERED,
	HIDDEN,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

THREAD_DOCTYPE = "Training Question Thread"
THREAD_ROUTE = "training-question-thread"

MANAGER_ROLES = {"Training Manager", "System Manager"}

# Longest question we will store. Not really a validation: a learner pasting an
# essay into a lesson question box is asking for a phone call, and a thread is
# not the place to have it.
MAX_QUESTION_CHARS = 2000


def _in_maintenance_context():
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _learner():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


def _require_runtime():
	if not (is_enabled("training_enabled") and is_enabled("portal_enabled")):
		frappe.throw(_("Training is not available yet."))


# ------------------------------------------------------------------- visibility


def _visible_or_throw(user, course):
	"""Refuse unless this learner may actually open the course.

	Lazy import: ``api.training`` pulls in ``training.gcs_media``, ``training.grading``
	and ``training.progress``, and importing it at module scope here would make an
	import cycle out of any future edit that wires Q&A into that file.
	"""
	from erpnext_enhancements.api.training import _learner_profile, _visible_course_names

	profile = _learner_profile(user)
	if course not in _visible_course_names(user, profile):
		frappe.throw(_("This course is not available to you."), frappe.PermissionError)
	return profile


def _lesson_row(course, lesson_key):
	"""The published lesson a question is being asked from.

	Addressed by ``lesson_key`` and never by docname, matching the player: the
	docname is the argument for ``/api/resource/Training Lesson``, which a learner
	role cannot read, and there is no reason to publish the key to a door and then
	rely on the lock.
	"""
	current_version = frappe.db.get_value("Training Course", course, "current_version")
	if not current_version:
		frappe.throw(_("That course has no published version."))
	row = frappe.db.get_value(
		"Training Lesson",
		{"course_version": current_version, "lesson_key": lesson_key},
		["name", "lesson_title", "allow_questions"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("That lesson is not part of this course."))
	return row


def _course_author(course):
	"""Who a question is routed to.

	The author, falling back to every Training Manager. An unroutable question is
	worse than a noisy one: it sits Open forever, and the learner concludes that
	asking does nothing.
	"""
	author = frappe.db.get_value("Training Course", course, "author")
	if author and frappe.db.get_value("User", author, "enabled"):
		return [author]
	return [
		u
		for u in frappe.get_all(
			"Has Role",
			filters={"role": "Training Manager", "parenttype": "User"},
			pluck="parent",
			distinct=True,
		)
		if frappe.db.get_value("User", u, "enabled")
	]


def _may_answer(course):
	"""The course author, or a Training Manager. Nobody else, ever."""
	if MANAGER_ROLES & set(frappe.get_roles()):
		return True
	author = frappe.db.get_value("Training Course", course, "author")
	return bool(author) and author == frappe.session.user


def _notify(recipients, subject, body_html):
	"""Best-effort email + Notification Log, returning whether anything went out.

	Gated on ``Training Settings → Send Notifications`` like the rest of the
	module's mail, and the boolean comes back so a caller can say "your question is
	filed, but notifications are switched off" rather than implying somebody has
	been told.
	"""
	if _in_maintenance_context() or not notifications._enabled():
		return False
	sent = False
	for user in recipients or []:
		if not user:
			continue
		recipient = notifications._recipient(user)
		if recipient and notifications._send(recipient, subject, body_html):
			sent = True
	return sent


# ------------------------------------------------------------------------ ask


@frappe.whitelist()
def ask_question(course, lesson_key, question, at_seconds=None):
	"""File a learner's question against a lesson and route it to the author."""
	user = _learner()
	_require_runtime()

	text = (question or "").strip()
	if not text:
		frappe.throw(_("Type a question first."))
	if len(text) > MAX_QUESTION_CHARS:
		frappe.throw(
			_("That is longer than a lesson question box is meant to hold. Trim it to {0} characters, "
			  "or email the trainer directly.").format(MAX_QUESTION_CHARS)
		)

	_visible_or_throw(user, course)
	lesson = _lesson_row(course, lesson_key)
	if not cint(lesson.allow_questions):
		frappe.throw(_("Questions are turned off for this lesson."))

	doc = frappe.get_doc(
		{
			"doctype": THREAD_DOCTYPE,
			"lesson": lesson.name,
			"question": text,
			"at_seconds": cint(at_seconds),
			# Set explicitly rather than left to the controller's session default:
			# this endpoint inserts with ignore_permissions, and the asker is the
			# one field on the record that must not depend on how it was saved.
			"user": user,
		}
	)
	doc.insert(ignore_permissions=True)

	notified = _notify(
		_course_author(course),
		_("Training question: {0}").format(lesson.lesson_title or course),
		f"""
			<p>A learner has asked a question on
			<b>{frappe.utils.escape_html(lesson.lesson_title or '')}</b>:</p>
			<blockquote>{frappe.utils.escape_html(text)}</blockquote>
			<p><a href="{get_url(f'/app/{THREAD_ROUTE}/{doc.name}')}">Answer it</a></p>
		""",
	)

	return {"thread": doc.name, "status": doc.status, "notified": notified}


# --------------------------------------------------------------------- answer


@frappe.whitelist()
def answer_question_thread(thread, answer, is_public=0):
	"""Answer a learner's question. Course author or Training Manager only.

	``is_public`` is the whole reason this is worth building: an answer marked
	public is shown to every future learner of that lesson, which is how a question
	asked five times becomes one paragraph of content instead of five emails.
	It is a *request* — the controller refuses to publish a hidden or unanswered
	thread — so the value that comes back is the one that stuck.
	"""
	user = _learner()
	text = (answer or "").strip()
	if not text:
		frappe.throw(_("Type an answer first."))

	doc = frappe.get_doc(THREAD_DOCTYPE, thread)
	if not _may_answer(doc.course):
		frappe.throw(
			_("Only the course author or a Training Manager can answer this."), frappe.PermissionError
		)
	if doc.status == HIDDEN:
		frappe.throw(_("That thread has been hidden. Un-hide it before answering."))

	doc.answer = text
	doc.answered_by = user
	doc.is_public = 1 if cint(is_public) else 0
	doc.save(ignore_permissions=True)

	lesson_title = frappe.db.get_value("Training Lesson", doc.lesson, "lesson_title") or ""
	notified = _notify(
		[doc.user],
		_("Your training question has been answered"),
		f"""
			<p>Your question on <b>{frappe.utils.escape_html(lesson_title)}</b> has an answer:</p>
			<blockquote>{frappe.utils.escape_html(text)}</blockquote>
			<p><a href="{get_url('/training')}">Open your training</a></p>
		""",
	)

	return {
		"thread": doc.name,
		"status": doc.status,
		"is_public": cint(doc.is_public),
		"notified": notified,
	}


# ----------------------------------------------------------------------- read


@frappe.whitelist()
def get_lesson_questions(course, lesson_key):
	"""What this learner may read on this lesson: their own threads, plus public answers.

	Two lists rather than one, because they are two different things. ``mine`` is
	correspondence — it shows an unanswered question still sitting there, which is
	the only way a learner knows whether asking did anything. ``public`` is content:
	the author's answers, with nobody's name on the questions.
	"""
	user = _learner()
	if not (is_enabled("training_enabled") and is_enabled("portal_enabled")):
		return {"enabled": False, "mine": [], "public": []}

	_visible_or_throw(user, course)
	lesson = _lesson_row(course, lesson_key)

	mine = frappe.get_all(
		THREAD_DOCTYPE,
		filters={"lesson": lesson.name, "user": user},
		fields=["name", "question", "answer", "status", "asked_on", "answered_on", "at_seconds", "is_public"],
		order_by="creation desc",
	)

	public_rows = frappe.get_all(
		THREAD_DOCTYPE,
		filters={"lesson": lesson.name, "is_public": 1, "status": ANSWERED},
		fields=["name", "question", "answer", "answered_on", "at_seconds", "upvotes", "user"],
		order_by="upvotes desc, answered_on asc",
	)
	# The asker is dropped here rather than left out of the field list, so that a
	# future filter or sort needing ``user`` cannot quietly publish it too.
	public = [
		{key: value for key, value in row.items() if key != "user"}
		for row in public_rows
		if row.get("user") != user
	]

	return {"enabled": True, "lesson_title": lesson.lesson_title, "mine": mine, "public": public}
