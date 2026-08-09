# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retry policy for the Google Chat transport — **pure functions, no I/O, no Frappe**.

Two decisions live here and nothing else: *how long to wait* and *whether to wait at
all*. They are separated from :mod:`erpnext_enhancements.chat.gchat.client` on purpose,
and the purpose is testability rather than taste. This repo has **no Frappe
integration-test job** (``CLAUDE.md``), so the only tier with automatic regression
protection in CI is the bench-free one — and a bench-free test can only reach code that
imports neither ``frappe`` nor a network library. This module imports neither, which is
why the retry policy is the part of the transport that CI can actually hold still.

**The one rule that matters more than the arithmetic: never retry a 4xx other than 429.**
A ``403`` from the Chat API means the identity is wrong, the scope is missing, or the
domain-wide-delegation binding was never granted — every one of which is a configuration
fault that a retry converts from a fast, legible failure into a slow, confusing one. ADR
0009 §G names this explicitly and the classifier's table test is what keeps it true.

Defaults here are **fallbacks, not policy**. The live values come from ``Chat Settings``
(``relay_max_attempts``, ``relay_initial_backoff_seconds``, ``backoff_cap_seconds``) and
are read by the caller — see :class:`~erpnext_enhancements.chat.gchat.client.GoogleChatClient`.
Nothing in this module reads settings, because reading settings is I/O.
"""

from __future__ import annotations

import random
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Final

# Fallbacks only. ADR 0009 §G / Phase 1 §4.5 state base 0.5s, cap 32s, 5 retries, and the
# shipped `Chat Settings` defaults now agree (2s base / 32s cap / 5 attempts). The cap
# matters more than it looks: the client sleeps *synchronously* inside its retry loop, so
# the cap multiplied by the attempt count is how long one call can pin a worker. An earlier
# 300s default here would have let a single 429 storm hold a relay worker for ~20 minutes.
# Whichever values the site holds win, because the caller passes them in.
DEFAULT_BACKOFF_BASE_SECONDS: Final[float] = 0.5
DEFAULT_BACKOFF_CAP_SECONDS: Final[float] = 32.0
DEFAULT_MAX_RETRIES: Final[int] = 5

# Exactly the five statuses ADR 0009 §G authorises. Deliberately a closed set rather than
# "any 5xx": the audited list is the contract the table test asserts, and an unlisted 5xx
# is rare enough that failing fast and surfacing it beats silently absorbing it.
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

# Matched against every class name in the exception's MRO, so both `requests`' exception
# tree and the builtin socket errors are covered without importing `requests` — which the
# bench-free CI tier does not install.
_RETRYABLE_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
	{
		"ChunkedEncodingError",
		"ConnectionAbortedError",
		"ConnectionError",
		"ConnectionResetError",
		"ConnectTimeout",
		"IncompleteRead",
		"ProtocolError",
		"ReadTimeout",
		"RemoteDisconnected",
		"Timeout",
		"TimeoutError",
	}
)

# Checked FIRST, and the ordering is load-bearing: `requests.exceptions.SSLError`
# subclasses `requests.exceptions.ConnectionError`, so a naive MRO scan retries a
# certificate failure. A TLS error against the Chat API host is a trust-store or
# interception problem; retrying it five times only delays the person who has to read it.
_NEVER_RETRY_EXCEPTION_NAMES: Final[frozenset[str]] = frozenset(
	{
		"InvalidSchema",
		"InvalidURL",
		"MissingSchema",
		"SSLCertVerificationError",
		"SSLError",
		"TooManyRedirects",
		"URLRequired",
	}
)

# 2 ** attempt with an unbounded attempt is an OverflowError waiting for a bad caller.
# Clamped well past the point where min(cap, ...) has already saturated.
_MAX_EXPONENT: Final[int] = 30


class RetryDecision(str, Enum):
	"""What the transport should do with a failed attempt."""

	RETRY = "RETRY"
	FAIL = "FAIL"


def compute_backoff(
	attempt: int,
	base: float = DEFAULT_BACKOFF_BASE_SECONDS,
	cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
	retry_after: float | None = None,
	*,
	rng: random.Random | None = None,
) -> float:
	"""Seconds to sleep before retry number ``attempt`` (0-based), with **full jitter**.

	``random.uniform(0, min(cap, base * 2 ** attempt))`` — the full-jitter variant, not
	the "half the delay plus jitter" one. Full jitter is what actually de-synchronises a
	fleet: with several relay workers retrying the same space after a 503, equal-width
	sleeps re-collide on the next attempt and the herd survives the backoff.

	``retry_after`` **wins over the computed value** when present and non-negative, because
	a ``Retry-After`` from Google is information and a jittered guess is not. It is still
	clamped to ``cap``: an over-long server hint would otherwise park a worker for minutes
	holding a queue slot. Clamping trades "obeyed exactly" for "bounded", and the retry
	budget rather than the sleep is what then absorbs a long outage.

	``rng`` exists so a test can seed the sequence without mutating global RNG state; the
	default draws from the module-level ``random``, which honours ``random.seed()``.

	The return value is never negative and never exceeds ``cap`` — both are asserted by
	``tests/test_chat_gchat_client.py``, because "jitter produced a negative sleep" is the
	kind of defect that only shows up as a ``ValueError`` from ``time.sleep`` at 3am.
	"""
	safe_cap = max(float(cap), 0.0)
	safe_base = max(float(base), 0.0)
	safe_attempt = max(int(attempt), 0)

	if retry_after is not None:
		hinted = float(retry_after)
		if hinted >= 0.0:
			return min(hinted, safe_cap)
		# A negative hint is a malformed header, not an instruction. Fall through.

	ceiling = min(safe_cap, safe_base * float(2 ** min(safe_attempt, _MAX_EXPONENT)))
	ceiling = max(ceiling, 0.0)
	if ceiling == 0.0:
		return 0.0

	draw = rng.uniform(0.0, ceiling) if rng is not None else random.uniform(0.0, ceiling)
	# uniform() is documented as possibly returning the endpoint due to rounding; clamp
	# both ends rather than reason about float edge cases at a call site that sleeps.
	return min(max(draw, 0.0), safe_cap)


def parse_retry_after(value: str | float | None, *, now: float | None = None) -> float | None:
	"""Normalise a ``Retry-After`` header to seconds, or ``None`` if it is unusable.

	RFC 9110 allows either a delta in seconds or an HTTP-date, and Google has been
	observed to send both across its APIs. Returning ``None`` rather than ``0`` for junk
	matters: ``0`` would look like "retry immediately" and defeat the backoff entirely.

	``now`` (a POSIX timestamp) is injectable so the HTTP-date branch is deterministic
	under test. Reading the clock is the only impurity in this module and it is confined
	to the branch that cannot avoid it.
	"""
	if value is None:
		return None

	if isinstance(value, int | float) and not isinstance(value, bool):
		return float(value) if float(value) >= 0.0 else None

	text = str(value).strip()
	if not text:
		return None

	try:
		seconds = float(text)
	except ValueError:
		pass
	else:
		return seconds if seconds >= 0.0 else None

	try:
		when = parsedate_to_datetime(text)
	except (TypeError, ValueError):
		return None
	if when is None:
		return None

	if now is None:
		import time

		now = time.time()
	delta = when.timestamp() - float(now)
	return delta if delta >= 0.0 else 0.0


def classify_error(status: int | None, exc: BaseException | None = None) -> RetryDecision:
	"""Decide whether a failed attempt is worth repeating.

	``RETRY`` for ``429/500/502/503/504`` and for connection/read timeouts. ``FAIL`` for
	everything else — every other 4xx included, and that is the point of the function.

	The exception branch matches on **class names across the MRO** instead of importing
	``requests``: this module has to stay importable in the bench-free CI tier, which
	installs neither ``frappe`` nor ``requests``. The deny-list is consulted first because
	``requests``' ``SSLError`` is a subclass of its ``ConnectionError`` and would otherwise
	be retried (see ``_NEVER_RETRY_EXCEPTION_NAMES``).

	With no status and no exception there is nothing to reason about, so the answer is
	``FAIL``: a transport that retries on absence of evidence retries forever.
	"""
	if exc is not None:
		names = {type(exc).__name__} | {klass.__name__ for klass in type(exc).__mro__}
		if names & _NEVER_RETRY_EXCEPTION_NAMES:
			return RetryDecision.FAIL
		if names & _RETRYABLE_EXCEPTION_NAMES:
			return RetryDecision.RETRY
		return RetryDecision.FAIL

	if status is None:
		return RetryDecision.FAIL

	return RetryDecision.RETRY if int(status) in RETRYABLE_STATUS_CODES else RetryDecision.FAIL


def should_retry(decision: RetryDecision, attempt: int, max_attempts: int) -> bool:
	"""Combine the classifier with the attempt budget, so no caller re-derives the
	off-by-one.

	``attempt`` is 0-based and counts attempts already **made**, so the last permitted
	attempt is ``max_attempts - 1`` and there is no retry after it.
	"""
	if decision is not RetryDecision.RETRY:
		return False
	return int(attempt) + 1 < max(int(max_attempts), 1)
