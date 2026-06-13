"""Secured inbound webhook receiver for provider change notifications.

A single guest endpoint both providers can POST to (path carries the provider).
Authentication is a shared **Bearer** secret (``MDM Settings.webhook_secret``),
verified constant-time BEFORE any payload is parsed; the raw body is archived and
a single-provider resync is enqueued so the HTTP response returns fast. Mirrors
the security discipline of ``quickbooks_online/webhooks.py`` /
``api/telephony.py``. (Provider webhook support varies; polling in ``tasks.py`` is
the primary path — this is the near-real-time bonus when a provider can push.)
"""

import frappe

from erpnext_enhancements.mdm_integration.utils import get_settings, json_dumps, verify_webhook_bearer

_SERVICE_USER = "mdm@sapphirefountains.com"


@frappe.whitelist(allow_guest=True)
def handle_webhook(provider):
	"""Verify the Bearer secret, archive the payload, enqueue a resync."""
	if provider not in ("Miradore", "Action1"):
		frappe.local.response.http_status_code = 404
		return {"status": "error", "message": "unknown provider"}

	auth = frappe.get_request_header("Authorization") or ""
	token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else None
	if not verify_webhook_bearer(token):
		frappe.local.response.http_status_code = 401
		return {"status": "error", "message": "invalid webhook secret"}

	# Act as the service user for any writes (the pattern api/telephony uses).
	frappe.set_user(_SERVICE_USER if frappe.db.exists("User", _SERVICE_USER) else "Administrator")

	body = frappe.request.get_data(as_text=True) if frappe.request else ""
	settings = get_settings()
	try:
		payload = frappe.parse_json(body) if body else {}
	except Exception:
		payload = {"_raw": body}

	doc = frappe.new_doc("MDM Raw Payload")
	doc.provider = provider
	doc.source = "Webhook"
	doc.payload = json_dumps(payload)
	doc.received_at = frappe.utils.now_datetime()
	doc.insert(ignore_permissions=True)

	settings.db_set("status_message", f"Webhook received from {provider}", update_modified=False)
	frappe.db.commit()

	# Resync the provider in the background so the response returns immediately.
	frappe.enqueue(
		"erpnext_enhancements.mdm_integration.sync.run_device_sync",
		queue="short",
		provider_key=provider,
	)
	return {"status": "ok"}
