# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One box on a Warehouse Map (a child row). Every rule about it is the parent's:
``inventory_enhancements/warehouse_map_rules.py``, applied by ``WarehouseMap.validate``."""

from frappe.model.document import Document


class WarehouseMapShape(Document):
	pass
