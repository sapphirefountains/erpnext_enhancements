# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The AI question review queue — the screen the publish gate has always implied.

Every question an AI path creates is stamped ``ai_generated`` with **no** reviewer, and
``api/training_author._unreviewed_ai_questions`` is what ``submit_for_review`` and
``publish_version`` both refuse on. That gate has existed since Phase 4 and works. What has
never existed is anywhere to *do* the reviewing.

Measured on production 2026-09-15: **128 AI-drafted questions unreviewed, across all 11 Draft
courses**, against 14 ever reviewed. Every one of those 11 courses is Draft because of this gate.
The ten Technician Program drafts add 239 more. So this is not a screen for one release's
backlog; it is the missing half of a gate the app has shipped for months.

Three things here are deliberate and worth not undoing.

**The reviewer is taken from the session, never from the caller.** Until now the only thing that
ever flipped ``ai_reviewed_by`` on an already-persisted question was the browser — the authoring
canvas calls core ``frappe.client.save`` with ``body.ai_reviewed_by = frappe.session.user``. That
works, but it makes the attestation *caller-supplied*: the one field whose entire job is recording
**who vouched for this answer key** was being set by the client that wanted it set. Here the field
is written server-side from ``frappe.session.user`` and the payload cannot name a reviewer. A
signature you can address to somebody else is not a signature.

**Reject means the question leaves the course, because that is the only thing that clears the
gate.** ``submit_for_review`` tells the author to "accept or reject each one", and there has never
been a reject path — nor is there anywhere to record one: ``Training Question`` carries
``ai_generated`` and ``ai_reviewed_by`` and nothing else. A "Rejected" state that left
``ai_reviewed_by`` unset would be indistinguishable from "nobody has looked at it yet" and would go
on blocking publication forever; one that *set* it would let a question somebody rejected go live.
So rejecting drops the question from the lesson's quiz pool, deletes it if no other pool wants it,
and writes the reason to the course version's timeline. No new field, no patch, no migration — and
the audit trail lands where a reader would look for it.

**There is no bulk accept, and that is the point.** Reviewing 367 questions one at a time is the
work; a button that clears a course in one click turns the whole gate into theatre, and the gate is
the only thing standing between a machine-written answer key and somebody's compliance record. What
this module does instead is make the *unit of review the lesson* — you read a lesson once and judge
the two to four questions drawn from it together — and hand the page everything it needs in one
call, so the cost per question is a keystroke rather than a page load.

Indentation is tabs, matching the ``training/`` package. The endpoints live here rather than in
``api/`` for the same reason ``analytics.get_training_analytics`` does: this is a Desk console's own
server half, and ``api/training.py`` carries a boundary contract (every reply key must be read by
the learner's ``transport.js``) that an author-facing payload would immediately violate.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, escape_html, sanitize_html

# The predicate the publish gate blocks on, restated here as data so the queue cannot drift from
# it. `_unreviewed_ai_questions` builds the same two keys inline; `tests/test_training_review.py`
# extracts that literal from its source and asserts it equals this dict. A queue that showed a
# different number from the thing refusing to publish would be worse than no queue.
PENDING_FILTER = {"ai_generated": 1, "ai_reviewed_by": ["is", "not set"]}

# Who may review. Neither existing constant fits: `submissions.MANAGER_ROLES` is
# {Training Manager, System Manager} and `permissions.UNSCOPED_ROLES` deliberately excludes
# Training Author with a comment saying so. But reviewing a draft question is *authoring* work —
# it is the same person, on the same draft, in the same afternoon — and Training Author is exactly
# the role the canvas grants for it. So this is a third set, and it matches `api`'s AUTHOR_ROLES
# and the `roles` on both training-canvas.json and training-review.json.
REVIEWER_ROLES = {"Training Author", "Training Manager", "System Manager"}

#: Block types whose body is author HTML. Everything else is media the reviewer does not need in
#: order to judge a question, and which this page deliberately does not fetch — see `_blocks`.
HTML_BLOCKS = ("Rich Text", "Callout")

#: Interactive list blocks, whose content lives in the `data` JSON column.
LIST_BLOCKS = ("Checklist", "Flashcards", "Accordion")

#: How much of a rejection reason is kept. Long enough for a real explanation, short enough that
#: the timeline stays readable — the same stance `submissions.grade_submission` takes on feedback.
MAX_REASON = 2000


# ---------------------------------------------------------------------------- guards


def _reviewer():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	if not (REVIEWER_ROLES & set(frappe.get_roles(user))):
		frappe.throw(
			_("Only a Training Author or Training Manager can review drafted questions."),
			frappe.PermissionError,
		)
	return user


def _is_reviewer(user):
	return bool(REVIEWER_ROLES & set(frappe.get_roles(user)))


def _pending_names(names=None):
	"""Names of AI-drafted questions nobody has accepted, optionally within a candidate set.

	``frappe.get_all`` sets ``ignore_permissions=True`` unconditionally, so the role gate above is
	the only thing keeping this data in the right hands. Every caller here goes through it.
	"""
	filters = dict(PENDING_FILTER)
	if names is not None:
		if not names:
			return []
		filters["name"] = ["in", list(names)]
	return frappe.get_all("Training Question", filters=filters, pluck="name")


# ---------------------------------------------------------------------------- the queue


@frappe.whitelist()
def get_review_queue():
	"""What is left to review, by course.

	Scoped to **draft** versions (``docstatus == 0``) on purpose. A published version is frozen —
	``_draft()`` refuses it — so an unreviewed question inside one could not be accepted or removed
	even if it existed, and showing it would be offering work nobody can do. Measured on production
	2026-09-15 there are none, which is the gate doing its job.
	"""
	_reviewer()

	pending = _pending_names()
	if not pending:
		return {"total": 0, "courses": [], "reviewed": _reviewed_count()}

	rows = frappe.get_all(
		"Training Quiz Question",
		filters={"question": ["in", pending], "parenttype": "Training Lesson"},
		fields=["question", "parent"],
	)
	lessons = _lesson_index({r.parent for r in rows})

	# question -> the lessons that draw it. A question shared by two lessons is counted once per
	# course it actually appears in, because that is where the reviewer will meet it.
	by_course = {}
	for row in rows:
		lesson = lessons.get(row.parent)
		if not lesson:
			continue
		bucket = by_course.setdefault(
			lesson["course"],
			{
				"course": lesson["course"],
				"course_title": lesson["course_title"],
				"course_version": lesson["course_version"],
				"course_status": lesson["course_status"],
				"questions": set(),
				"lessons": set(),
			},
		)
		bucket["questions"].add(row.question)
		bucket["lessons"].add(row.parent)

	courses = [
		{
			"course": c["course"],
			"course_title": c["course_title"],
			"course_version": c["course_version"],
			"course_status": c["course_status"],
			"pending": len(c["questions"]),
			"lessons": len(c["lessons"]),
		}
		for c in by_course.values()
	]
	courses.sort(key=lambda c: (-c["pending"], c["course_title"]))

	return {
		"total": sum(c["pending"] for c in courses),
		"courses": courses,
		"reviewed": _reviewed_count(),
	}


def _reviewed_count():
	return frappe.db.count("Training Question", {"ai_generated": 1, "ai_reviewed_by": ["is", "set"]})


def _lesson_index(lesson_names):
	"""``{lesson: {...course context...}}`` for lessons on an unpublished version.

	Three reads rather than one per lesson: `get_submission_queue` does a `db.get_value` per row for
	its lesson title, which at this scale would be several hundred round trips.
	"""
	if not lesson_names:
		return {}
	lessons = frappe.get_all(
		"Training Lesson",
		filters={"name": ["in", list(lesson_names)]},
		fields=[
			"name",
			"lesson_title",
			"course_version",
			"chapter_key",
			"idx_in_chapter",
			"has_quiz",
			"creation",
		],
	)
	versions = {
		v.name: v
		for v in frappe.get_all(
			"Training Course Version",
			filters={"name": ["in", list({l.course_version for l in lessons})], "docstatus": 0},
			fields=["name", "course", "version_number"],
		)
	}
	courses = {
		c.name: c
		for c in frappe.get_all(
			"Training Course",
			filters={"name": ["in", list({v.course for v in versions.values()})]},
			fields=["name", "course_title", "status"],
		)
	}

	index = {}
	for lesson in lessons:
		version = versions.get(lesson.course_version)
		if not version:
			# A published (or cancelled) version. Frozen, so there is nothing to review here.
			continue
		course = courses.get(version.course)
		if not course:
			continue
		index[lesson.name] = {
			"lesson": lesson.name,
			"lesson_title": lesson.lesson_title,
			"course_version": lesson.course_version,
			"version_number": version.version_number,
			"course": course.name,
			"course_title": course.course_title,
			"course_status": course.status,
			"chapter_key": lesson.chapter_key,
			"idx_in_chapter": cint(lesson.idx_in_chapter),
			"has_quiz": cint(lesson.has_quiz),
			"creation": lesson.creation,
		}
	return index


# ---------------------------------------------------------------------------- one lesson


@frappe.whitelist()
def get_review_lesson(lesson=None, course=None):
	"""The next lesson with questions to review, with everything needed to judge them.

	**The lesson is the unit, and that is the whole design.** The question a reviewer cannot answer
	from a list is *"could a learner get this from the lesson?"* — so the lesson's own content comes
	back with its questions, and the page shows them side by side. Reviewing a quiz without the
	lesson in front of you is proof-reading, not reviewing.

	Pass ``lesson`` to reopen a specific one, ``course`` to stay inside a course, or neither to be
	handed whatever is next.
	"""
	_reviewer()

	target = _next_lesson(lesson=lesson, course=course)
	if not target:
		return {"lesson": None, "queue": get_review_queue()}

	pool = frappe.get_all(
		"Training Quiz Question",
		filters={"parent": target["lesson"], "parenttype": "Training Lesson"},
		fields=["question", "points", "is_required", "idx"],
		order_by="idx asc",
	)
	pending = set(_pending_names([r.question for r in pool]))

	questions = _questions([r.question for r in pool if r.question in pending])
	context = dict(target)
	context.update(
		{
			"blocks": _blocks(target["lesson"]),
			"questions": questions,
			"pool_size": len(pool),
			"pending_in_lesson": len(questions),
		}
	)
	return {"lesson": context, "queue": get_review_queue()}


def _next_lesson(lesson=None, course=None):
	"""Resolve the lesson to show: a named one, else the first pending one in reading order."""
	if lesson:
		index = _lesson_index({lesson})
		return index.get(lesson)

	pending = _pending_names()
	if not pending:
		return None
	rows = frappe.get_all(
		"Training Quiz Question",
		filters={"question": ["in", pending], "parenttype": "Training Lesson"},
		fields=["parent"],
	)
	index = _lesson_index({r.parent for r in rows})
	candidates = [c for c in index.values() if not course or c["course"] == course]
	if not candidates:
		return None
	# Reading order within a course, so a reviewer works through a course the way a learner would
	# rather than in whatever order the child table happened to come back.
	candidates.sort(
		key=lambda c: (c["course_title"], c["chapter_key"] or "", c["idx_in_chapter"], c["creation"])
	)
	return candidates[0]


def _blocks(lesson):
	"""The lesson's readable content, sanitised, with media deliberately left out.

	A reviewer needs the *words* — what the lesson actually taught — to judge whether a question is
	answerable from it. Images, PDFs and video are not fetched: they cost a signed URL each and none
	of them changes whether a quiz option is defensible. ``block_type`` still comes back for every
	block, so the page can say "[Video]" and the reviewer knows something is there.

	The HTML is author-written and is rendered into the Desk, so it is sanitised on the way out
	rather than trusted. These are the same strings the learner sees, but the learner sees them
	through the player; nothing else here has cleaned them for a manager's browser.
	"""
	rows = frappe.get_all(
		"Training Content Block",
		filters={"parent": lesson, "parenttype": "Training Lesson"},
		fields=["block_key", "block_type", "heading", "content", "callout_tone", "caption", "data"],
		order_by="idx asc",
	)
	out = []
	for row in rows:
		block = {
			"block_key": row.block_key,
			"block_type": row.block_type,
			"heading": row.heading or "",
			"callout_tone": row.callout_tone or "",
			"caption": row.caption or "",
			"html": "",
			"items": [],
		}
		if row.block_type in HTML_BLOCKS and row.content:
			block["html"] = sanitize_html(row.content, always_sanitize=True)
		elif row.block_type in LIST_BLOCKS:
			block["items"] = _list_items(row.block_type, row.data)
		out.append(block)
	return out


def _list_items(block_type, raw):
	"""Flatten a Checklist / Flashcards / Accordion into plain strings.

	The reviewer wants to read them, not interact with them, so the three shapes collapse to one.
	A malformed `data` column yields nothing rather than raising: a review screen that will not
	open because one lesson has bad JSON helps nobody.
	"""
	try:
		parsed = json.loads(raw) if raw else []
	except (ValueError, TypeError):
		return []
	if not isinstance(parsed, list):
		return []

	items = []
	for entry in parsed:
		if isinstance(entry, str):
			items.append(entry)
		elif isinstance(entry, dict):
			if block_type == "Flashcards":
				items.append(f"{entry.get('front', '')} — {entry.get('back', '')}".strip(" —"))
			elif block_type == "Accordion":
				items.append(f"{entry.get('title', '')} — {entry.get('body', '')}".strip(" —"))
			else:
				items.append(str(entry.get("text") or entry.get("label") or ""))
	return [sanitize_html(i, always_sanitize=True) for i in items if i]


def _questions(names):
	"""The questions themselves, answer key included.

	**This payload carries ``is_correct`` on purpose** — it is the thing being reviewed, and the
	whole point of the screen is that a person looks at the key and says yes or no. That is safe
	here and nowhere near the learner boundary: this module is author-facing, gated on
	``REVIEWER_ROLES``, and reachable only from a Desk page whose `roles` are the same three. The
	learner's answer-key-stays-server-side guarantee lives in ``grading`` and ``_split_lesson`` and
	is untouched by anything here — which is exactly why this payload is built from scratch rather
	than by reusing a learner-facing helper.
	"""
	if not names:
		return []
	rows = frappe.get_all(
		"Training Question",
		filters={"name": ["in", list(names)]},
		fields=[
			"name",
			"question_text",
			"question_type",
			"explanation",
			"difficulty",
			"points",
			"correct_text_answers",
			"ai_generated",
			"ai_reviewed_by",
			"ai_model",
			"ai_source",
		],
	)
	options = {}
	for opt in frappe.get_all(
		"Training Answer Option",
		filters={"parent": ["in", list(names)], "parenttype": "Training Question"},
		fields=["parent", "option_key", "option_text", "is_correct", "explanation"],
		order_by="parent asc, idx asc",
	):
		options.setdefault(opt.parent, []).append(
			{
				"option_key": opt.option_key or "",
				"option_text": opt.option_text or "",
				"is_correct": cint(opt.is_correct),
				"explanation": opt.explanation or "",
			}
		)

	order = list(names)
	rows.sort(key=lambda r: order.index(r.name))
	return [
		{
			"question": r.name,
			"question_text": r.question_text or "",
			"question_type": r.question_type or "",
			"explanation": r.explanation or "",
			"difficulty": r.difficulty or "",
			"points": cint(r.points),
			"correct_text_answers": r.correct_text_answers or "",
			"ai_model": r.ai_model or "",
			# Empty on every question the course seeder wrote -- only `accept_ai_suggestions`
			# records a grounding quote. The page must not lead with a field that is blank 367
			# times out of 367.
			"ai_source": r.ai_source or "",
			"options": options.get(r.name, []),
		}
		for r in rows
	]


# ---------------------------------------------------------------------------- verdicts


@frappe.whitelist()
def accept_question(question, question_text=None, explanation=None, options=None):
	"""Accept a drafted question, optionally fixing it on the way through.

	Correcting and accepting is one action rather than two because it is one decision: a reviewer
	who spots a wrong key fixes it and vouches for the result. Splitting them would leave a window
	where the question is edited but still unreviewed, and a reviewer who edits and then forgets.

	Edits go through ``doc.save()``, so ``TrainingQuestion.validate`` runs — option bounds, exactly
	one correct answer on a Single Choice, no two options reading the same. A spec that would not
	survive ``insert()`` does not survive this either.
	"""
	user = _reviewer()
	doc = frappe.get_doc("Training Question", question)

	if not cint(doc.ai_generated):
		# Nothing to accept. `ai_reviewed_by` means "a human accepted the machine's answer key", and
		# setting it on a hand-written question would put a reviewer's name against work nobody
		# drafted for them -- and `training_ai` is explicit that the two fields move together.
		frappe.throw(_("That question was not AI-drafted, so there is nothing to accept."))

	if question_text is not None:
		text = (question_text or "").strip()
		if not text:
			frappe.throw(_("A question cannot be left without any text."))
		doc.question_text = text
	if explanation is not None:
		doc.explanation = (explanation or "").strip()
	if options is not None:
		_apply_options(doc, options)

	# The attestation, taken from the session and never from the payload. This is the one field on
	# the record whose job is to say who vouched for the answer key.
	doc.ai_reviewed_by = user
	doc.save(ignore_permissions=True)

	return {"question": doc.name, "reviewed_by": user, "remaining": _remaining_summary()}


def _apply_options(doc, options):
	"""Replace the option table, preserving ``option_key`` wherever the caller sent one.

	Everything in this module joins on stable keys rather than position, the same discipline the
	rest of Training keeps -- a re-minted key on a question a learner is part-way through would
	strand their answer. These drafts are unpublished so nothing is in flight today, but the cost of
	being careful is one line.
	"""
	if isinstance(options, str):
		try:
			options = json.loads(options)
		except (ValueError, TypeError):
			frappe.throw(_("The options are not valid JSON."))
	if not isinstance(options, list) or not options:
		frappe.throw(_("A question needs its options."))

	doc.set("options", [])
	for raw in options:
		if not isinstance(raw, dict):
			frappe.throw(_("Each option must be an object."))
		doc.append(
			"options",
			{
				"option_key": (raw.get("option_key") or "").strip(),
				"option_text": (raw.get("option_text") or "").strip(),
				"is_correct": 1 if cint(raw.get("is_correct")) else 0,
				"explanation": (raw.get("explanation") or "").strip(),
			},
		)


@frappe.whitelist()
def reject_question(question, reason, drop_quiz=0):
	"""Take a drafted question out of the course, and say why.

	Rejecting removes the question from every **draft** lesson pool that draws it, and deletes the
	question itself if nothing else wants it. That is what "reject" has to mean: ``Training
	Question`` has nowhere to record a verdict, and the publish gate reads *"ai_generated with no
	reviewer"* -- so a rejected question left in the pool would block publication for ever, and one
	marked reviewed would go live.

	``drop_quiz`` is the reviewer confirming a consequence rather than a flag to pass by default.
	``TrainingLesson._validate_quiz`` throws when ``has_quiz`` is ticked and the pool is empty, so
	removing a lesson's **last** question would make that lesson unsaveable -- by anybody, until
	somebody works out why. Rather than quietly unticking the box (which silently turns a lesson
	that was meant to be assessed into one that is not), the endpoint refuses and says so; passing
	``drop_quiz`` unticks it as a decision the reviewer has taken.
	"""
	user = _reviewer()
	reason = (reason or "").strip()
	if not reason:
		# Same stance as a Needs Rework verdict in `submissions`: the negative verdict is the one
		# that is useless without words, and this one destroys the evidence of what was rejected.
		frappe.throw(_("Say why you are rejecting it — the question itself is about to be gone."))
	if len(reason) > MAX_REASON:
		frappe.throw(_("Keep the reason under {0} characters.").format(MAX_REASON))

	doc = frappe.get_doc("Training Question", question)
	if not cint(doc.ai_generated):
		frappe.throw(_("That question was not AI-drafted. Remove it from the course canvas instead."))
	if doc.ai_reviewed_by:
		frappe.throw(_("That question has already been accepted by {0}.").format(doc.ai_reviewed_by))

	rows = frappe.get_all(
		"Training Quiz Question",
		filters={"question": question, "parenttype": "Training Lesson"},
		fields=["parent"],
	)
	index = _lesson_index({r.parent for r in rows})
	if not index:
		frappe.throw(_("That question is not in any draft lesson, so there is nothing to remove it from."))

	emptied = [
		ctx["lesson_title"] for name, ctx in index.items() if ctx["has_quiz"] and _pool_size(name) <= 1
	]
	if emptied and not cint(drop_quiz):
		frappe.throw(
			_(
				"This is the last question in {0}. Rejecting it leaves that lesson with a quiz and "
				"nothing to ask, which the lesson will refuse to save. Confirm that the lesson "
				"should have no quiz."
			).format(", ".join(emptied))
		)

	touched = []
	for name, ctx in index.items():
		lesson = frappe.get_doc("Training Lesson", name)
		lesson.set("quiz_questions", [r for r in lesson.quiz_questions if r.question != question])
		if cint(drop_quiz) and not lesson.quiz_questions:
			lesson.has_quiz = 0
		lesson.save(ignore_permissions=True)
		touched.append(ctx)

	_record_rejection(doc, touched, reason, user)

	# Only now, and only if nothing else draws it. A bank question is somebody's reusable asset and
	# is not this reviewer's to delete.
	orphaned = not frappe.db.exists("Training Quiz Question", {"question": question})
	if orphaned and not cint(doc.is_bank_question):
		frappe.delete_doc("Training Question", question, ignore_permissions=True, delete_permanently=False)

	return {
		"question": question,
		"removed_from": [c["lesson_title"] for c in touched],
		"deleted": bool(orphaned and not cint(doc.is_bank_question)),
		"remaining": _remaining_summary(),
	}


def _pool_size(lesson):
	return frappe.db.count("Training Quiz Question", {"parent": lesson, "parenttype": "Training Lesson"})


def _record_rejection(doc, contexts, reason, user):
	"""Write the rejection to the course version's timeline.

	The question is about to be deleted, so this comment is the only surviving record that it was
	ever drafted and why somebody threw it out. It goes on the **course version** rather than the
	lesson because that is the document an auditor opens when asking what this release contains, and
	because a lesson can be deleted while the version stays.
	"""
	body = escape_html(doc.question_text or "")
	detail = escape_html(reason)
	where = ", ".join(escape_html(c["lesson_title"]) for c in contexts)
	for version in {c["course_version"] for c in contexts}:
		try:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Comment",
					"reference_doctype": "Training Course Version",
					"reference_name": version,
					"content": (
						f"<p><b>AI-drafted question rejected</b> by {escape_html(user)} "
						f"from {where}.</p><p><i>{body}</i></p><p>Reason: {detail}</p>"
					),
				}
			).insert(ignore_permissions=True)
		except Exception:
			# The removal is the substance and it has already happened. Losing the note is bad;
			# failing the whole rejection because the timeline would not take a comment is worse.
			frappe.log_error(
				title="Training review: rejection note not recorded",
				message=f"{doc.name} on {version}\n{frappe.get_traceback()}",
			)


def _remaining_summary():
	"""How much is left, for the page's progress line. Cheap enough to return on every verdict."""
	return {
		"total": len(_pending_names()),
		"reviewed": _reviewed_count(),
	}
