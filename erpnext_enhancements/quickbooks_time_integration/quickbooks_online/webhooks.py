from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import store_raw_payload, sync_entity
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import (
	get_secret,
	get_settings,
	verify_intuit_signature,
)


def handle_webhook():
	settings = get_settings()
	body = frappe.request.get_data() or b""
	signature = frappe.get_request_header("intuit-signature")
	verifier_token = get_secret(settings, "webhook_verifier_token")
	if not verify_intuit_signature(body, signature, verifier_token):
		frappe.local.response.http_status_code = 401
		return {"status": "error", "message": "Invalid QuickBooks webhook signature."}

	payload = json.loads(body.decode("utf-8") or "{}")
	store_raw_payload("Webhook", "WebhookNotification", payload, realm_id=settings.realm_id)
	for event in _iter_events(payload):
		if event.get("realm_id") != settings.realm_id:
			continue
		frappe.enqueue(
			"erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync.sync_entity",
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
	for notification in payload.get("eventNotifications", []):
		realm_id = notification.get("realmId")
		for data_change_event in notification.get("dataChangeEvent", {}).get("entities", []):
			entity_type = data_change_event.get("name")
			qbo_id = data_change_event.get("id")
			if entity_type and qbo_id:
				yield {"realm_id": realm_id, "entity_type": entity_type, "qbo_id": qbo_id}

