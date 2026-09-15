# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The budget category catalog — WI-075 sub-phase M.

The controller has one job worth the name: **the protection flag cannot be quietly unticked.**

``budgets.PROTECTED`` names the three categories a project manager may not move money into or out
of alone. The ``is_protected`` Check on the row is the *display* of that rule, not the rule itself
— and if the two are allowed to disagree, the control disappears with no diff anywhere. Somebody
unticks Contingency one afternoon, every later reallocation stops asking for a second approval,
and the only trace is a Version row nobody reads.

So ``validate`` re-asserts the flag rather than rejecting the save, which is the same choice
``Quality Settings`` makes and for the same reason: a repair keeps the form usable while a
rejection leaves somebody unable to save a document they did not break. The correction is
msgprinted so it is visible rather than silent.

Note the direction this permits: a category **not** in the tuple may be given the flag freely. A
site that wants Equipment protected can tick it and the reallocation rule honours it immediately.
Protection can always be added in the Desk; it can only be *removed* in a diff.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import budgets


class ProjectBudgetCategory(Document):
	def validate(self):
		self._enforce_code_level_protection()
		self._normalise_sources()

	def _enforce_code_level_protection(self):
		"""Re-assert protection for a category the code treats as protected."""
		if not budgets.is_protected(self.category_name):
			return
		if self.is_protected:
			return
		self.is_protected = 1
		frappe.msgprint(
			_(
				"{0} is a protected category, so its protection has been restored. Reallocations "
				"touching it need a second approval."
			).format(frappe.bold(self.category_name)),
			indicator="orange",
			alert=True,
		)

	def _normalise_sources(self):
		"""An unset source means None, not an empty string.

		The Select ships a ``None`` default, but a row created before a field existed — or one
		written by a patch that omitted it — reads empty. ``budgets.coverage`` already treats
		empty as No Source, so this is tidiness rather than a guard; it keeps the stored value and
		the rendered one saying the same thing.
		"""
		for fieldname in ("committed_source", "actual_source"):
			if not self.get(fieldname):
				self.set(fieldname, budgets.SOURCE_NONE)
