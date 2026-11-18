# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A rate limit counted per signed-in user, for whitelisted endpoints.

Frappe's own ``@frappe.rate_limiter.rate_limit`` keys its counter on the client IP
address or on a value taken from the request's form data. Neither is the person:
every phone and desk in the office shares one public address, so an IP-keyed limit
on a staff endpoint is one bucket for the whole company, and a form-data key is
whatever the client chooses to send. :func:`user_rate_limit` keys on
``frappe.session.user`` instead.

It follows ``api/feedback.py``'s ``_enforce_filing_rate``: ``make_key`` gives the
counter the site's prefix (``incrby`` is raw redis), and the expiry is set on the
first hit only, so the window is fixed rather than sliding forever under a steady
trickle. The deploy's FLUSHDB resets every counter, which is harmless.

Two deliberate choices:

* **Only inside a web request.** A scheduler job or a background worker calling the
  same function in-process is not throttled (the same rule as Frappe's own
  decorator, which returns early when there is no ``frappe.request``).
* **A counter that cannot be read is not a refusal.** If redis is unavailable the
  call goes through. This is a ceiling on volume, not an access check; the
  endpoint's own permission checks still run either way.

Usage, below ``@frappe.whitelist()`` so the whitelisted object is the wrapper::

	@frappe.whitelist()
	@user_rate_limit(limit=30, seconds=3600)
	def send_something(...):
		...
"""

import functools

import frappe
from frappe import _


def _in_request():
	return bool(getattr(getattr(frappe, "local", None), "request", None))


def enforce_user_rate(bucket, limit, seconds, user=None):
	"""Count one call for ``user`` (default: the session user) in ``bucket``, and raise
	``frappe.RateLimitExceededError`` once more than ``limit`` calls fall inside one
	``seconds``-long window. A no-op outside a web request."""
	if not _in_request():
		return
	user = user or frappe.session.user
	if not user:
		return
	try:
		key = frappe.cache.make_key(f"ee_user_rate:{bucket}:{user}")
		count = frappe.cache.incrby(key, 1)
		# The window starts on the first call, and is set again whenever the counter is found
		# without one, so a counter can never outlive its window.
		if count == 1 or frappe.cache.ttl(key) < 0:
			frappe.cache.expire(key, seconds)
	except Exception:
		return
	if count > limit:
		frappe.throw(
			_("Too many requests in a short time. Wait a little and try again."),
			frappe.RateLimitExceededError,
		)


def user_rate_limit(limit, seconds=3600, bucket=None):
	"""Decorator form of :func:`enforce_user_rate`. ``bucket`` defaults to the wrapped
	function's dotted path, so each endpoint has its own counter."""

	def decorator(fn):
		name = bucket or f"{fn.__module__}.{fn.__qualname__}"

		@functools.wraps(fn)
		def wrapper(*args, **kwargs):
			enforce_user_rate(name, limit, seconds)
			return fn(*args, **kwargs)

		return wrapper

	return decorator
