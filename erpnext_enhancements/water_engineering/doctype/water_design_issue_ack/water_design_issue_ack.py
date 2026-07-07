# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One acknowledged design warning: who signed off on which issue, when.

Rows are appended by ``api/water_design.acknowledge_issue`` and pruned by the
parent's validate when their issue key no longer matches a live issue (a stale
acknowledgement must never grandfather a new problem). Deleting a row in the
grid un-acknowledges the warning.
"""

from frappe.model.document import Document


class WaterDesignIssueAck(Document):
	pass
