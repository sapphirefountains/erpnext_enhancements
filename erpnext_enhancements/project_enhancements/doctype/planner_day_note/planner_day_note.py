# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A note on a day for the whole crew (Project Planner Phase 6D, TASK-2026-02470).

"Shop meeting 7 am", "City inspection at Riverwalk": shown on both planners' calendars, in My week
and in the 6 AM digest of everyone it is for. ``audience`` is blank for everyone, or one Planner
Resource group (Field, PM, Design, Subcontractor), which narrows who gets it in their own digest
and My week; the planners' calendars always show every note.

The planners (``crew_availability.SCHEDULER_ROLES``) write them, through ``api/planner_blocks``
or the Desk; everyone with planner access reads them through the API.

The class name is the one Frappe derives (``doctype.replace(" ", "")``);
``tests/test_doctype_controller_names.py`` guards it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The Select's options after the blank: the Planner Resource groups.
AUDIENCES = ("Field", "PM", "Design", "Subcontractor")


class PlannerDayNote(Document):
	def validate(self):
		self.note = (self.note or "").strip()
		if not self.note:
			frappe.throw(_("Write the note."))
		self.audience = (self.audience or "").strip() or None
		if self.audience and self.audience not in AUDIENCES:
			frappe.throw(_("A note is for everyone or for one of: {0}.").format(", ".join(AUDIENCES)))
