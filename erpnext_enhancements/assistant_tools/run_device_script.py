"""run_device_script — gated AI tool: run a script on a managed computer (Action1).

Routes to Action1 via ``mdm_integration.actions.execute_device_action``. WRITES
and **HIGH risk** — arbitrary remote code is as dangerous as a wipe, so it is in
the gate's ``HIGH_RISK`` set. Runs nothing until a human confirms in the desk.
"""

from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for


class RunDeviceScript(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "run_device_script"
		self.description = (
			"Run a script on a managed computer (laptop/desktop, via Action1). "
			"Provide the Managed Device name and the 'script' to run. This WRITES, "
			"runs ARBITRARY code on the endpoint, and is HIGH risk — it runs nothing "
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
				"script": {"type": "string", "description": "The script body to execute on the endpoint."},
			},
			"required": ["device", "script"],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from erpnext_enhancements.mdm_integration.actions import execute_device_action

		args = arguments or {}
		device = args.get("device")
		if not device or not frappe.db.exists("Managed Device", device):
			frappe.throw(_("Unknown Managed Device: {0}").format(device), frappe.ValidationError)
		script = (args.get("script") or "").strip()
		if not script:
			frappe.throw(_("A non-empty 'script' is required."), frappe.ValidationError)
		if not frappe.has_permission("Managed Device", "write"):
			frappe.throw(_("You do not have permission to act on devices."), frappe.PermissionError)
		return execute_device_action(
			device, "run_script", source="Assistant", script=script, requested_by=frappe.session.user
		)


__all__ = ["RunDeviceScript"]
