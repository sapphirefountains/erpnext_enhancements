"""Remote device-action executor — the one path every action converges on.

Both the manager-UI button (``api.remote_action``) and the gated AI assistant
tools (``assistant_tools/remote_*``) call ``execute_device_action``. It:

1. routes the device to its provider (``client.get_provider_for``) and rejects
   any action the provider's capability map disallows (a wipe can never reach an
   Action1 computer);
2. enforces the **BYOD wipe guard** (``routing.resolve_wipe_mode``) before the
   API call — a personally-owned device is never full-wiped;
3. dispatches to the provider, and writes an immutable **Device Action Log** row
   on every attempt (success or failure);
4. notifies Device Managers on a wipe or a failure.

The whitelisted callers enforce the role / human-confirmation; this executor
additionally requires the acting user to hold a manager role for UI/Assistant
sources (defense in depth — the AI gate re-runs the tool as the confirming
human, so the check binds to them).
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_enhancements.api.device_management import MANAGER_ROLES, _notify_device_managers
from erpnext_enhancements.mdm_integration.client import MDMProviderError, get_provider_for
from erpnext_enhancements.mdm_integration.routing import resolve_wipe_mode
from erpnext_enhancements.mdm_integration.utils import get_settings

# Actions that warrant notifying Device Managers when they succeed.
_NOTIFY_ON_SUCCESS = {"wipe", "lock"}


def execute_device_action(
	device, action, mode=None, *, source="UI", requested_by=None, script=None, patch=None, pending_action=None
):
	"""Dispatch a remote action to a device's provider, guarded + audited.

	Returns a result dict on success; raises (after logging a failed Device Action
	Log row) on any policy violation or provider error.
	"""
	requested_by = requested_by or frappe.session.user
	if source in ("UI", "Assistant") and not MANAGER_ROLES.intersection(set(frappe.get_roles(requested_by))):
		frappe.throw(_("You are not permitted to run device actions."), frappe.PermissionError)

	doc = frappe.get_doc("Managed Device", device)
	settings = get_settings()
	provider, key = get_provider_for(doc.as_dict(), settings)

	if not provider:
		_fail(doc, action, mode, key, source, requested_by, pending_action,
			f"No MDM provider manages a {doc.device_type or doc.platform or 'device'} like {doc.name}.")
	if not provider.supports(action):
		_fail(doc, action, mode, key, source, requested_by, pending_action,
			f"{key} does not support the '{action}' action.")

	# BYOD wipe guard — resolve the effective mode (or refuse) BEFORE any API call.
	effective_mode = mode
	if action == "wipe":
		effective_mode, err = resolve_wipe_mode(
			doc.ownership,
			mode,
			block_byod_full=bool(settings.block_full_wipe_byod),
			allow_corporate_full=bool(settings.allow_full_wipe_corporate),
		)
		if err:
			_fail(doc, action, mode, key, source, requested_by, pending_action, err)

	provider_id = doc.mdm_provider_device_id
	if not provider_id:
		_fail(doc, action, effective_mode, key, source, requested_by, pending_action,
			f"{doc.name} is not linked to a {key} device yet (run a sync / confirm the discovered device first).")

	# Dispatch.
	success, result, error = True, None, None
	try:
		result = _dispatch(provider, action, provider_id, effective_mode, script, patch)
	except Exception as exc:  # MDMProviderError or transport
		success, error = False, str(exc)

	_write_log(doc, action, effective_mode, key, provider_id, source, requested_by, success, result, error, pending_action)

	if not success:
		_notify_device_managers(
			subject=_("Device action FAILED: {0} on {1}").format(action, doc.device_name or doc.name),
			message=_("The '{0}' action on {1} failed: {2}").format(action, doc.device_name or doc.name, error),
			device=doc.name,
		)
		frappe.throw(_("Device action '{0}' failed: {1}").format(action, error), MDMProviderError)

	if action in _NOTIFY_ON_SUCCESS:
		_notify_device_managers(
			subject=_("Device {0}: {1}").format(action, doc.device_name or doc.name),
			message=_("'{0}'{1} completed on {2} via {3}, requested by {4}.").format(
				action,
				f" ({effective_mode})" if effective_mode else "",
				doc.device_name or doc.name,
				key,
				requested_by,
			),
			device=doc.name,
		)

	return {
		"ok": True,
		"device": doc.name,
		"action": action,
		"mode": effective_mode,
		"provider": key,
		"result": result,
	}


def _dispatch(provider, action, provider_id, mode, script, patch):
	if action == "lock":
		return provider.lock(provider_id)
	if action == "wipe":
		return provider.wipe(provider_id, mode=mode or "selective")
	if action == "locate":
		return provider.locate(provider_id)
	if action == "reboot":
		return provider.reboot(provider_id)
	if action == "run_script":
		if not script:
			raise MDMProviderError("A 'script' is required for run_script.")
		return provider.run_script(provider_id, script)
	if action == "deploy_patch":
		if not patch:
			raise MDMProviderError("A 'patch' identifier is required for deploy_patch.")
		return provider.deploy_patch(provider_id, patch)
	raise MDMProviderError(f"Unknown action: {action}")


def _write_log(doc, action, mode, provider, provider_id, source, requested_by, success, result, error, pending_action):
	"""Append an immutable Device Action Log row (never raises into the caller)."""
	try:
		frappe.get_doc(
			{
				"doctype": "Device Action Log",
				"device": doc.name,
				"action": action,
				"mode": mode,
				"provider": provider,
				"provider_device_id": provider_id,
				"ownership": doc.ownership,
				"source": source,
				"requested_by": requested_by,
				"timestamp": now_datetime(),
				"pending_action": pending_action,
				"success": 1 if success else 0,
				"result": json.dumps(result, default=str, indent=1) if result is not None else None,
				"error": (error or "")[:2000] or None,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Device Action Log insert failed")


def _fail(doc, action, mode, provider, source, requested_by, pending_action, message):
	"""Log a refused/failed action and raise — used for policy violations before
	any provider call."""
	_write_log(doc, action, mode, provider, doc.get("mdm_provider_device_id"), source, requested_by, False, None, message, pending_action)
	frappe.throw(message, MDMProviderError)
