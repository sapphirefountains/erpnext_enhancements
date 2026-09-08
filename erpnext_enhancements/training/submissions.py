# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Learner work submissions and the grader's queue.

Some lessons cannot be assessed by a quiz — the learner has to *do* something and
hand in the result: a photo of a finished basin, a filled-in checklist, a signed
form. A lesson marked ``requires_submission`` asks the learner for one file; a
Training Manager grades it Passed or Needs Rework from a queue.

Three deliberate shapes, each with a reason:

**The submitted file is private and follows the record.** A learner uploads a
private ``File`` (owned by them, readable by nobody else), and ``submit_work``
re-parents it onto the new ``Training Submission`` — ``attached_to_doctype`` /
``attached_to_name`` — so from then on Frappe's standard private-file check gates
it by read permission on the submission. The learner reads their own submission
(``training/permissions.py`` scopes it to them); a grader is a Training Manager and
reads all. No bespoke file-serving route is needed, unlike the author-uploaded
lesson media in ``gcs_media`` — that is shipped *to* learners who do not own it;
this is a file the learner already owns.

**Visibility is not re-derived.** Whether a learner may submit against a course is
the same predicate as whether they may take it, and ``api/training._visible_course_names``
is the single implementation of it — imported lazily for exactly the reason
``qa.py`` documents (``api.training`` pulls in several ``training.*`` modules, so a
module-scope import here is one careless edit from a cycle).

**Grading is Training-Manager-only, and the check is on the server.** A learner
holding the Training Learner role can call any whitelisted method directly. The
grade endpoint therefore verifies the role itself rather than trusting that the
desk hid the button — the same stance ``qa.answer_question_thread`` takes.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url

from erpnext_enhancements.training import notifications
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

SUBMISSION_DOCTYPE = "Training Submission"

MANAGER_ROLES = {"Training Manager", "System Manager"}

# The statuses a grader is allowed to move a submission to. "Submitted" is the
# learner's opening state and is never a grade; the two terminal grades and the
# "I have picked this up" interim are the grader's to set.
GRADEABLE_STATUSES = {"Under Review", "Passed", "Needs Rework"}

# Longest grader note we will store. A grade is a verdict plus a sentence, not an
# essay; anything longer is a conversation that belongs somewhere with a reply box.
MAX_FEEDBACK_CHARS = 4000


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


def _is_manager(user):
	return bool(MANAGER_ROLES & set(frappe.get_roles(user)))


# ------------------------------------------------------------------- visibility


def _visible_or_throw(user, course):
	"""Refuse unless this learner may actually open the course.

	Lazy import, for the reason spelled out in ``qa._visible_or_throw``: importing
	``api.training`` at module scope makes an import cycle out of any future edit.
	"""
	from erpnext_enhancements.api.training import _learner_profile, _visible_course_names

	profile = _learner_profile(user)
	if course not in _visible_course_names(user, profile):
		frappe.throw(_("This course is not available to you."), frappe.PermissionError)
	return profile


def _lesson_row(course, lesson_key):
	"""The published lesson a submission is being handed in against.

	Addressed by ``lesson_key`` and never by docname, matching the player and
	``qa._lesson_row``: the docname is the argument for ``/api/resource/Training
	Lesson``, which a learner role cannot read.
	"""
	current_version = frappe.db.get_value("Training Course", course, "current_version")
	if not current_version:
		frappe.throw(_("That course has no published version."))
	row = frappe.db.get_value(
		"Training Lesson",
		{"course_version": current_version, "lesson_key": lesson_key},
		["name", "lesson_title", "requires_submission"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("That lesson is not part of this course."))
	return row


def _attach_file_to(file_url, user, submission):
	"""Re-parent the learner's uploaded file onto their submission.

	Returns the file_url that ended up on the submission, or ``None`` if there was
	nothing usable to attach. Guards against a learner passing a ``file_url`` that
	is not theirs: the private file must be owned by the submitter (or the caller
	must be a manager acting on their behalf), otherwise a crafted request could
	staple someone else's private file to a submission and read it back through it.
	"""
	url = (file_url or "").strip()
	if not url:
		return None

	f = frappe.db.get_value(
		"File", {"file_url": url}, ["name", "owner", "is_private"], as_dict=True
	)
	if not f:
		frappe.throw(_("That file upload could not be found. Try attaching it again."))
	if f.owner != user and not _is_manager(frappe.session.user):
		frappe.throw(_("That file is not yours to submit."), frappe.PermissionError)

	frappe.db.set_value(
		"File",
		f.name,
		{
			"is_private": 1,
			"attached_to_doctype": SUBMISSION_DOCTYPE,
			"attached_to_name": submission,
		},
	)
	return url


# ----------------------------------------------------------------------- submit


@frappe.whitelist()
def submit_work(course, lesson_key, file=None, text=None, block_key=None):
	"""Hand in a file against a lesson that asks for one.

	The learner uploads the file first (a private ``File``, via Frappe's
	``upload_file``) and passes its ``file_url`` here. A fresh submission is created
	each time rather than overwriting the last — a Needs Rework verdict and the
	reworked answer are two records, and the history is the point.
	"""
	user = _learner()
	_require_runtime()

	url = (file or "").strip()
	note = (text or "").strip()
	if not url and not note:
		frappe.throw(_("Attach a file or write a note before submitting."))

	_visible_or_throw(user, course)
	lesson = _lesson_row(course, lesson_key)
	if not cint(lesson.requires_submission):
		frappe.throw(_("This lesson is not asking for a work submission."))

	doc = frappe.get_doc(
		{
			"doctype": SUBMISSION_DOCTYPE,
			"course": course,
			"lesson": lesson.name,
			"block_key": (block_key or "").strip() or None,
			# Set explicitly rather than left to a session default: this endpoint
			# inserts with ignore_permissions, and the learner is the one field on
			# the record that must not depend on how it was saved.
			"user": user,
			"submission_text": note or None,
			"status": "Submitted",
			"submitted_on": frappe.utils.now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)

	stored = _attach_file_to(url, user, doc.name)
	if stored:
		frappe.db.set_value(SUBMISSION_DOCTYPE, doc.name, "file", stored)

	notified = _notify(
		_graders(),
		_("Work submitted: {0}").format(lesson.lesson_title or course),
		f"""
			<p>A learner has submitted work on
			<b>{frappe.utils.escape_html(lesson.lesson_title or '')}</b> and it is waiting to be graded.</p>
			<p><a href="{get_url(f'/app/training-submission/{doc.name}')}">Grade it</a></p>
		""",
	)

	return {"submission": doc.name, "status": doc.status, "notified": notified}


# ------------------------------------------------------------------------ grade


@frappe.whitelist()
def grade_submission(submission, status, feedback=None, grade=None):
	"""Grade a learner's submission. Training Manager only.

	``Needs Rework`` requires feedback — sending work back with no reason is the one
	verdict that is useless without words. ``Passed`` and ``Under Review`` may carry
	feedback but do not require it.
	"""
	caller = _learner()
	if not _is_manager(caller):
		frappe.throw(
			_("Only a Training Manager can grade submissions."), frappe.PermissionError
		)

	target = (status or "").strip()
	if target not in GRADEABLE_STATUSES:
		frappe.throw(
			_("Grade a submission Passed, Needs Rework, or Under Review.")
		)

	note = (feedback or "").strip()
	if len(note) > MAX_FEEDBACK_CHARS:
		frappe.throw(
			_("That feedback is longer than this box is meant to hold. Trim it to {0} characters.").format(
				MAX_FEEDBACK_CHARS
			)
		)
	if target == "Needs Rework" and not note:
		frappe.throw(_("Say what needs reworking — a Needs Rework with no note tells the learner nothing."))

	doc = frappe.get_doc(SUBMISSION_DOCTYPE, submission)
	doc.status = target
	doc.feedback = note or None
	doc.grade = (grade or "").strip() or None
	# "Under Review" is claiming the work, not grading it, so it does not stamp a
	# grader/time — the two terminal verdicts do.
	if target in {"Passed", "Needs Rework"}:
		doc.graded_by = caller
		doc.graded_on = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)

	notified = False
	if target in {"Passed", "Needs Rework"}:
		lesson_title = frappe.db.get_value("Training Lesson", doc.lesson, "lesson_title") or ""
		verdict = _("marked passed") if target == "Passed" else _("sent back for rework")
		body_note = f"<blockquote>{frappe.utils.escape_html(note)}</blockquote>" if note else ""
		notified = _notify(
			[doc.user],
			_("Your training submission was {0}").format(verdict),
			f"""
				<p>Your submission on <b>{frappe.utils.escape_html(lesson_title)}</b> was {verdict}.</p>
				{body_note}
				<p><a href="{get_url('/training')}">Open your training</a></p>
			""",
		)

	return {"submission": doc.name, "status": doc.status, "notified": notified}


# ------------------------------------------------------------------------ queue


@frappe.whitelist()
def get_submission_queue():
	"""Submissions waiting on a grader, oldest first. Training Manager only.

	Managers are unscoped in ``training/permissions.py`` so this list is the whole
	pending queue. A non-manager gets an empty list rather than an error — the
	button that opens this is theirs to not see, not to be shouted at.
	"""
	caller = _learner()
	if not _is_manager(caller):
		return []

	rows = frappe.get_all(
		SUBMISSION_DOCTYPE,
		filters={"status": ["in", ("Submitted", "Under Review")]},
		fields=[
			"name",
			"course",
			"lesson",
			"user",
			"status",
			"grade",
			"file",
			"submission_text",
			"submitted_on",
		],
		order_by="submitted_on asc, creation asc",
	)
	titles = {}
	for row in rows:
		title = titles.get(row.course)
		if title is None:
			title = frappe.db.get_value("Training Course", row.course, "course_title") or row.course
			titles[row.course] = title
		row["course_title"] = title
		row["lesson_title"] = (
			frappe.db.get_value("Training Lesson", row.lesson, "lesson_title") if row.lesson else ""
		) or ""
	return rows


# --------------------------------------------------------------------- internal


def _graders():
	"""Every Training Manager, for the "waiting to be graded" nudge.

	Best-effort and deliberately broad: unlike a sign-off there is no single named
	supervisor to route to, so the queue's owners are told collectively.
	"""
	return [
		u
		for u in frappe.get_all(
			"Has Role",
			filters={"role": "Training Manager", "parenttype": "User"},
			pluck="parent",
		)
		if u and u not in ("Administrator", "Guest")
	]


def _notify(users, subject, body_html):
	"""Best-effort email + Notification Log; returns whether anything went out.

	Gated on ``Training Settings → Send Notifications`` like the rest of the
	module's mail, and silent during migrate/install/patch/import.
	"""
	if not users or _in_maintenance_context() or not notifications._enabled():
		return False
	recipients = users if isinstance(users, (list, tuple)) else [users]
	sent = False
	for user in recipients:
		recipient = notifications._recipient(user)
		if recipient and notifications._send(recipient, subject, body_html):
			sent = True
	return sent
