# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Batch assignment fan-out — raise each cohort member's assignments through the
**existing** Training Assignment engine.

Kept out of the ``Training Batch`` controller for three reasons, all of which the
rest of the training module already learned the hard way:

* **It reuses ``training_author.run_bulk_assign``** — the one creation path that
  already skips anyone with an open assignment, notifies the assignee, and isolates
  a bad row so one failure does not lose the rest of the cohort. A batch that raised
  its own assignments would be the "second assignment path" the whole design exists
  to avoid.
* **It is enqueued, not inline.** A cohort of thirty people across four courses is a
  hundred-and-twenty inserts; doing that inside the manager's save would block the
  form, and ``run_bulk_assign`` commits as it goes, which has no business happening
  mid-save. The controller enqueues it after commit.
* **It is idempotent, so it survives a re-drive.** The prod deploy FLUSHDBs the
  queue redis and drops pending jobs (see CLAUDE.md), so a fan-out enqueued across a
  deploy can silently never run. Because every raise is guarded on open status,
  re-saving the batch (or any later member add) simply completes what was missed
  rather than double-assigning.
"""

import frappe
from frappe.utils import now_datetime


def sync_batch(batch):
	"""Raise assignments for every member × every course of an *active* batch.

	Returns the number of assignments created. A batch that is not Active is a
	no-op: it may have been paused, completed or cancelled between the enqueue and
	the run.
	"""
	from erpnext_enhancements.api.training_author import run_bulk_assign

	doc = frappe.get_doc("Training Batch", batch)
	if doc.status != "Active":
		return 0

	learners = [row.learner for row in (doc.members or []) if row.learner]
	courses = [row.course for row in (doc.courses or []) if row.course]
	if not learners or not courses:
		return 0

	# Due by the cohort's end date — a batch is a group finishing together — falling
	# back to the start date, then to None, which lets the Training Assignment
	# controller apply the course/settings default (course.due_days for Required
	# courses). run_bulk_assign forces optional courses' due dates to None anyway.
	due_date = doc.end_date or doc.start_date or None

	created = 0
	for course in courses:
		# run_bulk_assign commits per call and dedups on OPEN_STATUSES, so this loop
		# is safe to re-run and safe to interrupt.
		created += run_bulk_assign(course, learners, due_date, doc.owner)

	_stamp_enrolled_on(doc)
	return created


def _stamp_enrolled_on(doc):
	"""Record when each member was enrolled, once.

	Written straight to the child row with ``update_modified=False`` so it neither
	re-enters the batch's own ``on_update`` (which would re-enqueue this job) nor
	churns the parent's timestamp. The assignment engine keys off ``enrolled_on``
	rather than the row's creation, per the field's own description.
	"""
	stamp = now_datetime()
	for row in doc.members or []:
		if not row.enrolled_on:
			frappe.db.set_value(
				"Training Batch Member", row.name, "enrolled_on", stamp, update_modified=False
			)
