"""Controller for the Inventory Count Session doctype.

A persistent, resumable physical-count audit. Each counted row lives in the ``lines`` child table
(Inventory Count Line) with the system-qty snapshot captured at scan time. The phone page
(``api.inventory_scanner``) and the Count Desk (``api.count_desk``, v1.737.0) both write here;
a Count Desk count carries a ``scope`` (the bins it covers), a phone count does not.
Finalizing turns the variances into a Stock Reconciliation; ``stock_reconciliation`` links
back to it. A Count Desk post can make several (at most 100 rows each), all listed in
``stock_reconciliations``; a paper count's ``counted_at`` is the time its lines were counted
against.
"""

from frappe.model.document import Document

from erpnext_enhancements.inventory_enhancements import count_desk_rules


class InventoryCountSession(Document):
	def validate(self):
		# A variance is a fact about (item, warehouse), not one line: each line's system_qty is the
		# warehouse's on-hand, and an item spread over two Storage Locations is only short once both
		# are counted. Until v1.737.0 this set every line to its own counted - system_qty on each
		# save, which undid the per-item variance add_count had just stamped (count_desk_rules.groups).
		group_map = count_desk_rules.groups([line.as_dict() for line in self.lines])
		for line in self.lines:
			group = group_map.get((line.item_code, line.warehouse))
			line.variance = group["variance"] if group else 0.0
