"""Whitelisted endpoints for the MDM Integration (Device-Manager gated).

* ``test_connection`` — list one provider's devices (Mock returns canned data).
* ``trigger_sync`` — run one provider's device sync now and return the counters.
* ``remote_action`` — the manager-UI path into ``actions.execute_device_action``
  (the Managed Device form button; the AI-assistant path is the gated tools).
* ``confirm_device`` — a Device Manager confirms a Discovered device and says
  who owns it. The only way a Discovered device becomes Managed.

``test_connection`` and ``trigger_sync`` are the MDM Settings form's buttons
(``doctype/mdm_settings/mdm_settings.js``).
"""

import frappe
from frappe import _

from erpnext_enhancements.api.device_management import MANAGER_ROLES
from erpnext_enhancements.mdm_integration.actions import execute_device_action
from erpnext_enhancements.mdm_integration.client import get_provider
from erpnext_enhancements.mdm_integration.sync import clear_provider_auth_block, run_device_sync
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
		# A passing test means the credentials work now — lift any auth pause so
		# the scheduler resumes automatic syncs.
		clear_provider_auth_block(settings, provider)
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


@frappe.whitelist(methods=["POST"])
def confirm_device(device, ownership):
	"""Confirm a Discovered device: a person has checked it is ours to manage, and
	who owns it.

	The sync creates provider devices it cannot match as Discovered, with
	ownership defaulting to Company. Until confirmed, a full wipe is refused
	(``actions.execute_device_action``), because the BYOD guard is only as good as
	that ownership field. Recorded on the device's timeline.
	"""
	_check_manager()
	if ownership not in ("Company", "BYOD"):
		frappe.throw(_("Ownership must be Company or BYOD."), frappe.ValidationError)
	doc = frappe.get_doc("Managed Device", device)
	if doc.mdm_link_state != "Discovered":
		frappe.throw(_("{0} is not awaiting confirmation.").format(doc.name), frappe.ValidationError)
	doc.ownership = ownership
	doc.mdm_link_state = "Managed"
	doc.save()
	doc.add_comment("Info", _("Confirmed as a {0} device by {1}.").format(ownership, frappe.session.user))
	return {"device": doc.name, "ownership": doc.ownership, "mdm_link_state": doc.mdm_link_state}
