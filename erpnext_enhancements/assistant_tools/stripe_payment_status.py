"""stripe_payment_status — Stripe Payments operational status (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Reports the health of the Stripe Payments subsystem the way the desk dashboard
does, but as structured data for the assistant: connection/config state, a
payment-status breakdown, the two trouble signals (paid-but-unreconciled
payments and errored webhook events), and the most recent payments. Read-only;
every count and list goes through ``frappe.get_list`` so a user only ever sees
the Stripe Payments they are permitted to read.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, require_doc_read

_STATUSES = ["Draft", "Link Sent", "Processing", "Paid", "Failed", "Expired", "Refunded"]

_RECENT_FIELDS = [
	"name",
	"customer",
	"sales_invoice",
	"amount",
	"currency",
	"status",
	"payment_method_type",
	"channel",
	"payment_entry",
	"modified",
]

_DETAIL_FIELDS = [
	"name",
	"customer",
	"sales_invoice",
	"amount",
	"currency",
	"status",
	"surcharge_amount",
	"payment_method_type",
	"channel",
	"initiated_by",
	"payment_entry",
	"amount_refunded",
	"error_message",
	"stripe_payment_intent",
	"stripe_charge_id",
	"checkout_url",
	"modified",
]


class StripePaymentStatus(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "stripe_payment_status"  # must match module filename
		self.description = (
			"Operational status of the Stripe Payments subsystem (hosted card/ACH "
			"checkout). Returns the connection/config state (environment, enabled, "
			"connection status, last webhook time), a count of Stripe Payments by "
			"status (Draft/Link Sent/Processing/Paid/Failed/Expired/Refunded), two "
			"trouble signals — 'unreconciled_paid' (status Paid but no Payment Entry "
			"linked yet) and 'failed_webhooks' (Stripe Event that errored) — and the "
			"most recent payments. Use it to answer 'are Stripe payments working', "
			"'what failed', or 'which paid charges still need reconciling'. Read-only; "
			"amounts are in each payment's own currency. To inspect one payment in "
			"full, pass 'payment'."
		)
		self.category = "Accounting"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Stripe Payment"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"payment": {
					"type": "string",
					"description": "A Stripe Payment name (e.g. STR-PAY-2026-00001) to return its full detail instead of the summary.",
				},
				"status": {
					"type": "string",
					"enum": _STATUSES,
					"description": "Limit the recent-payments list to a single status.",
				},
				"limit": {
					"type": "integer",
					"description": "How many recent payments to return (default 15, max 100).",
				},
			},
			"required": [],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		args = arguments or {}
		payment = args.get("payment")
		if payment:
			return self._detail(payment)
		return self._summary(args)

	def _detail(self, payment: str) -> dict[str, Any]:
		if not frappe.db.exists("Stripe Payment", payment):
			return {"success": False, "error": f"Stripe Payment {payment} not found"}
		require_doc_read("Stripe Payment", payment)
		doc = frappe.db.get_value("Stripe Payment", payment, _DETAIL_FIELDS, as_dict=True) or {}
		doc["modified"] = str(doc.get("modified") or "")
		return {"success": True, "payment": doc}

	def _summary(self, args: dict[str, Any]) -> dict[str, Any]:
		limit = clamp_limit(args.get("limit"), 15, 100)

		# Status breakdown — permission-aware aggregate over the user's visible rows.
		counts = {
			row["status"]: row["count"]
			for row in frappe.get_list(
				"Stripe Payment",
				fields=["status", "count(name) as count"],
				group_by="status",
			)
		}

		# Trouble signal 1: paid but not yet reconciled to a Payment Entry.
		unreconciled = frappe.get_list(
			"Stripe Payment",
			filters={"status": "Paid", "payment_entry": ["is", "not set"]},
			fields=["count(name) as count"],
		)
		unreconciled_paid = unreconciled[0]["count"] if unreconciled else 0

		result: dict[str, Any] = {
			"success": True,
			"settings": self._settings(),
			"counts_by_status": counts,
			"unreconciled_paid": unreconciled_paid,
			"recent": self._recent(args, limit),
		}

		# Trouble signal 2: errored webhook events (best-effort — needs Stripe Event read).
		webhooks = self._failed_webhooks()
		if webhooks is not None:
			result["failed_webhooks"] = webhooks
		return result

	def _recent(self, args: dict[str, Any], limit: int) -> list[dict[str, Any]]:
		filters = {"status": args["status"]} if args.get("status") else {}
		rows = frappe.get_list(
			"Stripe Payment",
			filters=filters,
			fields=_RECENT_FIELDS,
			order_by="modified desc",
			limit_page_length=limit,
		)
		for row in rows:
			row["modified"] = str(row.get("modified") or "")
		return rows

	def _settings(self) -> dict[str, Any] | None:
		if not frappe.has_permission("Stripe Payments Settings", "read"):
			return None
		try:
			doc = frappe.get_cached_doc("Stripe Payments Settings")
		except Exception:
			return None
		return {
			"environment": doc.get("environment"),
			"enabled": bool(doc.get("enabled")),
			"company": doc.get("company"),
			"connection_status": doc.get("status"),
			"status_message": doc.get("status_message"),
			"last_webhook_at": str(doc.get("last_webhook_at") or ""),
			"enable_card": bool(doc.get("enable_card")),
			"enable_ach": bool(doc.get("enable_ach")),
		}

	def _failed_webhooks(self) -> dict[str, Any] | None:
		if not frappe.has_permission("Stripe Event", "read"):
			return None
		errored = frappe.get_list(
			"Stripe Event",
			filters={"process_status": "Error"},
			fields=["name", "event_type", "error", "modified"],
			order_by="modified desc",
			limit_page_length=10,
		)
		for row in errored:
			row["modified"] = str(row.get("modified") or "")
		return {"count": len(errored), "recent": errored}


__all__ = ["StripePaymentStatus"]
