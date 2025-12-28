import frappe
from frappe.model.document import Document
from frappe import _

class JobInterval(Document):
	def validate(self):
		if self.status == "Open":
			# Check if employee already has an open interval
			existing = frappe.db.exists("Job Interval", {
				"employee": self.employee,
				"status": "Open",
				"name": ["!=", self.name]
			})
			if existing:
				frappe.throw(_("Employee {0} already has an open Job Interval ({1}). Please complete it before starting a new one.").format(self.employee, existing))

		if self.end_time and self.start_time and self.end_time < self.start_time:
			frappe.throw(_("End Time cannot be before Start Time"))
