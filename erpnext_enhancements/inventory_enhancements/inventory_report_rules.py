# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The decisions behind the four inventory reports (v1.805.0). No Frappe, tested bench-free.

Location Contents, Where Is This Item, Project Materials and Job Site Contents each read the
database in their own module and hand the rows here. What a row *means* is decided in this file:

* **A location's status for an item** (:func:`location_status`): as expected, assigned here with
  none on hand, here and not assigned, here but assigned to another bin, on order to here, empty
  and not decided. A place that is never anybody's bin (the Project Shelf, a job site, the
  Boneyard) holds stock without assigning it, so there the answer is "held here" and never a
  problem.
* **What kind of place a warehouse is** (:func:`place_kind`): a shop bin, the Project Shelf, a job
  site, the Boneyard, a vehicle. The same split Stock Scan makes between the shop and job sites
  (``job_site_rules.split_places``), with the other places named.
* **Whose stock is on the Project Shelf** (:func:`allocate_shelf`): the shelf is one warehouse, so
  ``tabBin`` has one figure per item there and the job comes from the stock ledger.
* **One project's items** (:func:`project_item_rows`): ordered, received, on the shelf, at the job
  site, used, returned to the supplier, still to come, and what was received that the stock ledger
  cannot place.
* **How long stock has been at a job site** (:func:`stay`).

Indentation is tabs (new file).
"""

import datetime as dt

TOLERANCE = 1e-9

# --- Location Contents ---------------------------------------------------------------------

STATUS_OK = "As expected"
STATUS_EMPTY = "Assigned here, none on hand"
STATUS_UNASSIGNED = "Here, not assigned"
STATUS_ELSEWHERE = "Here, assigned to another bin"
STATUS_ON_ORDER = "On order to here"
STATUS_UNDECIDED = "Empty, not decided"
STATUS_HELD = "Held here"
STATUS_NOTHING = "Nothing here"

#: Every status, in the order the report sorts and lists them.
STATUSES = (
	STATUS_ELSEWHERE,
	STATUS_UNASSIGNED,
	STATUS_EMPTY,
	STATUS_UNDECIDED,
	STATUS_ON_ORDER,
	STATUS_OK,
	STATUS_HELD,
	STATUS_NOTHING,
)

#: The statuses somebody should act on: what "Only problems" keeps.
PROBLEMS = frozenset({STATUS_ELSEWHERE, STATUS_UNASSIGNED, STATUS_EMPTY, STATUS_UNDECIDED})

ASSIGNED_RULE = "Bin rule"
ASSIGNED_DEFAULT = "Item default"
ASSIGNED_BOTH = "Bin rule and item default"
ASSIGNED_NO = "No"


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def assigned_here(row):
	"""How the item is assigned to the location, in words: by an enabled Putaway Rule (what the
	Bin Manager's *Keep on this bin* writes), by being its Item Default warehouse, by both, or not
	at all. ``row`` is one of ``api.stock_scan._items_at``'s rows."""
	rule = bool(row.get("assigned"))
	default = bool(row.get("is_default"))
	if rule and default:
		return ASSIGNED_BOTH
	if rule:
		return ASSIGNED_RULE
	if default:
		return ASSIGNED_DEFAULT
	return ASSIGNED_NO


def location_status(row, holding=False, elsewhere=()):
	"""The plain status of one item at one location.

	``row`` is one of ``api.stock_scan._items_at``'s rows (``on_hand``, ``on_order``, ``assigned``,
	``is_default``, ``undecided``). ``holding`` is true for a place that is never an item's bin:
	the Project Shelf, a job site, the Boneyard. ``elsewhere`` is the other places the item is
	assigned to (already filtered to real bins by the caller).

	Stock on hand is judged first: it is physically there, whatever is on order. With nothing on
	hand, an order on its way to the location is the answer before "empty", because somebody has
	already acted on the empty bin.
	"""
	on_hand = _num(row.get("on_hand")) > TOLERANCE
	on_order = _num(row.get("on_order")) > TOLERANCE
	if holding:
		if on_hand:
			return STATUS_HELD
		return STATUS_ON_ORDER if on_order else STATUS_NOTHING
	here = bool(row.get("assigned")) or bool(row.get("is_default"))
	if on_hand:
		if here:
			return STATUS_OK
		return STATUS_ELSEWHERE if elsewhere else STATUS_UNASSIGNED
	if on_order:
		return STATUS_ON_ORDER
	if here:
		return STATUS_EMPTY
	if row.get("undecided"):
		return STATUS_UNDECIDED
	return STATUS_NOTHING


def is_problem(status):
	return status in PROBLEMS


def status_rank(status):
	"""Where a status sorts: the ones to act on first."""
	try:
		return STATUSES.index(status)
	except ValueError:
		return len(STATUSES)


def other_bins(item_code, here, rules_by_item, defaults_by_item, not_bins=()):
	"""The other places an item is assigned to, as a sorted list of warehouse names.

	Enabled Putaway Rules come first and are the whole answer when there are any: a rule is a
	decision somebody made on a bin. With no rule anywhere else, the item's Item Default warehouse
	counts.

	Either way a place that is never a bin is left out (``not_bins``: the Project Shelf, job
	sites, the Boneyard, group warehouses). Production has Putaway Rules and Item Defaults that
	name the warehouse becoming the Project Shelf, and "assigned to the Project Shelf" would send
	somebody to put shop stock on a shelf that is not for it.
	"""
	skip = set(not_bins or ())
	skip.add(here)
	rules = sorted({w for w in rules_by_item.get(item_code, ()) if w and w not in skip})
	if rules:
		return rules
	return sorted({w for w in defaults_by_item.get(item_code, ()) if w and w not in skip})


def location_summary(rows):
	"""Counts by status over every row at the chosen locations, before "Only problems" or the
	Status filter narrows the table. So the cards always say how big the whole picture is."""
	counts = {status: 0 for status in STATUSES}
	for row in rows:
		counts[row.get("status")] = counts.get(row.get("status"), 0) + 1
	return {
		"locations": len({row.get("warehouse") for row in rows}),
		"rows": len(rows),
		"ok": counts[STATUS_OK] + counts[STATUS_HELD],
		"problems": sum(counts[s] for s in PROBLEMS),
		"by_status": counts,
	}


# --- Places --------------------------------------------------------------------------------

KIND_SHOP = "Shop bin"
KIND_SHELF = "Project Shelf"
KIND_SITE = "Job site"
KIND_BONEYARD = "Boneyard"
KIND_VEHICLE = "Vehicle"

#: The order places are listed in: where the shop keeps it, what is held for jobs, what is out
#: on jobs, then what nobody should pick from.
KINDS = (KIND_SHOP, KIND_SHELF, KIND_SITE, KIND_VEHICLE, KIND_BONEYARD)

#: Kinds that are never an item's bin. Nothing is assigned there, and stock there is not a
#: bin-assignment problem.
HOLDING_KINDS = frozenset({KIND_SHELF, KIND_SITE, KIND_BONEYARD})


def place_kind(warehouse, shelf=None, sites=(), boneyard=None, vehicles=()):
	"""What kind of place ``warehouse`` is. A job site wins over everything (a site is a site
	wherever somebody moved it in the tree), then the Project Shelf, the Boneyard, a vehicle, and
	anything else is the shop."""
	if not warehouse:
		return KIND_SHOP
	if warehouse in sites:
		return KIND_SITE
	if shelf and warehouse == shelf:
		return KIND_SHELF
	if boneyard and warehouse == boneyard:
		return KIND_BONEYARD
	if warehouse in vehicles:
		return KIND_VEHICLE
	return KIND_SHOP


def kind_rank(kind):
	try:
		return KINDS.index(kind)
	except ValueError:
		return len(KINDS)


def is_holding(kind):
	return kind in HOLDING_KINDS


def leaves_under(warehouses, root=None):
	"""The scannable locations under ``root`` (or all of them): not a group, not disabled, not a
	transit warehouse. The Bin Manager's picker lists the same ones.

	``warehouses`` is ``[{name, parent_warehouse, is_group, disabled, warehouse_type}, ...]``.
	``root`` may itself be a leaf, and is then the one answer. Membership is walked up each
	warehouse's own parent, never read from the tree's left and right numbers: those were out of
	step on production once, and ``job_site_rules.descendants`` walks the same way for the same
	reason. A chain that loops, or runs past 64 levels, is under nothing.
	"""
	by_name = {w.get("name"): w for w in warehouses}

	def scannable(w):
		return not (
			_on(w.get("is_group")) or _on(w.get("disabled")) or (w.get("warehouse_type") or "") == "Transit"
		)

	if not root:
		return [w.get("name") for w in warehouses if scannable(w)]
	if root not in by_name:
		return []
	out = []
	for w in warehouses:
		if not scannable(w):
			continue
		name = w.get("name")
		seen = set()
		while name and name not in seen and len(seen) < 64:
			if name == root:
				out.append(w.get("name"))
				break
			seen.add(name)
			name = (by_name.get(name) or {}).get("parent_warehouse")
	return out


def _on(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return bool(value)


# --- The Project Shelf, by job -------------------------------------------------------------

NO_PROJECT = ""


def allocate_shelf(on_hand, by_project):
	"""Split one item's Project Shelf stock between the jobs it was received for.

	``on_hand`` is what ``tabBin`` says is on the shelf: the truth about how many there are.
	``by_project`` is ``{project: net ledger quantity}``, what the stock ledger says came in for
	each job less what went back out carrying the same job. The two disagree whenever somebody
	counted the shelf (a count carries no job), so the shelf's own figure wins:

	* a job's share is its net quantity, never less than nothing;
	* what the jobs do not claim is the shelf's own, under no job (``NO_PROJECT``);
	* when the jobs claim more than is there, every share is scaled down by the same factor, so
	  the shares still add up to what is on the shelf.

	Returns ``[(project, qty), ...]``, jobs by name and the no-job remainder last, with nothing
	at zero.
	"""
	total = max(_num(on_hand), 0.0)
	claims = {p: _num(q) for p, q in (by_project or {}).items() if p and _num(q) > TOLERANCE}
	if total <= TOLERANCE:
		return []
	claimed = sum(claims.values())
	if claimed > total + TOLERANCE:
		factor = total / claimed
		return [(p, round(claims[p] * factor, 6)) for p in sorted(claims)]
	out = [(p, claims[p]) for p in sorted(claims)]
	rest = total - claimed
	if rest > TOLERANCE:
		out.append((NO_PROJECT, round(rest, 6)))
	return out


# --- Project Materials ---------------------------------------------------------------------

#: A Purchase Order in one of these is not bringing anything more: Closed is a decision to stop
#: chasing it, Delivered went from the supplier straight to the customer. The rule
#: Pending Items by Project and the Pick Routing Map use.
SETTLED_PO_STATUSES = frozenset({"Closed", "Delivered"})

STANDING_WAITING = "Still to come"
STANDING_CLOSED_SHORT = "Order closed, not received"
STANDING_NO_ORDER = "Bought without an order"
STANDING_RECEIVED = "Received"
STANDING_ORDERED = "Ordered"
STANDING_FROM_STOCK = "From shop stock"

WHERE_NOT_STOCK = "Not a stock item: where it is was never recorded"
WHERE_SHOP_BIN = "Received into a shop bin, where stock is not kept by job"
WHERE_NOT_LOCATED = "Received before it was a stock item: where it is was never recorded"

SHOW_ALL = "Everything"
SHOW_WAITING = "Still to come"
SHOW_HELD = "On the shelf or at the site"
SHOW_NOT_LOCATED = "Received, not located"
SHOW_OPTIONS = (SHOW_ALL, SHOW_WAITING, SHOW_HELD, SHOW_NOT_LOCATED)


def _stock_qty(line, qty_field="qty"):
	"""A line's quantity in the item's stock unit: its ``stock_qty`` when ERPNext stored one,
	else its quantity times its conversion factor (a blank factor is 1)."""
	if line.get("stock_qty") is not None:
		return _num(line.get("stock_qty"))
	return _num(line.get(qty_field)) * (_num(line.get("conversion_factor")) or 1.0)


def _pending_stock_qty(line):
	"""What an order line still has to bring, in the stock unit. Never less than nothing: a line
	received over its quantity is not owed anything back."""
	short = _num(line.get("qty")) - _num(line.get("received_qty"))
	if short <= TOLERANCE:
		return 0.0
	return short * (_num(line.get("conversion_factor")) or 1.0)


def _date(value):
	if isinstance(value, dt.datetime):
		return value.date()
	if isinstance(value, dt.date):
		return value
	if not value:
		return None
	try:
		return dt.date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def _blank_item():
	return {
		"item_name": "",
		"stock_uom": "",
		"ordered": 0.0,
		"ordered_amount": 0.0,
		"received": 0.0,
		"returned": 0.0,
		"still_to_come": 0.0,
		"closed_short": 0.0,
		"on_shelf": 0.0,
		"at_site": 0.0,
		"used": 0.0,
		"held_value": 0.0,
		"purchase_orders": set(),
		"receipts": set(),
		"first_ordered": None,
		"last_received": None,
	}


def project_item_rows(
	order_lines,
	receipt_lines,
	shelf=None,
	site=None,
	used=None,
	supplier_returns=None,
	items=None,
	received_into=None,
):
	"""One row per item for one project: what was ordered for the job and where each stands.

	* ``order_lines``: submitted Purchase Order lines for the project (``item_code``,
	  ``item_name``, ``purchase_order``, ``po_status``, ``transaction_date``, ``qty``,
	  ``stock_qty``, ``received_qty``, ``conversion_factor``, ``stock_uom``, ``amount``).
	* ``receipt_lines``: submitted Purchase Receipt lines for the project (``item_code``,
	  ``item_name``, ``purchase_receipt``, ``is_return``, ``posting_date``, ``stock_qty``,
	  ``qty``, ``conversion_factor``, ``stock_uom``). A return receipt's quantities are negative,
	  as ERPNext stores them.
	* ``shelf`` / ``site``: ``{item_code: qty}`` on the Project Shelf for this job and at its job
	  site; ``site`` may instead map to ``(qty, value)``.
	* ``used``: ``{item_code: qty}`` taken out of stock for the job, less what came back.
	* ``supplier_returns``: ``{item_code: qty}`` from the Supplier Return record (the seam; the
	  doctype is built by another change). Added to the returns ERPNext's own return receipts show.
	* ``items``: ``{item_code: {item_name, stock_uom, is_stock_item, item_group}}``.
	* ``received_into``: ``{item_code: [warehouse, ...]}``, where the job's receipts actually put
	  stock (:func:`received_into`), already in the words to show.

	**Ordered and received come from different documents on purpose.** Ordered is the order
	lines. Received is the receipt lines, not the order line's ``received_qty``: a store run is a
	receipt with no order, and it belongs in the job's record as much as an ordered part does.
	The order line's own short quantity is still what "still to come" and "order closed, not
	received" read, because that is the number purchasing chases.

	**Not located** is what was received and not sent back that the ledger cannot place: not on
	the shelf, not at the site, not used. For an item that is not a stock item that is all of it,
	since a receipt of one writes no stock ledger row. For a stock item it is stock received into
	a shop bin (where stock is not kept by job; *Received Into* names the bin), or received before
	Maintain Stock was switched on. It is never less than nothing.
	"""
	items = items or {}
	received_into = received_into or {}
	out = {}

	def entry(code):
		if code not in out:
			out[code] = _blank_item()
		return out[code]

	for line in order_lines or ():
		code = line.get("item_code")
		if not code:
			continue
		row = entry(code)
		row["item_name"] = row["item_name"] or (line.get("item_name") or "")
		row["stock_uom"] = row["stock_uom"] or (line.get("stock_uom") or "")
		row["ordered"] += _stock_qty(line)
		row["ordered_amount"] += _num(line.get("amount"))
		if line.get("purchase_order"):
			row["purchase_orders"].add(line.get("purchase_order"))
		ordered_on = _date(line.get("transaction_date"))
		if ordered_on and (row["first_ordered"] is None or ordered_on < row["first_ordered"]):
			row["first_ordered"] = ordered_on
		pending = _pending_stock_qty(line)
		if pending:
			if (line.get("po_status") or "") in SETTLED_PO_STATUSES:
				row["closed_short"] += pending
			else:
				row["still_to_come"] += pending

	for line in receipt_lines or ():
		code = line.get("item_code")
		if not code:
			continue
		row = entry(code)
		row["item_name"] = row["item_name"] or (line.get("item_name") or "")
		row["stock_uom"] = row["stock_uom"] or (line.get("stock_uom") or "")
		qty = _stock_qty(line)
		if _on(line.get("is_return")):
			row["returned"] += abs(qty)
		else:
			row["received"] += qty
			received_on = _date(line.get("posting_date"))
			if received_on and (row["last_received"] is None or received_on > row["last_received"]):
				row["last_received"] = received_on
		if line.get("purchase_receipt"):
			row["receipts"].add(line.get("purchase_receipt"))

	for code, qty in (shelf or {}).items():
		if _num(qty) > TOLERANCE:
			entry(code)["on_shelf"] += _num(qty)
	for code, value in (site or {}).items():
		qty, worth = value if isinstance(value, (tuple, list)) else (value, 0.0)
		if _num(qty) > TOLERANCE:
			entry(code)["at_site"] += _num(qty)
			entry(code)["held_value"] += _num(worth)
	for code, qty in (used or {}).items():
		if abs(_num(qty)) > TOLERANCE:
			entry(code)["used"] += _num(qty)
	for code, qty in (supplier_returns or {}).items():
		if _num(qty) > TOLERANCE:
			entry(code)["returned"] += _num(qty)

	rows = []
	for code in sorted(out):
		row = out[code]
		item = items.get(code) or {}
		is_stock = _on(item.get("is_stock_item"))
		kept = row["received"] - row["returned"]
		not_located = max(kept - row["on_shelf"] - row["at_site"] - max(row["used"], 0.0), 0.0)
		if not_located <= TOLERANCE:
			not_located = 0.0
		rows.append(
			{
				"item_code": code,
				"item_name": item.get("item_name") or row["item_name"] or code,
				"item_group": item.get("item_group") or "",
				"stock_uom": item.get("stock_uom") or row["stock_uom"],
				"is_stock_item": 1 if is_stock else 0,
				"ordered": row["ordered"],
				"received": row["received"],
				"on_shelf": row["on_shelf"],
				"at_site": row["at_site"],
				"used": row["used"],
				"returned": row["returned"],
				"still_to_come": row["still_to_come"],
				"closed_short": row["closed_short"],
				"not_located": not_located,
				"ordered_amount": row["ordered_amount"],
				"standing": standing(row),
				"received_into": ", ".join(received_into.get(code) or ()),
				"where_note": where_note(is_stock, not_located, bool(received_into.get(code))),
				"first_ordered": row["first_ordered"],
				"last_received": row["last_received"],
				"purchase_orders": ", ".join(sorted(row["purchase_orders"])),
				"receipts": ", ".join(sorted(row["receipts"])),
			}
		)
	return rows


def standing(row):
	"""Where one item stands for the job, in a few words. Still waiting comes first: it is the
	one somebody acts on. An item with no order and no receipt is on the list because parts were
	taken from the shop's own stock for the job, or are held for it."""
	if row["still_to_come"] > TOLERANCE:
		return STANDING_WAITING
	if row["closed_short"] > TOLERANCE and row["received"] <= TOLERANCE:
		return STANDING_CLOSED_SHORT
	if row["ordered"] <= TOLERANCE and row["received"] > TOLERANCE:
		return STANDING_NO_ORDER
	if row["received"] > TOLERANCE:
		return STANDING_RECEIVED
	if row["ordered"] > TOLERANCE:
		return STANDING_ORDERED
	return STANDING_FROM_STOCK


def where_note(is_stock_item, not_located, stocked=False):
	"""Why some of an item has no place in the report, or blank when all of it has one.
	``stocked`` says the job's receipts put some of it into stock (so it went to a shop bin)."""
	if _num(not_located) <= TOLERANCE:
		return ""
	if not is_stock_item:
		return WHERE_NOT_STOCK
	return WHERE_SHOP_BIN if stocked else WHERE_NOT_LOCATED


#: The vouchers that receive stock from a supplier.
RECEIVING_VOUCHERS = frozenset({"Purchase Receipt", "Purchase Invoice"})


def received_into(ledger_rows, holding=()):
	"""``{item_code: [warehouse, ...]}``: the shop locations the job's receipts put stock into.

	Read from the job's stock ledger (the same grouped rows :func:`used_by_item` reads), not from
	the receipt line's Warehouse field: a line for an item that is not a stock item carries a
	warehouse too, and nothing ever went there. The Project Shelf and job sites (``holding``) are
	left out, since stock there has columns of its own.
	"""
	holding = set(holding or ())
	out = {}
	for row in ledger_rows or ():
		if (row.get("voucher_type") or "") not in RECEIVING_VOUCHERS or _num(row.get("qty")) <= TOLERANCE:
			continue
		warehouse = row.get("warehouse")
		if warehouse and warehouse not in holding:
			out.setdefault(row.get("item_code"), set()).add(warehouse)
	return {code: sorted(names) for code, names in out.items()}


def show_row(row, show):
	"""Whether the Show filter keeps a project item row."""
	if show == SHOW_WAITING:
		return row["still_to_come"] > TOLERANCE
	if show == SHOW_HELD:
		return row["on_shelf"] > TOLERANCE or row["at_site"] > TOLERANCE
	if show == SHOW_NOT_LOCATED:
		return row["not_located"] > TOLERANCE
	return True


def project_item_summary(rows):
	return {
		"items": len(rows),
		"waiting": sum(1 for r in rows if r["still_to_come"] > TOLERANCE),
		"held": sum(1 for r in rows if r["on_shelf"] > TOLERANCE or r["at_site"] > TOLERANCE),
		"not_located": sum(1 for r in rows if r["not_located"] > TOLERANCE),
		"not_stock": sum(1 for r in rows if not r["is_stock_item"]),
	}


#: What counts as used on a job, as ERPNext's own "consumed material cost" counts it
#: (``project.py`` ``get_stock_value_consumed``): a Material Issue, a Delivery Note, a Sales Invoice.
USED_VOUCHERS = frozenset({"Delivery Note", "Sales Invoice"})
PURPOSE_ISSUE = "Material Issue"
PURPOSE_RECEIPT = "Material Receipt"


def used_by_item(ledger_rows, holding=()):
	"""``{item_code: qty used on the job}`` from the job's stock ledger rows.

	``ledger_rows`` are the project's live rows grouped by item, voucher type, Stock Entry purpose
	and warehouse: ``[{item_code, voucher_type, purpose, warehouse, qty}, ...]``, ``qty`` the sum
	of ``actual_qty`` (negative for stock going out).

	* Taken out for the job: a Material Issue, a Delivery Note or a Sales Invoice carrying it.
	* Brought back: a Material Receipt carrying the job, which is how Stock Scan records parts
	  coming back from a job. It lowers what was used. A Material Receipt straight onto the
	  Project Shelf or a job site (``holding``) is stock arriving for the job, not parts coming
	  back from use, and is left out.
	* A transfer is not use: the parts are still somewhere, and the shelf and site columns say
	  where.

	Never less than nothing: more brought back than was ever taken reads 0.
	"""
	holding = set(holding or ())
	out = {}
	for row in ledger_rows or ():
		code = row.get("item_code")
		if not code:
			continue
		voucher = row.get("voucher_type") or ""
		purpose = row.get("purpose") or ""
		if voucher in USED_VOUCHERS or (voucher == "Stock Entry" and purpose == PURPOSE_ISSUE):
			out[code] = out.get(code, 0.0) - _num(row.get("qty"))
		elif voucher == "Stock Entry" and purpose == PURPOSE_RECEIPT and row.get("warehouse") not in holding:
			out[code] = out.get(code, 0.0) - _num(row.get("qty"))
	return {code: qty for code, qty in out.items() if qty > TOLERANCE}


def held_by_project(shelf_rows, site_rows, projects=None):
	"""What is physically held for each project, one row per project.

	``shelf_rows`` and ``site_rows`` are ``[{project, item_code, qty, value}, ...]``: the Project
	Shelf split by job (:func:`allocate_shelf`) and the stock at each job's site. Shelf stock
	under no job is not any project's and is left out; the caller reports it in a note.
	``projects`` is ``{project: {project_name, customer, project_type, status}}``.
	"""
	projects = projects or {}
	out = {}

	def entry(project):
		if project not in out:
			out[project] = {
				"shelf_qty": 0.0,
				"shelf_value": 0.0,
				"site_qty": 0.0,
				"site_value": 0.0,
				"items": set(),
			}
		return out[project]

	for row in shelf_rows or ():
		if not row.get("project") or _num(row.get("qty")) <= TOLERANCE:
			continue
		e = entry(row.get("project"))
		e["shelf_qty"] += _num(row.get("qty"))
		e["shelf_value"] += _num(row.get("value"))
		e["items"].add(row.get("item_code"))
	for row in site_rows or ():
		if not row.get("project") or _num(row.get("qty")) <= TOLERANCE:
			continue
		e = entry(row.get("project"))
		e["site_qty"] += _num(row.get("qty"))
		e["site_value"] += _num(row.get("value"))
		e["items"].add(row.get("item_code"))

	rows = []
	for project in sorted(out):
		e = out[project]
		info = projects.get(project) or {}
		rows.append(
			{
				"project": project,
				"project_name": info.get("project_name") or "",
				"customer": info.get("customer") or "",
				"project_type": info.get("project_type") or "",
				"project_status": info.get("status") or "",
				"items": len(e["items"]),
				"shelf_qty": e["shelf_qty"],
				"shelf_value": e["shelf_value"],
				"site_qty": e["site_qty"],
				"site_value": e["site_value"],
				"total_value": e["shelf_value"] + e["site_value"],
			}
		)
	return rows


# --- Job Site Contents ---------------------------------------------------------------------


def stay(entries):
	"""When the stock now at a place arrived, from that item's ledger rows there.

	``entries`` are one item's live Stock Ledger rows at one warehouse, oldest first
	(``posting_date``, ``qty_after_transaction``). Returns ``(arrived, last_in)``:

	* ``arrived``: the day the balance last rose from nothing. Stock that was all used up and
	  sent again starts a new stay, so "how long it has been there" is not counted from a
	  delivery that is long gone.
	* ``last_in``: the day of the latest row that raised the balance.

	The change on each row is its balance after, less the balance after the row before. That is
	how ERPNext's own Stock Ledger report reads a count, which ERPNext 16 stores with no change
	of its own (``actual_qty`` 0) and the counted balance in ``qty_after_transaction``.
	"""
	arrived = last_in = None
	before = 0.0
	for row in entries or ():
		after = _num(row.get("qty_after_transaction"))
		day = _date(row.get("posting_date"))
		if after > TOLERANCE and before <= TOLERANCE:
			arrived = day
		if after - before > TOLERANCE:
			last_in = day
		if after <= TOLERANCE:
			arrived = None
		before = after
	return arrived, last_in


def days_since(day, today):
	"""Whole days from ``day`` to ``today``, or ``None`` when either is missing. Never negative:
	a row dated ahead of today (a wrong posting date) reads 0."""
	day, today = _date(day), _date(today)
	if not day or not today:
		return None
	return max((today - day).days, 0)


def job_site_summary(rows):
	ages = [r.get("days_there") for r in rows if r.get("days_there") is not None]
	return {
		"sites": len({r.get("warehouse") for r in rows if _num(r.get("on_hand")) > TOLERANCE}),
		"lines": len(rows),
		"value": sum(_num(r.get("value")) for r in rows),
		"oldest_days": max(ages) if ages else None,
	}
