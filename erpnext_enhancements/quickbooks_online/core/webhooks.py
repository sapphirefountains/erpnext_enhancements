"""Inbound QuickBooks Online webhook handling.

Intuit pushes near-real-time change notifications here when accounting data is
created/updated/deleted in the connected company. The single public entry point
``handle_webhook`` is invoked by the whitelisted ``api.quickbooks_webhook``
(allow_guest) route. It verifies the request signature, archives the raw
notification, then enqueues a background ``sync.sync_entity`` for each changed
entity so the HTTP response returns quickly.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.quickbooks_online.core.sync import store_raw_payload, sync_entity
from erpnext_enhancements.quickbooks_online.core.utils import (
	get_secret,
	get_settings,
	verify_intuit_signature,
)


def handle_webhook():
	"""Verify, archive and dispatch an inbound Intuit webhook notification.

	Flow:
	  1. Read the raw request body and the ``intuit-signature`` header and verify
	     the HMAC against the stored ``webhook_verifier_token``; reject with HTTP
	     401 on mismatch (no payload is processed).
	  2. Store the full notification as a ``QuickBooks Raw Payload`` (audit trail).
	  3. For each changed entity whose realm matches the connected company,
	     enqueue ``sync.sync_entity`` on the "short" queue (the per-entity GET +
	     upsert happens asynchronously, keeping the webhook response fast).
	  4. Stamp ``last_webhook_at``/status on Settings and commit.

	Side effects: DB writes (raw payload, Settings), background jobs, commit.
	Returns ``{"status": "success"}`` on accept.
	"""
	settings = get_settings()
	body = frappe.request.get_data() or b""
	signature = frappe.get_request_header("intuit-signature")
	verifier_token = get_secret(settings, "webhook_verifier_token")
	# Signature check first: never parse or act on an unverified payload.
	if not verify_intuit_signature(body, signature, verifier_token):
		frappe.local.response.http_status_code = 401
		return {"status": "error", "message": "Invalid QuickBooks webhook signature."}

	payload = json.loads(body.decode("utf-8") or "{}")
	store_raw_payload("Webhook", "WebhookNotification", payload, realm_id=settings.realm_id)
	for event in _iter_events(payload):
		# Ignore notifications for any company other than the connected realm.
		if event.get("realm_id") != settings.realm_id:
			continue
		frappe.enqueue(
			"erpnext_enhancements.quickbooks_online.core.sync.sync_entity",
			queue="short",
			entity_type=event["entity_type"],
			qbo_id=event["qbo_id"],
			source="Webhook",
		)
	settings.last_webhook_at = now_datetime()
	settings.status = "Connected"
	settings.status_message = "QuickBooks webhook received."
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return {"status": "success"}


def _iter_events(payload):
	"""Yield flattened change events from an Intuit webhook envelope.

	The QBO payload nests entities under
	``eventNotifications[].dataChangeEvent.entities[]``; this walks that
	structure and yields ``{realm_id, entity_type, qbo_id}`` dicts, skipping any
	entry missing a name or id.
	"""
	for notification in payload.get("eventNotifications", []):
		realm_id = notification.get("realmId")
		for data_change_event in notification.get("dataChangeEvent", {}).get("entities", []):
			entity_type = data_change_event.get("name")
			qbo_id = data_change_event.get("id")
			if entity_type and qbo_id:
				yield {"realm_id": realm_id, "entity_type": entity_type, "qbo_id": qbo_id}

