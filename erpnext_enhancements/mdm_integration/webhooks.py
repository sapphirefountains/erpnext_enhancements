"""Secured inbound webhook receiver for provider change notifications.

A single guest endpoint both providers can POST to; the ``provider`` query-string
parameter says which one is calling. Authentication is a shared secret
(``MDM Settings.webhook_secret``) in the ``X-MDM-Webhook-Secret`` header, verified
constant-time BEFORE any payload is parsed; the raw body is archived and a
single-provider resync is enqueued so the HTTP response returns fast. (Provider
webhook support varies; polling in ``tasks.py`` is the primary path — this is the
near-real-time bonus when a provider can push.) Provider-side setup is in this
package's README::

    POST https://erp.sapphirefountains.com/api/method/
         erpnext_enhancements.mdm_integration.webhooks.handle_webhook?provider=Miradore
    X-MDM-Webhook-Secret: <MDM Settings.webhook_secret>
    Content-Type: application/json

    -> 200 {"message": {"status": "ok"}}

## Why a custom header and not ``Authorization: Bearer``

This endpoint shipped expecting ``Authorization: Bearer <secret>``, and on Frappe
v16 that request never reaches this module. ``frappe.auth.validate_auth`` runs
first, treats any two-part ``Authorization`` header as a credential, tries it as
an OAuth bearer token and then as an API key, and raises ``AuthenticationError``
(HTTP 401, ``{"exc_type": "AuthenticationError"}``) when neither yields a user.
Verified against this endpoint on production 2026-09-22. So the secret travels in
``X-MDM-Webhook-Secret``, which Frappe does not interpret. Never put it in
``Authorization``. (Same defect and same fix as the website lead ingress,
``crm_enhancements/web_lead.py``.)

## Why the provider is read from the query string by hand

Frappe v16 builds ``form_dict`` from a JSON body *instead of* the query string
(``frappe.app.make_form_dict``), so on a JSON POST -- which is what a webhook
sender makes -- ``?provider=Miradore`` never reached a ``provider`` argument, and
the call died with ``TypeError`` (HTTP 500) before any of this code ran. Also
verified on production 2026-09-22. The query string is the part of the request
we configure, so it is the only place the provider is read from; a ``provider``
key in the body is ignored.
"""

import frappe
from frappe.rate_limiter import rate_limit

from erpnext_enhancements.mdm_integration.utils import get_settings, json_dumps, verify_webhook_secret

_SERVICE_USER = "mdm@sapphirefountains.com"
_PROVIDERS = ("Miradore", "Action1")

#: The request header carrying the shared secret. NOT ``Authorization`` -- see the
#: module docstring: Frappe v16 rejects that header before this code runs.
SECRET_HEADER = "X-MDM-Webhook-Secret"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=3600, methods=["POST"])
def handle_webhook(**_body):
	"""Verify the secret header, archive the payload, enqueue a resync.

	``_body`` absorbs whatever keys Frappe lifted out of the request body. The
	payload is read raw below, and the provider only from the query string.

	Rate-limited per caller address (nginx's realip makes that the real caller, not
	the load balancer) so that a leaked secret can add at most 120 Raw Payload rows
	an hour from any one address -- the only thing a caller can write here.
	"""
	provider = _query_provider()
	if provider not in _PROVIDERS:
		frappe.local.response.http_status_code = 404
		return {"status": "error", "message": "unknown provider"}

	if not verify_webhook_secret((frappe.get_request_header(SECRET_HEADER) or "").strip()):
		frappe.local.response.http_status_code = 401
		return {"status": "error", "message": "invalid webhook secret"}

	# Act as the service user for any writes (the pattern api/telephony uses).
	frappe.set_user(_SERVICE_USER if frappe.db.exists("User", _SERVICE_USER) else "Administrator")

	body = frappe.request.get_data(as_text=True) if frappe.request else ""
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
	# Deliberately no write to MDM Settings.status_message: it holds the reason the
	# last sync failed, and a push is no reason to erase that. The Raw Payload row
	# above (source "Webhook") is the record that one arrived.
	frappe.db.commit()

	# Resync in the background so the response returns immediately. One queued
	# resync per provider: a burst of pushes (a fleet re-enrolling) must not become
	# a burst of full device pulls.
	frappe.enqueue(
		"erpnext_enhancements.mdm_integration.webhooks.resync_from_webhook",
		queue="short",
		job_id=f"mdm-webhook-resync-{provider}",
		deduplicate=True,
		provider_key=provider,
	)
	return {"status": "ok"}


def resync_from_webhook(provider_key):
	"""Background: resync one provider after a push.

	Gated like the hourly ``tasks.sync_devices`` -- a disabled provider, or one
	paused after a non-retryable auth failure, is not synced -- but without its
	throttle, because a push is the reason to sync now. ``run_device_sync`` checks
	neither gate itself, and this path used to call it directly, so a provider
	paused on a standing 401 would have been hit again on every push.
	"""
	from erpnext_enhancements.mdm_integration.client import MDMProviderError
	from erpnext_enhancements.mdm_integration.sync import auth_blocked, run_device_sync
	from erpnext_enhancements.mdm_integration.utils import provider_enabled

	settings = get_settings()
	if not provider_enabled(settings, provider_key) or auth_blocked(settings, provider_key):
		return
	try:
		run_device_sync(provider_key)
	except MDMProviderError:
		pass  # recorded on the Sync Log + provider status_message; pauses if permanent
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"MDM webhook resync failed: {provider_key}")


def _query_provider():
	"""The ``provider`` query-string parameter, or None. Never the body: see the module docstring."""
	if not frappe.request:
		return None
	return frappe.request.args.get("provider")
