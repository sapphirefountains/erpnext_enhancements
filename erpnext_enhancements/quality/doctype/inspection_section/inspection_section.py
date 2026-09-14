# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A reusable block of checks — WI-075 sub-phase C.

A section is the unit of reuse: "Commissioning" is the same block of checks whether it hangs off
a Build inspection or a Products one, so it is a top-level record that templates point at rather
than a table copied into each of them.

That shape is taken from ``sapphire_maintenance``, which solved the same problem first, and the
reason is structural rather than stylistic: **Frappe has no grandchild tables.** A template
cannot own sections that own checks. So the section is its own DocType with the checks as its
children, and the template holds a thin child row pointing at it.

Each check carries an ``item_key``, minted once and never regenerated. A generated inspection
copies the check and records the key, so reordering or rewording a row later never repoints a
result that has already been recorded.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import stable_keys

KEY_FIELD = "item_key"


class InspectionSection(Document):
	def validate(self):
		self._mint_item_keys()
		self._reject_duplicate_keys()
		self._validate_measurement_bounds()

	def _mint_item_keys(self):
		stable_keys.mint_missing_keys(self.items, frappe.generate_hash, key_field=KEY_FIELD)

	def _reject_duplicate_keys(self):
		"""Frappe's grid row-duplicate action copies read-only fields too, so two rows sharing
		a key is two clicks away rather than hypothetical."""
		duplicates = stable_keys.duplicate_keys(self.items, key_field=KEY_FIELD)
		if duplicates:
			frappe.throw(
				_("Two checks share the same key: {0}. Delete the duplicated row and add a fresh one.").format(
					", ".join(duplicates)
				),
				title=_("Duplicated check"),
			)

	def _validate_measurement_bounds(self):
		"""A min above its max can never pass, and reads as a working check until somebody
		fails it on site and cannot see why."""
		for row in self.items or []:
			if row.check_type != "Measurement":
				continue
			if row.min_value and row.max_value and row.min_value > row.max_value:
				frappe.throw(
					_("Row {0}: minimum ({1}) is above the maximum ({2}), so nothing can pass it.").format(
						row.idx, row.min_value, row.max_value
					),
					title=_("Impossible range"),
				)
