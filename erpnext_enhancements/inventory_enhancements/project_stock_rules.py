# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where a project's material is now, worked out from the stock ledger (v1.811.0). No Frappe:
tested bench-free (``tests/test_project_stock.py``).

**The question.** The Procurement Tracker used to show one warehouse per line: where the line
was *received*. A part received onto the Project Shelf and carried to the job a week later
still read "Stores". The owner asked for where it is now (Nik, 2026-11-12): on the Project
Shelf, at the job's site, in a named bin, taken to the job, or sent back to the supplier.

**ERPNext keeps on-hand by item and warehouse, with no project on that table.** What does carry
the project is the Stock Ledger: every receipt, move and take posted for a job has the job on
its ledger rows. So "where is this job's material" is answered in two parts:

* **At the job's own site**, everything on hand is the job's. A site warehouse belongs to one
  project (``job_sites``), so its Bin rows are read as they stand.
* **Anywhere else**, the ledger rows that name the job are added up by item and warehouse. What
  is left at a warehouse is what was received or moved there for the job and has not been moved
  on or taken *with the job on the entry*. That sum is then **capped at what the Bin row says is
  there now**: a shelf-to-shelf move made without a job, or a count correction, takes stock away
  without the ledger saying whose it was, and the page must never show more at a place than the
  place holds.

A take posted for the job (a Material Issue, or a delivery) is **taken to the job**; a purchase
return is **returned to the supplier**. Whatever the purchasing documents say was received and
none of the above accounts for is **place not recorded**, said in those words rather than left
out: it was received before the item kept stock, or it was moved or counted with no job on the
entry.

**What this is not.** It is not a reservation and it is not exact per purchase-order line. Two
lines of one item on one job share one answer, and two jobs that share a shelf each see their
own ledger sum against the same Bin row. It is the best reading of the records there are, and
every figure can be traced to ledger rows that name the job.

Indentation is tabs (new file).
"""

from collections import defaultdict

from erpnext_enhancements.inventory_enhancements.stock_scan_rules import TOLERANCE, natural_key

# ---------------------------------------------------------------------------
# Kinds of place
# ---------------------------------------------------------------------------

#: The shared Project Shelf (``Inventory Scanner Settings.project_shelf_warehouse``).
KIND_SHELF = "project_shelf"
#: The site warehouse of the job being asked about.
KIND_SITE = "job_site"
#: Any other shop location, named: a shelf bin, the inventory room.
KIND_BIN = "shop_bin"
#: A service truck or van.
KIND_VEHICLE = "vehicle"
#: Another job's site (or the hand-made site with no job of its own).
KIND_OTHER_SITE = "other_site"
#: The boneyard.
KIND_BONEYARD = "boneyard"
#: Left stock for the job: a Material Issue or a delivery with the job on it.
KIND_TAKEN = "taken"
#: Sent back to the supplier: a purchase return.
KIND_RETURNED = "returned"
#: Received for the job, and no record says where it is.
KIND_UNRECORDED = "unrecorded"

#: The order places are listed in: where the stock can be picked up first, then where it went.
PLACE_ORDER = (
	KIND_SHELF,
	KIND_SITE,
	KIND_BIN,
	KIND_VEHICLE,
	KIND_OTHER_SITE,
	KIND_BONEYARD,
	KIND_TAKEN,
	KIND_RETURNED,
	KIND_UNRECORDED,
)

#: Kinds that are a warehouse somebody can walk to.
WAREHOUSE_KINDS = (KIND_SHELF, KIND_SITE, KIND_BIN, KIND_VEHICLE, KIND_OTHER_SITE, KIND_BONEYARD)

#: What each kind is called on screen. A shop bin, a vehicle and another job's site are called by
#: their own names, so they are absent here.
KIND_LABELS = {
	KIND_SHELF: "Project Shelf",
	KIND_SITE: "Job site",
	KIND_BIN: "Shop bins",
	KIND_VEHICLE: "Vehicles",
	KIND_OTHER_SITE: "Another job's site",
	KIND_BONEYARD: "Boneyard",
	KIND_TAKEN: "Taken to the job",
	KIND_RETURNED: "Returned to supplier",
	KIND_UNRECORDED: "Place not recorded",
}

# ---------------------------------------------------------------------------
# Ledger rows
# ---------------------------------------------------------------------------

#: Stock moved in or between places: a receipt, a transfer, stock brought back from a job.
LEDGER_MOVE = "move"
#: Stock that left for the job.
LEDGER_ISSUE = "issue"
#: Stock that went back to the supplier.
LEDGER_SUPPLIER_RETURN = "supplier_return"
#: Stock put back into stock with the job on the entry and no order behind it (a Material
#: Receipt): parts the job took and did not use.
LEDGER_BROUGHT_BACK = "brought_back"

_ISSUE_VOUCHERS = frozenset({"Delivery Note", "Sales Invoice"})
_PURCHASE_VOUCHERS = frozenset({"Purchase Receipt", "Purchase Invoice"})

#: Names that mark a warehouse, or a group above it, as a vehicle. Only a fallback: the rule
#: that decides is ``Employee.custom_default_vehicle_warehouse`` (``project_stock.vehicles``).
_VEHICLE_WORDS = ("truck", "van", "vehicle", "trailer")


def _qty(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _on(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def ledger_kind(voucher_type, purpose=None, is_return=0, qty=0):
	"""What one ledger row (or one grouped sum of them) did, for :func:`item_places`.

	* a **Material Issue** stock entry, a Delivery Note or a stock-updating Sales Invoice is a
	  take for the job;
	* a **purchase return** (``is_return`` on the receipt, or a purchase voucher whose quantity is
	  negative) went back to the supplier;
	* a **Material Receipt** stock entry with the job on it is parts coming back from the job:
	  they are stock again where they landed, and no longer "taken";
	* everything else moved stock into or between places: a Purchase Receipt, a Material
	  Transfer.
	"""
	voucher_type = str(voucher_type or "")
	if voucher_type == "Stock Entry":
		purpose = str(purpose or "")
		if purpose == "Material Issue":
			return LEDGER_ISSUE
		return LEDGER_BROUGHT_BACK if purpose == "Material Receipt" else LEDGER_MOVE
	if voucher_type in _ISSUE_VOUCHERS:
		return LEDGER_ISSUE
	if voucher_type in _PURCHASE_VOUCHERS and (_on(is_return) or _qty(qty) < -TOLERANCE):
		return LEDGER_SUPPLIER_RETURN
	return LEDGER_MOVE


# ---------------------------------------------------------------------------
# Kinds of warehouse
# ---------------------------------------------------------------------------


def looks_like_vehicle(names):
	"""Whether any of ``names`` (a warehouse's own name and its groups') reads as a vehicle."""
	for name in names or ():
		words = str(name or "").lower().replace("-", " ").split()
		if any(word.rstrip("s") in _VEHICLE_WORDS or word in _VEHICLE_WORDS for word in words):
			return True
	return False


def warehouse_kind(warehouse, shelf=None, boneyard=None, own_sites=(), all_sites=(), vehicles=()):
	"""Which kind of place a warehouse is. ``own_sites`` are the sites of the job being asked
	about; ``all_sites`` every job site (``job_sites.site_warehouses``). Asked with no job, every
	site is :data:`KIND_SITE`."""
	if not warehouse:
		return KIND_BIN
	if shelf and warehouse == shelf:
		return KIND_SHELF
	if warehouse in own_sites:
		return KIND_SITE
	if warehouse in all_sites:
		return KIND_OTHER_SITE if own_sites else KIND_SITE
	if boneyard and warehouse == boneyard:
		return KIND_BONEYARD
	if warehouse in vehicles:
		return KIND_VEHICLE
	return KIND_BIN


def plain(value):
	"""A quantity for a sentence: ``6`` not ``6.0``, ``2.5`` not ``2.500``."""
	text = f"{_qty(value):.3f}".rstrip("0").rstrip(".")
	return text or "0"


def place_text(kind, qty, name=None):
	"""One place in words: ``6 on the Project Shelf``, ``4 at the job site``, ``2 in Bin A1-2-3``."""
	amount = plain(qty)
	if kind == KIND_SHELF:
		return f"{amount} on the Project Shelf"
	if kind == KIND_SITE:
		return f"{amount} at the job site"
	if kind == KIND_TAKEN:
		return f"{amount} taken to the job"
	if kind == KIND_RETURNED:
		return f"{amount} returned to the supplier"
	if kind == KIND_UNRECORDED:
		return f"{amount} with no place recorded"
	if kind == KIND_BONEYARD:
		return f"{amount} in the boneyard"
	if kind == KIND_OTHER_SITE:
		return f"{amount} at {name or 'another job site'}"
	return f"{amount} in {name or 'the shop'}"


def _place(kind, qty, warehouse=None, name=None):
	label = name if kind in (KIND_BIN, KIND_VEHICLE, KIND_OTHER_SITE) and name else KIND_LABELS[kind]
	return {
		"kind": kind,
		"warehouse": warehouse,
		"label": label,
		"qty": round(_qty(qty), 6),
		"text": place_text(kind, qty, name),
	}


def _order(place):
	return (PLACE_ORDER.index(place["kind"]), natural_key(place.get("label") or ""))


def item_places(ledger, on_hand, own_sites=(), kind_of=None, names=None, returned=0.0, held=None):
	"""The places one item is for one job, as a list of place dicts, in :data:`PLACE_ORDER`.

	* ``ledger`` -- the job's ledger rows for this item, each ``{"warehouse", "kind", "qty"}``
	  with ``kind`` from :func:`ledger_kind` and ``qty`` signed as the ledger has it.
	* ``on_hand`` -- ``{warehouse: qty}``: what the item's Bin rows say is there now.
	* ``own_sites`` -- the job's own site warehouses. Everything on hand there is the job's.
	* ``kind_of`` -- ``warehouse -> kind`` (:func:`warehouse_kind`, already told the shelf and
	  the sites); ``names`` -- ``{warehouse: warehouse_name}``.
	* ``returned`` -- what the job's Supplier Returns say has left for the supplier
	  (:func:`return_summary`). The purchase return behind a Supplier Return is the same goods the
	  ledger's return rows count, so the two are never added: the larger one is shown. A return
	  made in the Desk with no Supplier Return is only in the ledger; a return whose rows carry no
	  job is only on the Supplier Return.

	* ``held`` -- ``{warehouse: qty}`` for a place whose share for this job somebody else has
	  already worked out, used as it stands instead of the ledger sum. The Project Shelf's number
	  comes this way (``project_stock.places_for``), so the tracker and the Bin Manager, which
	  both read it from the Project Shelf's own function, cannot disagree about it.

	**Parts brought back are counted once, where they are.** A job that took 3 and put 3 back
	(a Material Receipt with the job on it) has them at the place they came back to, and has
	taken none: what came back is taken off "taken to the job", never below zero.

	Each place is ``{"kind", "warehouse", "label", "qty", "text"}``. Nothing is listed at a
	warehouse where the job's ledger sum is zero or negative (a take of shop stock for the job
	leaves the bin's sum negative: that is a take, not a place), and never more than the Bin row
	holds.
	"""
	names = names or {}
	own = set(own_sites or ())
	kind_of = kind_of or (lambda warehouse: KIND_SITE if warehouse in own else KIND_BIN)
	held = held or {}
	net = defaultdict(float)
	taken = ledger_returned = brought_back = 0.0
	for row in ledger or ():
		qty = _qty(row.get("qty"))
		kind = row.get("kind")
		if kind == LEDGER_ISSUE:
			taken -= qty
		elif kind == LEDGER_SUPPLIER_RETURN:
			ledger_returned -= qty
		elif kind == LEDGER_BROUGHT_BACK:
			brought_back += qty
		if row.get("warehouse"):
			net[row["warehouse"]] += qty
	taken = max(taken - max(brought_back, 0.0), 0.0)
	for warehouse in held:
		net.setdefault(warehouse, 0.0)

	places = []
	for warehouse in own:
		qty = _qty((on_hand or {}).get(warehouse))
		if qty > TOLERANCE:
			places.append(_place(KIND_SITE, qty, warehouse, names.get(warehouse)))
	for warehouse, qty in net.items():
		if warehouse in own:
			continue
		if warehouse in held:
			here = _qty(held[warehouse])
		else:
			here = min(qty, _qty((on_hand or {}).get(warehouse)))
		if here > TOLERANCE:
			places.append(_place(kind_of(warehouse), here, warehouse, names.get(warehouse) or warehouse))
	if taken > TOLERANCE:
		places.append(_place(KIND_TAKEN, taken))
	gone = max(ledger_returned, _qty(returned))
	if gone > TOLERANCE:
		places.append(_place(KIND_RETURNED, gone))
	places.sort(key=_order)
	return places


def with_unrecorded(places, received):
	"""``places`` plus a :data:`KIND_UNRECORDED` place for what was received and nothing above
	accounts for. ``received`` is what the purchasing documents say arrived for the job, in the
	stock unit; ``None`` (nothing knows) adds nothing.

	An approximation in one direction only: parts taken for the job out of shop stock count
	toward "accounted for", so they can hide an unrecorded remainder, never invent one.
	"""
	places = list(places or ())
	if received is None:
		return places
	accounted = sum(_qty(p.get("qty")) for p in places)
	missing = _qty(received) - accounted
	if missing > TOLERANCE:
		places.append(_place(KIND_UNRECORDED, missing))
	return places


def places_text(places):
	"""Every place in one line: ``6 on the Project Shelf · 4 at the job site``."""
	return " · ".join(p["text"] for p in places or ())


def shelf_rows(ledger, on_hand, shelf):
	"""What is on the Project Shelf for one job: ``{item_code: qty}``.

	``ledger`` is the job's rows for every item (``{"item_code", "warehouse", "qty"}``),
	``on_hand`` is ``{item_code: qty}`` at the shelf. The same rule as :func:`item_places`: the
	job's ledger sum at the shelf, never more than the shelf holds.
	"""
	net = defaultdict(float)
	for row in ledger or ():
		if shelf and row.get("warehouse") == shelf and row.get("item_code"):
			net[row["item_code"]] += _qty(row.get("qty"))
	out = {}
	for item_code, qty in net.items():
		here = min(qty, _qty((on_hand or {}).get(item_code)))
		if here > TOLERANCE:
			out[item_code] = round(here, 6)
	return out


def capped_shelf_rows(rows, untagged):
	"""The Project Shelf module's per-job rows (``project_shelf.shelf_stock()["rows"]``), held to
	what the shelf can hand over.

	That function reports each job's ledger balance as it stands: it can be negative, and the sum
	over jobs can exceed what is on the shelf (a count or a move with no job on it makes the two
	drift; it lists the drift as ``untagged``). A screen that offers "send this job's 6 to the
	site" needs a number the shelf can honor, so a row is cut to the item's on-hand wherever the
	module says the item's tagged total and its on-hand differ, and a row with nothing left is
	dropped.

	Since that module's own review it does this cut itself: ``qty`` arrives already held to the
	shelf's on-hand, and the uncut ledger balance is in ``tagged``. Both shapes are read, so the
	answer is the same whichever version is installed.

	Returns ``[{"project", "item_code", "item_name", "stock_uom", "qty", "recorded"}]``:
	``recorded`` is the job's ledger balance (``tagged`` when the row has it), and it is above
	``qty`` exactly when the row was cut.
	"""
	on_hand = {
		row.get("item_code"): _qty(row.get("on_hand")) for row in untagged or () if row.get("item_code")
	}
	out = []
	for row in rows or ():
		code = row.get("item_code")
		qty = _qty(row.get("qty"))
		recorded = _qty(row.get("tagged")) if row.get("tagged") is not None else qty
		if code in on_hand:
			qty = min(qty, max(on_hand[code], 0.0))
		if not code or qty <= TOLERANCE:
			continue
		out.append(
			{
				"project": row.get("project"),
				"item_code": code,
				"item_name": row.get("item_name") or code,
				"stock_uom": row.get("stock_uom"),
				"qty": round(qty, 6),
				"recorded": round(recorded, 6),
			}
		)
	return out


def return_summary(lines):
	"""A job's Supplier Return lines (``supplier_returns.returned_lines(project=...)``) by item:
	``{item_code: {"gone": qty, "returns": [{"supplier_return", "status", "qty", "gone", "open"}]}}``.

	``gone`` adds up the stock-unit quantity of the lines whose goods have left (*Sent Back* and
	after). ``returns`` is one entry per Supplier Return, in the order they came, its lines of the
	item added together, so the tracker can name each return and say how far along it is. A line
	with no item, or a return with no name, is skipped.
	"""
	out = {}
	for line in lines or ():
		code = line.get("item_code")
		name = line.get("supplier_return")
		if not code or not name:
			continue
		qty = _qty(line.get("stock_qty") if line.get("stock_qty") is not None else line.get("qty"))
		entry = out.setdefault(code, {"gone": 0.0, "returns": []})
		gone = bool(line.get("gone"))
		if gone:
			entry["gone"] += qty
		held = next((r for r in entry["returns"] if r["supplier_return"] == name), None)
		if held:
			held["qty"] = round(held["qty"] + qty, 6)
		else:
			entry["returns"].append(
				{
					"supplier_return": name,
					"status": line.get("status"),
					"qty": round(qty, 6),
					"gone": gone,
					"open": bool(line.get("open")),
				}
			)
	for entry in out.values():
		entry["gone"] = round(entry["gone"], 6)
	return out
