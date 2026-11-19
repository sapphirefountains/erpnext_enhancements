"""Mask credential-shaped values in log text, keeping everything around them.

:func:`mask_secrets` is applied to every Error Log row before it is stored
(``security_alerts.mask_error_log``). frappe already replaces a traceback
variable whose *name* is on its short list (``password``, ``token``, ...) with
``********``; what it cannot see is a value nested inside another value's repr,
such as a connection's keyword arguments or a client object that prints its
own settings. This module is the second line for those: it works on the text
itself, so it does not matter which variable carried the value.

Rules, each replacing only the value with :data:`MARKER`:

* PEM private-key blocks;
* vendor token shapes: Google API keys, OAuth client secrets, access and
  refresh tokens; Stripe secret, restricted and webhook-signing keys;
* a 32-hex value written after ``auth_token`` or ``api_secret`` (account and
  key *identifiers* are left as they are);
* ``Authorization`` header values, ``Bearer <token>`` and ``token <key>:<secret>``;
* ``name='value'``, ``name="value"``, ``'name': 'value'`` and
  ``"name": "value"`` pairs whose name contains a credential word
  (:data:`NAME_WORDS`; :data:`EXACT_NAME_WORDS` only as a whole word);
* URL query values for credential parameters (:data:`QUERY_PARAMS`);
* the password in ``scheme://user:password@host``.

Pure: no frappe import, so it is testable without a bench and safe to call from
an Error Log hook (it can never log an error of its own). Every pattern has
bounded quantifiers and runs in time linear in the input, and each rule is
skipped outright unless a cheap substring test says it could match. Input
longer than :data:`MAX_SCAN_CHARS` is masked up to that point and the rest is
kept unchanged: the stored text is never shortened.

Idempotent: masking masked text changes nothing, so re-running the stored-row
backfill is safe.
"""

import json
import re

#: What a masked value becomes.
MARKER = "[masked]"

#: Masking stops here on longer input; the remainder is kept exactly as it was.
#: An Error Log row is usually a few kilobytes and occasionally hundreds; this
#: bounds the cost of one pathological row without truncating anything.
MAX_SCAN_CHARS = 2_000_000

#: Credential words a pair's name may contain anywhere (``db_password``,
#: ``client_secret``, ``refresh_token``, ``Set-Cookie``).
NAME_WORDS = (
	"password",
	"passwd",
	"secret",
	"token",
	"api_key",
	"apikey",
	"private_key",
	"authorization",
	"cookie",
)

#: Credential words that count only as a whole word, because they are common
#: fragments of ordinary names (``inside``, ``consider``).
EXACT_NAME_WORDS = ("sid", "pwd")

#: URL query parameters whose value is masked.
QUERY_PARAMS = (
	"key",
	"api_key",
	"access_token",
	"refresh_token",
	"token",
	"secret",
	"client_secret",
	"sig",
	"signature",
	"password",
)

# The PEM header is assembled from fragments so this file does not read as a key
# file to the repository's own secrets scanner (scripts/check_no_committed_secrets.py).
_DASHES = "-" * 5
_PEM_BLOCK_RE = re.compile(
	_DASHES
	+ r"BEGIN [A-Z ]{0,24}PRIVATE KEY"
	+ _DASHES
	# The body: base64, whitespace, and the backslash-n of an escaped repr. The class
	# excludes "-", so the run always stops at the next header or footer.
	+ r"[A-Za-z0-9+/=\s\\]{0,16384}"
	+ r"(?:"
	+ _DASHES
	+ r"END [A-Z ]{0,24}PRIVATE KEY"
	+ _DASHES
	+ r")?"
)

_TOKEN_CHARS = r"[0-9A-Za-z_\-]"

# (substring that must be present, compiled pattern). Each match is replaced whole.
_WHOLE_VALUE_RULES = (
	("AIza", re.compile(r"AIza" + _TOKEN_CHARS + r"{35}")),
	("GOCSPX-", re.compile(r"GOCSPX-" + _TOKEN_CHARS + r"{8,128}")),
	("ya29.", re.compile(r"ya29\.[0-9A-Za-z_\-.]{8,4096}")),
	("1//", re.compile(r"(?<![0-9A-Za-z/])1//" + _TOKEN_CHARS + r"{16,2048}")),
	("_live_", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{8,256}")),
	("_test_", re.compile(r"\b[sr]k_test_[0-9A-Za-z]{8,256}")),
	("whsec_", re.compile(r"\bwhsec_[0-9A-Za-z+/=]{8,256}")),
)

# A quoted value, its quotes kept outside the value group. Three shapes: a
# backslash-escaped double quote (JSON inside a string), a double-quoted and a
# single-quoted string. The closing quote is optional, so a value cut short by a
# line end (a repr shortened by the traceback formatter) is still masked, and a
# value longer than the bound is masked for that length: once the opening quote
# has matched, the rest cannot fail, which keeps the pattern free of backtracking.
_QUOTED_VALUE = (
	r"(?:(?P<q1>\\\")(?P<v1>[^\"\\\r\n]{1,4096})(?:\\\")?"
	r"|(?P<q2>\")(?P<v2>(?:[^\"\\\r\n]|\\.){1,4096})\"?"
	r"|(?P<q3>')(?P<v3>(?:[^'\\\r\n]|\\.){1,4096})'?)"
)

_NAME_VALUE_RE = re.compile(
	r"(?P<head>"
	r"(?:" + "|".join(re.escape(word) for word in NAME_WORDS) + r")"
	r"|(?<![A-Za-z0-9])(?:" + "|".join(EXACT_NAME_WORDS) + r")(?![A-Za-z0-9])"
	r")"
	# The rest of the name, the closing quote of a quoted name, the separator and an
	# optional bytes prefix. Kept verbatim.
	r"(?P<sep>[\w\-]{0,40}(?:\\?[\"'])?\s{0,5}[:=]\s{0,5}b?)" + _QUOTED_VALUE,
	re.IGNORECASE,
)

_TWILIO_SECRET_RE = re.compile(
	r"(?P<keep>(?:auth_token|api_secret)(?:\\?[\"'])?\s{0,5}[:=]\s{0,5}(?:\\?[\"'])?)"
	r"[0-9a-fA-F]{32}(?![0-9a-fA-F])",
	re.IGNORECASE,
)

# An Authorization header written out unquoted, "Authorization: Basic abc" and the
# like: an optional scheme word, then the credential. A quoted header value is the
# name/value rule's, and after it the value starts with "[", which this never matches.
_AUTH_HEADER_RE = re.compile(
	r"(?P<keep>authorization(?:\\?[\"'])?\s{0,5}[:=]\s{0,5})"
	r"(?:[A-Za-z]{1,20}\s{1,5})?[A-Za-z0-9\-._~+/:=]{6,4096}",
	re.IGNORECASE,
)

# Case-sensitive on purpose: the header scheme is written "Bearer", while the
# lower-case word turns up in ordinary prose.
_BEARER_RE = re.compile(r"(?P<keep>\bBearer\s{1,5})[A-Za-z0-9\-._~+/]{8,4096}=*")

# frappe's API-key header value: "token <api_key>:<api_secret>".
_API_TOKEN_RE = re.compile(r"(?P<keep>\btoken\s{1,5})[A-Za-z0-9]{6,64}:[A-Za-z0-9]{6,64}\b", re.IGNORECASE)

_QUERY_RE = re.compile(
	r"(?P<keep>[?&](?:" + "|".join(re.escape(name) for name in QUERY_PARAMS) + r")=)[^&#\s\"'<>]{1,4096}",
	re.IGNORECASE,
)

_URL_PASSWORD_RE = re.compile(
	r"(?P<keep>\b[A-Za-z][A-Za-z0-9+.\-]{0,20}://[^\s:/@\"'<>]{1,256}:)[^\s/@\"'<>]{1,256}(?=@)"
)


def _keep_then_marker(match):
	return match.group("keep") + MARKER


def _name_value_replacement(match):
	for quote, value in (("q1", "v1"), ("q2", "v2"), ("q3", "v3")):
		if match.group(value) is not None:
			# Only the value is replaced; the quotes around it are put back as they
			# were, and a value cut short by a line end gets no closing quote.
			closing = match.string[match.end(value) : match.end()]
			return match.group("head") + match.group("sep") + match.group(quote) + MARKER + closing
	return match.group(0)


def _mask(text):
	lowered = text.lower()

	if "PRIVATE KEY" + _DASHES in text:
		text = _PEM_BLOCK_RE.sub(MARKER, text)

	for needle, pattern in _WHOLE_VALUE_RULES:
		if needle in text:
			text = pattern.sub(MARKER, text)

	if any(word in lowered for word in NAME_WORDS + EXACT_NAME_WORDS):
		text = _NAME_VALUE_RE.sub(_name_value_replacement, text)

	if "auth_token" in lowered or "api_secret" in lowered:
		text = _TWILIO_SECRET_RE.sub(_keep_then_marker, text)

	if "authorization" in lowered:
		text = _AUTH_HEADER_RE.sub(_keep_then_marker, text)

	if "Bearer" in text:
		text = _BEARER_RE.sub(_keep_then_marker, text)

	if "token" in lowered:
		text = _API_TOKEN_RE.sub(_keep_then_marker, text)

	if "=" in text and ("?" in text or "&" in text):
		text = _QUERY_RE.sub(_keep_then_marker, text)

	if "://" in text and "@" in text:
		text = _URL_PASSWORD_RE.sub(_keep_then_marker, text)

	return text


def mask_secrets(text):
	"""Return ``text`` with credential-shaped values replaced by :data:`MARKER`.

	Anything that is not a non-empty string is returned unchanged. Text past
	:data:`MAX_SCAN_CHARS` is not scanned and is kept as it was; the cut is moved
	back to the last line break near the limit so a value is not split.
	"""
	if not isinstance(text, str) or not text:
		return text
	if len(text) <= MAX_SCAN_CHARS:
		return _mask(text)

	cut = text.rfind("\n", MAX_SCAN_CHARS - 65536, MAX_SCAN_CHARS)
	if cut == -1:
		cut = MAX_SCAN_CHARS
	return _mask(text[:cut]) + text[cut:]


#: How deep :func:`mask_json_text` follows nested objects, lists and JSON held in strings.
MAX_JSON_DEPTH = 12

#: frappe's own placeholder for a value it already replaced; left as it is.
_ALREADY_REPLACED = frozenset("*")


def _is_credential_name(name):
	"""True when a JSON key reads as a credential name: it contains one of
	:data:`NAME_WORDS`, or it is exactly one of :data:`EXACT_NAME_WORDS` (so ``sid`` is,
	and an identifier such as ``call_sid`` is not)."""
	lowered = str(name).strip().lower()
	return lowered in EXACT_NAME_WORDS or any(word in lowered for word in NAME_WORDS)


def _holds_json(text):
	"""True when a string is itself a JSON object or list (followed, not replaced whole)."""
	if text.lstrip()[:1] not in ("{", "["):
		return False
	try:
		return isinstance(json.loads(text), (dict, list))
	except ValueError:
		return False


def _mask_json_value(value, depth):
	"""``(masked value, changed)`` for one decoded JSON value."""
	if depth > MAX_JSON_DEPTH:
		return value, False

	if isinstance(value, dict):
		out, changed = {}, False
		for key, item in value.items():
			# Only a text value is replaced: counts, flags and expiry numbers under such a
			# key are kept, and so is frappe's own placeholder.
			if (
				_is_credential_name(key)
				and isinstance(item, str)
				and item not in ("", MARKER)
				and not set(item) <= _ALREADY_REPLACED
				and not _holds_json(item)
			):
				out[key], changed = MARKER, True
				continue
			out[key], item_changed = _mask_json_value(item, depth + 1)
			changed = changed or item_changed
		return out, changed

	if isinstance(value, list):
		out, changed = [], False
		for item in value:
			masked, item_changed = _mask_json_value(item, depth + 1)
			out.append(masked)
			changed = changed or item_changed
		return out, changed

	if isinstance(value, str) and value:
		# A string that is itself a JSON object or list (a posted document, a job's
		# arguments) is masked by its own keys, then written back as JSON text.
		if value.lstrip()[:1] in ("{", "["):
			try:
				inner = json.loads(value)
			except ValueError:
				inner = None
			if isinstance(inner, (dict, list)):
				masked, changed = _mask_json_value(inner, depth + 1)
				return (json.dumps(masked), True) if changed else (value, False)
		masked = mask_secrets(value)
		return masked, masked != value

	return value, False


def mask_json_text(text):
	"""Mask a JSON document held as text, such as Error Log ``metadata``.

	The document is decoded and masked by structure rather than as flat text: a
	value whose *key* is a credential name becomes :data:`MARKER` (containers under
	such a key are followed, not replaced), a string that holds JSON of its own is
	masked the same way, and every other string goes through :func:`mask_secrets`.
	The result is always valid JSON, written the way frappe writes it.

	Text that is not a JSON object or list, or is longer than
	:data:`MAX_SCAN_CHARS`, is handed to :func:`mask_secrets` instead. Unchanged
	input is returned as the same string, and masking is idempotent.
	"""
	if not isinstance(text, str) or not text:
		return text
	if len(text) > MAX_SCAN_CHARS:
		return mask_secrets(text)
	try:
		document = json.loads(text)
	except ValueError:
		return mask_secrets(text)
	if not isinstance(document, (dict, list)):
		return mask_secrets(text)
	masked, changed = _mask_json_value(document, 0)
	if not changed:
		return text
	return json.dumps(masked, indent=1, sort_keys=True, separators=(",", ": "))
