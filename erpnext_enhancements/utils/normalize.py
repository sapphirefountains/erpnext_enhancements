# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a phone, email, website, address part or date looks like — stored and shown.

One rule per kind, in one pure module, so the save hook, the cleanup, the dry-run report
(``enhancements_core/report/data_normalization_preview``), print (``print_style``) and the
Desk (``public/js/normalize/normalize.js``, the JavaScript twin) all agree about the same
value. ``docs/data-normalization.md`` is the prose version of this file.

**Frappe-free on purpose.** ``print_style`` imports it, and ``print_style`` is ``exec``-ed
under a stub ``frappe`` by the bench-free print suites; one ``import frappe`` here breaks
them. Standard library only.

**The rules the product owner set (2026-10-13), which everything below serves:**

* A phone is **stored** in E.164 — ``+18015550100`` — and **shown** as ``(801) 555-0100``.
* An extension is **stored** after a comma — ``+18015550100,123``, the dial-pause a phone
  understands — and **shown** as ``(801) 555-0100 x123``. Not ``x``: Frappe v16 validates a
  typed Phone field against ``([0-9\\ \\+\\_\\-\\,\\.\\*\\#\\(\\)]){1,20}$``, which has no
  letters in it and stops at 20 characters, so ``x`` would make the record unsaveable the
  day the field becomes a Phone field.
* Dates show as ``10/14/2026``, date-times as ``10/14/2026 2:00 PM``, a time as ``2:00 PM``.
* **A value that cannot be normalized is left exactly as typed and reported**, never
  guessed and never rejected. That is what :class:`Result` carries: ``ok=False`` means a
  person must look, and ``value`` is then the input, untouched.

**Phones are conservative.** A wrong guess produces a number nobody can dial, which is
worse than a badly formatted one somebody can. Without a ``+`` only North American shapes
are accepted — production holds a Singapore contact stored ``65-688-000-88``, ten digits,
which a digit count alone would turn into ``(656) 880-0088``. ``utils/phone.py`` is a
different tool (the exact-match key for party matching) and keeps its own behaviour.
"""

import datetime as _dt
import re
from typing import NamedTuple


class Result(NamedTuple):
	"""The outcome of normalizing one value.

	Invariants (``tests/test_normalize.py`` holds every case to them):

	* ``ok`` — ``reason`` is ``""``; ``value`` is the normalized value; ``changed`` is
	  ``value != input`` (so trimming outer whitespace alone counts as a change).
	* not ``ok`` — ``value`` is the input exactly as given (not even stripped),
	  ``changed`` is False, and ``reason`` says in plain English why a person must look.

	``None`` counts as the empty string: ``phone(None)`` is ``Result("", False, True, "")``.
	"""

	value: str
	changed: bool
	ok: bool
	reason: str


def _as_text(raw):
	return "" if raw is None else str(raw)


def _ok(raw, value):
	return Result(value, value != _as_text(raw), True, "")


def _fail(raw, reason):
	return Result(_as_text(raw), False, False, reason)


def _digits(text):
	# [0-9], not \d: Python's \d matches every Unicode digit, the JavaScript twin's does not.
	return re.sub(r"[^0-9]", "", text)


# ---------------------------------------------------------------------------
# Phones

#: An extension at the end: ``x123``, ``x 123``, ``ext 123``, ``ext. 123``,
#: ``extension 123``, ``#123``, ``,123``. One to six digits. The lookbehind keeps a word
#: ending in x (``fax``) from reading as an extension marker. A ``,`` or ``;`` may come
#: *before* a word marker too — production holds ``801.566.7272,x5115``.
_EXTENSION = re.compile(
	r"^(?P<main>.*?)\s*(?:,|#|(?:[,;]\s*)?(?<![A-Za-z])(?:extension|ext|x)[.:]?)\s*(?P<ext>[0-9]{1,6})\s*$",
	re.IGNORECASE | re.DOTALL,
)

#: Dashes and spaces that are not ASCII, as a word processor or a phone's autocorrect writes
#: them: the hyphens and dashes U+2010 to U+2015 and the minus sign U+2212, and the no-break,
#: narrow, fixed-width and ideographic spaces. Production holds ``855-444-9477`` typed with
#: U+2011, the non-breaking hyphen. Mapped to ``-`` and a space for *parsing only*: a value
#: that still cannot be normalized comes back exactly as typed.
_UNICODE_DASHES = re.compile("[\u2010-\u2015\u2212]")
_UNICODE_SPACES = re.compile("[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")

#: A ``tel:`` link pasted into the field (``tel:+1(770)277-1120``), with or without ``//``.
_TEL_PREFIX = re.compile(r"^tel:(?://)?", re.IGNORECASE)

#: An extension marker followed by more digits than :data:`_EXTENSION` will peel.
_LONG_EXTENSION = re.compile(r"(?:#|(?<![A-Za-z])(?:extension|ext|x)[.:]?)\s*[0-9]{7,}\s*$", re.IGNORECASE)

#: A North American number as people write it, without a ``+``: optional ``1``, an area
#: code with or without parentheses, then 3 + 4 digits with spaces, dots or dashes between.
#: The same shape ``print_style`` was checked against on every phone-shaped value on
#: production (2026-09-24) — ``(208)-283-2638``, ``786-5361357``, ``888) 885-0228``,
#: ``954. 579-9476`` and ``1800 407 6657`` included.
_NANP_SHAPE = re.compile(r"(?:1[-. ]?)?\(?[0-9]{3}\)?[-. ]*[0-9]{3}[-. ]?[0-9]{4}")

#: What may follow a ``+``: digits and the usual punctuation, nothing else.
_INTL_CHARS = re.compile(r"[0-9\s().\-]+")

#: Frappe v16's Phone fieldtype stops at 20 characters.
PHONE_MAX_LENGTH = 20


def _nanp(raw, national, ext):
	if national[0] in "01":
		return _fail(raw, "area code cannot start with 0 or 1")
	return _stored(raw, "+1" + national, ext)


def _stored(raw, main, ext):
	value = main + ("," + ext if ext else "")
	if len(value) > PHONE_MAX_LENGTH:
		return _fail(raw, "too long to store with its extension (20 characters at most)")
	return _ok(raw, value)


def _two_numbers(main):
	"""Fourteen or more digits with a separator between them: ``801-555-0100 / 801-555-0101``."""
	return len(_digits(main)) >= 14 and bool(re.search(r"[/;&,]|\bor\b|\band\b", main, re.IGNORECASE))


def _not_nanp_reason(main):
	if len(_digits(main)) == 7:
		return "no area code"
	return "not a North American number and no +country code"


def phone(raw):
	"""Normalize a phone number to E.164 (``+18015550100``, ``+18015550100,123``).

	Reads Unicode dashes and spaces as their ASCII forms and drops a leading ``tel:``, then
	peels an extension off the end, then accepts an explicit ``+`` number (``+1``
	must be followed by exactly ten digits; any other country code is kept as ``+`` and its
	8–15 digits) or, without a ``+``, only a North American shape. Everything else is left
	as typed with a reason.
	"""
	text = _UNICODE_SPACES.sub(" ", _UNICODE_DASHES.sub("-", _as_text(raw))).strip()
	text = _TEL_PREFIX.sub("", text).strip()
	if not text:
		return _ok(raw, "")

	main, ext = text, ""
	m = _EXTENSION.match(text)
	if m:
		main, ext = m.group("main").strip(), m.group("ext")
		if not main:
			return _fail(raw, "an extension with no phone number")

	if _two_numbers(main):
		return _fail(raw, "two numbers in one field")
	if _LONG_EXTENSION.search(main):
		return _fail(raw, "an extension longer than six digits")
	if re.search(r"[A-Za-z]", main):
		return _fail(raw, "letters in the number")

	if main.startswith("+"):
		rest = main[1:]
		if "(0)" in rest.replace(" ", ""):
			return _fail(raw, "a (0) trunk prefix inside an international number")
		if not _INTL_CHARS.fullmatch(rest or "x"):
			return _fail(raw, "not a phone number")
		digits = _digits(rest)
		if digits.startswith("1"):
			if len(digits) != 11:
				return _fail(raw, "+1 must be followed by exactly ten digits")
			return _nanp(raw, digits[1:], ext)
		if not 8 <= len(digits) <= 15:
			return _fail(raw, "wrong number of digits for an international number")
		return _stored(raw, "+" + digits, ext)

	if _NANP_SHAPE.fullmatch(main):
		digits = _digits(main)
		if len(digits) == 11 and digits.startswith("1"):
			digits = digits[1:]
		if len(digits) == 10:
			return _nanp(raw, digits, ext)
	return _fail(raw, _not_nanp_reason(main))


def phone_display(value):
	"""A stored or raw phone the way a person reads it: ``(801) 555-0100 x123``.

	North American numbers take the familiar grouping; an international number shows its
	stored ``+<digits>`` form; anything that cannot be normalized comes back unchanged.
	"""
	result = phone(value)
	if not result.ok:
		return _as_text(value)
	if not result.value:
		return ""
	main, _, ext = result.value.partition(",")
	if main.startswith("+1") and len(main) == 12:
		shown = f"({main[2:5]}) {main[5:8]}-{main[8:]}"
	else:
		shown = main
	return shown + (f" x{ext}" if ext else "")


# ---------------------------------------------------------------------------
# Email

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email(raw):
	"""Trim, drop a ``mailto:``, lowercase. Two addresses, or ``Name <a@b.com>``, need a person."""
	text = _as_text(raw).strip()
	if not text:
		return _ok(raw, "")
	if "<" in text or ">" in text:
		return _fail(raw, "a name and an address in one field")
	candidate = re.sub(r"^mailto:", "", text, flags=re.IGNORECASE).strip()
	candidate = re.sub(r"[,;\s]+$", "", candidate)
	parts = [p for p in re.split(r"[,;\s]+", candidate) if p]
	if candidate.count("@") > 1 or sum("@" in p for p in parts) > 1:
		return _fail(raw, "two addresses in one field")
	if len(parts) > 1:
		return _fail(raw, "spaces inside the address")
	value = candidate.lower()
	if not _EMAIL.match(value):
		return _fail(raw, "not an email address")
	return _ok(raw, value)


# ---------------------------------------------------------------------------
# Website

#: Prefixes that mean "this is already a usable reference, leave it alone". ``/`` and ``#``
#: are relative links frappe's ``validate_url`` accepts outright; the two schemes are the
#: ones people paste into a website field by mistake, and prefixing them would produce
#: ``https://mailto:...``. Case-sensitive, exactly as ``website_cleanup`` has always had it.
WEBSITE_EXEMPT_PREFIXES = ("/", "#", "mailto:", "tel:")
WEBSITE_DEFAULT_SCHEME = "https://"

_URL_PARTS = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*)://([^/?#]*)(.*)$", re.DOTALL)


def add_scheme(value):
	"""``value`` with ``https://`` prefixed, or ``None`` when it needs no prefix.

	The rule ``crm_enhancements.website_cleanup.normalize_website`` (and, through it, the
	QuickBooks healer) has applied since v1.36.0, unchanged: anything carrying ``://`` or
	starting with an exempt prefix is left alone, every other non-empty string is prefixed.
	"""
	if not isinstance(value, str):
		return None
	stripped = value.strip()
	if not stripped:
		return None
	if "://" in stripped or stripped.startswith(WEBSITE_EXEMPT_PREFIXES):
		return None
	return WEBSITE_DEFAULT_SCHEME + stripped


def website(raw):
	"""A website with a scheme, and its scheme and host lowercased (never its path or query).

	Stricter than :func:`add_scheme` only in what it refuses to call clean: a value with a
	space in it, an email address, or a "host" with no dot (``N/A``) is reported rather
	than rewritten — the on-save healer still prefixes those so the record stays saveable.
	"""
	text = _as_text(raw).strip()
	if not text:
		return _ok(raw, "")
	if text.startswith(WEBSITE_EXEMPT_PREFIXES):
		return _ok(raw, text)
	if re.search(r"\s", text):
		return _fail(raw, "spaces inside the address")
	if "://" not in text and "@" in text and "/" not in text:
		return _fail(raw, "an email address, not a website")
	with_scheme = add_scheme(text) or text
	m = _URL_PARTS.match(with_scheme)
	if not m:
		return _fail(raw, "not a web address")
	scheme, netloc, rest = m.groups()
	userinfo, at, host = netloc.rpartition("@")
	if "." not in host:
		return _fail(raw, "not a web address")
	return _ok(raw, f"{scheme.lower()}://{userinfo}{at}{host.lower()}{rest}")


# ---------------------------------------------------------------------------
# Text, states, ZIP codes


def text(raw):
	"""Trim, collapse every run of whitespace (tabs, newlines) to one space, drop trailing commas."""
	value = re.sub(r"\s+", " ", _as_text(raw)).strip()
	value = re.sub(r"[\s,]+$", "", value)
	return _ok(raw, value)


_US_COUNTRIES = {"", "united states", "united states of america", "usa", "us", "u.s.", "u.s.a."}


def is_us(country):
	"""True for the United States, by any of its usual spellings, or no country at all."""
	return _as_text(country).strip().lower() in _US_COUNTRIES


#: USPS codes: the states, DC, the territories, and the three military "states".
# fmt: off
US_STATES = {
	"ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
	"COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
	"HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
	"KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
	"MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
	"MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
	"NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
	"NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
	"RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
	"TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
	"WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
	"DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC",
	"PUERTO RICO": "PR", "GUAM": "GU", "AMERICAN SAMOA": "AS",
	"NORTHERN MARIANA ISLANDS": "MP", "VIRGIN ISLANDS": "VI", "US VIRGIN ISLANDS": "VI",
	"ARMED FORCES AMERICAS": "AA", "ARMED FORCES EUROPE": "AE", "ARMED FORCES PACIFIC": "AP",
}
# fmt: on
US_STATE_CODES = frozenset(US_STATES.values())


def state(raw, country=None):
	"""A US state, DC or territory as its two-letter USPS code. Outside the US, :func:`text` only."""
	cleaned = text(raw)
	if not is_us(country) or not cleaned.value:
		return cleaned
	# "Washington, D.C." -> "WASHINGTON DC"; "U.T." -> "UT".
	key = re.sub(r"\s+", " ", cleaned.value.replace(".", "").replace(",", " ")).strip().upper()
	compact = key.replace(" ", "")
	if len(compact) == 2 and compact in US_STATE_CODES:
		return _ok(raw, compact)
	if key in US_STATES:
		return _ok(raw, US_STATES[key])
	return _fail(raw, "not a US state or territory")


_ZIP = re.compile(r"([0-9]{5})(?:[-\s]?([0-9]{4}))?")


def zip_code(raw, country=None):
	"""A US ZIP as ``84010`` or ``84010-1234``. Outside the US, :func:`text` only."""
	cleaned = text(raw)
	if not is_us(country) or not cleaned.value:
		return cleaned
	m = _ZIP.fullmatch(cleaned.value)
	if m:
		return _ok(raw, m.group(1) + ("-" + m.group(2) if m.group(2) else ""))
	if re.fullmatch(r"[0-9]{4}", cleaned.value):
		return _fail(raw, "four digits: a leading zero may have been lost")
	return _fail(raw, "not a US ZIP code")


# ---------------------------------------------------------------------------
# Dates and times — display only. Server strings are site-local, so nothing here
# converts a time zone.

_DATE_STR = re.compile(
	r"^\s*([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[ T]([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?)?\s*$"
)
_TIME_STR = re.compile(r"^\s*(?:-?[0-9]+ days?, )?([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]+)?)?\s*$")


def _parse_date_str(value):
	"""``(date, (hour, minute) or None)`` from an ISO string, or ``None`` when it is not one."""
	m = _DATE_STR.match(value)
	if not m:
		return None
	try:
		day = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
	except ValueError:
		return None
	if m.group(4) is None:
		return day, None
	hour, minute = int(m.group(4)), int(m.group(5))
	if hour > 23 or minute > 59:
		return None
	return day, (hour, minute)


def _date_text(day):
	return f"{day.month:02d}/{day.day:02d}/{day.year:04d}"


def _clock(hour, minute):
	return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def format_date(value):
	"""``10/14/2026`` from a date, a datetime or an ISO string. Anything else comes back as text."""
	if value is None or value == "":
		return ""
	if isinstance(value, _dt.datetime):
		return _date_text(value.date())
	if isinstance(value, _dt.date):
		return _date_text(value)
	parsed = _parse_date_str(str(value))
	return _date_text(parsed[0]) if parsed else str(value)


def format_datetime(value):
	"""``10/14/2026 2:05 PM``. A value with no time part shows as a date alone."""
	if value is None or value == "":
		return ""
	if isinstance(value, _dt.datetime):
		return f"{_date_text(value.date())} {_clock(value.hour, value.minute)}"
	if isinstance(value, _dt.date):
		return _date_text(value)
	parsed = _parse_date_str(str(value))
	if not parsed:
		return str(value)
	day, clock = parsed
	return _date_text(day) + (f" {_clock(*clock)}" if clock else "")


def format_time(value):
	"""``2:05 PM`` from a time, a timedelta (what Frappe returns for a Time field) or a string."""
	if value is None or value == "":
		return ""
	if isinstance(value, _dt.timedelta):
		seconds = int(value.total_seconds()) % 86400
		return _clock(seconds // 3600, (seconds % 3600) // 60)
	if isinstance(value, (_dt.datetime, _dt.time)):
		return _clock(value.hour, value.minute)
	text_value = str(value)
	parsed = _parse_date_str(text_value)
	if parsed:
		return _clock(*parsed[1]) if parsed[1] else text_value
	m = _TIME_STR.match(text_value)
	if not m:
		return text_value
	hour, minute = int(m.group(1)), int(m.group(2))
	if hour > 23 or minute > 59:
		return text_value
	return _clock(hour, minute)


# ---------------------------------------------------------------------------
# Dispatch

KINDS = {"phone": phone, "email": email, "website": website, "text": text, "state": state, "zip": zip_code}

#: The kinds whose rule depends on the record's country (Address.state, Address.pincode).
COUNTRY_KINDS = frozenset({"state", "zip"})


def normalize(kind, raw, country=None):
	"""Normalize ``raw`` as ``kind`` (a key of :data:`KINDS`). An unknown kind raises ValueError."""
	fn = KINDS.get(kind)
	if fn is None:
		raise ValueError(f"unknown normalization kind {kind!r}")
	if kind in COUNTRY_KINDS:
		return fn(raw, country)
	return fn(raw)
