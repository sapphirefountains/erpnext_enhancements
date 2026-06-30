# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared low-level helpers for the Plaid bank-balance integration.

Loads the single Settings doc, reads/writes encrypted secrets, and persists
status. Mirrors the Stripe / QuickBooks Online ``core/utils.py`` conventions so
the Plaid module reads the same as its siblings. Secrets (``plaid_secret`` /
``plaid_access_token``) are never logged.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from erpnext_enhancements.plaid_banking.core.constants import SETTINGS_DOCTYPE


def get_settings():
	"""Return the singleton ``Plaid Settings`` document."""
	return frappe.get_single(SETTINGS_DOCTYPE)


def is_enabled(settings=None) -> bool:
	"""True when the Plaid master switch is on."""
	settings = settings or get_settings()
	return bool(cint(settings.plaid_enabled))


def get_secret(settings, fieldname: str) -> str | None:
	"""Read an encrypted Password field, decrypting it.

	Used for ``plaid_secret`` / ``plaid_access_token``. Falls back to the raw
	field value when decryption is unavailable, returning None when absent.
	"""
	try:
		return settings.get_password(fieldname)
	except Exception:
		return settings.get(fieldname)


def set_secret(settings, fieldname: str, value: str | None) -> None:
	"""Store (or clear) an encrypted Password field via Frappe's password store."""
	if value:
		settings.set(fieldname, value)
	else:
		try:
			settings.set(fieldname, "")
		except Exception:
			pass


def get_credentials(settings=None) -> tuple[str, str]:
	"""Return ``(client_id, secret)``; throw if either is missing."""
	settings = settings or get_settings()
	client_id = settings.plaid_client_id
	secret = get_secret(settings, "plaid_secret")
	if not client_id or not secret:
		frappe.throw("Plaid client_id / secret are not configured in Plaid Settings.")
	return client_id, secret


def update_settings_status(status: str, message: str | None = None, **fields):
	"""Persist ``plaid_status`` / ``plaid_status_message`` (and any extra fields).

	Saves with ``ignore_permissions`` and commits. Returns the saved Settings doc.
	"""
	settings = get_settings()
	settings.plaid_status = status
	if message is not None:
		settings.plaid_status_message = message[:1000]
	for fieldname, value in fields.items():
		setattr(settings, fieldname, value)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return settings


def clear_access_token(settings, message: str) -> None:
	"""Remove the stored access token + item identifiers and mark Not Connected.

	Keeps client_id / secret so a reconnect is one click. Used on Disconnect.
	Frappe stores Password fields in the ``__Auth`` table; setting the field to
	empty and saving removes the encrypted value.
	"""
	settings.set("plaid_access_token", "")
	settings.plaid_item_id = ""
	settings.plaid_institution_name = ""
	settings.plaid_status = "Not Connected"
	settings.plaid_status_message = message[:1000] if message else None
	settings.plaid_auth_blocked = 0
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def error_snippet(text, limit: int = 500) -> str:
	"""Bound an error body so large/echoed payloads never spill into logs."""
	text = str(text or "")
	return text if len(text) <= limit else text[:limit] + "… (truncated)"
