# -*- coding: utf-8 -*-
"""Controller stub for a customised Expense Claim Type doctype.

This app ships its own ``enhancements_core`` copy of the HR "Expense Claim Type"
doctype controller. No custom logic — present so the doctype resolves to this
module; behaviour comes from the standard framework/HR handling and JSON.
"""

import frappe
from frappe.model.document import Document

class ExpenseClaimType(Document):
	pass
