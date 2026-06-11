"""AI Pending Action — a proposed AI mutation awaiting human confirmation.

Created by the write gate in ``assistant_tools/_gate.py`` whenever an AI
client asks FAC to mutate something while gating is enabled; transitions
(Confirm / Cancel) happen exclusively through the whitelisted endpoints in
``assistant_tools/gating_api.py`` (the form buttons call them by dotted path).
Direct status edits in the desk are blocked so the lifecycle stays honest.

Lifecycle: Pending → Confirmed (transient, while executing) → Executed/Failed,
or Pending → Cancelled / Expired.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class AIPendingAction(Document):
	def validate(self):
		if self.is_new():
			return
		if frappe.flags.ai_action_transition:
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status:
			frappe.throw(
				_(
					"Pending-action status can only change through the Confirm / Cancel "
					"buttons (or expiry) — not by editing the document."
				)
			)
