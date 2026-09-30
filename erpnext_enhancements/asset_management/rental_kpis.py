# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Rental fleet KPIs for the Product dashboard (v1.567.0).

The existing rental KPIs count **Projects** of type Events. They answer "how many events", not "is
the fleet earning its keep" — a fountain can sit idle all summer while the event count looks
healthy. These read the fleet itself: each fountain's calendar (``Asset Booking`` Rental legs of
firm bookings), the Rental Bookings' booked value, the accessory pools and out-of-service records.

``metrics()`` returns ``[(key, label, value, unit, source, direction), ...]``;
``kpi_dashboards/snapshots._product_metrics`` adds them beside the Events KPIs. A value that cannot
be computed (no rentable fountains yet) is left out rather than published as a 0 that reads as a
result.
"""

import datetime

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, getdate, nowdate

from erpnext_enhancements.asset_management import rental_rules as rules
from erpnext_enhancements.kpi_dashboards.metrics import HIGHER, LOWER

FIRM = tuple(rules.FIRM_STATUSES)


def _fleet():
	from erpnext_enhancements.asset_management.rental_availability import fountain_profiles

	return list(fountain_profiles(rentable_only=True))


def _rental_windows(assets, start, end):
	"""``{asset: [(from, to), ...]}`` for submitted Rental legs (firm rentals) touching the window."""
	windows = {asset: [] for asset in assets}
	if not assets:
		return windows
	for row in frappe.db.sql(
		"""
		select asset, from_datetime, to_datetime
		from `tabAsset Booking`
		where asset in %(assets)s
			and docstatus = 1
			and booking_type = 'Rental'
			and from_datetime < %(end)s
			and to_datetime > %(start)s
		""",
		{"assets": tuple(assets), "start": start, "end": end},
		as_dict=True,
	):
		windows[row.asset].append((get_datetime(row.from_datetime), get_datetime(row.to_datetime)))
	return windows


def utilization(assets, start, end):
	"""``(percent of fountain-hours rented, fountains with no rental at all)`` over the window."""
	windows = _rental_windows(assets, start, end)
	hours = (end - start).total_seconds() / 3600
	used = {asset: rules.covered_hours(w, start, end) for asset, w in windows.items()}
	capacity = hours * len(assets)
	percent = sum(used.values()) / capacity * 100 if capacity else None
	idle = sum(1 for value in used.values() if value <= 0)
	return percent, idle


def booked_revenue(start_date, end_date):
	"""Rental totals of firm bookings delivering in the window (booked, not invoiced, value)."""
	value = frappe.db.sql(
		"""
		select sum(total_amount) from `tabRental Booking`
		where status in %(firm)s and delivery_datetime >= %(start)s and delivery_datetime < %(end)s
		""",
		{"firm": FIRM, "start": get_datetime(start_date), "end": get_datetime(end_date)},
	)
	return flt(value[0][0]) if value else 0


def pool_full_days(start, days):
	"""Pool-days in the coming window on which a pool is fully booked (every unit out)."""
	from erpnext_enhancements.asset_management.rental_availability import pool_capacity

	full = 0
	for pool in frappe.get_all("Rental Accessory Pool", filters={"disabled": 0}, pluck="name"):
		_label, capacity, _turnaround, _disabled = pool_capacity(pool)
		if capacity <= 0:
			continue
		lines = [
			(get_datetime(r.block_from), get_datetime(r.block_to), cint(r.qty))
			for r in frappe.db.sql(
				"""
				select a.block_from, a.block_to, a.qty
				from `tabRental Booking Accessory` a
				inner join `tabRental Booking` b on b.name = a.parent
				where a.parenttype = 'Rental Booking' and a.pool = %(pool)s and b.status in %(holding)s
					and a.block_to > %(start)s and a.block_from < %(end)s
				""",
				{
					"pool": pool,
					"holding": tuple(rules.HOLDING_STATUSES),
					"start": start,
					"end": start + datetime.timedelta(days=days),
				},
				as_dict=True,
			)
		]
		for day in range(days):
			day_start = start + datetime.timedelta(days=day)
			if rules.peak_usage(lines, day_start, day_start + datetime.timedelta(days=1)) >= capacity:
				full += 1
	return full


def metrics():
	if not frappe.db.table_exists("Rental Booking"):
		return []
	fleet = _fleet()
	if not fleet:
		return []
	today = getdate(nowdate())
	now = get_datetime(today)
	d30 = get_datetime(add_days(today, -30))
	d90 = add_days(today, -90)
	out = []

	percent, idle = utilization(fleet, d30, now)
	if percent is not None:
		out.append(("fleet_utilization_30", "Fleet Utilization (30d)", percent, "%", "Asset Booking", HIGHER))
	out.append(("fleet_idle_fountains_30", "Idle Fountains (30d)", idle, "count", "Asset Booking", LOWER))

	revenue_90 = booked_revenue(d90, add_days(today, 1))
	out.append(("rental_booked_revenue_90", "Rental Revenue Booked (90d)", revenue_90, "USD", "Rental Booking", HIGHER))
	out.append(
		("rental_revenue_per_fountain_90", "Rental Revenue per Fountain (90d)", revenue_90 / len(fleet), "USD", "Rental Booking", HIGHER)
	)
	out.append(
		(
			"rental_holds_open",
			"Rental Holds Open",
			frappe.db.count("Rental Booking", {"status": "Tentative"}),
			"count",
			"Rental Booking",
			HIGHER,
		)
	)
	out.append(
		(
			"fleet_out_of_service",
			"Fountains Out of Service",
			frappe.db.count("Asset Out of Service", {"status": "Out of Service"}),
			"count",
			"Asset Out of Service",
			LOWER,
		)
	)
	out.append(
		("accessory_pool_full_days_30", "Accessory Pool-days Fully Booked (next 30d)", pool_full_days(now, 30), "count", "Rental Accessory Pool", LOWER)
	)
	return out
