"""reboot_device — gated AI tool: remotely reboot a managed computer (Action1).

Routes to Action1 via ``mdm_integration.actions.execute_device_action``. WRITES
(disruptive but not destructive) → gated, Medium risk. Reboots nothing until a
human confirms in the desk.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool


class RebootDevice(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "reboot_device"
		self.description = (
			"Remotely reboot a managed computer (laptop/desktop, via Action1). "
			"Provide the Managed Device name. This WRITES and is gated: it reboots "
			"nothing until a human confirms in ERPNext."
		)
		self.category = "Device Management"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Managed Device"
		self.inputSchema = {
			"type": "object",
			"properties": {"device": {"type": "string", "description": "Managed Device name to reboot."}},
			"required": ["device"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		device = (arguments or {}).get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(device, "reboot", source="Assistant", requested_by=frappe.session.user)


__all__ = ["RebootDevice"]
