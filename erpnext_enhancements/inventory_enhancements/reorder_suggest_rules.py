# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Suggested reorder levels from real usage (v1.831.0). No Frappe, no I/O.

From the 2026-11-19 operations review: Nik's next inventory job is setting reorder points, from
what is actually used rather than a guess. The Reorder Board's **Reorder levels** tab shows, next to
each item's reorder rule, a suggested level and quantity, the basis it rests on, and an Apply button;
``reorder_suggestions`` reads the ledger and writes the rule, and every judgement is here.

**What counts as used.** Stock that left the shop for a job or a customer, inside the rule's scope:

* a **take** (Stock Entry, Material Issue: Stock Scan's −) from a shop warehouse;
* a **delivery** (Delivery Note, Sales Invoice or POS Invoice taking stock out) from a shop warehouse;
* a **move out to a job**: a Material Transfer from a shop warehouse into a job site or the Project
  Shelf. A Build job's material is moved to its site, not issued (v1.801.0), so from the shop's
  side the move is the use.

minus what came back from a job:

* stock added **with a job** (Stock Entry, Material Receipt carrying a Project) into a shop warehouse:
  Stock Scan's + with a job picked is a return from that job, taken off in full;
* a move from a **job site or the Project Shelf** back into a shop warehouse (leftovers, or parts set
  aside for a job and released again), taken off **up to what the shop sent out** to a site or the
  shelf in the same period. The shelf also holds parts bought straight for a job, and a site can be
  stocked from the shelf; neither was ever the shop's, so releasing them to a shop bin must not
  cancel the shop's own takes. The cap keeps a move out and its move back netting to nothing without
  letting parts the shop never sent out reduce what it used.

**Not use:** a count correction (Stock Reconciliation: it says the shelf was wrong, not that
anything was used, so it is shown beside the suggestion and never counted), a move between shop
places, a receipt from a supplier, and a return to a supplier.

**The shop** is every warehouse that is not a job site or the Project Shelf (the board's own
reading since v1.801.0 and v1.817.0). **The rule's scope** is where ERPNext reads its projected qty:
the rule's ``warehouse_group`` and everything under it, or its own warehouse when it has no group
(``reorder_rules.in_scope``).

**The basis.** Usage is counted, and averaged, over one period: from the latest of the window's
start, the first take this site ever recorded (Stock Scan went live in late September 2026, so a
90-day window would otherwise count weeks when nobody recorded anything), and the first day the item
was in the shop at all, through today (:func:`measure`). The uses and the days are always the same
days. A suggestion needs at least :data:`MIN_USES` uses over at least :data:`MIN_DAYS` days; anything
thinner is shown with its basis and the reason in words, and has no Apply.

**The suggestion.** ``daily = used / days``.

* *Reorder at* = ``daily × (lead time + SAFETY_DAYS)``: enough to last until the order arrives,
  plus a week's buffer. An item with no lead time (every item on production, 2026-11-19) is given
  :data:`DEFAULT_LEAD_DAYS`, and the page says it was assumed.
* *Buy* = ``daily × COVER_DAYS``: about a month's use per order.

Both are rounded up, to whole units for a whole-number unit, and Buy is never below one unit.

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import datetime as _dt
import math

#: The windows the page offers, in days. Production's take history began in late September 2026.
WINDOW_CHOICES = (30, 60, 90, 180)
DEFAULT_WINDOW = 90

#: The least history a suggestion needs. Fewer uses or fewer days and it is shown, marked, with no Apply.
MIN_USES = 3
MIN_DAYS = 14

#: The buffer on top of the lead time, in days of average use.
SAFETY_DAYS = 7
#: How many days of average use one order buys.
COVER_DAYS = 30
#: The lead time assumed for an item that has none set (Item.lead_time_days 0 or blank).
DEFAULT_LEAD_DAYS = 7

#: Who may apply a suggestion (the task's gate). The person's own Item write permission is checked as
#: well, by the save itself: applying is an ordinary edit of the Item's reorder table.
APPLY_ROLES = ("Purchase Manager", "Stock Manager", "System Manager")

#: A level or quantity above this is a typo (the Reorder Board's ``reorder_rules.MAX_QTY``).
MAX_QTY = 100000

RECONCILIATION = "Stock Reconciliation"
STOCK_ENTRY = "Stock Entry"
ISSUE = "Material Issue"
RECEIPT = "Material Receipt"
TRANSFER_PURPOSES = frozenset({"Material Transfer", "Material Transfer for Manufacture"})
SELLING_VOUCHERS = frozenset({"Delivery Note", "Sales Invoice", "POS Invoice"})

DIRECTION_HIGHER = "higher"
DIRECTION_LOWER = "lower"
DIRECTION_SAME = "same"

_EPS = 1e-9


def flt(value) -> float:
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	return 0.0 if math.isnan(number) or math.isinf(number) else number


def as_date(value):
	"""A date from a date, a datetime or ISO text; ``None`` for anything else."""
	if isinstance(value, _dt.datetime):
		return value.date()
	if isinstance(value, _dt.date):
		return value
	if isinstance(value, str) and value:
		try:
			return _dt.date.fromisoformat(value[:10])
		except ValueError:
			return None
	return None


def clean_window(days) -> int:
	"""One of :data:`WINDOW_CHOICES`; anything else is :data:`DEFAULT_WINDOW`."""
	try:
		days = int(flt(days))
	except (TypeError, ValueError):
		return DEFAULT_WINDOW
	return days if days in WINDOW_CHOICES else DEFAULT_WINDOW


def window_start(today, days):
	"""The first day of a window of ``days`` days ending today (today counts as one)."""
	return today - _dt.timedelta(days=max(int(days), 1) - 1)


def item_usage(rows, in_shop_scope, sites, not_shop, start, today):
	"""What one item's ledger says about its use in ``[start, today]``.

	``rows``: every live Stock Ledger Entry of the item, in ledger order (``posting_datetime``, then
	``creation``), each ``{warehouse, actual_qty, qty_after_transaction, posting_date, voucher_type,
	voucher_no, project, purpose}`` (``purpose``: the Stock Entry's, else None). ``in_shop_scope(w)``:
	whether ``w`` is a shop warehouse inside the rule's scope. ``sites``: the job-site warehouses.
	``not_shop``: the sites and the Project Shelf.

	Returns ``{used, uses, gross_used, returned, counted_down, counted_up, first_seen}``: ``used`` is
	what left minus what came back, never below 0; ``uses`` how many vouchers took some; ``returned``
	the stock added back with a job plus the moves back from a site or the shelf, those capped at what
	the shop sent out to them in the same period; the counts are the units count corrections removed
	and added in the scope (derived as ERPNext's Stock Ledger report does: a count's balance minus the
	balance before it); ``first_seen`` the first day the item had any ledger row in the scope, at any
	date. Call it through :func:`measure`, which picks ``start`` so the use and the days agree.
	"""
	balance = {}
	moves = {}
	first_seen = None
	gross = 0.0
	returned = 0.0
	sent_out = 0.0
	came_back = 0.0
	counted_down = 0.0
	counted_up = 0.0
	use_vouchers = set()
	for row in rows or ():
		warehouse = row.get("warehouse")
		after = flt(row.get("qty_after_transaction"))
		change = flt(row.get("actual_qty"))
		voucher_type = row.get("voucher_type") or ""
		if voucher_type == RECONCILIATION and abs(change) < _EPS:
			change = after - balance.get(warehouse, 0.0)
		balance[warehouse] = after
		in_shop = bool(in_shop_scope(warehouse))
		day = as_date(row.get("posting_date"))
		if in_shop and day and first_seen is None:
			first_seen = day
		if not day or day < start or day > today:
			continue
		purpose = row.get("purpose")
		voucher = row.get("voucher_no")
		if voucher_type == RECONCILIATION:
			if in_shop:
				if change < 0:
					counted_down -= change
				else:
					counted_up += change
		elif voucher_type == STOCK_ENTRY and purpose == ISSUE:
			if in_shop and change < 0:
				gross -= change
				use_vouchers.add(voucher)
		elif voucher_type in SELLING_VOUCHERS:
			if in_shop and change < 0:
				gross -= change
				use_vouchers.add(voucher)
		elif voucher_type == STOCK_ENTRY and purpose in TRANSFER_PURPOSES:
			moves.setdefault(voucher, []).append((warehouse, change, in_shop))
		elif voucher_type == STOCK_ENTRY and purpose == RECEIPT:
			if in_shop and change > 0 and row.get("project"):
				returned += change
	for voucher, legs in moves.items():
		out_of_shop = sum(-c for _w, c, shop in legs if c < 0 and shop)
		to_a_job = any(c > 0 and w in not_shop for w, c, _shop in legs)
		if out_of_shop > _EPS and to_a_job:
			gross += out_of_shop
			sent_out += out_of_shop
			use_vouchers.add(voucher)
		from_a_job = any(c < 0 and w in not_shop for w, c, _shop in legs)
		if from_a_job:
			came_back += sum(c for _w, c, shop in legs if c > 0 and shop)
	# A move back is taken off only up to what the shop sent out: parts bought straight onto the shelf,
	# or sent to a site from the shelf, were never the shop's to have used.
	returned += min(came_back, sent_out)
	return {
		"used": max(gross - returned, 0.0),
		"uses": len(use_vouchers),
		"gross_used": gross,
		"returned": returned,
		"counted_down": counted_down,
		"counted_up": counted_up,
		"first_seen": first_seen,
	}


def first_seen_in(rows, in_shop_scope):
	"""The first day the item had a ledger row in the scope, at any date, or ``None``."""
	for row in rows or ():
		day = as_date(row.get("posting_date"))
		if day and in_shop_scope(row.get("warehouse")):
			return day
	return None


def measure(rows, in_shop_scope, sites, not_shop, window_first_day, tracking_since, today):
	"""One rule's use and the days it was measured over, the same period for both.

	The period is :func:`basis`'s; the use is :func:`item_usage` over exactly those days, so "4 uses in 30
	days" never pairs uses from 90 days with an average over 30. Returns ``item_usage``'s dict plus
	``basis_start`` and ``basis_days`` (``None`` and 0, and nothing counted, when the item was never in
	the scope).
	"""
	first = first_seen_in(rows, in_shop_scope)
	start, days = basis(window_first_day, tracking_since, first, today)
	usage = item_usage(rows, in_shop_scope, sites, not_shop, start or today + _dt.timedelta(days=1), today)
	usage.update({"first_seen": first, "basis_start": start, "basis_days": days})
	return usage


def basis(window_first_day, tracking_since, first_seen, today):
	"""``(start, days)``: the days the average is taken over, or ``(None, 0)`` when the item was never in
	the scope. ``tracking_since``: the first take this site recorded (``None`` if none ever was)."""
	if first_seen is None:
		return None, 0
	start = max(d for d in (window_first_day, tracking_since, first_seen) if d is not None)
	if start > today:
		return None, 0
	return start, (today - start).days + 1


def _times(n):
	return "1 time" if n == 1 else f"{n} times"


def _days(n):
	return "1 day" if n == 1 else f"{n} days"


def _thin_reason(used, uses, days):
	"""Why there is no suggestion yet, one cause in words, or ``None`` when there is enough."""
	if uses <= 0:
		return f"Not used in these {_days(days)}."
	if used <= _EPS:
		return f"Used {_times(uses)}, but as much came back as went out: nothing used on balance."
	if uses < MIN_USES:
		return f"Used {_times(uses)} in {_days(days)}: a suggestion needs at least {MIN_USES} uses."
	if days < MIN_DAYS:
		return f"Only {_days(days)} of history: a suggestion needs at least {MIN_DAYS} days."
	return None


def _round_up(value, whole):
	if whole:
		return float(math.ceil(value - _EPS))
	return math.ceil(value * 100 - _EPS) / 100.0


def suggest(used, uses, days, lead_time_days, whole):
	"""The suggestion for one rule, or the reason there is none.

	Returns ``{enough, level, qty, daily, lead_days, lead_assumed, reason}``. ``enough`` False means there
	is no suggestion: ``level`` and ``qty`` are then None and ``reason`` gives the one cause, in this
	order: not in the shop at all, not used, used but as much came back as left, too few uses, too few
	days. ``lead_assumed`` is True when the item has no lead time and :data:`DEFAULT_LEAD_DAYS` was used.
	"""
	lead = int(flt(lead_time_days))
	lead_assumed = lead <= 0
	lead_days = DEFAULT_LEAD_DAYS if lead_assumed else lead
	out = {
		"enough": False,
		"level": None,
		"qty": None,
		"daily": 0.0,
		"lead_days": lead_days,
		"lead_assumed": lead_assumed,
		"reason": None,
	}
	used = flt(used)
	uses = int(flt(uses))
	days = int(flt(days))
	if days <= 0:
		out["reason"] = "Not in the shop during this window."
		return out
	out["daily"] = used / days
	reason = _thin_reason(used, uses, days)
	if reason:
		out["reason"] = reason
		return out
	daily = used / days
	level = _round_up(daily * (lead_days + SAFETY_DAYS), whole)
	qty = _round_up(daily * COVER_DAYS, whole)
	qty = max(qty, 1.0 if whole else 0.01)
	out.update({"enough": True, "level": min(level, float(MAX_QTY)), "qty": min(qty, float(MAX_QTY))})
	return out


def direction(current_level, suggested_level):
	"""Whether the suggestion raises, lowers or keeps the rule's reorder level; None with no suggestion."""
	if suggested_level is None:
		return None
	diff = flt(suggested_level) - flt(current_level)
	if diff > _EPS:
		return DIRECTION_HIGHER
	if diff < -_EPS:
		return DIRECTION_LOWER
	return DIRECTION_SAME


def clean_rule(level, qty, whole):
	"""``(level, qty, error)`` for the numbers a person approved. Reorder at may be 0 (reorder when it
	runs out, as the reorder editor allows) but not below; Buy must be more than 0; neither above
	:data:`MAX_QTY`; whole numbers for a whole-number unit."""
	values = []
	for value, label, minimum_exclusive in ((level, "Reorder at", False), (qty, "Buy", True)):
		text = str(value if value is not None else "").strip().replace(",", "")
		if not text:
			return None, None, f"Enter {label}."
		try:
			number = float(text)
		except ValueError:
			return None, None, f"{label} must be a number."
		if math.isnan(number) or math.isinf(number):
			return None, None, f"{label} must be a number."
		if number < 0 or (minimum_exclusive and number <= 0):
			return (
				None,
				None,
				f"{label} must be more than 0." if minimum_exclusive else f"{label} cannot be below 0.",
			)
		if number > MAX_QTY:
			return None, None, f"{label} cannot be more than {MAX_QTY:,}."
		if whole and abs(number - round(number)) > _EPS:
			return None, None, f"{label}: this unit takes whole numbers only."
		values.append(number)
	return values[0], values[1], None


def same_stamp(a, b) -> bool:
	"""Whether two ``modified`` values are the same moment (a datetime, or its text from JSON)."""

	def norm(value):
		if isinstance(value, _dt.datetime):
			return value
		try:
			return _dt.datetime.fromisoformat(str(value).strip())
		except (TypeError, ValueError):
			return None

	first, second = norm(a), norm(b)
	return first is not None and first == second
