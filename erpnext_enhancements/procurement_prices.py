"""What the Procurement Tracker says things cost (v1.814.0).

Pure functions, no ``frappe`` import, for the reason ``procurement_quantities`` gives:
``project_enhancements/__init__.py`` imports ``frappe``, so nothing under it can be tested
without a bench, and this is the part worth testing. ``tests/test_procurement_prices.py`` runs it
in CI.

Nik, 2026-11-12: *"on the procurement tracker, there should be another column for the price of
the item we spent."* This is one reading of that, and it can be corrected. The decisions in it:

**The only price ERPNext holds is the purchase order line's rate.** Bills are entered in
QuickBooks, so there are no Purchase Invoices to read a price from. Nothing here is called
*billed* or *paid*. **Spent** is what was **ordered** for this job: the sum of the submitted
purchase order lines' own ``base_amount`` (company currency), never recomputed from a rate and a
quantity. **Received value** is what has arrived at the order line's rate (received quantity times
``base_rate``): a second figure beside Spent, not a replacement for it.

**A missing price is not a zero.** A line whose rate is 0 is *no price*, left out of every sum, and
a sum that leaves lines out says how many. A line ERPNext itself marks free (``is_free_item``, or
``allow_zero_valuation_rate`` on a receipt line) is *no charge*: deliberately free, also left out
of the sum, but not something purchasing has to fix. On 2026-11-12, 373 of the last 543 submitted
purchase order lines had a rate of 0, because prices were often left off orders.

**A quote is not a spend.** A Supplier Quotation line's rate is carried as a quote, on its own
keys, and is never added into Spent. A Material Request or a Request for Quotation has no price.

**A draft is not an order**, the rule ``procurement_quantities`` already holds for quantities. A
draft order's amount is carried as ``draft_spent`` and left out of ``spent``.

**One order line is counted once.** A request line split across two orders reaches the feed once
per order (the ``OR`` join), and the quantity de-duplication keys on the *request* line, which is
the wrong grain for money: two orders are two prices. Everything here keys on the order line.

**The keys exist only for a person who may read Purchase Orders.** ``price_keys`` and friends are
only called when the feed was asked for prices; without that the keys are not on the row at all.

Indentation is tabs (new file).
"""

PRICED = "priced"
NO_PRICE = "no_price"
NO_CHARGE = "no_charge"

#: Every key a feed row carries when the person may see prices. ``None`` is "nothing to say here",
#: which is not the same fact as "no price": a request line has no price to be missing.
PRICE_KEYS = (
	# The order line behind the row (None when nothing is on a live order).
	"price_state",
	"price_docstatus",
	"po_rate",
	"po_amount",
	"po_unit_rate",
	"po_qty",
	"po_rate_uom",
	"received_value",
	# The receipt line behind the row, valued at the order line's rate.
	"receipt_item",
	"receipt_state",
	"receipt_rate",
	"receipt_value",
	# The quotation line behind the row: a quote, never a spend.
	"sq_item",
	"quote_rate",
	"quote_unit_rate",
	"quote_amount",
)


def _num(value):
	"""A number, or ``None`` for blank / not a number. ``0`` stays ``0``: it is a value."""
	if value is None or value == "":
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def _money(value):
	"""Sums of 2-decimal amounts, with the float noise taken off."""
	return round(float(value or 0.0), 6)


def empty_price_keys():
	return {key: None for key in PRICE_KEYS}


def line_price_state(rate, free=False):
	"""``priced`` for a rate above zero, ``no_charge`` for a zero rate ERPNext itself calls free,
	``no_price`` for any other zero or blank rate."""
	value = _num(rate)
	if value is not None and value > 0:
		return PRICED
	return NO_CHARGE if free else NO_PRICE


def _per_stock_unit(rate, factor):
	value = _num(rate)
	if value is None:
		return None
	return value / (_num(factor) or 1.0)


def _order_keys(rate, base_rate, base_amount, qty, factor, free, uom, docstatus):
	state = line_price_state(rate, free)
	priced = state == PRICED
	return {
		"price_state": state,
		"price_docstatus": docstatus,
		# A line with no price carries no figures: a 0 here would be read as a price.
		"po_rate": _num(base_rate) if priced else None,
		"po_amount": _num(base_amount) if priced else None,
		"po_unit_rate": _per_stock_unit(base_rate, factor) if priced else None,
		"po_qty": _num(qty),
		"po_rate_uom": uom,
	}


def price_keys(row, orders=True, quotes=True):
	"""The price keys for one chain row, from the columns the feed's query selected.

	``row`` is a record of ``project_enhancements._chain_rows``'s query. An order line that is not
	on a live order (the order was cancelled, so its join came back empty) has no price: the
	feed's own rule is that a cancelled order is not part of the chain.
	"""
	out = empty_price_keys()
	if orders and row.get("po_name") and row.get("po_item_name"):
		out.update(
			_order_keys(
				row.get("po_line_rate"),
				row.get("po_line_base_rate"),
				row.get("po_line_base_amount"),
				row.get("po_line_qty"),
				row.get("po_conversion_factor"),
				row.get("po_line_free"),
				row.get("po_line_uom"),
				row.get("po_docstatus"),
			)
		)
		received = _num(row.get("po_line_received_qty"))
		if out["po_rate"] is not None and received is not None:
			out["received_value"] = _money(received * out["po_rate"])
		if row.get("pr_name") and row.get("pr_item_name"):
			# What this receipt brought in, valued at the ORDER line's rate. The receipt line's own
			# rate is the one copied at the time, which is 0 exactly where the price was left off
			# the order and added later (update_child_qty_rate changes the order, not the receipt).
			out["receipt_item"] = row.get("pr_item_name")
			out["receipt_state"] = out["price_state"]
			out["receipt_rate"] = out["po_rate"]
			qty = _num(row.get("pr_line_qty"))
			if out["po_rate"] is not None and qty is not None:
				out["receipt_value"] = _money(qty * out["po_rate"])
	if quotes and row.get("sq_name") and row.get("sq_item_name"):
		quoted = _num(row.get("sq_line_rate"))
		if quoted is not None and quoted > 0:
			out["sq_item"] = row.get("sq_item_name")
			out["quote_rate"] = _num(row.get("sq_line_base_rate"))
			out["quote_amount"] = _num(row.get("sq_line_base_amount"))
			out["quote_unit_rate"] = _per_stock_unit(row.get("sq_line_base_rate"), row.get("sq_line_conversion_factor"))
	return out


def minimal_price_keys(doctype, docstatus, item, project=None, orders=True, quotes=True):
	"""The price keys for a line of a document that never joined a chain (the feed's sweep).

	``item`` is the child row. A line that names a *different* job is another job's money and has
	no price here, however the document found its way into this job's list.
	"""
	out = empty_price_keys()
	other = item.get("project")
	if project and other and other != project:
		return out
	if doctype == "Purchase Order" and orders:
		out.update(
			_order_keys(
				item.get("rate"),
				item.get("base_rate"),
				item.get("base_amount"),
				item.get("qty"),
				item.get("conversion_factor"),
				item.get("is_free_item"),
				item.get("uom"),
				docstatus,
			)
		)
	elif doctype == "Purchase Receipt" and orders:
		# No order behind it (a store run): the receipt's own line is all there is to read.
		free = item.get("is_free_item") or item.get("allow_zero_valuation_rate")
		state = line_price_state(item.get("rate"), free)
		out["receipt_item"] = item.get("name")
		out["receipt_state"] = state
		if state == PRICED:
			out["receipt_rate"] = _num(item.get("base_rate"))
			out["receipt_value"] = _num(item.get("base_amount"))
	elif doctype == "Supplier Quotation" and quotes:
		quoted = _num(item.get("rate"))
		if quoted is not None and quoted > 0:
			out["sq_item"] = item.get("name")
			out["quote_rate"] = _num(item.get("base_rate"))
			out["quote_amount"] = _num(item.get("base_amount"))
			out["quote_unit_rate"] = _per_stock_unit(item.get("base_rate"), item.get("conversion_factor"))
	return out


# ---------------------------------------------------------------------------
# By item
# ---------------------------------------------------------------------------

#: What an item row carries when the person may see prices. ``None`` for the money of an item that
#: has no priced order line, never 0.
ITEM_PRICE_KEYS = (
	"spent",
	"spent_lines",
	"no_price_lines",
	"no_charge_lines",
	"draft_spent",
	"draft_no_price_lines",
	"unit_price_low",
	"unit_price_high",
	"unit_price_avg",
	"price_uoms",
	"received_value",
	"quote_low",
	"quote_high",
	"quote_count",
)


def _distinct(rows, key_names):
	"""The first row for each distinct line. ``key_names`` are the row keys that name the line,
	tried in order; a row with none of them is its own line (the sweep's rows)."""
	seen = {}
	for index, row in enumerate(rows):
		key = None
		for name in key_names:
			if row.get(name):
				key = (name, row[name])
				break
		seen.setdefault(key if key is not None else ("__row__", row.get("row_id") or index), row)
	return list(seen.values())


def item_spending(rows):
	"""The ``ITEM_PRICE_KEYS`` of one item, from every row of that item.

	``rows`` are the item's feed rows (the by-item grouping hands them all over, quotes and bills
	included: only the rows that carry an order line or a quote line count here). The order lines
	are de-duplicated on the order line, not on the request line (see the module docstring).
	"""
	orders = _distinct([r for r in rows if r.get("price_state")], ("po_item",))
	live = [r for r in orders if r.get("price_docstatus") == 1]
	drafts = [r for r in orders if r.get("price_docstatus") == 0]

	priced = [r for r in live if r["price_state"] == PRICED and r.get("po_amount") is not None]
	spent = _money(sum(r["po_amount"] for r in priced))
	unit_prices = [r["po_unit_rate"] for r in priced if r.get("po_unit_rate") is not None]

	# What was spent over what was ordered (in the stock unit), so one big line at one price
	# outweighs a small one at another. Only lines that say how many were ordered take part.
	money, qty_total = 0.0, 0.0
	for r in priced:
		qty = _num(r.get("po_qty"))
		if qty:
			money += r["po_amount"]
			qty_total += qty * (_num(r.get("conversion_factor")) or 1.0)
	if qty_total:
		average = money / qty_total
	elif unit_prices:
		average = sum(unit_prices) / len(unit_prices)
	else:
		average = None

	draft_priced = [r for r in drafts if r["price_state"] == PRICED and r.get("po_amount") is not None]

	quotes = _distinct([r for r in rows if r.get("sq_item")], ("sq_item",))
	quote_prices = [q["quote_unit_rate"] for q in quotes if q.get("quote_unit_rate") is not None]

	return {
		"spent": spent if priced else None,
		"spent_lines": len(priced),
		"no_price_lines": sum(1 for r in live if r["price_state"] == NO_PRICE),
		"no_charge_lines": sum(1 for r in live if r["price_state"] == NO_CHARGE),
		"draft_spent": _money(sum(r["po_amount"] for r in draft_priced)) if draft_priced else None,
		"draft_no_price_lines": sum(1 for r in drafts if r["price_state"] == NO_PRICE),
		"unit_price_low": min(unit_prices) if unit_prices else None,
		"unit_price_high": max(unit_prices) if unit_prices else None,
		"unit_price_avg": average,
		# The units the priced lines were written in. ERPNext's conversion factor is 1 on every line
		# on production, even where one line is priced per FT and another per Unit, so a range can be
		# two prices for two things; the tracker says so when the units differ.
		"price_uoms": sorted({r["po_rate_uom"] for r in priced if r.get("po_rate_uom")}),
		"received_value": _money(sum(r.get("received_value") or 0.0 for r in priced)) if priced else None,
		"quote_low": min(quote_prices) if quote_prices else None,
		"quote_high": max(quote_prices) if quote_prices else None,
		"quote_count": len(quotes),
	}


def spent_total(items):
	"""``{"spent", "spent_lines", "no_price_lines", "no_charge_lines"}`` across by-item rows: the
	figure under the tracker's by-item table, and what the assistant tool reports."""
	items = list(items or ())
	return {
		"spent": _money(sum(i.get("spent") or 0.0 for i in items)),
		"spent_lines": sum(i.get("spent_lines") or 0 for i in items),
		"no_price_lines": sum(i.get("no_price_lines") or 0 for i in items),
		"no_charge_lines": sum(i.get("no_charge_lines") or 0 for i in items),
	}


# ---------------------------------------------------------------------------
# By document
# ---------------------------------------------------------------------------

#: The key that names each kind of line, and the state and amount a document of that kind totals.
_DOCUMENT_TOTALS = {
	"Purchase Order": ("po_item", "price_state", "po_amount"),
	"Purchase Receipt": ("receipt_item", "receipt_state", "receipt_value"),
	"Supplier Quotation": ("sq_item", None, "quote_amount"),
}


def document_total(doctype, rows):
	"""This document's total for the job's lines: ``{"amount", "lines", "no_price_lines",
	"no_charge_lines", "job_lines"}``, or ``None`` for a document that carries no price (a request,
	a request for quotation, an invoice).

	A purchase order is its lines' ``base_amount``; a receipt is what arrived at the *order's* rate;
	a quotation is what it quoted (``amount`` there is a quote, never a spend, and the caller labels
	it so). A line with no price is counted and left out of ``amount``.
	"""
	spec = _DOCUMENT_TOTALS.get(doctype)
	if not spec:
		return None
	id_key, state_key, amount_key = spec
	lines = _distinct([r for r in rows if (r.get(state_key) if state_key else r.get(id_key))], (id_key,))
	if not lines:
		return None
	amount, priced, no_price, no_charge = 0.0, 0, 0, 0
	for r in lines:
		state = r.get(state_key) if state_key else PRICED
		value = r.get(amount_key)
		if state == NO_PRICE:
			no_price += 1
		elif state == NO_CHARGE:
			no_charge += 1
		elif value is not None:
			amount += value
			priced += 1
		else:
			no_price += 1
	return {
		"amount": _money(amount),
		"lines": priced,
		"no_price_lines": no_price,
		"no_charge_lines": no_charge,
		"job_lines": len(lines),
	}
