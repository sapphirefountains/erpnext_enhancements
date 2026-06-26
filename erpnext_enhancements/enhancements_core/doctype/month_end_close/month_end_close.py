"""Month-End Close — period-close checklist with a hard period lock.

One record per accounting period (per company). It carries a checklist of close
tasks (reconcile bank/CC, post accruals, review AR/AP aging, external-accountant
review, approve statements) seeded from the Finance process map, and gates the
close: the document can only be **submitted** (= period Closed) once every task
is Done or N/A, and submission is permitted only to Accounts Manager.

The teeth: on submit we set the Company's ``accounts_frozen_till_date`` to the
period end date (ERPNext's GL ``check_freezing_date`` then blocks any posting on
or before that date for everyone except the ``role_allowed_for_frozen_entries``
role — which we default to "Accounts Manager" so finance can still post genuine
corrections). Cancelling the close restores the previous frozen-till date,
re-opening the period. The prior value is stashed on the record for an exact
restore.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import formatdate, now_datetime

# Default close checklist, from the Finance — Month-End Close process map.
DEFAULT_TASKS = [
	("Reconcile all bank accounts", "Lisa Symanski"),
	("Reconcile credit card accounts", "Lisa Symanski"),
	("Post accruals & adjusting journal entries", "Lisa Symanski"),
	("Review AR aging", "Lisa Symanski"),
	("Review AP aging", "Lisa Symanski"),
	("Reconcile vs QuickBooks Balance Comparison", "Lisa Symanski"),
	("Review P&L and Balance Sheet", "Lisa Symanski"),
	("External accountant review", "John Juntunen"),
	("Approve financial statements", "Lisa Symanski"),
]

_OPEN = "Open"
_IN_PROGRESS = "In Progress"
_CLOSED = "Closed"
_DONE_STATES = ("Done", "N/A")
_DEFAULT_MODIFIER_ROLE = "Accounts Manager"


class MonthEndClose(Document):
	def before_insert(self):
		if not self.tasks:
			for task, responsible in DEFAULT_TASKS:
				self.append("tasks", {"task": task, "responsible": responsible, "status": "Pending"})

	def validate(self):
		self._stamp_task_completion()
		self._roll_up_status()

	def before_submit(self):
		if not self.period_end_date:
			frappe.throw(_("Period End Date is required to close the period."))
		incomplete = [row.task for row in self.tasks if row.status not in _DONE_STATES]
		if incomplete:
			frappe.throw(
				_("Complete (or mark N/A) all checklist tasks before closing: {0}").format(
					", ".join(incomplete)
				)
			)

	def on_submit(self):
		self._lock_period()

	def on_cancel(self):
		self._restore_period()

	# --- helpers -------------------------------------------------------------
	def _stamp_task_completion(self):
		for row in self.tasks:
			if row.status == "Done":
				if not row.completed_on:
					row.completed_on = now_datetime()
					row.completed_by = frappe.session.user
			else:
				row.completed_on = None
				row.completed_by = None

	def _roll_up_status(self):
		if self.docstatus == 1:
			self.status = _CLOSED
		elif any(row.status in _DONE_STATES for row in self.tasks):
			self.status = _IN_PROGRESS
		else:
			self.status = _OPEN

	def _lock_period(self):
		# Stash the current frozen-till date for an exact restore on cancel.
		self.db_set(
			"previous_frozen_till_date",
			frappe.db.get_value("Company", self.company, "accounts_frozen_till_date"),
		)
		frappe.db.set_value("Company", self.company, "accounts_frozen_till_date", self.period_end_date)
		# Ensure a modifier role exists so finance can still post corrections; an
		# empty role would lock the period for everyone (incl. Accounts Manager).
		if not frappe.db.get_value("Company", self.company, "role_allowed_for_frozen_entries"):
			frappe.db.set_value(
				"Company", self.company, "role_allowed_for_frozen_entries", _DEFAULT_MODIFIER_ROLE
			)
		frappe.msgprint(
			_("Period locked: posting on or before {0} is now restricted for {1}.").format(
				formatdate(self.period_end_date), self.company
			)
		)

	def _restore_period(self):
		frappe.db.set_value(
			"Company", self.company, "accounts_frozen_till_date", self.previous_frozen_till_date
		)
		frappe.msgprint(
			_("Period re-opened for {0}: frozen-till date restored.").format(self.company)
		)
