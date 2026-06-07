from __future__ import annotations

import hashlib
import hmac
import json
import base64
from datetime import datetime, timezone

import frappe
from frappe.utils import get_datetime, now_datetime


def utcnow():
	return datetime.now(timezone.utc).replace(tzinfo=None)


def json_dumps(data) -> str:
	return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def json_loads(value, default=None):
	if not value:
		return default
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except Exception:
		return default


def get_settings():
	return frappe.get_single("QuickBooks Online Settings")


def get_secret(settings, fieldname: str) -> str | None:
	try:
		return settings.get_password(fieldname)
	except Exception:
		return settings.get(fieldname)


def set_secret(settings, fieldname: str, value: str | None):
	if not value:
		return
	if hasattr(settings, "set_password"):
		settings.set_password(fieldname, value)
	else:
		settings.set(fieldname, value)


def parse_qbo_datetime(value):
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
	if not settings.token_expires_at:
		return True
	return get_datetime(settings.token_expires_at) <= frappe.utils.add_to_date(
		now_datetime(), minutes=buffer_minutes, as_datetime=True
	)


def verify_intuit_signature(body: bytes, signature: str | None, verifier_token: str | None) -> bool:
	if not signature or not verifier_token:
		return False
	digest = hmac.new(verifier_token.encode("utf-8"), body, hashlib.sha256).digest()
	expected = base64.b64encode(digest).decode("utf-8")
	return hmac.compare_digest(signature, expected)


def update_settings_status(status: str, message: str | None = None, **fields):
	settings = get_settings()
	settings.status = status
	if message is not None:
		settings.status_message = message[:1000]
	for fieldname, value in fields.items():
		setattr(settings, fieldname, value)
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return settings
