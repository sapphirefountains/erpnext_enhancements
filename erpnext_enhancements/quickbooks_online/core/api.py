"""Whitelisted RPC entry points for the QuickBooks Online integration.

This is the public surface called by the browser (Settings form, dashboard
page) and by Intuit (the webhook). Functions here are thin ``@frappe.whitelist``
wrappers that handle permission boundaries, OAuth CSRF state and light argument
coercion, then delegate to the implementation modules:
``client`` (OAuth), ``sync`` (import/resync/entity/retry), ``mapping``
(link/preview existing matches) and ``webhooks`` (inbound notifications).

Access control: the sync engine runs with ``ignore_permissions=True``, so these
RPC entry points are the *only* access-control boundary. Every privileged
endpoint therefore calls ``_require_qbo_operator`` first; without it any
logged-in user could invoke connect/disconnect/import/resync directly via
``/api/method``. The two guest callbacks (``oauth_callback`` /
``quickbooks_webhook``) are intentionally exempt -- they are unauthenticated by
necessity and gated instead by the one-time OAuth ``state`` token and the
webhook HMAC signature respectively.

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
)
from erpnext_enhancements.quickbooks_online.core.mapping import (
	preview_existing_matches as run_preview_existing_matches,
)
from erpnext_enhancements.quickbooks_online.core.opening_balances import (
	sync_opening_balances as run_sync_opening_balances,
)
from erpnext_enhancements.quickbooks_online.core.reconcile import (
	compare_account_balances as run_compare_account_balances,
)
from erpnext_enhancements.quickbooks_online.core.reconcile import (
	reconcile_transactions as run_reconcile_transactions,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	import_all as run_import_all,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	preview_resync as run_preview_resync,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	retry_failed as run_retry_failed,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	run_resync as run_run_resync,
)
from erpnext_enhancements.quickbooks_online.core.sync import (
	sync_entity as run_sync_entity,
)
from erpnext_enhancements.quickbooks_online.core.utils import clear_oauth_tokens
from erpnext_enhancements.quickbooks_online.core.webhooks import handle_webhook

# Roles permitted to operate the QuickBooks Online integration. Mirrors the
# Settings doc's access (System Manager full, Accounts Manager read) -- the
# accounting operators -- and excludes ordinary authenticated users.
QBO_OPERATOR_ROLES = ("System Manager", "Accounts Manager")

# RQ timeout (seconds) for the long-running background syncs enqueued below. A
# full first import of a large company pages through tens of thousands of records
# sequentially; this is set generously (10h) so the worker can't kill it mid-run.
# The work is idempotent, so letting an over-long run finish is safe.
QBO_JOB_TIMEOUT = 36000


def _require_qbo_operator():
	"""Throw ``frappe.PermissionError`` unless the user holds a QBO operator role.

	The single access-control gate for the privileged RPCs below (the engine
	itself runs with ``ignore_permissions``). Administrator passes implicitly.
	"""
	frappe.only_for(QBO_OPERATOR_ROLES)


@frappe.whitelist()
def start_oauth(environment=None):
	"""Begin the OAuth2 connect flow; return the Intuit authorization URL.

	Optionally switches the environment (Sandbox/Production) on Settings first,
	then mints a single-use CSRF ``state`` token cached for 10 minutes. The
	browser is sent to the returned ``authorization_url``. Invoked by the
	"Connect QuickBooks" buttons on the Settings form and dashboard.
	"""
	_require_qbo_operator()
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
def disconnect():
	"""Disconnect from QuickBooks Online: revoke at Intuit, then clear local state.

	User-initiated (the Settings / dashboard "Disconnect QuickBooks" button).
	Best-effort revokes the OAuth2 grant at Intuit, then forgets the stored
	tokens/realm and marks the connection Not Connected so the next Connect is a
	clean re-consent. Returns ``{"revoked": bool}`` -- whether Intuit acknowledged
	the revoke; local state is cleared either way.
	"""
	_require_qbo_operator()
	settings = frappe.get_single("QuickBooks Online Settings")
	revoked = QuickBooksClient(settings).revoke_tokens()
	clear_oauth_tokens(settings)
	return {"revoked": bool(revoked)}


@frappe.whitelist()
def disconnect_callback():
	"""Intuit "Disconnect URL" landing target: forget local tokens, then redirect.

	Register this as the app's Disconnect URL in the Intuit developer portal. When
	a user disconnects the app from Intuit's side (My Apps), Intuit has already
	revoked the grant, so this only clears the now-dead local tokens and marks the
	connection Not Connected, then redirects to the dashboard. Unlike
	``oauth_callback`` it is NOT ``allow_guest`` and is operator-gated -- requiring
	a logged-in operator keeps the endpoint from being used to force a disconnect.
	"""
	_require_qbo_operator()
	settings = frappe.get_single("QuickBooks Online Settings")
	clear_oauth_tokens(settings, message="Disconnected from the Intuit side. Reconnect to resume sync.")
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/app/quickbooks-online-dashboard"


@frappe.whitelist()
def import_all():
	"""RPC: enqueue a full QBO import on the long queue (returns immediately).

	The import pages through every QBO record sequentially and can run for many
	minutes on a real company -- far longer than the HTTP gateway/worker timeout,
	which surfaced as a 504 when this ran inline. It now runs as a background job;
	progress is tracked in QuickBooks Sync Log. Returns ``{"status": "queued"}``,
	or ``{"status": "already_running"}`` when an import is already in progress.
	"""
	_require_qbo_operator()
	if frappe.db.exists("QuickBooks Sync Log", {"sync_type": "Import All", "status": "Running"}):
		return {"status": "already_running"}
	frappe.enqueue(run_import_all, queue="long", timeout=QBO_JOB_TIMEOUT)
	return {"status": "queued"}


@frappe.whitelist()
def preview_resync(entity_types=None):
	"""RPC: build a dry-run resync preview. ``entity_types`` may be a CSV string."""
	_require_qbo_operator()
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_resync(entity_types=entity_types)


@frappe.whitelist()
def run_resync(preview_id):
	"""RPC: enqueue applying a previously generated preview (overwrite resync).

	Replays the preview's stored payloads, which can be a large batch, so it runs
	on the long queue like ``import_all`` to avoid a gateway timeout. The preview
	id is validated synchronously for immediate feedback. Returns
	``{"status": "queued"}``.
	"""
	_require_qbo_operator()
	if not preview_id or not frappe.db.exists("QuickBooks Sync Log", preview_id):
		frappe.throw("A valid Preview Resync log is required before running overwrite resync.")
	frappe.enqueue(run_run_resync, queue="long", timeout=QBO_JOB_TIMEOUT, preview_id=preview_id)
	return {"status": "queued"}


@frappe.whitelist()
def sync_entity(entity_type, qbo_id):
	"""RPC: fetch and upsert a single QBO entity (dashboard per-entity Sync)."""
	_require_qbo_operator()
	return run_sync_entity(entity_type, qbo_id)


@frappe.whitelist()
def retry_failed(log_name=None):
	"""RPC: enqueue re-running failed sync logs (dashboard "Retry Failed").

	Re-running can replay a full import or CDC pass, so it is backgrounded on the
	long queue like ``import_all``. Returns ``{"status": "queued"}``.
	"""
	_require_qbo_operator()
	frappe.enqueue(run_retry_failed, queue="long", timeout=QBO_JOB_TIMEOUT, log_name=log_name)
	return {"status": "queued"}


@frappe.whitelist()
def preview_existing_matches(entity_types=None, limit=100):
	"""RPC: suggest ERPNext records to link for unmapped QBO entities (CSV-tolerant)."""
	_require_qbo_operator()
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_existing_matches(entity_types=entity_types, limit=int(limit or 100))


@frappe.whitelist()
def link_existing_record(entity_type, qbo_id, erpnext_doctype, erpnext_name, apply_qbo_data=0):
	"""RPC: manually link a QBO entity to a chosen ERPNext record (link dialog)."""
	_require_qbo_operator()
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
def compare_account_balances(as_of_date=None, tolerance=0.01):
	"""RPC: compare QBO Trial Balance against ERPNext GL balances (read-only).

	Backs the "QuickBooks Balance Comparison" report and the dashboard "Compare
	Balances" action. Returns matched/mismatched/qb_only/erp_only buckets.
	"""
	_require_qbo_operator()
	return run_compare_account_balances(as_of_date=as_of_date, tolerance=float(tolerance or 0.01))


@frappe.whitelist()
def reconcile_transactions(entity_types=None, tolerance=1.0):
	"""RPC: compare imported transaction amounts against their QBO payloads (read-only)."""
	_require_qbo_operator()
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_reconcile_transactions(entity_types=entity_types, tolerance=float(tolerance or 1.0))


@frappe.whitelist()
def sync_opening_balances(as_of_date=None, auto_submit=0):
	"""RPC: build an opening Journal Entry from QBO balances (draft unless auto_submit)."""
	_require_qbo_operator()
	return run_sync_opening_balances(as_of_date=as_of_date, auto_submit=auto_submit)


@frappe.whitelist()
def get_dashboard_status():
	"""RPC: snapshot of connection state, failed-log count and recent logs.

	Read-only aggregate consumed by the dashboard page to render status tiles and
	the recent-sync-logs list.
	"""
	_require_qbo_operator()
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
