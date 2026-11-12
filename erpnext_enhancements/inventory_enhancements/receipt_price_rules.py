# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A price is required when a stock item is received (v1.803.0). No Frappe, no I/O.

Nik's decision of 2026-11-12. Stock comes in at the price on its receipt line, so a $0 line puts
the part on the shelf valued at nothing, and every job it is later issued to is charged nothing
for it. 373 of the 543 Purchase Order lines dated in the 180 days before the decision had a rate
of 0. Until now that only cost a warning on the order (``po_price_check``, v1.532.0) and ERPNext's
own orange toast on the receipt (``StockController.check_zero_rate``, which never refuses).

**The rule.** A Purchase Receipt line, or a Purchase Invoice line when the invoice updates stock,
is *unpriced* when its item keeps stock, its ``rate`` is below half a cent, and it is not marked
as free on purpose. An unpriced line refuses the **submit**. A draft still saves, with a warning,
because the packing-slip and vendor-bill intake make drafts for a person to finish.

**Free on purpose** is ERPNext's own tick, ``allow_zero_valuation_rate`` ("Allow Zero Valuation
Rate") on the line: a free sample, a warranty replacement. ERPNext sets it itself for a
customer-provided item. A pricing rule's free item (``is_free_item``) counts too. No new field.

**What is left alone.** A return (its rate comes from the receipt it returns), an internal
transfer between companies (its rate is the sender's valuation), a non-stock line, and every
document already submitted: nothing here changes a rate on an existing receipt.

**Where the price goes.** Buying Settings keeps *Maintain Same Rate Throughout the Purchase Cycle*
on, with the action Stop, so a receipt line's rate must equal its order line's
(``TransactionBase.validate_rate_with_reference_doc``, v16.54.0). A receipt against an order
therefore cannot be priced on the receipt: the price goes on the order line, through ERPNext's own
*Update Items*, and the receipt takes it from there. :func:`price_changes` builds the rows
*Update Items* wants. The Receive Items dialog and the Stock Scan page ask for the price and do
that for a person who may edit the order; anyone else is told who to ask.

The reads and the hooks are ``receipt_price.py``. Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

#: A rate below half a cent rounds to $0.00 on the receipt. The same line as ``po_price_check``.
ZERO_RATE_BELOW = 0.005

#: The most a typed unit price may be. A guard against a slipped decimal on a phone, not a policy:
#: a dearer part is priced on the order's own form.
MAX_UNIT_PRICE = 100000.0

#: ERPNext's field for a line that is free on purpose, and its label on the form.
FREE_FIELD = "allow_zero_valuation_rate"
FREE_LABEL = "Allow Zero Valuation Rate"


def number(value):
	"""A float from whatever a row carries. Unreadable is 0: a rate nobody can read is no price."""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _get(row, key):
	getter = getattr(row, "get", None)
	if callable(getter):
		return getter(key)
	return getattr(row, key, None)


def _truthy(value):
	try:
		return bool(int(float(value or 0)))
	except (TypeError, ValueError):
		return bool(value)


def is_unpriced(row, stock_items):
	"""Whether one line is a stock item's line with no price and no "free on purpose" mark."""
	code = _get(row, "item_code")
	if not code or code not in stock_items:
		return False
	if _truthy(_get(row, FREE_FIELD)) or _truthy(_get(row, "is_free_item")):
		return False
	return abs(number(_get(row, "rate"))) < ZERO_RATE_BELOW


def applies(doctype, is_return=False, is_internal=False, update_stock=True):
	"""Whether the rule judges this document at all."""
	if is_return or is_internal:
		return False
	if doctype == "Purchase Receipt":
		return True
	return doctype == "Purchase Invoice" and bool(update_stock)


def unpriced_lines(rows, stock_items, order_field="purchase_order"):
	"""The unpriced lines, as ``[{"idx", "item_code", "name", "order"}]``.

	``order_field`` is where the row names its Purchase Order (``purchase_order`` on both a
	receipt and an invoice line). ``idx`` falls back to the row's position.
	"""
	stock_items = set(stock_items or ())
	out = []
	for position, row in enumerate(rows or (), start=1):
		if not is_unpriced(row, stock_items):
			continue
		out.append(
			{
				"idx": _get(row, "idx") or position,
				"item_code": _get(row, "item_code"),
				"name": _get(row, "name") or "",
				"order": _get(row, order_field) or "",
			}
		)
	return out


def _rows(lines):
	return ", ".join(f"row {line['idx']} ({line['item_code']})" for line in lines)


def _first_upper(text):
	return text[:1].upper() + text[1:]


def _orders(lines):
	return sorted({line["order"] for line in lines if line.get("order")})


def what_to_do(lines):
	"""The "what to do" sentences, shared by the refusal and the warning."""
	orders = _orders(lines)
	loose = [line for line in lines if not line.get("order")]
	parts = []
	if orders:
		named = ", ".join(orders)
		parts.append(
			f"Put the price on Purchase Order {named} with Update Items, then set the same Rate "
			"here: a receipt's price has to match its order's."
		)
	if loose:
		parts.append(
			"Enter the price paid in Rate."
			if not orders
			else f"For {_rows(loose)}, enter the price paid in Rate."
		)
	parts.append(
		f"If it really cost nothing (a free sample or a warranty replacement), tick {FREE_LABEL} "
		"on that row."
	)
	return " ".join(parts)


def submit_refusal(doctype, lines):
	"""Why the document can't be submitted, naming each line and what to do."""
	what = "This receipt" if doctype == "Purchase Receipt" else "This invoice"
	has = "has" if len(lines) == 1 else "have"
	return (
		f"{what} can't be submitted. {_first_upper(_rows(lines))} {has} no price. Stock comes in at "
		"the price on this line, so it would sit on the shelf valued at $0 and any job it goes to "
		f"would be charged nothing for it. {what_to_do(lines)}"
	)


def draft_warning(doctype, lines):
	"""The orange message on a draft: it saved, and it will not submit like this."""
	what = "This receipt" if doctype == "Purchase Receipt" else "This invoice"
	has = "has" if len(lines) == 1 else "have"
	return (
		f"{what} is saved, but it can't be submitted yet. {_first_upper(_rows(lines))} {has} no "
		f"price. {what_to_do(lines)}"
	)


# ---------------------------------------------------------------------------
# Receiving against an order: the Receive Items dialog and the Stock Scan page
# ---------------------------------------------------------------------------


def order_lines_needing_price(order_lines, stock_items, wanted=None):
	"""The order lines that would make an unpriced receipt line.

	``order_lines`` are dicts with ``name``, ``idx``, ``item_code``, ``item_name``, ``rate``,
	``uom`` and ``is_free_item``; ``wanted`` limits it to the lines being received now.
	"""
	stock_items = set(stock_items or ())
	wanted = None if wanted is None else set(wanted)
	out = []
	for line in order_lines or ():
		if wanted is not None and line.get("name") not in wanted:
			continue
		if not is_unpriced(line, stock_items):
			continue
		out.append(
			{
				"purchase_order_item": line.get("name"),
				"idx": line.get("idx"),
				"item_code": line.get("item_code"),
				"item_name": line.get("item_name") or "",
				"uom": line.get("uom") or "",
			}
		)
	return out


def checked_price(value):
	"""``(rate, problem)`` for a price a person typed. ``problem`` is words, or ``None``."""
	rate = number(value)
	if rate < ZERO_RATE_BELOW:
		return None, "Enter a price above zero."
	if rate > MAX_UNIT_PRICE:
		return None, (
			f"{rate:,.2f} each is more than this page takes. Put the price on the purchase order "
			"itself with Update Items."
		)
	return round(rate, 6), None


def settle(needing, typed):
	"""Split the lines that need a price by what the person sent for each.

	``typed`` is ``{order line: {"rate": x, "no_charge": bool}}``. Returns
	``(prices, free, missing, problems)``: ``{order line: rate}`` to put on the order, the lines
	marked free, the lines still without an answer, and what was wrong with a typed price.
	"""
	prices, free, missing, problems = {}, [], [], []
	for line in needing:
		answer = (typed or {}).get(line["purchase_order_item"]) or {}
		if _truthy(answer.get("no_charge")):
			free.append(line["purchase_order_item"])
			continue
		if answer.get("rate") in (None, ""):
			missing.append(line)
			continue
		rate, problem = checked_price(answer.get("rate"))
		if problem:
			problems.append(f"Row {line['idx']} ({line['item_code']}): {problem}")
			continue
		prices[line["purchase_order_item"]] = rate
	return prices, free, missing, problems


def receive_refusal(order, lines):
	"""Why nothing was received: these order lines have no price, and what to send instead."""
	has = "has" if len(lines) == 1 else "have"
	rows = ", ".join(f"row {line['idx']} ({line['item_code']})" for line in lines)
	return (
		f"Nothing was received. On {order}, {rows} {has} no price, and stock comes in at the price "
		"on the order. Enter what each one cost, or mark it as no charge if it was free (a sample "
		"or a warranty replacement), then receive again."
	)


def cannot_price_refusal(order):
	"""For a person who may receive but may not edit the order."""
	return (
		f"You can receive against {order}, but you can't set prices on it. Ask purchasing to put "
		"the price on the order (Update Items), then receive this again."
	)


def price_changes(order_rows, prices):
	"""The rows ERPNext's *Update Items* (``update_child_qty_rate``) wants, with ``prices`` applied.

	Every row of the order must be sent: a row left out is **deleted**. Each row carries exactly
	what the order already holds, so ERPNext sees it as unchanged and skips it, except the rows in
	``prices``, whose ``rate`` is the new price. ``description`` is sent because ERPNext writes
	whatever it is given, and a missing one would blank the line.
	"""
	out = []
	for row in order_rows or ():
		name = _get(row, "name")
		date = _get(row, "schedule_date")
		entry = {
			"docname": name,
			"name": name,
			"idx": _get(row, "idx"),
			"item_code": _get(row, "item_code"),
			"qty": number(_get(row, "qty")),
			"rate": prices[name] if name in prices else number(_get(row, "rate")),
			"uom": _get(row, "uom"),
			"conversion_factor": number(_get(row, "conversion_factor")) or 1.0,
			"schedule_date": str(date) if date else None,
			"description": _get(row, "description"),
		}
		for extra in ("fg_item", "fg_item_qty"):
			if _get(row, extra):
				entry[extra] = _get(row, extra)
		out.append(entry)
	return out


def price_comment(full_name, rows):
	"""The Comment left on the order: who priced which rows while receiving. No amounts."""
	return f"{full_name} entered the price for row {rows} while receiving it."
