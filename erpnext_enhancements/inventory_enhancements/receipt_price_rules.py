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

**A Stock Entry of type Material Receipt is judged too**, on its ``basic_rate``. ERPNext does not
let a typed $0 through as such: ``StockEntry.set_basic_rate`` replaces it with the item's last
valuation in that warehouse (``stock_ledger.get_valuation_rate``, v16.54.0), and refuses when it
finds none. But that lookup accepts a last valuation of exactly 0, so an item whose stock is
already valued at nothing (55 receipt lines came in at $0 on production before this rule) can be
received again at $0, in silence. The rule refuses that row at submit. Stock Scan's own "add
without a purchase order" always posts a cost above zero (``_receipt_cost``), so it passes.
A Stock Reconciliation is not judged: it states a count, and refusing it would stop a count of
an item that is valued at nothing today.

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
that for a person who may price the order.

**Who may price, who may say "free", and what happens to everyone else.** Pricing needs write on
the order, and the person must not be the one who raised the Material Request it fills: *Update
Items* re-runs this app's separation-of-duties gate, which refuses them (:data:`BLOCKS`).
Marking a line free is held to the same people, and needs a reason
(:data:`NO_CHARGE_NEEDS_ORDER_WRITE`, :data:`NO_CHARGE_NEEDS_REASON`). **Both are the manager's
recommended defaults, pending Nik's answer on 2026-11-12; each is one constant to flip.** Without
them, "no charge" would be one tap past the refusal for anyone who can receive, and stock would
come in at $0 with only a Comment to show for it.

A receiver who can do neither does not get a $0 receipt and is not stuck either: the line is
**left unreceived**, the rest of the delivery is received as usual, and purchasing gets a ToDo on
the order naming the rows that need a price (:func:`settle`, ``receipt_price.ask_purchasing``).

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

#: PENDING NIK (2026-11-12), the manager's recommended defaults. On the two receiving screens,
#: only a person who may price the order may mark a line "no charge", and must say why. Set the
#: first to False and any receiver may mark a line free again; set the second to False and the
#: reason becomes optional. The Purchase Receipt form's own tick is ERPNext's and is not touched.
NO_CHARGE_NEEDS_ORDER_WRITE = True
NO_CHARGE_NEEDS_REASON = True

#: Why a person may not price an order, as ``receipt_price.price_block`` names it.
BLOCK_NO_WRITE = "no_write"
BLOCK_REQUESTER = "requester"
BLOCKS = (BLOCK_NO_WRITE, BLOCK_REQUESTER)

#: How a ToDo that asks purchasing for a price begins. Also how an open one is recognized, so
#: the same order is not asked about twice.
TODO_MARKER = "Price needed"

#: The Stock Entry purpose that brings stock in from nowhere.
MATERIAL_RECEIPT = "Material Receipt"


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


def is_unpriced(row, stock_items, rate_field="rate"):
	"""Whether one line is a stock item's line with no price and no "free on purpose" mark."""
	code = _get(row, "item_code")
	if not code or code not in stock_items:
		return False
	if _truthy(_get(row, FREE_FIELD)) or _truthy(_get(row, "is_free_item")):
		return False
	return abs(number(_get(row, rate_field))) < ZERO_RATE_BELOW


def applies(doctype, is_return=False, is_internal=False, update_stock=True, purpose=None):
	"""Whether the rule judges this document at all."""
	if doctype == "Stock Entry":
		return purpose == MATERIAL_RECEIPT
	if is_return or is_internal:
		return False
	if doctype == "Purchase Receipt":
		return True
	return doctype == "Purchase Invoice" and bool(update_stock)


def rate_field(doctype):
	"""The field that carries a line's price: ``basic_rate`` on a Stock Entry, ``rate`` elsewhere."""
	return "basic_rate" if doctype == "Stock Entry" else "rate"


def incoming_rows(doctype, rows):
	"""The rows that bring stock in. On a Stock Entry that is a row with a target warehouse and
	no source; on a receipt or an invoice, every row."""
	if doctype != "Stock Entry":
		return list(rows or ())
	return [row for row in rows or () if _get(row, "t_warehouse") and not _get(row, "s_warehouse")]


def unpriced_lines(rows, stock_items, order_field="purchase_order", rate_field="rate"):
	"""The unpriced lines, as ``[{"idx", "item_code", "name", "order"}]``.

	``order_field`` is where the row names its Purchase Order (``purchase_order`` on both a
	receipt and an invoice line). ``idx`` falls back to the row's position.
	"""
	stock_items = set(stock_items or ())
	out = []
	for position, row in enumerate(rows or (), start=1):
		if not is_unpriced(row, stock_items, rate_field=rate_field):
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


def stock_entry_refusal(lines):
	"""Why a Material Receipt can't be submitted: a row would come into stock at $0."""
	would = "would" if len(lines) > 1 else "would"
	return (
		f"This stock entry can't be submitted. {_first_upper(_rows(lines))} {would} come into stock "
		"at $0: no cost was entered, and the stock it already has is valued at nothing. Enter what "
		f"one cost in Basic Rate. If it really cost nothing, tick {FREE_LABEL} on that row."
	)


def submit_refusal(doctype, lines):
	"""Why the document can't be submitted, naming each line and what to do."""
	if doctype == "Stock Entry":
		return stock_entry_refusal(lines)
	what = "This receipt" if doctype == "Purchase Receipt" else "This invoice"
	has = "has" if len(lines) == 1 else "have"
	return (
		f"{what} can't be submitted. {_first_upper(_rows(lines))} {has} no price. Stock comes in at "
		"the price on this line, so it would sit on the shelf valued at $0 and any job it goes to "
		f"would be charged nothing for it. {what_to_do(lines)}"
	)


def draft_warning(doctype, lines):
	"""The orange message on a draft: it saved, and it will not submit like this."""
	if doctype == "Stock Entry":
		return stock_entry_refusal(lines).replace(
			"This stock entry can't be submitted.",
			"This stock entry is saved, but it can't be submitted yet.",
		)
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


def settle(needing, typed, can_price=True, can_mark_free=True):
	"""Split the lines that need a price by what the person sent for each, and may do.

	``typed`` is ``{order line: {"rate": x, "no_charge": bool, "reason": text}}``. Returns a dict:

	* ``prices``: ``{order line: rate}`` to put on the order (only for a person who may price);
	* ``free``: ``[(order line, reason)]`` marked free on purpose (only for a person who may);
	* ``missing``: lines a person who *may* price left without an answer: they are asked again;
	* ``problems``: what was wrong with a typed price, or a free line with no reason;
	* ``held``: lines this person can do nothing about. They are left unreceived, and purchasing
	  is asked for the price. An answer sent for one of them is ignored, not obeyed.
	"""
	out = {"prices": {}, "free": [], "missing": [], "problems": [], "held": []}
	for line in needing:
		name = line["purchase_order_item"]
		answer = (typed or {}).get(name) or {}
		where = f"Row {line['idx']} ({line['item_code']})"
		if _truthy(answer.get("no_charge")) and can_mark_free:
			reason = " ".join(str(answer.get("reason") or "").split())
			if NO_CHARGE_NEEDS_REASON and not reason:
				out["problems"].append(f"{where}: say why it was free (a sample, a warranty replacement).")
				continue
			out["free"].append((name, reason[:140]))
			continue
		if not can_price:
			out["held"].append(line)
			continue
		if answer.get("rate") in (None, ""):
			out["missing"].append(line)
			continue
		rate, problem = checked_price(answer.get("rate"))
		if problem:
			out["problems"].append(f"{where}: {problem}")
			continue
		out["prices"][name] = rate
	return out


def _order_rows(lines):
	return ", ".join(f"row {line['idx']} ({line['item_code']})" for line in lines)


def receive_refusal(order, lines):
	"""Why nothing was received: these order lines have no price, and what to send instead."""
	has = "has" if len(lines) == 1 else "have"
	return (
		f"Nothing was received. On {order}, {_order_rows(lines)} {has} no price, and stock comes in "
		"at the price on the order. Enter what each one cost, or mark it as no charge and say why "
		"if it was free (a sample or a warranty replacement), then receive again."
	)


def block_sentence(block, order):
	"""Why this person can't price ``order``, and who can. ``block`` is one of :data:`BLOCKS`."""
	if block == BLOCK_REQUESTER:
		return (
			f"You raised the request that {order} fills, so you can't set its prices: the person "
			"who asks for something is not the one who commits the money for it. Someone else in "
			"purchasing can."
		)
	return f"You can receive against {order}, but you can't set prices on it. Purchasing can."


def asked_sentence(asked):
	"""Who was asked for the price, in words. ``asked`` is a list of names."""
	names = [name for name in asked or () if name]
	if not names:
		return "Tell purchasing it needs a price."
	if len(names) == 1:
		who = names[0]
	else:
		who = ", ".join(names[:-1]) + " and " + names[-1]
	has = "has" if len(names) == 1 else "have"
	return f"{who} {has} been asked to put the price on the order (a ToDo)."


def held_message(order, lines, block, asked, received_some):
	"""What the receiver is told about the lines left unreceived."""
	was = "was" if len(lines) == 1 else "were"
	has = "has" if len(lines) == 1 else "have"
	lead = (
		f"{_first_upper(_order_rows(lines))} {was} not received"
		if received_some
		else f"Nothing was received. {_first_upper(_order_rows(lines))} {has} no price"
	)
	tail = (
		": no price on the order, and stock comes in at the price on the order."
		if received_some
		else ", and stock comes in at the price on the order."
	)
	return (
		f"{lead}{tail} {block_sentence(block, order)} {asked_sentence(asked)} "
		"Set the goods aside and receive them once the order has a price."
	)


def price_todo(order, lines, asker):
	"""The ToDo purchasing gets. Starts with :data:`TODO_MARKER`."""
	has = "has" if len(lines) == 1 else "have"
	return (
		f"{TODO_MARKER} on {order}: {_order_rows(lines)} {has} no price, so the goods can't be "
		f"received. {asker} has them at the dock. Put the price on the order with Update Items "
		"(or receive them as no charge if they were free), then tell them to receive again."
	)


def cannot_price_refusal(order):
	"""For a person who may receive but may not edit the order."""
	return f"{block_sentence(BLOCK_NO_WRITE, order)} Ask them to put the price on the order (Update Items)."


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


def free_comment(full_name, rows, reason=""):
	"""The Comment left on the order when a line was received as free on purpose."""
	text = f"{full_name} received row {rows} at no charge, so it came into stock at $0."
	if reason:
		text += f" Reason given: {reason}"
	return text


def price_comment(full_name, rows):
	"""The Comment left on the order: who priced which rows while receiving. No amounts."""
	return f"{full_name} entered the price for row {rows} while receiving it."
