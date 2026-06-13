"""Controller for the Inventory Count Session doctype.

A persistent, resumable physical-count audit run by one clerk. Each counted row
lives in the ``lines`` child table (Inventory Count Line) with the system-qty
snapshot and variance captured at scan time. Finalizing the session (see
``erpnext_enhancements.api.inventory_scanner.finalize_session``) aggregates the
lines per (item, warehouse) into a draft Stock Reconciliation for a Stock
Manager to review and submit; ``stock_reconciliation`` links back to it.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class InventoryCountSession(Document):
	def validate(self):
		# Keep each line's variance consistent with its counted/system qty,
		# whether the line was appended by the scanner API or edited in the desk.
		for line in self.lines:
			line.variance = flt(line.counted_qty) - flt(line.system_qty)
