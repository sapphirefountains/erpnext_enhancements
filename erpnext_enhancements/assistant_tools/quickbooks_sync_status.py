"""quickbooks_sync_status — QuickBooks Online sync health (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Surfaces the state of the two-way QuickBooks Online accounting sync as
structured data: the connection state (connected? token expiry? last
full-import / CDC / webhook), how many sync runs are sitting in Failed, and the
most recent QuickBooks Sync Log rows with their per-entity counters. Pass a
specific log name to get that run's summary (created/updated/failed counts plus
its error message). Read-only; every list goes through ``frappe.get_list`` so
permissions are enforced.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, require_doc_read

_STATUSES = ["Queued", "Running", "Completed", "Failed"]

_LOG_FIELDS = [
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
	"started_at",
	"finished_at",
	"modified",
]


class QuickbooksSyncStatus(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "quickbooks_sync_status"  # must match module filename
		self.description = (
			"Health of the QuickBooks Online accounting sync. Returns the connection "
			"state (connected/not connected, the QBO realm id, token expiry, sync "
			"enabled, and the last full-import / CDC-poll / webhook timestamps), the "
			"number of sync runs currently in Failed, and the most recent QuickBooks "
			"Sync Log entries with their per-entity counters (created/updated/linked/"
			"failed/conflict/manual-review). Use it to answer 'is QuickBooks "
			"connected', 'did the last sync work', or 'what's failing'. Pass "
			"'sync_log' to get one run's summary including its error message. "
			"Read-only."
		)
		self.category = "Accounting"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "QuickBooks Sync Log"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"sync_log": {
					"type": "string",
					"description": "A QuickBooks Sync Log name (e.g. QBO-SYNC-2026-00001) to return that run's summary instead of the overview.",
				},
				"status": {
					"type": "string",
					"enum": _STATUSES,
					"description": "Limit the recent-logs list to a single status (e.g. Failed).",
				},
				"limit": {
					"type": "integer",
					"description": "How many recent sync-log rows to return (default 10, max 50).",
				},
			},
			"required": [],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		args = arguments or {}
		sync_log = args.get("sync_log")
		if sync_log:
			return self._detail(sync_log)
		return self._summary(args)

	def _detail(self, sync_log: str) -> dict[str, Any]:
		if not frappe.db.exists("QuickBooks Sync Log", sync_log):
			return {"success": False, "error": f"QuickBooks Sync Log {sync_log} not found"}
		require_doc_read("QuickBooks Sync Log", sync_log)
		doc = (
			frappe.db.get_value(
				"QuickBooks Sync Log",
				sync_log,
				[*_LOG_FIELDS, "error_message", "retry_count"],
				as_dict=True,
			)
			or {}
		)
		for key in ("started_at", "finished_at", "modified"):
			doc[key] = str(doc.get(key) or "")
		return {"success": True, "sync_log": doc}

	def _summary(self, args: dict[str, Any]) -> dict[str, Any]:
		limit = clamp_limit(args.get("limit"), 10, 50)

		failed = frappe.get_list(
			"QuickBooks Sync Log",
			filters={"status": "Failed"},
			fields=["count(name) as count"],
		)
		failed_records = failed[0]["count"] if failed else 0

		filters = {"status": args["status"]} if args.get("status") else {}
		latest = frappe.get_list(
			"QuickBooks Sync Log",
			filters=filters,
			fields=_LOG_FIELDS,
			order_by="modified desc",
			limit_page_length=limit,
		)
		for row in latest:
			for key in ("started_at", "finished_at", "modified"):
				row[key] = str(row.get(key) or "")

		return {
			"success": True,
			"settings": self._settings(),
			"failed_records": failed_records,
			"latest_logs": latest,
		}

	def _settings(self) -> dict[str, Any] | None:
		if not frappe.has_permission("QuickBooks Online Settings", "read"):
			return None
		try:
			doc = frappe.get_cached_doc("QuickBooks Online Settings")
		except Exception:
			return None
		return {
			"environment": doc.get("environment"),
			"company": doc.get("company"),
			"sync_enabled": bool(doc.get("sync_enabled")),
			"connection_status": doc.get("status"),
			"status_message": doc.get("status_message"),
			"realm_bound": bool(doc.get("realm_id")),
			"token_expires_at": str(doc.get("token_expires_at") or ""),
			"last_full_import": str(doc.get("last_full_import") or ""),
			"last_cdc_sync": str(doc.get("last_cdc_sync") or ""),
			"last_webhook_at": str(doc.get("last_webhook_at") or ""),
		}


__all__ = ["QuickbooksSyncStatus"]
