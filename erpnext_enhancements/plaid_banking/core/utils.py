# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared low-level helpers for the Plaid bank-balance widget.

Two settings documents are involved and the split is the whole design:

* ``Plaid Banking Settings`` (ours) -- the widget switch, throttle, status and the
  auth-pause flag. Nothing secret.
* ``Plaid Settings`` (ERPNext's, module ERPNext Integrations) -- client id, secret
  and environment. Read-only from here; the operator edits it on the native form.

Access tokens are per institution, on ``Bank.plaid_access_token``, written by the
native Plaid Link flow. :func:`linked_banks` is the only reader, and the token it
returns goes straight into a request body -- never into a log, a status message
or an API response.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

from erpnext_enhancements.plaid_banking.core.constants import (
	DEFAULT_ENVIRONMENT,
	ENVIRONMENT_BASE_URLS,
	NATIVE_SETTINGS_DOCTYPE,
	SETTINGS_DOCTYPE,
)


def get_settings():
	"""Return the singleton ``Plaid Banking Settings`` document (ours)."""
	return frappe.get_single(SETTINGS_DOCTYPE)


def get_native_settings():
	"""Return ERPNext's singleton ``Plaid Settings`` document (keys + environment)."""
	return frappe.get_single(NATIVE_SETTINGS_DOCTYPE)


def is_enabled(settings=None) -> bool:
	"""True when the widget switch is on."""
	settings = settings or get_settings()
	return bool(cint(settings.plaid_enabled))


def get_credentials(native=None) -> tuple[str, str]:
	"""Return ``(client_id, secret)`` from the native Plaid Settings; throw if missing.

	The secret is a Password field, so it comes back through ``get_password`` (the
	``__Auth`` table), never from the Singles row. The message names the native
	form because that is where the fix is -- our own settings page has no key fields.
	"""
	native = native or get_native_settings()
	client_id = native.get("plaid_client_id")
	secret = None
	try:
		secret = native.get_password("plaid_secret", raise_exception=False)
	except Exception:
		secret = None
	if not client_id or not secret:
		frappe.throw(
			"Plaid client id / secret are not configured. Enter them in the native "
			"Plaid Settings (ERPNext Integrations), not in Plaid Banking Settings."
		)
	return client_id, secret


def get_environment(native=None) -> str:
	"""Return the native ``plaid_env`` value, normalised to a key of ENVIRONMENT_BASE_URLS."""
	native = native or get_native_settings()
	env = (native.get("plaid_env") or DEFAULT_ENVIRONMENT).strip().lower()
	return env if env in ENVIRONMENT_BASE_URLS else DEFAULT_ENVIRONMENT


def linked_banks() -> list[dict]:
	"""Return ``[{bank, access_token}, ...]`` for every Bank the native Link flow has linked.

	Filters on a non-empty ``plaid_access_token`` in SQL and again in Python, because
	a nullable column and ``!= ''`` disagree about NULL. The token is handed to the
	caller for the request body only.
	"""
	rows = frappe.get_all(
		"Bank",
		filters={"plaid_access_token": ["!=", ""]},
		fields=["name", "plaid_access_token"],
		order_by="name asc",
	)
	return [
		{"bank": row.get("name"), "access_token": row.get("plaid_access_token")}
		for row in rows
		if row.get("plaid_access_token")
	]


def linked_bank_names() -> list[str]:
	"""The linked banks by name only -- safe to return from an API."""
	return [row["bank"] for row in linked_banks()]


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


def error_snippet(text, limit: int = 500) -> str:
	"""Bound an error body so large/echoed payloads never spill into logs."""
	text = str(text or "")
	return text if len(text) <= limit else text[:limit] + "… (truncated)"
