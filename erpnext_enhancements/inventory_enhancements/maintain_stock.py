# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn on Maintain Stock for an Item ERPNext's form refuses to switch, and guard its receipts.

Three parts, each with its reasons:

* **The reads** (:func:`gather_facts`, :func:`run_checks`). Every judgement is
  ``maintain_stock_rules.evaluate``; this module only counts. A count is of *documents*, not lines
  ("2 Sales Invoices"), and each group names up to three of them, so the person can open one, but
  only the ones they may read (:func:`_readable`); the rest are counted, not named. Table names
  come from the rules module's constants, never from a request.
* **The switch** (:func:`turn_on`). The Item row is read ``for update`` first, and the item's own
  flags come from that locking read, not from the transaction's earlier snapshot (under
  REPEATABLE READ a plain read would see the item as it was before the lock). So two clicks, or
  the action racing a form save, run one after the other and the second sees the first. Then
  every check runs again, because the dialog's answer is only what was true when it opened. Then
  ``frappe.db.set_value``, which writes the column without loading the document, so
  ``Item.cant_change`` never runs. That is the bypass, and the checks are why it is safe.
  ``set_value`` moves ``modified``, so a form left open from before refuses its next save rather
  than writing Maintain Stock back off. Then the document cache cleared (``get_cached_value``
  readers such as ``get_stock_items`` would otherwise see the old flag), the open orders counted
  (below), a Comment that says who, when and how, and what to do next.

  **Open Purchase Orders and Material Requests** (v1.809.0) no longer refuse. After the flag is
  written, a line with no warehouse gets one (:func:`_fill_order_warehouses`, with a Comment on
  its order), and each warehouse an open line names has its Bin recounted by ERPNext's own
  ``stock_balance`` functions (:func:`_recount_bins`), never by a copy of their arithmetic.
  ``maintain_stock_rules`` says what goes wrong otherwise, case by case.

  **The race left over.** A Delivery Note or Stock Entry being submitted at the same moment does
  not lock the Item row; it reads the flag through ``get_cached_value``. One that commits between
  this transaction's snapshot and its commit is not among the documents the checks counted. The
  window is the length of one request. Nothing in ERPNext offers a lock that would close it short
  of locking every transaction table. For the Bin there is a second recount straight after the
  commit (:func:`_recount_after_commit`), which sees a receipt that committed in that window.
* **The guards** on the item's old receipts. After the switch those lines are a stock item's lines
  that never wrote a stock ledger entry, and every path in ERPNext v16.52.1 that rebuilds a
  receipt's ledger does it from the items that keep stock *now*. So each of these is refused:

  - **Cancel** (``refuse_unstocked_receipt_cancel``, ``before_cancel``).
    ``BuyingController.update_stock_ledger`` builds the entries and ``stock_ledger.make_sl_entries``
    negates them: the cancel would take out stock the line never put in.
  - **Return** (``refuse_unstocked_receipt_return``, ``validate``), whether a row names the line it
    returns (``purchase_receipt_item``) or, built by hand, only the item and ``return_against``:
    ``validate_returned_items`` accepts both, so both are judged.
  - **Bill** (``refuse_billing_unstocked_receipts``, ``validate``).
    ``PurchaseInvoice.set_expense_account`` books a stock item's bill line to Stock Received But
    Not Billed when *any* line of the receipt in its ``purchase_receipt`` field credited that
    account, or when the line names no receipt at all. So whether an old receipt bills cleanly
    depends on what else was on it, which nobody can judge at the bill. A row is judged by its
    receipt line, else by every line of its receipt for that item, else by every receipt line of
    its order line.
  - **Rebuild** (``refuse_rebuilding_unstocked_receipts``): a Landed Cost Voucher on ``validate`` and
    ``before_cancel`` (``update_landed_cost`` cancels and re-posts the receipt's ledger and GL, and
    ``get_pr_items`` picks the line up once the item keeps stock), a Repost Item Valuation that
    recreates stock ledgers (``recreate_stock_ledger_entries``) and a Repost Accounting Ledger (the
    receipt's GL made again). Each would give the old line backdated stock and a Stock Received
    But Not Billed credit, and, worse, a ledger entry: from then on the line looks like any other
    stock line and every guard above lets it through.

  The test is the stock ledger, not a date: a stock item's receipt line with no ledger entry was
  received while it kept no stock, however it was switched (one item was switched in a bench
  console on 2026-10-27, before this existed, and is covered the same way). These are the paths
  found in v16.52.1; another way to rebuild a receipt's ledger, in a later ERPNext or another app,
  would not be guarded.

  - **Deliver against an old invoice** (``refuse_delivering_old_invoices``, ``Delivery Note``
    ``validate``, v1.809.0). Only for an item switched under the relaxed Sales Invoice rule, which
    leaves a ``Maintain Stock Decision`` row saying so and when. *Create > Delivery Note* on an
    invoice posted on or before that day would take out of stock goods that left long ago.

The guards are data-integrity guards, not nudges, so unlike ``item_naming_guard`` they also run
outside a web request: a bulk cancel runs as a background job. They stand aside during install,
migrate, patch and test runs (doc_events fire in ERPNext's own test bootstrap), and for a document
whose ``flags.ignore_maintain_stock_guard`` is set, the way out for accounting working in a console.
They read only standard ERPNext fields, so a fresh database cannot crash them.

Tabs, per ``CLAUDE.md`` (new file).
"""

import html
import inspect

import frappe
from frappe import _
from frappe.utils import cint, format_datetime, get_fullname, now_datetime

from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_accounts

#: ``frappe.flags`` under which the guards stand aside.
SKIP_FLAGS = ("in_install", "in_migrate", "in_patch", "in_test")

#: Set on a document's ``flags`` to let accounting past the guards on purpose.
IGNORE_FLAG = "ignore_maintain_stock_guard"

#: The Item's own fields the checks judge, read in one query (``for update`` when switching).
ITEM_FIELDS = ["name", "item_name", "is_stock_item", "is_fixed_asset", "variant_of", "has_variants"]


# ---------------------------------------------------------------------------
# The reads
# ---------------------------------------------------------------------------


def erpnext_lock_list():
	"""The doctypes the installed ERPNext's lock checks, or ``None`` when its source can't be read."""
	try:
		from erpnext.stock.doctype.item.item import Item

		return rules.lock_list_from_source(inspect.getsource(Item._get_linked_submitted_documents))
	except Exception:
		return None


def erpnext_refusal(item_code):
	"""The document ERPNext's own form would name when it refuses, ``False`` when it would not
	refuse, ``None`` when it can't be asked. Calls ERPNext's own method rather than copying it."""
	try:
		linked = frappe.get_doc("Item", item_code)._get_linked_submitted_documents(["is_stock_item"])
	except Exception:
		return None
	if not linked:
		return False
	return {"doctype": linked.get("doctype"), "name": linked.get("docname")}


def _group(count_sql, example_sql, values):
	count = cint((frappe.db.sql(count_sql, values) or [[0]])[0][0])
	examples = []
	if count:
		rows = frappe.db.sql(f"{example_sql} limit {cint(rules.EXAMPLES)}", values)
		examples = [{"doctype": row[0], "name": row[1]} for row in rows]
	return {"count": count, "examples": examples}


def _submitted(child, item_code):
	if child == "BOM":
		return _group(
			"select count(*) from `tabBOM` where item = %(item)s and docstatus = 1",
			"select 'BOM', name from `tabBOM` where item = %(item)s and docstatus = 1 order by creation desc",
			{"item": item_code},
		)
	return _group(
		f"select count(distinct parent) from `tab{child}` where item_code = %(item)s and docstatus = 1",
		f"select parenttype, parent from `tab{child}` where item_code = %(item)s and docstatus = 1 "
		"group by parenttype, parent order by max(creation) desc",
		{"item": item_code},
	)


def _open(child, item_code):
	rule = rules.OPEN_RULES[child]
	outstanding, done = rule["outstanding"]
	statuses = ", ".join(f"%(status{i})s" for i in range(len(rule["closed_statuses"])))
	values = {"item": item_code}
	values.update({f"status{i}": s for i, s in enumerate(rule["closed_statuses"])})
	where = (
		f"from `tab{child}` c join `tab{rule['parent']}` p on p.name = c.parent "
		f"where c.item_code = %(item)s and p.docstatus = 1 and c.{outstanding} > c.{done} "
		f"and p.status not in ({statuses})"
	)
	if rule["skip_drop_ship"]:
		where += " and coalesce(c.delivered_by_supplier, 0) = 0"
	return _group(
		f"select count(distinct c.parent) {where}",
		f"select '{rule['parent']}', c.parent {where} group by c.parent order by max(p.creation) desc",
		values,
	)


def _packed(item_code):
	"""The item as a component of a Product Bundle (``tabPacked Item``), by the bundle's document.

	ERPNext moves a bundle's components through these rows (``SellingController.get_item_list``)
	by today's flag, and reserves them on an open Sales Order (``get_reserved_qty``). A submitted
	Delivery Note, Sales or POS Invoice counts; a Sales Order counts while its bundle line still has
	something to deliver and the order isn't Closed (the Sales Order rule in ``OPEN_RULES``).
	"""
	groups = {}
	for parent in rules.PACKED_SUBMITTED_PARENTS:
		groups[parent] = _group(
			"select count(distinct parent) from `tabPacked Item` "
			"where item_code = %(item)s and parenttype = %(parent)s and docstatus = 1",
			"select parenttype, parent from `tabPacked Item` "
			"where item_code = %(item)s and parenttype = %(parent)s and docstatus = 1 "
			"group by parenttype, parent order by max(creation) desc",
			{"item": item_code, "parent": parent},
		)
	where = (
		"from `tabPacked Item` pi join `tabSales Order` so on so.name = pi.parent "
		"join `tabSales Order Item` soi on soi.name = pi.parent_detail_docname "
		"where pi.item_code = %(item)s and pi.parenttype = 'Sales Order' and so.docstatus = 1 "
		"and so.status <> 'Closed' and soi.qty > soi.delivered_qty "
		"and coalesce(soi.delivered_by_supplier, 0) = 0"
	)
	groups["Sales Order"] = _group(
		f"select count(distinct pi.parent) {where}",
		f"select 'Sales Order', pi.parent {where} group by pi.parent order by max(so.creation) desc",
		{"item": item_code},
	)
	return groups


def _open_lines(child, item_code):
	"""The open lines of ``child`` for the item, one dict a line (``rules.plan_open_orders``)."""
	rule = rules.OPEN_RULES[child]
	outstanding, done = rule["outstanding"]
	statuses = ", ".join(f"%(status{i})s" for i in range(len(rule["closed_statuses"])))
	values = {"item": item_code}
	values.update({f"status{i}": s for i, s in enumerate(rule["closed_statuses"])})
	query = (
		f"select c.name, c.parent, c.idx, c.warehouse, p.company, c.{done} "
		f"from `tab{child}` c join `tab{rule['parent']}` p on p.name = c.parent "
		f"where c.item_code = %(item)s and p.docstatus = 1 and c.{outstanding} > c.{done} "
		f"and p.status not in ({statuses})"
	)
	if rule["skip_drop_ship"]:
		query += " and coalesce(c.delivered_by_supplier, 0) = 0"
	query += " order by c.parent, c.idx"
	return [
		{
			"doctype": child,
			"name": row[0],
			"parent": row[1],
			"idx": row[2],
			"warehouse": row[3] or "",
			"company": row[4] or "",
			# Only a Purchase Order line can be partly *received*; a request's is partly ordered.
			"received": (row[5] or 0) if child == "Purchase Order Item" else 0,
		}
		for row in frappe.db.sql(query, values)
	]


def _usable_warehouse(warehouse, company):
	"""``warehouse`` when it is a real, enabled, non-group warehouse of ``company``; else ``None``."""
	if not warehouse:
		return None
	row = frappe.db.get_value(
		"Warehouse", warehouse, ["name", "company", "is_group", "disabled"], as_dict=True
	)
	if not row or cint(row.is_group) or cint(row.disabled) or row.company != company:
		return None
	return row.name


def _erpnext_line_warehouse(item_code, company):
	"""The warehouse ERPNext itself puts on a new line for this item in this company when neither
	the row nor the document names one, or ``None``. ERPNext's own function is asked
	(``get_item_details.get_item_warehouse_``), never a copy of its order, which at 16.54.0 is:
	the item's default, its item group's, its brand's, then the Company's default warehouse. It
	does not read Stock Settings. Never raises: on a version where that function has moved, or
	for a user whose warehouse permissions hide the answer, this is ``None`` and
	:func:`_fill_warehouses` goes on to what it can read itself.
	"""
	try:
		from erpnext.stock.get_item_details import get_item_warehouse_

		return (
			get_item_warehouse_(
				frappe._dict(company=company, warehouse=None, set_warehouse=None),
				frappe._dict(name=item_code),
				True,
			)
			or None
		)
	except Exception:
		return None


def _company_default_warehouse(company):
	try:
		return frappe.get_cached_value("Company", company, "default_warehouse") or None
	except Exception:
		return None


def _fill_warehouses(item_code, lines):
	"""``{company: warehouse}`` for the companies whose open lines lack a warehouse.

	**What a new line for the item would be given** (v1.817.0): ERPNext's own answer
	(:func:`_erpnext_line_warehouse`), then the item's own default, then the Company's default
	warehouse, the first of them that is a real, enabled, non-group warehouse of that company. A
	company with none is left out, and the check fails.

	Until v1.817.0 this read the item's default and then Stock Settings' default warehouse, and
	said that was ERPNext's order. It is not: ERPNext never reads Stock Settings there, and ends
	on the Company's default. The difference began to matter with the Project Shelf patch, which
	blanks the default warehouse of every non-stock item that named the shelf: such an item with
	a warehouse-less open line would have been refused here, where ERPNext gives the same line
	the Company's default without a word. Stock Settings' default is not a fallback to offer
	anyone either: ERPNext copies it into every new item (``Item.update_defaults_from_item_group``),
	which is how 774 items came to name one warehouse.

	The fill is one warehouse a company, not one a line: a line's project is not read here. On a
	site with a Project Shelf an Overhead line may so be given the shelf, as ERPNext would give
	it; it is moved to the item's own bin when it is received, like every open Overhead line that
	already names the shelf (``procurement_project.default_receipt_warehouses``).
	"""
	out = {}
	for company in sorted({line["company"] for line in lines if not line["warehouse"] and line["company"]}):
		for candidate in (
			_erpnext_line_warehouse(item_code, company),
			_default_warehouse(item_code, company),
			_company_default_warehouse(company),
		):
			warehouse = _usable_warehouse(candidate, company)
			if warehouse:
				out[company] = warehouse
				break
	return out


def _item_row(item_code):
	return frappe.db.get_value("Item", item_code, ITEM_FIELDS, as_dict=True)


def gather_facts(item_code, item=None):
	"""Everything :func:`maintain_stock_rules.evaluate` judges, read in one place. ``None`` when
	there is no such Item. ``item`` is the Item's row when the caller already read it (locked)."""
	item = item or _item_row(item_code)
	if not item:
		return None
	values = {"item": item_code}
	open_lines = [line for child in rules.RECOUNT_DOCTYPES for line in _open_lines(child, item_code)]
	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"is_stock_item": cint(item.is_stock_item),
		"is_fixed_asset": cint(item.is_fixed_asset),
		"variant_of": item.variant_of or "",
		"has_variants": cint(item.has_variants),
		"stock_ledger": _group(
			"select count(*) from (select distinct voucher_type, voucher_no from `tabStock Ledger Entry` "
			"where item_code = %(item)s and is_cancelled = 0) vouchers",
			"select voucher_type, voucher_no from `tabStock Ledger Entry` "
			"where item_code = %(item)s and is_cancelled = 0 "
			"group by voucher_type, voucher_no order by max(creation) desc",
			values,
		),
		"bundles": _group(
			"select count(*) from `tabProduct Bundle` where new_item_code = %(item)s",
			"select 'Product Bundle', name from `tabProduct Bundle` where new_item_code = %(item)s",
			values,
		),
		"packed": _packed(item_code),
		# Sales Invoice has a check of its own (the relaxed rule), and is counted the same way.
		"submitted": {
			child: _submitted(child, item_code) for child in (*rules.SUBMITTED_DOCTYPES, rules.SALES_INVOICE)
		},
		"open": {child: _open(child, item_code) for child in rules.OPEN_RULES},
		# The open Purchase Order and Material Request lines, which the switch recounts, and the
		# warehouse a line without one would get.
		"open_lines": open_lines,
		"fill_warehouses": _fill_warehouses(item_code, open_lines),
		# The relaxed Sales Invoice rule passes only when this is empty.
		"sales_invoices_with_stock": _group(
			"select count(distinct c.parent) from `tabSales Invoice Item` c "
			"join `tabSales Invoice` p on p.name = c.parent "
			"where c.item_code = %(item)s and c.docstatus = 1 and p.update_stock = 1",
			"select 'Sales Invoice', c.parent from `tabSales Invoice Item` c "
			"join `tabSales Invoice` p on p.name = c.parent "
			"where c.item_code = %(item)s and c.docstatus = 1 and p.update_stock = 1 "
			"group by c.parent order by max(p.creation) desc",
			values,
		),
		"receipts": _submitted("Purchase Receipt Item", item_code),
		# A submitted return refuses (maintain_stock_rules explains: an ordinary repost re-books it).
		"receipt_returns": _group(
			"select count(distinct c.parent) from `tabPurchase Receipt Item` c "
			"join `tabPurchase Receipt` p on p.name = c.parent "
			"where c.item_code = %(item)s and c.docstatus = 1 and p.is_return = 1",
			"select 'Purchase Receipt', c.parent from `tabPurchase Receipt Item` c "
			"join `tabPurchase Receipt` p on p.name = c.parent "
			"where c.item_code = %(item)s and c.docstatus = 1 and p.is_return = 1 "
			"group by c.parent order by max(p.creation) desc",
			values,
		),
		"lock_list": erpnext_lock_list(),
	}


def _may_read(doctype, name):
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name))
	except Exception:
		return False


def _readable(examples):
	"""``(shown, hidden)``: the examples the caller may read, and how many they may not."""
	shown = [e for e in examples or () if _may_read(e.get("doctype"), e.get("name"))]
	return shown, len(examples or ()) - len(shown)


def run_checks(item_code, item=None, relax_sales_invoices=False, facts=None):
	"""The dialog's answer: every check, whether all passed, and what ERPNext's form would say.

	Documents are named only where the caller may read them; the rest are counted
	(``hidden_examples``). The checks themselves always count everything. ``facts`` is
	:func:`gather_facts`' answer when the caller already has it.
	"""
	facts = facts or gather_facts(item_code, item=item)
	if facts is None:
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	checks = rules.evaluate(facts, relax_sales_invoices=relax_sales_invoices)
	shown_checks = []
	for check in checks:
		shown, hidden = _readable(check["examples"])
		shown_checks.append(dict(check, examples=shown, hidden_examples=hidden))
	receipts = dict(facts["receipts"])
	receipts["examples"], receipts["hidden_examples"] = _readable(receipts["examples"])
	locked_by = erpnext_refusal(item_code)
	if locked_by and not _may_read(locked_by["doctype"], locked_by["name"]):
		# Still true that the form would refuse; just not which document says so.
		locked_by = {"doctype": None, "name": None}
	return {
		"item_code": facts["item_code"],
		"item_name": facts["item_name"],
		"checks": shown_checks,
		"can_switch": rules.can_switch(checks),
		"receipts": receipts,
		# Whether the form's own save would refuse, and on which document. The form script asks
		# only so it can step in before ERPNext's message; the switch never depends on it.
		"locked_by": locked_by,
	}


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def stock_adjustment_account(company):
	"""``(account, is_company_default)`` for the first Stock Reconciliation's Difference Account.

	ERPNext's Stock Reconciliation reads only ``Company.stock_adjustment_account``, which production
	leaves empty (``stock_accounts`` explains), so the person must pick one by hand. This names the
	company's one leaf Stock Adjustment account for them. ``(None, False)`` when there isn't one, or
	there are several to choose between.
	"""
	if not company:
		return None, False
	default = stock_accounts.usable_account(
		frappe.get_cached_value("Company", company, "stock_adjustment_account"), company
	)
	if default:
		return default, True
	leaves = frappe.get_all(
		"Account",
		filters={"company": company, "account_type": "Stock Adjustment", "is_group": 0, "disabled": 0},
		pluck="name",
		limit=2,
	)
	return (leaves[0], False) if len(leaves) == 1 else (None, False)


def _default_warehouse(item_code, company):
	if not company:
		return None
	try:
		from erpnext.stock.doctype.item.item import get_item_defaults

		return (get_item_defaults(item_code, company) or {}).get("default_warehouse") or None
	except Exception:
		return None


def first_count_warehouse(item_code, company):
	"""Where the item's first count opens: the warehouse the result's ``warehouse`` carries and
	*Open a Stock Reconciliation* puts on the new form's row. ``None`` leaves the row's
	location empty, so the person has to choose it.

	**Never the Project Shelf and never a job site** (v1.817.0;
	``project_shelf_rules.first_count_warehouse`` has the rule and why). On a site with a
	Project Shelf it is the item's own bin (``procurement_project.item_own_bin``), else the
	Overhead receiving warehouse, else nothing. On a site with no shelf set up it is the
	item's default warehouse, as it was before. It is not what an open order line with no
	warehouse is given (:func:`_fill_warehouses`): a line's right place depends on its project.
	Never raises. On a site with a shelf a failure answers ``None`` (the person chooses: the
	item's default there is most likely the shelf itself). Whether there is a shelf is asked
	first and by itself, so a site with none depends on nothing else here.
	"""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

		has_shelf = bool(project_shelf.warehouse())
		not_shop = project_shelf.not_shop_warehouses()
	except Exception:
		return _default_warehouse(item_code, company)
	if not has_shelf:
		return project_shelf_rules.first_count_warehouse(
			False, default=_default_warehouse(item_code, company), not_bins=not_shop
		)
	try:
		from erpnext_enhancements import procurement_project

		return project_shelf_rules.first_count_warehouse(
			True,
			own=procurement_project.item_own_bin(item_code, company),
			fallback=project_shelf.fallback_warehouse(),
		)
	except Exception:
		return None


def _shelf_label():
	"""What the Project Shelf is called here, or ``None`` on a site that has none. Never raises."""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf

		return project_shelf.label()
	except Exception:
		return None


def _fill_order_warehouses(item_code, plan, full_name):
	"""Give each open order line that has no warehouse the one the plan chose, and say so on the
	order. A direct column write, like the flag: the line is submitted, and ERPNext offers no way
	to set a warehouse on one. ``update_modified=False`` leaves the order's own timestamp alone, so
	a form open on it still saves."""
	by_order = {}
	for line in plan["fill"]:
		frappe.db.set_value(
			line["doctype"], line["name"], "warehouse", line["warehouse"], update_modified=False
		)
		parent = rules.OPEN_RULES[line["doctype"]]["parent"]
		by_order.setdefault((parent, line["parent"], line["warehouse"]), []).append(str(line["idx"]))
	for (parent, name, warehouse), rows in by_order.items():
		comment = rules.order_comment(item_code, full_name, ", ".join(rows), warehouse)
		frappe.get_doc(parent, name).add_comment("Comment", html.escape(comment, quote=False))


def _recount_bins(item_code, plan):
	"""Recount what is on order and requested, with ERPNext's own functions.

	``get_ordered_qty`` and ``get_indented_qty`` are what a Purchase Order and a Material Request
	call for a stock item; ``update_bin_qty`` writes the Bin (making it when there is none) and
	recomputes its projected quantity. Called after the flag is written and the cache cleared.
	"""
	if not plan["recount"]:
		return
	from erpnext.stock import stock_balance

	readers = {"ordered_qty": stock_balance.get_ordered_qty, "indented_qty": stock_balance.get_indented_qty}
	for field, warehouse in plan["recount"]:
		stock_balance.update_bin_qty(item_code, warehouse, {field: readers[field](item_code, warehouse)})


def _recount_after_commit(item_code, plan):
	"""Recount the same Bins once more, right after this transaction commits.

	The first recount reads the database as it was when this transaction began (REPEATABLE READ).
	A Purchase Receipt for the same item that committed while the switch was running is not in
	that picture: it moved ``received_qty`` without touching the Bin, because it still saw the
	item as non-stock, so the Bin's on-order figure would be too high. The second recount, in a
	fresh transaction, sees it.

	What is left: a receipt that began before the switch committed and commits after this second
	recount. It still carries the old flag, posts no stock, and leaves on-order too high until the
	next order or receipt for that item and warehouse recounts it, as ERPNext does on every one.

	A savepoint, not a rollback, on failure: a full rollback inside a commit callback would drop
	the other callbacks still waiting to run.
	"""
	after_commit = getattr(frappe.db, "after_commit", None)
	if not plan["recount"] or after_commit is None:
		return

	def again():
		try:
			frappe.db.savepoint("maintain_stock_recount")
			_recount_bins(item_code, plan)
			frappe.db.commit()
		except Exception:
			try:
				frappe.db.rollback(save_point="maintain_stock_recount")
			except Exception:
				pass
			frappe.log_error(frappe.get_traceback(), "Maintain Stock: recount after commit failed")

	after_commit.add(again)


def turn_on(item_code, relax_sales_invoices=False, user=None, via=None):
	"""Switch Maintain Stock on after every check passes again. Refuses, in words, when one fails.

	``relax_sales_invoices`` asks for the relaxed Sales Invoice rule (``maintain_stock_rules``);
	only the bulk page passes it, and it records the switch so the Delivery Note guard knows.
	``user`` is the person the switch is for when it runs in a background job, and ``via`` how
	they asked (``rules.VIA_BULK``); both go into the Comment.
	"""
	# The locking read is also where the item's own flags come from (see the module docstring).
	item = frappe.db.get_value("Item", item_code, ITEM_FIELDS, as_dict=True, for_update=True)
	if not item:
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	facts = gather_facts(item_code, item=item)
	result = run_checks(item_code, relax_sales_invoices=relax_sales_invoices, facts=facts)
	if not result["can_switch"]:
		frappe.throw(
			rules.refusal_message(item_code, result["checks"]), title=_("Maintain Stock not turned on")
		)
	plan = rules.plan_open_orders(facts["open_lines"], facts["fill_warehouses"])
	open_groups = {d: g for d, g in facts["open"].items() if d in rules.RECOUNT_DOCTYPES and g.get("count")}
	invoices = cint((facts["submitted"].get(rules.SALES_INVOICE) or {}).get("count"))

	frappe.db.set_value("Item", item_code, "is_stock_item", 1)
	# Before the recount: ERPNext reads the flag through get_cached_value.
	frappe.clear_document_cache("Item", item_code)
	user = user or frappe.session.user
	full_name = get_fullname(user)
	_fill_order_warehouses(item_code, plan, full_name)
	_recount_bins(item_code, plan)
	_recount_after_commit(item_code, plan)
	comment = rules.switch_comment(
		full_name,
		format_datetime(now_datetime()),
		result["receipts"]["count"],
		via=via,
		open_orders=rules.open_orders_detail(open_groups, plan),
		invoices=invoices,
	)
	frappe.get_doc("Item", item_code).add_comment("Comment", html.escape(comment, quote=False))
	frappe.clear_document_cache("Item", item_code)

	from erpnext import get_default_company

	company = get_default_company()
	account, is_default = stock_adjustment_account(company)
	return {
		"item_code": item_code,
		"is_stock_item": 1,
		"modified": str(frappe.db.get_value("Item", item_code, "modified")),
		"company": company,
		"account": account,
		"account_is_company_default": is_default,
		# Where the first count opens (v1.817.0): never the Project Shelf or a job site. The
		# item's old default warehouse is the shelf on most items this switch is for.
		"warehouse": (count_at := first_count_warehouse(item_code, company)),
		"next_steps": rules.next_steps(
			item_code, account, is_default, count_at=count_at, not_at=_shelf_label()
		),
		# What the bulk page records: the invoices the relaxed rule let through.
		"invoices_before_switch": invoices,
		"open_orders_recounted": sum(cint(g.get("count")) for g in open_groups.values()),
	}


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def _stands_aside(doc):
	if any(getattr(frappe.flags, flag, False) for flag in SKIP_FLAGS):
		return True
	return bool(getattr(getattr(doc, "flags", None), IGNORE_FLAG, False))


def _stock_items(codes):
	codes = sorted({code for code in codes if code})
	if not codes:
		return set()
	return set(frappe.get_all("Item", filters={"name": ("in", codes), "is_stock_item": 1}, pluck="name"))


def _receipt_lines_with_stock(line_names):
	"""Which Purchase Receipt lines wrote a stock ledger entry."""
	line_names = sorted({name for name in line_names if name})
	if not line_names:
		return set()
	return set(
		frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": "Purchase Receipt", "voucher_detail_no": ("in", line_names)},
			pluck="voucher_detail_no",
		)
	)


def _receipt_lines(filters):
	"""Submitted Purchase Receipt lines matching ``filters``, as plain dicts."""
	return [
		{
			"name": row.name,
			"receipt": row.parent,
			"idx": row.idx,
			"item_code": row.item_code,
			"purchase_order_item": row.purchase_order_item,
		}
		for row in frappe.get_all(
			"Purchase Receipt Item",
			filters=dict(filters, docstatus=1),
			fields=["name", "parent", "idx", "item_code", "purchase_order_item"],
		)
	]


def _line(row, **extra):
	line = {"idx": row.get("idx"), "item_code": row.get("item_code"), "name": row.get("name")}
	line.update(extra)
	return line


def unstocked_receipts(receipts):
	"""``{receipt: [lines]}`` for the Purchase Receipts holding a stock item's line that wrote no
	stock ledger entry. Receipts with none are left out."""
	receipts = sorted({receipt for receipt in receipts if receipt})
	if not receipts:
		return {}
	lines = _receipt_lines({"parent": ("in", receipts)})
	stock_items = _stock_items(line["item_code"] for line in lines)
	lines = [line for line in lines if line["item_code"] in stock_items]
	if not lines:
		return {}
	unstocked = rules.unstocked_lines(
		lines, stock_items, _receipt_lines_with_stock(line["name"] for line in lines)
	)
	by_receipt = {}
	for line in unstocked:
		by_receipt.setdefault(line["receipt"], []).append(line)
	return by_receipt


def refuse_unstocked_receipt_cancel(doc, method=None):
	"""``Purchase Receipt`` → ``before_cancel``: refuse when a line of a stock item wrote no stock."""
	if _stands_aside(doc):
		return
	lines = [_line(row) for row in doc.get("items") or [] if row.get("item_code")]
	stock_items = _stock_items(line["item_code"] for line in lines)
	lines = [line for line in lines if line["item_code"] in stock_items]
	if not lines:
		return
	unstocked = rules.unstocked_lines(
		lines, stock_items, _receipt_lines_with_stock(line["name"] for line in lines)
	)
	if unstocked:
		frappe.throw(
			rules.cancel_refusal(doc.name, unstocked, is_return=cint(doc.get("is_return"))),
			title=_("This receipt can't be canceled"),
		)


def refuse_unstocked_receipt_return(doc, method=None):
	"""``Purchase Receipt`` → ``validate``: refuse a return of a receipt line that wrote no stock.

	A row normally names the line it returns (``purchase_receipt_item``). One built by hand may name
	only the item, and ERPNext matches it to ``return_against`` by item code, so it is judged by
	every line of that receipt for that item.
	"""
	if _stands_aside(doc) or not cint(doc.get("is_return")):
		return
	rows = [row for row in doc.get("items") or [] if row.get("item_code")]
	stock_items = _stock_items(row.get("item_code") for row in rows)
	rows = [row for row in rows if row.get("item_code") in stock_items]
	if not rows:
		return
	by_item = {}
	against = doc.get("return_against")
	if against and any(not row.get("purchase_receipt_item") for row in rows):
		for line in _receipt_lines({"parent": against}):
			by_item.setdefault(line["item_code"], []).append(line["name"])
	judged = [
		{
			"idx": row.get("idx"),
			"item_code": row.get("item_code"),
			"lines": [row.get("purchase_receipt_item")]
			if row.get("purchase_receipt_item")
			else by_item.get(row.get("item_code")) or [],
		}
		for row in rows
	]
	with_stock = _receipt_lines_with_stock(name for row in judged for name in row["lines"])
	offenders = rules.touches_unstocked(judged, stock_items, with_stock)
	if offenders:
		frappe.throw(rules.return_refusal(offenders), title=_("This return can't be submitted"))


def refuse_billing_unstocked_receipts(doc, method=None):
	"""``Purchase Invoice`` → ``validate``: refuse a bill line for goods a non-stock line received."""
	if _stands_aside(doc):
		return
	rows = [
		{
			"idx": row.get("idx"),
			"item_code": row.get("item_code"),
			"pr_detail": row.get("pr_detail") or "",
			"purchase_receipt": row.get("purchase_receipt") or "",
			"po_detail": row.get("po_detail") or "",
		}
		for row in doc.get("items") or []
		if row.get("item_code")
		and (row.get("pr_detail") or row.get("purchase_receipt") or row.get("po_detail"))
	]
	stock_items = _stock_items(row["item_code"] for row in rows)
	rows = [row for row in rows if row["item_code"] in stock_items]
	if not rows:
		return
	unlinked = [row for row in rows if not row["pr_detail"]]
	receipt_lines_by_receipt = {}
	receipts = sorted({row["purchase_receipt"] for row in unlinked if row["purchase_receipt"]})
	if receipts:
		for line in _receipt_lines({"parent": ("in", receipts)}):
			receipt_lines_by_receipt.setdefault((line["receipt"], line["item_code"]), []).append(line["name"])
	receipt_lines_by_order = {}
	order_lines = sorted({row["po_detail"] for row in unlinked if row["po_detail"]})
	if order_lines:
		for line in _receipt_lines({"purchase_order_item": ("in", order_lines)}):
			receipt_lines_by_order.setdefault(line["purchase_order_item"], []).append(line["name"])
	billed = {row["pr_detail"] for row in rows if row["pr_detail"]}
	for names in [*receipt_lines_by_receipt.values(), *receipt_lines_by_order.values()]:
		billed.update(names)
	offenders = rules.billing_offenders(
		rows,
		stock_items,
		receipt_lines_by_order,
		_receipt_lines_with_stock(billed),
		receipt_lines_by_receipt=receipt_lines_by_receipt,
	)
	if offenders:
		frappe.throw(rules.bill_refusal(offenders), title=_("These receipts can't be billed here"))


def refuse_rebuilding_unstocked_receipts(doc, method=None):
	"""Refuse anything that would rebuild an old non-stock receipt's ledger from today's flag.

	``Landed Cost Voucher`` → ``validate`` and ``before_cancel`` (its ``purchase_receipts`` rows),
	``Repost Item Valuation`` → ``validate`` when it recreates stock ledgers for a Purchase Receipt,
	``Repost Accounting Ledger`` → ``validate`` (its ``vouchers`` rows).
	"""
	if _stands_aside(doc):
		return
	if doc.doctype == "Landed Cost Voucher":
		receipts = [
			row.get("receipt_document")
			for row in doc.get("purchase_receipts") or []
			if row.get("receipt_document_type") == "Purchase Receipt"
		]
	elif doc.doctype == "Repost Item Valuation":
		recreates = cint(doc.get("recreate_stock_ledgers")) and doc.get("based_on") == "Transaction"
		receipts = (
			[doc.get("voucher_no")] if recreates and doc.get("voucher_type") == "Purchase Receipt" else []
		)
	elif doc.doctype == "Repost Accounting Ledger":
		receipts = [
			row.get("voucher_no")
			for row in doc.get("vouchers") or []
			if row.get("voucher_type") == "Purchase Receipt"
		]
	else:
		return
	unstocked = unstocked_receipts(receipts)
	if unstocked:
		canceling = doc.doctype == "Landed Cost Voucher" and cint(doc.get("docstatus")) == 2
		frappe.throw(
			rules.rebuild_refusal(doc.doctype, unstocked, canceling=canceling),
			title=_("This would rebuild a receipt's ledger"),
		)


#: Where a switch under the relaxed Sales Invoice rule is recorded (``maintain_stock_bulk``).
DECISION = "Maintain Stock Decision"


def _relaxed_switches(item_codes):
	"""``{item_code: date}`` for the items switched under the relaxed Sales Invoice rule.

	Empty when the doctype is not installed yet: this guard is hooked on a standard doctype, and
	``doc_events`` fire before this app's own tables exist.
	"""
	item_codes = sorted({code for code in item_codes if code})
	if not item_codes:
		return {}
	try:
		if not frappe.db.table_exists(DECISION):
			return {}
		rows = frappe.get_all(
			DECISION,
			filters={
				"item_code": ("in", item_codes),
				"invoices_before_switch": (">", 0),
				"status": "Switched",
			},
			fields=["item_code", "switched_on"],
		)
	except Exception:
		return {}
	# Two rows for one item (two Items merged): the later switch covers the most invoices.
	switched = {}
	for row in rows:
		day = str(row.switched_on or "")[:10]
		if day and day > switched.get(row.item_code, ""):
			switched[row.item_code] = day
	return switched


def refuse_delivering_old_invoices(doc, method=None):
	"""``Delivery Note`` → ``validate``: refuse a line that delivers a Sales Invoice posted on or
	before the day its item was switched under the relaxed rule. Those goods left while the item
	kept no stock; this would take them out again."""
	if _stands_aside(doc) or cint(doc.get("is_return")):
		return
	rows = [
		row for row in doc.get("items") or [] if row.get("item_code") and row.get("against_sales_invoice")
	]
	if not rows:
		return
	switched = _relaxed_switches(row.get("item_code") for row in rows)
	rows = [row for row in rows if row.get("item_code") in switched]
	if not rows:
		return
	invoices = sorted({row.get("against_sales_invoice") for row in rows})
	posted = {
		row.name: str(row.posting_date or "")[:10]
		for row in frappe.get_all(
			"Sales Invoice", filters={"name": ("in", invoices)}, fields=["name", "posting_date"]
		)
	}
	offenders = [
		_line(row)
		for row in rows
		if posted.get(row.get("against_sales_invoice"))
		and posted[row.get("against_sales_invoice")] <= switched[row.get("item_code")]
	]
	if offenders:
		frappe.throw(rules.delivery_refusal(offenders), title=_("These goods already left"))
