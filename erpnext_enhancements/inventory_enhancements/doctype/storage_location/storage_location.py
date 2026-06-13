"""Controller for the Storage Location doctype.

A scannable shelf/bin sub-location beneath a stock-bearing Warehouse. The
Inventory Scanner Audit page resolves a scanned location barcode to its
``warehouse`` so item counts post against the right stock ledger, while the bin
itself is recorded on each count line for physical traceability.

Stock in ERPNext is tracked at the Warehouse level, not per bin: a Storage
Location is a physical pick face that rolls up to exactly one Warehouse.
"""

import frappe
from frappe.model.document import Document


class StorageLocation(Document):
	def before_validate(self):
		# Every location must be scannable; default the printed barcode to the
		# location code so a code-only setup needs no extra data entry.
		if not self.barcode:
			self.barcode = self.location_code
