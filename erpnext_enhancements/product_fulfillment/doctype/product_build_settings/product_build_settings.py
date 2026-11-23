# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Product Build Settings: how product orders are built, numbered, tested and shipped.

A Single read through ``product_fulfillment.settings.get_settings`` (``frappe.get_cached_doc``),
never ``get_single_value``: a Single nobody has saved has no ``tabSingles`` rows, and only a whole
document load falls back to the JSON defaults (the serial series, the 365-day warranty, the test
switch and template). Nothing in the app writes a single field of it either, for the reason
CLAUDE.md gives: the first one-field write makes every other field load blank.

The three warehouses default to empty on purpose. Nikolas picks the Build Room shelves later; until
then Work Orders are left as drafts and say why (``rules.resolve_warehouses``).
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.product_fulfillment import rules


class ProductBuildSettings(Document):
	def validate(self):
		if self.serial_no_series and not rules.valid_serial_series(self.serial_no_series):
			frappe.throw(
				_("The serial number series needs a run of # for the number, for example SF-.YY.-.#####.")
			)
		for field in ("build_warehouse", "wip_warehouse", "finished_warehouse"):
			warehouse = self.get(field)
			if warehouse and frappe.db.get_value("Warehouse", warehouse, "is_group"):
				frappe.throw(
					_("{0} is a group warehouse. Pick a warehouse that holds stock.").format(warehouse)
				)
		if self.wip_warehouse and self.wip_warehouse == self.finished_warehouse:
			frappe.throw(
				_(
					"The work-in-progress and finished units warehouses must differ: a unit still being "
					"built would read as ready to ship."
				)
			)
