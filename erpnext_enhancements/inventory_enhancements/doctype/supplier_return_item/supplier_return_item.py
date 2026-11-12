# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One line of a Supplier Return: an item, how many, and why. Filled from the receipt line it
returns (``inventory_enhancements/supplier_returns._fill_line``); only the quantity, the reason
and the condition notes are the person's."""

from frappe.model.document import Document


class SupplierReturnItem(Document):
	pass
