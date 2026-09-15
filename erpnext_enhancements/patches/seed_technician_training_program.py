"""Seed the ten Technician Program course drafts, and one badge for each.

**Every course is created as a Draft, and that is the whole safety property.**

``training/assignment.py`` selects courses to auto-assign with ``{"status": "Published", "weight":
"Required", "auto_assign": 1}`` -- all three conditions. These ten are Required and carry an
assignment rule aimed at the Technician job family, so a Draft is the only one of the three this
patch does not supply, and a Draft assigns nobody and emails nobody whatever the settings say.

Measured on production 2026-09-15: ``training_enabled = 1``, ``notifications_enabled = 1``,
``gamification_enabled = 1``, ``auto_assign_enabled = 0``, ``portal_enabled = 0``, against 18 live
courses. ``auto_assign_enabled`` was **1** a day earlier, when the Quality drafts were seeded -- it
is a checkbox a Training Manager can tick, so it is not the guardrail. The Draft status is.

**Publishing one is the act of adopting it**, and it will assign and email real technicians on the
next sweep. Nothing here publishes anything.

The courses are built through ``api/training_course_authoring.author_course_from_spec`` rather than
by creating documents directly, so every rule that path enforces applies -- in particular that each
quiz question is stamped ``ai_generated`` with **no reviewer**, which the publish gate refuses to let
through until a person has read it. Seventy-two lessons' worth of machine-written questions carry the
same gate as any other machine-written question. That is correct, not a technicality.

The badges are inert for the same reason the assignment is, by a longer chain worth writing down:
``gamification_enabled`` is 1, so awarding is live, but a ``Course Completed`` badge is earned when
``gamification._badge_is_earned`` finds its course among the learner's completions, a completion
means a submitted ``Training Completion``, and nobody completes a course that has never been
published. Adopting a course turns on its assignment, its certificate and its badge together.

Insert-only and idempotent, keyed on course title and badge name. Running it twice creates nothing
and overwrites nothing -- it will not reset a course somebody has corrected, and it will never
un-publish one somebody has adopted. A re-run after a partial failure completes what was missed:
a course that exists without its badge still gets its badge.
"""

import frappe

from erpnext_enhancements.training import technician_program as program

COURSE_DOCTYPES = ("Training Course", "Training Course Version", "Training Lesson")
BADGE_DOCTYPE = "Training Badge"


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy -- and the failure
	# is not a stopped deploy but a half-finished one. Every guard here returns quietly.
	for doctype in COURSE_DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			return

	created = []
	skipped = []

	for spec in program.COURSES:
		title = spec["course"]["course_title"]
		try:
			existing = frappe.db.get_value("Training Course", {"course_title": title}, "name")
			if existing:
				skipped.append(title)
				continue
			name = _build(spec, title)
		except Exception:
			# Belt and braces. `_build` already swallows, but this loop must survive anything --
			# including a failure in the existence check itself. Seeding draft courses is never
			# worth aborting a deploy over.
			frappe.log_error(
				title="Technician training course seed failed",
				message=f"{title}: {frappe.get_traceback()}",
			)
			continue
		if name:
			created.append(title)

	badges = _seed_badges()

	if created or badges:
		frappe.db.commit()

	print(
		f"seed_technician_training_program: {len(created)} courses created as DRAFT, "
		f"{len(skipped)} already present, {badges} badges created"
	)
	if created:
		print(
			"  These assign nobody until somebody publishes them. Publishing is the act of adopting "
			"a course, and it will assign and email on the next sweep."
		)


def _build(spec, title):
	"""Create one course. Returns its name, or ``None`` if it could not be built."""
	from erpnext_enhancements.api.training_course_authoring import author_course_from_spec

	try:
		result = author_course_from_spec(spec)
		name = result.get("course")
		if not name:
			return None
		# INSIDE the try, deliberately. In v1.464.0 the sibling patch made this call outside its
		# try, it threw, and it aborted `bench migrate` -- which on this repo is the production
		# deploy.
		_apply_course_settings(name, title)
		return name
	except Exception:
		# One bad course must not take the other nine down, and must never abort the migrate.
		frappe.log_error(
			title="Technician training course seed failed",
			message=f"{title}: {frappe.get_traceback()}",
		)
		return None


def _apply_course_settings(name, title):
	"""Weight and the assignment rule -- recorded, and inert while the course is a Draft.

	The rule is the design and belongs on the record; it does nothing until somebody publishes.
	Written through the document API rather than ``db.set_value`` because the child table needs the
	parent's own append, and this is ten documents on a migrate, not a bulk walk.

	No sign-off is set. Several of these modules describe practical competencies that a quiz cannot
	prove -- running a confined-space entry, making a solvent weld that holds -- and a supervisor
	sign-off would be the right instrument for them. But ``TrainingCourse._validate_signoff``
	refuses a course that asks for a signature without saying what the signature is *for*, and what
	a Sapphire supervisor is verifying is exactly the kind of thing this package does not get to
	invent. Whoever adopts a course sets it, with the criterion in their own words.
	"""
	rule = dict((entry[0], entry) for entry in program.assignment_rules()).get(title)
	doc = frappe.get_doc("Training Course", name)

	# Required is the declared intent: this is a technician training programme, not a library. It is
	# safe to set here because `status` stays Draft -- assignment needs Draft's opposite as well as
	# Required and auto_assign, and the first is absent.
	doc.weight = "Required"
	doc.auto_assign = 1

	if rule:
		_course, applies_to, value, due_days = rule
		if _target_exists(applies_to, value):
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
		else:
			# A rule pointing at a Position that does not exist would match nobody, silently,
			# forever. Skipping it loudly is better than storing a dead rule.
			frappe.log_error(
				title="Technician course assignment rule skipped",
				message=f"{title}: no {applies_to} named {value!r}",
			)

	doc.save(ignore_permissions=True)


def _seed_badges():
	"""One ``Course Completed`` badge per module. Returns how many were created.

	Separate from the course loop and keyed on badge name, so a re-run after a partial failure gives
	a badge to a course that already exists. Runs after every course, because it resolves the course
	by title.

	``Training Badge`` may not have migrated yet on a given site -- the same guard
	``training/setup.py`` has always carried for the starter badges, and for the same reason.
	"""
	if not frappe.db.exists("DocType", BADGE_DOCTYPE):
		return 0

	created = 0
	for course_title, badge_name, description, points, image in program.badges():
		try:
			if frappe.db.exists(BADGE_DOCTYPE, badge_name):
				continue
			course = frappe.db.get_value("Training Course", {"course_title": course_title}, "name")
			if not course:
				# The course could not be built, or somebody renamed it. A badge whose
				# `criteria_course` is empty is not earnable by anybody and not obviously broken to
				# anybody either, which is the worst of both -- so it is not created at all.
				frappe.log_error(
					title="Technician training badge skipped",
					message=f"{badge_name}: no Training Course titled {course_title!r}",
				)
				continue
			frappe.get_doc(
				{
					"doctype": BADGE_DOCTYPE,
					"badge_name": badge_name,
					"description": description,
					"criteria_type": "Course Completed",
					"criteria_course": course,
					"points": points,
					# A static app asset, not an uploaded File -- `image` is an Attach Image field
					# and stores a URL, so there is no File record to create and nothing to
					# re-parent. `bench build` puts the artwork at this path on every deploy.
					"image": image,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
			created += 1
		except Exception:
			# Badges are decoration; a deploy is not. Same rule as `training/setup.py`.
			frappe.log_error(
				title="Technician training badge seed failed",
				message=f"{badge_name}: {frappe.get_traceback()}",
			)
			continue
	return created


def _target_exists(applies_to, value):
	try:
		return bool(frappe.db.exists(applies_to, value))
	except Exception:
		return False
