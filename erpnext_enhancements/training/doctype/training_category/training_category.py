# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Category — how courses and bank questions are grouped for browsing.

Named by its own title (``autoname: field:category_name``) so a category reads as
itself everywhere it is linked. Starter rows are seeded on ``after_migrate`` by
``training/setup.py`` so the builder's category picker is never empty on a fresh
site.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrainingCategory(Document):
	def validate(self):
		self._reject_cycle()

	# ------------------------------------------------------------------ helpers

	def _reject_cycle(self):
		"""A category that is its own ancestor makes the browse tree infinite, and
		the failure only shows up as a hung page much later."""
		seen = {self.name}
		parent = self.parent_category
		while parent:
			if parent in seen:
				frappe.throw(_("{0} cannot be nested inside itself.").format(self.name))
			seen.add(parent)
			parent = frappe.db.get_value("Training Category", parent, "parent_category")
