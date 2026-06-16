"""Whitelisted RPC entry points for the QuickBooks Online integration.

This is the public surface called by the browser (Settings form, dashboard
page) and by Intuit (the webhook). Functions here are thin ``@frappe.whitelist``
wrappers that handle permission boundaries, OAuth CSRF state and light argument
coercion, then delegate to the implementation modules:
``client`` (OAuth), ``sync`` (import/resync/entity/retry), ``mapping``
(link/preview existing matches) and ``webhooks`` (inbound notifications).

OAuth note: ``start_oauth`` mints a one-time ``state`` token cached for 10
minutes; ``oauth_callback`` (guest-accessible, as Intuit redirects the browser
to it) validates and consumes that token to prevent CSRF before exchanging the
authorization code.
"""

from __future__ import annotations

import secrets

import frappe

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.mapping import (
	link_existing_record as run_link_existing_record,
	preview_existing_matches as run_preview_existing_matches,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	import_all as run_import_all,
	preview_resync as run_preview_resync,
	retry_failed as run_retry_failed,
	run_resync as run_run_resync,
	sync_entity as run_sync_entity,
)
from erpnext_enhancements.quickbooks_online.core.webhooks import handle_webhook


@frappe.whitelist()
def start_oauth(environment=None):
	"""Begin the OAuth2 connect flow; return the Intuit authorization URL.

	Optionally switches the environment (Sandbox/Production) on Settings first,
	then mints a single-use CSRF ``state`` token cached for 10 minutes. The
	browser is sent to the returned ``authorization_url``. Invoked by the
	"Connect QuickBooks" buttons on the Settings form and dashboard.
	"""
	settings = frappe.get_single("QuickBooks Online Settings")
	if environment:
		settings.environment = environment
		settings.save(ignore_permissions=True)
	# One-time CSRF token validated on the callback; expires in 10 minutes.
	state = secrets.token_urlsafe(32)
	frappe.cache().set_value(_state_key(state), 1, expires_in_sec=600)
	return {"authorization_url": QuickBooksClient(settings).build_authorization_url(state, environment), "state": state}


@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, realmId=None, realm_id=None, state=None):
	"""OAuth2 redirect target (guest): validate state, exchange code, redirect.

	Guest-accessible because Intuit redirects the user's browser here. Rejects
	the request unless code/realmId/state are all present and ``state`` matches an
	unexpired cached token (CSRF protection), then consumes the token and exchanges
	the code for tokens (stored on Settings via the client). Finally redirects the
	browser to the dashboard page. ``realmId`` is QBO's company id.
	"""
	if not code or not (realmId or realm_id) or not state:
		frappe.throw("QuickBooks OAuth callback is missing code, realmId, or state.")
	# Reject unless the state matches the token minted by start_oauth (anti-CSRF).
	if not frappe.cache().get_value(_state_key(state)):
		frappe.throw("QuickBooks OAuth state is invalid or expired.")
	frappe.cache().delete_value(_state_key(state))
	settings = frappe.get_single("QuickBooks Online Settings")
	QuickBooksClient(settings).exchange_code(code, realmId or realm_id)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/app/quickbooks-online-dashboard"


@frappe.whitelist()
def import_all():
	"""RPC: run a full QBO import (dashboard/Settings "Import All"). Returns log name."""
	return run_import_all()


@frappe.whitelist()
def preview_resync(entity_types=None):
	"""RPC: build a dry-run resync preview. ``entity_types`` may be a CSV string."""
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_resync(entity_types=entity_types)


@frappe.whitelist()
def run_resync(preview_id):
	"""RPC: apply a previously generated preview (overwrite resync)."""
	return run_run_resync(preview_id)


@frappe.whitelist()
def sync_entity(entity_type, qbo_id):
	"""RPC: fetch and upsert a single QBO entity (dashboard per-entity Sync)."""
	return run_sync_entity(entity_type, qbo_id)


@frappe.whitelist()
def retry_failed(log_name=None):
	"""RPC: re-run failed sync logs (dashboard "Retry Failed"); optionally one log."""
	return run_retry_failed(log_name=log_name)


@frappe.whitelist()
def preview_existing_matches(entity_types=None, limit=100):
	"""RPC: suggest ERPNext records to link for unmapped QBO entities (CSV-tolerant)."""
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_existing_matches(entity_types=entity_types, limit=int(limit or 100))


@frappe.whitelist()
def link_existing_record(entity_type, qbo_id, erpnext_doctype, erpnext_name, apply_qbo_data=0):
	"""RPC: manually link a QBO entity to a chosen ERPNext record (link dialog)."""
	return run_link_existing_record(
		entity_type,
		qbo_id,
		erpnext_doctype,
		erpnext_name,
		apply_qbo_data=frappe.utils.cint(apply_qbo_data),
	)


@frappe.whitelist(allow_guest=True)
def quickbooks_webhook():
	"""RPC (guest): inbound Intuit webhook endpoint. Delegates to webhooks.handle_webhook.

	Guest-accessible because Intuit posts here unauthenticated; the handler
	verifies the HMAC signature before doing anything.
	"""
	return handle_webhook()


@frappe.whitelist()
def get_dashboard_status():
	"""RPC: snapshot of connection state, failed-log count and recent logs.

	Read-only aggregate consumed by the dashboard page to render status tiles and
	the recent-sync-logs list.
	"""
	settings = frappe.get_single("QuickBooks Online Settings")
	failed_records = frappe.db.count("QuickBooks Sync Log", {"status": "Failed"})
	latest_logs = frappe.get_all(
		"QuickBooks Sync Log",
		fields=[
			"name",
			"sync_type",
			"status",
			"entity_type",
			"created_count",
			"updated_count",
			"linked_count",
			"deleted_count",
			"conflict_count",
			"manual_review_count",
			"failed_count",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=10,
	)
	return {
		"settings": {
			"environment": settings.environment,
			"company": settings.company,
			"sync_enabled": settings.sync_enabled,
			"realm_id": settings.realm_id,
			"status": settings.status,
			"status_message": settings.status_message,
			"last_full_import": settings.last_full_import,
			"last_cdc_sync": settings.last_cdc_sync,
			"last_webhook_at": settings.last_webhook_at,
		},
		"failed_records": failed_records,
		"latest_logs": latest_logs,
	}


def _state_key(state):
	"""Cache key namespacing the one-time OAuth CSRF state token."""
	return f"qbo_oauth_state:{state}"
