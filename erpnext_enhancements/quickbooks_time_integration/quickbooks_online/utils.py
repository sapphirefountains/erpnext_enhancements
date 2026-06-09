"""Shared low-level helpers for the QuickBooks Online integration.

Cross-cutting utilities used throughout the OAuth -> client -> mapping -> sync
-> log pipeline: loading the single Settings doc, reading/writing encrypted
secrets (tokens, client secret, webhook verifier), deterministic JSON
serialization for stored payloads/mappings, QBO datetime normalization, OAuth
token-expiry checks and Intuit webhook signature verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import base64
from datetime import datetime, timezone

import frappe
from frappe.utils import get_datetime, now_datetime


def utcnow():
	"""Return the current UTC time as a naive datetime (tzinfo stripped).

	Frappe stores datetimes without timezone, so we normalize to naive-UTC to
	stay consistent with values persisted on Settings/Mapping docs.
	"""
	return datetime.now(timezone.utc).replace(tzinfo=None)


def json_dumps(data) -> str:
	"""Serialize ``data`` to a compact, deterministic JSON string.

	Keys are sorted and separators are minimized so that stored raw payloads and
	mapping ``owned_fields`` are byte-stable (useful for comparison/diffing).
	``default=str`` lets non-JSON types (datetimes, Decimals) fall back to their
	string form instead of raising.
	"""
	return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(value, default=None):
	"""Best-effort JSON decode that never raises.

	Accepts an already-decoded dict/list (returned as-is), a JSON string, or an
	empty value. Returns ``default`` on empty input or any parse error -- callers
	rely on this to read possibly-malformed stored payloads safely.
	"""
	if not value:
		return default
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except Exception:
		return default


def get_settings():
	"""Return the singleton ``QuickBooks Online Settings`` document.

	This is the integration's only credential/state store (client id/secret,
	OAuth tokens, realm id, sync cursors, status).
	"""
	return frappe.get_single("QuickBooks Online Settings")


def get_secret(settings, fieldname: str) -> str | None:
	"""Read an encrypted Password field from Settings, decrypting it.

	Used for ``access_token``/``refresh_token``/``client_secret``/
	``webhook_verifier_token``. Falls back to the raw field value if decryption
	is unavailable (e.g. value not yet stored as a password), returning None when
	absent.
	"""
	try:
		return settings.get_password(fieldname)
	except Exception:
		return settings.get(fieldname)


def set_secret(settings, fieldname: str, value: str | None):
	"""Write an encrypted Password field on Settings (in memory only).

	No-ops on falsy values so a token refresh that omits a new refresh_token does
	not clobber the existing one. Does NOT save -- the caller (typically
	``client._store_tokens``) is responsible for persisting and committing.
	"""
	if not value:
		return
	if hasattr(settings, "set_password"):
		settings.set_password(fieldname, value)
	else:
		settings.set(fieldname, value)


def parse_qbo_datetime(value):
	"""Parse a QBO timestamp into a naive-UTC datetime, or None on failure.

	QBO returns ISO-8601 strings with timezone offsets (e.g. MetaData
	LastUpdatedTime). Any tz-aware result is converted to UTC and made naive to
	match how Frappe stores datetimes; unparseable input yields None.
	"""
	if not value:
		return None
	try:
		parsed = get_datetime(value)
	except Exception:
		return None
	if isinstance(parsed, str):
		try:
			parsed = datetime.fromisoformat(parsed.replace("Z", "+00:00"))
		except ValueError:
			return None
	if getattr(parsed, "tzinfo", None):
		parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
	return parsed


def is_token_expiring(settings, buffer_minutes=5) -> bool:
	"""True if the OAuth access token is expired or within ``buffer_minutes``.

	A missing ``token_expires_at`` is treated as expiring so a refresh is forced.
	The scheduler-driven ``tasks.refresh_token_if_needed`` performs the actual
	refresh.
	"""
	if not settings.token_expires_at:
		return True
	return get_datetime(settings.token_expires_at) <= frappe.utils.add_to_date(
		now_datetime(), minutes=buffer_minutes, as_datetime=True
	)


def verify_intuit_signature(body: bytes, signature: str | None, verifier_token: str | None) -> bool:
	"""Verify an Intuit webhook payload's HMAC-SHA256 signature.

	Computes ``base64(HMAC_SHA256(verifier_token, raw_body))`` and compares it to
	the ``intuit-signature`` header using a constant-time comparison to resist
	timing attacks. Returns False (reject) if either the signature header or the
	stored verifier token is missing. Called by ``webhooks.handle_webhook``
	before any payload is processed.
	"""
	if not signature or not verifier_token:
		return False
	digest = hmac.new(verifier_token.encode("utf-8"), body, hashlib.sha256).digest()
	expected = base64.b64encode(digest).decode("utf-8")
	return hmac.compare_digest(signature, expected)


def update_settings_status(status: str, message: str | None = None, **fields):
	"""Persist a status (and optional message/extra fields) on Settings.

	Side effects: loads Settings, writes ``status``/``status_message`` (truncated
	to 1000 chars) plus any keyword fields, saves with ``ignore_permissions`` and
	commits. Returns the saved Settings doc.
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
