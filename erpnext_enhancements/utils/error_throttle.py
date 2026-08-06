# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A circuit breaker for ``frappe.log_error``.

Error Log is a DocType, so every ``frappe.log_error`` is an INSERT. A caller
that fails inside a loop — a scheduler retrying a dead credential, an hourly
walk over every Customer — therefore writes one *row* per failure, and the log
stops being readable exactly when something is wrong enough to need reading.

This is not hypothetical. On 2026-06-15/16 the MDM retry loop hit a standing
Miradore ``401`` and wrote **44,069** rows in about thirty hours — 88% of the
entire Error Log table at the time — for what was, in substance, one fact: a
credential had expired. A QuickBooks import storm added ~27k more over the
following two days, and a helpdesk filelock contention loop another 3,781.

:func:`log_error_throttled` collapses that. The first ``limit`` occurrences of
a signature inside ``window`` seconds are logged in full; the next one is logged
as a suppression notice; the rest are counted and dropped. A storm becomes a
handful of rows that say the same thing the 44,069 said, and the operator can
still see every *distinct* problem because throttling is per-signature, never
global.

Deliberately *not* a replacement for ``frappe.log_error`` everywhere. Use it at
call sites that can repeat unboundedly — scheduler loops, per-row iteration,
retry paths. A one-shot failure in a request handler should still log every
time; that is a fact, not a storm.
"""

from __future__ import annotations

import hashlib

import frappe

#: How long one signature's budget lasts, in seconds.
DEFAULT_WINDOW = 3600
#: How many full rows a signature may write inside ``DEFAULT_WINDOW``.
DEFAULT_LIMIT = 3

_CACHE_PREFIX = "ee_error_throttle::"


def _signature(title: str, key: str | None) -> str:
	"""The cache key a message throttles against.

	Defaults to the *title*, not the message body, because tracebacks embed
	line numbers, ids and timestamps that differ on every occurrence — keying on
	the body would give every repeat its own budget and throttle nothing. Pass
	an explicit ``key`` to split one title into several independent budgets (for
	example per provider, so a dead Miradore credential cannot mask a separate
	Action1 failure).
	"""
	raw = f"{title}::{key or ''}"
	return _CACHE_PREFIX + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def log_error_throttled(
	message: str,
	title: str,
	*,
	window: int = DEFAULT_WINDOW,
	limit: int = DEFAULT_LIMIT,
	key: str | None = None,
) -> bool:
	"""``frappe.log_error``, capped at ``limit`` rows per ``window`` seconds.

	Returns True when the row was written, False when it was suppressed, so a
	caller that also emails/notifies can stay in step with the log.

	The cache is the only state. If it is unavailable — a Redis blip, a bench
	context without one — this falls back to logging unconditionally: losing an
	error is worse than writing a duplicate, and a broken cache must not also
	break the record of *why* something broke.
	"""
	cache_key = _signature(title, key)
	try:
		count = frappe.cache().incr(cache_key)
		if count == 1:
			# incr creates the key without a TTL; stamp one on the first hit so
			# the budget actually expires instead of throttling forever.
			frappe.cache().expire(cache_key, window)
	except Exception:
		frappe.log_error(message, title)
		return True

	if count <= limit:
		frappe.log_error(message, title)
		return True

	if count == limit + 1:
		frappe.log_error(
			f"{message}\n\n"
			f"--- Further identical '{title}' errors are suppressed for up to {window}s "
			f"to keep the Error Log readable (see utils/error_throttle.py). "
			f"The underlying failure is still happening; fix the cause, not the log. ---",
			f"{title} (throttled)",
		)
		return True

	return False


def suppressed_count(title: str, key: str | None = None) -> int:
	"""How many occurrences of this signature the current window has seen.

	Read-only; used by tests and by diagnostics that want to report "this failed
	N times" without re-reading the Error Log table.
	"""
	try:
		return int(frappe.cache().get_value(_signature(title, key)) or 0)
	except Exception:
		return 0


def reset(title: str, key: str | None = None) -> None:
	"""Clear one signature's budget — for tests, and for an operator who has
	just fixed the cause and wants the next failure to be logged in full."""
	try:
		frappe.cache().delete_value(_signature(title, key))
	except Exception:
		pass
