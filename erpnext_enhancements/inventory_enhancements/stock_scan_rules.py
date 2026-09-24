# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The decisions behind the Stock Scan page. **No Frappe, no I/O.**

``api/stock_scan.py`` does the reads and writes; every judgement it makes about *what* a
scan means, *whether* a quantity is acceptable, *who* may undo a save, and *what* a
printed label says is made here, so it runs bench-free in CI. There is no Frappe
integration-test job, so bench-free code is the only code that runs on every push (the
same split as :mod:`item_naming_rules` and ``procurement_quantities``).

Nothing here raises. Functions return a value or a sentence in the user's terms, and the
caller decides what a refusal means.

**What a QR label encodes.** A full URL, ``https://<site>/stock-scan?w=<Warehouse name>``,
not a bare code. The phone's own camera app opens a URL straight into the browser, so the
first scan of a run needs no app and no button; a bare code would open a web search. The
in-page scanner reads the same labels, so one label serves both. :func:`parse_scan` accepts
any host on purpose: a label printed on the test site must still resolve on production,
where the warehouse has the same name.
"""

import datetime
import math
import re
from urllib.parse import parse_qs, quote, unquote_plus, urlsplit

#: The page every label points at. ``www/stock-scan.html``; its controller is
#: ``www/stock_scan.py`` because Frappe never imports a hyphenated controller.
SCAN_ROUTE = "/stock-scan"

#: Query keys the page understands, in the order :func:`parse_scan` tries them.
#: ``w`` is the one printed on labels; ``item`` and ``loc`` exist so an item label or a
#: Storage Location label can reuse the page later without a second format.
QUERY_KINDS = (("w", "warehouse"), ("item", "item"), ("loc", "storage_location"))

#: The largest quantity one save may move. Not a business rule: a guard against a
#: thumb resting on a key, or a barcode scanned into the quantity field, turning into a
#: ledger entry for 4006381333931 units.
MAX_QTY = 100000

TOLERANCE = 1e-9

#: Who may use the page. ``Stock User`` is held by 17 of the 18 enabled staff on
#: production (2026-09-23), technicians included, and carries create/submit/cancel on
#: Stock Entry and Purchase Receipt in stock ERPNext — so the gate admits the people the
#: framework would already let post the voucher by hand, and the framework still checks
#: the voucher itself (nothing here posts with ``ignore_permissions``). ``Inventory Clerk``
#: is this app's counting role; a clerk without Stock User gets a clear refusal at the
#: voucher rather than a page that will not open.
SCAN_ROLES = frozenset({"System Manager", "Stock Manager", "Stock User", "Inventory Clerk"})

#: Who may undo anybody's save at any time, and read everybody's recent activity.
SUPERVISOR_ROLES = frozenset({"System Manager", "Stock Manager"})

#: Who may print labels: everyone who scans, plus the people who maintain warehouses.
LABEL_ROLES = SCAN_ROLES | {"Item Manager"}

#: Project statuses after which no more parts are charged to a job. A **deny**-list, not an
#: allow-list of "Open": this site renames ERPNext's Open to ``Active`` and adds ``Client
#: Hold``, ``Parked``, ``Invoiced`` and ``Paid`` (Property Setter ``Project-status-options``).
#: On 2026-09-23 production had 411 Active projects and **zero** Open ones, so the first
#: draft of this page — which filtered on Open — offered an empty job picker and refused
#: every job. ``Cancelled`` is ERPNext's own spelling, kept so the rule also holds on a site
#: without the Property Setter.
CLOSED_JOB_STATUSES = frozenset({"Completed", "Invoiced", "Paid", "Canceled", "Cancelled"})

#: The status the picker lists first: the jobs people are actually working.
ACTIVE_JOB_STATUS = "Active"

#: Values of ``Stock Scan Log.action``. Renaming one later is a data migration (a stored
#: Select value outside the options makes the row unsaveable), so they are named for what
#: the person did rather than for the ERPNext voucher behind it.
ACTION_TAKE = "Take"
ACTION_RECEIVE = "Receive"
ACTION_ADD_WITHOUT_PO = "Add Without PO"
ACTION_MOVE = "Move"
#: "Bought on a store run" (v1.535.0, POL-0602 §4.7): a submitted Purchase Receipt with no
#: purchase order, one per line, flagged for review. Appended LAST: the Select's order is
#: this tuple's, and the existing values keep their positions.
ACTION_STORE_RUN = "Store Run"
ACTIONS = (ACTION_TAKE, ACTION_RECEIVE, ACTION_ADD_WITHOUT_PO, ACTION_MOVE, ACTION_STORE_RUN)

# ---------------------------------------------------------------------------
# Store runs (v1.535.0)
# ---------------------------------------------------------------------------

#: The three reasons a technician gives for a store run, stored verbatim in
#: ``Stock Scan Log.store_run_reason`` (whose Select options are these, after a blank that keeps
#: the other actions valid). Purchasing acts on each differently, which is the point of asking.
REASON_OUT_OF_STOCK = "A stocked item was out"
REASON_NOT_STOCKED = "Not something we stock"
REASON_ONLY_THIS_JOB = "Only for this job"
STORE_RUN_REASONS = (REASON_OUT_OF_STOCK, REASON_NOT_STOCKED, REASON_ONLY_THIS_JOB)

#: What each reason means to Purchasing, shown under it on the page.
REASON_HINTS = {
	REASON_OUT_OF_STOCK: "The minimum is too low",
	REASON_NOT_STOCKED: "Twice in 60 days means add it to the kit",
	REASON_ONLY_THIS_JOB: "Fine",
}

#: "Twice in 60 days means add it to the kit": the second run for an unstocked item inside the
#: window flags the line. Runs, not lines -- four of one part split over two bins is one run.
REPEAT_WINDOW_DAYS = 60
REPEAT_THRESHOLD = 2

#: The dearest single part a counter sells, as a guard rather than a rule: a barcode scanned
#: into the price field must not become a $4,006,381,333,931 receipt.
MAX_UNIT_PRICE = 100000
MAX_RECEIPT_TOTAL = 1000000

#: The units a quick Item may be created in. The unit is fixed at creation -- ERPNext refuses
#: to change a stock UOM once stock has moved -- so the choice is short and deliberate.
QUICK_ITEM_UOMS = ("Unit", "FT", "Gallon")

#: Longest code or name a quick Item may carry (Frappe's ``name`` column and ``item_name``).
MAX_ITEM_TEXT = 140

#: A run id is a client reference with this prefix: ``sr-<time36>-<rand10>``.
RUN_REF_PREFIX = "sr-"

#: How many open runs the page offers to add to.
MAX_OPEN_RUNS = 5

#: Who may undo a store-run line after the undo window, or a line someone else recorded.
#: Stock Manager is NOT here, deliberately: 15 of the 16 people who scan hold it, every
#: technician among them, so the supervisor bypass every other save has would mean a
#: technician could cancel a receipt weeks later, after Accounting matched it, and the run
#: would silently drop out of the KPI. The people who answer for the purchase are Purchasing
#: and Accounts.
STORE_RUN_UNDO_ROLES = frozenset({"Purchase Manager", "Accounts Manager"})


# ---------------------------------------------------------------------------
# What was scanned
# ---------------------------------------------------------------------------


def parse_scan(raw):
	"""Classify raw scanned or typed text as ``(kind, value)``.

	``kind`` is ``"warehouse"``, ``"item"`` or ``"storage_location"`` when ``raw`` is one of
	this page's own URLs carrying ``w=``, ``item=`` or ``loc=``, and ``"text"`` for anything
	else — a bare code the caller looks up (an item barcode, an item code, a warehouse
	name typed by hand). ``("", "")`` for blank input.

	Any scheme and host are accepted, and a relative ``/stock-scan?...`` too, because only
	the path and the query identify a label. A URL to some *other* page is returned as
	text rather than guessed at.
	"""
	# str(), not ``raw or ""``: a JSON caller may send a barcode as a number, and ``.strip()``
	# on an int would raise. The page's parseScan does the same (String(raw)).
	text = ("" if raw is None else str(raw)).strip()
	if not text:
		return "", ""

	lowered = text.lower()
	looks_like_url = "://" in lowered or lowered.startswith(SCAN_ROUTE)
	if looks_like_url:
		try:
			parts = urlsplit(text)
		except ValueError:
			# urlsplit raises on a malformed netloc -- an unbalanced "[" or "]" ("Invalid IPv6
			# URL"), or a host that NFKC-normalizes into one containing "/", "?", "#" or "@".
			# Such a string is not one of our labels; returning it as text keeps the promise
			# above, and a raise here would be a 500 from resolve() on a stray scan.
			return "text", text
		path = (parts.path or "").rstrip("/")
		if path.lower().endswith(SCAN_ROUTE):
			query = parse_qs(parts.query, keep_blank_values=False)
			for key, kind in QUERY_KINDS:
				values = query.get(key)
				if values and values[0].strip():
					return kind, values[0].strip()
		return "text", text

	return "text", text


def scan_url(base_url, warehouse):
	"""The URL printed in a warehouse's QR code: ``<base>/stock-scan?w=<name>``.

	Spaces become ``%20`` rather than ``+``: both decode the same, but ``%20`` is what
	every phone's QR preview shows readably, and ``+`` is a literal plus to anything that
	decodes a path rather than a query.
	"""
	base = (base_url or "").rstrip("/")
	return f"{base}{SCAN_ROUTE}?w={quote(warehouse or '', safe='')}"


def login_redirect(full_path, fallback=SCAN_ROUTE):
	"""``/login?redirect-to=<full_path>``, encoded so the whole query comes back after login.

	``frappe.utils.quoted`` (what other shells here use) leaves ``&``, ``=`` and ``?`` raw.
	The login page reads ``redirect-to`` by splitting its own query on ``&``, so
	``/warehouse-labels?under=Row%201&size=avery-5163&skip=3`` came back as just the ``under``
	part, and ``?w=A&w=B`` as just ``A``. Encoding everything but ``/`` makes it one value,
	decoded exactly once on the way back.
	"""
	return "/login?redirect-to=" + quote(full_path or fallback, safe="/")


def decode_param(value):
	"""A query value as the page's JavaScript would decode it (``+`` is a space)."""
	return unquote_plus(value or "")


# ---------------------------------------------------------------------------
# Which items the page can move
# ---------------------------------------------------------------------------


def item_refusal(item, today=None):
	"""A sentence saying why the page cannot add or take ``item``, or ``None`` when it can.

	``item`` is the Item's row as a dict. Each refusal is something ERPNext would refuse
	anyway, later and in its own words — or, for serial and batch items, would *not*
	refuse but should: on submit it auto-picks serial numbers FIFO
	(``auto_create_serial_and_batch_bundle_for_outward``), which is wrong for serialised
	equipment someone may claim a warranty on. The page shows the item either way; only
	the stepper is withheld.
	"""
	if not item:
		return "No such item."
	name = item.get("item_name") or item.get("item_code") or item.get("name") or "This item"
	if item.get("disabled"):
		return f"{name} is disabled."
	end_of_life = item.get("end_of_life")
	if end_of_life and today and str(end_of_life)[:10] <= str(today)[:10]:
		return f"{name} has reached its end of life."
	if not item.get("is_stock_item"):
		return (
			f"{name} is not tracked in inventory (Maintain Stock is off on the item), "
			"so there is nothing to add or take."
		)
	if item.get("has_variants"):
		return f"{name} is a template. Scan or search for one of its variants instead."
	if item.get("has_serial_no") or item.get("has_batch_no"):
		return (
			f"{name} is tracked by serial or batch number. Use a Stock Entry in the Desk "
			"so the right serial or batch is recorded."
		)
	if item.get("is_customer_provided_item"):
		return f"{name} is supplied by the customer and carries no cost. Use a Stock Entry in the Desk."
	return None


# ---------------------------------------------------------------------------
# Quantities
# ---------------------------------------------------------------------------


def _number(value):
	if value is None or value == "":
		return None
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	if math.isnan(number) or math.isinf(number):
		return None
	return number


def check_qty(value, whole_number=False, uom=""):
	"""``(qty, problem)`` for a quantity typed or tapped on the page.

	The page sends the *size* of the change and the endpoint says its direction, so a
	valid quantity is always positive. ``whole_number`` is the stock UOM's
	``must_be_whole_number``: ERPNext would refuse 2.5 Units on submit, and saying so
	first names the unit instead of a conversion-factor error.
	"""
	qty = _number(value)
	if qty is None:
		return None, "Enter a quantity."
	if qty <= TOLERANCE:
		return None, "The quantity must be more than zero."
	if qty > MAX_QTY:
		return None, f"{plain(qty)} is more than one save can move. Check the number and try again."
	if whole_number and abs(qty - round(qty)) > TOLERANCE:
		return None, f"{uom or 'This unit'} is counted in whole numbers, so {plain(qty)} cannot be used."
	return qty, None


def check_take(qty, on_hand, warehouse_name=""):
	"""A sentence when taking ``qty`` would drive the location below zero, else ``None``.

	Production refuses negative stock (``Stock Settings.allow_negative_stock = 0``), so
	ERPNext would refuse this anyway — with a message about a Stock Ledger Entry. This one
	says what the technician can do about it.
	"""
	have = _number(on_hand) or 0.0
	if qty > have + TOLERANCE:
		where = f" at {warehouse_name}" if warehouse_name else " here"
		if have <= TOLERANCE:
			return (
				f"The system shows none on hand{where}. If you are holding it, it was never put "
				"away here: use Move here to bring it from where it is recorded, then take it."
			)
		return f"Only {plain(have)} on hand{where}, so {plain(qty)} cannot be taken."
	return None


def to_order_uom(stock_qty, conversion_factor, whole_number=False, uom=""):
	"""Convert a stock-UOM quantity into a Purchase Order line's own UOM.

	Returns ``(qty, problem)``. The page counts in the item's stock UOM; a Purchase
	Order line may be in another (a Box of 10), and ERPNext's receipt quantity is in the
	line's UOM. A result that is not whole in a whole-number UOM is refused here rather
	than rounded: rounding would receive goods that did not arrive.
	"""
	factor = _number(conversion_factor) or 1.0
	if factor <= TOLERANCE:
		factor = 1.0
	qty = stock_qty / factor
	if whole_number and abs(qty - round(qty)) > 1e-6:
		return None, (
			f"This order counts in {uom or 'another unit'} of {plain(factor)}. "
			f"{plain(stock_qty)} is not a whole number of those."
		)
	if abs(qty - round(qty)) <= 1e-6:
		qty = float(round(qty))
	return qty, None


def plain(value):
	"""A quantity for a sentence: ``3`` not ``3.0``, ``2.5`` stays ``2.5``."""
	number = _number(value)
	if number is None:
		return str(value)
	if abs(number - round(number)) <= 1e-9:
		return str(int(round(number)))
	return f"{number:.6f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Where a receipt came from
# ---------------------------------------------------------------------------


def order_line_sort_key(line):
	"""Oldest promise first: required-by date, then order date, then order name.

	A line with no date sorts after every dated line rather than before, so a
	placeholder never jumps the queue ahead of an order a supplier actually committed to.
	"""
	schedule = line.get("schedule_date")
	ordered = line.get("transaction_date")
	return (
		schedule is None,
		str(schedule or ""),
		ordered is None,
		str(ordered or ""),
		str(line.get("purchase_order") or ""),
		int(line.get("idx") or 0),
	)


def pending_stock_qty(line):
	"""What is still to come on a Purchase Order line, in the item's stock UOM.

	``qty`` and ``received_qty`` on ``Purchase Order Item`` are in the line's own UOM
	(the same arithmetic ``make_purchase_receipt`` pre-fills with), so the remainder is
	scaled by ``conversion_factor`` to compare with what the page counts.
	"""
	pending = (_number(line.get("qty")) or 0.0) - (_number(line.get("received_qty")) or 0.0)
	factor = _number(line.get("conversion_factor")) or 1.0
	return max(pending, 0.0) * (factor if factor > TOLERANCE else 1.0)


# ---------------------------------------------------------------------------
# Store runs (v1.535.0, POL-0602 §4.7-4.8)
# ---------------------------------------------------------------------------

#: What QuickBooks leaves on an Item it deleted: the code gains this suffix and the record
#: stays. 135 of them on 2026-09-24, all non-stock. Matched case-insensitively, as
#: ``item_naming_rules.DELETED_MARKER`` is.
DELETED_MARKER = "(deleted)"


def store_run_item_refusal(item, today=None):
	"""Why ``item`` cannot be a line of a store run, or ``None`` when it can.

	:func:`item_refusal` with one difference: an item that is **not** kept in stock is
	allowed. On 2026-09-24, 660 of 1,086 Items were non-stock -- 524 of them live -- and what a
	crew buys at a counter is mostly tools and consumables, exactly those. Refusing them left
	the technician one way to finish: invent a new code for a part that already exists, which
	is the duplicate POL-0602 §4.8 and the naming guard exist to stop. ERPNext accepts a
	non-stock line on a Purchase Receipt and posts neither stock nor GL for it (provisional
	accounting is off on production), so the receipt records the store, price, job and photo
	and nothing else. A QuickBooks tombstone is refused: it is a deleted record, not a part.
	"""
	if not item:
		return "No such item."
	name = item.get("item_name") or item.get("item_code") or item.get("name") or "This item"
	code = str(item.get("item_code") or item.get("name") or "")
	if DELETED_MARKER in code.lower():
		return f"{code} is a record QuickBooks deleted. Pick the live item, or create a new one."
	if item.get("is_stock_item"):
		return item_refusal(item, today)
	# Non-stock: item_refusal's own checks, minus "Maintain Stock is off" (and serial and batch,
	# which only a stock item can carry).
	if item.get("disabled"):
		return f"{name} is disabled."
	end_of_life = item.get("end_of_life")
	if end_of_life and today and str(end_of_life)[:10] <= str(today)[:10]:
		return f"{name} has reached its end of life."
	if item.get("has_variants"):
		return f"{name} is a template. Scan or search for one of its variants instead."
	if item.get("is_customer_provided_item"):
		return f"{name} is supplied by the customer and carries no cost. Use a Stock Entry in the Desk."
	return None


def _money_number(value):
	"""A price or total typed on a phone: ``$4.97``, ``1,234.50`` and ``4.97`` all read 4.97."""
	if isinstance(value, str):
		value = value.strip().replace("$", "").replace(",", "")
	return _number(value)


def check_price(value):
	"""``(rate, problem)`` for the price each, before tax, as printed on the receipt.

	The same shape as :func:`check_qty`. Required and more than zero: a store-run line at $0
	would receive its stock at no cost, which makes every later issue of it cost nothing.
	"""
	rate = _money_number(value)
	if rate is None:
		return None, "Enter the price each, before tax, as it is on the receipt."
	if rate <= TOLERANCE:
		return None, "The price must be more than zero."
	if rate > MAX_UNIT_PRICE:
		return None, f"{money(rate)} each is more than a counter sells anything for. Check the price."
	return rate, None


def check_receipt_total(value):
	"""``(total, problem)`` for the paper receipt's total, tax included.

	Asked once per run. It is what the card is charged, so it is what the store-run KPI
	matches a card charge on and what the run's spend is before the charge arrives. It also
	lets Accounting see the tax without a tax template on the receipt.
	"""
	total = _money_number(value)
	if total is None:
		return None, "Enter the receipt's total, tax included."
	if total <= TOLERANCE:
		return None, "The receipt total must be more than zero."
	if total > MAX_RECEIPT_TOTAL:
		return None, f"{money(total)} is more than one receipt. Check the total."
	return total, None


def _as_date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return datetime.date.fromisoformat(str(value or "")[:10])
	except ValueError:
		return None


def purchase_date(bought, today):
	"""The day a store run was bought: ``today`` or the day before, else ``None``.

	POL-0602 §4.7 says to record a run the same day. Yesterday is allowed because a run made
	at the end of a shift is recorded the next morning, and the log flags it
	(:func:`recorded_late`). Anything older is recorded in the Desk by Purchasing, who can
	see the card statement.
	"""
	day = _as_date(today)
	if day is None:
		return None
	choice = str(bought or "").strip().lower()
	if choice == "today":
		return day
	if choice == "yesterday":
		return day - datetime.timedelta(days=1)
	return None


def run_is_open(started_on, today):
	"""Whether a run started (first line recorded) on ``started_on`` still takes lines.

	For the whole day it was started, and for anyone. Not "six hours after its last line":
	a crew that stops for lunch between the counter and the shop, or two people who shopped
	together and record their halves, would otherwise each open a second run for one trip,
	and the KPI would count the trip twice.
	"""
	start, day = _as_date(started_on), _as_date(today)
	return bool(start and day and start == day)


def recorded_late(bought_on, posted_on):
	"""True when a run was recorded on a later day than it was bought (the policy says same day)."""
	bought, posted = _as_date(bought_on), _as_date(posted_on)
	return bool(bought and posted and bought < posted)


def repeat_unstocked(reason, prior_runs):
	"""True for the second (or later) run in :data:`REPEAT_WINDOW_DAYS` that bought an item as
	"Not something we stock". ``prior_runs`` counts earlier RUNS, the current one excluded, so
	one part split over two bins in one run is not a repeat."""
	if reason != REASON_NOT_STOCKED:
		return False
	count = _number(prior_runs) or 0
	return count >= REPEAT_THRESHOLD - 1


def is_run_ref(value):
	"""Whether ``value`` has the shape of a run id the page mints (``sr-<time36>-<rand10>``)."""
	text = str(value or "")
	return text.startswith(RUN_REF_PREFIX) and bool(re.fullmatch(r"[A-Za-z0-9._:-]{8,80}", text))


def store_key(name):
	"""The store a Supplier name means, for grouping: ``Lowes`` and ``Lowe's`` are one store.

	Two Suppliers are both ticked as store-run vendors for Lowe's (QuickBooks vendors 1015 and
	2720). Whether to merge the records is Purchasing's call; until then the picker shows one
	and the KPI counts one.
	"""
	return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def store_picker(rows):
	"""The stores the page offers, one per :func:`store_key`, the newest Supplier of each.

	``rows`` are Supplier rows (``name``, ``supplier_name``, ``creation``, ``disabled``,
	``on_hold``). Disabled and on-hold suppliers are dropped FIRST: collapsing first could keep
	a record a receipt then refuses and hide the usable one behind it.
	"""
	best = {}
	for row in rows or ():
		if row.get("disabled") or row.get("on_hold"):
			continue
		label = row.get("supplier_name") or row.get("name") or ""
		key = store_key(label) or store_key(row.get("name"))
		if not key:
			continue
		kept = best.get(key)
		if kept is None or str(row.get("creation") or "") > str(kept.get("creation") or ""):
			best[key] = row
	out = [
		{
			"supplier": row.get("name"),
			"supplier_name": row.get("supplier_name") or row.get("name"),
			"key": key,
		}
		for key, row in best.items()
	]
	out.sort(key=lambda r: natural_key(r["supplier_name"]))
	return out


def quick_item_problem(spec, groups, uoms):
	"""Why a quick Item cannot be created as described, or ``None``.

	``spec`` is ``{item_code, item_name, item_group, stock_uom}``; ``groups`` the leaf Item
	Groups; ``uoms`` the units offered (:data:`QUICK_ITEM_UOMS` that are enabled). The naming
	guard (``item_naming_guard``) still runs on insert; this is only what makes an insert
	pointless to try.
	"""
	spec = spec if isinstance(spec, dict) else {}
	code = str(spec.get("item_code") or "").strip()
	name = str(spec.get("item_name") or "").strip()
	if not code:
		return "Enter the part or model number, exactly as printed."
	if not name:
		return "Enter a name for the item."
	if len(code) > MAX_ITEM_TEXT:
		return f"The part number is longer than {MAX_ITEM_TEXT} characters."
	if len(name) > MAX_ITEM_TEXT:
		return f"The name is longer than {MAX_ITEM_TEXT} characters."
	if re.search(r"[<>]", code):
		return "A part number cannot contain < or >."
	if (spec.get("item_group") or "") not in set(groups or ()):
		return "Choose a group from the list."
	if (spec.get("stock_uom") or "") not in set(uoms or ()):
		return f"Choose one of the units offered: {', '.join(uoms or QUICK_ITEM_UOMS)}."
	return None


def money(value):
	"""``$4.97``, ``$1,234.50``; four places when a unit price needs them (``$0.1250``)."""
	number = _number(value)
	if number is None:
		return str(value)
	if abs(number * 100 - round(number * 100)) > 1e-6:
		return f"${number:,.4f}"
	return f"${number:,.2f}"


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


def undo_refusal(
	status,
	is_owner,
	is_supervisor,
	age_minutes,
	window_minutes,
	store_run=False,
	is_purchasing=False,
	reviewed=False,
):
	"""A sentence saying why a save cannot be undone, or ``None`` when it can.

	The person who saved it may undo it for ``window_minutes``; a Stock Manager may undo
	any save at any time, which is the same power they have over the voucher in the Desk.
	A save already undone cannot be undone twice.

	A **store-run** line (``store_run``) is a purchase, and other people answer for it. Once
	Purchasing has reviewed it (``reviewed``) nobody undoes it from the page: the receipt may
	already be matched to its card charge. Before that, Purchasing and Accounts
	(:data:`STORE_RUN_UNDO_ROLES`, ``is_purchasing``) may undo it at any time, and everyone
	else -- Stock Managers included, which is every technician -- only their own line, inside
	the window.
	"""
	if status != "Posted":
		return "This has already been undone."
	who = "a Stock Manager"
	if store_run:
		if reviewed:
			return "Purchasing has reviewed this store run, so it can't be undone here. Ask Purchasing."
		if is_purchasing:
			return None
		if not is_owner:
			return "Only the person who recorded this store run, or Purchasing, can undo it."
		is_supervisor = False
		who = "Purchasing"
	if is_supervisor:
		return None
	if not is_owner:
		return "Only the person who saved this, or a Stock Manager, can undo it."
	window = _number(window_minutes)
	if window is None or window <= 0:
		return f"Undo is switched off. Ask {who} to reverse it."
	age = _number(age_minutes)
	if age is None or age > window + TOLERANCE:
		return f"Undo is only available for {plain(window)} minutes after saving. Ask {who} to reverse it."
	return None


# ---------------------------------------------------------------------------
# Printed labels
# ---------------------------------------------------------------------------

#: Label stock the print page lays out, keyed by the ``size`` query value. Geometry in
#: inches, from the manufacturers' templates; each Avery sheet sums to exactly 8.5 x 11 in,
#: which :func:`preset_fits_page` checks in CI so a typo cannot shift every label a
#: sixteenth off its die-cut. A label printer takes one label per page.
LABEL_PRESETS = {
	"avery-5160": {
		"title": "Avery 5160 / 8160 sheet: 30 labels, 2⅝ × 1 in",
		"page_width": 8.5,
		"page_height": 11.0,
		"columns": 3,
		"rows": 10,
		"width": 2.625,
		"height": 1.0,
		"top": 0.5,
		"left": 0.1875,
		"column_gap": 0.125,
		"row_gap": 0.0,
	},
	"avery-5163": {
		"title": "Avery 5163 / 8163 sheet: 10 labels, 4 × 2 in",
		"page_width": 8.5,
		"page_height": 11.0,
		"columns": 2,
		"rows": 5,
		"width": 4.0,
		"height": 2.0,
		"top": 0.5,
		"left": 0.15625,
		"column_gap": 0.1875,
		"row_gap": 0.0,
	},
	"thermal-2x1": {
		"title": "Label printer: one 2 × 1 in label per page",
		"page_width": 2.0,
		"page_height": 1.0,
		"columns": 1,
		"rows": 1,
		"width": 2.0,
		"height": 1.0,
		"top": 0.0,
		"left": 0.0,
		"column_gap": 0.0,
		"row_gap": 0.0,
	},
}
DEFAULT_LABEL_PRESET = "avery-5160"


def label_preset(key):
	"""``(key, preset)`` for a ``size`` query value, falling back to the default sheet."""
	key = (key or "").strip().lower()
	if key not in LABEL_PRESETS:
		key = DEFAULT_LABEL_PRESET
	return key, LABEL_PRESETS[key]


def preset_fits_page(preset):
	"""True when a preset's margins, labels and gaps add up to its page, both ways."""
	across = (
		2 * preset["left"]
		+ preset["columns"] * preset["width"]
		+ (preset["columns"] - 1) * preset["column_gap"]
	)
	down = preset["top"] + preset["rows"] * preset["height"] + (preset["rows"] - 1) * preset["row_gap"]
	return abs(across - preset["page_width"]) < 1e-6 and down <= preset["page_height"] + 1e-6


def natural_key(text):
	"""Sort key that puts ``Bin B1-2-9`` before ``Bin B1-2-10``.

	The warehouse tree's own order (``lft``) is creation order, and on production that
	already reads ``Bin C2-3-6`` before ``Bin C2-3-5``; a sheet of labels peeled onto a
	shelf in that order puts two of them on the wrong bins.
	"""
	return [
		(0, int(part), "") if part.isdigit() else (1, 0, part.lower())
		for part in re.split(r"(\d+)", text or "")
		if part
	]


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def location_trail(ancestors):
	"""The breadcrumb under a location's name: ``Row 2 › Bay B1 › Shelf B1-2``.

	``ancestors`` are the warehouse's group parents, outermost first, as their
	``warehouse_name``. The tree's root ("All Warehouses") says nothing on a shelf label
	and is dropped.
	"""
	names = [name for name in (ancestors or []) if name and name.strip().lower() != "all warehouses"]
	return " › ".join(names)


def remark(
	action,
	who,
	warehouse,
	qty,
	uom,
	from_warehouse=None,
	project=None,
	purchase_order=None,
	supplier=None,
	reason=None,
	run=None,
	non_stock=False,
):
	"""The ``remarks`` written on the voucher a save creates.

	The voucher is what accounting and the stock ledger show, so it has to say, without
	the Stock Scan Log beside it, that it came from a scan, who did it, and where.

	A store run names the store, the reason, the job (or that there was none) and the run id,
	because the receipt carries no project of its own (see ``api.stock_scan._store_run_receipt``)
	and the run id is how Accounting finds the other receipts of the same trip.
	"""
	amount = f"{plain(qty)} {uom}".strip()
	if action == ACTION_STORE_RUN:
		what = f"{amount} (not a stock item)" if non_stock else amount
		where = f"at {supplier}" if supplier else "at a counter"
		job = f"Job: {project}." if project else "No job (safety or shop)."
		return (
			f"Stock Scan: {who} bought {what} {where} on a store run, into {warehouse}. "
			f"Reason: {reason or 'not given'}. {job} Run {run or '?'}, flagged for review."
		)
	if action == ACTION_TAKE:
		text = f"Stock Scan: {who} took {amount} from {warehouse}"
	elif action == ACTION_RECEIVE:
		text = f"Stock Scan: {who} received {amount} into {warehouse}"
		if purchase_order:
			text += f" against {purchase_order}"
	elif action == ACTION_ADD_WITHOUT_PO and project:
		# A return from a job: say so, and name the job in the sentence rather than as the
		# trailing "for <job>" a take gets.
		return (
			f"Stock Scan: {who} returned {amount} from {project} to {warehouse} "
			"without a purchase order (flagged for review)."
		)
	elif action == ACTION_ADD_WITHOUT_PO:
		text = (
			f"Stock Scan: {who} added {amount} to {warehouse} without a purchase order (flagged for review)"
		)
	elif action == ACTION_MOVE:
		text = f"Stock Scan: {who} moved {amount} from {from_warehouse} to {warehouse}"
	else:
		text = f"Stock Scan: {who} changed {amount} at {warehouse}"
	if project:
		text += f" for {project}"
	return text + "."
