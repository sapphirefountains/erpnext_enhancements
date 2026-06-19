"""deploy_device_patch — gated AI tool: deploy an update to a computer (Action1).

Routes to Action1 via ``mdm_integration.actions.execute_device_action``. WRITES
→ gated, Medium risk. Deploys nothing until a human confirms in the desk.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class DeployDevicePatch(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "deploy_device_patch"
		self.description = (
			"Deploy a specific update/patch to a managed computer (laptop/desktop, "
			"via Action1). Provide the Managed Device name and the 'patch' identifier "
			"(the Action1 update id). This WRITES and is gated: it deploys nothing "
			"until a human confirms in ERPNext."
		)
		self.category = "Device Management"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Managed Device"
		self.annotations = annotations_for(self.name)
		self.inputSchema = {
			"type": "object",
			"properties": {
				"device": {"type": "string", "description": "Managed Device name (a computer)."},
				"patch": {"type": "string", "description": "The update/patch identifier to deploy."},
			},
			"required": ["device", "patch"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		args = arguments or {}
		device = args.get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		patch = (args.get("patch") or "").strip()
		if not patch:
			frappe.throw(_("A 'patch' identifier is required."), frappe.ValidationError)
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(
			device, "deploy_patch", source="Assistant", patch=patch, requested_by=frappe.session.user
		)


__all__ = ["DeployDevicePatch"]
