# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one HTTP transport publishing calls go through. ``requests``, no SDK.

**Separate from ``core/client.py`` on purpose.** That is the ad connectors' transport, read-only
by construction: it refuses every Meta and LinkedIn POST and must stay that way. Publishing
writes, so it gets its own transport and its own allowlist (``publish/constants.PUBLISH_ALLOWLIST``),
and that list never names an ad endpoint. ``tests/test_marketing_publish_oauth.py`` checks both
directions: this transport refuses an ads path, and the ads transport refuses a publishing one.

The retry rules differ from the read path, and the difference is the point:

* **Reads retry** on 408/429/5xx and transport errors, with the ad connectors' backoff.
* **Writes never retry on their own.** A create that timed out or came back 502 may already
  have published; sending it again can put the same post up twice, in public. Whether to try
  again is the outbox's call (TASK-2026-01481), made with the job's state in hand.
* **One retry after a 401, for any method**, with a freshly refreshed token. A 401 means the
  platform refused the request before acting on it, so resending cannot duplicate anything. A
  second 401 means a person has to reconnect.

Every raise is ``from None`` with a redacted message, as in ``core/client.py``: the request
carries a bearer token.
"""

import time
from urllib.parse import urlsplit

from erpnext_enhancements.marketing.core.client import (
	MarketingAPIError,
	_provider_message,
	backoff_seconds,
	effective_status,
)
from erpnext_enhancements.marketing.core.utils import redact_url
from erpnext_enhancements.marketing.publish import constants as P

#: Methods safe to resend after a transient failure.
RETRYABLE_METHODS = frozenset({"GET"})


class PublishViolation(MarketingAPIError):
	"""A request outside the publishing allowlist. Never sent, never retried."""


class NotPublished(MarketingAPIError):
	"""Raised by a publisher's send() after an ambiguous failure it has **verified** did not publish.

	The outbox retries it rather than holding it Unconfirmed. Raise it only on evidence -- an
	Instagram container that still reads FINISHED, not PUBLISHED -- never on a guess.
	"""


def error_codes(response):
	"""``(code, subcode)`` from a Graph API error body, or ``(None, None)``. Pure."""
	try:
		body = response.json()
	except ValueError:
		return None, None
	error = body.get("error") if isinstance(body, dict) else None
	if not isinstance(error, dict):
		return None, None
	return error.get("code"), error.get("error_subcode")


def allowed(connection, method, url):
	"""True if ``method url`` is on the publishing allowlist for ``connection``."""
	parts = urlsplit(url)
	if parts.scheme != "https":
		return False
	return any(
		c == connection and m == method.upper() and host == parts.hostname and pattern.match(parts.path)
		for c, m, host, pattern in P.PUBLISH_ALLOWLIST
	)


class PublishTransport:
	"""Sends allowlisted requests for one publishing connection.

	``token`` is the bearer token to start with. ``refresh``, when given, is called with no
	arguments after a 401 and must return a new token or raise ``MarketingAPIError`` with
	status 401. ``headers`` are extra headers (LinkedIn's version header, for one).
	``observe(status, headers)``, when given, sees every response -- the rate limiter
	(``ratelimit.observe``) reads Meta's usage headers and a 429's Retry-After from it -- and
	is best-effort: a limiter failure never fails a request. ``http`` and ``sleep`` are
	injectable so the rules above are tested without a network or a clock.
	"""

	def __init__(
		self,
		connection,
		*,
		token,
		refresh=None,
		headers=None,
		max_retries=3,
		timeout=30,
		http=None,
		sleep=time.sleep,
		observe=None,
	):
		if http is None:
			import requests

			http = requests
		self.connection = connection
		self._token = token
		self._refresh = refresh
		self._extra_headers = dict(headers or {})
		self.max_retries = max(int(max_retries or 0), 0)
		self.timeout = timeout
		self.http = http
		self.sleep = sleep
		self._observe = observe

	def _headers(self):
		return {**self._extra_headers, "Authorization": f"Bearer {self._token}"}

	def _observed(self, status, response):
		if not self._observe:
			return
		try:
			self._observe(status, dict(getattr(response, "headers", None) or {}))
		except Exception:
			pass  # an optimisation failing must not fail the request (see ratelimit.py)

	def request(self, method, url, *, params=None, json=None, data=None, headers=None, with_headers=False):
		"""Send one request and return the parsed JSON body (``{}`` for an empty one).

		``headers`` are added for this request only (LinkedIn's ``X-RestLi-Method: FINDER``);
		``data`` may be raw bytes (an upload PUT). ``with_headers`` returns ``(body, headers)``,
		for a network that answers in a header (LinkedIn's new post URN is in ``x-restli-id``).
		The bearer token goes on every request, which is why the allowlist pins hosts: it can
		reach only the network it belongs to, upload hosts included.
		"""
		method = method.upper()
		if not allowed(self.connection, method, url):
			raise PublishViolation(
				self.connection, f"refused {method} {redact_url(url)}: not on the publishing allowlist"
			)

		attempt = 0
		refreshed = False
		while True:
			attempt += 1
			try:
				response = self.http.request(
					method,
					url,
					params=params,
					json=json,
					data=data,
					headers={**self._headers(), **(headers or {})},
					timeout=self.timeout,
				)
			except Exception as exc:  # requests.RequestException and friends
				error = MarketingAPIError(self.connection, f"transport error: {type(exc).__name__}")
				if method not in RETRYABLE_METHODS or attempt > self.max_retries:
					raise error from None
				self.sleep(backoff_seconds(attempt))
				continue

			status = effective_status(response)  # Meta's dead-token 400 reads as 401
			self._observed(status, response)
			if status < 400:
				body = {}
				if (response.text or "").strip():
					try:
						body = response.json()
					except ValueError:
						if method != "PUT":  # an upload host may answer with anything
							raise MarketingAPIError(
								self.connection, "response was not JSON", status=status
							) from None
				if with_headers:
					return body, {str(k).lower(): v for k, v in (response.headers or {}).items()}
				return body

			if status == 401 and self._refresh and not refreshed:
				refreshed = True
				self._token = self._refresh()
				attempt -= 1  # the auth retry does not spend a transient retry
				continue

			error = MarketingAPIError(
				self.connection, f"HTTP {status}: {_provider_message(response)}", status=status
			)
			# The network's own error codes, for a publisher that needs them (Instagram's
			# "daily limit reached" is a 400 with a subcode, and must be read as a 429).
			error.code, error.subcode = error_codes(response)
			if method not in RETRYABLE_METHODS or not error.retryable or attempt > self.max_retries:
				raise error from None
			self.sleep(backoff_seconds(attempt, response.headers.get("Retry-After")))
