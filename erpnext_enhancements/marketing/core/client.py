# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one HTTP transport every ad-platform call goes through. ``requests``, no SDK.

No SDK for the reason every other connector here has none (ADR 0004): the host is a managed
server with no pip-install step in the deploy, and Google's and Meta's SDKs are large,
fast-moving and would pin transitive versions this app does not control.

Three rules live here and nowhere else:

1. **Read-only by construction.** A request is sent only if its method and path match
   ``constants.READ_ONLY_ALLOWLIST``. Anything else raises ``ReadOnlyViolation`` before a
   byte leaves the process -- a bug elsewhere cannot turn into a changed bid or budget.
2. **Retry only what can succeed on retry.** 408/429/5xx and transport errors, with
   exponential backoff, honoring ``Retry-After``. Every other 4xx fails at once: retrying a
   bad request or a dead credential only burns quota and delays the error.
3. **No secret leaves in an exception.** Every raise is ``from None`` so the traceback
   carries no frame locals (this app has published private key material that way before),
   and every provider message is passed through ``utils.redact_text``.
"""

import random
import re
import time
from urllib.parse import urlsplit

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.utils import redact_text, redact_url


class MarketingAPIError(Exception):
	"""A failed ad-platform call. ``status`` is None for a transport failure."""

	def __init__(self, platform, message, status=None):
		super().__init__(f"{platform}: {redact_text(message, 500)}")
		self.platform = platform
		self.status = status

	@property
	def is_auth_failure(self):
		return self.status in C.AUTH_FAILURE_STATUSES

	@property
	def retryable(self):
		return self.status is None or self.status in C.RETRYABLE_STATUSES


class ReadOnlyViolation(MarketingAPIError):
	"""A request outside the read-only allowlist. Never retried, never sent."""


def allowed(platform, method, url):
	"""True if ``method url`` is on the read-only allowlist for ``platform``."""
	parts = urlsplit(url)
	if parts.scheme != "https" or parts.hostname != C.DATA_HOSTS.get(platform):
		return False
	path = parts.path
	prefix = C.PATH_PREFIX[platform]
	if not path.startswith(prefix):
		return False
	path = path[len(prefix) :]
	return any(
		p == platform and m == method.upper() and re.match(pattern, path)
		for p, m, pattern in C.READ_ONLY_ALLOWLIST
	)


def backoff_seconds(attempt, retry_after=None, rand=random.random):
	"""Seconds to wait before retry ``attempt`` (1-based). Pure."""
	if retry_after is not None:
		try:
			return min(max(float(retry_after), 0.0), C.RETRY_AFTER_CAP_SECONDS)
		except (TypeError, ValueError):
			pass
	base = min(C.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), C.BACKOFF_CAP_SECONDS)
	return base * (0.5 + rand() / 2)


class Transport:
	"""Sends allowlisted requests with retries. One per sync run.

	``http`` and ``sleep`` are injectable so the retry policy is tested without a network
	or a clock. ``archive`` is called with ``(platform, method, url, status, text)`` for
	every response the sync wants kept (Marketing Raw Payload); the URL is redacted first.
	"""

	def __init__(
		self, platform, *, headers, max_retries=3, timeout=30, http=None, sleep=time.sleep, archive=None
	):
		if http is None:
			import requests

			http = requests
		self.platform = platform
		self._headers = dict(headers)
		self.max_retries = max(int(max_retries or 0), 0)
		self.timeout = timeout
		self.http = http
		self.sleep = sleep
		self.archive = archive

	def request(self, method, url, *, params=None, json=None):
		"""Send one read request and return the parsed JSON body."""
		method = method.upper()
		if not allowed(self.platform, method, url):
			raise ReadOnlyViolation(
				self.platform, f"refused {method} {redact_url(url)}: not on the read-only allowlist"
			)

		attempt = 0
		while True:
			attempt += 1
			try:
				response = self.http.request(
					method, url, params=params, json=json, headers=self._headers, timeout=self.timeout
				)
			except Exception as exc:  # requests.RequestException and friends
				error = MarketingAPIError(
					self.platform, f"transport error: {type(exc).__name__}", status=None
				)
				if attempt > self.max_retries:
					raise error from None
				self.sleep(backoff_seconds(attempt))
				continue

			status = response.status_code
			if status < 400:
				if self.archive:
					self.archive(
						self.platform, method, redact_url(response.url or url), status, response.text
					)
				try:
					return response.json()
				except ValueError:
					raise MarketingAPIError(self.platform, "response was not JSON", status=status) from None

			message = _provider_message(response)
			error = MarketingAPIError(self.platform, f"HTTP {status}: {message}", status=status)
			if not error.retryable or attempt > self.max_retries:
				raise error from None
			self.sleep(backoff_seconds(attempt, response.headers.get("Retry-After")))


def _provider_message(response):
	"""The provider's own error text, redacted and short. Never the request."""
	try:
		body = response.json()
	except ValueError:
		return redact_text(response.text, 300)
	error = body.get("error") if isinstance(body, dict) else None
	if isinstance(error, dict):
		return redact_text(error.get("message") or error.get("status") or str(error), 300)
	if isinstance(body, dict) and body.get("message"):
		return redact_text(body["message"], 300)
	return redact_text(str(body), 300)
