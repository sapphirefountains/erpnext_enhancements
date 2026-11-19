"""Remote device-action executor — the one path every action converges on.

Both the manager-UI button (``api.remote_action``) and the gated AI assistant
tools (``assistant_tools/remote_*``) call ``execute_device_action``. It:

1. routes the device to its provider (``client.get_provider_for``) and rejects
   any action the provider's capability map disallows (a wipe can never reach an
   Action1 computer);
2. refuses every action on a **Discovered** device (any wipe mode, lock, locate,
   reboot, run script, deploy patch): nobody has confirmed whose device it is yet,
   and its ownership defaults to Company. Confirming the device clears this;
3. sends a **personally owned (BYOD) device** nothing but Locate and Lock
   (:data:`BYOD_ACTIONS`; owner decision, 2026-11-19): every other action, any wipe
   mode included, is refused before the API call. For a company device the wipe mode
   is resolved by ``routing.resolve_wipe_mode``, and the provider applies its own
   guard on top: Miradore refuses a selective wipe on an enrollment where it would
   factory-reset (``routing.miradore_selective_wipe_refusal``);
4. dispatches to the provider, and writes an immutable **Device Action Log** row
   on every attempt (success or failure). A locate request also leaves a Comment
   on the device's timeline (who asked, and when) before anything is sent;
5. notifies Device Managers on a wipe, a lock, a locate or a failure.

A refused or failed action is recorded even though the request ends in an error
(since v1.832.0). Every caller rolls the transaction back when this raises (see
:func:`_keep_through_rollback`), which would take the row, the Device Managers'
notice and a locate's Comment with it, so each of them is written again after
that rollback and committed by itself. Exactly one of each per attempt, whichever
path the request takes.

The whitelisted callers enforce the role / human-confirmation; this executor
additionally requires the acting user to hold a manager role for UI/Assistant
sources (defense in depth — the AI gate re-runs the tool as the confirming
human, so the check binds to them).
"""

from __future__ import annotations

import functools
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_enhancements.api.device_management import MANAGER_ROLES, _notify_device_managers
from erpnext_enhancements.mdm_integration.client import MDMProviderError, get_provider_for
from erpnext_enhancements.mdm_integration.routing import resolve_wipe_mode
from erpnext_enhancements.mdm_integration.utils import get_settings

# Actions that warrant notifying Device Managers when they succeed.
_NOTIFY_ON_SUCCESS = {"wipe", "lock", "locate"}

#: Everything a personally owned (BYOD) device accepts: it can be found and locked. An
#: allowlist, so a wipe in any mode, a reboot, a script, a patch and any action added later
#: are refused on a personal device unless they are named here.
BYOD_ACTIONS = frozenset({"locate", "lock"})


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

	# A device nobody has confirmed accepts no action at all: its ownership is only a default.
	if doc.get("mdm_link_state") == "Discovered":
		_fail(doc, action, mode, key, source, requested_by, pending_action,
			f"{doc.name} was discovered by the {key} sync and nobody has confirmed who owns it. "
			f"Confirm the device (and its ownership) on its form before running a '{action}' action.")

	# A personally owned device accepts Locate and Lock, and nothing else.
	if (doc.get("ownership") or "") == "BYOD" and action not in BYOD_ACTIONS:
		_fail(doc, action, mode, key, source, requested_by, pending_action,
			f"{doc.name} is personally owned (BYOD). A personal device accepts only Locate and Lock, "
			f"so the '{action}' action was not sent.")

	# Wipe mode — resolve the effective mode (or refuse) BEFORE any API call.
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

	# A locate request leaves a record on the device's timeline before anything is sent, and the
	# record stays when the request then fails and is rolled back.
	if action == "locate":
		note = _("Location requested by {0} on {1} via {2}.").format(requested_by, now_datetime(), key)
		try:
			doc.add_comment("Info", note)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Device locate request not recorded")
			_fail(doc, action, effective_mode, key, source, requested_by, pending_action,
				f"Could not record the locate request on {doc.name}, so nothing was sent.")
		_keep_through_rollback(functools.partial(_comment_quietly, doc, note))

	# Dispatch.
	success, result, error = True, None, None
	try:
		result = _dispatch(provider, action, provider_id, effective_mode, script, patch)
	except Exception as exc:  # MDMProviderError or transport
		success, error = False, str(exc)

	_write_log(doc, action, effective_mode, key, provider_id, source, requested_by, success, result, error, pending_action)

	if not success:
		_notify(
			subject=_("Device action FAILED: {0} on {1}").format(action, doc.device_name or doc.name),
			message=_("The '{0}' action on {1} failed: {2}").format(action, doc.device_name or doc.name, error),
			device=doc.name,
		)
		frappe.throw(_("Device action '{0}' failed: {1}").format(action, error), MDMProviderError)

	if action in _NOTIFY_ON_SUCCESS:
		_notify(
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
	"""Append an immutable Device Action Log row (never raises into the caller).

	The row is built once and written now, in the caller's transaction, and again if that
	transaction is rolled back (:func:`_keep_through_rollback`), so a refused or failed action is
	recorded even though the request ends in an error.
	"""
	try:
		row = {
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
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Device Action Log insert failed")
		return
	_write_and_keep(functools.partial(_insert_log, row))


def _insert_log(row):
	try:
		frappe.get_doc(dict(row)).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Device Action Log insert failed")


def _notify(subject, message, device):
	"""Notify Device Managers, now and again if the caller's transaction is rolled back."""
	_write_and_keep(functools.partial(_notify_quietly, subject, message, device))


def _notify_quietly(subject, message, device):
	try:
		_notify_device_managers(subject=subject, message=message, device=device)
	except Exception:
		_log_quietly("Device Manager notification failed")


def _comment_quietly(doc, note):
	try:
		doc.add_comment("Info", note)
	except Exception:
		_log_quietly("Device locate request not recorded")


def _log_quietly(title):
	try:
		frappe.log_error(frappe.get_traceback(), title)
	except Exception:
		pass


def _write_and_keep(write):
	"""Run ``write`` now, in the caller's transaction, and keep it through a rollback of that transaction."""
	write()
	_keep_through_rollback(write)


def _keep_through_rollback(write):
	"""Run ``write`` again, and commit it by itself, if the caller's transaction is rolled back.

	Why it is needed: every path into the executor rolls back when it raises.
	* The form button (``api.remote_action``): frappe v16.56.0 ``frappe/app.py`` lines 156-159
	  call ``db.rollback(chain=True)`` when the request raises; only a request that returns
	  reaches ``sync_database``, which commits a POST (lines 161-162 and 445-446).
	* A confirmed assistant action (``assistant_tools.gating_api._confirm_one``) calls
	  ``frappe.db.rollback()`` as soon as the tool raises, commits its own Failed outcome, and
	  then raises, so the request is rolled back again; a batch (``_run_batch``) rolls back
	  after each failed action as well.

	How: ``frappe.db.after_rollback`` (frappe v16.56.0 ``frappe/database/database.py``). A full
	``rollback`` runs ``after_rollback`` after the ROLLBACK, in the fresh transaction (lines
	1215-1229), so ``write`` runs there and nothing the caller had pending is left to be
	committed with it. ``write`` then queues a commit at the end of the same run
	(``CallbackManager.run`` pops callbacks until the queue is empty, frappe v16.56.0
	``frappe/utils/__init__.py`` lines 1161-1165), so the commit comes after every other
	rollback callback instead of clearing the ones still queued, and the first such commit
	covers every record this module rewrote.

	Exactly one record per attempt: ``commit`` resets ``after_rollback`` before it commits
	(``database.py`` lines 1196-1197), so when the first write is committed (a path that does
	not roll back) this never runs, and ``run`` pops each callback once, so a second rollback
	does not repeat it. A rollback to a savepoint runs no callbacks (lines 1212-1214); no path
	into the executor uses one. Nothing here raises: an exception from an ``after_rollback``
	callback would escape from the caller's ``rollback``.
	"""
	try:
		frappe.db.after_rollback.add(functools.partial(_rewrite_after_rollback, write))
	except Exception:
		_log_quietly("Device action record not kept through a rollback")


def _rewrite_after_rollback(write):
	try:
		write()
		frappe.db.after_rollback.add(_commit_rewrites)
	except Exception:
		_log_quietly("Device action record not rewritten after a rollback")


def _commit_rewrites():
	try:
		frappe.db.commit()
	except Exception:
		_log_quietly("Device action record not committed after a rollback")


def _fail(doc, action, mode, provider, source, requested_by, pending_action, message):
	"""Log a refused/failed action and raise — used for policy violations before
	any provider call. The row outlives the rollback that follows (``_write_log``); the
	refusal is still raised."""
	_write_log(doc, action, mode, provider, doc.get("mdm_provider_device_id"), source, requested_by, False, None, message, pending_action)
	frappe.throw(message, MDMProviderError)
