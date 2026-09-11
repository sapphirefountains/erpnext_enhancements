# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""How to shut a feature down and make sure it stays down.

One procedure per piece of equipment the crew services, listing every energy
source, where it is, how to isolate it, and — the column everybody leaves out —
**how to verify it is actually dead**. Locking a breaker is not the same as
confirming the pump will not start, and the difference is somebody's hand.

`last_inspected_on` and `inspection_due_on` are derived from the inspection
records rather than typed, because the annual periodic inspection is the
requirement almost nobody has and a self-reported date is exactly how it stays
that way.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, getdate

INSPECTION_MONTHS = 12


class LockoutTagoutProcedure(Document):
	def validate(self):
		self._require_verification_steps()
		self.refresh_inspection_dates()

	def _require_verification_steps(self):
		"""Every source needs a way to check it is dead.

		The one column that is always blank, and the only one whose absence can kill
		somebody: a procedure that says "lock breaker 14" and stops has told you how to
		feel safe rather than how to be.
		"""
		for row in self.energy_sources or []:
			if not (row.verification_method or "").strip():
				frappe.throw(
					_("Row {0}: say how to VERIFY {1} is dead, not just how to isolate it.").format(
						row.idx, row.energy_type or _("this source")
					)
				)

	def refresh_inspection_dates(self):
		"""Derived from the inspections, never typed.

		Also called by the inspection's own `on_update`, so filing one moves the date
		on the procedure without anybody remembering to.
		"""
		if not frappe.db.exists("DocType", "Lockout Periodic Inspection"):
			return
		last = frappe.db.get_value(
			"Lockout Periodic Inspection",
			{"procedure": self.name},
			"inspected_on",
			order_by="inspected_on desc",
		)
		self.last_inspected_on = last
		# An unwritten procedure is due now rather than never. A blank due date reads
		# as "nothing to do", which is the wrong way for this to fail.
		basis = last or self.written_on
		self.inspection_due_on = add_months(getdate(basis), INSPECTION_MONTHS) if basis else None
