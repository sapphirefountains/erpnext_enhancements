# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The scope, authored once and locked — WI-075 sub-phase B1.

Scope, contract terms, pricing and inspection standards have historically lived in four
separate places, and when they disagree there is no single record either side can point to.
This is that record: one submitted ``Project Scope of Work`` per project, carrying measurable
acceptance criteria that the Statement of Work and the inspection template both read from
rather than restate.

**Submit is the lock.** There is no separate "approved" flag to fall out of step with
``docstatus`` — the framework already has exactly one boolean for "this document is final", and
a second one would only ever be wrong.

Inert on purpose, for now
-------------------------

Nothing consumes this yet. Sub-phase B2 wires it into the hand-off engine — a new step, a new
anchor, and the renumbering of the existing step 7 — and that is deliberately a separate change
so the schema can be reviewed without also reviewing an edit to the engine that 707 live
``Project Process Step`` rows already run through.

So this file has no ``on_submit``. When B2 adds one, it goes *there* and not here, and it
swallows-and-logs rather than letting a mirroring failure undo a lock the user just performed.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import scope_criteria


class ProjectScopeOfWork(Document):
	def validate(self):
		self._mint_criterion_keys()
		self._reject_duplicate_keys()

	def before_submit(self):
		self._require_inspectable_criteria()
		self._reject_second_lock()
		# Server clock and server session, never the browser's. The lesson is
		# `process_steps.complete_step`: the old client path let the browser propose
		# `completed_on`, and the audit found retroactive box-checking.
		self.locked_on = frappe.utils.now_datetime()
		self.locked_by = frappe.session.user
		self.status = "Locked"

	def on_cancel(self):
		# `status` is allow_on_submit so it can move without a new revision. A cancelled scope
		# is superseded rather than back to Draft: the draft it came from is gone, and calling
		# it Draft would invite somebody to treat it as still in play.
		self.status = "Superseded"

	# ------------------------------------------------------------------ helpers

	def _mint_criterion_keys(self):
		"""Give every criterion a stable identity before anything can point at it."""
		scope_criteria.mint_missing_keys(self.acceptance_criteria, frappe.generate_hash)

	def _reject_duplicate_keys(self):
		"""Two rows sharing a key is a couple of grid clicks away — Frappe's row-duplicate
		action copies read-only fields too — and a downstream join would then resolve to
		whichever row it read first."""
		duplicates = scope_criteria.duplicate_keys(self.acceptance_criteria)
		if duplicates:
			frappe.throw(
				_("Two acceptance criteria share the same key: {0}. Delete the duplicated row and add a fresh one.").format(
					", ".join(duplicates)
				),
				title=_("Duplicated criterion"),
			)

	def _require_inspectable_criteria(self):
		"""A scope with nothing measurable in it is the problem this record exists to end.

		Checked at submit rather than at save so a half-written draft can be parked. The
		blank-check uses ``strip()`` in Python rather than a SQL comparison on purpose: under
		MariaDB's default PAD SPACE collation a value of ``" "`` compares equal to ``""``, so
		the obvious SQL form of this test reports clean on data that is genuinely empty.
		"""
		if not self.acceptance_criteria:
			frappe.throw(
				_("Add at least one acceptance criterion before locking. A scope nobody can inspect against is what this record exists to prevent."),
				title=_("Nothing to inspect"),
			)

		problems = scope_criteria.incomplete_rows(self.acceptance_criteria)
		if problems:
			lines = [
				_("Row {0}: missing {1}").format(idx, ", ".join(missing))
				for idx, missing in problems
			]
			frappe.throw(
				_("These criteria cannot be inspected as written:<br>{0}").format("<br>".join(lines)),
				title=_("Criteria not measurable"),
			)

	def _reject_second_lock(self):
		"""At most one locked scope per project.

		Enforced here rather than by a unique index: a cancelled or amended row keeps its name
		and its ``project``, so a database constraint would refuse the legitimate re-lock after
		an amendment. ``docstatus = 1`` is the real predicate.
		"""
		existing = frappe.get_all(
			"Project Scope of Work",
			filters={"project": self.project, "docstatus": 1, "name": ("!=", self.name)},
			pluck="name",
			limit=1,
		)
		if existing:
			frappe.throw(
				_("{0} already has a locked Scope of Work ({1}). Amend that one, or raise a Change Order against it.").format(
					self.project, existing[0]
				),
				title=_("Scope already locked"),
			)
