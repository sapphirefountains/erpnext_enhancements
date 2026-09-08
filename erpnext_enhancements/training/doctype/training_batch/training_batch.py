# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Batch — a cohort of learners moving through a set of courses together.

The foundation of the LMS-expansion (WI-071, Phase A). A batch is deliberately a
*thin* record: it names the cohort, its course set, and its members, and it leans
on the **existing** Training Assignment engine to raise each member's assignments
rather than inventing a second assignment path — so a batch cannot drift out of
step with the individual-assignment model the rest of the module already trusts.

Activation (`status = Active`) fans the assignments out. The fan-out itself lives
in `training/batch.py::sync_batch` and is **enqueued after commit** and
**idempotent**: `run_bulk_assign` skips anyone who already has an open assignment
for a course, so a re-save, a newly-added member, or a deploy re-drive never
double-assigns. See that module for why it is not inline.
"""

import frappe
from frappe.model.document import Document


class TrainingBatch(Document):
	def validate(self):
		self._resolve_member_employees()

	def on_update(self):
		# Fan out on every save of an active batch — adding a member to a running
		# cohort must assign them too. The job is idempotent, and deduplicated on the
		# batch name so a burst of saves collapses to one run; enqueue_after_commit so
		# it never races the write it is reacting to.
		if self.status == "Active":
			frappe.enqueue(
				"erpnext_enhancements.training.batch.sync_batch",
				queue="long",
				enqueue_after_commit=True,
				job_id=f"training-batch-sync::{self.name}",
				deduplicate=True,
				batch=self.name,
			)

	def _resolve_member_employees(self):
		"""Fill each staff member's Employee from their User, matched on ``user_id``.

		The doctype shipped with ``fetch_from: learner.name`` — but ``learner`` is a
		*User* link, so that copied the login id into an Employee field: wrong for the
		"group a cohort by Employee" reporting the field exists for, and a broken link.
		Employees are matched on ``user_id``; a customer Website User has no Employee
		and is left blank rather than pointed at a row that is not one.
		"""
		for row in self.members or []:
			if not row.learner:
				continue
			row.employee = frappe.db.get_value("Employee", {"user_id": row.learner}, "name") or None
