"""Hand-Off Attendee Role child-table doctype controller.

One row per function (Sales / Production / Billing) on **ERPNext Enhancements
Settings → Hand-Off Attendees**. Resolution lives in
:func:`erpnext_enhancements.crm_enhancements.handoff.resolve_attendees` — an
explicit ``email`` wins, otherwise every holder of ``role``. No custom
controller logic.
"""

from frappe.model.document import Document


class HandOffAttendeeRole(Document):
	pass
