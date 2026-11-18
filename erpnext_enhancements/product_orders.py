"""The rules behind the Projects Dashboard's Products tab, with no Frappe in them.

Nikolas, 2026-11-18 (vault ``decisions/2026-11-18-estop-product-line.md``): a product sale is a
Sales Order, not a Project. It runs Quotation -> Sales Order -> Work Order -> Delivery Note ->
Sales Invoice. Common configurations are built ahead to a Build Room shelf; everything else is
built to order. Custom-engineered control panels stay Projects of type "Products".

The tab's endpoint (``project_dashboard.get_products_data``) gathers the facts with SQL; the
decisions it makes from them live here, so ``tests/test_product_orders.py`` can hold them
without a bench:

* ``product_line`` / ``is_product_item``: which Items are products, and which product line
  (E-Stops, Control Panels, Other) each one belongs to;
* ``classify_lines`` (with ``is_product_document`` and ``skips_project``): **the shared rule**
  for a whole Quotation or Sales Order. The Products tab reads it, and so does quote to order
  (#1465), which must not keep its own copy. A document is a product sale only when every line is
  a product or an incidental line (freight, delivery); a product beside anything else is a job,
  and a job stays off the tab. ``needs_project`` is True for a product document with a control
  panel on it;
* ``order_stage``: the board column of one Sales Order (Ordered / Paid -> Building -> Testing
  -> Ready -> Delivered), with room for the pre-shipment test a later task records;
* ``allocate_stock``: which orders the finished units on the shelf already cover;
* ``paid_state``, ``is_late``, ``is_open_quotation``, ``is_open_opportunity``, ``win_rate``,
  ``component_state``: the smaller facts the tab shows.

**Where a control-panel order shows, once.** A product Sales Order that needs a project (a
control panel on it) is not on the order board. Its working record is its Project of type
Products, so it shows in the tab's Control panel projects section: on its Project's row (with the
order beside it), or, while it has no Project yet, as its own row marked "No project yet". The
board holds the product orders that get no project (E-Stops and the other standard products), so
no sale is listed twice and none is lost.

Dates are compared as ISO ``YYYY-MM-DD`` strings, so a ``datetime.date`` and a string read the
same way and nothing here depends on a time zone.
"""

import re

# ---------------------------------------------------------------------------------------------
# Product lines and the product-item rule
# ---------------------------------------------------------------------------------------------

LINE_ESTOPS = "E-Stops"
LINE_PANELS = "Control Panels"
LINE_OTHER = "Other"
#: The product-line chips on the tab, in their order.
LINES = (LINE_ESTOPS, LINE_PANELS, LINE_OTHER)

#: ``PDT-0040`` and every configuration generated from it (``PDT-0040-1-1-1-2-0``): the
#: Stillwater E-Stop.
ESTOP_FAMILIES = frozenset({"0040"})

#: The control panels Nikolas listed for phase P4 (2026-11-18), by their PDT family number.
CONTROL_PANEL_FAMILIES = frozenset(
	{
		# Splash Wizard Basic, Plus, Max, Industrial; 0017 is a second Industrial item
		"0000",
		"0001",
		"0002",
		"0003",
		"0017",
		# VFD Bypass with motor protection, 5 to 20 HP
		"0008",
		"0009",
		"0010",
		"0011",
		"0012",
		# Pool & Fountain Center Mini, Standard, Pinnacle
		"0014",
		"0015",
		"0016",
		# Level Master Water, Wind, Wind & Water
		"0020",
		"0021",
		"0022",
		# Activation Bollard Standard, Heavy Duty, Replacement Sensor
		"0055",
		"0056",
		"0057",
	}
)

#: PDT codes that are billing lines, not products. Checked on prod on 2026-11-18: PDT-0701
#: ("SERVICE - FOUNTAIN MATERIALS & EQUIPMENT") is on 325 invoice lines and PDT-0400
#: ("CHEMICALS - CHLORINE, ACID") on 178, nearly all of them service work. Counting them would
#: put most service quotes on the Products tab. PDT-0030 ("BUILD - SAPPHIRE CONNECT HARDWARE")
#: looks like the same "<stream> - ..." catch-all, but Nikolas ruled on 2026-11-18 that Sapphire
#: Connect hardware is a product: it is in the Other line, not here.
NOT_PRODUCT_FAMILIES = frozenset({"0400", "0701"})

# "PDT-" and four digits, then the end, a hyphen or a space. "PDT-0040-1-1-1-2-0" is family
# 0040; "PDT-00040 (deleted)" is not (its fifth character is a digit), and neither is "PDT-00XX".
_FAMILY = re.compile(r"^PDT-(\d{4})(?=$|[-\s])", re.IGNORECASE)


def pdt_family(item_code):
	"""The four-digit PDT family of an item code, or None when it has none."""
	match = _FAMILY.match(str(item_code or "").strip())
	return match.group(1) if match else None


def _line_of_family(family):
	if family in ESTOP_FAMILIES:
		return LINE_ESTOPS
	if family in CONTROL_PANEL_FAMILIES:
		return LINE_PANELS
	return LINE_OTHER


def product_line(item_code, source_configuration=None):
	"""The product line an Item belongs to, or None when it is not a product.

	An Item is a product when either:

	* the Product Configurator generated it (``Item.custom_source_configuration`` is set). Its
	  line comes from its code's PDT family, and is Other when the code has none; or
	* its code is in a PDT product family (``PDT-0040``, ``PDT-0040-1-1-1-2-0``, ``PDT-0003``),
	  apart from the billing families in ``NOT_PRODUCT_FAMILIES``.

	The item group "Configured Products" is deliberately not a rule of its own. On prod it holds
	13 hand-made Items that are not products (a first aid kit, a pipe union, a heat exchanger),
	and none of them carries ``custom_source_configuration``; the configurator stamps that field
	on every Item it makes, so the field is the configurator's own mark.
	"""
	family = pdt_family(item_code)
	if source_configuration:
		return _line_of_family(family) if family else LINE_OTHER
	if family is None or family in NOT_PRODUCT_FAMILIES:
		return None
	return _line_of_family(family)


def is_product_item(item_code, source_configuration=None):
	return product_line(item_code, source_configuration) is not None


def lines_of(items):
	"""The product lines of a document's lines, in chip order.

	``items`` holds dicts with ``item_code`` and, optionally, ``source_configuration``, or a
	``line`` already worked out. A line given is used as it is: worked out again from the code
	alone, a configured Item whose code has no PDT family would lose its line.
	"""
	found = set()
	for item in items or ():
		given = item.get("line")
		found.add(
			given if given in LINES else product_line(item.get("item_code"), item.get("source_configuration"))
		)
	return [line for line in LINES if line in found]


# ---------------------------------------------------------------------------------------------
# The document rule: is this Quotation or Sales Order a product sale, and does it need a project?
# ---------------------------------------------------------------------------------------------

#: Lines that ride along with a product and do not make a document a job: freight and delivery.
#: Item codes, read on prod on 2026-11-18. SRV-105 "BUILD - FREIGHT & DELIVERY" (23 quote lines),
#: SRV-206 "SERVICE - FREIGHT & DELIVERY", SRV-401 "RENT - FREIGHT & DELIVERY" and the plain
#: "Freight" Item. Kept explicit and short on purpose: a line not listed here makes the document a
#: job, which is the safe way to be wrong (a job keeps its project and stays off the tab). Add the
#: carrier-shipping Item here when quote to order adds one.
INCIDENTAL_ITEMS = frozenset({"SRV-105", "SRV-206", "SRV-401", "Freight"})

#: The PDT family test in SQL, for a count that cannot take a Python filter: MariaDB ``REGEXP``,
#: the same shape as ``_FAMILY`` (case-insensitive under the site's ``_ci`` collation). The billing
#: families are not excluded by it: add ``NOT REGEXP`` on ``NOT_PRODUCT_FAMILIES`` alongside.
PDT_FAMILY_SQL_REGEXP = "^PDT-[0-9]{4}([- ]|$)"

KIND_PRODUCT = "product"  # every line is a product or incidental, and at least one is a product
KIND_JOB = "job"  # at least one product line, and at least one line that is neither
KIND_NONE = "none"  # no product line at all (an empty document, or freight alone)


def is_incidental_item(item_code):
	return str(item_code or "").strip() in INCIDENTAL_ITEMS


def classify_lines(lines):
	"""Classify one Quotation's or Sales Order's lines. The rule #1464 and #1465 both read.

	``lines``: dicts with ``item_code`` and, optionally, ``source_configuration`` (the Item's
	``custom_source_configuration``), ``amount`` (the line's amount before tax) and ``line`` (a
	product line already worked out).

	* **A product document** (``KIND_PRODUCT``): every line is a product Item (``product_line``)
	  or an incidental one (``INCIDENTAL_ITEMS``), and at least one is a product. A configured
	  E-Stop plus freight is one; so is a hand-typed PDT-0040 E-Stop with no configuration.
	* **A job** (``KIND_JOB``): a product line beside anything else. A fountain quote that carries
	  one Splash Wizard (prod's SAL-QTN-2026-02070: $114,552.44 in all, $8,165.64 of it PDT-0002) is
	  a job: it stays off the Products tab and keeps its project. Mixed means job.
	* **Neither** (``KIND_NONE``): no product line.

	**Does it need a project?** Nikolas, 2026-11-18 (decision pick 1): E-Stops and standard
	products get no project; custom-engineered control panels keep a Project of type Products. So
	``needs_project`` is True for a product document with a Control Panels line, and for nothing
	else. A pure control-panel order is a product order **and** needs a project. (A job needs one
	too, by the old rule, but that is the caller's business: this answers for product documents.)

	Returns ``{"kind", "is_product", "needs_project", "lines", "product_lines",
	"incidental_lines", "job_lines", "product_amount"}``: ``lines`` are the product lines in chip
	order, ``product_lines`` the product line dicts (each with its ``line``), ``job_lines`` the item
	codes that made it a job, and ``product_amount`` the sum of the product lines' ``amount``, never
	the document's grand total.
	"""
	product, incidental, job = [], [], []
	for item in lines or ():
		code = item.get("item_code")
		given = item.get("line")
		line = given if given in LINES else product_line(code, item.get("source_configuration"))
		if line is not None:
			product.append(dict(item, line=line))
		elif is_incidental_item(code):
			incidental.append(item)
		else:
			job.append(code)
	if not product:
		kind = KIND_NONE
	elif job:
		kind = KIND_JOB
	else:
		kind = KIND_PRODUCT
	is_product = kind == KIND_PRODUCT
	return {
		"kind": kind,
		"is_product": is_product,
		"needs_project": is_product and any(p["line"] == LINE_PANELS for p in product),
		"lines": lines_of(product),
		"product_lines": product,
		"incidental_lines": incidental,
		"job_lines": job,
		"product_amount": sum(_num(p.get("amount")) for p in product),
	}


def is_product_document(lines):
	"""Every line is a product or incidental, and at least one is a product."""
	return classify_lines(lines)["is_product"]


def skips_project(lines):
	"""A product sale that gets no project: a product document with no control panel on it.

	The answer quote to order (#1465) needs: True means "the Sales Order is the record".
	"""
	result = classify_lines(lines)
	return result["is_product"] and not result["needs_project"]


# ---------------------------------------------------------------------------------------------
# The order board
# ---------------------------------------------------------------------------------------------

STAGE_ORDERED = "ordered"
STAGE_BUILDING = "building"
STAGE_TESTING = "testing"
STAGE_READY = "ready"
STAGE_DELIVERED = "delivered"
#: The board's columns, left to right.
STAGES = (STAGE_ORDERED, STAGE_BUILDING, STAGE_TESTING, STAGE_READY, STAGE_DELIVERED)
STAGE_LABELS = {
	STAGE_ORDERED: "Ordered / Paid",
	STAGE_BUILDING: "Building",
	STAGE_TESTING: "Testing",
	STAGE_READY: "Ready",
	STAGE_DELIVERED: "Delivered",
}

#: How long a delivered order stays on the board, so the Delivered column shows recent work
#: rather than every sale ever made.
DELIVERED_WINDOW_DAYS = 30

#: The pre-shipment test results the QC task will pass to ``order_stage`` (P1, TASK-2026-03221
#: tree). Until it lands, the endpoint passes None: the test is not recorded in ERPNext yet.
QC_PASSED = "passed"
QC_PENDING = "pending"
QC_FAILED = "failed"

# Work Order states that mean the work is over. "Closed" is ERPNext's "stop and produce no
# more": the build is finished as far as the order is concerned.
_WO_DONE = ("Completed", "Closed")
_WO_GONE = ("Cancelled",)


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _work_order_done(wo):
	if wo.get("status") in _WO_DONE:
		return True
	qty = _num(wo.get("qty"))
	return qty > 0 and _num(wo.get("produced_qty")) >= qty


def work_order_summary(work_orders):
	"""Split an order's Work Orders into what moves the board.

	Returns ``(drafts, live, done)``: drafts are unsubmitted (docstatus 0) and move nothing;
	live are submitted and not cancelled; done is True when there is at least one live Work
	Order and every one of them is finished.
	"""
	drafts, live = [], []
	for wo in work_orders or ():
		if wo.get("status") in _WO_GONE or int(_num(wo.get("docstatus"))) == 2:
			continue
		if int(_num(wo.get("docstatus"))) == 0:
			drafts.append(wo)
		else:
			live.append(wo)
	done = bool(live) and all(_work_order_done(wo) for wo in live)
	return drafts, live, done


def order_stage(per_delivered=0, work_orders=(), stock_covered=False, qc=None):
	"""The board column of one product Sales Order, and a short note on why.

	Facts, all from ERPNext:

	* ``per_delivered``: the Sales Order's own percentage, which ERPNext keeps from its
	  submitted Delivery Notes;
	* ``work_orders``: the order's Work Orders, dicts with ``status``, ``docstatus``, ``qty``
	  and ``produced_qty``;
	* ``stock_covered``: the finished units on hand cover what is left to deliver
	  (``allocate_stock``). A common configuration built ahead to the shelf ships from stock and
	  has no Work Order of its own;
	* ``qc``: the pre-shipment test, None while ERPNext does not record it. The QC task passes
	  ``QC_PASSED``, ``QC_PENDING`` or ``QC_FAILED``; until then a built unit goes straight to
	  Ready and the Testing column stays empty.

	The rules, in order:

	1. Delivered when ``per_delivered`` reaches 100.
	2. Building while a submitted Work Order is not finished (not started, in process or
	   stopped). A draft Work Order has not been released to the shop and moves nothing.
	3. Built when every submitted Work Order is finished, or, with none, when stock covers the
	   order. Built is Ready, unless the test is recorded and has not passed: then Testing.
	4. Otherwise Ordered / Paid: sold, nothing released to the shop yet.

	Returns ``{"stage": ..., "note": ...}``.
	"""
	if _num(per_delivered) >= 100:
		return {"stage": STAGE_DELIVERED, "note": "Delivered"}

	drafts, live, done = work_order_summary(work_orders)
	if live and not done:
		unfinished = [wo for wo in live if not _work_order_done(wo)]
		if any(wo.get("status") == "Stopped" for wo in unfinished):
			note = "Work Order stopped"
		elif any(_num(wo.get("produced_qty")) > 0 or wo.get("status") == "In Process" for wo in unfinished):
			note = "Work Order in process"
		else:
			note = "Work Order not started"
		return {"stage": STAGE_BUILDING, "note": note}

	built = done or (not live and stock_covered)
	if built:
		if qc is None or qc == QC_PASSED:
			return {"stage": STAGE_READY, "note": "Built" if done else "From stock"}
		return {
			"stage": STAGE_TESTING,
			"note": "Test failed" if qc == QC_FAILED else "Waiting for the pre-shipment test",
		}

	if drafts:
		return {"stage": STAGE_ORDERED, "note": "Work Order not submitted"}
	return {"stage": STAGE_ORDERED, "note": "No Work Order yet"}


def allocate_stock(orders, on_hand):
	"""The names of the orders that finished units on hand already cover.

	``orders``: dicts with ``name``, ``delivery_date``, ``built`` (every submitted Work Order of
	the order is finished), ``has_work_order`` (it has a submitted one) and ``lines``, each
	``{"item_code", "pending_qty"}`` with the quantity still to deliver. ``on_hand``: finished
	units per item code, across warehouses.

	An order built to order has its own units on the shelf, so those are set aside for it
	first and never cover a second order. What is left goes to the orders with no Work Order,
	earliest promise date first (no date last, then by name), and an order is covered only when
	every one of its product lines is. An order whose build is still going claims nothing.
	"""
	left = {code: _num(qty) for code, qty in (on_hand or {}).items()}

	def needs(order):
		need = {}
		for line in order.get("lines") or ():
			qty = _num(line.get("pending_qty"))
			if qty > 0:
				need[line.get("item_code")] = need.get(line.get("item_code"), 0) + qty
		return need

	def set_aside(need):
		for code, qty in need.items():
			left[code] = left.get(code, 0) - qty

	def take(order):
		need = needs(order)
		if not need or any(left.get(code, 0) < qty for code, qty in need.items()):
			return False
		set_aside(need)
		return True

	for order in orders or ():
		if order.get("built"):
			set_aside(needs(order))

	waiting = [o for o in orders or () if not o.get("has_work_order") and not o.get("built")]
	waiting.sort(key=lambda o: (_iso(o.get("delivery_date")) or "9999-12-31", str(o.get("name") or "")))
	return {o.get("name") for o in waiting if take(o)}


def paid_state(invoiced_total, outstanding_total, per_billed):
	"""Whether the order is paid, from its submitted, non-return Sales Invoices.

	``not_invoiced`` when nothing is invoiced; ``paid`` when nothing is outstanding and the
	whole order is billed; ``part_paid`` when some of it is paid, or all that is billed is paid
	but not all is billed; ``unpaid`` otherwise. Pay-at-order invoices the order at once
	(Nikolas, 2026-11-18), so a web sale reads Paid from the start.
	"""
	invoiced = _num(invoiced_total)
	outstanding = _num(outstanding_total)
	if invoiced <= 0:
		return "not_invoiced"
	if outstanding <= 0.005:
		return "paid" if _num(per_billed) >= 99.99 else "part_paid"
	if outstanding < invoiced - 0.005:
		return "part_paid"
	return "unpaid"


def _iso(value):
	if value is None or value == "":
		return ""
	return str(value)[:10]


def is_late(delivery_date, today, stage):
	"""Past its promise date and not delivered."""
	promise = _iso(delivery_date)
	return bool(promise) and stage != STAGE_DELIVERED and promise < _iso(today)


# ---------------------------------------------------------------------------------------------
# The sales pipeline
# ---------------------------------------------------------------------------------------------

#: The same won and lost as the Value Stream Performance report
#: (kpi_dashboards/report/value_stream_performance), so the tab's win rate and the report's
#: agree. tests/test_product_orders.py reads the report's constants and fails if they part.
WON_STATUSES = ("Closed Won",)
LOST_STATUSES = ("Lost",)
#: Statuses that end an Opportunity without winning or losing it.
CLOSED_STATUSES = ("Closed", "Converted")

#: Quotation statuses that are no longer open.
QUOTE_CLOSED_STATUSES = ("Lost", "Ordered", "Expired", "Cancelled")
#: A quotation with no Valid Till counts as open for this many days after its date. On prod
#: every product quote is a draft mirrored from QuickBooks with no Valid Till, back to 2016:
#: 147 of them, 13 from the last 90 days.
QUOTE_OPEN_DAYS = 90

#: The win rate's window.
WIN_RATE_DAYS = 365


def is_open_quotation(status, docstatus, valid_till, transaction_date, since, today):
	"""An open quotation: not cancelled, not closed, and still current.

	Current means its Valid Till is today or later; with no Valid Till, that it was made on or
	after ``since`` (``today`` minus ``QUOTE_OPEN_DAYS``).
	"""
	if int(_num(docstatus)) == 2 or status in QUOTE_CLOSED_STATUSES:
		return False
	if _iso(valid_till):
		return _iso(valid_till) >= _iso(today)
	return bool(_iso(transaction_date)) and _iso(transaction_date) >= _iso(since)


def is_open_opportunity(status):
	return status not in WON_STATUSES + LOST_STATUSES + CLOSED_STATUSES


def win_rate(won, lost):
	"""Won out of decided, as a whole percentage; None before anything is decided.

	Open deals have not lost, so they are left out (the Value Stream Performance report's rule).
	"""
	won, lost = int(_num(won)), int(_num(lost))
	if won + lost == 0:
		return None
	return round(100.0 * won / (won + lost))


# ---------------------------------------------------------------------------------------------
# Stock and shortages
# ---------------------------------------------------------------------------------------------

#: The component item groups of each product line. The Product Configurator files E-Stop parts
#: under "E-Stop Components" (seed_data.py); the other lines have no group yet.
COMPONENT_GROUPS = {LINE_ESTOPS: ("E-Stop Components",)}


def component_state(on_hand, reorder_level):
	"""``out`` at zero or below, ``low`` under a reorder level that is set, else ``ok``."""
	qty = _num(on_hand)
	if qty <= 0:
		return "out"
	level = _num(reorder_level)
	if level > 0 and qty < level:
		return "low"
	return "ok"
