# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Service Call controller (v1.777.0). The rules live in ``sapphire_maintenance/service_calls.py``;
this keeps the record consistent on every save: the title, the rate kind for the service type,
the copied rate, the visit template and the address as shown."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from erpnext_enhancements.sapphire_maintenance import service_calls


class ServiceCall(Document):
	def before_insert(self):
		# Copied once, at creation: a later edit to the rate table never ripples here.
		service_calls.copy_rates(self)

	def validate(self):
		if not self.get("rate_kind"):
			self.rate_kind = service_calls.default_rate_kind(self.service_type)
		problem = service_calls.rate_row_problem(self.service_type, self.rate_kind)
		if problem:
			frappe.throw(_(problem), title=_("Rate kind"))
		if self.has_value_changed("rate_kind") or self.has_value_changed("service_type"):
			# A different kind picked while the call is still being logged takes its own rate.
			if self.get("status") in ("Requested", None) and not self.is_new():
				self.set(service_calls.rate_field(self.service_type), 0)
				service_calls.copy_rates(self)
		if not self.get("template") or self.has_value_changed("service_type"):
			self.template = service_calls.template_for(self.service_type) or self.get("template")
		if not self.get("status"):
			self.status = "Requested"
		self.address_display = service_calls.address_display(self.get("site_address"))
		self.title = self._title()
		if self.get("project") and self.get("scheduled_date"):
			self._warn_dates()

	def _title(self):
		customer = ""
		try:
			customer = frappe.db.get_value("Customer", self.customer, "customer_name") or self.customer
		except Exception:
			customer = self.customer or ""
		day = getdate(self.scheduled_date) if self.get("scheduled_date") else None
		label = service_calls.service_label(self.service_type)
		return f"{customer} - {label}{' ' + str(day) if day else ''}"[:140]

	def _warn_dates(self):
		"""The visit task follows the scheduled date while the call is still open. Never raises."""
		if not self.has_value_changed("scheduled_date") or self.is_new():
			return
		if self.get("status") in ("Done", "Invoiced", "Canceled"):
			return
		try:
			from frappe.utils import nowdate

			service_calls.redate_tasks(self.project, nowdate(), self.scheduled_date)
		except Exception:
			frappe.log_error(title=service_calls.LOG_TITLE, message=frappe.get_traceback())
