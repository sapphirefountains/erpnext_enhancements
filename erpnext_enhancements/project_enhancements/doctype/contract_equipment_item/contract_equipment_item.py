"""Rental Agreement equipment line: description + serial/ID.

Rows live on Project Contract; rendered into the printed agreement by the
contract template (see templates/contracts/).
"""

from frappe.model.document import Document


class ContractEquipmentItem(Document):
	pass
