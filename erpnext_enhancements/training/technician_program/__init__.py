# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Technician Training Program — ten course drafts and a badge for each.

Sapphire supplied a ten-module outline with seventy-two numbered topics under it. This package is
that outline turned into **Course Specs**: data, validated by
:mod:`~erpnext_enhancements.training.course_spec` and built by
``api/training_course_authoring.author_course_from_spec`` — the same reviewed path the AI drafting
tool and the four Quality drafts use. One module becomes one **Training Course**, one numbered topic
becomes one **Training Lesson**, and every lesson carries an end-of-lesson quiz.

Nothing here creates a document directly, so every rule that path enforces applies: the course is
created **Draft**, its version is unpublished, and **every quiz question is stamped ``ai_generated``
with no reviewer**, which the publish gate refuses to let through until a person has read it.

What these teach, and what they refuse to
-----------------------------------------

The four Quality drafts in :mod:`~erpnext_enhancements.training.quality_course_specs` could be
written from the code, because what they teach is how this software behaves. **These cannot.** They
teach a trade, and a trade course has a failure mode the software courses do not: a technician reads
a cure time, a torque figure or a dose rate here, does not check the label, and makes a joint that
fails under a slab in three years.

So the rule this package holds to is narrower than "do not invent Sapphire policy". It is:

* **Any number that belongs to a product, a manufacturer or a job is not printed.** Cure times,
  torques, dose rates, test pressures, anchor embedments, service intervals, chemical targets. The
  lesson teaches the principle and then says, in a ``_common.ask_block``, that the label / the data
  sheet / the submittal / the engineer decides.
* **Numbers that are universal are stated plainly**, because hedging a fact teaches nothing. Water
  weighs about 8.34 lb/gal. A Class A GFCI trips at roughly 4-6 mA. A DMX512 universe carries 512
  channels. Oxygen in a confined space is acceptable between about 19.5% and 23.5%.
* **Safety content defers.** Confined space, lock-out/tag-out, electrical work and first aid name the
  standard, teach what it is *for*, and say when to stop and get somebody qualified. None of it is
  written as a procedure anybody should follow instead of being trained.
* **No company policy.** No deadline, no frequency, no approval limit, no escalation path.
  ``tests/test_technician_training_program.py`` fails the build on any invented deadline of the form
  "within N days", the same guard the Quality drafts carry.

Why Draft is the load-bearing word
-----------------------------------

``training/assignment.py`` selects courses to auto-assign on ``{"status": "Published", "weight":
"Required", "auto_assign": 1}`` — all three. These ten are Required, they carry assignment rules, and
they are created Draft, so they assign nobody and email nobody.

Measured on production **2026-09-15**: ``training_enabled = 1``, ``notifications_enabled = 1``,
``gamification_enabled = 1``, ``auto_assign_enabled = 0``, ``portal_enabled = 0``, against 18 live
courses. Note that ``auto_assign_enabled`` was **1** when the Quality drafts were written a day
earlier — it is a checkbox a Training Manager can tick, so it is not a guardrail. The Draft status is.

**Publishing one is the act of adopting it**, it is a deliberate act by a named person, and with the
auto-assign switch on it will assign and email every Technician on the next sweep.

Why the badges are inert too, and it is the same reason
--------------------------------------------------------

``gamification_enabled`` is **1** on production, so badge awarding is live. The ten badges this
package seeds are nonetheless unearnable until the courses are adopted, and the chain is worth saying
out loud rather than assuming: a ``Course Completed`` badge is earned when
``gamification._badge_is_earned`` finds its ``criteria_course`` among the learner's completed
courses, a completed course means a submitted ``Training Completion``, and nobody can complete a
course that has never been published. One decision — adopting the course — turns on the assignment,
the certificate and the badge together.

Files
-----

``_common.py`` holds the draft notice and the two shared block helpers; ``module_01`` … ``module_10``
hold one ``COURSE`` spec each. The seeding patch is
``patches/seed_technician_training_program.py``.
"""

from erpnext_enhancements.training.technician_program import (
	module_01_piping,
	module_02_equipment,
	module_03_water_chemistry,
	module_04_materials,
	module_05_waterproofing,
	module_06_electrical,
	module_07_service_ops,
	module_08_troubleshooting,
	module_09_safety,
	module_10_design_pm,
)
from erpnext_enhancements.training.technician_program._common import DRAFT_NOTICE, ask_block, notice_block

#: Every course, in the order the patch creates them — which is the order of the outline, and the
#: order somebody would take them in.
COURSES = (
	module_01_piping.COURSE,
	module_02_equipment.COURSE,
	module_03_water_chemistry.COURSE,
	module_04_materials.COURSE,
	module_05_waterproofing.COURSE,
	module_06_electrical.COURSE,
	module_07_service_ops.COURSE,
	module_08_troubleshooting.COURSE,
	module_09_safety.COURSE,
	module_10_design_pm.COURSE,
)

#: Where the badge artwork is served from. These are static app assets rather than uploaded Files,
#: so there is no ``File`` record and no upload validation in play — the patch writes this path
#: straight into ``Training Badge.image``, which is an ``Attach Image`` field and stores a URL.
#:
#: **A raw ``/assets`` path is served immutable for a year with no content hash** — the same fact
#: behind this app's rule that global JS and CSS ship as esbuild bundles. An image referenced from a
#: database field has no bundling option, and it does not matter while the artwork never changes. It
#: matters the day somebody redraws one: an edit in place will not reach a browser that has already
#: cached it, for up to a year. **Give a redrawn badge a new filename** rather than overwriting.
BADGE_IMAGE_BASE = "/assets/erpnext_enhancements/images/training/badges"

#: One badge per module, which is what was asked for. ``criteria_type`` is **Course Completed** and
#: the patch resolves ``criteria_course`` to the course it just created — the only criterion in
#: ``gamification._badge_is_earned`` that means "this specific course", rather than a count that any
#: other course on the site would also satisfy.
#:
#: ``(badge_name, description, image file)``. The names carry no programme prefix, at Sapphire's
#: request — it reads better on a learner's profile, and it costs one thing worth writing down:
#: ``Training Badge`` autonames on ``badge_name`` and the field is **unique**, so these ten now hold
#: plain, generic names in a namespace shared with every badge anybody adds later. A collision is not
#: cosmetic; it is the patch silently failing to create that badge. They are clear of the five
#: starters in ``training/setup.py`` (``First Course``, ``Full Marks``, ``Five Courses``,
#: ``Ten Courses``, ``Steady Week``) — the whole of what exists today — and the tests assert it.
#:
#: A capstone "finished all ten" badge is deliberately **not** here. The mechanism that would carry
#: it, ``Category Completed``, means *every published course in the category*, and these ten share
#: their categories with the courses already on the site — so it would be earned by a set that
#: changes whenever somebody adds a course. A criterion whose meaning drifts is worse than no badge.
BADGE_SPECS = (
	(
		"Piping & Hydraulics",
		"Finished the piping module: solvent welding, bedding, pitch, pressure testing and penetrations.",
		"module-01-piping-hydraulics.svg",
	),
	(
		"Equipment Installation",
		"Finished the equipment module: pumps, filters, drains, gauges, chemical feed, heaters and anchors.",
		"module-02-equipment-installation.svg",
	),
	(
		"Water Chemistry",
		"Finished the water chemistry module: balance, testing, shocking, algae and scale.",
		"module-03-water-chemistry.svg",
	),
	(
		"Materials",
		"Finished the materials module: masonry, concrete, metals, mortar beds, tile and fasteners.",
		"module-04-materials.svg",
	),
	(
		"Waterproofing",
		"Finished the waterproofing module: membranes, sand broadcasting, submersible enclosures and vessels.",
		"module-05-waterproofing.svg",
	),
	(
		"Electrical & Controls",
		"Finished the electrical module: conduit, sensors, meters, GFCI, DMX and ladder logic.",
		"module-06-electrical-automation.svg",
	),
	(
		"Service Operations",
		"Finished the service module: vacuuming, winterisation and seasonal start-up.",
		"module-07-service-operations.svg",
	),
	(
		"Troubleshooting",
		"Finished the troubleshooting module: ten faults, and the method for working each one out.",
		"module-08-troubleshooting.svg",
	),
	(
		"Jobsite Safety",
		"Finished the safety module: vehicles, tools, confined space, lock-out/tag-out, codes, first aid and PPE.",
		"module-09-jobsite-safety.svg",
	),
	(
		"Design & Project Basics",
		"Finished the design module: components, diagrams, drawings, codes, bonding, LSI and working with customers.",
		"module-10-design-project-mgmt.svg",
	),
)

#: Points per lesson, used to price each badge. Derived rather than hand-typed, because the modules
#: are wildly different sizes — three lessons in Waterproofing against twelve in Design — and a flat
#: figure would say a technician's afternoon and their fortnight are worth the same.
#:
#: ``gamification`` is explicit that re-weighting silently reorders every historical board, so this
#: number has to stay put once anybody has earned one.
POINTS_PER_LESSON = 5

#: Auto-assignment targeting, applied by the patch and inert while every course is a Draft.
#:
#: ``Position: Technician`` is the job-family group above Junior / Senior / Master — verified present
#: on production 2026-09-15 — so one rule per course covers the whole family rather than reaching
#: two people at one tier.
#:
#: The due-day ladder is thirty days per module, and it encodes the **order** of the programme rather
#: than a commitment anybody has made. Whoever adopts these sets the real pace; what this records is
#: that Module 1 comes before Module 10.
ASSIGNMENT_POSITION = "Technician"
DUE_DAYS_PER_MODULE = 30


def course_titles():
	"""The ten titles, used by the patch to decide what already exists."""
	return tuple(spec["course"]["course_title"] for spec in COURSES)


def badges():
	"""``(course_title, badge_name, description, points, image)`` for each module, in order.

	Points are computed from the spec so the two cannot drift: adding a lesson to a module reprices
	its badge, and a badge can never be priced for a module that is not there. The image path is
	assembled here rather than stored, so the ten rows cannot disagree about where the artwork lives.
	"""
	return tuple(
		(
			spec["course"]["course_title"],
			name,
			description,
			POINTS_PER_LESSON * len(spec["lessons"]),
			f"{BADGE_IMAGE_BASE}/{image}",
		)
		for spec, (name, description, image) in zip(COURSES, BADGE_SPECS, strict=True)
	)


def assignment_rules():
	"""``(course_title, applies_to, value, due_days)`` for each module, in order."""
	return tuple(
		(
			spec["course"]["course_title"],
			"Position",
			ASSIGNMENT_POSITION,
			DUE_DAYS_PER_MODULE * (index + 1),
		)
		for index, spec in enumerate(COURSES)
	)
