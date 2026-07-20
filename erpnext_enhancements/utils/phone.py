# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phone-number normalisation shared across matching code.

Extracted so the fountain-move intake matcher and any future party-resolution
code agree on what "the same number" means, instead of each re-deriving it.

**This is deliberately NOT the algorithm ``api.telephony._get_caller_info`` uses.**
That function builds a fuzzy regex (``".*".join(digits)``) which matches a
10-digit suffix scattered anywhere across the stored value. That is the right
trade for "who might be calling?" — a wrong guess costs a mislabelled ringing
screen, and a near miss is worse than a loose match. It is the wrong trade for
"which account do we write to?", where a false positive silently merges two
customers' records. Here we normalise both sides and compare exactly.

Do not call ``_get_caller_info`` from conversion code for the same reason: it
creates a Customer and commits as a side effect.
"""

import re


def normalize_phone(raw):
	"""Reduce a phone number to its comparable digits.

	Strips every non-digit, drops a leading NANP country code, and keeps the
	last 10 digits. Returns ``""`` for empty input and passes short/foreign
	numbers through as bare digits so they can still compare equal to each
	other without ever colliding with a 10-digit NANP number.

	    >>> normalize_phone("(801) 555-1212")
	    '8015551212'
	    >>> normalize_phone("+1 801-555-1212")
	    '8015551212'
	    >>> normalize_phone("555-1212")
	    '5551212'

	Note the deliberate limitation: an extension glued onto the number
	("801-555-1212 x4" → 11 digits not starting with 1) slides the 10-digit
	window and yields a value that matches nothing. That is the safe failure —
	no match beats a wrong match. Callers gate on :func:`is_nanp` before
	trusting a phone comparison.
	"""
	digits = re.sub(r"\D", "", raw or "")
	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]
	return digits[-10:] if len(digits) >= 10 else digits


def is_nanp(raw):
	"""True when ``raw`` normalises to a full 10-digit North American number.

	Phone matching is only trusted at this length — a 4-digit extension or a
	partially-typed number would otherwise match far too much.
	"""
	return len(normalize_phone(raw)) == 10


def format_nanp(raw):
	"""Render a NANP number as ``(801) 555-1212``; return the input unchanged otherwise.

	Display only — never store the formatted form, and never compare against it.
	"""
	digits = normalize_phone(raw)
	if len(digits) != 10:
		return (raw or "").strip()
	return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
