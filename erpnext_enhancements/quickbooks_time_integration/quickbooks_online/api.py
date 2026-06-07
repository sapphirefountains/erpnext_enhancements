from __future__ import annotations

import secrets

import frappe

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.client import QuickBooksClient
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import (
	link_existing_record as run_link_existing_record,
	preview_existing_matches as run_preview_existing_matches,
)
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import (
	import_all as run_import_all,
	preview_resync as run_preview_resync,
	retry_failed as run_retry_failed,
	run_resync as run_run_resync,
	sync_entity as run_sync_entity,
)
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.webhooks import handle_webhook


@frappe.whitelist()
def start_oauth(environment=None):
	settings = frappe.get_single("QuickBooks Online Settings")
	if environment:
		settings.environment = environment
		settings.save(ignore_permissions=True)
	state = secrets.token_urlsafe(32)
	frappe.cache().set_value(_state_key(state), 1, expires_in_sec=600)
	return {"authorization_url": QuickBooksClient(settings).build_authorization_url(state, environment), "state": state}


@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, realmId=None, realm_id=None, state=None):
	if not code or not (realmId or realm_id) or not state:
		frappe.throw("QuickBooks OAuth callback is missing code, realmId, or state.")
	if not frappe.cache().get_value(_state_key(state)):
		frappe.throw("QuickBooks OAuth state is invalid or expired.")
	frappe.cache().delete_value(_state_key(state))
	settings = frappe.get_single("QuickBooks Online Settings")
	QuickBooksClient(settings).exchange_code(code, realmId or realm_id)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/app/quickbooks-online-dashboard"


@frappe.whitelist()
def import_all():
	return run_import_all()


@frappe.whitelist()
def preview_resync(entity_types=None):
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_resync(entity_types=entity_types)


@frappe.whitelist()
def run_resync(preview_id):
	return run_run_resync(preview_id)


@frappe.whitelist()
def sync_entity(entity_type, qbo_id):
	return run_sync_entity(entity_type, qbo_id)


@frappe.whitelist()
def retry_failed(log_name=None):
	return run_retry_failed(log_name=log_name)


@frappe.whitelist()
def preview_existing_matches(entity_types=None, limit=100):
	if isinstance(entity_types, str):
		entity_types = [entity.strip() for entity in entity_types.split(",") if entity.strip()]
	return run_preview_existing_matches(entity_types=entity_types, limit=int(limit or 100))


@frappe.whitelist()
def link_existing_record(entity_type, qbo_id, erpnext_doctype, erpnext_name, apply_qbo_data=0):
	return run_link_existing_record(
		entity_type,
		qbo_id,
		erpnext_doctype,
		erpnext_name,
		apply_qbo_data=frappe.utils.cint(apply_qbo_data),
	)


@frappe.whitelist(allow_guest=True)
def quickbooks_webhook():
	return handle_webhook()


@frappe.whitelist()
def get_dashboard_status():
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
	return f"qbo_oauth_state:{state}"
