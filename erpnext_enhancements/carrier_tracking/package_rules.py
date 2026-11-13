# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Adding a tracking number to an order, which orders may take one, and who may. No Frappe, no I/O.

``TrackedPackage.validate``, ``api/package_tracking.py`` and ``purchase_order_hooks.py`` call these;
the tests run them on plain values (``tests/test_carrier_tracking_poller.py``).
"""

from erpnext_enhancements.carrier_tracking import carriers as C

#: Who may add or remove a tracking number: purchasing, the warehouse, and System Manager.
#: The rule is Tracked Package's own create and delete permission (its JSON, held equal to this
#: set by a test) plus read on the order. It never needs write or submit on the Purchase Order,
#: so it widens nobody's rights over the order itself. External Contractor reads orders and is
#: deliberately not here.
MANAGE_ROLES = frozenset(
	{"System Manager", "PO Creator", "Purchase User", "Purchase Manager", "Stock User", "Stock Manager"}
)

UNKNOWN_CARRIER_REASON = (
	"Could not tell the carrier from this number. Remove it and add it again with FedEx or UPS "
	"picked, or pick Other."
)

#: Purchase Order statuses after which no package is on its way any more. "Delivered" is ERPNext's
#: word for an order the supplier delivered straight to the customer (drop-ship).
CLOSED_ORDER_STATUSES = ("Closed", "Completed", "Cancelled", "Delivered")

#: Every field the carrier (through ``tracking.apply``) owns, with the value a new row starts at.
#: ``TrackedPackage.validate`` resets all of them on insert, so a REST create cannot arrive already
#: "Delivered", with a forged estimate, or with a future ``last_checked`` that the poller would skip.
CARRIER_FIELD_DEFAULTS = {
	"status": C.NOT_CHECKED,
	"status_description": "",
	"estimated_delivery": None,
	"delivered_on": None,
	"last_location": "",
	"destination": "",
	"last_checked": None,
	"last_success": None,
	"last_change": None,
	"first_refused_on": None,
	"failure_count": 0,
	"last_error": "",
	"polling_stopped": 0,
	"stop_reason": "",
}


def order_accepts_packages(docstatus, status, per_received):
	"""Whether an order can still have a package on its way, so a number may be added to it.

	A draft or a submitted order that is not closed, completed, canceled, drop-shipped or fully
	received. The same test decides whether the banner offers *Add tracking number*.
	"""
	try:
		docstatus = int(docstatus or 0)
		received = float(per_received or 0)
	except (TypeError, ValueError):
		return False
	if docstatus not in (0, 1):
		return False
	if (status or "") in CLOSED_ORDER_STATUSES:
		return False
	return received < 100


def new_package_fields(number, choice, link_url=None):
	"""``(fields, problem)`` for a number being added. ``problem`` is a sentence, or ``None``.

	``fields`` are every Tracked Package value the number decides, including every carrier-written
	field at its starting value (``CARRIER_FIELD_DEFAULTS``): the cleaned number, the carrier choice,
	the carrier used and whether that was a guess, the tracking page, and, for a carrier that is not
	checked, the status and why.
	"""
	cleaned = C.clean_number(number)
	if not cleaned:
		return None, "Type a tracking number."
	choice = (choice or C.AUTO).strip() or C.AUTO
	if choice not in C.CARRIER_CHOICES:
		return None, "Pick Auto, FedEx, UPS or Other as the carrier."
	carrier, guessed = C.resolve(choice, cleaned)
	if not C.is_plausible(cleaned, carrier):
		low, high = C.LENGTHS.get(carrier, C.OTHER_LENGTH)
		return (
			None,
			f"A {carrier if carrier in C.TRACKABLE else 'tracking'} number is {low} to {high} letters and digits.",
		)
	# A UPS number that does not start 1Z is not refused when the person picked UPS: UPS has other
	# formats (Mail Innovations, freight), and an explicit carrier always wins over detection.
	link = (link_url or "").strip()
	url = C.tracking_url(carrier, cleaned, link)
	if link and carrier not in C.TRACKABLE and not url:
		return None, "A tracking link must start with http:// or https://."
	fields = dict(CARRIER_FIELD_DEFAULTS)
	fields.update(
		{
			"tracking_number": cleaned,
			"carrier": choice,
			"resolved_carrier": carrier,
			"carrier_guessed": 1 if guessed else 0,
			"tracking_url": url,
			"link_url": link if carrier not in C.TRACKABLE else "",
		}
	)
	if carrier not in C.TRACKABLE:
		fields.update(
			{
				"status": C.NOT_TRACKED,
				"polling_stopped": 1,
				"stop_reason": UNKNOWN_CARRIER_REASON if carrier == C.UNKNOWN else C.NOT_TRACKABLE_REASON,
			}
		)
	return fields, None
