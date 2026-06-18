# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inbound Stripe webhook handling.

The single public entry point ``handle_webhook`` is invoked by the whitelisted
``api.stripe_webhook`` (allow_guest) route. It verifies the Stripe signature,
records the event as a ``Stripe Event`` (whose name is the event id, so a
redelivery cannot be ingested twice), then enqueues ``reconcile.process_event`` so
the HTTP response returns fast. Mirrors the QuickBooks module's webhook flow.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.stripe_payments.core.client import verify_and_parse_event
from erpnext_enhancements.stripe_payments.core.utils import error_snippet, get_settings


def handle_webhook():
	"""Verify, record and dispatch an inbound Stripe webhook notification.

	Flow:
	  1. Verify the ``Stripe-Signature`` header against the signing secret; HTTP 400
	     on failure (no payload is processed).
	  2. Upsert a ``Stripe Event`` keyed by the event id — if already processed, ack
	     200 without re-enqueuing (idempotent).
	  3. Enqueue ``reconcile.process_event`` on the "short" queue.
	  4. Stamp ``last_webhook_at``/status on Settings.

	Returns ``{"status": "success"}`` on accept.
	"""
	settings = get_settings()
	body = frappe.request.get_data() or b""
	sig_header = frappe.get_request_header("Stripe-Signature")

	# Signature check first: never parse or act on an unverified payload.
	try:
		event = verify_and_parse_event(body, sig_header, settings)
	except Exception as exc:
		frappe.local.response.http_status_code = 400
		return {"status": "error", "message": f"Invalid Stripe webhook: {error_snippet(str(exc), 200)}"}

	event_id = event["id"]
	event_type = event.get("type")

	existing = frappe.db.get_value("Stripe Event", event_id, "processed")
	if existing is not None:
		# Already seen. Re-enqueue only if it never finished processing.
		if not existing:
			_enqueue(event_id)
		return {"status": "success", "duplicate": True}

	try:
		frappe.get_doc(
			{
				"doctype": "Stripe Event",
				"event_id": event_id,
				"event_type": event_type,
				"api_version": event.get("api_version"),
				"process_status": "Pending",
				"payload": body.decode("utf-8"),
			}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# A near-simultaneous redelivery won the race to insert this event id.
		# The unique constraint kept us from double-recording; just ensure it's
		# queued and ack.
		frappe.db.rollback()
		_enqueue(event_id)
		return {"status": "success", "duplicate": True}

	settings.db_set("last_webhook_at", now_datetime(), update_modified=False)
	settings.db_set("status", "Connected", update_modified=False)
	frappe.db.commit()

	_enqueue(event_id)
	return {"status": "success"}


def _enqueue(event_id: str):
	"""Enqueue background processing of a stored event on the short queue."""
	frappe.enqueue(
		"erpnext_enhancements.stripe_payments.core.reconcile.process_event",
		queue="short",
		event_name=event_id,
	)
