"""HR Stat Entry — the monthly manual HR stats paste.

The one piece of genuine manual entry for HR KPIs: whoever owns hiring pastes
the month's open-position count and (when surveyed) the eNPS score here. There
is no recruiting or survey doctype on this site (hrms is not installed), so
this doctype is the data source. One row per month, enforced by the
``HRSTAT-{month}`` autoname; the HR snapshot reads the newest row and flags the
source stale once an entry is older than the previous calendar month.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, getdate


class HRStatEntry(Document):
	def before_naming(self):
		# Normalize BEFORE autoname runs (naming happens before validate), so the
		# HRSTAT-{month} name enforces one-row-per-month regardless of which day
		# was picked.
		self._normalize_month()

	def validate(self):
		# Re-normalize on edits too (the name keeps its original month).
		self._normalize_month()
		if self.enps and not -100 <= cint(self.enps) <= 100:
			frappe.throw(frappe._("eNPS must be between -100 and 100."))

	def _normalize_month(self):
		if self.month:
			self.month = getdate(self.month).replace(day=1)
