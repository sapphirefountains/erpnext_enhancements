"""Payment milestone row for SOW and Owner Contract construction schedules.

Rows live on Project Contract; rendered into the printed agreement by the
contract template (see templates/contracts/).
"""

from frappe.model.document import Document


class ContractMilestone(Document):
	pass
