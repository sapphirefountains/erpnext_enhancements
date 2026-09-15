# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Filling in what a project has committed and spent, per budget category — WI-075 sub-phase M.

The decisions are all in :mod:`erpnext_enhancements.quality.budgets`, which imports no ``frappe``
and is therefore the part CI actually runs. This module is the glue: it asks the database the
three questions that module cannot ask, and hands back the answers.

What it will report on the day it deploys, and why that is the point
--------------------------------------------------------------------

Nothing. Every project on production has zero budget lines, so :func:`refresh` returns on its
first line and no query runs at all. As lines appear, measured 2026-09-14:

* **Actuals will report Not Tracked everywhere.** ``tabTimesheet`` holds 0 rows and no Purchase
  Invoice has submitted lines, so both actual sources are empty. That verdict is the honest
  answer and it is the whole reason the column exists — a confident ``0.00`` in its place would
  say *this project spent nothing*, which is false, rather than *nobody records this*, which is
  true.
* **Committed will report real money, and will under-report it at first.** 324 submitted
  project-tagged purchase-order lines carry $106,242 between them, and none of them yet names a
  budget category, because the field to name one with ships in this release. Until buyers start
  using it, that money lands in the unclassified bucket :func:`spend_for_project` returns
  separately — reported, never distributed across categories and never dropped.

Distributing it would invent an attribution nobody made; dropping it would under-report the job
silently, in the direction that looks clean. Same rule as the purchase-order project union in
sub-phase L, which is also the reason both queries here use
``ifnull(nullif(line.project, ''), parent.project)``: either field alone loses orders, and on
PRJ-00566 a row-only match returns 37 lines where the union returns 63.

Cost on a Project save
----------------------

:func:`refresh` runs from Project ``validate``, so it is on the path of every Project save on the
site. It does nothing and costs nothing when the project has no budget lines, which is every
project today. When lines do exist it runs at most three grouped queries. A project's budget is
edited rarely and read often, so recomputing on save rather than on read is the cheaper side of
that trade, and it keeps the stored figure and the lines from disagreeing.
"""

import frappe

from erpnext_enhancements.quality import budgets

#: Bucket key for project spend that names no budget category. Not a category name -- it must
#: never collide with one, and it must never be silently folded into one.
UNCLASSIFIED = "__unclassified__"

#: The Custom Field on Purchase Order Item that attributes a line to a budget category.
PO_CATEGORY_FIELD = "custom_budget_category"


def source_has_rows(source):
	"""Whether the instrument behind ``source`` holds **any** rows on this site.

	The question is deliberately site-wide rather than per project -- see
	:func:`erpnext_enhancements.quality.budgets.coverage`. A project with no purchase orders
	should read ``Tracked`` with zero; a site with no timesheets at all should read
	``Not Tracked``, because that zero is about the instrument and not about the job.

	Cached per request: this is asked once per budget line and the answer cannot change inside
	one save.
	"""
	doctype = budgets.source_doctype(source)
	if not doctype:
		return False

	cache = frappe.local.__dict__.setdefault("_ee_budget_source_rows", {})
	if doctype in cache:
		return cache[doctype]

	if doctype not in budgets.SOURCE_DOCTYPE.values():
		# The table name is interpolated below, so it may only ever come from our own mapping.
		return False

	try:
		has_rows = bool(frappe.db.sql(f"select name from `tab{doctype}` limit 1"))
	except Exception:
		# A missing table is not an error here: it means the instrument is not in use, which is
		# exactly what Not Tracked says. Hooks on this path also fire during ERPNext's own test
		# bootstrap, before several of these tables exist.
		has_rows = False

	cache[doctype] = has_rows
	return has_rows


def _committed_by_category(project):
	"""Submitted purchase-order value for ``project``, grouped by budget category.

	``ifnull(nullif(poi.project, ''), po.project)`` is the union from sub-phase L, defined in
	Python as ``msa.order_project`` and required by test to agree with it. Either field alone
	drops orders, silently.
	"""
	if not frappe.db.has_column("Purchase Order Item", PO_CATEGORY_FIELD):
		return {}

	rows = frappe.db.sql(
		f"""
		select ifnull(nullif(poi.{PO_CATEGORY_FIELD}, ''), %(unclassified)s) as category,
		       sum(poi.base_amount) as amount
		  from `tabPurchase Order Item` poi
		  join `tabPurchase Order` po on po.name = poi.parent
		 where po.docstatus = 1
		   and ifnull(nullif(poi.project, ''), po.project) = %(project)s
		 group by category
		""",
		{"project": project, "unclassified": UNCLASSIFIED},
		as_dict=True,
	)
	return {row.category: row.amount or 0 for row in rows}


def _invoiced_by_category(project):
	"""Submitted purchase-invoice value for ``project``, grouped by budget category.

	Returns ``{}`` until the same Custom Field exists on Purchase Invoice Item. It does not ship
	in this release: no Purchase Invoice on this site has submitted lines, so the column it would
	populate has nothing to populate it with, and a field nobody can fill is a field nobody
	maintains. Actuals report ``Not Tracked`` in the meantime, which is the true answer.
	"""
	if not frappe.db.has_column("Purchase Invoice Item", PO_CATEGORY_FIELD):
		return {}

	rows = frappe.db.sql(
		f"""
		select ifnull(nullif(pii.{PO_CATEGORY_FIELD}, ''), %(unclassified)s) as category,
		       sum(pii.base_amount) as amount
		  from `tabPurchase Invoice Item` pii
		  join `tabPurchase Invoice` pi on pi.name = pii.parent
		 where pi.docstatus = 1
		   and ifnull(nullif(pii.project, ''), pi.project) = %(project)s
		 group by category
		""",
		{"project": project, "unclassified": UNCLASSIFIED},
		as_dict=True,
	)
	return {row.category: row.amount or 0 for row in rows}


def _timesheet_cost(project):
	"""Submitted timesheet cost for ``project``.

	Uncategorised on purpose: a timesheet hour is labour by definition, so the whole figure
	belongs to whichever category declares Timesheets as its actual source. There is nothing to
	attribute and nothing to get wrong.
	"""
	value = frappe.db.sql(
		"""
		select sum(ifnull(td.costing_amount, 0))
		  from `tabTimesheet Detail` td
		  join `tabTimesheet` t on t.name = td.parent
		 where t.docstatus = 1 and td.project = %s
		""",
		(project,),
	)
	return (value and value[0] and value[0][0]) or 0


def spend_for_project(project):
	"""What each source says about one project, keyed by the source that said it.

	Keyed by source name rather than by "committed"/"actual" so a category that declares an
	unusual source still reads the right figures -- the source is the only thing that decides
	where a number comes from.

	The two purchase maps are keyed by budget category, with :data:`UNCLASSIFIED` holding the
	value that names no category. The caller decides what to do with that bucket; this function
	never folds it into a category and never discards it.
	"""
	return {
		budgets.SOURCE_PURCHASE_ORDERS: _committed_by_category(project),
		budgets.SOURCE_PURCHASE_INVOICES: _invoiced_by_category(project),
		budgets.SOURCE_TIMESHEETS: _timesheet_cost(project),
	}


def _category_sources(category):
	"""``(committed_source, actual_source)`` for a category, defaulting to no source.

	A category row that has gone missing is treated as declaring nothing rather than raising: a
	budget line pointing at a deleted category must render, so somebody can see what to fix.
	"""
	if not category:
		return budgets.SOURCE_NONE, budgets.SOURCE_NONE
	try:
		row = frappe.get_cached_doc("Project Budget Category", category)
	except frappe.DoesNotExistError:
		return budgets.SOURCE_NONE, budgets.SOURCE_NONE
	return (
		row.get("committed_source") or budgets.SOURCE_NONE,
		row.get("actual_source") or budgets.SOURCE_NONE,
	)


def refresh(doc):
	"""Fill in every budget line's committed, actual and coverage, then derive the total.

	Returns early and costs nothing when the project has no budget lines -- which is every
	project on production today.
	"""
	lines = doc.get(budgets.LINES_FIELD) or []
	if not lines:
		# Deliberately does NOT zero `estimated_costing`. A project with no category lines may
		# still carry a total somebody typed, and WI-057's backfill is going to write exactly
		# that figure onto hundreds of projects. Deriving a total from an empty list would erase
		# it -- the feature quietly destroying the data the work item before it exists to create.
		return

	spend = spend_for_project(doc.name) if doc.name else {}

	for line in lines:
		committed_source, actual_source = _category_sources(line.get("category"))

		line.committed_amount = _figure(committed_source, line.get("category"), spend)
		line.actual_amount = _figure(actual_source, line.get("category"), spend)
		line.spend_coverage = budgets.coverage(actual_source, source_has_rows(actual_source))

	doc.estimated_costing = budgets.total_budgeted(lines)


def _figure(source, category, spend):
	"""The amount for one category from one source, or zero when the source names nothing.

	Dispatch is on the **source alone**, never on which column is being filled. An earlier draft
	branched on "is this the committed column or the actual one" as well, which meant a category
	declaring Purchase Invoices as its *committed* source would have been handed purchase-order
	figures. Naming a source is the whole mechanism here; nothing else may override it.
	"""
	if source == budgets.SOURCE_TIMESHEETS:
		return spend.get(budgets.SOURCE_TIMESHEETS) or 0
	bucket = spend.get(source)
	if not bucket:
		return 0
	return bucket.get(category) or 0


def unclassified_for_project(project):
	"""Project purchase value carrying no budget category, committed and invoiced.

	Reported on its own rather than mixed into the categories. Today this is all of it: the field
	that attributes a purchase-order line to a category ships in this release, so every one of the
	324 existing project-tagged lines predates it.
	"""
	spend = spend_for_project(project)
	return {
		"committed": (spend.get(budgets.SOURCE_PURCHASE_ORDERS) or {}).get(UNCLASSIFIED) or 0,
		"actual": (spend.get(budgets.SOURCE_PURCHASE_INVOICES) or {}).get(UNCLASSIFIED) or 0,
	}


def on_project_validate(doc, method=None):
	"""``doc_events`` entry point. Never raises: a budget rollup must not block a Project save.

	The rollup is a convenience on a field somebody else owns. If it fails -- a category deleted
	mid-save, a table missing during ERPNext's own test bootstrap -- the right outcome is a logged
	error and a saved Project, not a project manager unable to save their own job.
	"""
	try:
		refresh(doc)
	except Exception:
		frappe.log_error(
			title="Budget rollup failed",
			message=f"Project {doc.name}: {frappe.get_traceback()}",
		)
