"""Whitelisted endpoints for the MDM Integration (Device-Manager gated).

* ``test_connection`` — list one provider's devices (Mock returns canned data).
* ``trigger_sync`` — run one provider's device sync now and return the counters.
* ``remote_action`` — the manager-UI path into ``actions.execute_device_action``
  (the Managed Device form button; the AI-assistant path is the gated tools).
"""

import frappe
from frappe import _

from erpnext_enhancements.api.device_management import MANAGER_ROLES
from erpnext_enhancements.mdm_integration.actions import execute_device_action
from erpnext_enhancements.mdm_integration.client import get_provider
from erpnext_enhancements.mdm_integration.sync import run_device_sync
from erpnext_enhancements.mdm_integration.utils import get_settings


def _check_manager():
	if not MANAGER_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(_("You are not permitted to manage the MDM integration."), frappe.PermissionError)


@frappe.whitelist()
def test_connection(provider):
	"""List a provider's devices once and report the count (or the error)."""
	_check_manager()
	settings = get_settings()
	try:
		devices = get_provider(provider, settings).list_devices()
		return {
			"ok": True,
			"provider": provider,
			"mode": settings.provider_mode,
			"device_count": len(devices),
		}
	except Exception as exc:
		return {"ok": False, "provider": provider, "mode": settings.provider_mode, "error": str(exc)}


@frappe.whitelist()
def trigger_sync(provider):
	"""Run one provider's device sync now and return the log counters."""
	_check_manager()
	log_name = run_device_sync(provider)
	doc = frappe.get_doc("MDM Sync Log", log_name)
	return {
		"sync_log": log_name,
		"status": doc.status,
		"created": doc.created_count,
		"updated": doc.updated_count,
		"discovered": doc.discovered_count,
		"unmanaged": doc.unmanaged_count,
		"failed": doc.failed_count,
	}


@frappe.whitelist()
def remote_action(device, action, mode=None, script=None, patch=None):
	"""Manager-UI entry into the action executor (the form button calls this)."""
	_check_manager()
	return execute_device_action(
		device, action, mode=mode, source="UI", script=script, patch=patch, requested_by=frappe.session.user
	)
