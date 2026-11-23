# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One refund or credit on a Supplier Return.

A row with a Payment Entry is this return's share of that payment
(``supplier_return_rules.split_payment``), so a payment that covers several returns is counted
once; a row without one was typed by hand. Rows are added by the form's buttons and by the sync
from linked documents (``inventory_enhancements/supplier_returns.py``), never in the grid.
"""

from frappe.model.document import Document


class SupplierReturnPayment(Document):
	pass
