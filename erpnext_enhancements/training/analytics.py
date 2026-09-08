# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Manager training analytics (WI-071 Phase H).

One whitelisted read, `get_training_analytics`, that rolls the training records up
into the numbers a training manager actually opens a dashboard to see: how many
learners are behind, which courses are dragging, how each active cohort is
tracking, and what is waiting to be graded. It is **manager-only** — the same
{System Manager, Training Manager, HR Manager} set that is unscoped in
`training/permissions.py` — because it reports across every learner, which is
exactly what row scoping withholds from everyone else.

The aggregation is done in Python over a handful of `get_all` reads rather than in
SQL, for two reasons that have bitten this module before: a `<`-on-a-nullable-date
filter silently matches NULLs in SQL (see the coalesce note in CLAUDE.md), and the
"overdue" rule is a predicate — closed statuses excluded, an explicit *Overdue*
status honoured, and only *then* a past due date — that reads as five lines here
and as a correlated mess in a query. Every doctype read is guarded on existence so
a site part-way through the WI-071 rollout returns partial numbers rather than a
crash.

The page at `/training_analytics` renders the dict this returns; the controller
injects it server-side, so the numbers are on the page at first paint.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today

from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

MANAGER_ROLES = {"System Manager", "Training Manager", "HR Manager"}

# An assignment still owes work in these states; it is closed in the others. Both
# sets are named so the "overdue" and "active" counts cannot drift apart.
ACTIVE_ASSIGNMENT_STATUSES = {"Not Started", "In Progress", "Awaiting Sign-off", "Overdue"}
CLOSED_ASSIGNMENT_STATUSES = {"Completed", "Waived", "Cancelled"}


def _is_manager(user=None):
	return bool(MANAGER_ROLES & set(frappe.get_roles(user or frappe.session.user)))


def _require_manager():
	if not _is_manager():
		frappe.throw(_("Training analytics is for training managers."), frappe.PermissionError)


def _exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


@frappe.whitelist()
def get_training_analytics():
	"""Org-wide training analytics for the manager dashboard. Manager-only.

	Returns ``{"enabled": False}`` when training is switched off, so the page can say
	so rather than show a wall of zeros as if they were real.
	"""
	_require_manager()
	if not is_enabled("training_enabled"):
		return {"enabled": False}

	assignments = (
		frappe.get_all(
			"Training Assignment",
			fields=["user", "course", "course_title", "status", "due_date"],
			limit_page_length=0,
		)
		if _exists("Training Assignment")
		else []
	)
	completions = (
		frappe.get_all(
			"Training Completion",
			filters={"status": "Valid"},
			fields=["user", "course", "course_title_snapshot", "score_percent", "completed_on"],
			order_by="completed_on desc",
			limit_page_length=0,
		)
		if _exists("Training Completion")
		else []
	)

	today_d = getdate(today())

	def is_overdue(a):
		if a.status in CLOSED_ASSIGNMENT_STATUSES:
			return False
		if a.status == "Overdue":
			return True
		return bool(a.due_date) and getdate(a.due_date) < today_d

	learners = {a.user for a in assignments if a.user}
	active = [a for a in assignments if a.status in ACTIVE_ASSIGNMENT_STATUSES]
	completed = [a for a in assignments if a.status == "Completed"]
	overdue = [a for a in assignments if is_overdue(a)]
	awaiting = [a for a in assignments if a.status == "Awaiting Sign-off"]

	certificates = (
		frappe.db.count("Training Certificate", {"status": "Valid"})
		if _exists("Training Certificate")
		else 0
	)

	# Average score is measured on completions (a real result), never on assignments.
	course_scores = {}
	for c in completions:
		course_scores.setdefault(c.course, []).append(flt(c.score_percent))

	# Per-course rollup. A Cancelled assignment is dropped from the denominator: it
	# is not work anyone still owes, and counting it drags every rate down forever.
	courses = {}
	for a in assignments:
		if a.status == "Cancelled":
			continue
		row = courses.setdefault(
			a.course,
			{"course": a.course, "title": a.course_title or a.course, "assigned": 0, "completed": 0, "overdue": 0},
		)
		row["assigned"] += 1
		if a.status == "Completed":
			row["completed"] += 1
		if is_overdue(a):
			row["overdue"] += 1

	by_course = []
	for course, row in courses.items():
		scores = course_scores.get(course, [])
		row["completion_rate"] = round(100 * row["completed"] / row["assigned"]) if row["assigned"] else 0
		row["avg_score"] = round(sum(scores) / len(scores)) if scores else None
		by_course.append(row)
	# Most overdue first, then the biggest cohorts — what a manager triages by.
	by_course.sort(key=lambda r: (-r["overdue"], -r["assigned"], r["title"]))
	by_course = by_course[:50]

	return {
		"enabled": True,
		"generated_on": str(now_datetime()),
		"totals": {
			"learners": len(learners),
			"active": len(active),
			"completed": len(completed),
			"overdue": len(overdue),
			"awaiting_signoff": len(awaiting),
			"certificates": certificates,
		},
		"by_course": by_course,
		"by_batch": _batch_progress(assignments),
		"submissions": _submission_summary(),
		"recent_completions": [
			{
				"user": c.user,
				"course_title": c.course_title_snapshot or c.course,
				"completed_on": str(c.completed_on or ""),
				"score_percent": round(flt(c.score_percent)),
			}
			for c in completions[:10]
		],
	}


def _batch_progress(assignments):
	"""Per active cohort: how far its members are through its courses.

	Progress is completed (member × course) pairs over the expected total, read from
	the assignments already in memory — a member who has a Completed assignment for a
	batch course counts as done on it. ``None`` when the batch doctypes are not
	migrated; ``[]`` when no cohort is active. Least-complete first: a cohort falling
	behind is the one a manager needs to see.
	"""
	if not _exists("Training Batch"):
		return None
	batches = frappe.get_all(
		"Training Batch", filters={"status": "Active"}, fields=["name", "title", "end_date"], limit_page_length=0
	)
	if not batches:
		return []

	completed_pairs = {(a.user, a.course) for a in assignments if a.status == "Completed"}
	out = []
	for bt in batches:
		members = [m for m in frappe.get_all("Training Batch Member", filters={"parent": bt.name}, pluck="learner") if m]
		batch_courses = [c for c in frappe.get_all("Training Batch Course", filters={"parent": bt.name}, pluck="course") if c]
		expected = len(members) * len(batch_courses)
		done = sum(1 for m in members for c in batch_courses if (m, c) in completed_pairs)
		out.append(
			{
				"batch": bt.name,
				"title": bt.title or bt.name,
				"members": len(members),
				"courses": len(batch_courses),
				"progress": round(100 * done / expected) if expected else 0,
				"end_date": str(bt.end_date or ""),
			}
		)
	out.sort(key=lambda r: (r["progress"], r["title"]))
	return out


def _submission_summary():
	"""Counts for the work-submission grading backlog (WI-071 Phase F).

	``None`` when the doctype is not migrated. ``pending`` is what is waiting on a
	grader — the number that should drive someone to open the queue.
	"""
	if not _exists("Training Submission"):
		return None
	rows = frappe.get_all("Training Submission", fields=["status"], limit_page_length=0)
	pending = sum(1 for r in rows if r.status in ("Submitted", "Under Review"))
	passed = sum(1 for r in rows if r.status == "Passed")
	needs_rework = sum(1 for r in rows if r.status == "Needs Rework")
	return {"pending": pending, "passed": passed, "needs_rework": needs_rework}
