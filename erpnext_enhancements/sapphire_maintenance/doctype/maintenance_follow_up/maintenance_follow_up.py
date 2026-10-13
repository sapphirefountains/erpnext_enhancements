"""Maintenance Follow-Up: one thing a visit found needs doing, tracked until it is invoiced.

Made from a visit's ``follow_ups`` rows when the visit is approved
(``sapphire_maintenance/follow_ups.py``, v1.600.0, D3 of Phase D). Lisa wanted to "get it over
to Brian and follow it through until the new part is installed and invoiced": the status runs
Open, Quoted, Ordered, Installed, Invoiced (or Canceled), and the Visit Review page's
follow-up list moves it on and makes the Opportunity. "Invoiced" is set by hand until
invoicing moves from QuickBooks to ERPNext.
"""

from frappe.model.document import Document

#: The title in lists and links: the first line of what is needed, shortened.
TITLE_LENGTH = 80


class MaintenanceFollowUp(Document):
	def validate(self):
		first_line = (self.get("description") or "").strip().splitlines()
		title = first_line[0] if first_line else ""
		self.title = title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1].rstrip() + "…"
