import frappe
from frappe.model.document import Document


class QuickBooksOnlineSettings(Document):
	def validate(self):
		if self.sync_enabled and not self.company:
			frappe.throw("ERPNext Company is required before enabling QuickBooks Online sync.")

		if self.environment not in {"Sandbox", "Production"}:
			frappe.throw("Environment must be Sandbox or Production.")

