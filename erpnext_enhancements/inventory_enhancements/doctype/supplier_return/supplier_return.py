# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Return: something we bought going back to the supplier.

The controller is two lines on purpose. What a save checks is
``inventory_enhancements/supplier_returns.validate`` and every judgement is
``supplier_return_rules``, where it is tested without a bench.

The class name is ``SupplierReturn``: Frappe looks for the DocType name with its spaces removed,
and ``bench migrate`` deletes a DocType whose controller it cannot import.
"""

from frappe.model.document import Document

from erpnext_enhancements.inventory_enhancements import supplier_returns


class SupplierReturn(Document):
	def validate(self):
		supplier_returns.validate(self)

	def on_trash(self):
		supplier_returns.refuse_delete(self)
