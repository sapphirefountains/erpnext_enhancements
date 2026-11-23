# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One bill-of-materials line on a Water Feature Design (``bom``).

The parent's validate rewrites every ``Auto`` line from the design model on each
save (``water_engineering/derive.py``) and keeps ``Manual`` lines as they are.
It is the table ``api/designer.push_quotation`` and ``push_material_request``
read: a line with an Item becomes an item line, one without becomes a text line.
"""

from frappe.model.document import Document


class WaterFeatureDesignLine(Document):
	pass
