"""Controller for the Training Insight doctype.

Captures a learning example for the AI assistant: a customer issue
(``original_issue``), how a human actually resolved it
(``human_resolution_path``), and a distilled rule for next time
(``suggested_heuristic``), optionally scoped to a ``customer``. Randomly named.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class TrainingInsight(Document):
	pass
