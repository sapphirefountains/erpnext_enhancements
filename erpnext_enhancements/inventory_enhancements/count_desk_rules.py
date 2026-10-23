# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The decisions behind the Count Desk. **No Frappe, no I/O.**

The Count Desk (``inventory_enhancements/page/count_desk``, endpoints in ``api/count_desk.py``) is
the laptop way to run a physical count: pick bins from the warehouse tree, get a sheet of every item
those bins hold, type the counts down a keyboard grid, review the variances and post them. It keeps
its counts in the same ``Inventory Count Session`` the phone page (Inventory Scanner Audit) uses.
Every judgement it makes is here, so it runs bench-free in CI; ``api/count_desk.py`` only reads and
writes.

Six of those judgements are not the obvious ones:

* **A variance is a fact about (item, warehouse), not about one line.** A line's ``system_qty`` is
  the warehouse's on-hand when it was counted, so an item spread over two Storage Locations of one
  warehouse is only short once *every* location is counted. :func:`groups` sums the lines first.
* **"When it was counted" is not always "when it was typed"** (v1.738.0). A paper count walked at 9:00 and
  typed in at 11:00 is a count of the shelf at 9:00. A session's optional ``counted_at``
  (:func:`check_counted_at`) makes every line's snapshot the stock ledger as of then, so a take at
  10:00 is not counted twice; blank is a live count, snapshotted as each row is typed.
* **What posts is the variance, not the count.** Stock keeps moving while a count is open: a
  technician takes three after the shelf was counted. Posting the counted number would undo that
  take. :func:`post_plan` posts *what is on hand now* plus the variance, so a movement after the
  count survives it (``moved`` on a row shows it). A count that is now below zero posts zero.
* **A count somebody else has posted since is not posted again.** Two open counts of one bin would
  otherwise each add their variance. A Stock Reconciliation after a group's last count makes the
  group ``superseded``; it is counted again or left.
* **A count that matches posts nothing.** ERPNext drops unchanged rows from a Stock Reconciliation
  and refuses one with none left ("None of the items have any change in quantity or value"), so a
  count where everything matched used to fail to finalize. Matches stay on the session, which is
  the record that they were counted.
* **A post is reconciliations of at most** ``RECONCILIATION_ROWS`` **rows** (v1.738.0). ERPNext v16's
  ``StockReconciliation.submit`` sends one of more than 100 rows to a background job and returns
  with it still a draft; a failed job puts it back to Draft, and a deploy's redis flush drops the
  job. :func:`batches` splits the post so each submits there and then.

Nothing here raises. Functions return a value or a sentence in the user's terms.
"""

import datetime
import html
import json

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules

TOLERANCE = scan_rules.TOLERANCE

#: Who may use the Count Desk: the counting roles of the phone page
#: (``api.inventory_scanner.ALLOWED_ROLES``, held equal by ``tests/test_count_desk.py``) and the
#: Inventory Count Session's own permission rows. Posting the result is a separate question, the
#: framework's: Stock Reconciliation create and submit.
COUNT_ROLES = frozenset({"System Manager", "Stock Manager", "Inventory Clerk"})

#: Who may open and act on a count somebody else started (``api.inventory_scanner.SUPERVISOR_ROLES``).
SUPERVISOR_ROLES = frozenset({"System Manager", "Stock Manager"})

#: The coverage window, the same 90 days as the Operations KPI *Stocked Items Counted (90d)*.
COVERAGE_DAYS = 90

#: Why a count differs from the system. Stored on ``Inventory Count Line.reason_code`` (a Select
#: whose options ``tests/test_count_desk.py`` holds equal to this) and summed in the comment on each
#: reconciliation, so a shrinkage report can group by cause later.
REASON_CODES = (
	"Earlier count was wrong",
	"Used and not recorded",
	"Received and not recorded",
	"In a different bin",
	"Damaged or scrapped",
	"Unit mix-up (box vs each)",
	"Missing, cause unknown",
	"Found, cause unknown",
	"Other",
)

#: The longest count title. It is a list-view column, not a document.
TITLE_MAX = 140

#: Items one reconciliation comment lists by name. The reasons of a 400-line count are a report, not
#: a comment: past this it says how many more the count has (the per-reason totals count them all).
COMMENT_ITEMS = 50

#: Rows one Stock Reconciliation may carry and still submit in the request that posts it. ERPNext
#: v16 ``StockReconciliation.submit`` queues one of more than 100 (``queue_action``) and returns it
#: a draft; prod has had reconciliations of 365 rows. A post is split into reconciliations this size.
RECONCILIATION_ROWS = 100

#: How long before a count was opened its Counted At may be: a paper count walked on Friday and typed
#: in on Monday. Older than this, the count is history, not a count to post now.
COUNTED_AT_DAYS = 7

#: A Counted At this far past the server's clock is a laptop clock running fast, not the future.
COUNTED_AT_SKEW = datetime.timedelta(minutes=5)

#: Bins one count may cover. A count is a walk someone finishes; the whole site (221 leaf
#: warehouses on 2026-10-14) is a stocktake, and a sheet that long is not one anyone types into.
MAX_SCOPE = 400

KEY_SEP = "\x1f"


def number(value):
	"""A float, or ``None`` for blank and anything that is not a finite number."""
	return scan_rules._number(value)


def plain(value):
	return scan_rules.plain(value)


# ---------------------------------------------------------------------------
# Scope: which bins a count covers
# ---------------------------------------------------------------------------


def parse_scope(value):
	"""The warehouse names a session's ``scope`` holds, in order, without blanks or repeats.

	Stored as a JSON list; a list is accepted as it is, and anything unreadable is no scope (the
	phone page's sessions have none: they cover whatever was scanned)."""
	if value in (None, ""):
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			return []
	if not isinstance(value, list | tuple):
		return []
	out = []
	for name in value:
		text = str(name).strip() if name is not None else ""
		if text and text not in out:
			out.append(text)
	return out


def dump_scope(names):
	return json.dumps(parse_scope(list(names or [])))


def countable(row):
	"""Whether a Warehouse row is a place stock can be counted: a leaf, enabled, not Transit (goods on
	a truck between warehouses are not on a shelf), the bins the labels and the Bin Manager list."""
	return bool(
		row
		and not int(row.get("is_group") or 0)
		and not int(row.get("disabled") or 0)
		and (row.get("warehouse_type") or "") != "Transit"
	)


def expand_scope(selected, warehouses):
	"""``(bins, problems)``: the countable leaf warehouses ``selected`` covers.

	``warehouses`` is every Warehouse row (``name``, ``warehouse_name``, ``is_group``, ``disabled``,
	``warehouse_type``, ``lft``, ``rgt``). A group selects every countable leaf under it; a leaf
	selects itself. A name that is not a countable warehouse and covers none is a problem, said in a
	sentence. ``bins`` is in shelf order (``natural_key``) with no repeats."""
	by_name = {row["name"]: row for row in warehouses or []}
	chosen = {}
	problems = []
	for name in parse_scope(list(selected or [])):
		row = by_name.get(name)
		if not row:
			problems.append(f"There is no location called {name}.")
			continue
		if int(row.get("is_group") or 0):
			lft, rgt = row.get("lft"), row.get("rgt")
			inside = [
				w
				for w in warehouses
				if countable(w)
				and lft is not None
				and w.get("lft") is not None
				and int(w["lft"]) > int(lft)
				and int(w["rgt"]) < int(rgt)
			]
			if not inside:
				problems.append(f"{row.get('warehouse_name') or name} has no locations to count.")
			for w in inside:
				chosen[w["name"]] = w
			continue
		if not countable(row):
			label = row.get("warehouse_name") or name
			if int(row.get("disabled") or 0):
				problems.append(f"{label} is disabled.")
			else:
				problems.append(f"{label} is a transit warehouse, not a place stock is kept.")
			continue
		chosen[row["name"]] = row
	bins = sorted(chosen.values(), key=lambda w: scan_rules.natural_key(w.get("warehouse_name") or w["name"]))
	return [w["name"] for w in bins], problems


def check_scope(bins, problems):
	"""A sentence when a count cannot start over ``bins``, else ``None``."""
	if problems and not bins:
		return " ".join(problems)
	if not bins:
		return "Choose at least one location to count."
	if len(bins) > MAX_SCOPE:
		return (
			f"That is {len(bins)} locations, and one count can cover {MAX_SCOPE}. "
			"Count a row or a bay at a time."
		)
	return None


def check_title(value):
	"""The title as stored: trimmed, single-spaced, at most ``TITLE_MAX`` characters."""
	text = " ".join(str(value or "").split())
	return text[:TITLE_MAX]


def default_title(labels):
	"""A count's title from what was picked: ``Shelf B1-2``, ``Shelf B1-2 and Shelf B1-3``,
	``Shelf B1-2 + 3 more``."""
	names = [str(label).strip() for label in labels or [] if str(label or "").strip()]
	if not names:
		return "Count"
	if len(names) == 1:
		return check_title(names[0])
	if len(names) == 2:
		return check_title(f"{names[0]} and {names[1]}")
	return check_title(f"{names[0]} + {len(names) - 1} more")


# ---------------------------------------------------------------------------
# When it was counted
# ---------------------------------------------------------------------------


def to_datetime(value):
	"""A ``datetime`` (seconds, no microseconds) from a datetime or ``YYYY-MM-DD HH:MM[:SS]`` text, or
	``None`` for blank and anything unreadable."""
	if value in (None, ""):
		return None
	if isinstance(value, datetime.datetime):
		return value.replace(microsecond=0, tzinfo=None)
	if isinstance(value, datetime.date):
		return datetime.datetime(value.year, value.month, value.day)
	try:
		parsed = datetime.datetime.fromisoformat(str(value).strip())
	except ValueError:
		return None
	return parsed.replace(microsecond=0, tzinfo=None)


def check_counted_at(value, created, now):
	"""``(when, problem)`` for a session's Counted At: when the shelf was counted, for a count taken
	on paper and typed in later. Blank is ``(None, None)``, a live count.

	``created`` is when the count was opened and ``now`` the server's time. A Counted At may be up to
	``COUNTED_AT_DAYS`` before the count was opened (the walk came first, the typing after), and not
	after now (``COUNTED_AT_SKEW`` allows for a laptop clock running fast)."""
	if value in (None, "") or (isinstance(value, str) and not value.strip()):
		return None, None
	when = to_datetime(value)
	if not when:
		return None, f"{value} is not a date and time."
	now = to_datetime(now)
	if now and when > now + COUNTED_AT_SKEW:
		return None, "Counted At is in the future. It is when the shelf was counted, so it has passed."
	created = to_datetime(created) or now
	if created and when < created - datetime.timedelta(days=COUNTED_AT_DAYS):
		return None, (
			f"Counted At can be at most {COUNTED_AT_DAYS} days before the count was started "
			f"({created:%Y-%m-%d %H:%M}). A count older than that is out of date: count the shelf again."
		)
	return min(when, now) if now else when, None


# ---------------------------------------------------------------------------
# The sheet
# ---------------------------------------------------------------------------


def line_key(item_code, warehouse, storage_location=None):
	"""One sheet row's key: a line is one (warehouse, storage location, item), as ``add_count``
	upserts it. ``KEY_SEP`` (the ASCII unit separator) cannot appear in a name typed on a form."""
	return KEY_SEP.join([warehouse or "", storage_location or "", item_code or ""])


def split_key(key):
	parts = str(key or "").split(KEY_SEP)
	if len(parts) != 3 or not parts[0] or not parts[2]:
		return None
	return {"warehouse": parts[0], "storage_location": parts[1] or None, "item_code": parts[2]}


def item_refusal(item):
	"""Why an item cannot be counted here, or ``None``: the phone page's own exclusions, said for a
	count. A serial or batch item's count is a list of numbers, not a quantity."""
	if not item:
		return "This item no longer exists."
	if int(item.get("disabled") or 0):
		return "This item is disabled."
	if not int(item.get("is_stock_item") or 0):
		return "This item is not kept in stock, so it is not counted."
	if int(item.get("has_serial_no") or 0):
		return "Serialized items are counted by serial number on the Stock Reconciliation form."
	if int(item.get("has_batch_no") or 0):
		return "Batch items are counted by batch on the Stock Reconciliation form."
	if int(item.get("has_variants") or 0):
		return "This is a template; count its variants instead."
	return None


def _stamp(value):
	"""A scan time comparable as text: ``YYYY-MM-DD HH:MM:SS``, or ``""``."""
	if value in (None, ""):
		return ""
	if isinstance(value, datetime.datetime):
		return value.strftime("%Y-%m-%d %H:%M:%S.%f")
	return str(value)


def groups(lines):
	"""The lines summed per (item, warehouse).

	``counted`` is the sum over every line (one per storage location); ``snapshot`` is the on-hand
	the **latest** of them saw, because each line's ``system_qty`` is the warehouse's total at its
	own scan time; ``latest`` is that scan time. ``reason_code`` / ``reason`` come from any line that
	has one (a reason on one line of an item satisfies the item)."""
	out = {}
	for line in lines or []:
		key = (line.get("item_code"), line.get("warehouse"))
		if not key[0] or not key[1]:
			continue
		group = out.setdefault(
			key,
			{
				"item_code": key[0],
				"warehouse": key[1],
				"counted": 0.0,
				"snapshot": 0.0,
				"latest": "",
				"reason_code": "",
				"reason": "",
				"lines": 0,
				"recount": False,
			},
		)
		group["counted"] += number(line.get("counted_qty")) or 0.0
		group["lines"] += 1
		stamp = _stamp(line.get("scan_time"))
		if group["lines"] == 1 or stamp >= group["latest"]:
			group["latest"] = stamp
			group["snapshot"] = number(line.get("system_qty")) or 0.0
		if not group["reason_code"] and (line.get("reason_code") or "").strip():
			group["reason_code"] = line["reason_code"].strip()
		if not group["reason"] and (line.get("reason") or "").strip():
			group["reason"] = line["reason"].strip()
		if int(line.get("recount") or 0):
			group["recount"] = True
	for group in out.values():
		group["variance"] = group["counted"] - group["snapshot"]
		if abs(group["variance"]) <= TOLERANCE:
			group["variance"] = 0.0
	return out


def sheet_rows(expected, lines, balances, items, names, as_of=None):
	"""The count sheet: every line counted, and every item expected in scope that has none.

	* ``expected`` -- ``{warehouse: [listing row, ...]}``, what each bin lists on the phone page
	  (``api.stock_scan._listed_items``: on hand, assigned, its default, on order, undecided).
	* ``lines`` -- the session's lines as dicts.
	* ``balances`` -- ``{(item_code, warehouse): {"actual_qty", "reserved", "valuation_rate"}}``.
	* ``items`` -- ``{item_code: {item_name, stock_uom, whole_number, disabled, is_stock_item,
	  has_serial_no, has_batch_no, has_variants, valuation_rate}}``.
	* ``names`` -- ``{warehouse: warehouse_name}``.
	* ``as_of`` -- for a count with a Counted At, ``{(item_code, warehouse): qty}`` the stock ledger
	  showed then, for the rows not yet counted; ``None`` for a live count.

	An expected (item, warehouse) is covered by **any** line for it, whatever its storage location.
	Each row carries the live on-hand (``system_qty``), and once counted the on-hand it was counted
	against (``snapshot_qty``), what has moved since (``moved``) and the item's variance (the
	group's: every line of one item at one warehouse shows the same). ``as_of_qty`` is what a count
	of the row is counted against, under a Counted At (``None`` without one): the page shows it as
	the system quantity. In shelf order, then by name.
	"""
	balances = balances or {}
	items = items or {}
	names = names or {}
	by_group = groups(lines)
	rows = []
	covered = set()

	def base(item_code, warehouse):
		item = items.get(item_code) or {}
		balance = balances.get((item_code, warehouse)) or {}
		rate = number(balance.get("valuation_rate")) or number(item.get("valuation_rate")) or 0.0
		return {
			"warehouse": warehouse,
			"warehouse_name": names.get(warehouse) or warehouse,
			"item_code": item_code,
			"item_name": item.get("item_name") or item_code,
			"uom": item.get("stock_uom") or "",
			"whole_number": bool(item.get("whole_number")),
			"system_qty": number(balance.get("actual_qty")) or 0.0,
			"reserved": number(balance.get("reserved")) or 0.0,
			"rate": rate,
			"refusal": item_refusal(item) if item else None,
		}

	listed = {}
	for warehouse, rows_here in (expected or {}).items():
		for row in rows_here or []:
			listed[(row.get("item_code"), warehouse)] = row

	for line in lines or []:
		item_code, warehouse = line.get("item_code"), line.get("warehouse")
		if not item_code or not warehouse:
			continue
		covered.add((item_code, warehouse))
		group = by_group.get((item_code, warehouse)) or {}
		row = base(item_code, warehouse)
		row.update(
			{
				"key": line_key(item_code, warehouse, line.get("storage_location")),
				"storage_location": line.get("storage_location") or None,
				"idx": line.get("idx"),
				"counted": True,
				"counted_qty": number(line.get("counted_qty")) or 0.0,
				"snapshot_qty": number(line.get("system_qty")) or 0.0,
				"variance": group.get("variance", 0.0),
				"group_lines": group.get("lines", 1),
				"reason_code": line.get("reason_code") or "",
				"reason": line.get("reason") or "",
				"recount": bool(int(line.get("recount") or 0)),
				"scan_time": _stamp(line.get("scan_time"))[:19] or None,
				"counted_by": line.get("counted_by") or None,
				"expected": (item_code, warehouse) in listed,
			}
		)
		if not row["item_name"] or row["item_name"] == item_code:
			row["item_name"] = line.get("item_name") or item_code
		if not row["uom"]:
			row["uom"] = line.get("uom") or ""
		row["moved"] = row["system_qty"] - group.get("snapshot", row["snapshot_qty"])
		if abs(row["moved"]) <= TOLERANCE:
			row["moved"] = 0.0
		row["value_variance"] = row["variance"] * row["rate"]
		row["as_of_qty"] = row["snapshot_qty"] if as_of is not None else None
		_listing_flags(row, listed.get((item_code, warehouse)))
		rows.append(row)

	for (item_code, warehouse), listing in listed.items():
		if (item_code, warehouse) in covered:
			continue
		row = base(item_code, warehouse)
		if not items.get(item_code):
			row["item_name"] = listing.get("item_name") or item_code
			row["uom"] = listing.get("stock_uom") or ""
			row["refusal"] = None
		row.update(
			{
				"key": line_key(item_code, warehouse),
				"storage_location": None,
				"idx": None,
				"counted": False,
				"counted_qty": None,
				"snapshot_qty": None,
				"variance": None,
				"group_lines": 0,
				"moved": 0.0,
				"value_variance": None,
				"reason_code": "",
				"reason": "",
				"recount": False,
				"scan_time": None,
				"counted_by": None,
				"expected": True,
				"as_of_qty": (number(as_of.get((item_code, warehouse))) or 0.0)
				if as_of is not None
				else None,
			}
		)
		_listing_flags(row, listing)
		rows.append(row)

	rows.sort(
		key=lambda r: (
			scan_rules.natural_key(r["warehouse_name"]),
			scan_rules.natural_key(r.get("storage_location") or ""),
			scan_rules.natural_key(r["item_name"]),
			r["item_code"],
		)
	)
	return rows


def _listing_flags(row, listing):
	listing = listing or {}
	row["assigned"] = bool(listing.get("assigned"))
	row["on_order"] = number(listing.get("on_order")) or 0.0
	row["is_default"] = bool(listing.get("is_default"))
	row["undecided"] = bool(listing.get("undecided"))


def summary(rows):
	"""What a sheet adds up to, per item (a variance is an item fact, not a line fact).

	``expected`` items the sheet lists, ``counted`` of them with a count, ``matched`` counted with no
	variance, ``variances`` counted with one, ``accuracy`` matched over counted (a percentage, or
	``None`` before anything is counted), ``net_value`` and ``gross_value`` the variances at the
	item's current rate, ``recount`` flagged for a recount, ``moved`` counted items whose stock moved
	after they were counted."""
	seen = {}
	for row in rows or []:
		key = (row.get("item_code"), row.get("warehouse"))
		if key in seen:
			if row.get("recount"):
				seen[key]["recount"] = True
			continue
		seen[key] = {
			"counted": bool(row.get("counted")),
			"variance": number(row.get("variance")) or 0.0,
			"value": number(row.get("value_variance")) or 0.0,
			"recount": bool(row.get("recount")),
			"moved": abs(number(row.get("moved")) or 0.0) > TOLERANCE,
			"refused": bool(row.get("refusal")),
		}
	counted = [g for g in seen.values() if g["counted"]]
	matched = [g for g in counted if abs(g["variance"]) <= TOLERANCE]
	countable_items = [g for g in seen.values() if not g["refused"] or g["counted"]]
	return {
		"expected": len(countable_items),
		"counted": len(counted),
		"uncounted": len([g for g in countable_items if not g["counted"]]),
		"matched": len(matched),
		"variances": len(counted) - len(matched),
		"accuracy": round(len(matched) * 100.0 / len(counted), 1) if counted else None,
		"net_value": round(sum(g["value"] for g in counted), 2),
		"gross_value": round(sum(abs(g["value"]) for g in counted), 2),
		"recount": len([g for g in seen.values() if g["recount"]]),
		"moved": len([g for g in counted if g["moved"]]),
	}


# ---------------------------------------------------------------------------
# A save from the sheet
# ---------------------------------------------------------------------------


def check_reason_code(value):
	"""``(code, problem)``: blank, or one of ``REASON_CODES``."""
	text = (value or "").strip()
	if not text:
		return "", None
	if text not in REASON_CODES:
		return "", f"{text} is not one of the reasons this page offers."
	return text, None


def check_entry(entry, scope, refusal, whole_number=False, uom=""):
	"""``(values, problem)`` for one row a save sends.

	``entry`` is ``{key, counted_qty, reason_code, reason, recount}`` as the page sends it; a
	``counted_qty`` of ``None`` (or ``""``) clears the count, and a key without one keeps the count
	and changes only the reason or the flag. ``scope`` is the session's bins (empty for a phone
	session, which counts wherever it is scanned). ``refusal`` is the item's (:func:`item_refusal`)."""
	if not isinstance(entry, dict):
		return None, "That row could not be read."
	where = split_key(entry.get("key"))
	if not where:
		return None, "That row could not be read."
	if scope and where["warehouse"] not in scope:
		return None, "That location is not part of this count. Add it to the count first."
	values = dict(where)
	if "counted_qty" in entry:
		raw = entry.get("counted_qty")
		if raw is None or (isinstance(raw, str) and not raw.strip()):
			values["clear"] = True
		else:
			if refusal:
				return None, refusal
			qty, problem = scan_rules.check_count(raw, whole_number=whole_number, uom=uom)
			if problem:
				return None, problem
			values["counted_qty"] = qty
	if "reason_code" in entry:
		code, problem = check_reason_code(entry.get("reason_code"))
		if problem:
			return None, problem
		values["reason_code"] = code
	if "reason" in entry:
		values["reason"] = str(entry.get("reason") or "").strip()[:1000]
	if "recount" in entry:
		values["recount"] = 1 if entry.get("recount") in (True, 1, "1", "true") else 0
	return values, None


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def post_plan(group_map, balances, refusals, superseded, rate_for, require_reason=False, paper=False):
	"""What posting a count does, per (item, warehouse). Pure: ``rate_for(item, warehouse)`` is the
	caller's, asked only for a row that adds stock to an empty bin. ``paper`` is a count with a
	Counted At, whose lines are stamped with it: counting a superseded item again does not move it
	past the later count, so the advice is to change Counted At instead.

	Returns ``{"adjust": [...], "matched": [...], "skipped": [...], "needs_reason": [...]}``:

	* ``adjust`` -- a Stock Reconciliation row: ``qty`` is the on-hand **now** plus the variance,
	  never below zero (``clamped`` when it would have been); ``valuation_rate`` only from zero, else
	  ``None`` (ERPNext keeps the current rate, so a recount changes the quantity, not the unit cost).
	* ``matched`` -- counted and equal: nothing to post.
	* ``skipped`` -- not posted, each with a sentence: the item cannot be counted, somebody posted a
	  count of it since (``superseded``), stock there is reserved (v16 refuses a reconciliation of
	  the pair and its cancel), it is already at zero, or there is no cost on record.
	* ``needs_reason`` -- variances without a reason, when the settings require one. The caller
	  refuses the whole post on any.
	"""
	plan = {"adjust": [], "matched": [], "skipped": [], "needs_reason": []}
	balances = balances or {}
	superseded = superseded or set()
	for key in sorted(group_map, key=lambda k: (k[1], k[0])):
		group = group_map[key]
		item_code, warehouse = key
		variance = group["variance"]
		base = {"item_code": item_code, "warehouse": warehouse, "variance": variance}
		refusal = (refusals or {}).get(item_code)
		if refusal:
			plan["skipped"].append(dict(base, reason=refusal))
			continue
		if abs(variance) <= TOLERANCE:
			plan["matched"].append(base)
			continue
		if key in superseded:
			plan["skipped"].append(
				dict(
					base,
					reason=(
						"A count of this item here was posted after this count's Counted At. Clear Counted At "
						"and count it again, or set a Counted At after that count, to post it."
						if paper
						else "A count of this item here was posted after yours. Count it again to post it."
					),
				)
			)
			continue
		balance = balances.get(key) or {}
		current = number(balance.get("actual_qty")) or 0.0
		reserved = number(balance.get("reserved")) or 0.0
		if reserved > TOLERANCE:
			plan["skipped"].append(
				dict(
					base,
					reason=(
						f"{plain(reserved)} are reserved for orders here, and ERPNext does not allow a count "
						"while stock is reserved. Ask a Stock Manager to release the reservation."
					),
				)
			)
			continue
		if require_reason and not (group.get("reason_code") or group.get("reason")):
			plan["needs_reason"].append(base)
			continue
		target = current + variance
		clamped = target < -TOLERANCE
		if target < TOLERANCE:
			target = 0.0
		if abs(target - current) <= TOLERANCE:
			plan["skipped"].append(
				dict(base, reason="Nothing is on hand here any more, so there is nothing to take off.")
			)
			continue
		rate = None
		if current <= TOLERANCE and target > TOLERANCE:
			rate = number(rate_for(item_code, warehouse)) or 0.0
			if rate <= 0:
				plan["skipped"].append(
					dict(
						base,
						reason=(
							"There is no cost on record for this item, so a count cannot add it. "
							"Ask a Stock Manager to set its Valuation Rate."
						),
					)
				)
				continue
		plan["adjust"].append(
			dict(
				base,
				qty=target,
				current=current,
				valuation_rate=rate,
				clamped=clamped,
				reason_code=group.get("reason_code") or "",
				reason=group.get("reason") or "",
			)
		)
	return plan


def batches(rows, size=RECONCILIATION_ROWS):
	"""``rows`` in runs of at most ``size``, in order: one Stock Reconciliation each."""
	rows = list(rows or [])
	size = max(int(size or RECONCILIATION_ROWS), 1)
	return [rows[i : i + size] for i in range(0, len(rows), size)]


def reconciliation_comment(session, title, who, adjust, link=None, part=1, parts=1):
	"""The comment on a reconciliation a count made: which count, who counted it, how many of its
	items each reason covers, and each item's reason. HTML, everything typed escaped.

	A comment, not ``remarks``: Stock Reconciliation has no remarks field on v16 (checked on prod
	2026-10-23), so a remark set on it was dropped on save. ``link`` is the session's form URL;
	``part`` of ``parts`` when the count posted several reconciliations (:func:`batches`)."""
	esc = html.escape
	name = f'<a href="{esc(link)}">{esc(session)}</a>' if link else esc(session)
	head = f"From count {name}"
	if title:
		head += f" ({esc(title)})"
	if who:
		head += f", counted by {esc(who)}"
	head += f" on the Count Desk. {len(adjust)} item(s) adjusted here"
	if parts > 1:
		head += f", part {part} of {parts}"
	out = [f"<p>{head}.</p>"]
	totals = {}
	for row in adjust:
		code = row.get("reason_code") or ("Note only" if row.get("reason") else "No reason given")
		totals[code] = totals.get(code, 0) + 1
	if totals:
		order = sorted(totals, key=lambda code: (-totals[code], code))
		out.append("<p>Reasons: " + " · ".join(f"{esc(code)}: {totals[code]}" for code in order) + "</p>")
	items = []
	for row in adjust:
		why = " - ".join(x for x in (row.get("reason_code"), row.get("reason")) if x)
		if why:
			items.append(f"<li>{esc(row['item_code'])} at {esc(row['warehouse'])}: {esc(why)}</li>")
	if items:
		more = len(items) - COMMENT_ITEMS
		listed = "".join(items[:COMMENT_ITEMS])
		if more > 0:
			listed += f"<li>and {more} more on {esc(session)}</li>"
		out.append(f"<ul>{listed}</ul>")
	return "".join(out)


def reconciliation_names(value, first=None):
	"""Every reconciliation a count made, in order. ``value`` is the session's
	``stock_reconciliations`` (one name a line, as :func:`dump_names` writes it); ``first`` its
	``stock_reconciliation``, the only one a phone count, or a count posted before the field
	existed, has. It leads when the list lacks it."""
	text = value if isinstance(value, str) else "\n".join(str(v) for v in value or [])
	names = []
	for name in [first or "", *text.replace(",", "\n").split("\n")]:
		name = name.strip()
		if name and name not in names:
			names.append(name)
	return names


def dump_names(names):
	return "\n".join(reconciliation_names(names))


def combined_status(statuses):
	"""One word for several reconciliations' states (``Draft``, ``Submitted``, ``Cancelled``):
	theirs when they agree, ``Mixed`` when they do not, ``None`` for none."""
	found = {s for s in statuses or [] if s}
	if not found:
		return None
	return found.pop() if len(found) == 1 else "Mixed"


def result_message(plan, posted, drafts):
	"""The sentence after a post. ``posted`` / ``drafts`` are the reconciliation names made."""
	adjusted = len(plan["adjust"])
	matched = len(plan["matched"])
	skipped = len(plan["skipped"])
	bits = []
	if posted:
		bits.append(f"Posted {adjusted} adjustment(s) on {', '.join(posted)}.")
	elif drafts:
		bits.append(f"Drafted {adjusted} adjustment(s) on {', '.join(drafts)} for a Stock Manager to submit.")
	elif adjusted == 0:
		bits.append("Nothing needed adjusting.")
	if matched:
		bits.append(f"{matched} item(s) matched the system.")
	if skipped:
		bits.append(f"{skipped} item(s) were not posted; see the list.")
	return " ".join(bits)


# ---------------------------------------------------------------------------
# Coverage and history
# ---------------------------------------------------------------------------


def _date(value):
	if value in (None, ""):
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return datetime.date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def days_since(value, today):
	"""Whole days from ``value`` (a date, datetime or ISO text) to ``today``, or ``None``."""
	day = _date(value)
	if not day:
		return None
	return max((_date(today) - day).days, 0)


def latest(*values):
	"""The latest of several dates or datetimes (text or objects), as ``YYYY-MM-DD``, or ``None``."""
	days = [d for d in (_date(v) for v in values) if d]
	return max(days).isoformat() if days else None


def coverage(bins, today, days=COVERAGE_DAYS):
	"""How much of the stock has been counted lately: of the bins **holding stock**, how many have a
	count recorded in the last ``days`` (``last_counted``), as ``{"bins", "counted", "pct",
	"never"}``. An empty bin has nothing to count and is left out of both sides."""
	holding = [b for b in bins or [] if int(b.get("on_hand_items") or 0) > 0]
	counted = [b for b in holding if (days_since(b.get("last_counted"), today) is not None)]
	recent = [b for b in counted if days_since(b.get("last_counted"), today) <= days]
	return {
		"bins": len(holding),
		"counted": len(recent),
		"pct": round(len(recent) * 100.0 / len(holding), 1) if holding else None,
		"never": len(holding) - len(counted),
	}


def accuracy(lines):
	"""``(matched, counted)`` items over finalized counts' lines: the share counted right."""
	group_map = groups(lines)
	counted = len(group_map)
	matched = len([g for g in group_map.values() if abs(g["variance"]) <= TOLERANCE])
	return matched, counted
