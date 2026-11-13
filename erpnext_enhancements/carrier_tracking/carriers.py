# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Carrier rules with no I/O: which carrier a tracking number belongs to, its public tracking
page, our own status codes, and keeping secrets out of anything that gets stored or logged.

No Frappe and no ``requests`` here, so every rule is tested on plain values
(``tests/test_carrier_tracking_client.py``). The HTTP half is ``client.py``; the payload
normalizers are in ``normalize.py``.

Things this module is careful about:

* **Detection is by format only, and "unknown" is a legal answer.** A tracking number carries
  no reliable carrier mark except UPS's ``1Z``. FedEx numbers are 12, 15 or 22 digits (the 22-
  digit "96" Ground barcode). Twenty- and 22-digit numbers that start ``92``-``95`` are USPS's
  Intelligent Mail format, which FedEx Ground Economy also prints, so they are reported as
  ``unknown`` rather than guessed. Check digits are not verified: a wrong guess here only means
  the carrier answers "not found", while a wrongly rejected real number would never be checked
  at all. **An explicit carrier always wins** over detection.
* **Our status codes are a small fixed set** (``STATUSES``), so the rest of the app never
  branches on a carrier's own vocabulary. The carrier's words travel beside them as
  ``description``.
* **Secrets never reach a stored or logged string.** ``mask`` replaces every known secret value
  and anything shaped like an ``Authorization`` header or a token field. Every error message is
  passed through it before it leaves the client.
"""

import re

FEDEX = "FedEx"
UPS = "UPS"
OTHER = "Other"
UNKNOWN = "Unknown"
AUTO = "Auto"

#: The carriers the client can ask. Anything else is recorded but never polled.
TRACKABLE = (FEDEX, UPS)

#: The choices a person makes when adding a number (Tracked Package.carrier).
CARRIER_CHOICES = (AUTO, FEDEX, UPS, OTHER)

# --------------------------------------------------------------------------- statuses

LABEL_CREATED = "label_created"
IN_TRANSIT = "in_transit"
OUT_FOR_DELIVERY = "out_for_delivery"
DELIVERED = "delivered"
EXCEPTION = "exception"
UNKNOWN_STATUS = "unknown"

#: Our status codes, in the order a package normally moves through them.
STATUSES = (LABEL_CREATED, IN_TRANSIT, OUT_FOR_DELIVERY, DELIVERED, EXCEPTION, UNKNOWN_STATUS)

#: What a person reads (Tracked Package.status options). "Exception" is carrier jargon, so it
#: is called a delivery problem here.
STATUS_LABELS = {
	LABEL_CREATED: "Label Created",
	IN_TRANSIT: "In Transit",
	OUT_FOR_DELIVERY: "Out for Delivery",
	DELIVERED: "Delivered",
	EXCEPTION: "Delivery Problem",
	UNKNOWN_STATUS: "Unknown",
}

#: The two row states that are not a carrier's answer.
NOT_CHECKED = "Not Checked Yet"
NOT_TRACKED = "Not Tracked"

#: Why a number of any other carrier is recorded but never checked.
NOT_TRACKABLE_REASON = "Only FedEx and UPS numbers are checked."

#: Every option of Tracked Package.status, in the JSON's order. A test holds them equal.
STATUS_OPTIONS = (NOT_CHECKED, *[STATUS_LABELS[code] for code in STATUSES], NOT_TRACKED)

# ------------------------------------------------------------------------- detection

_UPS_1Z = re.compile(r"^1Z[0-9A-Z]{16}$")
_DIGITS = re.compile(r"^[0-9]+$")


def clean_number(value):
	"""A tracking number as carriers read it: upper case, no spaces or dashes.

	People paste numbers from emails with spaces ("1Z 999 AA1 ...") and from some suppliers'
	pages with dashes. Anything else that is not a letter or a digit is left in, so the
	validation in ``is_plausible`` can refuse it instead of a cleaned version being tracked.
	"""
	return re.sub(r"[\s\-]+", "", str(value or "")).upper()


def is_plausible(number):
	"""Letters and digits only, 6 to 40 long. Applies to every carrier, Other included."""
	return bool(re.fullmatch(r"[0-9A-Z]{6,40}", number or ""))


def detect_carrier(number):
	"""FedEx, UPS or Unknown from the number's shape alone. See the module docstring."""
	number = clean_number(number)
	if _UPS_1Z.match(number):
		return UPS
	if _DIGITS.match(number):
		length = len(number)
		if length in (12, 15):
			return FEDEX
		if length == 22 and number.startswith("96"):
			return FEDEX
	return UNKNOWN


def resolve_carrier(choice, number):
	"""The carrier to use: an explicit FedEx, UPS or Other wins; Auto (or blank) detects."""
	choice = (choice or AUTO).strip()
	for known in (FEDEX, UPS, OTHER):
		if choice.lower() == known.lower():
			return known
	return detect_carrier(number)


# ------------------------------------------------------------------- public pages

#: The carriers' own public tracking pages, checked 2026-11-13. Both take the number in the query
#: string and need no sign-in. FedEx's older ``/fedextrack/?trknbr=`` answers with a permanent
#: redirect to the ``/wtrk/track/`` form used here.
PUBLIC_TRACKING_URLS = {
	FEDEX: "https://www.fedex.com/wtrk/track/?trknbr={number}",
	UPS: "https://www.ups.com/track?loc=en_US&tracknum={number}",
}


def tracking_url(carrier, number, given_url=None):
	"""The page a person opens to see the package.

	FedEx and UPS: always the carrier's own page for the number. Other and Unknown: the link the
	person gave, if it is an http(s) link, else nothing (a ``javascript:`` link never reaches the
	banner's ``href``).
	"""
	number = clean_number(number)
	pattern = PUBLIC_TRACKING_URLS.get(carrier)
	if pattern and number:
		return pattern.format(number=number)
	url = (given_url or "").strip()
	if re.match(r"^https?://[^\s<>\"']+$", url, re.IGNORECASE):
		return url[:500]
	return ""


# ----------------------------------------------------------------------- secrets

MASK = "[hidden]"

_AUTH_HEADER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]+")
_SECRET_PAIR = re.compile(
	r"(?i)([\"']?(?:access_token|refresh_token|id_token|client_secret|client_id|authorization|password)"
	r"[\"']?\s*[:=]\s*[\"']?)([^\"'&,\s}]+)"
)


def mask(text, secrets=(), limit=500):
	"""``text`` with every secret removed and the length capped. Never raises.

	``secrets`` are the exact values to remove (client id, client secret, an access token). A
	value shorter than four characters is not replaced on its own, since it would shred ordinary
	words; the patterns below still catch it in a header or a ``key=value`` pair.
	"""
	try:
		out = str(text if text is not None else "")
		for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
			if len(secret) >= 4:
				out = out.replace(secret, MASK)
		out = _AUTH_HEADER.sub(lambda m: f"{m.group(1)} {MASK}", out)
		out = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{MASK}", out)
		out = " ".join(out.split())
		return out[:limit]
	except Exception:
		return "(message withheld)"
