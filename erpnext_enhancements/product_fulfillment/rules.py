"""The rules behind building, testing and shipping a product unit, with no Frappe in them.

Nikolas, 2026-11-18 (vault ``decisions/2026-11-18-estop-product-line.md``): a product sale is a
Sales Order. Each unit is built from a Work Order, gets a serial number, and must pass the
Pre-Shipment Functional Test before it can ship. Logan Penrod or Nathan Cox builds each unit and the
other one tests it: **the tester is never the builder.** Two or three units of the common
configuration wait on a Build Room shelf; every other configuration is built to order.

The Frappe half (``build.py``, ``qc_gate.py``, ``serials.py``) gathers the facts; the decisions it
makes from them live here, so ``tests/test_product_fulfillment_rules.py`` can hold them without a
bench:

* ``plan_line``, ``free_stock``, ``line_claim``: what a product Sales Order line takes from the shelf
  and what it builds, one Work Order per unit;
* ``resolve_warehouses``: the build, work-in-progress and finished-units warehouses a Work Order
  gets, from Product Build Settings first, and whether it can be submitted with them;
* ``checklist_rows``, ``checklist_progress``: the configurator's build steps as a Work Order
  checklist;
* ``needs_test``, ``tester_problem``, ``unit_state``, ``delivery_gate``: the QC gate. A Delivery
  Note line of a configured product needs one serial number per unit, and every serial needs a
  passed test, signed by someone other than the person who built it;
* ``order_qc``, ``stock_qc``: the Products tab's Testing column.

Users are compared without case or surrounding spaces: ERPNext user ids are email addresses, and
``Logan@...`` and ``logan@...`` are one person.
"""

import math

from erpnext_enhancements import product_orders as po

# ---------------------------------------------------------------------------------------------
# Planning a Sales Order line: from the shelf, or built
# ---------------------------------------------------------------------------------------------


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def free_stock(on_hand, claims=()):
	"""Finished units on hand that no other open order has a claim on.

	``claims``: the quantities still to deliver on other open product order lines for the same Item
	that have no Work Order of their own (an order being built for claims its own unit, not the
	shelf's). The same rule the Products tab applies (``product_orders.allocate_stock``).
	"""
	return max(0.0, _num(on_hand) - sum(_num(c) for c in claims or ()))


def line_claim(pending_qty, has_work_order, produced_qty=0, delivered_qty=0):
	"""What one open order line claims of the finished units on the shelf.

	* A line with no Work Order will take what it still has to deliver from the shelf.
	* A line being built for claims only the units its Work Orders already made and it has not
	  delivered yet: they sit on the same shelf, counted in on hand, waiting for their own order.
	  Its units still being built are not on the shelf yet, so they claim nothing.

	All quantities in stock units. Never more than the line still has to deliver, never negative.
	"""
	pending = max(0.0, _num(pending_qty))
	if not has_work_order:
		return pending
	return min(pending, max(0.0, _num(produced_qty) - _num(delivered_qty)))


def plan_line(pending_qty, free_qty, planned_qty=0):
	"""What one product Sales Order line ships from stock, and how many units to build.

	* ``pending_qty``: what the line still has to deliver, in stock units;
	* ``free_qty``: finished units of the same Item on the shelf that nobody else claims
	  (``free_stock``);
	* ``planned_qty``: units already being built for this line (its Work Orders that are not
	  cancelled; a Work Order closed short counts only what it produced). Planning again never
	  builds twice.

	A built-ahead configuration with units on the shelf ships from stock; any shortfall is built.
	Units are whole: E-Stops are counted in Nos, and a fraction left by a conversion factor rounds
	up to a whole unit to build, never down to a missing one.

	Returns ``{"from_stock": n, "to_build": units}``, both whole numbers.
	"""
	need = max(0.0, _num(pending_qty) - _num(planned_qty))
	from_stock = min(need, max(0.0, _num(free_qty)))
	to_build = need - from_stock
	return {"from_stock": int(math.floor(from_stock + 1e-9)), "to_build": int(math.ceil(to_build - 1e-9))}


def planned_qty(work_orders):
	"""Units already being built for one Sales Order line, from its Work Orders.

	A cancelled Work Order builds nothing. A Closed one ("stop, produce no more") built only what it
	produced. Any other submitted or draft one will build its whole quantity.
	"""
	total = 0.0
	for wo in work_orders or ():
		if wo.get("status") == "Cancelled" or int(_num(wo.get("docstatus"))) == 2:
			continue
		if wo.get("status") == "Closed":
			total += _num(wo.get("produced_qty"))
		else:
			total += _num(wo.get("qty"))
	return total


# ---------------------------------------------------------------------------------------------
# Warehouses
# ---------------------------------------------------------------------------------------------


def resolve_warehouses(settings, line_warehouse=None, manufacturing=None):
	"""The warehouses one Work Order gets, and whether it can be submitted with them.

	``settings``: Product Build Settings (``build_warehouse``, ``wip_warehouse``,
	``finished_warehouse``). All three default to empty: Nikolas picks the Build Room shelves later,
	and nothing here hard-codes a warehouse. ``manufacturing``: Manufacturing Settings'
	``default_wip_warehouse`` and ``default_fg_warehouse`` (both empty on prod, read 2026-11-18).

	* build (where parts are picked from): the setting, else nothing. ERPNext then takes each part
	  from its own Item Default warehouse;
	* work in progress: the setting, else Manufacturing Settings' default;
	* finished units: the setting, else the Sales Order line's delivery warehouse (where the
	  Delivery Note will look for the unit), else Manufacturing Settings' default.

	ERPNext refuses to submit a Work Order without a work-in-progress and a finished-goods warehouse,
	so ``can_submit`` is False while either is missing, and ``missing`` names them.
	"""
	settings = settings or {}
	manufacturing = manufacturing or {}
	source = settings.get("build_warehouse") or None
	wip = settings.get("wip_warehouse") or manufacturing.get("default_wip_warehouse") or None
	fg = (
		settings.get("finished_warehouse")
		or line_warehouse
		or manufacturing.get("default_fg_warehouse")
		or None
	)
	missing = [label for label, value in (("work in progress", wip), ("finished units", fg)) if not value]
	return {"source": source, "wip": wip, "fg": fg, "missing": missing, "can_submit": not missing}


# ---------------------------------------------------------------------------------------------
# The build checklist
# ---------------------------------------------------------------------------------------------

STEP_BUILD = "Build"
STEP_QC = "QC"


def checklist_rows(steps):
	"""The configurator's resolved build steps as Work Order checklist rows.

	``steps``: a Product Configuration's ``build_steps`` (``section_title``, ``step_type``,
	``instruction``), already filtered and rendered for that configuration when it was saved
	(``engine.buildsteps.render_build_steps``). Copied, never linked: a step edited on the product
	later must not change what a unit already being built was told to do. Blank instructions are
	dropped. Both kinds are kept; only Build rows are ticked (``checklist_progress``), and the QC
	rows are shown to the tester as notes for this configuration.
	"""
	rows = []
	for step in steps or ():
		instruction = str(step.get("instruction") or "").strip()
		if not instruction:
			continue
		step_type = STEP_QC if str(step.get("step_type") or "").strip() == STEP_QC else STEP_BUILD
		rows.append(
			{
				"section_title": str(step.get("section_title") or "").strip(),
				"step_type": step_type,
				"instruction": instruction,
			}
		)
	return rows


def checklist_progress(rows):
	"""``{"done", "total", "complete"}`` over the Build rows; QC rows are the tester's."""
	build = [row for row in rows or () if (row.get("step_type") or STEP_BUILD) != STEP_QC]
	done = sum(1 for row in build if int(_num(row.get("done"))))
	return {"done": done, "total": len(build), "complete": bool(build) and done == len(build)}


# ---------------------------------------------------------------------------------------------
# Who built it, who tested it
# ---------------------------------------------------------------------------------------------


def same_person(a, b):
	"""Two user ids name the same person (case and surrounding spaces ignored); blanks never do."""
	a = str(a or "").strip().lower()
	b = str(b or "").strip().lower()
	return bool(a) and a == b


NO_BUILDER = "no_builder"
NO_TESTER = "no_tester"
TESTER_IS_BUILDER = "tester_is_builder"
SIGNER_NOT_TESTER = "signer_not_tester"


def tester_problem(tester, builder, signer=None):
	"""Why a test cannot stand, or None.

	* ``no_builder``: nobody is recorded as having built the unit, so nobody can show the tester is
	  someone else. Record the builder on the unit's Work Order;
	* ``no_tester``: the test names no tester (Quality Inspection's Inspected By);
	* ``tester_is_builder``: the person who built the unit tested it;
	* ``signer_not_tester``: the person submitting the test is not the tester it names. Submitting
	  is the signature, so a test is signed by the person who did it (only checked when ``signer``
	  is given, at submit).
	"""
	if not str(builder or "").strip():
		return NO_BUILDER
	if not str(tester or "").strip():
		return NO_TESTER
	if same_person(tester, builder):
		return TESTER_IS_BUILDER
	if signer is not None and not same_person(signer, tester):
		return SIGNER_NOT_TESTER
	return None


# ---------------------------------------------------------------------------------------------
# The QC gate
# ---------------------------------------------------------------------------------------------


def needs_test(is_stock_item, source_configuration):
	"""A delivered line needs the pre-shipment test: a configured product that is kept in stock.

	The configurator's own mark (``Item.custom_source_configuration``) decides, the same mark the
	Products tab trusts. A non-stock Item moves no unit (the hand-typed ``PDT-0040`` QuickBooks quotes
	used), so there is nothing to serial or test.
	"""
	return bool(int(_num(is_stock_item))) and bool(str(source_configuration or "").strip())


ACCEPTED = "Accepted"
REJECTED = "Rejected"

UNIT_PASSED = "passed"
UNIT_FAILED = "failed"
UNIT_UNTESTED = "untested"
#: An accepted test that cannot stand (built and tested by one person, or no builder recorded).
UNIT_TESTER_IS_BUILDER = TESTER_IS_BUILDER
UNIT_NO_BUILDER = NO_BUILDER


def _test_key(test):
	# The newest test decides, by when it was signed: a submitted test's ``modified`` is its submit
	# (nothing on a submitted Quality Inspection is editable after that), then when it was made, then
	# its name. Never by ``report_date``, which the tester types: a failed re-test dated a day early
	# would otherwise sort below the pass it overturned.
	return (
		str(test.get("modified") or ""),
		str(test.get("creation") or ""),
		str(test.get("name") or ""),
	)


def counted_tests(tests, item_code=None, template=None):
	"""The tests that count for a unit: submitted, Accepted or Rejected, for its Item, and on the
	pre-shipment template when one is set. A draft has not been signed; a cancelled test was taken
	back (ERPNext overwrites its status with Cancelled)."""
	out = []
	for test in tests or ():
		if int(_num(test.get("docstatus"))) != 1:
			continue
		if test.get("status") not in (ACCEPTED, REJECTED):
			continue
		if item_code and test.get("item_code") and test.get("item_code") != item_code:
			continue
		if template and test.get("quality_inspection_template") != template:
			continue
		out.append(test)
	return out


def latest_test(tests, item_code=None, template=None):
	counted = counted_tests(tests, item_code, template)
	return max(counted, key=_test_key) if counted else None


def unit_state(tests, builder, item_code=None, template=None):
	"""Where one unit stands with its pre-shipment test.

	The newest counted test decides: a unit that failed and was fixed and passed is passed; one that
	passed and then failed a re-test is failed. A passing test only stands when someone other than
	the recorded builder signed it (``tester_problem``); otherwise the unit reads
	``tester_is_builder`` or ``no_builder`` and does not ship.
	"""
	test = latest_test(tests, item_code, template)
	if test is None:
		return UNIT_UNTESTED
	if test.get("status") == REJECTED:
		return UNIT_FAILED
	problem = tester_problem(test.get("inspected_by"), builder)
	if problem == NO_BUILDER:
		return UNIT_NO_BUILDER
	if problem in (TESTER_IS_BUILDER, NO_TESTER):
		return UNIT_TESTER_IS_BUILDER
	return UNIT_PASSED


SERIALS_MISSING = "serials_missing"


def delivery_gate(rows, tests_by_serial, builders, template=None):
	"""Every problem that stops a Delivery Note from shipping tested product units. Empty means go.

	``rows``: the Delivery Note lines that need the test (``needs_test``), dicts with ``idx``,
	``item_code``, ``stock_qty`` (units in the stock UOM) and ``serials`` (the serial numbers picked
	on the line). ``tests_by_serial``: ``{serial: [test, ...]}``, Quality Inspections with
	``name``, ``docstatus``, ``status``, ``item_code``, ``inspected_by``, ``modified``,
	``creation`` and ``quality_inspection_template``. ``builders``: ``{serial: user}``.

	Each line needs one serial number per unit. The gate does not let ERPNext pick serials on its
	own at submit ("Auto Create Serial and Batch Bundle For Outward" is on, on prod): first in,
	first out would ship whichever unit is oldest, tested or not. Then each serial needs
	``unit_state`` passed.

	Returns dicts ``{"idx", "item_code", "serial", "code"}``, ``serial`` None for a line problem.
	"""
	problems = []
	for row in rows or ():
		serials = [s for s in (row.get("serials") or ()) if str(s or "").strip()]
		units = int(math.ceil(_num(row.get("stock_qty")) - 1e-9))
		if len(serials) < units:
			problems.append(
				{
					"idx": row.get("idx"),
					"item_code": row.get("item_code"),
					"serial": None,
					"code": SERIALS_MISSING,
				}
			)
		for serial in serials:
			state = unit_state(
				(tests_by_serial or {}).get(serial),
				(builders or {}).get(serial),
				row.get("item_code"),
				template,
			)
			if state != UNIT_PASSED:
				problems.append(
					{
						"idx": row.get("idx"),
						"item_code": row.get("item_code"),
						"serial": serial,
						"code": state,
					}
				)
	return problems


#: What each gate code means, for the message a person reads. The Frappe half wraps these in
#: ``_()``; they are kept here so a test can hold every code to a sentence.
GATE_MESSAGES = {
	SERIALS_MISSING: "pick the serial number of each unit on this line (one per unit)",
	UNIT_UNTESTED: "has no submitted Pre-Shipment Functional Test",
	UNIT_FAILED: "failed its latest Pre-Shipment Functional Test; fix it and test it again",
	UNIT_TESTER_IS_BUILDER: "was tested by the person who built it; someone else must test it",
	UNIT_NO_BUILDER: "has no recorded builder, so nobody can show the tester is someone else",
}


# ---------------------------------------------------------------------------------------------
# The Products tab's Testing column
# ---------------------------------------------------------------------------------------------


def order_qc(unit_states):
	"""The test result of one order's built units, for ``product_orders.order_stage``.

	``unit_states``: ``unit_state`` of every unit its finished Work Orders produced. Passed when
	every unit passed, failed when the newest test of any unit failed, otherwise pending (untested,
	or a test that cannot stand). None when there are no units to judge: the stage rule then keeps
	its own answer.
	"""
	states = list(unit_states or ())
	if not states:
		return None
	if all(state == UNIT_PASSED for state in states):
		return po.QC_PASSED
	if any(state == UNIT_FAILED for state in states):
		return po.QC_FAILED
	return po.QC_PENDING


def stock_qc(passed_on_hand, pending_qty):
	"""An order covered from the shelf: passed when the tested units on hand cover it.

	Which shelf unit ships is picked on the Delivery Note, so the board only asks whether enough
	units of that Item on hand have passed.
	"""
	if _num(pending_qty) <= 0:
		return None
	return po.QC_PASSED if _num(passed_on_hand) >= _num(pending_qty) else po.QC_PENDING


# ---------------------------------------------------------------------------------------------
# Serial numbers
# ---------------------------------------------------------------------------------------------

#: The serial number series configured Items get when Product Build Settings names none. "SF-26-00001"
#: in 2026: short enough for a label, unique across every product, and the year says the unit's age.
DEFAULT_SERIAL_SERIES = "SF-.YY.-.#####"

#: Warranty: 1 year parts and labor at the shop from the delivery date (Nikolas, 2026-11-18).
DEFAULT_WARRANTY_DAYS = 365


def valid_serial_series(series):
	"""A naming series ERPNext can count with: it has a run of ``#`` for the number."""
	series = str(series or "").strip()
	return bool(series) and "#" in series and len(series) <= 140
