# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Adding a tracking number to an order, and who may. No Frappe, no I/O.

``TrackedPackage.validate`` and ``api/package_tracking.py`` call these; the tests run them on plain
values (``tests/test_carrier_tracking_poller.py``).
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


def new_package_fields(number, choice, link_url=None):
	"""``(fields, problem)`` for a number being added. ``problem`` is a sentence, or ``None``.

	``fields`` are the Tracked Package values the number decides: the cleaned number, the carrier
	choice and the carrier used, the tracking page, and, for a carrier that is not checked, the
	status and why.
	"""
	cleaned = C.clean_number(number)
	if not cleaned:
		return None, "Type a tracking number."
	if not C.is_plausible(cleaned):
		return None, "A tracking number is 6 to 40 letters and digits."
	choice = (choice or C.AUTO).strip() or C.AUTO
	if choice not in C.CARRIER_CHOICES:
		return None, "Pick Auto, FedEx, UPS or Other as the carrier."
	carrier = C.resolve_carrier(choice, cleaned)
	# A UPS number that does not start 1Z is not refused when the person picked UPS: UPS has other
	# formats (Mail Innovations, freight), and an explicit carrier always wins over detection.
	link = (link_url or "").strip()
	url = C.tracking_url(carrier, cleaned, link)
	if link and carrier not in C.TRACKABLE and not url:
		return None, "A tracking link must start with http:// or https://."
	fields = {
		"tracking_number": cleaned,
		"carrier": choice,
		"resolved_carrier": carrier,
		"tracking_url": url,
		"link_url": link if carrier not in C.TRACKABLE else "",
		"status": C.NOT_CHECKED,
		"polling_stopped": 0,
		"stop_reason": "",
	}
	if carrier not in C.TRACKABLE:
		fields.update(
			{
				"status": C.NOT_TRACKED,
				"polling_stopped": 1,
				"stop_reason": UNKNOWN_CARRIER_REASON if carrier == C.UNKNOWN else C.NOT_TRACKABLE_REASON,
			}
		)
	return fields, None
