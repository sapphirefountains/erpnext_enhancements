"""Bring the ten Technician Program drafts up to date with Sapphire's own module documents.

v1.467.0 seeded these ten courses from specs written against the outline, from general trade
practice. Sapphire has since supplied its **own training document** for most of the modules, the
specs have been rewritten against them, and this is what makes that reach a site that already has
the courses.

**It exists because the seeder cannot do it.** ``seed_technician_training_program`` is insert-only
and keyed on course title, so it skips a course that is already there — correctly, since that is
what stops it resetting work somebody has done. And ``author_course_from_spec`` can only create:
``create_draft_version`` refuses outright when an open draft exists. So editing a spec file changes
what a *fresh install* gets and **nothing at all** on production. Without this patch the rewrite
would have been invisible on the only site that matters.

The guards, and what each one is protecting
--------------------------------------------

A rebuild deletes every lesson on the draft and builds new ones, which re-mints ``lesson_key`` and
every ``block_key``. Everything in this module joins on those keys. So a course is rebuilt **only**
when all four of these hold, and each is checked per course rather than once:

* **Still ``Draft``.** Publishing is the act of adopting a course; a published one has learners and
  a frozen version, and is none of this patch's business.
* **No AI question reviewed yet.** ``ai_reviewed_by`` set on any question in the draft means a
  person has started working through the review queue on it. Their work is not ours to delete.
* **Nobody has attempted it.** A ``Training Attempt`` means somebody is part-way through, and
  re-minting the keys would strand exactly where they are.
* **Nobody has completed it.** A ``Training Completion`` is an audit record of what somebody
  passed; rebuilding the content under one makes that record refer to material that no longer
  exists.

Any course failing any of those is **skipped and named** in the output. A silent skip here would be
indistinguishable from a rebuild that worked.

Re-running is safe: a rebuild makes the draft match the spec, so a second run makes it match again.
"""

import frappe

from erpnext_enhancements.training import technician_program as program

REQUIRED_DOCTYPES = ("Training Course", "Training Course Version", "Training Lesson")


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy -- and the
	# failure is not a stopped deploy but a half-finished one. Every guard here returns quietly.
	for doctype in REQUIRED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			return

	rebuilt = []
	skipped = []

	for spec in program.COURSES:
		title = spec["course"]["course_title"]
		try:
			course = frappe.db.get_value("Training Course", {"course_title": title}, "name")
			if not course:
				# Not seeded on this site yet. `seed_technician_training_program` runs and will
				# create it from the rewritten spec, so there is nothing to bring up to date.
				continue

			reason = _unsafe_reason(course)
			if reason:
				skipped.append(f"{title}: {reason}")
				continue

			result = _rebuild(course, spec, title)
			if result:
				rebuilt.append(f"{title} ({result['lessons']} lessons, {result['questions']} questions)")
		except Exception:
			# One bad course must not take the other nine down, and must never abort the migrate.
			frappe.log_error(
				title="Technician course rebuild failed",
				message=f"{title}: {frappe.get_traceback()}",
			)
			continue

	if rebuilt:
		frappe.db.commit()

	print(f"rebuild_technician_course_drafts: {len(rebuilt)} rebuilt, {len(skipped)} left alone")
	for line in rebuilt:
		print(f"  rebuilt: {line}")
	for line in skipped:
		# Named, never silent: a skip that looks like a success is how somebody concludes the
		# rewrite landed when it did not.
		print(f"  skipped: {line}")


def _unsafe_reason(course):
	"""Why this course must not be rebuilt, or ``''`` when it is safe.

	Returns the reason rather than a boolean so the output can say which guard stopped it. "Skipped"
	on its own tells nobody whether the course was adopted, reviewed or being taken.
	"""
	status = frappe.db.get_value("Training Course", course, "status")
	if status != "Draft":
		return f"status is {status}, so somebody has adopted it"

	draft_version = frappe.db.get_value("Training Course Version", {"course": course, "docstatus": 0}, "name")
	if not draft_version:
		return "there is no open draft version to rebuild"

	lessons = frappe.get_all("Training Lesson", filters={"course_version": draft_version}, pluck="name")
	if lessons:
		questions = frappe.get_all(
			"Training Quiz Question",
			filters={"parent": ["in", lessons], "parenttype": "Training Lesson"},
			pluck="question",
		)
		if questions:
			reviewed = frappe.db.count(
				"Training Question",
				{"name": ["in", questions], "ai_generated": 1, "ai_reviewed_by": ["is", "set"]},
			)
			if reviewed:
				return f"{reviewed} question(s) have already been reviewed by somebody"

	if frappe.db.exists("DocType", "Training Attempt") and frappe.db.count(
		"Training Attempt", {"course": course}
	):
		return "somebody has started taking it"

	if frappe.db.exists("DocType", "Training Completion") and frappe.db.count(
		"Training Completion", {"course": course}
	):
		return "somebody has completed it"

	return ""


def _rebuild(course, spec, title):
	"""Rebuild one course's draft. Returns the result dict, or ``None``."""
	from erpnext_enhancements.api.training_course_authoring import rebuild_draft_from_spec

	try:
		return rebuild_draft_from_spec(course, spec)
	except Exception:
		frappe.log_error(
			title="Technician course rebuild failed",
			message=f"{title}: {frappe.get_traceback()}",
		)
		return None
