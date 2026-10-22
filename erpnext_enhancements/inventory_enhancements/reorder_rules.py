# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Reorder Board's judgements (v1.730.0, ER-2026-1323810). No Frappe, no I/O.

Parker Bailey, approved by Nik 2026-10-22: instead of ERPNext raising a Material Request on its
own every night, tell the buyers what fell below its reorder point and let them choose what to
request, then follow each request through the purchase order to the receipt. Everything the
board, the daily digest and the Material Request endpoint *decide* is here, so one definition
serves all three and the tests can hold it without a bench:

* **Below, and how much.** :func:`is_below` and :func:`suggested_qty` are ERPNext's own rule, copied
  line for line from the closure inside ``erpnext.stock.reorder_item._reorder_item``
  (``add_to_material_request``), which cannot be imported, **as production runs it: ERPNext
  v16.50.0** (``tabInstalled Application``, 2026-10-22). The rule fires when
  ``(reorder_level or reorder_qty) and projected_qty <= reorder_level``, so a level of 0 with a
  quantity is a real rule ("reorder when out of stock", Parker); and the quantity is the larger of
  the rule's ``reorder_qty`` and the deficiency ``reorder_level - projected_qty``. The board agrees
  with what auto-indent would have done on this site. NB the ``version-16`` branch has since changed
  the quantity (``reorder_qty += reorder_level - projected_qty`` when ``projected + reorder_qty``
  would not clear the level); re-read the installed tag when ERPNext is upgraded.
* **Which projected qty.** :func:`projected_for_rule` reads ERPNext's
  ``get_item_warehouse_projected_qty`` map (every Bin's projected qty, rolled up into each
  ancestor warehouse) at the rule's ``warehouse_group`` when it has one, else at its warehouse,
  and a missing Bin is 0, exactly as ``_reorder_item`` does. On production every rule has a group
  (83 Inventory Room rules on ``All Warehouses - SF``, 32 bin rules on ``Inventory - SF``), so this
  is the common path, not an edge.
* **Where a request is, and whether it still covers.** :func:`stage_for_request` and
  :func:`stage_for_order` derive the stage of one open line from the documents' own links: Material
  Request Item <- Purchase Order Item (``material_request_item``) <- Purchase Receipt Item
  (``purchase_order_item``). There is no tracking table of our own to drift from them. Whether the
  line still keeps its item off Needs reorder is :func:`still_to_come` (a PO closed short brings
  nothing more; On Hold still does), :func:`is_stale_draft` (a Draft over :data:`STALE_DRAFT_DAYS`
  stops covering) and :func:`in_scope` (any warehouse under the rule's ``warehouse_group``).
* **Snoozes** (:func:`snooze_fields`, :func:`snooze_state`) and the **digest's pick**
  (:func:`digest_pick`), and the **recipients** (:func:`filter_recipients`).

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import datetime as _dt
import math
import re

#: A quantity above this is a typo (a scanned barcode is twelve digits). The reorder editor's
#: ``api/reorder_levels.MAX_QTY`` holds rules to the same ceiling.
MAX_QTY = 100000

#: How long a fully received line stays on the board's On order list, so the buyer sees it
#: arrive. A week covers a receipt booked on Friday and looked for on Monday; longer and the
#: list fills with finished work.
RECEIVED_VISIBLE_DAYS = 7

#: A Draft Material Request older than this (days since it was made) stops covering its item: the
#: item returns to Needs reorder and the Draft shows on its line as "Old draft request". A buyer
#: who makes a Draft and submits it within the month never sees the difference.
STALE_DRAFT_DAYS = 30

#: Most lines one Material Request call takes. The board lists ~116 rules on production.
MAX_LINES = 200

SNOOZE_WEEK = "1 week"
SNOOZE_MONTH = "1 month"
SNOOZE_DROP = "Until stock drops further"
SNOOZE_KINDS = (SNOOZE_WEEK, SNOOZE_MONTH, SNOOZE_DROP)

# Stages of an open line, in the order a purchase moves through them.
STAGE_DRAFT_REQUEST = "draft_request"
STAGE_REQUESTED = "requested"
STAGE_PO_DRAFT = "po_draft"
STAGE_PARTIALLY_ORDERED = "partially_ordered"
STAGE_ORDERED = "ordered"
STAGE_PARTIALLY_RECEIVED = "partially_received"
STAGE_RECEIVED = "received"
#: Not a stage a purchase moves through: a Draft left longer than STALE_DRAFT_DAYS.
STAGE_OLD_DRAFT = "old_draft"
STAGES = (
	STAGE_OLD_DRAFT,
	STAGE_DRAFT_REQUEST,
	STAGE_REQUESTED,
	STAGE_PO_DRAFT,
	STAGE_PARTIALLY_ORDERED,
	STAGE_ORDERED,
	STAGE_PARTIALLY_RECEIVED,
	STAGE_RECEIVED,
)
STAGE_LABELS = {
	STAGE_OLD_DRAFT: "Old draft request",
	STAGE_DRAFT_REQUEST: "Draft request",
	STAGE_REQUESTED: "Requested",
	STAGE_PO_DRAFT: "PO in draft",
	STAGE_PARTIALLY_ORDERED: "Partially ordered",
	STAGE_ORDERED: "Ordered",
	STAGE_PARTIALLY_RECEIVED: "Partially received",
	STAGE_RECEIVED: "Received",
}

#: Material Request statuses that no longer stand for a purchase anyone is making. A Draft MR
#: (docstatus 0) is NOT here: it covers its item, because a person made it on purpose and will
#: submit it (TASK-2026-02925 creates requests as Drafts). It does not raise ERPNext's requested
#: qty, so the projected qty stays low and only this rule keeps the item off Needs reorder.
CLOSED_REQUEST_STATUSES = frozenset({"Stopped", "Cancelled", "Closed"})
#: Purchase Order statuses that will bring nothing more (Cancelled is docstatus 2 and never read).
#: On Hold is deliberately absent: see :func:`order_is_closed`.
CLOSED_ORDER_STATUSES = frozenset({"Closed", "Cancelled", "Completed", "Delivered"})

#: Purchase Order.custom_order_stage values (``po_order_stage.py``) that say how the goods come.
ORDER_STAGE_PICKUP = "Waiting for Pickup"
ORDER_STAGE_DELIVERY = "Waiting for Delivery"

#: Accounts that hold a role without being a person who buys anything. ``Administrator`` and
#: ``Guest`` are Frappe's; ``triton@`` is the AI service account, which holds Purchase Manager
#: for its sync work (2026-10-22: 9 enabled Purchase Managers, these two among them).
EXCLUDED_RECIPIENTS = frozenset({"administrator", "guest", "triton@sapphirefountains.com"})

_EPS = 1e-9
_KEY_SEP = "::"


def flt(value) -> float:
	"""``frappe.utils.flt`` for the values this module sees: None, "", numbers, numeric text."""
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	return 0.0 if math.isnan(number) or math.isinf(number) else number


def line_key(item_code, warehouse) -> str:
	"""One rule's identity: an item has at most one reorder rule per warehouse."""
	return f"{item_code or ''}{_KEY_SEP}{warehouse or ''}"


def split_key(key):
	item_code, _sep, warehouse = (key or "").partition(_KEY_SEP)
	return item_code, warehouse


# --- ERPNext's rule ------------------------------------------------------------------


def projected_for_rule(projected_map, item_code, warehouse, warehouse_group=None) -> float:
	"""The projected qty ERPNext's reorder job compares with this rule's level.

	``projected_map`` is ``get_item_warehouse_projected_qty``'s answer:
	``{item_code: {warehouse: projected_qty}}`` with every Bin rolled up into its ancestors.
	The group wins when the rule has one; a missing entry (no Bin) is 0.
	"""
	per_item = (projected_map or {}).get(item_code) or {}
	return flt(per_item.get(warehouse_group if warehouse_group else warehouse))


def is_below(reorder_level, reorder_qty, projected_qty) -> bool:
	"""ERPNext's trigger: ``(reorder_level or reorder_qty) and projected_qty <= reorder_level``."""
	level = flt(reorder_level)
	qty = flt(reorder_qty)
	return bool((level or qty) and flt(projected_qty) <= level)


def suggested_qty(reorder_level, reorder_qty, projected_qty) -> float:
	"""How much ERPNext would have requested, in the stock UOM. 0 when the rule does not fire.

	ERPNext v16.50.0 (production)::

	        deficiency = reorder_level - projected_qty
	        if deficiency > reorder_qty:
	            reorder_qty = deficiency

	so a shortfall deeper than the rule's quantity is made good to the level.
	"""
	if not is_below(reorder_level, reorder_qty, projected_qty):
		return 0.0
	qty = flt(reorder_qty)
	deficiency = flt(reorder_level) - flt(projected_qty)
	if deficiency > qty:
		qty = deficiency
	return qty


def order_qty(stock_qty, conversion_factor=1.0, must_be_whole_number=False) -> float:
	"""A stock-UOM quantity in the purchase UOM, rounded up for a whole-number UOM.

	ERPNext's ``create_material_request``: ``qty = reorder_qty / conversion_factor``, then
	``ceil`` when the UOM must be whole.
	"""
	factor = flt(conversion_factor) or 1.0
	qty = flt(stock_qty) / factor
	if must_be_whole_number:
		qty = math.ceil(qty - _EPS)
	return qty


def clean_qty(value, must_be_whole_number=False):
	"""``(qty, error)`` for a buy quantity typed on the board, in the stock UOM.

	It must be a number above 0 and at most :data:`MAX_QTY`, and whole for a whole-number unit.
	"""
	try:
		qty = float(value)
	except (TypeError, ValueError):
		return None, "Enter a quantity."
	if math.isnan(qty) or math.isinf(qty) or qty <= 0:
		return None, "The quantity must be more than 0."
	if qty > MAX_QTY:
		return None, f"The quantity cannot be more than {MAX_QTY:,}."
	if must_be_whole_number and abs(qty - round(qty)) > _EPS:
		return None, "This unit takes whole numbers only."
	return qty, None


def schedule_date(today, lead_time_days):
	"""When a request's line is wanted: today plus the item's lead time, as ERPNext sets it."""
	try:
		days = int(flt(lead_time_days))
	except (TypeError, ValueError):
		days = 0
	return today + _dt.timedelta(days=max(days, 0))


# --- where an open line is ------------------------------------------------------------


def _sum(rows, field, docstatus=1):
	return sum(flt(r.get(field)) for r in rows if int(flt(r.get("docstatus"))) == docstatus)


def _received_progress(need, received):
	if need > _EPS and received >= need - _EPS:
		return STAGE_RECEIVED
	if received > _EPS:
		return STAGE_PARTIALLY_RECEIVED
	return None


def order_is_closed(order) -> bool:
	"""A submitted Purchase Order that will bring nothing more: Closed, Completed, Delivered.

	**On Hold is not closed.** ERPNext keeps an On Hold order's quantity in the Bin's ordered qty
	(``bin.update_ordered_qty`` leaves out only Closed and Delivered orders), so the projected qty
	the rule reads still counts it as coming; the buyer paused it, they did not give up on it, and
	"Resume" brings it back. Treating it as covering keeps the board agreeing with the projected qty.
	"""
	return (order.get("status") or "") in CLOSED_ORDER_STATUSES


def _received_on(order, receipts_by_order):
	return _sum(receipts_by_order.get(order.get("name"), []), "stock_qty")


def still_to_come(request, orders, receipts_by_order) -> float:
	"""How much of a Material Request line is still on its way, in the stock UOM.

	The part not yet on a submitted order, plus what submitted orders that are not closed have not
	delivered yet. A Purchase Order closed short (prod, 2026-10-22: 87 of 104 Closed orders under
	100% received) delivers nothing more, and ERPNext closing it touches the Bin but not the
	Material Request, which stays Ordered or Partially Received for ever. Its undelivered part is
	therefore NOT to come: counting it would keep the item covered, off Needs reorder and out of
	the digest, however low the stock goes. A Draft request is wanted whole.
	"""
	need = flt(request.get("stock_qty"))
	if int(flt(request.get("docstatus"))) == 0:
		return need
	submitted = [o for o in orders if int(flt(o.get("docstatus"))) == 1]
	unordered = max(need - _sum(submitted, "stock_qty"), 0.0)
	pending = sum(
		max(flt(o.get("stock_qty")) - _received_on(o, receipts_by_order), 0.0)
		for o in submitted
		if not order_is_closed(o)
	)
	return unordered + pending


def order_still_to_come(order, receipts_by_order) -> float:
	"""The same for a Purchase Order line no request asked for."""
	if int(flt(order.get("docstatus"))) == 1 and order_is_closed(order):
		return 0.0
	return max(flt(order.get("stock_qty")) - _received_on(order, receipts_by_order), 0.0)


def stage_for_request(request, orders, receipts) -> str:
	"""The stage of one Material Request Item, from the documents linked to it.

	``request``: ``{docstatus, stock_qty}`` of the MR line. ``orders``: the Purchase Order Items
	whose ``material_request_item`` is that line, ``{docstatus, stock_qty}`` (cancelled ones
	left out by the caller). ``receipts``: the Purchase Receipt Items whose ``purchase_order_item``
	is one of those orders, ``{docstatus, stock_qty}``. Quantities are all stock-UOM. Whether the
	line still covers its item is :func:`still_to_come`, not the stage.
	"""
	if int(flt(request.get("docstatus"))) == 0:
		return STAGE_DRAFT_REQUEST
	need = flt(request.get("stock_qty"))
	progress = _received_progress(need, _sum(receipts, "stock_qty"))
	if progress:
		return progress
	ordered = _sum(orders, "stock_qty")
	if need > _EPS and ordered >= need - _EPS:
		return STAGE_ORDERED
	if ordered > _EPS:
		return STAGE_PARTIALLY_ORDERED
	if any(int(flt(o.get("docstatus"))) == 0 for o in orders):
		return STAGE_PO_DRAFT
	return STAGE_REQUESTED


def stage_for_order(order, receipts) -> str:
	"""The stage of a Purchase Order Item that no Material Request asked for."""
	if int(flt(order.get("docstatus"))) == 0:
		return STAGE_PO_DRAFT
	progress = _received_progress(flt(order.get("stock_qty")), _sum(receipts, "stock_qty"))
	return progress or STAGE_ORDERED


def is_stale_draft(request, today) -> bool:
	"""A Draft request older than :data:`STALE_DRAFT_DAYS` (by its creation) no longer covers.

	Production held one eleven months old (MAT-MR-2025-00007) that hid its item from the board. A
	Draft that old was abandoned, not forgotten for a day; the item comes back to Needs reorder and
	the Draft is shown on its line ("Old draft request") to finish or delete.
	"""
	if int(flt(request.get("docstatus"))) != 0:
		return False
	made = _date(request.get("creation") or request.get("transaction_date"))
	if not made:
		return False
	return (today - made).days > STALE_DRAFT_DAYS


def still_listed(stage, is_open, last_receipt_date, today) -> bool:
	"""Whether a line stays on the On order list.

	An open line stays; a received one stays a week (:data:`RECEIVED_VISIBLE_DAYS`) so the buyer
	sees it arrive; a line closed short (nothing more to come, not fully received) drops off, and
	its item is judged on its stock again.
	"""
	if is_open:
		return True
	if stage != STAGE_RECEIVED or not last_receipt_date:
		return False
	return (today - last_receipt_date).days <= RECEIVED_VISIBLE_DAYS


def arrival(order_stage):
	"""``"pickup"``, ``"delivery"`` or None, from a PO's ``custom_order_stage``."""
	if order_stage == ORDER_STAGE_PICKUP:
		return "pickup"
	if order_stage == ORDER_STAGE_DELIVERY:
		return "delivery"
	return None


def _date(value):
	if isinstance(value, _dt.datetime):
		return value.date()
	if isinstance(value, str) and value:
		try:
			return _dt.date.fromisoformat(value[:10])
		except ValueError:
			return None
	return value


def _group(rows, field):
	out = {}
	for row in rows or ():
		out.setdefault(row.get(field) or "", []).append(row)
	return out


def _order_view(order, receipts):
	return {
		"name": order.get("parent"),
		"docstatus": int(flt(order.get("docstatus"))),
		"status": order.get("status"),
		"supplier": order.get("supplier"),
		"supplier_name": order.get("supplier_name") or order.get("supplier"),
		"expected": order.get("schedule_date"),
		"order_stage": order.get("order_stage") or None,
		"arrival": arrival(order.get("order_stage")),
		"receipts": sorted({r.get("parent") for r in receipts if r.get("parent")}),
	}


def _last_receipt(receipts):
	dates = [
		_date(r.get("posting_date"))
		for r in receipts
		if int(flt(r.get("docstatus"))) == 1 and r.get("posting_date")
	]
	return max(dates) if dates else None


def track_lines(requests, orders, receipts, today):
	"""The open requests and orders: one line per Material Request Item, and one per Purchase
	Order Item that no request asked for, each with its stage and whether it still covers
	(``open``: something is still to come). All derived from the document links.

	``requests``: MR Item rows ``{name, parent, item_code, warehouse, stock_qty, schedule_date,
	docstatus, transaction_date, creation}``. ``orders``: PO Item rows ``{name, parent, item_code,
	warehouse, stock_qty, schedule_date, material_request_item, docstatus, status, supplier,
	supplier_name, order_stage}``. ``receipts``: PR Item rows ``{parent, purchase_order_item,
	stock_qty, docstatus, posting_date}``. A Draft request older than :data:`STALE_DRAFT_DAYS` comes
	back as stage ``old_draft``, not open: the board shows it on the item's line, not on On order.
	"""
	by_request = _group(orders, "material_request_item")
	by_order = _group(receipts, "purchase_order_item")
	out = []

	def finish(line, stage, is_open, line_orders, line_receipts, fallback_expected):
		last = _last_receipt(line_receipts)
		if stage != STAGE_OLD_DRAFT and not still_listed(stage, is_open, last, today):
			return
		open_dates = [
			o.get("schedule_date") for o in line_orders if o.get("schedule_date") and not order_is_closed(o)
		]
		line.update(
			{
				"stage": stage,
				"stage_label": STAGE_LABELS[stage],
				"open": is_open,
				"ordered_qty": _sum(line_orders, "stock_qty"),
				"received_qty": _sum(line_receipts, "stock_qty"),
				"last_receipt": last,
				"expected": min(open_dates) if open_dates else fallback_expected,
				"orders": [_order_view(o, by_order.get(o.get("name"), [])) for o in line_orders],
			}
		)
		out.append(line)

	for req in requests or ():
		line_orders = by_request.get(req.get("name"), []) if req.get("name") else []
		line_receipts = [r for o in line_orders for r in by_order.get(o.get("name"), [])]
		if is_stale_draft(req, today):
			stage, is_open = STAGE_OLD_DRAFT, False
		else:
			stage = stage_for_request(req, line_orders, line_receipts)
			is_open = still_to_come(req, line_orders, by_order) > _EPS
		line = {
			"key": line_key(req.get("item_code"), req.get("warehouse")),
			"item_code": req.get("item_code"),
			"warehouse": req.get("warehouse"),
			"request": req.get("parent"),
			"request_docstatus": int(flt(req.get("docstatus"))),
			"requested_on": req.get("transaction_date"),
			"qty": flt(req.get("stock_qty")),
		}
		finish(line, stage, is_open, line_orders, line_receipts, req.get("schedule_date"))

	for order in orders or ():
		if order.get("material_request_item"):
			continue
		line_receipts = by_order.get(order.get("name"), [])
		stage = stage_for_order(order, line_receipts)
		line = {
			"key": line_key(order.get("item_code"), order.get("warehouse")),
			"item_code": order.get("item_code"),
			"warehouse": order.get("warehouse"),
			"request": None,
			"request_docstatus": None,
			"requested_on": None,
			"qty": flt(order.get("stock_qty")),
		}
		finish(
			line,
			stage,
			order_still_to_come(order, by_order) > _EPS,
			[order],
			line_receipts,
			order.get("schedule_date"),
		)

	out.sort(key=lambda l: (STAGES.index(l["stage"]), l.get("item_code") or "", l.get("warehouse") or ""))
	return out


# --- which warehouses a rule counts -----------------------------------------------------


def ancestor_map(parent_map):
	"""``{warehouse: frozenset(its ancestors)}`` from ``{warehouse: parent_warehouse}``."""
	out = {}
	for name in parent_map or {}:
		seen = []
		parent = parent_map.get(name)
		while parent and parent not in seen and len(seen) < 64:
			seen.append(parent)
			parent = parent_map.get(parent)
		out[name] = frozenset(seen)
	return out


def in_scope(warehouse, rule, ancestors) -> bool:
	"""Whether an open line at ``warehouse`` covers ``rule``.

	ERPNext judges a rule with a ``warehouse_group`` on the projected qty of every warehouse under
	that group (``get_item_warehouse_projected_qty`` rolls each Bin up its ancestors), so a request
	for any warehouse in the group is stock that rule is waiting for, once submitted. A Draft for a
	sibling bin counts the same way. A rule with no group counts its own warehouse only.
	"""
	if not warehouse:
		return False
	if warehouse == rule.get("warehouse"):
		return True
	group = rule.get("warehouse_group")
	if not group:
		return False
	return warehouse == group or group in (ancestors or {}).get(warehouse, ())


def assemble(rules, projected_map, tracked, watches, now, ancestors=None):
	"""Every rule as a board line, and the lists the board shows.

	``rules``: ``{item_code, warehouse, warehouse_group, reorder_level, reorder_qty, ...}`` (the
	extra keys -- item_name, uom, supplier -- are carried through). ``tracked``: from
	:func:`track_lines`. ``watches``: ``{key: Reorder Watch row}``. ``ancestors``: from
	:func:`ancestor_map`. Returns ``{"lines", "needs", "snoozed"}``: a line is *covered* when an
	open tracked line for its item sits in its scope (:func:`in_scope`), and only an uncovered line
	below its point needs reordering. ``old_drafts`` names the stale Drafts in its scope.
	"""
	by_item = {}
	for t in tracked or ():
		by_item.setdefault(t.get("item_code"), []).append(t)
	lines, needs, snoozed = [], [], []
	for rule in rules or ():
		key = line_key(rule.get("item_code"), rule.get("warehouse"))
		projected = projected_for_rule(
			projected_map, rule.get("item_code"), rule.get("warehouse"), rule.get("warehouse_group")
		)
		below = is_below(rule.get("reorder_level"), rule.get("reorder_qty"), projected)
		watch = (watches or {}).get(key) or {}
		mine = [
			t for t in by_item.get(rule.get("item_code"), []) if in_scope(t.get("warehouse"), rule, ancestors)
		]
		line = dict(rule)
		line.update(
			{
				"key": key,
				"projected_qty": projected,
				"below": below,
				"suggested_qty": suggested_qty(rule.get("reorder_level"), rule.get("reorder_qty"), projected),
				"covered_by": sorted(
					{
						t.get("request") or (t["orders"][0]["name"] if t.get("orders") else None) or ""
						for t in mine
						if t.get("open")
					}
					- {""}
				),
				"old_drafts": sorted(
					{t.get("request") for t in mine if t.get("stage") == STAGE_OLD_DRAFT} - {None}
				),
				"below_since": watch.get("below_since") if below else None,
				"snooze": snooze_state(watch, projected, now) if below else None,
				"snooze_kind": watch.get("snooze_kind"),
				"snoozed_until": watch.get("snoozed_until"),
				"snooze_projected_qty": watch.get("snooze_projected_qty"),
				"snoozed_by": watch.get("snoozed_by"),
				"snoozed_on": watch.get("snoozed_on"),
				"snooze_reason": watch.get("snooze_reason"),
			}
		)
		line["covered"] = any(t.get("open") for t in mine)
		lines.append(line)
		if not below or line["covered"]:
			continue
		(snoozed if line["snooze"] == "active" else needs).append(line)
	order = lambda l: (l.get("item_code") or "", l.get("warehouse") or "")  # noqa: E731
	needs.sort(key=order)
	snoozed.sort(key=order)
	return {"lines": lines, "needs": needs, "snoozed": snoozed}


def sync_plan(lines, watches, now):
	"""What the hourly sync writes to Reorder Watch, as ``(inserts, updates, deletes)``.

	* A line below its point with no row gets one, ``below_since`` now (how long it has been below,
	  to the hour).
	* A row whose line is no longer below (restocked, a submitted request raised its projected qty,
	  the rule removed) is deleted: if it falls again, that is news again.
	* A row whose snooze has ended is cleared and loses ``alerted_on``, so the next digest names it
	  again under "Snooze ended".
	"""
	inserts, updates, deletes = [], [], []
	below = {l["key"]: l for l in lines or () if l.get("below")}
	for key, watch in (watches or {}).items():
		if key not in below:
			deletes.append(key)
			continue
		if snooze_state(watch, below[key].get("projected_qty"), now) == "ended":
			fields = cleared_snooze()
			fields.update({"alerted_on": None, "snooze_ended_on": now})
			updates.append((key, fields))
	for key, line in below.items():
		if key not in (watches or {}):
			inserts.append(
				{
					"key": key,
					"item_code": line.get("item_code"),
					"warehouse": line.get("warehouse"),
					"below_since": now,
				}
			)
	return inserts, updates, deletes


# --- snoozes -------------------------------------------------------------------------


def add_months(when, months):
	"""``when`` plus whole months, the day clamped to the month's length (31 Jan + 1 = 28/29 Feb)."""
	month_index = when.month - 1 + months
	year = when.year + month_index // 12
	month = month_index % 12 + 1
	next_month = _dt.date(year + (month == 12), month % 12 + 1, 1)
	last_day = (next_month - _dt.timedelta(days=1)).day
	return when.replace(year=year, month=month, day=min(when.day, last_day))


def snooze_fields(kind, projected_qty, now, reason=None, user=None):
	"""``(fields, error)``: what a snooze of ``kind`` writes on the line's Reorder Watch row."""
	if kind not in SNOOZE_KINDS:
		return None, "Choose how long to snooze it."
	fields = {
		"snooze_kind": kind,
		"snoozed_until": None,
		"snooze_projected_qty": None,
		"snoozed_by": user,
		"snoozed_on": now,
		"snooze_reason": (reason or "").strip()[:500] or None,
		"snooze_ended_on": None,
	}
	if kind == SNOOZE_WEEK:
		fields["snoozed_until"] = now + _dt.timedelta(days=7)
	elif kind == SNOOZE_MONTH:
		fields["snoozed_until"] = add_months(now, 1)
	else:
		fields["snooze_projected_qty"] = flt(projected_qty)
	return fields, None


def cleared_snooze():
	"""The fields an un-snooze (or an ended snooze) writes."""
	return {
		"snooze_kind": None,
		"snoozed_until": None,
		"snooze_projected_qty": None,
		"snoozed_by": None,
		"snoozed_on": None,
		"snooze_reason": None,
	}


def snooze_state(watch, projected_qty, now):
	"""``"active"``, ``"ended"`` or None for a line's watch row.

	A dated snooze ends when its time is up; "until stock drops further" ends once the projected
	qty is *below* the qty it was snoozed at (equal is not further).
	"""
	if not watch:
		return None
	kind = watch.get("snooze_kind")
	if not kind:
		return None
	if kind == SNOOZE_DROP:
		snapshot = watch.get("snooze_projected_qty")
		if snapshot is None:
			return "ended"
		return "ended" if flt(projected_qty) < flt(snapshot) - _EPS else "active"
	until = watch.get("snoozed_until")
	if not until:
		return "ended"
	return "ended" if now >= until else "active"


# --- the digest ----------------------------------------------------------------------


def digest_pick(lines, watches):
	"""The Needs-reorder lines the next digest names, as ``(newly_below, snooze_ended)``.

	``lines``: the board's Needs reorder lines (below, nothing open covering them), each with
	``key``. ``watches``: ``{key: watch row}``. A line is named once: when its row has no
	``alerted_on``. A snoozed line is never named while the snooze holds; one whose snooze ended
	is named again, under its own heading, so "remember it later" actually reminds.
	"""
	newly, ended = [], []
	for line in lines:
		watch = (watches or {}).get(line.get("key"))
		if not watch or watch.get("alerted_on"):
			continue
		if line.get("snooze") == "active":
			continue
		(ended if watch.get("snooze_ended_on") else newly).append(line)
	return newly, ended


def filter_recipients(users, excluded=EXCLUDED_RECIPIENTS):
	"""Enabled System Users, minus Frappe's own accounts and the service account, de-duplicated.

	``users``: rows ``{name, email, enabled, user_type}``. Returns the names, in order.
	"""
	out, seen = [], set()
	for user in users or ():
		name = (user.get("name") or "").strip()
		if not name or name.lower() in excluded or name.lower() in seen:
			continue
		if not int(flt(user.get("enabled"))) or (user.get("user_type") or "System User") != "System User":
			continue
		if not user.get("email") and "@" not in name:
			continue
		seen.add(name.lower())
		out.append(name)
	return out


_CLIENT_REF = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def valid_client_ref(value) -> bool:
	"""A page-minted request id: 8-64 letters, digits, ``-`` or ``_``."""
	return bool(_CLIENT_REF.match(str(value or "")))
