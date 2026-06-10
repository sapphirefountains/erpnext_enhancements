"""Maintenance Services Agreement plan option row: included flag, price, unit.

Rows live on Project Contract; rendered into the printed agreement by the
contract template (see templates/contracts/).
"""

from frappe.model.document import Document


class ContractServiceOption(Document):
	pass
