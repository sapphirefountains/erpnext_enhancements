# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a stock movement was, and the totals by person and by item (v1.831.0). No Frappe, no I/O.

The Warehouse Transactions report (``report/warehouse_transactions``) lists one row per Stock Ledger
Entry. The 2026-11-19 operations review asked for two more ways to read the same rows: **totals by
person**, to see who is and is not using the system, and **totals by item**, to see how much of each
item moves. Both need to know what each line *was*, which the ledger does not store directly:

* **The kind** (:func:`movement_kind`). A Stock Ledger Entry has a ``voucher_type`` but not the
  Stock Entry's ``purpose``, and one purpose covers both a take and a return. The kind is decided
  from the voucher type, the Stock Entry's purpose (looked up by the caller) and the sign of the
  change:

  ============================  ===================================================
  Kind                          Ledger lines
  ============================  ===================================================
  Take                          Stock Entry *Material Issue* (Stock Scan's −)
  Receive                       Purchase Receipt or Purchase Invoice adding stock
  Add                           Stock Entry *Material Receipt* (added without a PO,
                                or brought back from a job)
  Move                          Stock Entry *Material Transfer* and the other
                                transfer purposes: one line out, one line in
  Count                         Stock Reconciliation (a count correction)
  Return to supplier            Purchase Receipt or Purchase Invoice taking stock out
  Delivered                     Delivery Note, Sales Invoice or POS Invoice taking
                                stock out
  Other                         anything else (a repack, a manufacture, a customer
                                return, a subcontracting purpose)
  ============================  ===================================================

* **Taken** is a Take or a Delivered line: stock that left for a job or a customer. A count
  correction is never taken (it says the shelf was wrong, not that anything was used), a move only
  changes where stock is, and a return to a supplier went back, not into a job. The column is called
  Taken, not Used, on purpose: the Reorder Board's suggested reorder levels count *use* from the
  shop's side (takes and deliveries, and also moves out to a job site or the Project Shelf, less
  returns), and one word must not mean two figures for the same item on the same day.

* **A move is counted once.** A transfer is two ledger lines, one out of the source and one into
  the target, and the report's filters may show only one of them (pick the source bin and the leg
  into the target is outside it). So a move is counted per *voucher and item*, and how much moved
  is the larger of its two sides as far as they are shown.

* **Exclude my entries** (v1.841.0, :func:`owner_filter`). A warehouse user asked to leave their own
  entries out, to see how everyone else has used the warehouse. On production on 2026-11-24 one person
  had made 254 of the 278 live ledger lines of the previous 30 days, so their own work buried everyone
  else's. The box narrows the entry's ``owner`` as the User filter does, and the two together have one
  case that matches nothing (the User filter naming the reader).

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import math

KIND_TAKE = "Take"
KIND_RECEIVE = "Receive"
KIND_ADD = "Add"
KIND_MOVE = "Move"
KIND_COUNT = "Count"
KIND_RETURN = "Return to supplier"
KIND_DELIVERED = "Delivered"
KIND_OTHER = "Other"

#: In the order the report's Kind filter offers them.
KINDS = (
	KIND_TAKE,
	KIND_RECEIVE,
	KIND_ADD,
	KIND_MOVE,
	KIND_COUNT,
	KIND_RETURN,
	KIND_DELIVERED,
	KIND_OTHER,
)

#: Stock that left for a job or a customer: the By item view's Taken.
TAKEN_KINDS = frozenset({KIND_TAKE, KIND_DELIVERED})
#: Stock that arrived from outside: bought, or added by hand.
IN_KINDS = frozenset({KIND_RECEIVE, KIND_ADD})

#: Stock Entry purposes that only change where stock is, from ERPNext v16.54.0's purpose list (the
#: others make, consume, repack or take in stock and are Other here).
TRANSFER_PURPOSES = frozenset(
	{"Material Transfer", "Material Transfer for Manufacture", "Send to Subcontractor"}
)
RECONCILIATION = "Stock Reconciliation"
BUYING_VOUCHERS = frozenset({"Purchase Receipt", "Purchase Invoice"})
SELLING_VOUCHERS = frozenset({"Delivery Note", "Sales Invoice", "POS Invoice"})

VIEW_MOVEMENTS = "Movements"
VIEW_PEOPLE = "By person"
VIEW_ITEMS = "By item"
VIEWS = (VIEW_MOVEMENTS, VIEW_PEOPLE, VIEW_ITEMS)

_EPS = 1e-9


def flt(value) -> float:
	"""``frappe.utils.flt`` for what these rows hold: None, "", numbers, numeric text."""
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	return 0.0 if math.isnan(number) or math.isinf(number) else number


def clean_view(value) -> str:
	"""One of :data:`VIEWS`; anything else (blank, a typo in a link) is the movement list."""
	value = str(value or "").strip()
	return value if value in VIEWS else VIEW_MOVEMENTS


def clean_kind(value):
	"""One of :data:`KINDS`, or ``None`` for "any kind"."""
	value = str(value or "").strip()
	return value if value in KINDS else None


def checked(value) -> bool:
	"""A Check filter's value as a bool. The report view sends ``1`` when the box is ticked and leaves an
	unticked one out; a link's route options send ``"1"``. Any non-zero number is on, anything else off."""
	return bool(int(flt(value)))


#: What :func:`owner_filter` returns when the User filter names the reader and Exclude my entries is on.
#: No entry can match both, so the report reads nothing. It is falsy, so it never becomes a condition.
NO_ONE = False


def owner_filter(user=None, exclude_mine=False, me=None):
	"""The condition on a ledger entry's ``owner`` (the person who made it) for the report's User filter
	and its Exclude my entries box (v1.841.0, TASK-2026-03290).

	============  ==================  ============================================
	User filter   Exclude my entries  Condition on ``owner``
	============  ==================  ============================================
	blank         off                 ``None``: anyone
	someone       off                 that person
	blank         on                  ``["!=", me]``: anyone but the reader
	someone else  on                  that person (who is already not the reader)
	the reader    on                  :data:`NO_ONE`: nothing can match
	============  ==================  ============================================

	``me`` is the signed-in user. Without one there is nobody to leave out, so the box does nothing.
	Names are compared without case, as MariaDB's default collation compares the ``owner`` column.
	"""
	user = str(user or "").strip()
	me = str(me or "").strip()
	if not exclude_mine or not me:
		return user or None
	if not user:
		return ["!=", me]
	if user.lower() == me.lower():
		return NO_ONE
	return user


def without_me(people, me):
	"""``people`` (``{user: full_name}``) without ``me``, compared without case: the By person view's
	list of people with Exclude my entries on, so the reader is not a row of zeros in it."""
	me = str(me or "").strip().lower()
	return {user: name for user, name in (people or {}).items() if not me or str(user or "").lower() != me}


def movement_kind(voucher_type, purpose=None, qty_change=0.0) -> str:
	"""The kind of one ledger line (the table in the module docstring).

	``purpose`` is the Stock Entry's purpose, ``None`` for any other voucher or when the reader may
	not see the Stock Entry (then a Stock Entry line is Other). ``qty_change`` is the line's change,
	the derived one for a count.
	"""
	voucher_type = voucher_type or ""
	qty = flt(qty_change)
	if voucher_type == RECONCILIATION:
		return KIND_COUNT
	if voucher_type == "Stock Entry":
		if purpose == "Material Issue":
			return KIND_TAKE
		if purpose == "Material Receipt":
			return KIND_ADD
		if purpose in TRANSFER_PURPOSES:
			return KIND_MOVE
		return KIND_OTHER
	if voucher_type in BUYING_VOUCHERS:
		return KIND_RETURN if qty < -_EPS else KIND_RECEIVE
	if voucher_type in SELLING_VOUCHERS and qty < -_EPS:
		return KIND_DELIVERED
	return KIND_OTHER


def _moved_by_pair(rows):
	"""``{(voucher_no, item_code): units moved}`` for the Move lines among ``rows``: the larger of
	what went out and what came in, as far as the rows show either side."""
	out_qty, in_qty = {}, {}
	for row in rows:
		if row.get("kind") != KIND_MOVE:
			continue
		key = (row.get("voucher_no"), row.get("item_code"))
		change = flt(row.get("qty_change"))
		if change < 0:
			out_qty[key] = out_qty.get(key, 0.0) - change
		else:
			in_qty[key] = in_qty.get(key, 0.0) + change
		out_qty.setdefault(key, 0.0)
		in_qty.setdefault(key, 0.0)
	return {key: max(out_qty[key], in_qty[key]) for key in out_qty}


def _latest(current, candidate):
	if candidate is None:
		return current
	if current is None:
		return candidate
	return candidate if str(candidate) > str(current) else current


def by_person(rows, people=None):
	"""One total per person, for the report's *By person* view.

	``rows``: the movement rows (each with ``user``, ``full_name``, ``voucher_no``, ``item_code``,
	``qty_change``, ``kind``, ``posting_date``). ``people``: ``{user: full_name}`` of everyone who
	*should* appear even with nothing in ``rows`` (the people who keep stock), so a person who made
	no movement shows as a row of zeros instead of being missing from the list.

	Per person: ``saves`` (distinct vouchers), ``lines`` (ledger lines), ``takes``, ``receipts``
	(Receive and Add), ``moves``, ``counts`` and ``others`` (each counted per voucher and item, so a
	transfer is one move however many of its legs are shown), ``items`` (distinct items),
	``days_active`` (distinct posting dates) and ``last_movement``. Most saves first; people with
	none last, by name.
	"""
	totals = {}

	def person(user, full_name):
		if user not in totals:
			totals[user] = {
				"user": user,
				"full_name": full_name or "",
				"saves": set(),
				"lines": 0,
				"takes": set(),
				"receipts": set(),
				"moves": set(),
				"counts": set(),
				"others": set(),
				"items": set(),
				"days": set(),
				"last_movement": None,
			}
		elif full_name and not totals[user]["full_name"]:
			totals[user]["full_name"] = full_name
		return totals[user]

	for user, full_name in (people or {}).items():
		if user:
			person(user, full_name)
	buckets = {
		KIND_TAKE: "takes",
		KIND_DELIVERED: "takes",
		KIND_RECEIVE: "receipts",
		KIND_ADD: "receipts",
		KIND_MOVE: "moves",
		KIND_COUNT: "counts",
	}
	for row in rows or ():
		user = row.get("user")
		if not user:
			continue
		total = person(user, row.get("full_name"))
		pair = (row.get("voucher_no"), row.get("item_code"))
		total["saves"].add(row.get("voucher_no"))
		total["lines"] += 1
		total[buckets.get(row.get("kind"), "others")].add(pair)
		total["items"].add(row.get("item_code"))
		total["days"].add(str(row.get("posting_date")))
		total["last_movement"] = _latest(total["last_movement"], row.get("posting_date"))

	out = []
	for total in totals.values():
		out.append(
			{
				"full_name": total["full_name"] or total["user"],
				"user": total["user"],
				"saves": len(total["saves"]),
				"lines": total["lines"],
				"takes": len(total["takes"]),
				"receipts": len(total["receipts"]),
				"moves": len(total["moves"]),
				"counts": len(total["counts"]),
				"others": len(total["others"]),
				"items": len(total["items"]),
				"days_active": len(total["days"]),
				"last_movement": total["last_movement"],
			}
		)
	out.sort(key=lambda r: (-r["saves"], -r["lines"], (r["full_name"] or "").lower(), r["user"]))
	return out


def by_item(rows, uoms=None):
	"""One total per item, for the report's *By item* view, in the item's stock unit.

	``taken``: units taken for a job or delivered (a positive number). ``received``: units bought or
	added by hand. ``moved``: units moved between places (:func:`_moved_by_pair`). ``counted``: the
	net of the count corrections (negative when counts found less than the system had).
	``returned``: units sent back to a supplier. ``net_change``: every line added up, so with a
	location chosen it is how that location's stock changed. ``takes``: how many take or delivery
	lines. Also ``people``, ``lines`` and ``last_movement``. Most taken first, then most lines.
	"""
	moved = _moved_by_pair(rows or ())
	totals = {}
	for row in rows or ():
		code = row.get("item_code")
		if not code:
			continue
		total = totals.setdefault(
			code,
			{
				"item_code": code,
				"item_name": row.get("item_name") or "",
				"stock_uom": (uoms or {}).get(code) or "",
				"taken": 0.0,
				"takes": 0,
				"received": 0.0,
				"moved": 0.0,
				"counted": 0.0,
				"returned": 0.0,
				"net_change": 0.0,
				"people": set(),
				"lines": 0,
				"last_movement": None,
			},
		)
		change = flt(row.get("qty_change"))
		kind = row.get("kind")
		if kind in TAKEN_KINDS:
			total["taken"] -= change
			total["takes"] += 1
		elif kind in IN_KINDS:
			total["received"] += change
		elif kind == KIND_COUNT:
			total["counted"] += change
		elif kind == KIND_RETURN:
			total["returned"] -= change
		total["net_change"] += change
		if row.get("user"):
			total["people"].add(row.get("user"))
		total["lines"] += 1
		total["last_movement"] = _latest(total["last_movement"], row.get("posting_date"))
	for (_voucher, code), qty in moved.items():
		if code in totals:
			totals[code]["moved"] += qty
	out = []
	for total in totals.values():
		total["people"] = len(total["people"])
		out.append(total)
	out.sort(key=lambda r: (-r["taken"], -r["lines"], r["item_code"]))
	return out


def group_trail(warehouse, parents, labels=None, separator=" › "):
	"""The groups above ``warehouse``, outermost first, the root left out: ``"Atrium › North Large
	Rack 1"`` for a shelf on that rack, ``""`` for a warehouse directly under the root.

	``parents``: ``{warehouse: parent_warehouse}``. ``labels``: ``{warehouse: warehouse_name}`` (the
	name without the company's abbreviation); the docname when missing. The root is the ancestor with
	no parent (``All Warehouses - SF``): it is above everything, so naming it says nothing. A loop in
	the tree stops the walk instead of hanging it.
	"""
	chain, seen = [], {warehouse}
	parent = (parents or {}).get(warehouse)
	while parent and parent not in seen and len(chain) < 64:
		seen.add(parent)
		chain.append(parent)
		parent = (parents or {}).get(parent)
	if chain and not (parents or {}).get(chain[-1]):
		chain.pop()
	return separator.join((labels or {}).get(name) or name for name in reversed(chain))


def stock_people(users, has_roles, excluded=()):
	"""``{user: full_name}`` of the people who keep stock: enabled System Users holding one of the
	stock roles, minus Frappe's own accounts and service accounts.

	``users``: rows ``{name, full_name, enabled, user_type}``. ``has_roles``: the user names that
	hold a stock role. ``excluded``: lower-cased names to leave out.
	"""
	holders = {str(name or "").lower() for name in has_roles or ()}
	skip = {str(name or "").lower() for name in excluded or ()}
	out = {}
	for user in users or ():
		name = str(user.get("name") or "").strip()
		if not name or name.lower() in skip or name.lower() not in holders:
			continue
		if not int(flt(user.get("enabled"))):
			continue
		if (user.get("user_type") or "System User") != "System User":
			continue
		out[name] = user.get("full_name") or name
	return out
