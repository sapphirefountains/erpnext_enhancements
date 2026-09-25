# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Stripe REST client built on ``requests`` — no third-party SDK.

The host is a managed server where PyPI packages can't be installed, so (like the
QuickBooks Online module) everything Stripe-facing is hand-rolled on top of
``requests`` (a Frappe dependency). All calls authenticate with the secret key
from the sandbox-guarded :func:`..utils.get_api_key`; amounts are always in Stripe
minor units (cents). This module also implements Stripe **webhook signature
verification** (the ``Stripe-Signature`` ``t=``/``v1=`` scheme) since there is no
SDK to do it. Higher layers (``checkout``, ``reconcile``, ``api``) call these
helpers and never touch HTTP directly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import frappe
import requests

from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_api_key,
	get_secret,
	get_settings,
)

API_BASE = "https://api.stripe.com/v1"
TIMEOUT = 30
# Reject webhook timestamps older/newer than this (replay protection), matching
# Stripe's default tolerance.
SIGNATURE_TOLERANCE_SECONDS = 300

#: HTTP statuses that are Stripe's definite answer that a request did **not** take
#: effect: invalid (400), unauthenticated (401), declined (402 — a card error, the one
#: that matters), forbidden (403), no such object (404). Everything else is an outcome
#: we cannot know from here: a transport error or timeout (no status at all), a 5xx
#: (Stripe caches it under the idempotency key, so it replays rather than resolves), a
#: 409 (the same idempotency key is still executing) or a 429. A charge whose outcome
#: is unknown may have gone through, and must never be treated as a decline.
DEFINITE_FAILURE_STATUSES = frozenset({400, 401, 402, 403, 404})


class StripeError(frappe.ValidationError):
	"""Raised when a Stripe REST call or signature verification fails.

	``status_code`` is the HTTP status Stripe answered with, or ``None`` when no answer
	arrived (a transport error or timeout). ``stripe_message`` is Stripe's own
	``error.message`` — for a card error it is written for the cardholder ("Your card
	was declined."), so it is the only part of a failure fit to show a customer.
	"""

	def __init__(self, message="", status_code=None, stripe_message=None):
		super().__init__(message)
		self.status_code = status_code
		self.stripe_message = stripe_message

	@property
	def definite(self) -> bool:
		"""Stripe answered, and the request did not take effect (see
		:data:`DEFINITE_FAILURE_STATUSES`)."""
		return self.status_code in DEFINITE_FAILURE_STATUSES


def is_definite_failure(exc) -> bool:
	"""Whether ``exc`` proves a Stripe write did not take effect. Anything that is not a
	:class:`StripeError` carrying a definite status — a bug, a response that would not
	parse, a killed connection — proves nothing, and is an unknown outcome."""
	return isinstance(exc, StripeError) and exc.definite


def is_missing(exc) -> bool:
	"""Whether ``exc`` is Stripe's definite answer that the object does not exist **for the
	configured key** (HTTP 404, ``resource_missing``): created in the other mode (Test vs
	Live) or another account, or never created at all. Not a failure to reach Stripe — asking
	again gets the same answer for ever, so a caller must settle the row rather than retry."""
	return isinstance(exc, StripeError) and exc.status_code == 404


def _encode(data, parent=None, out=None):
	"""Flatten a nested dict/list into Stripe's bracketed form-encoding pairs.

	e.g. ``{"metadata": {"a": 1}, "items": [{"x": 2}]}`` ->
	``[("metadata[a]", 1), ("items[0][x]", 2)]``. Booleans become "true"/"false";
	None values are dropped (Stripe rejects empty params).
	"""
	out = [] if out is None else out
	if isinstance(data, dict):
		for key, value in data.items():
			_encode(value, f"{parent}[{key}]" if parent else str(key), out)
	elif isinstance(data, list | tuple):
		for index, value in enumerate(data):
			_encode(value, f"{parent}[{index}]", out)
	elif data is not None:
		out.append((parent, "true" if data is True else "false" if data is False else data))
	return out


def _request(method, path, *, data=None, params=None, idempotency_key=None, settings=None, timeout=None):
	"""Make an authenticated Stripe REST call; return parsed JSON or raise StripeError.

	``timeout`` (seconds) defaults to :data:`TIMEOUT`. Read-only lookups made while a
	web page renders pass a few seconds instead, so a slow Stripe cannot hold a web
	worker for half a minute per invoice.

	The secret key goes straight into the call rather than into a local, and transport
	errors are raised ``from None``: a traceback logged with frame locals (as a failed
	background job's is) must never carry the ``Authorization`` header.
	"""
	settings = settings or get_settings()
	try:
		response = requests.request(
			method,
			f"{API_BASE}{path}",
			headers=_headers(settings, idempotency_key),
			data=_encode(data) if data else None,
			params=_encode(params) if params else None,
			timeout=timeout or TIMEOUT,
		)
	except requests.RequestException as exc:
		raise StripeError(f"Stripe request failed: {error_snippet(str(exc), 200)}") from None
	if response.status_code >= 400:
		raise StripeError(
			f"Stripe API error ({response.status_code}): {error_snippet(response.text)}",
			status_code=response.status_code,
			stripe_message=_stripe_error_message(response),
		)
	try:
		return response.json()
	except ValueError:
		# A 2xx we cannot read took effect all the same: an unknown outcome, not a failure.
		raise StripeError("Stripe returned a response that could not be read.") from None


def _headers(settings, idempotency_key=None) -> dict:
	headers = {"Authorization": f"Bearer {get_api_key(settings)}"}
	if idempotency_key:
		headers["Idempotency-Key"] = idempotency_key
	return headers


def _stripe_error_message(response) -> str | None:
	"""Stripe's ``error.message`` from an error response, when it has one."""
	try:
		return ((response.json() or {}).get("error") or {}).get("message") or None
	except Exception:
		return None


def ensure_stripe_customer(customer: str, settings=None) -> str:
	"""Return the Stripe Customer id for an ERPNext Customer, creating it if needed.

	Caches the id on ``Customer.custom_stripe_customer_id`` so repeat payments reuse
	the same Stripe Customer (required for saved payment methods later). Best-effort
	prefills the customer's email from their primary contact.
	"""
	settings = settings or get_settings()
	existing = frappe.db.get_value("Customer", customer, "custom_stripe_customer_id")
	if existing:
		return existing

	customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
	data = {"name": customer_name, "metadata": {"erpnext_customer": customer}}
	email = _customer_email(customer)
	if email:
		data["email"] = email

	obj = _request("POST", "/customers", data=data, idempotency_key=f"ee-cust-{customer}", settings=settings)
	# Store without tripping Customer hooks/permissions — this is a back-reference.
	frappe.db.set_value("Customer", customer, "custom_stripe_customer_id", obj["id"])
	return obj["id"]


def create_checkout_session(params: dict, idempotency_key: str | None = None):
	# Called by checkout._start_checkout while it holds the Sales Invoice row lock (so a
	# cancel waits for the Link Sent commit); the client's full timeout bounds that wait.
	"""Create a Stripe Checkout Session. ``params`` is a nested dict (form-encoded)."""
	return _request("POST", "/checkout/sessions", data=params, idempotency_key=idempotency_key)


def retrieve_checkout_session(session_id: str, expand: list[str] | None = None, timeout=None):
	"""Retrieve a Checkout Session, optionally expanding nested objects."""
	return _request(
		"GET",
		f"/checkout/sessions/{session_id}",
		params={"expand": expand} if expand else None,
		timeout=timeout,
	)


def expire_checkout_session(session_id: str, timeout=None):
	"""Expire an open Checkout Session (``POST /v1/checkout/sessions/:id/expire``).

	After this the payer's link shows "expired" and can no longer take a payment.
	Stripe refuses to expire a session that is already complete (paid, or an ACH debit
	submitted), which is exactly the case where a second payment must not start. No
	idempotency key, like a PaymentIntent cancel: Stripe never expires a session twice,
	and a key would replay a transient error for a day. Callers re-read the session
	when this raises.
	"""
	return _request("POST", f"/checkout/sessions/{session_id}/expire", timeout=timeout)


def retrieve_payment_intent(payment_intent_id: str, timeout=None):
	"""Retrieve a PaymentIntent with its latest charge expanded."""
	return _request(
		"GET",
		f"/payment_intents/{payment_intent_id}",
		params={"expand": ["latest_charge"]},
		timeout=timeout,
	)


def list_payment_intents(
	customer: str, *, created_gte: int | None = None, starting_after: str | None = None, timeout=None
):
	"""One page (up to 100, newest first) of a Stripe Customer's PaymentIntents.

	How a charge whose outcome was unknown is found again: the list endpoints are
	strongly consistent, unlike ``/payment_intents/search`` (which can lag a minute or
	more), so a PaymentIntent that exists is always on it — and one that is not on it,
	a quarter of an hour after the request, was never created.
	"""
	params = {"customer": customer, "limit": 100}
	if created_gte:
		params["created"] = {"gte": int(created_gte)}
	if starting_after:
		params["starting_after"] = starting_after
	return _request("GET", "/payment_intents", params=params, timeout=timeout)


def create_refund(payment_intent: str, amount_minor: int | None = None, reason: str | None = None):
	"""Refund a PaymentIntent (full unless ``amount_minor`` given)."""
	data = {"payment_intent": payment_intent}
	if amount_minor:
		data["amount"] = amount_minor
	if reason:
		data["reason"] = reason
	return _request(
		"POST", "/refunds", data=data, idempotency_key=f"ee-refund-{payment_intent}-{amount_minor or 'full'}"
	)


def retrieve_account(settings=None):
	"""Retrieve the connected Stripe account (used by Test Connection)."""
	return _request("GET", "/account", settings=settings)


def list_balance_transactions_for_payout(payout_id: str, settings=None) -> list[dict]:
	"""Return every Balance Transaction that makes up a payout (auto-paginated).

	Each entry carries ``amount``/``fee``/``net`` in minor units and a
	``reporting_category`` (charge, refund, dispute, fee, …). Stripe returns at
	most 100 per page; we follow ``has_more`` with ``starting_after`` until the
	list is exhausted. The page loop is bounded so a runaway response can't spin
	forever — but if that bound is hit with more pages still pending we raise
	rather than return a silently-partial list (a truncated list would under-count
	fees/gross and mis-book the Journal Entry).
	"""
	settings = settings or get_settings()
	out: list[dict] = []
	starting_after = None
	for _ in range(200):  # 200 pages * 100 = 20k txns/payout is far beyond real
		params = {"payout": payout_id, "limit": 100}
		if starting_after:
			params["starting_after"] = starting_after
		page = _request("GET", "/balance_transactions", params=params, settings=settings)
		data = page.get("data") or []
		out.extend(data)
		if not page.get("has_more") or not data:
			return out
		starting_after = data[-1].get("id")
	raise StripeError(
		f"Payout {payout_id} has more balance transactions than the page cap; "
		"refusing to post a Journal Entry from a partial list."
	)


def list_recent_payouts(limit: int = 20, settings=None) -> list[dict]:
	"""List recent Payout objects (newest first) for the missed-webhook backstop."""
	page = _request("GET", "/payouts", params={"limit": limit}, settings=settings)
	return page.get("data") or []


def create_payment_intent(params: dict, idempotency_key: str | None = None):
	"""Create (and usually confirm) a PaymentIntent — the off-session charge and the card
	page's charge. Callers always send an idempotency key, and go through
	``card_element.create_intent_settled``, which tells a decline from an unknown outcome."""
	return _request("POST", "/payment_intents", data=params, idempotency_key=idempotency_key)


def retrieve_setup_intent(setup_intent_id: str):
	"""Retrieve a SetupIntent with its payment method expanded (after setup-mode Checkout)."""
	return _request("GET", f"/setup_intents/{setup_intent_id}", params={"expand": ["payment_method"]})


def retrieve_payment_method(payment_method_id: str):
	"""Retrieve a PaymentMethod (for its display label, and its card funding type)."""
	return _request("GET", f"/payment_methods/{payment_method_id}")


def retrieve_confirmation_token(confirmation_token_id: str):
	"""Retrieve a ConfirmationToken created by Stripe.js on the payment page.

	Its ``payment_method_preview`` carries the method ``type`` and, for cards,
	``card.funding`` — **before** any amount is committed. That is what makes a
	funding-aware surcharge possible at all: we read the real card, price the fee,
	show the payer the true total, and only then confirm. Hosted Checkout cannot do
	this because its line items are fixed when the Session is created.
	"""
	return _request("GET", f"/confirmation_tokens/{confirmation_token_id}")


def detach_payment_method(payment_method_id: str):
	"""Detach a saved PaymentMethod from its customer (used when autopay is revoked)."""
	return _request("POST", f"/payment_methods/{payment_method_id}/detach")


def verify_and_parse_event(payload: bytes | str, sig_header: str | None, settings=None) -> dict:
	"""Verify the ``Stripe-Signature`` header and return the parsed event dict.

	Implements Stripe's signature scheme without the SDK: the header carries a
	timestamp ``t`` and one or more ``v1`` HMAC-SHA256 signatures over
	``"{t}.{payload}"``, keyed by the endpoint's signing secret. Raises
	:class:`StripeError` on any failure (the webhook route turns that into HTTP
	400). Uses constant-time comparison and rejects timestamps outside the tolerance
	window to resist replay.
	"""
	secret = get_secret(settings or get_settings(), "webhook_signing_secret")
	if not secret:
		raise StripeError("Stripe webhook signing secret is not configured.")
	if not sig_header:
		raise StripeError("Missing Stripe-Signature header.")

	payload_bytes = payload if isinstance(payload, bytes) else (payload or "").encode("utf-8")

	parts: dict[str, list[str]] = {}
	for item in sig_header.split(","):
		key, _, value = item.partition("=")
		parts.setdefault(key.strip(), []).append(value.strip())
	timestamp = (parts.get("t") or [None])[0]
	signatures = parts.get("v1") or []
	if not timestamp or not signatures:
		raise StripeError("Malformed Stripe-Signature header.")

	signed_payload = timestamp.encode("utf-8") + b"." + payload_bytes
	expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
	if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
		raise StripeError("Stripe signature verification failed.")

	try:
		if abs(time.time() - int(timestamp)) > SIGNATURE_TOLERANCE_SECONDS:
			raise StripeError("Stripe webhook timestamp is outside the tolerance window.")
	except (TypeError, ValueError):
		raise StripeError("Invalid Stripe webhook timestamp.")

	return json.loads(payload_bytes.decode("utf-8") or "{}")


def _customer_email(customer: str) -> str | None:
	"""Best-effort email for an ERPNext Customer, via its primary contact."""
	try:
		primary_contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
		if primary_contact:
			email = frappe.db.get_value("Contact", primary_contact, "email_id")
			if email:
				return email
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: customer email lookup failed")
	return None
