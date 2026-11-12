"""Certificate of Warranty (one year): the rules the template itself cannot hold.

Nikolas, 2026-11-12: "I need this added to the Contract Builder", attaching Sapphire's
one-year Certificate of Warranty as a Word document. It is a Contract Template like the
others (``template_key`` ``warranty``, ``templates/contracts/certificate_of_warranty.html``,
series ``SF-WAR-YYYY-####``), with three differences this module names in one place:

* **Only Sapphire signs it.** The Word document has one signature block, Sapphire's
  (By / Its / Date). So it never goes to a customer for a signature: no signing link, no
  signing at the table (:data:`ONE_SIDED_KEYS`; ``contract_builder`` and ``esign.api``
  read it). Issuing it with a name in *Signed for Sapphire by* is the signature; with
  that field empty it prints blank lines to sign in ink and is marked signed afterwards,
  as any paper contract is.
* **The term is one year, so the end date is computed, never typed**
  (:func:`warranty_end_date`). The certificate does not print an end date (the Word
  document has none); it is kept on the record so "is this still under warranty?" has an
  answer.
* **The project block wants the address on two lines** (street, then city / state / ZIP),
  and a Project keeps it on one (:func:`site_address_lines`, :func:`address_from_record`).

Pure functions on purpose: no ``frappe`` import, so the date rule and the address rule are
tested without a bench (``tests/test_warranty_certificate.py``).
"""

import calendar
import datetime
import re

TEMPLATE_KEY = "warranty"

#: Contract types only Sapphire signs. The customer is never asked for a signature on one.
ONE_SIDED_KEYS = frozenset({TEMPLATE_KEY})

#: The term the certificate's wording states: "a period of one (1) year".
TERM_YEARS = 1

#: A last address part that is a state and a ZIP ("UT 84010", "Utah 84010-1234").
_STATE_ZIP = re.compile(r"^[A-Za-z][A-Za-z .]*\s+\d{5}(?:-\d{4})?$")


def is_one_sided(template_key):
	"""Whether only Sapphire signs this kind of document."""
	return (template_key or "") in ONE_SIDED_KEYS


def _as_date(value):
	"""``value`` as a ``datetime.date``, or None when it is empty or not a date."""
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return datetime.date.fromisoformat(str(value).strip()[:10])
	except ValueError:
		return None


def warranty_end_date(start, years=TERM_YEARS):
	"""The day the warranty ends: the same day ``years`` later, or None with no start.

	"One (1) year from the date" is the anniversary: work completed 2026-03-15 is covered
	to 2027-03-15. A start on February 29 ends on February 28, the last day of that month
	a year on; nothing else needs a rule.
	"""
	day = _as_date(start)
	if day is None:
		return None
	year = day.year + int(years)
	last = calendar.monthrange(year, day.month)[1]
	return datetime.date(year, day.month, min(day.day, last))


def site_address_lines(text):
	"""A site address as the certificate prints it: street, then city / state / ZIP.

	A Project keeps its address on one line ("12 Example Way, Springfield, UT 84000").
	When that line ends in a state and ZIP, the last two comma parts become the second
	line. Anything already on several lines, or that does not end that way, is returned as
	it is: a wrong guess is worse than one long line the builder lets the person fix.
	"""
	value = (text or "").strip()
	if not value or "\n" in value:
		return value
	parts = [part.strip() for part in value.split(",") if part.strip()]
	if len(parts) >= 3 and _STATE_ZIP.match(parts[-1]):
		return ", ".join(parts[:-2]) + "\n" + ", ".join(parts[-2:])
	return value


def address_from_record(address):
	"""Two lines from an Address record's own fields, or "" when it has no street.

	``address`` is anything with ``get``: the street line(s), then "City, State ZIP".
	"""
	if not address:
		return ""
	get = address.get
	street = ", ".join(
		part for part in ((get("address_line1") or "").strip(), (get("address_line2") or "").strip()) if part
	)
	if not street:
		return ""
	state_zip = " ".join(
		part for part in ((get("state") or "").strip(), (get("pincode") or "").strip()) if part
	)
	city = ", ".join(part for part in ((get("city") or "").strip(), state_zip) if part)
	return street + ("\n" + city if city else "")
