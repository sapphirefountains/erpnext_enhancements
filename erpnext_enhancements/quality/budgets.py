# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Budget categories, reallocation, and what a spend figure is actually worth — WI-075 sub-phase M.

A project budget today is four flat fields on Project, and three of them are empty. Measured on
production 2026-09-14, across all 654 projects:

* ``estimated_costing`` is **zero on every single one** — unchanged from WI-057's count two months
  earlier, so that work item's backfill has not landed;
* ``custom_materials_budget`` is set on **4** projects, ``custom_time_budget_in_hours`` on **6**;
* ``custom_project_dollar_amount`` — the contract value, not a cost budget — is set on 73.

So this module builds the **structure** a budget is kept in. It does not invent the numbers, and
it is careful never to imply it has them. WI-057 owns the denominator and its acceptance criterion
is not restated here; what M adds is that once category lines exist the project total is their
sum, so a project manager who fills in the categories gets the denominator as a by-product rather
than as a second thing to remember.

Why ``committed`` and ``actual`` are not simply two more Currency columns
------------------------------------------------------------------------

The obvious shape for a budget line is ``category | budgeted | committed | actual``, and on this
site three of those four columns would read ``0.00`` forever while looking entirely correct.
Measured the same day:

* ``tabTimesheet`` holds **0 rows**. Not "few with a project" — the table is empty, so there are
  no labour actuals of any kind, for any project, anywhere.
* Submitted ``Purchase Invoice`` lines: **0**. So there are no material actuals either. Every
  ``actual`` on the site would be zero.
* Submitted project-tagged ``Purchase Order`` lines: 324, worth $106,242. Commitments do exist —
  but they **cannot be attributed to a category**, because 217 of those 324 lines carry item group
  ``Products``, a leaf sitting directly under ``All Item Groups``. The item group tree has no
  labour / materials / equipment / subcontract axis anywhere in it.

That is the failure this programme keeps finding in different costumes, and it always fails in the
same direction: *it returns a number, the number is about nothing, and nothing looks wrong.* It is
the trailing-space check that reports clean under PAD SPACE, and it is the reason this plan already
rejected core ``Supplier Scorecard`` — every core variable would compute cleanly off a denominator
nobody maintains.

So a spend figure here never travels alone. Each line carries a **coverage verdict** beside its
amount, and :func:`coverage` is the whole of the judgement:

``No Source``
    The category declares no instrument that could record spend against it. ``Contingency`` and
    ``Fee`` are this deliberately — money leaves them by *reallocation*, never by purchase, so a
    spend column against them is a category error rather than a gap. A zero here is correct.

``Not Tracked``
    The category names a source, and that source holds **no rows at all** on this site. This is
    Labour today: the category points at Timesheets and ``tabTimesheet`` is empty. A zero here
    means *nobody records this*, and must never be read as *nothing was spent*.

``Tracked``
    The source exists and is in use. A zero now carries its ordinary meaning — this project has
    not spent against this category.

Coverage is judged on whether the source holds rows **anywhere on the site**, not on this project.
That is deliberate: "the instrument is not in use" is a fact about the company, and a project that
genuinely has no purchase orders should read ``Tracked`` with zero rather than be told its data is
missing. The distinction is the entire point — it separates *we looked and found nothing* from
*nobody ever recorded this*, and those two zeros are not the same fact.

The same discipline governs :func:`variance`: a percentage of a zero budget is not zero and it is
not infinity, it is **unanswerable**, so the percentage comes back ``None``. Sub-phase L made the
identical call for an MSA with no expiry date, and for the identical reason.

The protected-category rule
---------------------------

``General Conditions``, ``Contingency`` and ``Fee`` are protected. A reallocation touching one on
**either** side needs a second approval, and both directions matter: taking money out of
Contingency to cover an overrun is how an overrun gets hidden, and moving money into Fee quietly
converts contracted work into margin. Neither is a project manager's decision alone.

And a second signature from the same hand is not a second signature — :func:`approval_errors`
refuses an additional approver who is also the requester or the project manager who approved it.

A reallocation is net zero, by construction
-------------------------------------------

:func:`apply_reallocation` moves money between two lines and never changes their total. The test
suite asserts that invariant directly, because it is what makes a reallocation safe to apply to a
Project whose ``estimated_costing`` is the sum of those lines: the derived total cannot move, so
applying one cannot silently rewrite the denominator WI-058 will eventually divide by. Amounts are
rounded to cents at every write so the invariant holds exactly rather than approximately.

Imports nothing. It lives in ``quality/`` rather than beside its DocTypes in
``project_enhancements/`` for the same reason as :mod:`~erpnext_enhancements.quality.change_orders`
and :mod:`~erpnext_enhancements.quality.scope_criteria`: that package's ``__init__`` imports
``frappe`` at module scope, so anything under it is unreachable from the bench-free test tier, and
there is no Frappe integration-test job in CI. A rule nobody can import is a rule nobody checks.
"""

#: The Table field on Project that holds the category lines. Named here rather than spelled out at
#: every call site so a test can assert the fixture and the code agree -- a fieldname typo would
#: read as "this project has no budget", silently, on every project.
LINES_FIELD = "custom_budget_lines"

# --- Categories -------------------------------------------------------------------------------

CATEGORY_LABOR = "Labor"
CATEGORY_MATERIALS = "Materials"
CATEGORY_EQUIPMENT = "Equipment"
CATEGORY_SUBCONTRACTORS = "Subcontractors"
CATEGORY_GENERAL_CONDITIONS = "General Conditions"
CATEGORY_CONTINGENCY = "Contingency"
CATEGORY_FEE = "Fee"

#: Categories a project manager may not move money into or out of on their own signature.
#: Both directions are controlled -- see the module docstring.
PROTECTED = (CATEGORY_GENERAL_CONDITIONS, CATEGORY_CONTINGENCY, CATEGORY_FEE)

# --- Where a spend figure could come from -----------------------------------------------------

SOURCE_NONE = "None"
SOURCE_PURCHASE_ORDERS = "Purchase Orders"
SOURCE_PURCHASE_INVOICES = "Purchase Invoices"
SOURCE_TIMESHEETS = "Timesheets"

SOURCES = (SOURCE_NONE, SOURCE_PURCHASE_ORDERS, SOURCE_PURCHASE_INVOICES, SOURCE_TIMESHEETS)

#: The doctype each source reads, so "does this instrument hold any rows at all" is answerable
#: without every caller hard-coding the mapping.
SOURCE_DOCTYPE = {
	SOURCE_PURCHASE_ORDERS: "Purchase Order",
	SOURCE_PURCHASE_INVOICES: "Purchase Invoice",
	SOURCE_TIMESHEETS: "Timesheet",
}

# --- What a spend figure is worth -------------------------------------------------------------

COVERAGE_TRACKED = "Tracked"
COVERAGE_NOT_TRACKED = "Not Tracked"
COVERAGE_NO_SOURCE = "No Source"

COVERAGE_STATES = (COVERAGE_TRACKED, COVERAGE_NOT_TRACKED, COVERAGE_NO_SOURCE)

#: The seven seeded categories: (name, is_protected, committed_source, actual_source, description).
#:
#: Read the two source columns as a statement of what this site can actually observe today, not as
#: an aspiration. Labour declares Timesheets and ``tabTimesheet`` is empty, so Labour spend reports
#: ``Not Tracked`` from the day this deploys -- which is the truth, and is more useful than a
#: confident $0.00. Contingency and Fee declare no source at all because money leaves them by
#: reallocation rather than by purchase.
SEED_CATEGORIES = (
	(
		CATEGORY_LABOR,
		0,
		SOURCE_NONE,
		SOURCE_TIMESHEETS,
		"Own crew hours. There is no purchase instrument that commits labour, so committed is "
		"blank by design. Actuals would come from Timesheets, which hold no rows on this site "
		"yet -- so labour spend reports Not Tracked rather than zero.",
	),
	(
		CATEGORY_MATERIALS,
		0,
		SOURCE_PURCHASE_ORDERS,
		SOURCE_PURCHASE_INVOICES,
		"Pipe, fittings, stone, pumps and everything else bought for the job.",
	),
	(
		CATEGORY_EQUIPMENT,
		0,
		SOURCE_PURCHASE_ORDERS,
		SOURCE_PURCHASE_INVOICES,
		"Rented or purchased plant -- lifts, pumps, compaction, temporary power.",
	),
	(
		CATEGORY_SUBCONTRACTORS,
		0,
		SOURCE_PURCHASE_ORDERS,
		SOURCE_PURCHASE_INVOICES,
		"Work bought in under a master agreement. The purchase order is the instrument -- see "
		"WI-075 sub-phase L.",
	),
	(
		CATEGORY_GENERAL_CONDITIONS,
		1,
		SOURCE_PURCHASE_ORDERS,
		SOURCE_PURCHASE_INVOICES,
		"Site overheads: supervision, facilities, access, waste. Protected -- it is the first "
		"place an overrun gets absorbed, which is precisely why moving money out of it needs a "
		"second signature.",
	),
	(
		CATEGORY_CONTINGENCY,
		1,
		SOURCE_NONE,
		SOURCE_NONE,
		"The reserve. Nothing is ever purchased against it: money leaves contingency by "
		"reallocation into the category that actually overran, which is what makes the overrun "
		"visible. A spend column against it would always read zero and would mean nothing.",
	),
	(
		CATEGORY_FEE,
		1,
		SOURCE_NONE,
		SOURCE_NONE,
		"Margin. Not a cost, and nothing is purchased against it. Protected, because moving "
		"money into fee converts contracted work into margin.",
	),
)


# --- Row access -------------------------------------------------------------------------------


def _get(row, field):
	"""Read ``field`` from a dict or from a Frappe child row, without caring which."""
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def _money(value):
	"""Coerce to cents-rounded float. ``None`` and unparseable values are zero.

	Rounding at every write is what keeps :func:`apply_reallocation` exactly net-zero rather than
	approximately net-zero, which matters because a Project's derived total is the sum of these.
	"""
	if value is None or value == "":
		return 0.0
	try:
		return round(float(value), 2)
	except (TypeError, ValueError):
		return 0.0


# --- Categories -------------------------------------------------------------------------------


def is_protected(category, protected=PROTECTED):
	"""Whether moving money into or out of ``category`` needs a second approval."""
	if not category:
		return False
	return category in (protected or ())


def seed_rows():
	"""The seven categories as dicts, ready for the seeding patch.

	Built from :data:`SEED_CATEGORIES` rather than written out a second time in the patch, so the
	protection flags and the declared sources cannot drift between the rule and the seed.
	"""
	rows = []
	for name, protected, committed_source, actual_source, description in SEED_CATEGORIES:
		rows.append(
			{
				"category_name": name,
				"is_protected": protected,
				"committed_source": committed_source,
				"actual_source": actual_source,
				"description": description,
			}
		)
	return rows


# --- What a spend figure is worth -------------------------------------------------------------


def coverage(source, source_has_rows):
	"""Whether a spend figure computed from ``source`` means anything.

	``source_has_rows`` is whether that source doctype holds **any rows at all on this site** --
	not on this project. See the module docstring: "the instrument is not in use" is a fact about
	the company, and a project that genuinely has no purchase orders should read ``Tracked`` with
	zero rather than be told its data is missing.
	"""
	if not source or source == SOURCE_NONE:
		return COVERAGE_NO_SOURCE
	if not source_has_rows:
		return COVERAGE_NOT_TRACKED
	return COVERAGE_TRACKED


def spend_is_meaningful(verdict):
	"""Whether a figure carrying ``verdict`` may be read as money.

	Both other verdicts mean the zero beside them is about the instrument rather than about the
	project, so a caller that totals or charts spend must skip them rather than add their zeros.
	"""
	return verdict == COVERAGE_TRACKED


def source_doctype(source):
	"""The doctype ``source`` reads, or ``None`` when it names no instrument."""
	return SOURCE_DOCTYPE.get(source)


# --- Budget lines -----------------------------------------------------------------------------


def line_for(lines, category):
	"""The first line on ``lines`` for ``category``, or ``None``."""
	if not category:
		return None
	for line in lines or ():
		if _get(line, "category") == category:
			return line
	return None


def total_budgeted(lines):
	"""The project total: the sum of every line.

	This is what a Project's ``estimated_costing`` becomes once category lines exist -- WI-057's
	denominator, arrived at by filling in the categories rather than by typing a second number.
	"""
	return round(sum(_money(_get(line, "budgeted_amount")) for line in (lines or ())), 2)


def duplicate_categories(lines):
	"""Categories appearing on more than one line, in first-seen order.

	Two lines for one category make every rollup ambiguous -- a reallocation would debit whichever
	row a query returned first, and the two would drift apart with nothing reporting it. The same
	shape as the overlapping MSA rate lines in sub-phase L.
	"""
	seen = []
	dupes = []
	for line in lines or ():
		category = _get(line, "category")
		if not category:
			continue
		if category in seen and category not in dupes:
			dupes.append(category)
		elif category not in seen:
			seen.append(category)
	return dupes


def negative_lines(lines):
	"""Categories budgeted below zero.

	A negative budget is never a deliberate entry; it is the residue of a reallocation that took
	more than a line held, which is why :func:`reallocation_errors` refuses that at source.
	"""
	out = []
	for line in lines or ():
		if _money(_get(line, "budgeted_amount")) < 0:
			category = _get(line, "category")
			if category:
				out.append(category)
	return out


def variance(budgeted, spent):
	"""Over/under against a budget, with the percentage left unanswered when it cannot be had.

	Returns ``(delta, percent)``. ``delta`` is positive when spend exceeds budget. ``percent`` is
	``None`` -- never ``0``, never a large number -- when the budget is zero, because a proportion
	of nothing is unanswerable rather than complete. Sub-phase L made the same call for an MSA
	with no recorded expiry: unknown is a third answer, and collapsing it into either of the other
	two produces a confident wrong one.
	"""
	budget = _money(budgeted)
	actual = _money(spent)
	delta = round(actual - budget, 2)
	if budget == 0:
		return delta, None
	return delta, round(actual / budget * 100.0, 1)


def unclassified_amount(project_total, classified_total):
	"""Project spend that carries no budget category.

	Reported rather than distributed. Spreading it across categories would invent an attribution
	nobody made, and dropping it would under-report the job -- silently, and in the direction that
	looks clean. Same rule as the purchase-order project union in sub-phase L: name the gap and
	count it.
	"""
	return round(_money(project_total) - _money(classified_total), 2)


# --- Reallocation -----------------------------------------------------------------------------


def requires_second_approval(from_category, to_category, protected=PROTECTED):
	"""Whether this reallocation needs an approver above the project manager.

	Either side triggers it. Out of a protected category is how an overrun gets absorbed quietly;
	into one is how contracted work becomes margin.
	"""
	return is_protected(from_category, protected) or is_protected(to_category, protected)


def reallocation_errors(from_category, to_category, amount, lines, protected=PROTECTED):
	"""Every reason this reallocation must not be applied. An empty list means it may.

	A list rather than a raise-on-first: a form that reports one problem, is corrected, and then
	reports the next is three round trips for a person who could have been told everything at once.
	The same shape as ``change_orders.blocking_reasons``.
	"""
	errors = []
	moved = _money(amount)

	if not from_category:
		errors.append("Name the category the money comes from.")
	if not to_category:
		errors.append("Name the category the money goes to.")
	if from_category and to_category and from_category == to_category:
		errors.append(
			"A reallocation moves money between two different categories; "
			f"{from_category} is named on both sides."
		)

	if moved <= 0:
		# A negative amount is not a reallocation the other way -- it is a reallocation whose
		# from/to labels are lies, and every report reading them would be wrong. Reverse the
		# categories instead.
		errors.append("Enter an amount greater than zero.")

	if from_category:
		source_line = line_for(lines, from_category)
		if source_line is None:
			errors.append(
				f"This project has no {from_category} budget line, so there is nothing to "
				"move from."
			)
		elif moved > 0:
			available = _money(_get(source_line, "budgeted_amount"))
			if moved > available:
				errors.append(
					f"{from_category} holds {available:,.2f}; moving {moved:,.2f} would "
					"leave it negative."
				)

	return errors


def approval_errors(requested_by, pm_approved_by, second_approved_by, needs_second):
	"""Why this reallocation is not yet cleared to submit.

	The segregation rule is the point: a second signature from the same hand is not a second
	signature. An additional approver who is also the requester, or who is the project manager who
	already approved it, is refused -- which is what stops the protected-category control from
	being satisfiable by one person clicking twice.
	"""
	errors = []

	if not pm_approved_by:
		errors.append("Awaiting project manager approval.")

	if needs_second:
		if not second_approved_by:
			errors.append(
				"This reallocation touches a protected category and needs a second approval."
			)
		else:
			if requested_by and second_approved_by == requested_by:
				errors.append(
					"The second approval cannot come from the person who requested it."
				)
			if pm_approved_by and second_approved_by == pm_approved_by:
				errors.append(
					"The second approval cannot come from the project manager who approved it."
				)
	elif second_approved_by:
		# Not an error. Someone senior signed a reallocation that did not need it, which is a
		# stricter outcome than the rule requires and never a reason to refuse the document.
		pass

	return errors


def apply_reallocation(lines, from_category, to_category, amount):
	"""Return a **new** list of ``{category, budgeted_amount}`` with the move applied.

	Pure: the caller's rows are not touched, so a controller can compute the result, check the
	invariant and only then write. The ``to`` line is created when the project does not have one
	yet -- a project manager moving contingency into a category the job did not originally carry
	is an ordinary thing to do, and making them add an empty line first is friction with no
	control value. The ``from`` line must already exist, because you cannot take from nothing.

	The total is unchanged. That is asserted directly in the test suite rather than left implied,
	because it is what makes this safe to apply to a Project whose derived total is that sum.
	"""
	moved = _money(amount)
	out = []
	seen_to = False

	for line in lines or ():
		category = _get(line, "category")
		budgeted = _money(_get(line, "budgeted_amount"))
		if category == from_category:
			budgeted = round(budgeted - moved, 2)
		if category == to_category:
			budgeted = round(budgeted + moved, 2)
			seen_to = True
		out.append({"category": category, "budgeted_amount": budgeted})

	if to_category and not seen_to:
		out.append({"category": to_category, "budgeted_amount": moved})

	return out


def describe_reallocation(from_category, to_category, amount, protected=PROTECTED):
	"""One line for a timeline comment, saying what moved and whether it needed two signatures."""
	moved = _money(amount)
	source = from_category or "(unnamed)"
	target = to_category or "(unnamed)"
	text = f"Moved {moved:,.2f} from {source} to {target}."
	if requires_second_approval(from_category, to_category, protected):
		text += " Touches a protected category, so it required a second approval."
	return text
