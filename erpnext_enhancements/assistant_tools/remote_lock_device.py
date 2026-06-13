"""remote_lock_device — gated AI tool: remotely lock a managed mobile device.

Routes through the MDM provider for the device (Miradore for phones/tablets) via
``mdm_integration.actions.execute_device_action``. WRITES and is **HIGH risk**:
listed in the gate's ``APP_MUTATING`` + ``HIGH_RISK`` sets, so when AI write
gating is on it returns an ``awaiting_user_confirmation`` envelope and an AI
Pending Action — nothing is locked until a human clicks Confirm & Execute (the
gate re-runs this ``execute`` as that user). See ``assistant_tools/_gate.py``.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool


class RemoteLockDevice(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "remote_lock_device"
		self.description = (
			"Remotely lock a managed mobile device (phone/tablet, via Miradore). "
			"Provide the Managed Device name (e.g. 'DEV-2026-00012'). This WRITES and "
			"is gated: when AI write gating is on it returns "
			"status='awaiting_user_confirmation' and locks nothing until a human "
			"confirms in ERPNext — then call check_ai_pending_action with the action_id."
		)
		self.category = "Device Management"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Managed Device"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"device": {"type": "string", "description": "Managed Device name to lock."},
			},
			"required": ["device"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		device = (arguments or {}).get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		# Binds the action to whoever confirmed (the gate re-runs this as them).
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(device, "lock", source="Assistant", requested_by=frappe.session.user)


__all__ = ["RemoteLockDevice"]
