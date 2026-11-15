# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Carrier Tracking Settings read for the client, and Redis as its token cache.

**Off until three things are true for a carrier:** the master switch, that carrier's own switch,
and both its client id and secret. ``carrier_config`` returns ``None`` otherwise, and every caller
treats ``None`` as "do nothing, say nothing" (the poller logs nothing for an unconfigured carrier).

**Read so a never-saved Single is safe.** The settings are read with ``frappe.get_cached_doc``,
which builds a never-saved Single from its JSON defaults, and every value is then judged by what
it is rather than trusted: an environment that is not exactly "Production" is the sandbox, a blank
switch is off. Nothing here writes to the Single (see the controller's docstring for why).

**Secrets** are read with ``get_password(raise_exception=False)`` at the moment of the call and
handed straight to the client; they are never returned to a browser, put into a message, or cached.
The cache holds only access tokens, under the TTL the client sets.
"""

import frappe

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import client

SETTINGS = "Carrier Tracking Settings"

_PREFIX = {C.FEDEX: "fedex", C.UPS: "ups"}


def _settings():
	try:
		if not frappe.db.exists("DocType", SETTINGS):
			return None
		return frappe.get_cached_doc(SETTINGS)
	except Exception:
		return None


def feature_enabled(settings=None):
	settings = settings or _settings()
	return bool(settings and frappe.utils.cint(settings.get("enabled")))


def carrier_state(carrier, settings=None):
	"""``(switched_on, has_credentials, environment)`` for one carrier. Reads secrets as booleans."""
	settings = settings or _settings()
	prefix = _PREFIX.get(carrier)
	if not settings or not prefix:
		return False, False, client.SANDBOX
	switched_on = bool(frappe.utils.cint(settings.get("enabled"))) and bool(
		frappe.utils.cint(settings.get(f"{prefix}_enabled"))
	)
	environment = (
		client.PRODUCTION if settings.get(f"{prefix}_environment") == client.PRODUCTION else client.SANDBOX
	)
	has_id = bool((settings.get(f"{prefix}_client_id") or "").strip())
	# A Password field holds asterisks in the document when a secret is stored.
	has_secret = bool(settings.get(f"{prefix}_client_secret"))
	return switched_on, has_id and has_secret, environment


def carrier_config(carrier, settings=None):
	"""A ``client.Config`` when the carrier is on and has credentials, else ``None``."""
	settings = settings or _settings()
	switched_on, has_credentials, environment = carrier_state(carrier, settings)
	if not (switched_on and has_credentials):
		return None
	prefix = _PREFIX[carrier]
	try:
		secret = settings.get_password(f"{prefix}_client_secret", raise_exception=False)
	except Exception:
		secret = None
	if not secret:
		return None
	return client.Config(
		carrier=carrier,
		environment=environment,
		client_id=(settings.get(f"{prefix}_client_id") or "").strip(),
		client_secret=secret,
	)


class RedisCache:
	"""The three calls the client makes, on ``frappe.cache``. Values are pickled by Frappe."""

	def get(self, key):
		return frappe.cache.get_value(key)

	def set(self, key, value, seconds):
		frappe.cache.set_value(key, value, expires_in_sec=int(seconds))

	def delete(self, key):
		frappe.cache.delete_value(key)


def cache():
	return RedisCache()


def track(carrier, number, settings=None):
	"""The client's answer for one number, configured from the settings. Never raises."""
	try:
		config = carrier_config(carrier, settings)
		if config is None:
			return client._result(
				client.Config(carrier, client.SANDBOX, None, None),
				C.clean_number(number),
				frappe.utils.now_datetime(),
				"unsupported" if carrier not in C.TRACKABLE else "not_configured",
			)
		return client.track(config, number, cache(), now=frappe.utils.now_datetime())
	except Exception:
		return client._result(
			client.Config(carrier, client.SANDBOX, None, None),
			C.clean_number(number),
			frappe.utils.now_datetime(),
			"bad_response",
			"Something went wrong while asking the carrier.",
		)


def forget_tokens():
	"""Drop every cached token and remembered refusal (the settings were saved)."""
	try:
		frappe.cache.delete_keys(f"{client.CACHE_PREFIX}:")
	except Exception:
		pass
