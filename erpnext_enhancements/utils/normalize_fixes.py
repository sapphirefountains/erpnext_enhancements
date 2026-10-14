# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Proposed fixes for the values the normalization rules cannot read (form kit P2-C, part two).

``utils/normalize.py`` stores a value the one way only when it is sure; everything else is left
exactly as typed and listed as "Can't normalize — needs a person" by the Data Normalization
Preview report (84 values on prod after the 2026-10-13 cleanup). Nik chose (2026-10-13) to have
a fix *proposed* for each, reviewed, and applied only where he approves. This module proposes;
the report shows the proposals next to the values, and a System Manager ticks the rows to apply
(``normalize_cleanup.apply_fixes``). Nothing here writes.

**Rules, not records.** The repository is public, so no record's value or name lives here: each
rule is generic and runs against the live value inside the report. A rule proposes only when it
is sure in its own terms, and returns ``None`` otherwise — "needs a person" stays the answer for
a typo, a truncated number or a foreign number whose country cannot be told.

What it proposes:

* **Only spaces** in a phone, email or website field → blank. Never in a name or an address part:
  those can be mandatory, and a blank mandatory field makes the record unsaveable (the reason the
  cleanup itself never blanks).
* **A placeholder phone** (``123-456-7890``, ``098-765-4321``, ``(120) 120-1200``, one digit
  repeated) → blank.
* **An international phone typed without its ``+``** (``44 161 834 9478``, ``(39) 348 230
  4636``) → the ``+`` number, and a ``(0)`` trunk prefix inside one is dropped. Only when the
  number *opens* with a real country calling code set apart from the rest, and the result is a
  valid number for that country (Google's ``phonenumbers``, which Frappe v16 itself depends on).
  A run of digits with no separator (``518215009``) or a leading ``0`` (``(055) 830-2269``, a
  trunk prefix whose country is unknown) is never guessed. Without ``phonenumbers`` this rule
  proposes nothing.
* **One phone number inside text** (``X385-266-6938``, ``(801) 663-5382 (Wes)``) → that number;
  a trailing ``ext``/``x``/``PIN`` and its digits become the extension, which the dialer sends
  after connecting.
* **A vanity number** (``1-855-493-HOGS``) → the keypad digits, when the result is a valid North
  American number; a bracketed restatement of the digits (``(5433)``) is dropped first.
* **An email address in a phone field** → moved to that record's matching email field when that
  is empty, or simply cleared when the email field already holds the same address.
* **A website in an email field** → moved to the record's website field the same way.
* **Text with no @ in an email field** → on a Contact, a job title with no digits moves to an
  empty Designation; anything else (an address, a note) is cleared.
* **Text with no digits in a phone field** (a name, "Email only") → cleared.
* **A four-digit US ZIP** whose state's ZIPs begin with 0 (New England, New Jersey, Puerto
  Rico) → the leading zero put back, only when the padded ZIP falls in that state's range.

Clearing loses the text from the record (the undo file keeps it), which is why every proposal
is applied only when a person ticks it.
"""

import re
from collections import namedtuple

from erpnext_enhancements.utils import normalize

#: ``value`` is the field's new value; ``also`` is ``{fieldname: value}`` written beside it on
#: the same record (a value moved to the field it belongs in); ``why`` is shown to the reviewer.
Fix = namedtuple("Fix", "value also why")

#: The kinds a value of only spaces may be blanked in: never mandatory on this site.
BLANKABLE_KINDS = frozenset({"phone", "email", "website"})

#: The digits of numbers people type as placeholders. Not anyone's number.
PLACEHOLDER_DIGITS = frozenset({"1234567890", "0987654321", "1201201200"})

#: Where an email found in a phone field belongs, per (doctype, phone field).
EMAIL_FOR_PHONE = {
	("Customer", "custom_accounts_phone_number"): "custom_accounts_email_address",
	("Customer", "custom_primary_phone"): "custom_primary_email_address",
	("Lead", "custom_account_phone_number"): "custom_account_email",
	("Lead", "custom_contact_phone_number"): "custom_contact_email",
	("Supplier", "custom_phone_number"): "custom_email_address",
}

#: Where a website found in an email field belongs, per doctype.
WEBSITE_FIELD = {"Customer": "website", "Lead": "website", "Opportunity": "website", "Supplier": "website"}

#: Where a job title found in an email field belongs, per doctype.
TITLE_FIELD = {"Contact": "designation"}

#: Columns a proposal reads that the normalization registry does not list. The report fetches
#: them with the record, when the site has them.
EXTRA_COLUMNS = {"Contact": ("designation",)}

#: The first three digits of each state's ZIPs, for the states whose ZIPs begin with 0.
ZERO_ZIP_RANGES = {
	"PR": (6, 9),
	"VI": (8, 8),
	"MA": (10, 27),
	"RI": (28, 29),
	"NH": (30, 38),
	"ME": (39, 49),
	"VT": (50, 59),
	"CT": (60, 69),
	"NJ": (70, 89),
}

_KEYPAD = {
	letter: digit
	for digit, letters in {
		"2": "ABC",
		"3": "DEF",
		"4": "GHI",
		"5": "JKL",
		"6": "MNO",
		"7": "PQRS",
		"8": "TUV",
		"9": "WXYZ",
	}.items()
	for letter in letters
}

#: One North American number inside other text, with or without a leading 1.
_EMBEDDED_NANP = re.compile(r"(?<![0-9])(?:1[-. ]?)?\(?[2-9][0-9]{2}\)?[-. ]*[0-9]{3}[-. ]?[0-9]{4}(?![0-9])")

#: An extension, or a conference PIN, right after the number.
_TRAILING_EXTENSION = re.compile(r"^\s*(?:extension|ext|x|pin)[.:]?\s*([0-9]{1,6})\b", re.IGNORECASE)

#: An extension at the very end of a vanity number.
_END_EXTENSION = re.compile(r"\s*(?:extension|ext|x)[.:]?\s*([0-9]{1,6})\s*$", re.IGNORECASE)

#: A vanity number opens with an area code (or 1 and an area code) in digits.
_VANITY_OPENING = re.compile(r"^\s*(?:1[-. ]?)?\(?[2-9][0-9]{2}\)?[-. ]")

#: A bracketed group of digits: a restatement of the letters, as in ``-LIFE (5433)``.
_BRACKETED_DIGITS = re.compile(r"\(\s*[0-9][0-9 .\-]*\)")

#: A country calling code opening the number and set apart from the rest: ``44 161…``,
#: ``(39) 348…``, ``49(203)…``, ``+ 44 20…``. It never starts with 0.
_COUNTRY_OPENING = re.compile(r"^\s*\+?\s*\(?\s*([1-9][0-9]{0,2})\s*\)?[\s\-.(]")

#: A ``(0)`` trunk prefix, which an international number never dials.
_TRUNK_ZERO = re.compile(r"\(\s*0\s*\)")

_WEBSITE_SHAPE = re.compile(r"^(?:https?://|www\.)\S+$|^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.IGNORECASE)


def _digits(text):
	return re.sub(r"[^0-9]", "", text)


def _blank(value):
	return not (value or "").strip()


def _phonenumbers():
	try:
		import phonenumbers

		return phonenumbers
	except Exception:
		return None


def propose(kind, raw, record=None, doctype=None, fieldname=None):
	"""A :class:`Fix` for a value the rules cannot read, or ``None`` when only a person can tell.

	``record`` is the rest of the record as a mapping (its registered fields and
	:data:`EXTRA_COLUMNS`), so a move can check that the field it moves to is empty.
	"""
	if raw is None:
		return None
	text = str(raw)
	if kind not in BLANKABLE_KINDS and kind != "zip":
		return None
	if not text.strip():
		return Fix("", {}, "only spaces") if kind in BLANKABLE_KINDS else None
	record = record or {}
	if kind == "phone":
		return _phone(text, record, doctype, fieldname)
	if kind == "email":
		return _email(text, record, doctype)
	if kind == "zip":
		return _zip(text, record)
	return None


def _move(record, target, value, moved_why, duplicate_why, same):
	"""Clear this field and put ``value`` in ``target`` when that is empty or already holds it."""
	if not target:
		return None
	current = record.get(target)
	if _blank(current):
		return Fix("", {target: value}, moved_why)
	if same(current):
		return Fix("", {}, duplicate_why)
	return None


# ---------------------------------------------------------------------------
# Phones


def _phone(text, record, doctype, fieldname):
	digits = _digits(text)
	national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
	if national in PLACEHOLDER_DIGITS or (len(digits) >= 7 and len(set(digits)) == 1):
		return Fix("", {}, "a placeholder number, not a real one")

	if "@" in text:
		result = normalize.email(text)
		if not result.ok or not result.value:
			return None
		return _move(
			record,
			EMAIL_FOR_PHONE.get((doctype, fieldname)),
			result.value,
			"an email address in a phone field: moved to the email field",
			"an email address in a phone field, already in the email field: cleared",
			lambda current: normalize.email(current).value == result.value,
		)

	for rule in (_international, _embedded, _vanity):
		fix = rule(text)
		if fix:
			return fix

	if not digits:
		return Fix("", {}, "no digits: a name or a note, not a phone number")
	return None


def _international(text):
	without_trunk = _TRUNK_ZERO.sub(" ", text)
	opening = _COUNTRY_OPENING.match(without_trunk)
	if not opening:
		return None
	country_code = int(opening.group(1))
	if country_code == 1:
		return None
	pn = _phonenumbers()
	if pn is None:
		return None
	try:
		if pn.region_code_for_country_code(country_code) in (None, "ZZ"):
			return None
		candidate = "+" + without_trunk.strip().lstrip("+").strip()
		if not pn.is_valid_number(pn.parse(candidate, None)):
			return None
	except Exception:
		return None
	result = normalize.phone(candidate)
	if not result.ok or not result.value:
		return None
	reasons = []
	if not text.lstrip().startswith("+"):
		reasons.append("an international number typed without its +")
	if without_trunk != text:
		reasons.append("the (0) trunk prefix dropped")
	return Fix(result.value, {}, "; ".join(reasons))


def _embedded(text):
	if not re.search(r"[A-Za-z]", text):
		return None
	found = list(_EMBEDDED_NANP.finditer(text))
	if len(found) != 1:
		return None
	number = found[0]
	extension = _TRAILING_EXTENSION.match(text[number.end() :])
	candidate = number.group(0) + (f" x{extension.group(1)}" if extension else "")
	result = normalize.phone(candidate)
	if not result.ok or not result.value:
		return None
	why = "the one phone number in the text kept; the rest dropped"
	if extension:
		why = "the one phone number in the text kept, with its extension or PIN; the rest dropped"
	return Fix(result.value, {}, why)


def _vanity(text):
	main, extension = text, ""
	end = _END_EXTENSION.search(text)
	if end:
		main, extension = text[: end.start()], end.group(1)
	main = _BRACKETED_DIGITS.sub(" ", main)
	letters = "".join(re.findall(r"[A-Za-z]", main))
	if not letters or len(letters) > 7 or not _VANITY_OPENING.match(main):
		return None
	mapped = "".join(_KEYPAD.get(char.upper(), char) for char in main)
	mapped = re.sub(r"[\s.\-]+", "-", mapped).strip("-")
	result = normalize.phone(mapped + (f" x{extension}" if extension else ""))
	if not result.ok or not result.value.startswith("+1"):
		return None
	return Fix(result.value, {}, "a vanity number: its letters turned into keypad digits")


# ---------------------------------------------------------------------------
# Emails


def _email(text, record, doctype):
	stripped = text.strip()
	if "@" in stripped:
		return None  # an address missing its .com, or two in one: only a person can tell
	if _WEBSITE_SHAPE.match(stripped):
		site = normalize.website(stripped)
		if site.ok and site.value:
			return _move(
				record,
				WEBSITE_FIELD.get(doctype),
				site.value,
				"a website in an email field: moved to the website field",
				"a website in an email field, already in the website field: cleared",
				lambda current: normalize.website(current).value.rstrip("/").lower()
				== site.value.rstrip("/").lower(),
			)
	title_field = TITLE_FIELD.get(doctype)
	if title_field and not _digits(stripped) and len(stripped) <= 80:
		return _move(
			record,
			title_field,
			stripped,
			"a job title in an email field: moved to Designation",
			"a job title in an email field, already in Designation: cleared",
			lambda current: current.strip().lower() == stripped.lower(),
		)
	return Fix("", {}, "not an email address (a note or an address): cleared")


# ---------------------------------------------------------------------------
# ZIPs


def _zip(text, record):
	stripped = text.strip()
	if not re.fullmatch(r"[0-9]{4}", stripped) or not normalize.is_us(record.get("country")):
		return None
	state = normalize.state(record.get("state") or "", record.get("country")).value
	bounds = ZERO_ZIP_RANGES.get(state)
	padded = "0" + stripped
	if not bounds or not bounds[0] <= int(padded[:3]) <= bounds[1]:
		return None
	return Fix(padded, {}, f"a ZIP that lost its leading zero ({state} ZIPs start with 0)")
