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
ACTIONS = (ACTION_TAKE, ACTION_RECEIVE, ACTION_ADD_WITHOUT_PO, ACTION_MOVE)


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
# Undo
# ---------------------------------------------------------------------------


def undo_refusal(status, is_owner, is_supervisor, age_minutes, window_minutes):
	"""A sentence saying why a save cannot be undone, or ``None`` when it can.

	The person who saved it may undo it for ``window_minutes``; a Stock Manager may undo
	any save at any time, which is the same power they have over the voucher in the Desk.
	A save already undone cannot be undone twice.
	"""
	if status != "Posted":
		return "This has already been undone."
	if is_supervisor:
		return None
	if not is_owner:
		return "Only the person who saved this, or a Stock Manager, can undo it."
	window = _number(window_minutes)
	if window is None or window <= 0:
		return "Undo is switched off. Ask a Stock Manager to reverse it."
	age = _number(age_minutes)
	if age is None or age > window + TOLERANCE:
		return (
			f"Undo is only available for {plain(window)} minutes after saving. "
			"Ask a Stock Manager to reverse it."
		)
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


def remark(action, who, warehouse, qty, uom, from_warehouse=None, project=None, purchase_order=None):
	"""The ``remarks`` written on the voucher a save creates.

	The voucher is what accounting and the stock ledger show, so it has to say, without
	the Stock Scan Log beside it, that it came from a scan, who did it, and where.
	"""
	amount = f"{plain(qty)} {uom}".strip()
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
