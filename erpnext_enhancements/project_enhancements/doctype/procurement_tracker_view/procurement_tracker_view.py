# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Procurement Tracker View: one person's saved layout for the tracker on the Project form (v1.815.0).

The record is owned by whoever created it (Frappe's own ``owner``). **Nobody but a System Manager has
a DocPerm on it**: people reach their own views only through ``procurement_views.py``, which checks
the owner on every read, change and delete and writes nothing but the person's own records. This
controller is the backstop for the one door that is not that file (the Desk form, an import, a
script): whatever writes the record, the layout is held to ``procurement_view_rules.validate_layout``,
so a stored view always names known columns only and fits the size limit, and its owner cannot be
swapped by editing it.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

import frappe
from frappe.model.document import Document

from erpnext_enhancements import procurement_view_rules as rules


class ProcurementTrackerView(Document):
	def validate(self):
		try:
			self.view_name = rules.clean_name(self.view_name)
			layout = rules.validate_layout(self.layout, self.mode)
		except rules.ViewError as e:
			frappe.throw(str(e))
		self.layout = rules.layout_json(layout)
		if not self.is_new():
			# A view stays with the person who made it, whoever edits the record.
			before = self.get_doc_before_save()
			if before and before.owner != self.owner:
				frappe.throw("A view stays with the person who made it.")
