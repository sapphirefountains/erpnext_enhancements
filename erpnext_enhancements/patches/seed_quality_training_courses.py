"""Seed the four Quality course drafts — WI-075, v1.464.0.

**Every course is created as a Draft, and that is the whole safety property.**

``training/assignment.py`` selects courses to auto-assign with
``{"status": "Published", "weight": "Required", "auto_assign": 1}`` — all three conditions. A
Draft course therefore assigns nobody and emails nobody, whatever the Training Settings say.

That matters more than the plan expected. The plan assumed the module was dormant:

    *Training Settings ships dormant (training_enabled = 0, auto_assign_enabled = 0). Four
    Required courses assign nothing and mail nobody while those are off.*

Measured on production 2026-09-14, that is **no longer true**: ``training_enabled = 1``,
``auto_assign_enabled = 1`` and ``portal_enabled = 1``, against 14 live courses, 278 lessons and
123 questions. Training is in real use. So the dormancy the plan leaned on is gone, and the Draft
status is the only thing between these drafts and a real assignment in a real technician's inbox.

**Publishing one is the act of adopting it**, it is a deliberate act by a named person, and it
will assign and email immediately on the next sweep. Nothing here publishes anything.

Built through ``api/training_course_authoring.author_course_from_spec`` rather than by creating
documents directly, so every rule that path enforces applies — in particular that **each quiz
question is stamped ``ai_generated`` with no reviewer**, which the publish gate refuses to let
through until a person has read it. These questions were machine-written and carry the same gate
as any other machine-written question. That is correct, not a technicality.

Insert-only and idempotent, keyed on course title. Running it twice creates nothing and
overwrites nothing — it will not reset a course somebody has corrected, and it will never
un-publish one somebody has adopted.
"""

import frappe

from erpnext_enhancements.training import quality_course_specs as specs

REQUIRED_DOCTYPES = ("Training Course", "Training Course Version", "Training Lesson")


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy -- and the
	# failure is not a stopped deploy but a half-finished one. Every guard here returns quietly.
	for doctype in REQUIRED_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			return

	created = []
	skipped = []

	for spec in specs.COURSES:
		title = spec["course"]["course_title"]
		try:
			if frappe.db.exists("Training Course", {"course_title": title}):
				skipped.append(title)
				continue
			name = _build(spec, title)
		except Exception:
			# Belt and braces. `_build` already swallows, but this loop must survive anything --
			# including a failure in the existence check itself. Seeding four draft courses is
			# never worth aborting a deploy over.
			frappe.log_error(
				title="Quality training course seed failed",
				message=f"{title}: {frappe.get_traceback()}",
			)
			continue
		if name:
			created.append(title)

	if created:
		frappe.db.commit()

	print(
		f"seed_quality_training_courses: {len(created)} created as DRAFT, "
		f"{len(skipped)} already present"
	)
	if created:
		print(
			"  These assign nobody until somebody publishes them. Publishing is the act of "
			"adopting a course, and it will assign and email on the next sweep."
		)


def _build(spec, title):
	"""Create one course. Returns its name, or ``None`` if it could not be built."""
	from erpnext_enhancements.api.training_course_authoring import author_course_from_spec

	try:
		result = author_course_from_spec(spec)
		name = result.get("course")
		if not name:
			return None
		# INSIDE the try, deliberately. In v1.464.0 this call sat outside it, threw
		# ValidationError because the course asked for a supervisor signature with no stated
		# criterion, and aborted `bench migrate` -- which on this repo is the production deploy.
		_apply_course_settings(name, title)
		return name
	except Exception:
		# One bad course must not take the other three down, and must never abort the migrate.
		frappe.log_error(
			title="Quality training course seed failed",
			message=f"{title}: {frappe.get_traceback()}",
		)
		return None


def _apply_course_settings(name, title):
	"""Weight, sign-off and assignment rules — recorded, and inert while the course is a Draft.

	The rules are the design and belong on the record; they do nothing until somebody publishes.
	Written through the document API rather than ``db.set_value`` because the child table needs
	the parent's own append, and this is four documents on a migrate, not a bulk walk.
	"""
	doc = frappe.get_doc("Training Course", name)

	# Required is the declared intent from WI-075. It is safe to set here because `status` stays
	# Draft -- assignment needs Published AND Required AND auto_assign, and the first is absent.
	doc.weight = "Required"
	doc.auto_assign = 1

	instructions = specs.REQUIRES_SIGNOFF.get(title)
	if instructions:
		# A practical competency: passing a quiz about the wizard is not evidence somebody can
		# run an inspection. `training/authority.py` already knows who may sign.
		#
		# The instructions are set together with the flag because the controller refuses one
		# without the other -- "a sign-off with no stated criterion is a signature on nothing".
		# Reading both from a single mapping is what stops them drifting apart again.
		doc.require_supervisor_signoff = 1
		doc.signoff_supervisor_source = "Reports To"
		doc.signoff_instructions = instructions

	for applies_to, value, due_days in specs.ASSIGNMENT_RULES.get(title, ()):
		if not _target_exists(applies_to, value):
			# A rule pointing at a Position or Role that does not exist would match nobody,
			# silently, forever. Skipping it loudly is better than storing a dead rule.
			frappe.log_error(
				title="Quality course assignment rule skipped",
				message=f"{title}: no {applies_to} named {value!r}",
			)
			continue
		doc.append(
			"assign_rules",
			{
				"enabled": 1,
				"applies_to": applies_to,
				"applies_to_doctype": applies_to,
				"applies_to_value": value,
				"due_days": due_days,
			},
		)

	doc.save(ignore_permissions=True)


def _target_exists(applies_to, value):
	try:
		return bool(frappe.db.exists(applies_to, value))
	except Exception:
		return False
