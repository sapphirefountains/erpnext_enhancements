"""Controller for the Sapphire Maintenance Section doctype.

A Section is a reusable building block for maintenance form templates: a typed
group of checklist items shared across templates (e.g. one "Chemical Dosing"
section reused by every maintenance form). The ``section_type`` decides which
child-table columns are meaningful and which Maintenance Record table its rows
are instantiated into (see ``get_visit_payload`` on Sapphire Maintenance
Record):

  * Chemical Dosing       -> consumables rows (item link required per row)
  * Water Chemistry       -> chemistry_readings rows (min/max target range)
  * Equipment Inspection  -> maintenance_results rows (Pass/Fail/Replace/Other)
  * Cleaning Tasks        -> cleaning_tasks rows (done / not done)

Any line can name weekdays in ``days`` (v1.618.0): it then goes only on visits on those days,
for an agreement's weekly schedule. See ``sapphire_maintenance/visit_days.py``.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class SapphireMaintenanceSection(Document):
	def validate(self):
		self.normalize_days()
		if self.section_type == "Chemical Dosing":
			for row in self.items:
				if not row.item:
					frappe.throw(
						_("Row {0}: Chemical Dosing items must link an Item so consumption can reduce stock.").format(
							row.idx
						)
					)
		if self.section_type == "Water Chemistry":
			for row in self.items:
				if row.min_value is not None and row.max_value is not None and row.min_value > row.max_value:
					frappe.throw(
						_("Row {0}: minimum value {1} is greater than maximum value {2}.").format(
							row.idx, row.min_value, row.max_value
						)
					)

	def normalize_days(self):
		"""Store each line's weekdays one way ("Tue, Fri"), and refuse a word that is not a day.

		A line with days goes only on visits on those weekdays (``visit_days``). A misspelt day
		would otherwise be a line that silently goes on every visit, the opposite of what was
		meant, so it is refused here where it was typed.
		"""
		from erpnext_enhancements.sapphire_maintenance.visit_days import normalize_days

		for row in self.items:
			try:
				row.days = normalize_days(row.get("days"))
			except ValueError as error:
				frappe.throw(
					_('Row {0}: "{1}" is not a day of the week. Write the days like Tue, Fri or Mon-Fri.').format(
						row.idx, error
					)
				)
