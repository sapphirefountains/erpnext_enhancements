# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Server side of the Rental Planner desk page (``page/rental_planner``), v1.563.0.

Two endpoints:

* :func:`get_timeline` — the fleet timeline: one row per rentable fountain with every
  calendar entry on it (the Rental Booking legs, anything booked by hand, out-of-service
  periods), and one row per accessory pool with how many units are out each day.
* :func:`create_rental` — the wizard's last step: a Rental Booking from what the person picked,
  and optionally the Events Project it belongs to, in one transaction. If the booking is refused
  (someone took the fountain in the meantime) the Project is rolled back with it, so a failed
  booking never leaves an orphan Project behind.

Availability itself is not re-implemented here: the wizard's fountain step calls
``rental_availability.get_availability`` and ``plan_package``, and the booking's own save is the
authority (row locks and all).
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate

from erpnext_enhancements.asset_management import rental_availability as availability
from erpnext_enhancements.asset_management import rental_rules as rules

#: The widest window the timeline draws, in days. A quarter and a bit: wider than that and a
#: day is a few pixels, and the pool rows' per-day peaks become the slow part.
MAX_TIMELINE_DAYS = 100

#: Rental Booking fields the wizard may set. Anything else in the payload is ignored, so a
#: crafted request cannot set status history, confirmation stamps or totals.
BOOKING_FIELDS = (
	"customer",
	"contact_person",
	"project",
	"opportunity",
	"quotation",
	"rental_package",
	"event_name",
	"venue_address",
	"venue_notes",
	"delivery_datetime",
	"setup_datetime",
	"event_start_datetime",
	"event_end_datetime",
	"takedown_datetime",
	"schedule_notes",
	"delivery_setup_fee",
	"pickup_removal_fee",
	"other_fee",
	"security_deposit",
	"notes",
)

EVENTS = "Events"


def _bar_kind(row):
	if row.rental_booking:
		return {rules.LEG_PREP: "prep", rules.LEG_TURNAROUND: "turnaround"}.get(row.rental_leg, "rental")
	return "booking"


@frappe.whitelist()
def get_timeline(start, days=31):
	"""Every rentable fountain and accessory pool across ``days`` days from ``start``."""
	frappe.has_permission("Rental Booking", "read", throw=True)
	days = min(max(cint(days) or 31, 1), MAX_TIMELINE_DAYS)
	window_from = get_datetime(getdate(start))
	window_to = window_from + datetime.timedelta(days=days)

	profiles = availability.fountain_profiles(rentable_only=True)
	fountains = {
		name: {
			"asset": name,
			"asset_name": p.asset_name,
			"item_code": p.item_code,
			"turnaround_hours": p.turnaround_hours,
			"bars": [],
		}
		for name, p in profiles.items()
	}

	if fountains:
		params = {"assets": tuple(fountains), "from": window_from, "to": window_to}
		for row in frappe.db.sql(
			"""
			select ab.name, ab.asset, ab.booking_type, ab.rental_leg, ab.docstatus,
				ab.from_datetime, ab.to_datetime, ab.rental_booking,
				rb.customer_name, rb.status, rb.event_name
			from `tabAsset Booking` ab
			left join `tabRental Booking` rb on rb.name = ab.rental_booking
			where ab.asset in %(assets)s
				and ab.docstatus < 2
				and ab.from_datetime < %(to)s
				and ab.to_datetime > %(from)s
			order by ab.from_datetime
			""",
			params,
			as_dict=True,
		):
			kind = _bar_kind(row)
			if row.rental_booking:
				label = row.customer_name or row.rental_booking
				if row.event_name:
					label = f"{label}: {row.event_name}"
				link = {"doctype": "Rental Booking", "name": row.rental_booking}
			else:
				label = _(row.booking_type)
				link = {"doctype": "Asset Booking", "name": row.name}
			fountains[row.asset]["bars"].append(
				{
					"kind": kind,
					"held": row.docstatus == 0,
					"status": row.status or "",
					"label": label,
					"from": str(row.from_datetime),
					"to": str(row.to_datetime),
					**link,
				}
			)

		for row in frappe.db.sql(
			"""
			select name, asset, reason, out_from, blocked_until, status
			from `tabAsset Out of Service`
			where asset in %(assets)s
				and out_from < %(to)s
				and (blocked_until is null or blocked_until > %(from)s)
			""",
			params,
			as_dict=True,
		):
			fountains[row.asset]["bars"].append(
				{
					"kind": "out_of_service",
					"held": False,
					"status": row.status,
					"label": _("Out of service: {0}").format(_(row.reason)),
					"from": str(row.out_from),
					# Open-ended: draw to the edge of the window.
					"to": str(row.blocked_until or window_to),
					"open_ended": not row.blocked_until,
					"doctype": "Asset Out of Service",
					"name": row.name,
				}
			)

	return {
		"start": str(window_from),
		"days": days,
		"fountains": list(fountains.values()),
		"pools": _pool_rows(window_from, days),
	}


def _pool_rows(window_from, days):
	"""Each accessory pool with the most units out on each day of the window."""
	window_to = window_from + datetime.timedelta(days=days)
	pools = []
	for name in frappe.get_all("Rental Accessory Pool", filters={"disabled": 0}, pluck="name", order_by="name"):
		label, capacity, _turnaround, _disabled = availability.pool_capacity(name)
		lines = [
			(get_datetime(r.block_from), get_datetime(r.block_to), cint(r.qty))
			for r in frappe.db.sql(
				"""
				select a.block_from, a.block_to, a.qty
				from `tabRental Booking Accessory` a
				inner join `tabRental Booking` b on b.name = a.parent
				where a.parenttype = 'Rental Booking'
					and a.pool = %(pool)s
					and b.status in %(holding)s
					and a.block_from < %(to)s
					and a.block_to > %(from)s
				""",
				{"pool": name, "holding": tuple(rules.HOLDING_STATUSES), "from": window_from, "to": window_to},
				as_dict=True,
			)
		]
		used = [
			rules.peak_usage(lines, window_from + datetime.timedelta(days=d), window_from + datetime.timedelta(days=d + 1))
			for d in range(days)
		]
		pools.append({"pool": name, "label": label, "capacity": capacity, "used": used})
	return pools


@frappe.whitelist(methods=["POST"])
def create_rental(data):
	"""Create the Rental Booking the wizard describes, and its Events Project if asked.

	``data`` is the wizard's draft: the fields in :data:`BOOKING_FIELDS`, ``fountains``
	(``[{asset, rate}]``), ``accessories`` (``[{pool, qty, rate}]``), ``status`` (Tentative or
	Confirmed) and ``create_project``. Returns ``{"name", "project"}``.
	"""
	frappe.has_permission("Rental Booking", "create", throw=True)
	data = frappe.parse_json(data) or {}
	status = data.get("status") or "Tentative"
	if status not in rules.INITIAL_STATUSES:
		frappe.throw(_("A new booking starts as Tentative or Confirmed."))

	values = {field: data.get(field) for field in BOOKING_FIELDS if data.get(field) not in (None, "")}
	if not values.get("customer"):
		frappe.throw(_("Pick the customer first."))

	if not values.get("project") and cint(data.get("create_project")):
		values["project"] = create_event_project(values)

	doc = frappe.get_doc(
		{
			"doctype": "Rental Booking",
			"status": status,
			**values,
			"fountains": [
				{"asset": row.get("asset"), "rate": flt(row.get("rate"))}
				for row in data.get("fountains") or []
				if row.get("asset")
			],
			"accessories": [
				{"pool": row.get("pool"), "qty": cint(row.get("qty")), "rate": flt(row.get("rate"))}
				for row in data.get("accessories") or []
				if row.get("pool") and cint(row.get("qty")) > 0
			],
		}
	)
	doc.insert()
	return {"name": doc.name, "project": doc.project}


def create_event_project(values):
	"""A new Events Project for the rental, with the customer and dates filled in.

	Typed by both ``project_type`` and the ``custom_value_stream`` row, because "what kind of
	job is this" is read from either, and a Project carrying only one is invisible to whatever
	reads the other. Inserted with the caller's own permissions.
	"""
	frappe.has_permission("Project", "create", throw=True)
	customer_name = frappe.db.get_value("Customer", values["customer"], "customer_name") or values["customer"]
	delivery = getdate(values.get("delivery_datetime")) if values.get("delivery_datetime") else getdate()
	takedown = getdate(values.get("takedown_datetime")) if values.get("takedown_datetime") else delivery
	name = values.get("event_name") or _("{0} Event {1}").format(customer_name, frappe.utils.formatdate(delivery))

	project = frappe.get_doc(
		{
			"doctype": "Project",
			"project_name": name,
			"customer": values["customer"],
			"expected_start_date": delivery,
			"expected_end_date": max(takedown, delivery),
		}
	)
	if frappe.db.exists("Project Type", EVENTS):
		project.project_type = EVENTS
	if frappe.get_meta("Project").has_field("custom_value_stream") and frappe.db.exists("Value Streams", EVENTS):
		project.append("custom_value_stream", {"value_stream": EVENTS})
	project.insert()
	return project.name

