"""Getting a product unit to the customer, with no Frappe in it (TASK-2026-03231).

Nikolas, 2026-11-18 (vault ``decisions/2026-11-18-estop-product-line.md``, pick 7): at launch a
product order is picked up at the shop, shipped by a carrier, or delivered by Sapphire. There is no
on-site commissioning add-on.

The Delivery Note records which (``custom_fulfillment_method``). The carrier and the tracking number
go in ERPNext's own fields on it: ``transporter`` (a Supplier) and ``lr_no``, relabeled "Tracking
Number". A Sapphire delivery gets a date and a person. The customer contact is emailed "Ready for
pickup" or "Shipped" (with the tracking link).

``shipping.py`` reads and writes the records; the decisions live here so
``tests/test_product_fulfillment_shipping.py`` holds them without a bench:

* ``method_problems``: what a method needs before the Delivery Note can be submitted;
* ``carrier_for``: which carrier a transporter is, from Product Build Settings' carrier table first,
  else from its name;
* ``tracking_url``: the carrier's public tracking page for a number. FedEx and UPS come from
  ``carrier_tracking.carriers`` (one copy of those URLs in the app); USPS and DHL are added here; any
  other carrier needs a URL template in the carrier table;
* ``email_for``: which customer email a method sends.
"""

import re

from erpnext_enhancements.carrier_tracking import carriers as tracking_carriers

PICKUP = "Pickup at shop"
CARRIER = "Carrier shipping"
SAPPHIRE = "Sapphire delivery"
#: The Select options, in order (the field starts blank).
METHODS = (PICKUP, CARRIER, SAPPHIRE)

UPS = tracking_carriers.UPS
FEDEX = tracking_carriers.FEDEX
USPS = "USPS"
DHL = "DHL"
OTHER = "Other"
#: The carriers the carrier table offers.
CARRIERS = (UPS, FEDEX, USPS, DHL, OTHER)

#: Public tracking pages, ``{number}`` replaced by the cleaned number. FedEx and UPS are
#: ``carrier_tracking``'s own (checked there on 2026-11-13). USPS's Track & Confirm and DHL's tracking
#: page both take the number in the query string and need no sign-in.
TRACKING_URLS = {
	UPS: tracking_carriers.PUBLIC_TRACKING_URLS[UPS],
	FEDEX: tracking_carriers.PUBLIC_TRACKING_URLS[FEDEX],
	USPS: "https://tools.usps.com/go/TrackConfirmAction?tLabels={number}",
	DHL: "https://www.dhl.com/us-en/home/tracking/tracking-express.html?submit=1&tracking-id={number}",
}

# Words in a Supplier's name that say which carrier it is. On prod (2026-11-18) the suppliers are
# "UPS", "UPS store", "FedEx", "FedEx Office", "Usps" and "DHL", none of them marked Is Transporter yet.
_NAME_RULES = (
	(re.compile(r"\bups\b", re.I), UPS),
	(re.compile(r"\bfed\s*ex\b", re.I), FEDEX),
	(re.compile(r"\busps\b|\bpostal service\b|\bpost office\b", re.I), USPS),
	(re.compile(r"\bdhl\b", re.I), DHL),
)


def clean_tracking(number):
	"""A tracking number as carriers read it: upper case, no spaces or dashes."""
	return tracking_carriers.clean_number(number)


def carrier_for(transporter, transporter_name=None, table=()):
	"""``(carrier, url_template)`` for a Delivery Note's transporter.

	``table``: Product Build Settings' carrier rows, dicts with ``transporter`` (the Supplier),
	``carrier`` and ``tracking_url`` (a template with ``{number}``). A row for the Supplier wins; else
	its name decides (``_NAME_RULES``); else Other with no template. A row's template is used only when
	it is an http(s) link with ``{number}`` in it.
	"""
	for row in table or ():
		if transporter and row.get("transporter") == transporter:
			carrier = row.get("carrier") if row.get("carrier") in CARRIERS else OTHER
			return carrier, _template(row.get("tracking_url"))
	name = " ".join(str(v or "") for v in (transporter_name, transporter))
	for pattern, carrier in _NAME_RULES:
		if pattern.search(name):
			return carrier, None
	return (OTHER, None) if (transporter or transporter_name) else (None, None)


def _template(value):
	value = str(value or "").strip()
	if re.match(r"^https?://[^\s<>\"']+$", value, re.I) and "{number}" in value:
		return value
	return None


def tracking_url(carrier, number, template=None):
	"""The public page for a tracking number, or "" when there is none to give.

	A carrier table template wins over the built-in page; Other with no template has none. Only a
	number of letters and digits makes a link: the URL lands unescaped in an email's ``href`` (the
	email macros escape text, never URLs), so anything else in it gets no link at all.
	"""
	number = clean_tracking(number)
	if not re.fullmatch(r"[0-9A-Z]{4,40}", number or ""):
		return ""
	pattern = _template(template) or TRACKING_URLS.get(carrier)
	if not pattern:
		return ""
	return pattern.replace("{number}", number)[:500]


NEEDS_METHOD = "needs_method"
NEEDS_CARRIER = "needs_carrier"
NEEDS_TRACKING = "needs_tracking"
NEEDS_DATE = "needs_date"
NEEDS_PERSON = "needs_person"

PROBLEM_MESSAGES = {
	NEEDS_METHOD: "Choose how it gets to the customer: Pickup at shop, Carrier shipping or Sapphire delivery.",
	NEEDS_CARRIER: "Carrier shipping needs the carrier.",
	NEEDS_TRACKING: "Carrier shipping needs the tracking number.",
	NEEDS_DATE: "Sapphire delivery needs the delivery date.",
	NEEDS_PERSON: "Sapphire delivery needs the person delivering it.",
}


def method_problems(
	method, transporter=None, tracking=None, delivery_date=None, delivery_person=None, required=False
):
	"""What stops this Delivery Note from being submitted, as codes. Empty means go.

	``required``: the note ships a configured product, so it must say how it travels. Any other
	note may leave the method blank, but a method chosen is held to what it needs.
	"""
	method = str(method or "").strip()
	if not method:
		return [NEEDS_METHOD] if required else []
	problems = []
	if method == CARRIER:
		if not str(transporter or "").strip():
			problems.append(NEEDS_CARRIER)
		if not clean_tracking(tracking):
			problems.append(NEEDS_TRACKING)
	elif method == SAPPHIRE:
		if not delivery_date:
			problems.append(NEEDS_DATE)
		if not str(delivery_person or "").strip():
			problems.append(NEEDS_PERSON)
	return problems


READY_FOR_PICKUP = "ready_for_pickup"
SHIPPED = "shipped"


def email_for(method):
	"""Which customer email a method sends: Ready for pickup (sent by a person, from the draft),
	Shipped (sent when the note is submitted), or none (a Sapphire delivery is arranged in person)."""
	return {PICKUP: READY_FOR_PICKUP, CARRIER: SHIPPED}.get(str(method or "").strip())


def first_email(*candidates):
	"""The first candidate that looks like an email address."""
	for value in candidates:
		value = str(value or "").strip()
		if re.match(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$", value):
			return value
	return None
