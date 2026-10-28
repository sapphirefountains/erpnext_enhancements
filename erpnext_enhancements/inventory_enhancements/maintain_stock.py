# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn on Maintain Stock for an Item ERPNext's form refuses to switch, and guard its receipts.

Three parts, each with its reasons:

* **The reads** (:func:`gather_facts`, :func:`run_checks`). Every judgement is
  ``maintain_stock_rules.evaluate``; this module only counts. A count is of *documents*, not lines
  ("2 Sales Invoices"), and each group names up to three of them, so the person can open one. Table
  names come from the rules module's constants, never from a request.
* **The switch** (:func:`turn_on`). The Item row is locked first (``for update``), so two clicks,
  or the action racing a form save, run one after the other, and the second sees the first. Then
  every check runs again, because the dialog's answer is only what was true when it opened. Then
  ``frappe.db.set_value``, which writes the column without loading the document, so
  ``Item.cant_change`` never runs. That is the bypass, and the checks are why it is safe.
  ``set_value`` moves ``modified``, so a form left open from before refuses its next save rather
  than writing Maintain Stock back off. Then a Comment that says who, when and how, the document
  cache cleared (``get_cached_value`` readers such as ``get_stock_items`` would otherwise see the
  old flag), and what to do next.
* **The guards** on the item's old receipts. After the switch those lines are a stock item's lines
  that never wrote a stock ledger entry. ERPNext rebuilds a cancel's ledger entries from the items
  that keep stock *now* (``BuyingController.update_stock_ledger``) and negates them
  (``stock_ledger.make_sl_entries``), so a cancel would take out stock the line never put in, and
  so would a return against it. A bill is the other half: ``PurchaseInvoice.set_expense_account``
  books a stock item's bill line to Stock Received But Not Billed when *any* line of its receipt
  credited that account, or when the line names no receipt at all. So whether an old receipt bills
  cleanly depends on what else was on it, which nobody can judge at the bill. All three are refused:
  :func:`refuse_unstocked_receipt_cancel` (``before_cancel``), :func:`refuse_unstocked_receipt_return`
  and :func:`refuse_billing_unstocked_receipts` (``validate``). The test is the stock ledger, not a
  date: a stock item's receipt line with no ledger entry was received while it kept no stock,
  however it was switched (one item was switched in a bench console on 2026-10-27, before this
  existed, and is covered the same way).

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


def gather_facts(item_code):
	"""Everything :func:`maintain_stock_rules.evaluate` judges, read in one place. ``None`` when
	there is no such Item."""
	item = frappe.db.get_value(
		"Item",
		item_code,
		["name", "item_name", "is_stock_item", "is_fixed_asset", "variant_of"],
		as_dict=True,
	)
	if not item:
		return None
	values = {"item": item_code}
	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"is_stock_item": cint(item.is_stock_item),
		"is_fixed_asset": cint(item.is_fixed_asset),
		"variant_of": item.variant_of or "",
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
		"submitted": {child: _submitted(child, item_code) for child in rules.SUBMITTED_DOCTYPES},
		"open": {child: _open(child, item_code) for child in rules.OPEN_DOCTYPES},
		"receipts": _submitted("Purchase Receipt Item", item_code),
		"lock_list": erpnext_lock_list(),
	}


def run_checks(item_code):
	"""The dialog's answer: every check, whether all passed, and what ERPNext's form would say."""
	facts = gather_facts(item_code)
	if facts is None:
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	checks = rules.evaluate(facts)
	return {
		"item_code": facts["item_code"],
		"item_name": facts["item_name"],
		"checks": checks,
		"can_switch": rules.can_switch(checks),
		"receipts": facts["receipts"],
		# Whether the form's own save would refuse, and on which document. The form script asks
		# only so it can step in before ERPNext's message; the switch never depends on it.
		"locked_by": erpnext_refusal(item_code),
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
	# Serialises two clicks, or this and a form save, on the Item row.
	frappe.db.get_value("Item", item_code, "name", for_update=True)
	result = run_checks(item_code)
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


def _line(row, **extra):
	line = {"idx": row.get("idx"), "item_code": row.get("item_code"), "name": row.get("name")}
	line.update(extra)
	return line


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
	"""``Purchase Receipt`` → ``validate``: refuse a return of a receipt line that wrote no stock."""
	if _stands_aside(doc) or not cint(doc.get("is_return")):
		return
	# Keyed on the line being returned (purchase_receipt_item), which is what the ledger is asked about.
	lines = [
		_line(row, name=row.get("purchase_receipt_item"))
		for row in doc.get("items") or []
		if row.get("item_code") and row.get("purchase_receipt_item")
	]
	stock_items = _stock_items(line["item_code"] for line in lines)
	lines = [line for line in lines if line["item_code"] in stock_items]
	if not lines:
		return
	unstocked = rules.unstocked_lines(
		lines, stock_items, _receipt_lines_with_stock(line["name"] for line in lines)
	)
	if unstocked:
		frappe.throw(rules.return_refusal(unstocked), title=_("This return can't be submitted"))


def refuse_billing_unstocked_receipts(doc, method=None):
	"""``Purchase Invoice`` → ``validate``: refuse a bill line for goods a non-stock line received."""
	if _stands_aside(doc):
		return
	rows = [
		{
			"idx": row.get("idx"),
			"item_code": row.get("item_code"),
			"pr_detail": row.get("pr_detail") or "",
			"po_detail": row.get("po_detail") or "",
		}
		for row in doc.get("items") or []
		if row.get("item_code") and (row.get("pr_detail") or row.get("po_detail"))
	]
	stock_items = _stock_items(row["item_code"] for row in rows)
	rows = [row for row in rows if row["item_code"] in stock_items]
	if not rows:
		return
	# A line billed from the order rather than the receipt bills every receipt line of that order line.
	order_lines = sorted({row["po_detail"] for row in rows if row["po_detail"] and not row["pr_detail"]})
	receipt_lines_by_order = {}
	if order_lines:
		for receipt_line in frappe.get_all(
			"Purchase Receipt Item",
			filters={"purchase_order_item": ("in", order_lines), "docstatus": 1},
			fields=["name", "purchase_order_item"],
		):
			receipt_lines_by_order.setdefault(receipt_line.purchase_order_item, []).append(receipt_line.name)
	billed = {row["pr_detail"] for row in rows if row["pr_detail"]}
	for names in receipt_lines_by_order.values():
		billed.update(names)
	offenders = rules.billing_offenders(
		rows, stock_items, receipt_lines_by_order, _receipt_lines_with_stock(billed)
	)
	if offenders:
		frappe.throw(rules.bill_refusal(offenders), title=_("These receipts can't be billed here"))
