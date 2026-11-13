# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn one carrier's tracking answer into our shape. Pure functions, no I/O.

Each function takes the part of the response that describes one package and returns
``status``, ``description``, ``estimated_delivery`` (``YYYY-MM-DD``), ``delivered_at``
(``YYYY-MM-DD HH:MM:SS``) and ``last_location``. A field the carrier did not send is ``None`` or
``""``; nothing here raises on a missing or oddly typed key, because a carrier adding or dropping a
field must not stop a package from being tracked.

**Times are kept as the carrier wrote them, in the local time of the place the scan happened.**
Both carriers send local times (FedEx sometimes with a UTC offset, sometimes without; UPS always
without). Converting would need the location's time zone, which neither sends reliably, and the
person reading "Delivered 2:14 PM" means the time on the dock. So the offset, if any, is dropped,
not applied.

The status tables were read from the carriers' published code lists on 2026-11-13:

* FedEx: "Tracking Event Codes for Basic Integrated Visibility API (formerly Track API)", the API
  Reference tables at https://developer.fedex.com/api/en-us/guides/api-reference.html (data file
  https://developer.fedex.com/wirc/json/api_groups/API_Reference_Guide.json). FedEx does not say in
  so many words that these are the values of ``latestStatusDetail.code``; its own examples use them
  that way, and ``derivedCode`` is read first because it is FedEx's own rollup. A code missing from
  the table is ``unknown`` with the carrier's words beside it, never an error.
* UPS: the activity types and the numeric status table in the Tracking API appendix,
  https://developer.ups.com/api/reference/tracking/appendix (``UPS_TYPES``, ``UPS_STATUS_CODES``).
"""

from erpnext_enhancements.carrier_tracking import carriers as C

# ----------------------------------------------------------------------------- FedEx

_FEDEX_LABEL = {"OC", "LC"}
_FEDEX_IN_TRANSIT = {
	# Picked up and moving
	"PU", "DO", "IP", "DS", "PD", "HP", "US", "IT", "AF", "AR", "DP", "TR", "DR", "PM", "MD", "CH",
	"AC", "OX", "IN", "HL",
	# Customs clearance
	"CC", "CP", "CD", "EA",
	# Estimated delivery updates
	"AE", "AO", "DY",
}  # fmt: skip
_FEDEX_OUT = {"OD"}
_FEDEX_DELIVERED = {"DL"}
# Delivery exception, delivery delay, shipment exception, canceled by the sender, and the
# return-to-shipper codes of the webhook table: none of them is a package on its way to us.
_FEDEX_PROBLEM = {"DE", "DD", "SE", "CA", "RS", "RT", "RC", "RR", "RG", "RP", "RD"}


def fedex_status(code):
	"""Our status code for a FedEx event code (``derivedCode`` or ``code``)."""
	code = str(code or "").strip().upper()
	if code in _FEDEX_DELIVERED:
		return C.DELIVERED
	if code in _FEDEX_OUT:
		return C.OUT_FOR_DELIVERY
	if code in _FEDEX_PROBLEM:
		return C.EXCEPTION
	if code in _FEDEX_LABEL:
		return C.LABEL_CREATED
	if code in _FEDEX_IN_TRANSIT:
		return C.IN_TRANSIT
	return C.UNKNOWN_STATUS


#: ``dateAndTimes[].type`` values that are an estimate of arrival, best first.
_FEDEX_ESTIMATE_TYPES = ("ESTIMATED_DELIVERY", "APPOINTMENT_DELIVERY", "COMMITMENT")


def fedex_package(track_result):
	"""Normalize one ``output.completeTrackResults[].trackResults[]`` entry (no ``error`` key)."""
	result = track_result if isinstance(track_result, dict) else {}
	latest = _dict(result.get("latestStatusDetail"))
	code = latest.get("derivedCode") or latest.get("code")
	status = fedex_status(code)
	if status == C.UNKNOWN_STATUS and latest.get("code") and latest.get("code") != code:
		status = fedex_status(latest.get("code"))

	moments = {}
	for item in _list(result.get("dateAndTimes")):
		item = _dict(item)
		kind = str(item.get("type") or "").upper()
		if kind and item.get("dateTime") and kind not in moments:
			moments[kind] = item.get("dateTime")

	delivered_at = _local_datetime(moments.get("ACTUAL_DELIVERY"))

	estimate = None
	window = _dict(_dict(result.get("estimatedDeliveryTimeWindow")).get("window"))
	for candidate in (moments.get("ESTIMATED_DELIVERY"), window.get("ends"), window.get("begins")):
		estimate = _local_date(candidate)
		if estimate:
			break
	if not estimate:
		for kind in _FEDEX_ESTIMATE_TYPES[1:]:
			estimate = _local_date(moments.get(kind))
			if estimate:
				break
	if not estimate:
		standard = _dict(_dict(result.get("standardTransitTimeWindow")).get("window"))
		estimate = _local_date(standard.get("ends"))

	location = _place(latest.get("scanLocation"))
	if not location:
		for event in _list(result.get("scanEvents")):
			location = _place(_dict(event).get("scanLocation"))
			if location:
				break

	description = latest.get("description") or latest.get("statusByLocale") or ""
	return {
		"status": status,
		"description": str(description).strip(),
		"estimated_delivery": None if status == C.DELIVERED else estimate,
		"delivered_at": delivered_at,
		"last_location": location,
	}


def fedex_error_outcome(code):
	"""``not_found``, ``invalid`` or ``None`` for a FedEx error code."""
	code = str(code or "").upper()
	if "NOTFOUND" in code or "NOT.FOUND" in code:
		return "not_found"
	if "INVALID" in code or code.endswith(".EMPTY"):
		return "invalid"
	return None


# ------------------------------------------------------------------------------- UPS

#: UPS ``status.type`` letters, from the "Package Activity Types" tab of the Tracking API appendix,
#: https://developer.ups.com/api/reference/tracking/appendix , read on 2026-11-13. UPS has no
#: letter for "out for delivery": it is an ``I`` (in transit) whose numeric ``statusCode`` says so,
#: which ``UPS_STATUS_CODES`` handles.
UPS_TYPES = {
	"M": C.LABEL_CREATED,  # Billing Information Received: UPS has the label data, not the box
	"MV": C.EXCEPTION,  # Billing Information Voided: the shipper canceled the label
	"I": C.IN_TRANSIT,  # In Transit (and Out for Delivery, see UPS_STATUS_CODES)
	"D": C.DELIVERED,  # Delivered
	"X": C.EXCEPTION,  # Exception
	"DO": C.IN_TRANSIT,  # Delivered Origin CFS (freight only)
	"DD": C.IN_TRANSIT,  # Delivered Destination CFS (freight only)
	"W": C.IN_TRANSIT,  # Warehousing (freight only)
	"NA": C.UNKNOWN_STATUS,  # Not Available
	"U": C.UNKNOWN_STATUS,  # Updates (Track Alert API only)
}

#: UPS's numeric ``statusCode`` (sent zero-padded, "003"), from the Status/Description table of the
#: same appendix. Read before the letter because it is finer: it is the only place "out for
#: delivery" and "returned to sender" are told apart from plain in-transit.
UPS_STATUS_CODES = {
	3: C.LABEL_CREATED,  # Shipment Ready for UPS
	5: C.IN_TRANSIT,  # On the Way
	6: C.OUT_FOR_DELIVERY,  # Out for Delivery
	21: C.OUT_FOR_DELIVERY,  # Out for Delivery
	37: C.OUT_FOR_DELIVERY,  # Out for Delivery
	11: C.DELIVERED,  # Delivered
	13: C.EXCEPTION,  # Exception
	34: C.EXCEPTION,  # Returned to Sender
	38: C.IN_TRANSIT,  # Picked Up by UPS
}


def ups_status(type_code, status_code=None):
	"""Our status code from UPS's numeric ``statusCode`` if we know it, else its ``type`` letter."""
	try:
		number = int(str(status_code).strip())
	except (TypeError, ValueError):
		number = None
	if number in UPS_STATUS_CODES:
		return UPS_STATUS_CODES[number]
	return UPS_TYPES.get(str(type_code or "").strip().upper(), C.UNKNOWN_STATUS)


#: ``deliveryDate[].type`` values: DEL delivered, SDD scheduled, RDD rescheduled.
_UPS_ESTIMATE_TYPES = ("RDD", "SDD")


def ups_package(package):
	"""Normalize one ``trackResponse.shipment[].package[]`` entry."""
	package = package if isinstance(package, dict) else {}
	current = _dict(package.get("currentStatus"))
	activities = [_dict(a) for a in _list(package.get("activity"))]
	newest = activities[0] if activities else {}
	newest_status = _dict(newest.get("status"))

	status = ups_status(current.get("type"), current.get("statusCode"))
	if status == C.UNKNOWN_STATUS and newest_status:
		status = ups_status(newest_status.get("type"), newest_status.get("statusCode"))

	dates = {}
	for item in _list(package.get("deliveryDate")):
		item = _dict(item)
		kind = str(item.get("type") or "").upper()
		if kind and item.get("date") and kind not in dates:
			dates[kind] = item.get("date")

	delivered_at = None
	if status == C.DELIVERED:
		time = _dict(package.get("deliveryTime"))
		delivered_at = _ups_datetime(dates.get("DEL"), time.get("endTime") or time.get("startTime"))
		if not delivered_at:
			for activity in activities:
				activity_status = _dict(activity.get("status"))
				if ups_status(activity_status.get("type"), activity_status.get("statusCode")) == C.DELIVERED:
					delivered_at = _ups_datetime(activity.get("date"), activity.get("time"))
					break

	estimate = None
	if status != C.DELIVERED:
		for kind in _UPS_ESTIMATE_TYPES:
			estimate = _ups_date(dates.get(kind))
			if estimate:
				break

	location = ""
	for activity in activities:
		location = _place(_dict(_dict(activity.get("location")).get("address")))
		if location:
			break

	description = (
		current.get("description")
		or current.get("simplifiedTextDescription")
		or newest_status.get("description")
		or ""
	)
	return {
		"status": status,
		"description": str(description).strip(),
		"estimated_delivery": estimate,
		"delivered_at": delivered_at,
		"last_location": location,
	}


# --------------------------------------------------------------------------- helpers


def _dict(value):
	return value if isinstance(value, dict) else {}


def _list(value):
	return value if isinstance(value, list) else []


def _place(address):
	"""'City, ST, US' from either carrier's address object."""
	address = _dict(address)
	parts = [
		address.get("city"),
		address.get("stateOrProvinceCode") or address.get("stateProvince"),
		address.get("countryCode"),
	]
	return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _local_date(value):
	"""``YYYY-MM-DD`` from an ISO date or date-time, offset ignored; ``None`` if it is not one."""
	text = str(value or "").strip()
	if len(text) >= 10 and text[4] == "-" and text[7] == "-" and text[:4].isdigit():
		return text[:10]
	return None


def _local_datetime(value):
	"""``YYYY-MM-DD HH:MM:SS`` from an ISO date-time, offset dropped (see module docstring)."""
	text = str(value or "").strip()
	day = _local_date(text)
	if not day:
		return None
	clock = text[11:19] if len(text) >= 19 and text[10] in "T " else "00:00:00"
	if not (len(clock) == 8 and clock[2] == ":" and clock[5] == ":"):
		clock = "00:00:00"
	return f"{day} {clock}"


def _ups_date(value):
	"""``YYYY-MM-DD`` from UPS's ``YYYYMMDD``."""
	text = str(value or "").strip()
	if len(text) == 8 and text.isdigit():
		return f"{text[:4]}-{text[4:6]}-{text[6:]}"
	return None


def _ups_datetime(day, clock):
	"""``YYYY-MM-DD HH:MM:SS`` from UPS's ``YYYYMMDD`` and ``HHMMSS``."""
	date = _ups_date(day)
	if not date:
		return None
	clock = str(clock or "").strip()
	if len(clock) == 6 and clock.isdigit():
		return f"{date} {clock[:2]}:{clock[2:4]}:{clock[4:]}"
	return f"{date} 00:00:00"
