"""remote_wipe_device — gated AI tool: remotely wipe a managed mobile device.

Routes to Miradore via ``mdm_integration.actions.execute_device_action``. WRITES
and **HIGH risk** (irreversible). The executor enforces the BYOD guard: a
personally-owned device is always coerced to a selective (corporate-data-only)
wipe and a full wipe is refused — the model cannot override this. Gated like
``remote_lock_device``: nothing is wiped until a human confirms in the desk.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

_MODES = ("selective", "full")


class RemoteWipeDevice(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "remote_wipe_device"
		self.description = (
			"Remotely wipe a managed mobile device (phone/tablet, via Miradore). "
			"Provide the Managed Device name and a 'mode': 'selective' (remove "
			"corporate data only — the default and safest) or 'full' (factory reset). "
			"BYOD/personally-owned devices are ALWAYS selective regardless of mode. "
			"This WRITES, is IRREVERSIBLE, and is gated: it wipes nothing until a "
			"human confirms in ERPNext."
		)
		self.category = "Device Management"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Managed Device"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"device": {"type": "string", "description": "Managed Device name to wipe."},
				"mode": {
					"type": "string",
					"enum": list(_MODES),
					"default": "selective",
					"description": "'selective' (corporate data only) or 'full' (factory reset). BYOD is forced selective.",
				},
			},
			"required": ["device"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		args = arguments or {}
		device = args.get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		mode = (args.get("mode") or "selective").strip().lower()
		if mode not in _MODES:
			frappe.throw(_("'mode' must be 'selective' or 'full'."), frappe.ValidationError)
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(device, "wipe", mode=mode, source="Assistant", requested_by=frappe.session.user)


__all__ = ["RemoteWipeDevice"]
