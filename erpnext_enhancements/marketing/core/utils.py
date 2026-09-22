# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Settings, credentials, the sync window, and redaction.

The window and redaction helpers are pure (standard library only) so the rules that decide
*what gets pulled* and *what may be written down* are tested without a bench.
"""

import datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from erpnext_enhancements.marketing.core import constants as C

# ---------------------------------------------------------------- settings


def get_settings():
	import frappe

	return frappe.get_cached_doc(C.SETTINGS_DOCTYPE)


def get_credentials():
	import frappe

	return frappe.get_doc(C.CREDENTIALS_DOCTYPE)


def platform_enabled(platform, settings=None):
	"""Master switch AND the platform's own flag. Both ship off."""
	from frappe.utils import cint

	settings = settings or get_settings()
	return bool(cint(settings.get("enabled"))) and bool(cint(settings.get(C.ENABLED_FLAG[platform])))


def field(platform, name):
	"""Marketing Credentials fieldname for ``name`` on ``platform``."""
	return f"{C.CREDENTIAL_PREFIX[platform]}_{name}"


def get_secret(creds, fieldname):
	"""Decrypt a Password field; None when absent. Never logs, never raises."""
	try:
		return creds.get_password(fieldname, raise_exception=False) or None
	except Exception:
		return None


def set_secret(creds, fieldname, value):
	"""Stage an encrypted value on ``creds``. No-op on falsy, so a refresh that returns no
	new refresh token keeps the old one. The caller saves."""
	if value:
		creds.set(fieldname, value)


def is_connected(platform, creds=None):
	creds = creds or get_credentials()
	return (creds.get(field(platform, "connection_status")) or "") == "Connected"


# ---------------------------------------------------------------- the window


def sync_window(cursor, today, restate_days, initial_backfill_days):
	"""``(date_from, date_to)`` for one account, inclusive.

	* ``date_to`` is **yesterday**: today's numbers are still moving.
	* First run (no cursor): ``initial_backfill_days`` back from yesterday.
	* Every later run: restate the last ``restate_days`` up to the cursor, plus whatever
	  has closed since. Platforms revise spend and conversions for days that have already
	  closed, and the (campaign, date) upsert makes pulling them again safe.

	Returns None when there is nothing to pull (a cursor already at yesterday with a
	zero restate window).
	"""
	restate_days = max(int(restate_days or 0), 0)
	initial_backfill_days = max(int(initial_backfill_days or 0), 1)
	date_to = today - datetime.timedelta(days=1)
	if cursor is None:
		date_from = date_to - datetime.timedelta(days=initial_backfill_days - 1)
	else:
		date_from = min(
			cursor - datetime.timedelta(days=restate_days - 1), cursor + datetime.timedelta(days=1)
		)
		if restate_days == 0:
			date_from = cursor + datetime.timedelta(days=1)
	if date_from > date_to:
		return None
	return date_from, date_to


def click_days(date_from, date_to, today, lookback=C.CLICK_VIEW_LOOKBACK_DAYS):
	"""The days in the window Google still holds click_view for (it keeps 90)."""
	oldest = today - datetime.timedelta(days=lookback - 1)
	day = max(date_from, oldest)
	days = []
	while day <= date_to:
		days.append(day)
		day += datetime.timedelta(days=1)
	return days


# ---------------------------------------------------------------- redaction

_BEARER = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-~+/=]{8,}")
_KEY_VALUE = re.compile(
	r"(?i)\b(" + "|".join(sorted(C.SECRET_QUERY_KEYS)) + r")(\"?\s*[:=]\s*\"?)[^\"&\s,}]+"
)


def redact_url(url):
	"""``url`` with every secret-bearing query parameter replaced by ``REDACTED``."""
	if not url:
		return url
	parts = urlsplit(url)
	query = [
		(k, "REDACTED" if k.lower() in C.SECRET_QUERY_KEYS else v)
		for k, v in parse_qsl(parts.query, keep_blank_values=True)
	]
	return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def redact_text(text, limit=1000):
	"""Strip bearer tokens and ``secret_key=value`` pairs from free text, then cap it.

	Applied to every provider error message before it can reach an exception, the Sync
	Log or the Error Log. Provider errors sometimes echo the request back.
	"""
	if not text:
		return ""
	text = str(text)
	text = _BEARER.sub(r"\1 REDACTED", text)
	text = _KEY_VALUE.sub(r"\1\2REDACTED", text)
	return text[:limit]
