# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Moving money between budget categories, on the record — WI-075 sub-phase M.

Budget lines can be edited on the Project directly, and for the first draft of a budget that is
the right way to do it. This document exists for what happens *after* the budget is agreed: money
that moves between categories without a record is the mechanism by which an overrun becomes
invisible. Contingency quietly drains into Materials, every line still reads at or under budget,
and the job that ran $40,000 over reports clean.

Four decisions live here.

**A reallocation is net zero.** :func:`~erpnext_enhancements.quality.budgets.apply_reallocation`
moves an amount out of one line and into another and never changes the total. That is what makes
it safe to apply to a Project whose ``estimated_costing`` is the sum of those lines: the derived
denominator cannot move. The invariant is checked here at apply time as well as asserted in the
test suite, because a silently changed total is worse than a refused save.

**The second approval cannot come from the same hand.** Protected categories require an approver
above the project manager, and that approver may not be the requester or the project manager who
already approved it. Without that rule the control is satisfiable by one person clicking twice,
which is a control in appearance only.

**Balances are stamped, then never re-read.** ``from_balance_before``/``_after`` record what the
two lines held when this was applied. The lines go on changing afterwards; this document is the
record of one move against the numbers as they stood that day, which is what makes a sequence of
reallocations reconstructable rather than merely present.

**Amendment is refused, and so is a cancellation that would go negative.** Frappe's amend appends
``-1`` to the name and would re-apply the money on submit, so an amended reallocation is a second
move wearing the first one's name. And cancelling is only possible while the money is still there
to give back: if a later reallocation has already spent it, reversing this one would drive a line
below zero, so the cancel is refused and a compensating reallocation is the correct instrument.
That is ordinary accounting practice, and the alternative is a wrong number.

Why this saves the Project through the document API
---------------------------------------------------

WI-057 records the rule this looks like it breaks: **never bulk ``doc.save()`` on Project.**
Project carries heavy ``on_update`` hooks and a wildcard ``'*'`` ``after_save`` →
``global_triton_sync`` that fires per ORM save, so a patch walking hundreds of projects must use
``frappe.db.set_value`` in batches.

That rule is about *bulk*. This is one project, saved once, because a person deliberately moved
money on it — exactly the cost of that person editing the Project in the Desk, which nobody
proposes to prevent. Writing the child rows with ``db.set_value`` instead would skip the parent's
recalculation and cannot create a line the project does not have yet, and bypassing the ORM to
write child rows is the documented way to leave a parent's totals stale.

The save runs with ``ignore_permissions``: the gate on moving this money is the submit permission
on *this* document, which a Finance Team approver holds. Requiring write on Project as well would
stop them applying a reallocation they were entitled to approve.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import budgets

STATUS_DRAFT = "Draft"
STATUS_APPLIED = "Applied"
#: One 'l'. House style, and renaming a Select option later is a data migration rather than an
#: edit -- an off-options value makes every existing row unsaveable.
STATUS_CANCELED = "Canceled"


class BudgetReallocation(Document):
	def validate(self):
		self._refuse_amendment()
		self._stamp_requester()
		self.requires_additional_approval = (
			1 if budgets.requires_second_approval(self.from_category, self.to_category) else 0
		)
		self._check_move_is_possible()
		self.set_status()

	def before_submit(self):
		# Re-checked rather than trusted from validate: the budget lines are editable on the
		# Project and another reallocation may have submitted in between, so the balance this
		# document was drafted against can be gone by the time somebody presses Submit.
		self._check_move_is_possible()
		self._check_approvals()

	def on_submit(self):
		self._apply(+1)
		self.set_status()

	def on_cancel(self):
		self._apply(-1)
		self.set_status()

	# --- derived state ------------------------------------------------------------------------

	def set_status(self):
		"""Status follows ``docstatus`` and is never typed.

		A submittable document already has a state. A second stored status beside it is a fact
		free to disagree with the record it summarises, which is how a cancelled document goes on
		reporting itself as applied.
		"""
		if self.docstatus == 2:
			self.status = STATUS_CANCELED
		elif self.docstatus == 1:
			self.status = STATUS_APPLIED
		else:
			self.status = STATUS_DRAFT

	def _refuse_amendment(self):
		if not self.get("amended_from"):
			return
		frappe.throw(
			_(
				"A budget reallocation cannot be amended. Amending appends -1 to the name and "
				"would move the money a second time. Raise a new reallocation in the other "
				"direction instead."
			),
			title=_("Amendment refused"),
		)

	def _stamp_requester(self):
		"""Server clock and server session, never a client-proposed value.

		Same lesson as ``process_steps.complete_step``: the old client path let the browser
		propose the timestamp, and the audit found retroactive box-checking.
		"""
		if not self.requested_by:
			self.requested_by = frappe.session.user

	# --- the rules ----------------------------------------------------------------------------

	def _check_move_is_possible(self):
		errors = budgets.reallocation_errors(
			self.from_category, self.to_category, self.amount, self._project_lines()
		)
		if errors:
			frappe.throw("<br>".join(errors), title=_("This reallocation cannot be applied"))

	def _check_approvals(self):
		errors = budgets.approval_errors(
			self.requested_by,
			self.pm_approved_by,
			self.additional_approved_by,
			bool(self.requires_additional_approval),
		)
		if errors:
			frappe.throw("<br>".join(errors), title=_("Not yet approved"))

	# --- applying it --------------------------------------------------------------------------

	def _project_lines(self):
		"""The project's budget lines as plain dicts.

		Defensive read: this fires from a form that can be opened before ``bench migrate`` has
		added the Table custom field, and a missing field must read as "no lines" rather than
		raise.
		"""
		if not self.project:
			return []
		project = frappe.get_doc("Project", self.project)
		rows = project.get(budgets.LINES_FIELD) or []
		return [
			{
				"category": getattr(row, "category", None) or "",
				"budgeted_amount": getattr(row, "budgeted_amount", None) or 0,
			}
			for row in rows
		]

	def _apply(self, direction):
		"""Move the money on the Project. ``direction`` is +1 to apply, -1 to reverse."""
		if not self.project:
			return

		source = self.from_category if direction > 0 else self.to_category
		target = self.to_category if direction > 0 else self.from_category

		project = frappe.get_doc("Project", self.project)
		rows = project.get(budgets.LINES_FIELD) or []
		before = [
			{
				"category": getattr(row, "category", None) or "",
				"budgeted_amount": getattr(row, "budgeted_amount", None) or 0,
			}
			for row in rows
		]

		errors = budgets.reallocation_errors(source, target, self.amount, before)
		if errors:
			if direction > 0:
				frappe.throw("<br>".join(errors), title=_("This reallocation cannot be applied"))
			frappe.throw(
				_(
					"This reallocation can no longer be reversed: {0} Raise a compensating "
					"reallocation in the other direction instead."
				).format(" ".join(errors)),
				title=_("Cancellation refused"),
			)

		after = budgets.apply_reallocation(before, source, target, self.amount)

		# The invariant, checked rather than assumed. A reallocation that changed the total would
		# silently rewrite the project's derived denominator.
		if budgets.total_budgeted(before) != budgets.total_budgeted(after):
			frappe.throw(
				_("Refusing to apply: this would change the project budget total."),
				title=_("Reallocation is not net zero"),
			)

		self._write_lines(project, after)
		self._stamp_balances(before, after, direction)

		project.flags.ignore_permissions = True
		project.save()

		frappe.msgprint(
			budgets.describe_reallocation(source, target, self.amount),
			indicator="green",
			alert=True,
		)

	def _write_lines(self, project, after):
		"""Apply the computed amounts back onto the Project's child rows.

		Existing rows are updated in place so their ``budget_key`` survives; a category the
		project did not carry is appended.
		"""
		by_category = {row["category"]: row["budgeted_amount"] for row in after}
		for row in project.get(budgets.LINES_FIELD) or []:
			category = getattr(row, "category", None)
			if category in by_category:
				row.budgeted_amount = by_category.pop(category)
		for category, amount in by_category.items():
			project.append(
				budgets.LINES_FIELD, {"category": category, "budgeted_amount": amount}
			)

	def _stamp_balances(self, before, after, direction):
		"""Record what the two lines held, on the way through.

		Only on the applying pass. A cancellation must not overwrite the record of what the
		original move did -- that record is the reason to keep a cancelled document at all.
		"""
		if direction < 0:
			return

		def held(rows, category):
			line = budgets.line_for(rows, category)
			return (line or {}).get("budgeted_amount") or 0

		self.from_balance_before = held(before, self.from_category)
		self.from_balance_after = held(after, self.from_category)
		self.to_balance_before = held(before, self.to_category)
		self.to_balance_after = held(after, self.to_category)

