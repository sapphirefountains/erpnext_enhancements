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


class StripeError(frappe.ValidationError):
	"""Raised when a Stripe REST call or signature verification fails."""


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


def _request(method, path, *, data=None, params=None, idempotency_key=None, settings=None):
	"""Make an authenticated Stripe REST call; return parsed JSON or raise StripeError."""
	settings = settings or get_settings()
	headers = {"Authorization": f"Bearer {get_api_key(settings)}"}
	if idempotency_key:
		headers["Idempotency-Key"] = idempotency_key
	try:
		response = requests.request(
			method,
			f"{API_BASE}{path}",
			headers=headers,
			data=_encode(data) if data else None,
			params=_encode(params) if params else None,
			timeout=TIMEOUT,
		)
	except requests.RequestException as exc:
		raise StripeError(f"Stripe request failed: {error_snippet(str(exc), 200)}")
	if response.status_code >= 400:
		raise StripeError(f"Stripe API error ({response.status_code}): {error_snippet(response.text)}")
	return response.json()


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
	"""Create a Stripe Checkout Session. ``params`` is a nested dict (form-encoded)."""
	return _request("POST", "/checkout/sessions", data=params, idempotency_key=idempotency_key)


def retrieve_checkout_session(session_id: str, expand: list[str] | None = None):
	"""Retrieve a Checkout Session, optionally expanding nested objects."""
	return _request("GET", f"/checkout/sessions/{session_id}", params={"expand": expand} if expand else None)


def retrieve_payment_intent(payment_intent_id: str):
	"""Retrieve a PaymentIntent with its latest charge expanded."""
	return _request("GET", f"/payment_intents/{payment_intent_id}", params={"expand": ["latest_charge"]})


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


def create_payment_intent(params: dict, idempotency_key: str | None = None):
	"""Create (and usually confirm) a PaymentIntent — used for off-session charges."""
	return _request("POST", "/payment_intents", data=params, idempotency_key=idempotency_key)


def retrieve_setup_intent(setup_intent_id: str):
	"""Retrieve a SetupIntent with its payment method expanded (after setup-mode Checkout)."""
	return _request("GET", f"/setup_intents/{setup_intent_id}", params={"expand": ["payment_method"]})


def retrieve_payment_method(payment_method_id: str):
	"""Retrieve a PaymentMethod (for its display label)."""
	return _request("GET", f"/payment_methods/{payment_method_id}")


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
