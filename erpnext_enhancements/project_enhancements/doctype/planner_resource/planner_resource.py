# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A bookable person or outside crew on the Project Planner.

A Planner Resource is the planner's idea of "somebody whose hours can be booked": a field
technician, a PM on site, a designer, or a subcontractor crew that has no HR record at all. It
exists beside Employee rather than as Employee custom fields for exactly that last reason, and
because the people the planner books are only some of the staff -- the office never appears.

What the rules here protect:

* **One active resource per employee.** The planner joins everything by resource, so two active
  resources for one Employee would split that person's bookings across two rows and show each of
  them half-empty -- the overbooking warning is the whole point of the page, and it would go
  quiet. An inactive duplicate is fine (it is how a person is retired and re-added).
* **Hours are 0 to 24 and a range runs forwards.** The availability engine trusts the pattern
  rows; a 30-hour Tuesday or a range that ends before it starts would silently book nonsense.
* **A subcontractor has no employee and no user.** Clearing them stops a stale employee link,
  left behind by flipping the type, from joining the person to time off, holidays and
  assignments that belong to somebody else.

The class name is `PlannerResource` because Frappe derives it as
`doctype.replace(" ", "")`; `tests/test_doctype_controller_names.py` guards it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class PlannerResource(Document):
	def validate(self):
		if (self.resource_type or "Employee") == "Subcontractor":
			self.employee = None
			self.user = None
		else:
			self._require_employee()
			self._one_active_per_employee()
		self._validate_work_patterns()

	def _require_employee(self):
		if not self.employee:
			frappe.throw(
				_("An Employee resource needs an Employee. Use the Subcontractor type for outside crews.")
			)

	def _one_active_per_employee(self):
		if not self.is_active or not self.employee:
			return
		existing = frappe.db.get_value(
			"Planner Resource",
			{"employee": self.employee, "is_active": 1, "name": ("!=", self.name or "")},
			"name",
		)
		if existing:
			frappe.throw(
				_("{0} already has an active planner resource: {1}. Deactivate that one first.").format(
					self.employee, existing
				)
			)

	def _validate_work_patterns(self):
		for row in self.get("work_patterns") or []:
			for day in WEEKDAYS:
				hours = row.get(day)
				if hours is not None and not 0 <= float(hours) <= 24:
					frappe.throw(
						_("Row {0}: {1} hours must be between 0 and 24.").format(row.idx, _(day.capitalize()))
					)
			start, end = row.get("effective_from"), row.get("effective_to")
			if start and end and getdate(end) < getdate(start):
				frappe.throw(_("Row {0}: the end date cannot be before the start date.").format(row.idx))
