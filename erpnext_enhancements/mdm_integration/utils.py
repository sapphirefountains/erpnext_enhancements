"""Shared low-level helpers for the MDM Integration (Miradore + Action1).

Loading the single Settings doc, reading/writing encrypted secrets (API keys,
client secret, OAuth token, webhook secret), deterministic JSON, and the inbound
webhook bearer check. Mirrors ``quickbooks_online/utils.py``.
"""

from __future__ import annotations

import hmac
import json

import frappe


def get_settings():
	"""Return the singleton ``MDM Settings`` document (credentials + state)."""
	return frappe.get_single("MDM Settings")


def get_secret(settings, fieldname: str) -> str | None:
	"""Read an encrypted Password field from Settings, decrypting it.

	Falls back to the raw value if decryption is unavailable; None when absent.
	(Identical contract to the QuickBooks integration's helper.)
	"""
	try:
		return settings.get_password(fieldname)
	except Exception:
		return settings.get(fieldname)


def set_secret(settings, fieldname: str, value: str | None):
	"""Write an encrypted Password field on Settings (in memory only).

	No-ops on falsy values so a token refresh that omits a value never clobbers
	the stored one. The caller saves/commits.
	"""
	if not value:
		return
	if hasattr(settings, "set_password"):
		settings.set_password(fieldname, value)
	else:
		settings.set(fieldname, value)


def json_dumps(data) -> str:
	"""Compact, deterministic JSON (sorted keys) for stored raw payloads."""
	return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def provider_enabled(settings, provider_key: str) -> bool:
	"""True if the given provider's sync is enabled on Settings."""
	if provider_key == "Miradore":
		return bool(settings.get("miradore_enabled"))
	if provider_key == "Action1":
		return bool(settings.get("action1_enabled"))
	return False


def enabled_providers(settings=None):
	"""List of provider keys whose sync is enabled (['Miradore', 'Action1'])."""
	settings = settings or get_settings()
	return [p for p in ("Miradore", "Action1") if provider_enabled(settings, p)]


def verify_webhook_bearer(provided_token: str | None, settings=None) -> bool:
	"""Constant-time check of an inbound webhook's Bearer token against the secret."""
	settings = settings or get_settings()
	secret = get_secret(settings, "webhook_secret")
	if not secret or not provided_token:
		return False
	return hmac.compare_digest(provided_token, secret)
