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
  than writing Maintain Stock back off. Then a Comment that says who, when and how, the document
  cache cleared (``get_cached_value`` readers such as ``get_stock_items`` would otherwise see the
  old flag), and what to do next.

  **The race left over.** A Delivery Note or Stock Entry being submitted at the same moment does
  not lock the Item row; it reads the flag through ``get_cached_value``. One that commits between
  this transaction's snapshot and its commit is not among the documents the checks counted. The
  window is the length of one request. Nothing in ERPNext offers a lock that would close it short
  of locking every transaction table.
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


def _item_row(item_code):
	return frappe.db.get_value("Item", item_code, ITEM_FIELDS, as_dict=True)


def gather_facts(item_code, item=None):
	"""Everything :func:`maintain_stock_rules.evaluate` judges, read in one place. ``None`` when
	there is no such Item. ``item`` is the Item's row when the caller already read it (locked)."""
	item = item or _item_row(item_code)
	if not item:
		return None
	values = {"item": item_code}
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
		"submitted": {child: _submitted(child, item_code) for child in rules.SUBMITTED_DOCTYPES},
		"open": {child: _open(child, item_code) for child in rules.OPEN_DOCTYPES},
		"receipts": _submitted("Purchase Receipt Item", item_code),
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


def run_checks(item_code, item=None):
	"""The dialog's answer: every check, whether all passed, and what ERPNext's form would say.

	Documents are named only where the caller may read them; the rest are counted
	(``hidden_examples``). The checks themselves always count everything.
	"""
	facts = gather_facts(item_code, item=item)
	if facts is None:
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	checks = rules.evaluate(facts)
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


def turn_on(item_code):
	"""Switch Maintain Stock on after every check passes again. Refuses, in words, when one fails."""
	# The locking read is also where the item's own flags come from (see the module docstring).
	item = frappe.db.get_value("Item", item_code, ITEM_FIELDS, as_dict=True, for_update=True)
	if not item:
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	result = run_checks(item_code, item=item)
	if not result["can_switch"]:
		frappe.throw(
			rules.refusal_message(item_code, result["checks"]), title=_("Maintain Stock not turned on")
		)

	frappe.db.set_value("Item", item_code, "is_stock_item", 1)
	user = frappe.session.user
	comment = rules.switch_comment(
		get_fullname(user), format_datetime(now_datetime()), result["receipts"]["count"]
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
		"warehouse": _default_warehouse(item_code, company),
		"next_steps": rules.next_steps(item_code, account, is_default),
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
