# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar Cutover Item: one hand-made recurring series from the old Maintenance calendar, under review.

Made and refreshed only by the scan (``project_enhancements/calendar_sync/cutover.py``), System Manager
only. A person picks or confirms the profile, edits the proposed links, instructions, codes and address,
and approves or ignores the row; *Apply approved* copies it to the profile. ``status`` is read-only on
the form so it only ever moves through ``cutover.set_status`` and ``cutover.apply_approved``.

``validate`` keeps two things true: a profile picked by hand is marked *Chosen by hand* (so a rescan
keeps it), and ``project`` always follows the profile.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

import frappe
from frappe.model.document import Document

BY_HAND = "Chosen by hand"


class CalendarCutoverItem(Document):
	def validate(self):
		if self.get("profile") and self.get("profile") != self.get("proposed_profile"):
			self.match_quality = BY_HAND
		self.project = (
			frappe.db.get_value("Sapphire Maintenance Profile", self.get("profile"), "project")
			if self.get("profile")
			else None
		)
		for field in ("proposed_links", "proposed_instructions", "proposed_access_codes", "proposed_address"):
			if isinstance(self.get(field), str):
				self.set(field, self.get(field).strip())
