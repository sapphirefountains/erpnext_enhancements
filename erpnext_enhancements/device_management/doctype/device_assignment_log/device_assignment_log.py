"""Controller for the Device Assignment Log child table.

One append-only custody row per check-out / transfer / check-in on a Managed
Device. Rows are written by ``erpnext_enhancements.api.device_management`` (the
open row — no ``returned_on`` — is the current holder); no behaviour lives here.
"""

from frappe.model.document import Document


class DeviceAssignmentLog(Document):
	pass
