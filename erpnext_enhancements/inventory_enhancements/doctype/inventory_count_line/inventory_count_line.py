"""Controller for the Inventory Count Line child doctype.

One scanned/counted row inside an Inventory Count Session: the storage location
and its resolved warehouse, the item, the system-on-hand snapshot taken at scan
time, the clerk's counted quantity, and the resulting variance. Variance is
recomputed by the parent session's ``validate`` and by the scanner API.
"""

from frappe.model.document import Document


class InventoryCountLine(Document):
	pass
