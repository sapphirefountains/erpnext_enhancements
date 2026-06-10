"""Owner Contract phase row (Design & Engineering / Construction & Installation / Ongoing Maintenance): included flag, fee, retainer.

Rows live on Project Contract; rendered into the printed agreement by the
contract template (see templates/contracts/).
"""

from frappe.model.document import Document


class ContractPhaseItem(Document):
	pass
