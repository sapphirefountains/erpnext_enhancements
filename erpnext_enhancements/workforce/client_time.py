# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Timestamps from the kiosk's browser, put on the site's clock before they are stored.

Every Datetime column in Frappe holds a **naive** value in the site's time zone (System
Settings → Time Zone, ``America/Denver`` on prod) — that is what ``now_datetime()`` returns
and what every report compares against. A browser does not send that. ``Date.toISOString()``
sends UTC with a ``Z``, and ``get_datetime`` hands that back as a tz-*aware* datetime. Frappe
stringifies it on insert as ``2026-09-24 03:04:24.049000+00:00``, and MariaDB refuses the
offset outright: ``(1292, "Incorrect datetime value ...")``. That rolled back every kiosk
camera capture from v1.241.0 to v1.526.0 — the row that proves a job was photographed was
never written (fixed v1.526.1).

Dropping the offset is not the fix either. ``03:04 UTC`` stored as-is reads as 03:04 *site*
time — six hours after the photo was taken, on the next calendar day. An aware value is
converted to the site's zone first, and only then made naive.

Pure apart from three ``frappe.utils`` calls, so ``tests/test_workforce_client_time.py``
pins it without a bench.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from frappe.utils import get_datetime, get_system_timezone, now_datetime


def to_site_naive(value):
	"""``value`` as a naive datetime on the site's clock.

	Aware → converted to the site's time zone, then the offset dropped. Naive → returned
	untouched: a naive value is site-local by convention (``geo.js`` sends the device's
	wall-clock time with no offset, and the crews' phones are in the site's zone). None
	passes through.
	"""
	if value is None or value.tzinfo is None:
		return value
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _from_epoch_ms(ms):
	# An epoch is an instant, so it is read as UTC. Plain ``fromtimestamp`` would read it
	# in the *server process's* zone, which on prod is UTC and not the site's.
	return to_site_naive(datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC))


def parse_client_timestamp(ts):
	"""A client-supplied timestamp as a naive site-local datetime.

	Accepts an ISO-8601 string with or without an offset (``2026-09-24T03:04:24.049Z``,
	``2026-09-23 21:04:24``), a datetime, or epoch milliseconds as a number or numeric
	string. Empty → now. An unparseable string raises, as it always has; the caller decides
	whether a bad stamp is worth losing the record over.
	"""
	if ts in (None, ""):
		return now_datetime()
	if isinstance(ts, int | float):
		return _from_epoch_ms(ts)
	try:
		parsed = get_datetime(ts)
	except Exception:
		# Possibly epoch-ms delivered as a string.
		return _from_epoch_ms(ts)
	return to_site_naive(parsed)
