# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The rate table Service Calls copy from (v1.777.0).

One row per service type and rate kind. Nikolas fills it on prod (decision 2026-11-03: "rates are
entered on prod, never in the repo"), so this doctype ships empty and no fixture or patch writes a
figure into it. A Service Call copies the matching row's rate at creation
(``sapphire_maintenance/service_calls.copy_rates``); editing a row later changes nothing already
quoted, the same stamp semantics as a Service Plan.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.sapphire_maintenance import service_calls


class ServiceCallRate(Document):
	def validate(self):
		problem = service_calls.rate_row_problem(self.service_type, self.rate_kind)
		if problem:
			frappe.throw(_(problem), title=_("Rate kind"))
