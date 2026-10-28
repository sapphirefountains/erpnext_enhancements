# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turning on Maintain Stock for an Item the form refuses to switch (v1.754.0). No Frappe, no I/O.

ERPNext's ``Item.cant_change`` refuses to change ``is_stock_item`` once any submitted line uses
the item. The form says "Linked with submitted documents". On production a site Property
Setter defaults the box to off, so items start non-stock. About 490 enabled non-stock items were
locked this way on 2026-10-27, because almost every Purchase Receipt so far received them as
non-stock. Items first bought per job keep becoming items we stock, so the action that switches
them lives here instead of in a bench console. The reads are ``maintain_stock.py``, and the
endpoints are ``api/maintain_stock.py``.

**Why bypassing the lock is safe under these checks.** ERPNext's list is a list of places where
a non-stock line would go wrong once the item keeps stock. This module refuses every one of
them except one: the receipts.

* A Delivery Note, Sales or POS Invoice, Purchase Invoice, Stock Entry, Stock Reconciliation,
  Work Order or BOM posted while the item was non-stock moved no stock. Canceling or returning
  one after the switch would move stock that never moved. Any submitted one refuses.
* An *open* Purchase Order, Material Request or Sales Order line would leave the Bin's ordered,
  requested or reserved quantity wrong. ERPNext counts those only for stock items. "Open" is
  ERPNext's own rule from ``erpnext/stock/stock_balance.py`` at v16.52.1, the rule a Bin is
  recomputed from. A finished line counts for nothing there, so it refuses nothing here. That is
  the one place this module is looser than ``cant_change``, and on purpose: nearly every item
  has a fully received Purchase Order behind it.
* A Product Bundle must stay non-stock (``ProductBundle.validate_main_item``). So must a fixed
  asset item (``Item.validate_fixed_asset``).
* A variant takes Maintain Stock from its template. The default Item Variant Settings copy every
  field but a short exclusion list, so the template's next save would set it back. A template is
  refused too: ``set_value`` skips ``update_variants``, so its variants would stay non-stock, and
  the template's next save would push the flag onto each of them and hit each one's own
  ``cant_change``. Production had no variants on 2026-10-28; the checks cost nothing.
* The item as a **component** of a Product Bundle is not in ERPNext's list at all, but its rows
  (``tabPacked Item``) move stock by today's flag (``SellingController.get_item_list`` expands a
  bundle into them, ``update_stock_ledger`` keeps the stock ones) and are reserved on an open
  Sales Order (``get_reserved_qty``). So a submitted Delivery Note, Sales or POS Invoice carrying
  it inside a bundle refuses, and so does an open Sales Order (the Sales Order rule below, applied
  to the bundle's line). Production had no bundles on 2026-10-28.
* A submitted **return** Purchase Receipt refuses, though an ordinary receipt does not. ERPNext
  posts a return's lines to the GL whatever their valuation rate
  (``PurchaseReceipt.make_item_gl_entries``: ``flt(d.valuation_rate) or self.is_return``), and an
  ordinary accounting repost of the return (a backdated entry on another item on it, or a plain
  Transaction repost) rebuilds that GL from today's flag. So after the switch the old return
  would book Stock Received But Not Billed against expense for a line that originally posted
  nothing, and no guard here sees an ordinary repost. Refusing the switch closes that whole class;
  production had no submitted return receipts on 2026-10-28.
* **Purchase Receipt lines are the point.** They stay as they were: non-stock lines with no
  stock ledger entry, and ``maintain_stock``'s guards refuse everything in ERPNext v16.52.1 that
  would treat them as stock lines after the switch: a cancel or a return, which would take out
  stock the line never put in; a bill, which would debit Stock Received But Not Billed for a
  receipt that never credited it; and anything that rebuilds the receipt's ledger from today's
  flag (a Landed Cost Voucher, a Repost Item Valuation that recreates stock ledgers, a Repost
  Accounting Ledger), which would give the line backdated stock and, with it, a ledger entry that
  makes every other guard let it through. Another path that rebuilds a receipt's ledger, in a
  later ERPNext or another app, would not be guarded: the receipts are as safe as that list is
  complete, which is the reason the lock list below is re-read at run time.

**The list itself is checked against the installed ERPNext.** The doctypes come from the
``linked_doctypes`` lists in ``Item._get_linked_submitted_documents``. ERPNext's prod version
moves whenever ``app-upstream-prod`` runs, and a copied list is right only on the day it is
written. So :func:`lock_list_from_source` reads that method's source at run time, and
:func:`unknown_lock_doctypes` names any doctype ERPNext checks that :data:`LOCK_HANDLING` does not
know. Then the action refuses until somebody decides what the new one means. It fails closed: an
action that bypasses a safety lock must not keep working after the lock grows.

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import ast
import textwrap

#: Who may switch an item. Either role is enough. Proposed to Nik in the v1.754.0 PR. The form
#: script ``public/js/item_maintain_stock.js`` holds a copy to decide whether to show the action,
#: and ``tests/test_maintain_stock.py`` fails the build when the two differ. The endpoint is the
#: gate; the copy only hides a button.
SWITCH_ROLES = ("Stock Manager", "Item Manager")

#: Accounts that hold those roles without being a person at a screen. Switching is a person's
#: decision, made after reading the checks, so these are refused whatever roles they hold:
#: ``triton@`` holds Stock Manager and Item Manager for its sync work, and ``Administrator`` is
#: the account the 2026-08-02 compromise came through. ``design_review/authority.py`` keeps the
#: same list for the same reason; the app keeps no registry of service accounts.
NOT_A_PERSON = frozenset(
	{
		"Administrator",
		"Guest",
		"triton@sapphirefountains.com",
		"mdm@sapphirefountains.com",
	}
)


def may_switch(user, roles):
	"""Whether ``user``, holding ``roles``, may run the checks and the switch."""
	user = (user or "").strip()
	return bool(user) and user not in NOT_A_PERSON and bool(set(SWITCH_ROLES) & set(roles or ()))


#: What this action does with each doctype in ERPNext's lock list.
#:   ``guarded``  -- allowed; the lines stay non-stock and ``maintain_stock``'s guards protect them
#:   ``submitted`` -- any submitted line refuses
#:   ``open``      -- an open line refuses (ERPNext's Bin rule; see :data:`OPEN_RULES`)
#:   ``bundle``    -- the item is a Product Bundle; refuses
LOCK_HANDLING = {
	"Purchase Receipt Item": "guarded",
	"Delivery Note Item": "submitted",
	"Sales Invoice Item": "submitted",
	"POS Invoice Item": "submitted",
	"Purchase Invoice Item": "submitted",
	"Stock Entry Detail": "submitted",
	"Stock Reconciliation Item": "submitted",
	"Work Order Item": "submitted",
	"BOM": "submitted",
	"Purchase Order Item": "open",
	"Material Request Item": "open",
	"Sales Order Item": "open",
	"Product Bundle": "bundle",
}

#: When an order line still counts, as ERPNext recomputes a Bin (stock_balance.py, v16.52.1).
#: ``get_purchase_order_qty``: ``qty > received_qty``, the order not Closed or Delivered, and
#: not a drop-ship line. ``get_indented_qty``: ``stock_qty > ordered_qty`` ("Completed Qty"), the
#: request not Stopped. ``get_reserved_qty``: the order not Closed (On Hold is *kept* here, not
#: dropped as the Bin does: a held order is resumed, and its reservation with it), not drop-ship,
#: and something left to deliver. Read by ``maintain_stock`` to build its queries.
OPEN_RULES = {
	"Purchase Order Item": {
		"parent": "Purchase Order",
		"outstanding": ("qty", "received_qty"),
		"closed_statuses": ("Closed", "Delivered"),
		"skip_drop_ship": True,
	},
	"Material Request Item": {
		"parent": "Material Request",
		"outstanding": ("stock_qty", "ordered_qty"),
		"closed_statuses": ("Stopped",),
		"skip_drop_ship": False,
	},
	"Sales Order Item": {
		"parent": "Sales Order",
		"outstanding": ("qty", "delivered_qty"),
		"closed_statuses": ("Closed",),
		"skip_drop_ship": True,
	},
}

#: The documents whose submitted Packed Item rows (the item inside a bundle) refuse.
PACKED_SUBMITTED_PARENTS = ("Delivery Note", "Sales Invoice", "POS Invoice")

#: A document type as a person reads it: (one, several).
DOCUMENT_NAMES = {
	"Delivery Note": ("Delivery Note", "Delivery Notes"),
	"Sales Invoice": ("Sales Invoice", "Sales Invoices"),
	"POS Invoice": ("POS Invoice", "POS Invoices"),
	"Sales Order": ("Sales Order", "Sales Orders"),
	"Purchase Receipt Item": ("Purchase Receipt", "Purchase Receipts"),
	"Delivery Note Item": ("Delivery Note", "Delivery Notes"),
	"Sales Invoice Item": ("Sales Invoice", "Sales Invoices"),
	"POS Invoice Item": ("POS Invoice", "POS Invoices"),
	"Purchase Invoice Item": ("Purchase Invoice", "Purchase Invoices"),
	"Stock Entry Detail": ("Stock Entry", "Stock Entries"),
	"Stock Reconciliation Item": ("Stock Reconciliation", "Stock Reconciliations"),
	"Work Order Item": ("Work Order", "Work Orders"),
	"BOM": ("BOM", "BOMs"),
	"Purchase Order Item": ("Purchase Order", "Purchase Orders"),
	"Material Request Item": ("Material Request", "Material Requests"),
	"Sales Order Item": ("Sales Order", "Sales Orders"),
	"Product Bundle": ("Product Bundle", "Product Bundles"),
	"Stock Ledger Entry": ("stock ledger entry", "stock ledger entries"),
}

#: The doctypes whose submitted lines refuse, in the order a refusal lists them.
SUBMITTED_DOCTYPES = tuple(d for d, how in LOCK_HANDLING.items() if how == "submitted")

#: The doctypes whose open lines refuse.
OPEN_DOCTYPES = tuple(d for d, how in LOCK_HANDLING.items() if how == "open")

#: How many example documents a check names. Enough to find the problem, short enough to read.
EXAMPLES = 3


def lock_list_from_source(source):
	"""The doctypes ERPNext's lock checks, read from ``_get_linked_submitted_documents``'s source.

	Every string in a list literal assigned (``=``) or added (``+=``) to ``linked_doctypes``. Both
	branches count, the ``is_stock_item`` one included, since that is the field this action
	changes. ``None`` when the source cannot be parsed or names no such list, so the caller can
	refuse rather than guess.
	"""
	try:
		tree = ast.parse(textwrap.dedent(source or ""))
	except (SyntaxError, ValueError):
		return None
	found = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Assign):
			targets, value = node.targets, node.value
		elif isinstance(node, ast.AugAssign):
			targets, value = [node.target], node.value
		else:
			continue
		if not any(isinstance(t, ast.Name) and t.id == "linked_doctypes" for t in targets):
			continue
		if not isinstance(value, (ast.List, ast.Tuple)):
			continue
		for element in value.elts:
			if isinstance(element, ast.Constant) and isinstance(element.value, str):
				if element.value not in found:
					found.append(element.value)
	return found or None


def unknown_lock_doctypes(lock_list):
	"""The doctypes in ERPNext's lock list that :data:`LOCK_HANDLING` has no rule for."""
	return [d for d in lock_list or () if d not in LOCK_HANDLING]


def count_phrase(count, doctype):
	"""``"1 Sales Invoice"``, ``"3 Sales Invoices"``."""
	one, several = DOCUMENT_NAMES.get(doctype, (doctype, doctype))
	return f"{count} {one if count == 1 else several}"


def _phrases(groups):
	"""``"2 Sales Invoices and 1 Stock Entry"`` from ``{doctype: {"count": n}}``, in list order."""
	parts = [count_phrase(g["count"], d) for d, g in groups.items() if g and g.get("count")]
	if len(parts) <= 1:
		return "".join(parts)
	return ", ".join(parts[:-1]) + " and " + parts[-1]


def _verb(groups, one, several):
	"""``one`` when the groups add up to a single document, else ``several``."""
	counts = [g.get("count") or 0 for g in groups.values() if g]
	return one if sum(counts) == 1 else several


def _examples(groups):
	out = []
	for group in groups.values():
		for example in (group or {}).get("examples") or ():
			if example not in out:
				out.append(example)
	return out[:EXAMPLES]


def _check(key, label, ok, detail="", examples=None):
	return {"key": key, "label": label, "ok": bool(ok), "detail": detail, "examples": list(examples or [])}


def evaluate(facts):
	"""Every safety check, in the order the dialog shows them.

	``facts`` is what ``maintain_stock.gather_facts`` read: the item's own flags, and for each
	document group a ``{"count": n, "examples": [{"doctype", "name"}]}``. Each check is
	``{key, label, ok, detail, examples}``; ``detail`` says what failed, in words.
	"""
	submitted = {d: (facts.get("submitted") or {}).get(d) for d in SUBMITTED_DOCTYPES}
	submitted = {d: g for d, g in submitted.items() if g and g.get("count")}
	open_lines = {d: (facts.get("open") or {}).get(d) for d in OPEN_DOCTYPES}
	open_lines = {d: g for d, g in open_lines.items() if g and g.get("count")}
	ledger = facts.get("stock_ledger") or {}
	bundles = facts.get("bundles") or {}
	packed = {d: g for d, g in (facts.get("packed") or {}).items() if g and g.get("count")}
	returns = facts.get("receipt_returns") or {}
	lock_list = facts.get("lock_list")
	unknown = unknown_lock_doctypes(lock_list)

	checks = [
		_check(
			"still_off",
			"Maintain Stock is still off",
			not facts.get("is_stock_item"),
			"" if not facts.get("is_stock_item") else "Maintain Stock is already on for this item.",
		),
		_check(
			"not_asset",
			"It isn't a fixed asset item",
			not facts.get("is_fixed_asset"),
			""
			if not facts.get("is_fixed_asset")
			else "It is a fixed asset item, and ERPNext keeps those non-stock.",
		),
		_check(
			"not_variant",
			"It isn't a variant",
			not facts.get("variant_of"),
			""
			if not facts.get("variant_of")
			else (
				f"It is a variant of {facts.get('variant_of')}. ERPNext copies Maintain Stock from a "
				"template to its variants, so the template's next save would turn it back off."
			),
		),
		_check(
			"not_template",
			"It isn't a template with variants",
			not facts.get("has_variants"),
			""
			if not facts.get("has_variants")
			else (
				"It is a template. Switching it here wouldn't reach its variants, and the template's next "
				"save would try to copy Maintain Stock onto each one, which ERPNext refuses for any "
				"variant already in use. Switch the variants themselves."
			),
		),
		_check(
			"no_ledger",
			"Nothing in the stock ledger",
			not ledger.get("count"),
			""
			if not ledger.get("count")
			else f"It already has {count_phrase(ledger['count'], 'Stock Ledger Entry')}.",
			ledger.get("examples"),
		),
		_check(
			"not_bundle",
			"It isn't a Product Bundle",
			not bundles.get("count"),
			""
			if not bundles.get("count")
			else "It is a Product Bundle, and ERPNext requires a bundle to stay non-stock.",
			bundles.get("examples"),
		),
		_check(
			"not_bundle_component",
			"It isn't inside a Product Bundle on a submitted or open document",
			not packed,
			""
			if not packed
			else (
				f"It is inside a Product Bundle on {_phrases(packed)}. ERPNext moves a bundle's parts by "
				"today's Maintain Stock setting, so canceling, returning or delivering "
				f"{_verb(packed, 'that', 'those')} would move stock that never moved."
			),
			_examples(packed),
		),
		_check(
			"no_stock_documents",
			"No submitted delivery notes, invoices, stock entries, reconciliations, work orders or BOMs",
			not submitted,
			""
			if not submitted
			else (
				f"{_phrases(submitted)} {_verb(submitted, 'uses', 'use')} it. "
				f"{_verb(submitted, 'It was', 'They were')} posted while the item kept no stock, so "
				"canceling or returning one after the switch would move stock that never moved."
			),
			_examples(submitted),
		),
		_check(
			"no_receipt_returns",
			"No submitted Purchase Receipt returns",
			not returns.get("count"),
			""
			if not returns.get("count")
			else (
				f"{count_phrase(returns['count'], 'Purchase Receipt Item')} "
				f"{'returns' if returns['count'] == 1 else 'return'} it. ERPNext posts a return's lines to "
				"the accounts whatever their rate, and an ordinary repost rebuilds those accounts from "
				"today's Maintain Stock setting, so the switch would make a return book what it never booked."
			),
			returns.get("examples"),
		),
		_check(
			"no_open_orders",
			"No open purchase orders, material requests or sales orders",
			not open_lines,
			""
			if not open_lines
			else (
				f"{_phrases(open_lines)} still {_verb(open_lines, 'has', 'have')} some of it to receive, "
				"order or deliver. ERPNext "
				"counts what is on order, requested and reserved only for stock items, so those "
				"numbers would start out wrong. Receive, close or finish them first."
			),
			_examples(open_lines),
		),
		_check(
			"known_lock",
			"ERPNext's lock is the one these checks were written for",
			lock_list is not None and not unknown,
			""
			if lock_list is not None and not unknown
			else (
				"Couldn't read the list of documents ERPNext's lock checks, so this action can't be sure it "
				"checks them all."
				if lock_list is None
				else (
					f"ERPNext's lock now also checks {', '.join(unknown)}, which this action doesn't know "
					"yet. It refuses until someone updates it (inventory_enhancements/maintain_stock_rules.py)."
				)
			),
		),
	]
	return checks


def can_switch(checks):
	"""True when every check passed."""
	return bool(checks) and all(c["ok"] for c in checks)


def refusal_message(item_code, checks):
	"""One paragraph naming every failed check, for the endpoint's refusal."""
	failed = [c["detail"] or c["label"] for c in checks if not c["ok"]]
	return f"Maintain Stock was not turned on for {item_code}. " + " ".join(failed)


def switch_comment(full_name, when, receipt_count):
	"""The Comment left on the Item: who, when, how, and what stays protected."""
	text = (
		f"{full_name} turned on Maintain Stock on {when}, through the Item form's "
		'"Turn on Maintain Stock" action. Every safety check passed first. ERPNext\'s form refuses '
		"this change once a submitted document uses the item, so the action wrote the flag directly."
	)
	if receipt_count:
		text += (
			f" {count_phrase(receipt_count, 'Purchase Receipt Item')} received it while it kept no "
			"stock. They stay as they are: this app refuses canceling, returning, billing or re-costing "
			"them in ERPNext."
		)
	return text


def next_steps(item_code, account, account_is_company_default):
	"""What to do after the switch, in words. ``account`` may be ``None``."""
	text = (
		f"Maintain Stock is on for {item_code}. It has no cost yet, so put its first stock in "
		"with a Stock Reconciliation that gives a quantity and a valuation rate."
	)
	if account and not account_is_company_default:
		text += (
			f" Pick {account} as its Difference Account: the company has no default Stock "
			"Adjustment Account, so ERPNext won't fill it in."
		)
	elif not account:
		text += " Pick the Stock Adjustment account as its Difference Account."
	text += (
		" Don't cancel, return, bill or re-cost its earlier receipts in ERPNext (a Landed Cost "
		"Voucher or a repost would rebuild them): they put nothing into stock, and this app refuses "
		"all of these."
	)
	return text


# ---------------------------------------------------------------------------
# The receipts' guard
# ---------------------------------------------------------------------------


def unstocked_lines(lines, stock_items, lines_with_stock, line_key="name"):
	"""The lines of a stock item now that never wrote a stock ledger entry.

	``lines`` are dicts carrying ``item_code`` and ``line_key``; ``stock_items`` the item codes that
	keep stock today; ``lines_with_stock`` the keys that have a stock ledger entry. A line of a
	stock item with none was received while the item kept no stock. Nothing else makes a stock
	item's receipt line write nothing, so the ledger is the test, not the date of the switch, and
	it covers an item switched any way at all (the bench console included).
	"""
	stock_items = set(stock_items or ())
	lines_with_stock = set(lines_with_stock or ())
	return [
		line
		for line in lines or ()
		if line.get("item_code") in stock_items
		and line.get(line_key)
		and line.get(line_key) not in lines_with_stock
	]


def _rows(lines):
	return ", ".join(f"row {line.get('idx')} ({line.get('item_code')})" for line in lines)


def _first_upper(text):
	return text[:1].upper() + text[1:]


def cancel_refusal(receipt, lines, is_return=False):
	"""Why a Purchase Receipt can't be canceled. A return's cancel would put stock back instead."""
	if is_return:
		effect = (
			"took nothing out of stock. Maintain Stock has since been turned on, and canceling now "
			"would put that quantity back into stock anyway."
		)
	else:
		effect = (
			"put nothing into stock. Maintain Stock has since been turned on, and canceling now "
			"would take that quantity out of stock anyway."
		)
	return (
		f"Purchase Receipt {receipt} can't be canceled. {_first_upper(_rows(lines))} came in while "
		f"the item kept no stock, so the line {effect} If this receipt is wrong, ask accounting to "
		"correct it another way."
	)


def return_refusal(lines):
	"""Why a return against such a receipt can't be submitted."""
	return (
		f"This return can't be submitted. {_first_upper(_rows(lines))} returns goods that came in "
		"while the item kept no stock, so their receipt line put nothing into stock or into Stock "
		"Received But Not Billed. Maintain Stock has since been turned on, and returning them now "
		"would take out what was never put in. Ask accounting how to record this return."
	)


def rebuild_refusal(doctype, unstocked, canceling=False):
	"""Why a document that would rebuild an old receipt's ledger is refused.

	``unstocked`` is ``{receipt: [lines]}``.
	"""
	what = {
		"Landed Cost Voucher": "This Landed Cost Voucher",
		"Repost Item Valuation": "This repost",
		"Repost Accounting Ledger": "This repost",
	}.get(doctype, "This document")
	verb = "can't be canceled" if canceling else "can't include it"
	receipts = "; ".join(
		f"Purchase Receipt {receipt}: {_rows(lines)}" for receipt, lines in unstocked.items()
	)
	return (
		f"{what} {verb}. {receipts} came in while the item kept no stock. It would rebuild that "
		"receipt's stock ledger and accounts from today's Maintain Stock setting, which would put "
		"the quantity into stock, backdated, and credit Stock Received But Not Billed. Ask accounting "
		"how to correct the receipt another way."
	)


def touches_unstocked(rows, stock_items, lines_with_stock):
	"""The rows of a stock item that reach a receipt line with no stock ledger entry.

	Each row carries ``item_code`` and ``lines``, the receipt lines it bills or returns. One line
	without a ledger entry is enough to refuse: the row can't say which of the goods it means.
	"""
	stock_items = set(stock_items or ())
	lines_with_stock = set(lines_with_stock or ())
	return [
		row
		for row in rows or ()
		if row.get("item_code") in stock_items
		and any(name not in lines_with_stock for name in row.get("lines") or ())
	]


def billed_lines(row, receipt_lines_by_order=None, receipt_lines_by_receipt=None):
	"""The receipt lines one bill row pays for.

	Its receipt line (``pr_detail``) when it names one. Otherwise every line of its receipt for its
	item (``purchase_receipt``: ``set_expense_account`` decides on that field alone) and every
	receipt line of its order line (``po_detail``). A row whose order line has no receipt yet is
	ERPNext's ordinary bill-before-receipt, and reaches nothing.
	"""
	if row.get("pr_detail"):
		return [row["pr_detail"]]
	lines = []
	if row.get("purchase_receipt"):
		lines += (receipt_lines_by_receipt or {}).get((row["purchase_receipt"], row.get("item_code"))) or []
	if row.get("po_detail"):
		lines += (receipt_lines_by_order or {}).get(row["po_detail"]) or []
	return lines


def billing_offenders(
	rows, stock_items, receipt_lines_by_order, lines_with_stock, receipt_lines_by_receipt=None
):
	"""The bill rows that bill goods a non-stock receipt line brought in (:func:`billed_lines`)."""
	judged = [
		dict(row, lines=billed_lines(row, receipt_lines_by_order, receipt_lines_by_receipt))
		for row in rows or ()
	]
	return touches_unstocked(judged, stock_items, lines_with_stock)


def bill_refusal(lines):
	"""Why a Purchase Invoice can't bill such a receipt."""
	return (
		f"This invoice can't bill {_rows(lines)}. Those goods came in while the item kept no stock, "
		"so their receipt added nothing to Stock Received But Not Billed. Maintain Stock has since "
		"been turned on, and billing them here would book them against that account anyway and "
		"leave it wrong. Enter this bill in QuickBooks, or ask accounting."
	)
