"""Shared low-level helpers for the MDM Integration (Miradore + Action1).

Loading the single Settings doc, reading/writing encrypted secrets (API keys,
client secret, OAuth token, webhook secret), deterministic JSON, and the inbound
webhook secret check. Mirrors ``quickbooks_online/utils.py``.
"""

from __future__ import annotations

import hmac
import json

import frappe

from erpnext_enhancements.utils.error_throttle import log_error_throttled

#: A webhook secret shorter than this is treated as unset. It is the endpoint's
#: only credential, so a guessable one lets anyone archive payloads and trigger
#: provider resyncs. Same floor as the website lead ingress
#: (``crm_enhancements/web_lead.py``); ``secrets.token_urlsafe(32)`` gives 43.
MIN_WEBHOOK_SECRET_LENGTH = 32


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


def get_webhook_secret(settings) -> str | None:
	"""The configured webhook secret, or None when it is unset.

	Not ``get_secret``: its ``get_password`` default raises through ``frappe.throw``,
	which queues "Password not found for MDM Settings ... webhook_secret" into the
	response before the ``except`` swallows the exception. This read runs on a guest
	request, so that message went back to whoever called the endpoint (seen on prod
	2026-09-22, where the secret has never been set).
	"""
	try:
		return settings.get_password("webhook_secret", raise_exception=False) or None
	except Exception:
		return None


def verify_webhook_secret(provided: str | None, settings=None) -> bool:
	"""Constant-time check of an inbound webhook's secret against ``webhook_secret``.

	Fails closed: an unset secret -- or one shorter than
	``MIN_WEBHOOK_SECRET_LENGTH`` -- authorizes nothing. The short case is logged
	at most daily: from outside it looks exactly like a provider sending the wrong
	value, and the fix is on this side.
	"""
	settings = settings or get_settings()
	secret = get_webhook_secret(settings)
	if not secret:
		return False
	if len(secret) < MIN_WEBHOOK_SECRET_LENGTH:
		log_error_throttled(
			f"MDM Settings.webhook_secret is shorter than {MIN_WEBHOOK_SECRET_LENGTH} characters, so "
			"the MDM webhook refuses every request. Generate one with "
			'`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` and set it here and in '
			"the provider's webhook header together.",
			"MDM webhook: secret too short",
			window=86400,
			limit=1,
		)
		return False
	if not provided:
		return False
	# compare_digest rejects non-ASCII str operands (a header can carry latin-1);
	# encode both sides so a junk value is a clean 401, not a 500.
	return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))
