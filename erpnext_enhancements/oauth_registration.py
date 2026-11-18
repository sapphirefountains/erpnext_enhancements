# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Which OAuth clients may register themselves on this site (v1.826.9).

Staff connect AI clients to this site's MCP server over OAuth, and those clients register
themselves first (RFC 7591 dynamic client registration). Two guest POST endpoints do that:
frappe's ``frappe.integrations.oauth2.register_client`` and Frappe Assistant Core's
``frappe_assistant_core.api.oauth_registration.register_client``. Both read a JSON body with
``redirect_uris`` and ``client_name``.

Nikolas's decision (2026-11-18): dynamic registration stays on, but only for clients whose
redirect addresses are on an allow-list, and at a limited rate. :func:`guard_registration`
(``before_request``) enforces that before either endpoint runs.

**Which requests it judges.** A POST whose path is a method call (``/api/method/<dotted>``,
``/api/v1/method/...``, ``/api/v2/method/...``) or whose ``cmd`` argument is a dotted name, where
the last part of the dotted name is ``register_client``. Matching the last part rather than two
exact names covers a second dotted name for the same function too (see the note in ``hooks.py``
about aliases of whitelisted functions). Every other request returns at once, after a method
check and a string test, and nothing on that path can raise.

**What it allows.** Every entry in ``redirect_uris`` must match the allow-list; one entry that
does not refuses the whole registration. Built in:

* ``https://claude.ai/api/mcp/auth_callback`` and ``https://claude.com/api/mcp/auth_callback``
  (exact path)
* ``https://oauth-redirect.googleusercontent.com``, ``oauth-redirect-sandbox.googleusercontent.com``
  and ``oauth-redirect-test.googleusercontent.com`` (any path)

A URI must be https, its host must equal the listed host exactly (letter case aside: no trailing
dot, no port, no user name), and it may not carry a fragment, a dot segment, a percent-encoded
dot, slash or backslash, or any character outside RFC 3986's set (an internationalized host is
accepted only in its ``xn--`` form, and only if listed so).

**Site config** (``bench --site <site> set-config``):

* ``oauth_registration_redirect_allowlist``: extra entries, a list of ``host`` or
  ``host/path-prefix`` strings, for example ``["example.org/oauth/callback"]``. A loopback client
  is allowed only when listed explicitly as ``http://localhost`` or ``http://127.0.0.1`` (with an
  optional path prefix); a loopback entry accepts any port. Entries with a wildcard, a port or a
  user name are ignored.
* ``oauth_registration_guard``: ``0`` turns this hook off; anything else, or no key, leaves it on.

**Rate.** At most :data:`RATE_LIMIT` registration requests per client address per
:data:`RATE_WINDOW_SECONDS`, counted before the body is judged. The address is
``frappe.local.request_ip``, the same one every IP-keyed limit in this app uses; why that is the
client and not the load balancer is in ``utils/client_ip.py``. The window is fixed: the counter's
expiry is set on its first hit only.

**Refusals** end the request from the hook with a JSON body in RFC 7591's error shape:
400 ``invalid_redirect_uri``, 400 ``invalid_client_metadata`` when the body is not a JSON object
with a list of redirect URIs, or 429 when over the rate. frappe v16's ``application`` turns a
raised werkzeug ``HTTPException`` into its own response (``app.py``, ``e.get_response``), which is
how a ``before_request`` hook sends a chosen status and body. On a matching request anything
unexpected also refuses (400), so the check fails closed.
"""

import json
from urllib.parse import urlsplit

import frappe

#: Config keys (site_config.json).
GUARD_KEY = "oauth_registration_guard"
ALLOWLIST_KEY = "oauth_registration_redirect_allowlist"

METHOD_NAME = "register_client"

RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 3600

#: More redirect URIs than any real client sends in one registration.
MAX_REDIRECT_URIS = 20
MAX_URI_LENGTH = 2048

LOOPBACK_HOSTS = ("localhost", "127.0.0.1")

#: (scheme, host, path, exact). ``path`` None allows any path; ``exact`` False makes it a prefix.
BUILTIN_RULES = (
	("https", "claude.ai", "/api/mcp/auth_callback", True),
	("https", "claude.com", "/api/mcp/auth_callback", True),
	("https", "oauth-redirect.googleusercontent.com", None, False),
	("https", "oauth-redirect-sandbox.googleusercontent.com", None, False),
	("https", "oauth-redirect-test.googleusercontent.com", None, False),
)

# RFC 3986 unreserved and reserved characters, plus "%". Anything else (space, backslash, quotes,
# control characters, non-ASCII) refuses the URI.
_URI_CHARS = frozenset(
	"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~:/?#[]@!$&'()*+,;=%"
)
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-.")
_ENCODED_SEPARATORS = ("%2e", "%2f", "%5c")


# ---------------------------------------------------------------- which requests


def is_registration_method(dotted):
	"""True when a dotted method name ends in ``register_client``."""
	if not isinstance(dotted, str) or not dotted:
		return False
	return dotted.strip().rsplit(".", 1)[-1] == METHOD_NAME


def is_registration_path(path):
	"""True for a method-call path (``/api/[v1/|v2/]method/...``) naming a ``register_client``."""
	if not isinstance(path, str) or not path.startswith("/api/"):
		return False
	segments = [s for s in path.split("/") if s]
	if len(segments) < 3 or "method" not in segments[1:-1]:
		return False
	return is_registration_method(segments[-1])


def _is_registration_request():
	request = getattr(frappe.local, "request", None)
	if request is None or str(getattr(request, "method", "") or "").upper() != "POST":
		return False
	if is_registration_path(getattr(request, "path", None)):
		return True
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	return is_registration_method(form_dict.get("cmd"))


# ---------------------------------------------------------------- the allow-list


def _valid_host(host):
	if not host or host[0] in ".-" or host[-1] in ".-" or ".." in host:
		return False
	return all(c in _HOST_CHARS for c in host)


def _valid_path(path):
	"""No dot segments, no empty segments, no percent-encoded separators."""
	if not path:
		return True
	lowered = path.lower()
	if any(token in lowered for token in _ENCODED_SEPARATORS):
		return False
	segments = path.split("/")[1:]
	return not any(s in (".", "..") for s in segments) and "//" not in path


def parse_allowlist_entry(entry):
	"""A rule tuple for one site-config entry, or None when the entry is not usable."""
	if not isinstance(entry, str):
		return None
	text = entry.strip()
	if not text or any(c not in _URI_CHARS for c in text) or any(c in text for c in "*@#?%[]"):
		return None
	scheme = "https"
	lowered = text.lower()
	if lowered.startswith("http://"):
		scheme, text = "http", text[len("http://") :]
	elif lowered.startswith("https://"):
		text = text[len("https://") :]
	host, sep, rest = text.partition("/")
	host = host.lower()
	if scheme == "http" and host not in LOOPBACK_HOSTS:
		return None
	if not _valid_host(host):
		return None
	path = ("/" + rest).rstrip("/") if sep and rest.strip("/") else None
	if path is not None and not _valid_path(path):
		return None
	return (scheme, host, path, False)


def configured_rules():
	"""The built-in rules plus the usable entries of ``oauth_registration_redirect_allowlist``."""
	extra = (getattr(frappe.local, "conf", None) or {}).get(ALLOWLIST_KEY) or []
	if isinstance(extra, str):
		extra = [extra]
	rules = list(BUILTIN_RULES)
	if isinstance(extra, (list, tuple)):
		for entry in extra:
			rule = parse_allowlist_entry(entry)
			if rule:
				rules.append(rule)
	return tuple(rules)


def _split_uri(uri):
	"""(scheme, host, port, path) for a well-formed absolute URI, or None."""
	if not isinstance(uri, str) or not uri or len(uri) > MAX_URI_LENGTH:
		return None
	if any(c not in _URI_CHARS for c in uri) or "#" in uri:
		return None
	try:
		parts = urlsplit(uri)
	except ValueError:
		return None
	netloc = parts.netloc
	if not netloc or "@" in netloc or "[" in netloc or "]" in netloc:
		return None
	host, sep, port = netloc.partition(":")
	if sep and not (port.isdigit() and port.isascii()):
		return None
	host = host.lower()
	if not _valid_host(host) or not _valid_path(parts.path):
		return None
	return (parts.scheme.lower(), host, port, parts.path)


def redirect_uri_allowed(uri, rules=BUILTIN_RULES):
	"""True when ``uri`` matches one of ``rules``."""
	split = _split_uri(uri)
	if split is None:
		return False
	scheme, host, port, path = split
	for rule_scheme, rule_host, rule_path, exact in rules:
		if scheme != rule_scheme or host != rule_host:
			continue
		if port and not (scheme == "http" and host in LOOPBACK_HOSTS):
			continue
		if rule_path is None:
			return True
		if exact:
			if path == rule_path:
				return True
		elif path == rule_path or path.startswith(rule_path + "/"):
			return True
	return False


def refused_redirect_uri(data, rules=BUILTIN_RULES):
	"""Why a registration body is refused, as ``(error, description)``, or None when it may proceed."""
	if not isinstance(data, dict):
		return ("invalid_client_metadata", "The request body must be a JSON object.")
	uris = data.get("redirect_uris")
	if not isinstance(uris, list) or not uris:
		return ("invalid_client_metadata", "redirect_uris must be a non-empty list.")
	if len(uris) > MAX_REDIRECT_URIS:
		return ("invalid_client_metadata", "Too many redirect_uris.")
	for uri in uris:
		if not redirect_uri_allowed(uri, rules):
			shown = uri[:200] if isinstance(uri, str) else type(uri).__name__
			return ("invalid_redirect_uri", f"This site does not accept the redirect URI {shown}.")
	return None


# ---------------------------------------------------------------- the rate


def over_rate_limit(ip):
	"""Count one registration request for ``ip``; True once the window's count exceeds the limit.

	``incrby`` is raw redis, so ``make_key`` gives the counter the site's prefix, and the expiry is
	set on the first hit only so a steady trickle cannot keep the window open.
	"""
	key = frappe.cache.make_key(f"ee_oauth_registration:{ip or 'unknown'}")
	count = frappe.cache.incrby(key, 1)
	if count == 1:
		frappe.cache.expire(key, RATE_WINDOW_SECONDS)
	return count > RATE_LIMIT


# ---------------------------------------------------------------- the hook


def _guard_enabled():
	value = (getattr(frappe.local, "conf", None) or {}).get(GUARD_KEY, 1)
	return str(value).strip().lower() not in ("0", "false")


def _request_json():
	request = frappe.local.request
	return request.get_json(silent=True)


def judge():
	"""``None`` to let the request through, or ``(status, error, description)`` to refuse it.

	Only called for a registration request. Fails closed: anything unexpected refuses.
	"""
	try:
		if not _guard_enabled():
			return None
		if over_rate_limit(getattr(frappe.local, "request_ip", None)):
			return (429, "too_many_requests", "Too many registration requests. Try again later.")
		refusal = refused_redirect_uri(_request_json(), configured_rules())
		if refusal:
			return (400, *refusal)
		return None
	except Exception:
		return (400, "invalid_client_metadata", "The registration request could not be read.")


def refusal_exception(status, error, description):
	"""A werkzeug ``HTTPException`` carrying the JSON response frappe will send as it is."""
	from werkzeug.exceptions import BadRequest, TooManyRequests
	from werkzeug.wrappers import Response

	response = Response(
		json.dumps({"error": error, "error_description": description}),
		status=status,
		mimetype="application/json",
	)
	exc_class = TooManyRequests if status == 429 else BadRequest
	return exc_class(response=response)


def guard_registration():
	"""``before_request``: refuse a client registration whose redirect URIs are not allow-listed.

	Every other request returns after a method check and a string test, and never raises; only a
	matching request that is refused raises, with the response frappe sends.
	"""
	try:
		if not _is_registration_request():
			return
	except Exception:
		return
	refusal = judge()
	if not refusal:
		return
	try:
		exc = refusal_exception(*refusal)
	except Exception:
		exc = frappe.PermissionError(refusal[2])
	raise exc
