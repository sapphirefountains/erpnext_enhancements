"""Pure KPI math — no frappe import, so it runs in the bench-free CI suite.

These helpers turn a raw metric value plus its target into the presentation
fields a snapshot value carries: the Good/Watch/Bad status, the period-over-period
trend, a human display string, and a source-staleness check. Keeping them pure
(deterministic, side-effect-free, ``now`` injectable) makes the grading logic
unit-testable without a database — see ``tests/test_kpi_metrics.py``.
"""

import re
from datetime import date, datetime

# A value within this fraction of its target counts as "Watch" rather than "Bad".
WATCH_BAND = 0.10

#: A card charge for a store run is dated on the day of the purchase or up to this many days
#: after it, never before. QuickBooks holds the same purchase from two feeds -- itemised receipt
#: entries and bank-feed descriptors -- and the bank feed can be two days later (Home Depot
#: $43.31: 2026-07-07 as a receipt entry, 2026-07-09 from the feed). Matching the exact day
#: counted such a trip twice.
STORE_RUN_PAIR_DAYS = 3

#: Two dollar amounts closer than this are the same charge.
STORE_RUN_AMOUNT_TOLERANCE = 0.05

#: With no receipt total recorded, a charge still matches a run whose lines (before tax) are
#: within this much below it: Utah's combined sales tax is under 9%.
STORE_RUN_TAX_ALLOWANCE = 0.15

HIGHER = "Higher is better"
LOWER = "Lower is better"


def compute_status(value, target, direction=HIGHER, watch_band=WATCH_BAND):
	"""Grade ``value`` against ``target`` -> "Good" | "Watch" | "Bad" | "".

	Returns "" (no badge) when there is no usable target. ``direction`` decides
	whether being above or below target is good.
	"""
	if target is None:
		return ""
	try:
		value = float(value)
		target = float(target)
	except (TypeError, ValueError):
		return ""

	if target == 0:
		# A zero target only makes sense for "lower is better" (e.g. 0 failed
		# syncs): exactly zero is Good, anything above it is Bad.
		if direction == LOWER:
			return "Good" if value <= 0 else "Bad"
		return ""

	ratio = value / target
	if direction == LOWER:
		if ratio <= 1:
			return "Good"
		if ratio <= 1 + watch_band:
			return "Watch"
		return "Bad"
	# higher is better
	if ratio >= 1:
		return "Good"
	if ratio >= 1 - watch_band:
		return "Watch"
	return "Bad"


def compute_trend_pct(value, prior):
	"""Percent change of ``value`` vs the prior snapshot's value (None if N/A)."""
	if prior is None:
		return None
	try:
		value = float(value)
		prior = float(prior)
	except (TypeError, ValueError):
		return None
	if prior == 0:
		return None
	return (value - prior) / abs(prior) * 100.0


def fmt_value(value, unit=""):
	"""Human display string for a value given its unit."""
	try:
		v = float(value)
	except (TypeError, ValueError):
		return str(value) if value is not None else ""
	unit = (unit or "").strip()
	if unit == "USD":
		return f"${v:,.0f}"
	if unit == "%":
		return f"{v:,.1f}%"
	if unit == "days":
		return f"{v:,.1f} d"
	if unit in ("count", ""):
		if v == int(v):
			return f"{int(v):,}"
		return f"{v:,.1f}"
	return f"{v:,.2f} {unit}"


def turnover_rate_pct(separations, headcount_start, headcount_end):
	"""Separations over a window / two-point average headcount * 100.

	The classic annualized-turnover formula: the denominator averages the
	headcount at the start and end of the window so a growing (or shrinking)
	team isn't over- or under-penalized. None when inputs are non-numeric or
	the average headcount is zero (a rate over nobody is meaningless).
	"""
	try:
		separations = float(separations)
		avg = (float(headcount_start) + float(headcount_end)) / 2.0
	except (TypeError, ValueError):
		return None
	if avg <= 0:
		return None
	return separations / avg * 100.0


def _parse_dt(value):
	"""Coerce a frappe datetime/date or its string form to a naive datetime."""
	if isinstance(value, datetime):
		return value.replace(tzinfo=None)
	if hasattr(value, "year") and not isinstance(value, datetime):
		# a date — midnight
		return datetime(value.year, value.month, value.day)
	if isinstance(value, str):
		for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
			try:
				return datetime.strptime(value.strip()[:26], fmt)
			except ValueError:
				continue
	return None


def _as_day(value):
	"""A date from a ``date``, a ``datetime`` or an ISO string; ``None`` otherwise."""
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	try:
		return date.fromisoformat(str(value or "").strip()[:10])
	except ValueError:
		return None


def _amount(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _receipt_number(value):
	return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def combine_store_runs(charges, receipts, since, store_key, pair_days=STORE_RUN_PAIR_DAYS):
	"""``(count, spend)`` of store runs since ``since``, from two records of the same trips.

	``charges`` are money records, one per card charge: ``{supplier, day, amount}`` -- the
	QuickBooks card purchases, standalone Purchase Invoices, and Journal Entries or Payment
	Entries naming the store as the party. ``receipts`` are what the Stock Scan page recorded,
	one Purchase Receipt per line: ``{supplier, day, run, amount, receipt_total,
	receipt_number}``. Before the cutover most trips have both (the receipt the same day, the
	charge weeks later), some only a charge (not recorded), some only receipts (the charge not
	in yet). Counting either alone is wrong; adding them counts most trips twice.

	**Receipts become trips.** One trip is one run id (a receipt with none is its own trip), and
	runs that carry the same receipt number at the same store on the same day are one trip too
	-- two people who shopped together and each started a run. A trip's day is its earliest
	receipt; its amount is the receipt total the technician entered (tax included) or, without
	one, the sum of its receipts.

	**Each trip is paired with at most one charge, and each charge with at most one trip**: at
	the same store (``store_key``, so "Lowes" and "Lowe's" are one), dated on the trip's day or
	up to ``pair_days`` after it -- never before, a charge does not precede the purchase. Three
	passes, so the best evidence wins: first a charge equal to the receipt total, then one the
	lines plus tax could make, then any charge in the window; within a pass the nearest day.

	**Count** is trips plus unpaired charges; **spend** is the charge for a paired trip (what the
	card paid, tax included), the trip's own amount for an unpaired one, and every unpaired
	charge. Only trips and charges dated on or after ``since`` count: a trip just before the
	window still claims its charge inside it, so pass rows from a few days earlier.

	Pure: no frappe; ``store_key`` is a callable (``stock_scan_rules.store_key``); dates may be
	``date``/``datetime`` or ISO strings.
	"""
	since_day = _as_day(since)
	trips = {}
	for row in receipts or ():
		day = _as_day(row.get("day"))
		if day is None:
			continue
		store = store_key(row.get("supplier") or "")
		number = _receipt_number(row.get("receipt_number"))
		key = ("receipt", store, day, number) if number else ("run", str(row.get("run") or ""))
		trip = trips.setdefault(key, {"key": key, "store": store, "day": day, "net": 0.0, "total": 0.0})
		trip["day"] = min(trip["day"], day)
		trip["net"] += _amount(row.get("amount"))
		trip["total"] = max(trip["total"], _amount(row.get("receipt_total")))
	runs = sorted(trips.values(), key=lambda t: (t["day"], t["store"], str(t["key"])))

	bills = []
	for index, row in enumerate(charges or ()):
		day = _as_day(row.get("day"))
		if day is None:
			continue
		bills.append(
			{
				"index": index,
				"store": store_key(row.get("supplier") or ""),
				"day": day,
				"amount": _amount(row.get("amount")),
				"paired": False,
			}
		)

	tolerance = STORE_RUN_AMOUNT_TOLERANCE

	def equal_to_total(trip, bill):
		return trip["total"] > 0 and abs(bill["amount"] - trip["total"]) <= tolerance

	def lines_plus_tax(trip, bill):
		net = trip["net"]
		return (
			net > 0 and net - tolerance <= bill["amount"] <= net * (1 + STORE_RUN_TAX_ALLOWANCE) + tolerance
		)

	def any_amount(trip, bill):
		return True

	for matches in (equal_to_total, lines_plus_tax, any_amount):
		for trip in runs:
			if trip.get("bill") is not None:
				continue
			options = [
				bill
				for bill in bills
				if not bill["paired"]
				and bill["store"] == trip["store"]
				and 0 <= (bill["day"] - trip["day"]).days <= pair_days
				and matches(trip, bill)
			]
			if not options:
				continue
			target = trip["total"] or trip["net"]
			best = min(
				options,
				key=lambda bill: (
					(bill["day"] - trip["day"]).days,
					abs(bill["amount"] - target),
					bill["index"],
				),
			)
			best["paired"] = True
			trip["bill"] = best

	count = 0
	spend = 0.0
	for trip in runs:
		if since_day and trip["day"] < since_day:
			continue
		count += 1
		bill = trip.get("bill")
		spend += bill["amount"] if bill else (trip["total"] or trip["net"])
	for bill in bills:
		if bill["paired"] or (since_day and bill["day"] < since_day):
			continue
		count += 1
		spend += bill["amount"]
	return count, round(spend, 2)


def is_source_stale(last_sync, max_age_hours=6, now=None):
	"""True when an upstream source last synced more than ``max_age_hours`` ago
	(or never). ``now`` is injectable for deterministic tests."""
	if not last_sync:
		return True
	dt = _parse_dt(last_sync)
	if dt is None:
		return True
	now = now or datetime.now()
	age_hours = (now - dt).total_seconds() / 3600.0
	return age_hours > max_age_hours
