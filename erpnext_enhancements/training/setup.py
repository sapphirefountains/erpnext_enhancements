# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Migrate-time provisioning for Training — starter categories and badges.

Insert-only and idempotent: a category an admin renamed or deleted stays that
way. The point is only that the builder's category picker is never empty on a
fresh site, because an empty picker reads as a broken form.
"""

import frappe

# Chosen from what this business actually trains on — fountain service, water
# chemistry and the safety that goes with both — rather than generic LMS filler.
STARTER_CATEGORIES = (
	("Safety", "Site safety, PPE, confined space, electrical and chemical handling."),
	("Water Chemistry", "Treatment, dosing, testing and balancing."),
	("Service & Maintenance", "Routine visits, troubleshooting and repairs."),
	("Installation", "Build, plumbing and commissioning."),
	("Systems & Admin", "ERPNext, timekeeping and internal process."),
	("Customer Handover", "What customers are shown about operating their fountain."),
)


def ensure_training_categories():
	"""Create any starter category that does not already exist."""
	if frappe.flags.in_test:
		return
	if not frappe.db.exists("DocType", "Training Category"):
		# Same guard `ensure_training_badges` has always had, and its absence here
		# is what produced the "Training setup" rows in the Error Log. The two
		# existence checks are not interchangeable: `frappe.db.exists("Training
		# Category", name)` below queries `tabTraining Category`, which survives
		# from an earlier migrate, so it answers happily while the *DocType row*
		# is still missing. `frappe.get_doc` then asks for a controller, Frappe
		# reads a blank `module` off the absent row, falls back to Core, and
		# raises `No module named 'frappe.core.doctype.training_category'` —
		# a confusing way to say "not migrated yet".
		return
	for name, description in STARTER_CATEGORIES:
		if frappe.db.exists("Training Category", name):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Training Category",
					"category_name": name,
					"description": description,
				}
			).insert(ignore_permissions=True)
		except Exception:
			# A failure here must never abort a migrate — the categories are a
			# convenience, and an author can always type their own.
			frappe.log_error(
				f"Could not seed Training Category {name}\n{frappe.get_traceback()}",
				"Training setup",
			)


# Starter badges. Every `criteria_type` here is one `_badge_is_earned` actually
# evaluates — an unknown criterion awards nothing, so a badge it cannot answer
# would sit in the list forever being quietly unearnable.
#
# Deliberately only the criteria that need no site-specific setup: the two
# course-scoped kinds (Course Completed, Category Completed) would need a course
# or category that may not exist yet, and a badge pointing at a missing link is
# worse than no badge.
#: Where every badge's artwork is served from, and the single home for that path — the ten
#: Technician Program badges import this rather than restating it, so the directory cannot move
#: for half of them.
#:
#: **A raw ``/assets`` path is served immutable for a year with no content hash** — the same fact
#: behind this app's rule that global JS and CSS ship as esbuild bundles. An image referenced from a
#: database field has no bundling option, and it does not matter while the artwork never changes. It
#: matters the day somebody redraws one: an edit in place will not reach a browser that has already
#: cached it, for up to a year. **Give a redrawn badge a new filename** rather than overwriting it.
BADGE_IMAGE_BASE = "/assets/erpnext_enhancements/images/training/badges"

STARTER_BADGES = (
	# (name, description, criteria_type, criteria_value, points, image file)
	("First Course", "Finished your first course.", "First Completion", None, 10, "badge-first-course.svg"),
	("Full Marks", "Scored 100% on a quiz.", "Perfect Score", None, 15, "badge-full-marks.svg"),
	("Five Courses", "Finished five courses.", "Courses Completed Count", "5", 25, "badge-five-courses.svg"),
	("Ten Courses", "Finished ten courses.", "Courses Completed Count", "10", 50, "badge-ten-courses.svg"),
	("Steady Week", "Trained on seven days in a row.", "Streak Days", "7", 20, "badge-steady-week.svg"),
)


def ensure_training_badges():
	"""Create any starter badge that does not already exist.

	Registered on `after_migrate` since Phase 4 and **never written** — so every
	migrate since raised `AttributeError: module has no attribute
	'ensure_training_badges'` and took the whole deploy down with it. The hook was
	pointing at `gamification`, which holds the runtime awarding logic; seeding
	belongs here beside the categories.

	Insert-only and idempotent, like the categories: a badge an admin renamed or
	disabled stays that way. They are inert until gamification is switched on.
	"""
	if frappe.flags.in_test:
		return
	if not frappe.db.exists("DocType", "Training Badge"):
		# Phase 4 may not have migrated on this site yet. Not an error: the badge
		# doctype arriving later is exactly what the next migrate is for.
		return
	for name, description, criteria_type, criteria_value, points, image in STARTER_BADGES:
		if frappe.db.exists("Training Badge", name):
			# Insert-only, which is why the artwork needed a backfill patch of its own for the
			# sites that already had these five: `patches/backfill_starter_badge_images.py`.
			continue
		try:
			frappe.get_doc(
				{
					"doctype": "Training Badge",
					"badge_name": name,
					"description": description,
					"criteria_type": criteria_type,
					"criteria_value": criteria_value,
					"points": points,
					# A static app asset, not an uploaded File -- `image` is an Attach Image
					# field and stores a URL, so there is no File record to create.
					"image": f"{BADGE_IMAGE_BASE}/{image}",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		except Exception:
			# Same rule as the categories: a failure here must never abort a
			# migrate. Badges are decoration; a deploy is not.
			frappe.log_error(
				f"Could not seed Training Badge {name}\n{frappe.get_traceback()}",
				"Training setup",
			)
