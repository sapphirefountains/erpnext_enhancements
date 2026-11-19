# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One row of Product Build Settings' carrier table: a transporter Supplier, which carrier it is,
and its tracking page (``shipping_rules.carrier_for``).
"""

from frappe.model.document import Document


class ProductCarrier(Document):
	pass
