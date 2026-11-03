# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One approval link for a Service Call and its evidence (v1.777.0).

Made by ``sapphire_maintenance/service_call_approval.py`` only, the way a Visit Sign-Off Request
is made by ``sign_off.py``: the doctype is ``in_create`` and every field is read-only, so the
Desk shows the evidence and edits none of it.
"""

from frappe.model.document import Document


class ServiceCallApprovalRequest(Document):
	pass
