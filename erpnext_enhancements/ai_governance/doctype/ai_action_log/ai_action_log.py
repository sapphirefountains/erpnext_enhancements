"""AI Action Log — append-only record of executed AI mutations.

Belt-and-braces append-only enforcement (Frappe has no native write lock):
the permission rows grant read + create only (no write/delete, even for
System Manager), every field is ``read_only``, the controller rejects updates
outright, and ``on_trash`` only passes for the retention job (which sets
``frappe.flags.ai_log_purge``). Rows are inserted server-side by the write
gate / confirm flow with ``ignore_permissions=True``.

What this adds beyond native Version history and FAC's Assistant Audit Log:
intent (summary/risk), the human decision (link to the Pending Action),
``auto_approved`` provenance, and attempts that failed before any document
was touched — with retention the app controls (FAC purges its own log on its
own schedule).
"""

import frappe
from frappe import _
from frappe.model.document import Document


class AIActionLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("AI Action Log entries are append-only and cannot be edited."))

	def on_trash(self):
		if not frappe.flags.ai_log_purge:
			frappe.throw(
				_("AI Action Log entries can only be removed by the retention job.")
			)
