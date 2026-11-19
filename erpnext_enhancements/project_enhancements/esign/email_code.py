# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The emailed one-time code that opens signing on ``/contract-sign``.

Nik, 2026-11-19: "Emailed code. When they start signing, email a short code to the
address on file and ask them to type it." The endpoints that send and check it are in
:mod:`.portal`; this module is the code itself and the limits around it:

* six digits from :mod:`secrets`, valid for :data:`CODE_TTL` seconds (10 minutes);
* at most :data:`MAX_TRIES` entries per code, after which the code is spent;
* at most :data:`MAX_SENDS` codes per signing request in :data:`SEND_WINDOW` (15 minutes);
* kept only as a keyed hash in the cache, under the request's name and bound to the
  link it was issued for, never the code itself;
* compared in constant time, and single use: a right code is claimed atomically and
  deleted, so a second use of it fails the same way a wrong one does.

Sending a new code replaces the previous one. The cache is flushed on every deploy, so a
code can vanish before it is used: that costs the signer one "send a new code", nothing
more.

No ``frappe.db`` here and no request objects: callers pass the request's name and its
current ``token_hash``, which keeps this testable over a stand-in cache.
"""

import hashlib
import hmac
import json
import re
import secrets
import time

import frappe

CODE_DIGITS = 6
CODE_TTL = 10 * 60
MAX_TRIES = 5
MAX_SENDS = 3
SEND_WINDOW = 15 * 60

_PREFIX = "csign:code:"
_CODE_RE = re.compile(rf"^[0-9]{{{CODE_DIGITS}}}$")
# Spaces and dashes a person or a phone's autofill puts inside a code ("123 456").
_SEPARATORS_RE = re.compile(r"[\s\-]")


def _now():
	return time.time()


def new_code():
	"""A fresh code: ``CODE_DIGITS`` digits, uniformly from the OS's cryptographic source."""
	return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def clean(value):
	"""The code as typed, without separators, or "" when it is not ``CODE_DIGITS`` digits."""
	text = _SEPARATORS_RE.sub("", str(value or ""))
	return text if _CODE_RE.match(text) else ""


def send_allowed(request_name):
	"""Count one send for this request; False once ``MAX_SENDS`` were made in the window."""
	return _bump(f"{_PREFIX}sends:{request_name}", SEND_WINDOW) <= MAX_SENDS


def issue(request_name, token_hash):
	"""Mint a code for this request's live link, store its keyed hash, return the plaintext.

	The plaintext goes into the email and nowhere else. Replaces any earlier code.
	"""
	code = new_code()
	salt = secrets.token_hex(8)
	record = {
		"salt": salt,
		"digest": _digest(request_name, salt, code),
		"token_hash": token_hash or "",
		"expires_at": _now() + CODE_TTL,
	}
	frappe.cache.set_value(_key(request_name), json.dumps(record), expires_in_sec=CODE_TTL)
	return code


def check(request_name, token_hash, typed):
	"""True only for the right, unexpired, unused code issued for this request's live link.

	Every other outcome (no code, a code for a link since replaced, expired, too many
	tries, already used, wrong) is the same False, so the caller shows one message.
	"""
	typed = clean(typed)
	if not typed:
		return False
	record = _load(request_name)
	if not record:
		return False
	if not token_hash or record.get("token_hash") != token_hash:
		return False
	if _now() > float(record.get("expires_at") or 0):
		_discard(request_name)
		return False

	salt = str(record.get("salt") or "")
	# Counted before the comparison, atomically, so concurrent entries cannot share a try.
	tries = _bump(f"{_PREFIX}tries:{request_name}:{salt}", CODE_TTL)
	if tries > MAX_TRIES:
		_discard(request_name)
		return False

	expected = str(record.get("digest") or "").encode("utf-8")
	if not hmac.compare_digest(expected, _digest(request_name, salt, typed).encode("utf-8")):
		if tries >= MAX_TRIES:
			_discard(request_name)
		return False

	# Single use: only the first request to claim this code wins, even when two arrive at once.
	if _bump(f"{_PREFIX}used:{request_name}:{salt}", CODE_TTL) != 1:
		return False
	_discard(request_name)
	return True


def mask_email(email):
	"""``jane@example.com`` -> ``j•••@example.com``: the first character and the domain.

	A fixed run of dots, so the mask does not give away the length of the address.
	"""
	email = (email or "").strip()
	if "@" not in email:
		return ""
	local, _, domain = email.rpartition("@")
	return f"{local[:1]}•••@{domain}"


def mask_address_in(html, email):
	"""``html`` with every occurrence of ``email`` (any case, raw or HTML-escaped) masked.

	For the agreement shown before the code is entered: the document prints the client's
	address in its party block, and the page should not show it in full until the code
	confirms who is reading.
	"""
	email = (email or "").strip()
	if not html or "@" not in email:
		return html or ""
	masked = mask_email(email)
	escaped = (
		email.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace('"', "&quot;")
		.replace("'", "&apos;")
	)
	# The apostrophe has three spellings in rendered HTML: frappe's escape_html, Jinja's, and
	# the decimal form.
	spellings = {email, escaped, escaped.replace("&apos;", "&#x27;"), escaped.replace("&apos;", "&#39;")}
	variants = sorted(spellings, key=len, reverse=True)
	pattern = re.compile("|".join(re.escape(v) for v in variants), re.IGNORECASE)
	return pattern.sub(masked, html)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _key(request_name):
	return f"{_PREFIX}{request_name}"


def _digest(request_name, salt, code):
	"""HMAC-SHA256 of the code, keyed with the site's encryption key and bound to the request.

	Six digits are a million possibilities, so a plain hash would be no better than the code;
	the key means a copy of the cache alone does not give the code back.
	"""
	conf = getattr(frappe.local, "conf", None) or {}
	key = str(conf.get("encryption_key") or "") + ":contract-sign-code"
	message = f"{request_name}:{salt}:{code}"
	return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _load(request_name):
	try:
		raw = frappe.cache.get_value(_key(request_name))
		record = json.loads(raw) if isinstance(raw, str) else raw
		return record if isinstance(record, dict) else None
	except Exception:
		return None


def _discard(request_name):
	try:
		frappe.cache.delete_value(_key(request_name))
	except Exception:
		pass


def _bump(key, ttl):
	"""Atomically increment a per-site counter and return its new value (see ``portal._bump_counter``)."""
	namespaced = frappe.cache.make_key(key)
	count = frappe.cache.incrby(namespaced, 1)
	if count == 1:
		frappe.cache.expire(namespaced, ttl)
	return count
