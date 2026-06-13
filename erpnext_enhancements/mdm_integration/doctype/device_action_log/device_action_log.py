"""Controller for the Device Action Log doctype (immutable, ``in_create``).

One append-only row per remote device action attempt. Written by
``mdm_integration.actions.execute_device_action``; rows are never edited.
"""

from frappe.model.document import Document


class DeviceActionLog(Document):
	pass
