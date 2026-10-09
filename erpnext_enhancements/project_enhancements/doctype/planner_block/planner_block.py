# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Time one person is not available on one day (Project Planner Phase 6D, TASK-2026-02470).

"Unavailable 2–4 pm", "shop day": a personal block. Both planners read it through the shared
availability engine (``project_enhancements/crew_availability``), where an all-day block makes the
day like a day off and a timed one books its hours, so a PM dragging work onto it is asked for a
reason exactly as for any other conflict.

Nik's rules (2026-10-09), and where each is kept:

* **Planners can block anyone; each person can block their own time** (from My week). The
  planners are ``crew_availability.SCHEDULER_ROLES``. Technicians have no Desk permission on this
  doctype at all: every planner write goes through ``api/planner_blocks``, which checks
  own-or-scheduler explicitly, and the Desk is for System Managers.
* **Others see "Unavailable"; planners see the note.** The engine never reads ``note``; only
  ``crew_availability.block_notes`` does, and it answers per viewer.
* **One day per block.** Several days away belong in HR time off, which the planners already read.

What ``validate`` holds, because the engine trusts the row:

* the person exists and is active (an inactive resource is not on either planner, so a block on
  them would be invisible), and ``user`` / ``resource_name`` are copied from them;
* a timed block has both times and ends after it starts; an all-day block carries no times;
* a day that has passed is allowed, with a warning: a block can be recorded after the fact.

The class name is the one Frappe derives (``doctype.replace(" ", "")``);
``tests/test_doctype_controller_names.py`` guards it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from erpnext_enhancements.project_enhancements import crew_availability as engine


class PlannerBlock(Document):
	def validate(self):
		self._check_person()
		self._check_times()
		self.note = (self.note or "").strip() or None
		if self.date and getdate(self.date) < getdate(nowdate()):
			frappe.msgprint(
				_("This block is on a day that has already passed."), indicator="orange", alert=True
			)

	def _check_person(self):
		if not self.resource:
			frappe.throw(_("Pick the person this block is for."))
		row = frappe.db.get_value(
			"Planner Resource", self.resource, ["name", "resource_name", "user", "is_active"], as_dict=True
		)
		if not row:
			frappe.throw(_("{0} is not a Planner Resource.").format(self.resource))
		if not int(row.get("is_active") or 0):
			frappe.throw(
				_("{0} is not active on the planner.").format(row.get("resource_name") or self.resource)
			)
		self.user = row.get("user") or None
		self.resource_name = row.get("resource_name") or self.resource

	def _check_times(self):
		self.all_day = 1 if engine.is_all_day_block({"all_day": self.all_day}) else 0
		if self.all_day:
			self.from_time = None
			self.to_time = None
			return
		first, last = engine.time_minutes(self.from_time), engine.time_minutes(self.to_time)
		if first is None or last is None:
			frappe.throw(_("A block that is not all day needs a From and a To time."))
		if last <= first:
			frappe.throw(_("The To time must be after the From time."))
