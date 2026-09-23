"""Plan a Trip — the read and write side of the step-by-step trip page.

The page (``travel_management/page/plan_a_trip``) walks the office through a trip one step
at a time — the trip, the crew, getting there, getting back, where everyone sleeps, getting
around, the schedule — and ends on a checklist of what is still missing
(``completeness.py``). It reads and writes the same Travel Trip the desk form does, and
every save goes through the Travel Trip controller, so nothing here bypasses its rules.

Bookings on the page, one row per person in the database
--------------------------------------------------------
The office books for the crew, so the page thinks in *bookings*: one flight with four people
on it, one room shared by two. The tables store **one row per person**, because each
traveler must see their own booking and nobody else's — their own confirmation number on
their own itinerary, email and calendar invite. ``shape_itinerary`` and the ICS builder
already hide a row pinned to somebody else; per-person rows are what lets them do it.

So reading groups rows into cards by ``booking_group`` (:func:`get_state`), and writing fans
each card back out to one row per person (:func:`merge_bookings`). A row typed on the form
has no ``booking_group`` and becomes a one-person card keyed ``row:<name>``; saving it from
the page gives it a real group id.

What a card write will and will not touch
-----------------------------------------
* **Shared fields** (airline, times, hotel, dates ...) are written in full to a *new* row,
  but to an *existing* row only when the page says it changed them (``changed``). Two rows
  of one booking can disagree after an edit on the form, and the page shows the first row's
  value; writing that back to every row unasked would overwrite the other one silently.
* **The confirmation number is per person** and always written — the page shows each
  person's own value, so there is nothing to guess.
* **The total cost is split evenly** across the booking's rows, and only when the total or
  the set of people changed, so an uneven split typed on the form survives an unrelated
  edit.
* **A row on an Expense Claim, or with a Vehicle Log, is never deleted**, and its traveler
  and money fields are never rewritten: the claim was built from them. The page is told.

A save based on a version somebody else has since replaced is refused, not merged — the
same optimistic lock as ``api/quality_wizard.py``.

The merge functions take plain rows and return plain lists, so ``tests/test_travel_planner.py``
runs them without a site.
"""

import json
import re
import secrets
from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext_enhancements.travel_management import RELATED_PARTY_DOCTYPES, TRAVEL_FOR_DOCTYPES
from erpnext_enhancements.travel_management.completeness import (
	UNBOOKED_TRANSPORT,
	find_gaps,
	group_key,
	to_date,
)

#: Travel Trip header fields the page may write.
TRIP_FIELDS = (
	"purpose",
	"travel_type",
	"company",
	"start_date",
	"end_date",
	"travel_for_doctype",
	"travel_for_name",
	"billable",
	"trip_description",
)

#: Statuses the page may set. The rest are the daily job's (In Progress, Completed) or a
#: deliberate close on the form.
PAGE_STATUSES = ("Planning", "Booked")

#: The per-person booking tables: the fields one card shares across its rows, and the
#: per-person confirmation field.
BOOKING_TABLES = {
	"flights": {
		"shared": (
			"leg",
			"airline",
			"flight_number",
			"departure_airport",
			"departure_time",
			"arrival_airport",
			"arrival_time",
			"billable",
			"paid_by",
			"paid_by_traveler",
		),
		"ref": "booking_reference",
	},
	"accommodations": {
		"shared": (
			"hotel_lodging",
			"check_in_date",
			"check_in_time",
			"check_out_date",
			"check_out_time",
			"billable",
			"paid_by",
			"paid_by_traveler",
		),
		"ref": "booking_confirmation",
	},
	"ground_transport": {
		"shared": (
			"leg",
			"transport_type",
			"supplier",
			"vehicle",
			"pickup_location",
			"dropoff_location",
			"pickup_datetime",
			"arrival_datetime",
			"return_datetime",
			"cargo",
			"billable",
			"paid_by",
			"paid_by_traveler",
		),
		"ref": "booking_reference",
	},
}

#: Never rewritten on a row that is already on an Expense Claim or has a Vehicle Log.
MONEY_FIELDS = frozenset({"cost", "paid_by", "paid_by_traveler", "billable"})

#: Trip Freight fields the page may write. A shipment is ONE row, not one per person —
#: nobody has their own seat on a crate — so freight skips the booking fan-out and is
#: merged like the schedule. ``traveler`` is who receives it (blank = the whole crew).
FREIGHT_FIELDS = (
	"carrier",
	"tracking_number",
	"contents",
	"traveler",
	"ship_from",
	"deliver_to",
	"pickup_from",
	"pickup_to",
	"delivery_from",
	"delivery_to",
	"cost",
	"billable",
	"paid_by",
	"paid_by_traveler",
)

STOP_FIELDS = (
	"date",
	"time",
	"end_time",
	"activity_description",
	"related_party_doctype",
	"related_party_name",
	"location",
)

_GROUP_ID = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


class PlanError(Exception):
	"""A payload the page should never send. Raised by the pure helpers; the endpoint turns
	it into a ``frappe.throw`` with the same message."""


# --------------------------------------------------------------------------- helpers


def _get(row, field):
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def _set(row, field, value):
	if isinstance(row, dict):
		row[field] = value
	else:
		setattr(row, field, value)


def plain(value):
	"""A JSON-safe string for dates, datetimes and Time values (``timedelta`` from the DB)."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		return value.strftime("%Y-%m-%d %H:%M:%S")
	if isinstance(value, date):
		return value.isoformat()
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
	return value


def new_group_id():
	return secrets.token_hex(6)


def normalize_group(value):
	"""The group id to store: the page's own id if it is a safe one, a fresh one otherwise.

	``row:<name>`` (a lone row from the form) and ``new:<n>`` (a card not yet saved) are page
	keys, not ids, and get a fresh id here.
	"""
	value = (value or "").strip()
	if _GROUP_ID.match(value):
		return value
	return new_group_id()


def split_even(total, count):
	"""``total`` in ``count`` two-decimal parts that add back up to it exactly.

	The odd cents go to the first rows, so 100.00 / 3 is 33.34, 33.33, 33.33.
	"""
	if count <= 0:
		return []
	cents = round(flt(total) * 100)
	base, extra = divmod(cents, count)
	return [(base + (1 if i < extra else 0)) / 100 for i in range(count)]


def is_protected(row):
	"""True for a row whose money has left the trip: on an Expense Claim, or logged."""
	return bool(_get(row, "expense_claim") or _get(row, "vehicle_log"))


# --------------------------------------------------------------------------- merging


def merge_bookings(existing_rows, cards, table):
	"""Fan the page's booking cards for ``table`` out to one row per person.

	Args:
		existing_rows: the table's current rows (Documents, dicts or namespaces).
		cards: the page's cards — ``{group, origin, values, changed, members}``; ``group`` has
			already been through :func:`normalize_group` and ``origin`` is the key the page
			loaded the card under (see :func:`prepare_cards`).
		table: a key of :data:`BOOKING_TABLES`.

	Returns:
		(rows, notes): the table's new contents in card order — existing row objects,
		updated in place, and plain dicts for new rows — and messages for the page.
	"""
	spec = BOOKING_TABLES[table]
	by_name = {_get(row, "name"): row for row in existing_rows if _get(row, "name")}
	used = set()
	rows = []
	notes = []

	for card in cards:
		values = card.get("values") or {}
		changed = set(card.get("changed") or [])
		members = [m for m in card.get("members") or [] if m.get("traveler") or m.get("name")]
		if not members:
			raise PlanError(_("Every booking needs at least one person on it."))

		before = {
			_get(row, "name") for row in existing_rows if group_key(row) == card.get("origin")
		}
		card_rows = []
		seen_travelers = set()
		for member in members:
			traveler = member.get("traveler") or None
			if traveler and traveler in seen_travelers:
				continue
			seen_travelers.add(traveler)

			row = by_name.get(member.get("name")) if member.get("name") else None
			if row is not None and _get(row, "name") in used:
				row = None
			is_new = row is None
			if is_new:
				row = {}
			else:
				used.add(_get(row, "name"))
			protected = not is_new and is_protected(row)

			for field in spec["shared"]:
				if field not in values or not (is_new or field in changed):
					continue
				if protected and field in MONEY_FIELDS:
					continue
				_set(row, field, values[field] if values[field] != "" else None)
			if not protected:
				_set(row, "traveler", traveler)
			_set(row, spec["ref"], (member.get("ref") or "").strip() or None)
			_set(row, "booking_group", card["group"])
			card_rows.append((row, is_new, protected))

		after = {_get(row, "name") for row, is_new, _p in card_rows if not is_new}
		membership_changed = any(is_new for _r, is_new, _p in card_rows) or after != before
		if "cost" in values and ("cost" in changed or membership_changed):
			fixed = sum(flt(_get(row, "cost")) for row, _n, protected in card_rows if protected)
			open_rows = [row for row, _n, protected in card_rows if not protected]
			remaining = flt(values.get("cost")) - fixed
			if remaining < 0:
				notes.append(
					_("{0}: the total is less than what is already on an Expense Claim, so nothing more was split.").format(
						card.get("label") or _("A booking")
					)
				)
				remaining = 0
			for row, amount in zip(open_rows, split_even(remaining, len(open_rows)), strict=False):
				_set(row, "cost", amount)

		rows.extend(row for row, _n, _p in card_rows)

	for row in existing_rows:
		name = _get(row, "name")
		if name in used:
			continue
		if is_protected(row):
			rows.append(row)
			notes.append(
				_("Kept a {0} row for {1}: it is already on an Expense Claim or Vehicle Log.").format(
					table.replace("_", " "), _get(row, "traveler") or _("the crew")
				)
			)
	return rows, notes


def merge_mileage(existing_rows, ground_cards, ground_groups_before, trip_start=None):
	"""Keep one Trip Mileage row per Personal Vehicle card: the driver's reimbursable miles.

	Only mileage rows that belong to a Personal Vehicle booking are managed here — rows added
	on the form (no ``booking_group``, or a group that was never a Personal Vehicle card) are
	left exactly as they are.

	Args:
		existing_rows: the trip's current Trip Mileage rows.
		ground_cards: the page's ground-transport cards (already normalized).
		ground_groups_before: group ids of the Personal Vehicle cards before this save, so a
			card deleted on the page takes its mileage row with it.
		trip_start: fallback date for the mileage row when the drive has no pickup time.
	"""
	notes = []
	current = {}
	for card in ground_cards:
		values = card.get("values") or {}
		if values.get("transport_type") != "Personal Vehicle":
			continue
		mileage = card.get("mileage") or {}
		if mileage.get("driver") and flt(mileage.get("distance")) > 0:
			current[card["group"]] = (card, mileage)

	managed = set(ground_groups_before) | set(current)
	rows = []
	done = set()
	for row in existing_rows:
		group = _get(row, "booking_group")
		if not group or group not in managed:
			rows.append(row)
			continue
		if group in done or group not in current:
			if _get(row, "expense_claim"):
				rows.append(row)
				notes.append(_("Kept a mileage row that is already on an Expense Claim."))
			continue
		card, mileage = current[group]
		if not _get(row, "expense_claim"):
			_fill_mileage(row, card, mileage, trip_start)
		rows.append(row)
		done.add(group)

	for group, (card, mileage) in current.items():
		if group in done:
			continue
		row = {"booking_group": group}
		_fill_mileage(row, card, mileage, trip_start)
		rows.append(row)
	return rows, notes


def _fill_mileage(row, card, mileage, trip_start):
	values = card.get("values") or {}
	_set(row, "traveler", mileage.get("driver"))
	_set(row, "date", plain(to_date(values.get("pickup_datetime")) or to_date(trip_start)))
	_set(row, "from_location", values.get("pickup_location") or None)
	_set(row, "to_location", values.get("dropoff_location") or None)
	_set(row, "distance", flt(mileage.get("distance")))


def merge_travelers(existing_rows, travelers):
	"""The crew, in the page's order. Per diem, claim links and reminder stamps are left
	alone: an existing traveler keeps their row, and the page only writes who, when, and
	who leads."""
	by_employee = {_get(row, "employee"): row for row in existing_rows}
	rows = []
	seen = set()
	for traveler in travelers:
		employee = traveler.get("employee")
		if not employee or employee in seen:
			continue
		seen.add(employee)
		row = by_employee.get(employee)
		if row is None:
			row = {"employee": employee}
		_set(row, "is_trip_lead", 1 if cint(traveler.get("is_trip_lead")) else 0)
		_set(row, "from_date", traveler.get("from_date") or None)
		_set(row, "to_date", traveler.get("to_date") or None)
		rows.append(row)
	return rows


def merge_stops(existing_rows, stops):
	"""The schedule. A stop that already produced a Lead or Opportunity is kept even when the
	page drops it — it is that record's provenance."""
	by_name = {_get(row, "name"): row for row in existing_rows if _get(row, "name")}
	used = set()
	rows = []
	notes = []
	for stop in stops:
		row = by_name.get(stop.get("name")) if stop.get("name") else None
		if row is not None and _get(row, "name") in used:
			row = None
		if row is None:
			row = {}
		else:
			used.add(_get(row, "name"))
		for field in STOP_FIELDS:
			if field in stop:
				_set(row, field, stop[field] if stop[field] != "" else None)
		rows.append(row)
	for row in existing_rows:
		if _get(row, "name") not in used and _get(row, "outcome_name"):
			rows.append(row)
			notes.append(
				_("Kept the stop on {0}: a {1} was created from it.").format(
					plain(_get(row, "date")), _get(row, "outcome_doctype") or _("record")
				)
			)
	return rows, notes


def merge_freight(existing_rows, shipments):
	"""The trip's shipments, one row each. A shipment already on an Expense Claim keeps its
	money fields and is kept even when the page drops it — same rule as a booking."""
	by_name = {_get(row, "name"): row for row in existing_rows if _get(row, "name")}
	used = set()
	rows = []
	notes = []
	for shipment in shipments:
		row = by_name.get(shipment.get("name")) if shipment.get("name") else None
		if row is not None and _get(row, "name") in used:
			row = None
		if row is None:
			row = {}
		else:
			used.add(_get(row, "name"))
		protected = is_protected(row)
		for field in FREIGHT_FIELDS:
			if field not in shipment or (protected and field in MONEY_FIELDS):
				continue
			value = shipment[field]
			_set(row, field, flt(value) if field == "cost" else (value if value != "" else None))
		rows.append(row)
	for row in existing_rows:
		if _get(row, "name") not in used and is_protected(row):
			rows.append(row)
			notes.append(_("Kept a freight shipment that is already on an Expense Claim."))
	return rows, notes


def poi_geolocation(lat, lng):
	"""The GeoJSON a Travel POI stores, in the shape travel_poi.js writes and
	``api/travel.py _poi_latlng`` reads. ``None`` without a usable point."""
	try:
		lat, lng = float(lat), float(lng)
	except (TypeError, ValueError):
		return None
	if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
		return None
	return json.dumps(
		{
			"type": "FeatureCollection",
			"features": [
				{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [lng, lat]}}
			],
		}
	)


def prepare_cards(cards):
	"""Give every card a storable group id, remembering the key it was loaded under."""
	prepared = []
	for card in cards or []:
		card = dict(card)
		card["origin"] = card.get("group")
		card["group"] = normalize_group(card.get("group"))
		prepared.append(card)
	return prepared


# --------------------------------------------------------------------------- reading


def _cards(doc, table, mileage_by_group):
	spec = BOOKING_TABLES[table]
	groups = {}
	for row in doc.get(table) or []:
		groups.setdefault(group_key(row), []).append(row)

	cards = []
	for key, rows in groups.items():
		first = rows[0]
		card = {
			"group": key,
			"values": {field: plain(_get(first, field)) for field in spec["shared"]},
			"members": [
				{
					"name": row.name,
					"traveler": row.traveler or "",
					"ref": _get(row, spec["ref"]) or "",
					"protected": is_protected(row),
				}
				for row in rows
			],
			"protected": any(is_protected(row) for row in rows),
		}
		card["values"]["cost"] = sum(flt(row.cost) for row in rows)
		card["values"]["billable"] = cint(_get(first, "billable"))
		if table == "accommodations":
			# Read-only: fetched from the hotel Supplier's primary address on save.
			card["address"] = _get(first, "address") or ""
		if table == "ground_transport" and first.transport_type == "Personal Vehicle":
			mileage = mileage_by_group.get(key)
			card["mileage"] = (
				{
					"driver": mileage.traveler,
					"distance": flt(mileage.distance),
					"claimed": bool(mileage.expense_claim),
				}
				if mileage
				else {"driver": "", "distance": 0}
			)
		cards.append(card)
	return cards


def get_state(doc):
	"""The whole trip in the page's shape, plus its checklist."""
	mileage_by_group = {}
	for row in doc.get("mileage") or []:
		if row.booking_group and row.booking_group not in mileage_by_group:
			mileage_by_group[row.booking_group] = row

	# A stop's Place is a Travel POI, named by hash; the page shows its title.
	poi_ids = sorted({row.location for row in doc.get("itinerary") or [] if row.location})
	poi_titles = (
		{
			p.name: p.poi_name
			for p in frappe.get_all(
				"Travel POI", filters={"name": ["in", poi_ids]}, fields=["name", "poi_name"]
			)
		}
		if poi_ids
		else {}
	)

	return {
		"name": doc.name if not doc.is_new() else None,
		"modified": str(doc.modified) if doc.modified else None,
		"status": doc.status,
		"trip": {field: plain(doc.get(field)) for field in TRIP_FIELDS},
		"travelers": [
			{
				"name": row.name,
				"employee": row.employee,
				"employee_name": row.employee_name or row.employee,
				"is_trip_lead": cint(row.is_trip_lead),
				"from_date": plain(row.from_date),
				"to_date": plain(row.to_date),
			}
			for row in doc.get("travelers") or []
		],
		"bookings": {table: _cards(doc, table, mileage_by_group) for table in BOOKING_TABLES},
		"freight": [
			dict(
				{field: plain(_get(row, field)) for field in FREIGHT_FIELDS},
				name=row.name,
				cost=flt(row.cost),
				billable=cint(row.billable),
				traveler=row.traveler or "",
				protected=is_protected(row),
			)
			for row in doc.get("freight") or []
		],
		"stops": [
			{
				"name": row.name,
				"date": plain(row.date),
				"time": plain(row.time),
				"end_time": plain(_get(row, "end_time")),
				"activity_description": row.activity_description,
				"related_party_doctype": row.related_party_doctype,
				"related_party_name": row.related_party_name,
				"location": row.location,
				"location_title": poi_titles.get(row.location) or row.location or "",
				"outcome_name": row.outcome_name,
			}
			for row in doc.get("itinerary") or []
		],
		"gaps": find_gaps(doc),
	}


def _default_company(companies=None):
	if companies is None:
		companies = frappe.get_all("Company", pluck="name", order_by="name asc")
	return (
		frappe.defaults.get_user_default("Company")
		or frappe.defaults.get_global_default("company")
		or (companies[0] if companies else None)
	)


def _lookups():
	meta = frappe.get_meta("Travel Trip")
	companies = frappe.get_all("Company", pluck="name", order_by="name asc")
	default_company = _default_company(companies)
	settings = frappe.get_cached_doc("Travel Settings")
	return {
		# get_all, not get_list: a crew member's Employee user permission would otherwise
		# show them only themselves. Names and titles only, and only to someone allowed to
		# plan a trip — the same names the form's Traveler link already offers them.
		"employees": frappe.get_all(
			"Employee",
			filters={"status": "Active"},
			fields=["name", "employee_name", "designation"],
			order_by="employee_name asc",
		),
		"companies": companies,
		"default_company": default_company,
		"currency": frappe.db.get_value("Company", default_company, "default_currency")
		if default_company
		else None,
		"travel_types": [o for o in (meta.get_field("travel_type").options or "").split("\n") if o],
		"travel_for_doctypes": list(TRAVEL_FOR_DOCTYPES),
		"related_party_doctypes": list(RELATED_PARTY_DOCTYPES),
		"mileage_rate": flt(settings.mileage_rate),
		"unbooked_transport": sorted(UNBOOKED_TRANSPORT),
	}


def _check_not_stale(doc, modified):
	if modified and str(doc.modified) != str(modified):
		frappe.throw(
			_("This trip was changed somewhere else since you opened it. Reload to see the latest, then carry on."),
			title=_("Trip out of date"),
		)


# --------------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_plan(trip=None):
	"""Bootstrap the page: the trip (if one is named) and the pick-lists it needs."""
	if trip:
		doc = frappe.get_doc("Travel Trip", trip)
		doc.check_permission("read")
		state = get_state(doc)
		state["can_write"] = bool(doc.has_permission("write"))
	else:
		if not frappe.has_permission("Travel Trip", "create"):
			frappe.throw(_("You are not allowed to plan trips."), frappe.PermissionError)
		state = None
	return {"state": state, "lookups": _lookups()}


@frappe.whitelist()
def get_recent_plans():
	"""The landing list: trips this user can see that are still ahead or under way."""
	return frappe.get_list(
		"Travel Trip",
		filters={"status": ["in", ["Planning", "Booked", "In Progress"]]},
		fields=["name", "purpose", "status", "travel_type", "start_date", "end_date"],
		order_by="start_date asc",
		limit_page_length=30,
	)


@frappe.whitelist(methods=["POST"])
def place_to_poi(label, address=None, latitude=None, longitude=None):
	"""The Travel POI for a place chosen on the page's Schedule step — found, or made.

	A stop's Place links to a Travel POI, and the page's Place box searches Google (places
	and addresses, through the same component the Address form uses). A pick becomes a
	POI: one with exactly this name is reused, so picking "The Home Depot, South Power
	Road, Mesa" on two trips does not make two, and a new one is created with the point
	Google returned, which the trip map and ``/itinerary`` plot without a geocode. A name
	typed without picking anything arrives here too, with no point.

	The name is Google's full suggestion text, not the bare business name: there are
	dozens of "The Home Depot"s, and a lookup by that alone would reuse the wrong one.
	"""
	if not (label or "").strip():
		frappe.throw(_("Give the place a name."))
	return find_or_create_poi(label, address, latitude, longitude)


def find_or_create_poi(label, address=None, latitude=None, longitude=None):
	label = (label or "").strip()[:140]
	# order_by explicitly: a bare get_value returns the NEWEST match.
	existing = frappe.db.get_value("Travel POI", {"poi_name": label}, "name", order_by="creation asc")
	if existing:
		return {"name": existing, "poi_name": label}
	poi = frappe.get_doc(
		{
			"doctype": "Travel POI",
			"poi_name": label,
			"notes": (address or "").strip() or None,
			"geolocation": poi_geolocation(latitude, longitude),
		}
	)
	poi.insert()
	return {"name": poi.name, "poi_name": poi.poi_name}


@frappe.whitelist(methods=["POST"])
def save_plan(plan, trip=None, modified=None):
	"""Create or update a Travel Trip from the page's state and return the fresh state.

	Args:
		plan: JSON — ``{trip, status, travelers, bookings: {table: [card]}, stops}``. Every
			key is optional except on a first save, which needs the trip and the crew.
		trip: the Travel Trip to update; omitted on the first save.
		modified: the ``modified`` the page loaded. A mismatch is refused.
	"""
	plan = frappe.parse_json(plan) or {}
	if trip:
		doc = frappe.get_doc("Travel Trip", trip)
		doc.check_permission("write")
		_check_not_stale(doc, modified)
	else:
		doc = frappe.new_doc("Travel Trip")

	try:
		notes = apply_plan(doc, plan)
	except PlanError as exc:
		frappe.throw(str(exc))

	if doc.is_new():
		doc.insert()
	else:
		doc.save()

	state = get_state(doc)
	state["can_write"] = True
	state["notes"] = notes
	return state


def apply_plan(doc, plan):
	"""Write the page's state onto ``doc`` (unsaved). Returns notes for the page."""
	notes = []
	trip = plan.get("trip") or {}
	for field in TRIP_FIELDS:
		if field not in trip:
			continue
		value = trip[field]
		if field == "billable":
			value = 1 if cint(value) else 0
		elif value == "":
			value = None
		doc.set(field, value)
	if not doc.company:
		doc.company = _default_company()

	status = plan.get("status")
	if status in PAGE_STATUSES:
		doc.status = status

	if "travelers" in plan:
		_replace_table(doc, "travelers", merge_travelers(doc.get("travelers") or [], plan["travelers"]))

	crew = {row.employee for row in doc.get("travelers") or []}
	bookings = plan.get("bookings") or {}
	ground_before = {
		group_key(row)
		for row in doc.get("ground_transport") or []
		if row.transport_type == "Personal Vehicle"
	}
	for table in BOOKING_TABLES:
		if table not in bookings:
			continue
		cards = prepare_cards(bookings[table])
		for card in cards:
			for member in card.get("members") or []:
				if member.get("traveler") and member["traveler"] not in crew:
					frappe.throw(
						_("{0} is on a booking but is not in the crew. Add them on the Crew step first.").format(
							member["traveler"]
						)
					)
		rows, table_notes = merge_bookings(doc.get(table) or [], cards, table)
		_replace_table(doc, table, rows)
		notes.extend(table_notes)
		if table == "ground_transport":
			mileage, mileage_notes = merge_mileage(
				doc.get("mileage") or [], cards, ground_before, doc.start_date
			)
			_replace_table(doc, "mileage", mileage)
			notes.extend(mileage_notes)

	if "freight" in plan:
		for shipment in plan["freight"]:
			if shipment.get("traveler") and shipment["traveler"] not in crew:
				frappe.throw(
					_("{0} receives a shipment but is not in the crew. Add them on the Crew step first.").format(
						shipment["traveler"]
					)
				)
		rows, freight_notes = merge_freight(doc.get("freight") or [], plan["freight"])
		_replace_table(doc, "freight", rows)
		notes.extend(freight_notes)

	if "stops" in plan:
		# A place typed on the Schedule step without picking a Google suggestion still
		# has to become a Travel POI: the stop's Place is a Link.
		for stop in plan["stops"]:
			text = (stop.get("location_text") or "").strip()
			if text and not stop.get("location"):
				stop["location"] = find_or_create_poi(text)["name"]
		rows, stop_notes = merge_stops(doc.get("itinerary") or [], plan["stops"])
		_replace_table(doc, "itinerary", rows)
		notes.extend(stop_notes)
	return notes


def _replace_table(doc, fieldname, rows):
	doc.set(fieldname, [])
	for row in rows:
		doc.append(fieldname, row)
	for idx, row in enumerate(doc.get(fieldname), 1):
		row.idx = idx
