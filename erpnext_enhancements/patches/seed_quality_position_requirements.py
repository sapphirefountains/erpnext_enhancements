"""Make the four Quality courses job requirements — WI-075 training, v1.466.0.

These are the first ``Position Requirement`` rows of type ``Training Course`` on this site; all
55 that existed before are ``Credential``.

Why they ship **not mandatory**, and what to change when you adopt the courses
------------------------------------------------------------------------------

An assignment and a job requirement are different records: only the second reaches the competency
roster. So a requirement is a statement that *this course is part of doing this job*, and that is
true today — the courses exist and describe work people already do.

What is **not** true today is that anybody can complete one. All four are ``Draft``, and
``progression._course_line`` looks only for a submitted ``Training Completion``; it does not check
whether the course is even publishable. So a mandatory requirement against a Draft course reports
every holder of that position as **Missing**, permanently, for something they are unable to take.

``is_mandatory`` is exactly the right lever, because ``progression`` line 120 counts only
*mandatory* missing rows toward the readiness figure::

    "missing": sum(1 for line in lines if line["state"] == MISSING and line["mandatory"])

So at ``is_mandatory = 0`` the requirement is **visible on the roster and counted by nobody**. The
intent is on the record where people can see and argue with it, and no one is marked short for a
course that does not yet exist to them.

**Flipping these to mandatory is the second half of adopting a course**, alongside publishing it.
Do both together, or the roster and the assignment engine disagree about whether the course is
real. Each row carries a ``note`` saying so, and that note renders on the roster line.

Insert-only and idempotent, keyed on (position, course). Running it twice adds nothing, and it
never touches a row somebody has already made mandatory.
"""

import frappe

from erpnext_enhancements.training import quality_course_specs as specs

COURSE = "Training Course"
POSITION = "Position"

#: ``{position: (course titles)}`` — derived from the same course-to-position mapping the
#: assignment rules use, minus the Role-targeted ones, because a requirement hangs off a Position
#: and a Role is not one. Kept here rather than re-derived so the two cannot silently disagree
#: about who needs what.
BY_POSITION = {
	"Technician": ("Running an inspection in the field",),
	"Project Manager": (
		"Non-conformances and corrective actions, end to end",
		"Writing scope that can be inspected",
		"Subcontractor agreements, purchase orders and rates",
	),
	"Operations Manager": ("Non-conformances and corrective actions, end to end",),
	"Sales Representative": ("Writing scope that can be inspected",),
	"Purchasing Agent/Inventory Clerk": (
		"Subcontractor agreements, purchase orders and rates",
	),
	"AP/AR, Purchasing Manager": ("Subcontractor agreements, purchase orders and rates",),
}

NOTE = (
	"Part of this job, but not yet enforceable: the course is still a Draft, so nobody can "
	"complete it. Tick Mandatory when the course is published -- publishing and making it "
	"mandatory are two halves of the same decision."
)


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. Every guard
	# below returns or continues; nothing here propagates. v1.464.0 learned this the hard way --
	# a ValidationError from a sibling patch took a production deploy down.
	for doctype in (POSITION, "Position Requirement", COURSE):
		if not frappe.db.exists("DocType", doctype):
			return

	added = 0
	skipped = 0

	for position, titles in BY_POSITION.items():
		try:
			added_here, skipped_here = _apply(position, titles)
		except Exception:
			frappe.log_error(
				title="Quality position requirement seed failed",
				message=f"{position}: {frappe.get_traceback()}",
			)
			continue
		added += added_here
		skipped += skipped_here

	if added:
		frappe.db.commit()

	print(
		f"seed_quality_position_requirements: {added} added (not mandatory), {skipped} already present"
	)
	if added:
		print("  Tick Mandatory on these when the courses are published, not before.")


def _apply(position, titles):
	if not frappe.db.exists(POSITION, position):
		# A position that does not exist on this site is not an error -- it is a site that does
		# not have that job. Logged so a typo is still visible.
		frappe.log_error(
			title="Quality position requirement skipped",
			message=f"No Position named {position!r}",
		)
		return 0, 0

	doc = frappe.get_doc(POSITION, position)
	existing = {
		row.training_course
		for row in (doc.get("requirements") or [])
		if row.get("requirement_type") == "Training Course"
	}

	added = 0
	skipped = 0
	for title in titles:
		course = frappe.db.get_value(COURSE, {"course_title": title}, "name")
		if not course:
			# The course seed runs earlier in patches.txt, so this means it failed. Skip rather
			# than raise: a missing requirement is recoverable, a failed deploy is not.
			frappe.log_error(
				title="Quality position requirement skipped",
				message=f"{position}: no Training Course titled {title!r}",
			)
			continue
		if course in existing:
			skipped += 1
			continue
		doc.append(
			"requirements",
			{
				"requirement_type": "Training Course",
				"training_course": course,
				# Deliberately 0. See the module docstring: only mandatory rows count toward the
				# readiness figure, and nobody should be counted short for a Draft course.
				"is_mandatory": 0,
				"note": NOTE,
			},
		)
		added += 1

	if added:
		doc.save(ignore_permissions=True)
	return added, skipped
