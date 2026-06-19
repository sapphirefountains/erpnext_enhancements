"""locate_device — gated AI tool: request a managed mobile device's location.

Routes to Miradore via ``mdm_integration.actions.execute_device_action``.
Privileged (it pings a person's device), so it is gated (APP_MUTATING) but
classified Medium risk. Nothing happens until a human confirms in the desk.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class LocateDevice(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "locate_device"
		self.description = (
			"Request the current location of a managed mobile device (phone/tablet, "
			"via Miradore). Provide the Managed Device name. Privileged and gated: it "
			"runs only after a human confirms in ERPNext."
		)
		self.category = "Device Management"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Managed Device"
		self.annotations = annotations_for(self.name)
		self.inputSchema = {
			"type": "object",
			"properties": {"device": {"type": "string", "description": "Managed Device name to locate."}},
			"required": ["device"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		device = (arguments or {}).get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(device, "locate", source="Assistant", requested_by=frappe.session.user)


__all__ = ["LocateDevice"]
