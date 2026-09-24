"""AI Pending Action — a proposed AI mutation awaiting human confirmation.

Created by the write gate in ``assistant_tools/_gate.py`` whenever an AI
client asks FAC to mutate something while gating is enabled; transitions
(Confirm / Cancel) happen exclusively through the whitelisted endpoints in
``assistant_tools/gating_api.py`` (the form buttons call them by dotted path).
Direct status edits in the desk are blocked so the lifecycle stays honest.

Lifecycle: Pending → Confirmed (transient, while executing) → Executed/Failed,
or Pending → Cancelled / Expired.

``sealed_arguments`` (Password, hidden) holds the credential-like values that
``arguments`` shows as ***REDACTED***, encrypted in __Auth, so confirming runs
what was proposed. Only a Pending action needs them. Every save that leaves the
action in any other status deletes them. That covers the Confirmed transition
before execution, Cancel, and the expiry sweep, and the purge removes the rest
with the document.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class AIPendingAction(Document):
	def validate(self):
		# Before the early returns: this must run on every save, transitions included.
		# Frappe runs validate() before _save_passwords(), so a cleared Password field
		# deletes its __Auth row in this same save.
		if self.status != "Pending" and self.get("sealed_arguments"):
			self.sealed_arguments = None
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
