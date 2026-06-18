# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared low-level helpers for the Stripe Payments integration.

Cross-cutting utilities used across the checkout -> webhook -> reconcile pipeline:
loading the single Settings doc, reading encrypted secrets, converting between
ERPNext currency amounts and Stripe minor units (cents), and the lazily-imported,
sandbox-guarded ``stripe`` client handle. Mirrors the QuickBooks Online module's
``core/utils.py`` conventions.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

SETTINGS_DOCTYPE = "Stripe Payments Settings"


def get_settings():
	"""Return the singleton ``Stripe Payments Settings`` document."""
	return frappe.get_single(SETTINGS_DOCTYPE)


def is_enabled(settings=None) -> bool:
	"""True when the integration master switch is on."""
	settings = settings or get_settings()
	return bool(cint(settings.enabled))


def get_secret(settings, fieldname: str) -> str | None:
	"""Read an encrypted Password field from Settings, decrypting it.

	Used for ``secret_key`` / ``webhook_signing_secret``. Falls back to the raw
	field value when decryption is unavailable, returning None when absent.
	"""
	try:
		return settings.get_password(fieldname)
	except Exception:
		return settings.get(fieldname)


def to_minor_units(amount, currency: str = "USD") -> int:
	"""Convert an ERPNext currency amount to Stripe minor units (e.g. cents).

	Sapphire transacts in USD (2 decimals), so this multiplies by 100 and rounds.
	Zero-decimal currencies (JPY) would need different handling; USD-only for now.
	"""
	return int(round(flt(amount) * 100))


def from_minor_units(value, currency: str = "USD") -> float:
	"""Convert Stripe minor units back to an ERPNext currency amount."""
	return flt(value) / 100.0


def get_api_key(settings=None) -> str:
	"""Return the Stripe secret key after enforcing the sandbox guard.

	Reads the encrypted secret and refuses a key whose ``sk_live_``/``sk_test_``
	prefix contradicts the selected ``environment`` — so the integration cannot
	accidentally transact against the live account while the build is sandbox-only.
	There is no third-party SDK: callers reach Stripe's REST API with ``requests``
	(see ``core/client.py``), mirroring the QuickBooks module's hand-rolled client.
	"""
	settings = settings or get_settings()
	key = get_secret(settings, "secret_key")
	if not key:
		frappe.throw("Stripe secret key is not configured in Stripe Payments Settings.")

	environment = settings.environment or "Test"
	if environment == "Test" and key.startswith("sk_live_"):
		frappe.throw("Refusing to use a live Stripe key (sk_live_…) while Environment is Test.")
	if environment == "Live" and key.startswith("sk_test_"):
		frappe.throw("Refusing to use a test Stripe key (sk_test_…) while Environment is Live.")
	return key


def update_settings_status(status: str, message: str | None = None, **fields):
	"""Persist ``status``/``status_message`` (and any extra fields) on Settings.

	Saves with ``ignore_permissions`` and commits. Returns the saved Settings doc.
	"""
	settings = get_settings()
	settings.status = status
	if message is not None:
		settings.status_message = message[:1000]
	for fieldname, value in fields.items():
		setattr(settings, fieldname, value)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return settings


def error_snippet(text, limit: int = 500) -> str:
	"""Bound an error body so large/echoed payloads never spill into logs."""
	text = text or ""
	text = str(text)
	return text if len(text) <= limit else text[:limit] + "… (truncated)"
