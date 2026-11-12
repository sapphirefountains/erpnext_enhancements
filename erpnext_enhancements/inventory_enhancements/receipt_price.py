# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A price is required when a stock item is received (v1.803.0): the hooks and the reads.

Every judgement and every sentence is ``receipt_price_rules``; this module finds which items keep
stock, and talks to ERPNext.

* :func:`refuse_unpriced_stock_lines` -- ``Purchase Receipt`` and ``Purchase Invoice``
  ``before_submit``. The one refusal, and it covers every way a receipt gets made, because each of
  them ends in the same submit: the Purchase Receipt form, the order's Receive Items dialog, the
  Stock Scan page (and the store-run sheet's *Receive on PO*, which is the same endpoint), and the
  drafts the packing-slip and vendor-bill intake create.
* :func:`warn_unpriced_stock_lines` -- the same two doctypes' ``validate``, on a draft, in a web
  request only. Says the draft saved and will not submit as it is. Never raises.
* :func:`needing_price` and :func:`price_order_lines` -- for the two receiving screens, which ask
  for the price *before* a receipt exists and put it on the order line through ERPNext's own
  *Update Items* (``update_child_qty_rate``). That function checks write permission on the order,
  re-runs this app's submit gates (``hooks.py``: the approval threshold and the requester
  separation both listen on ``on_update_after_submit``), recalculates the order and recounts its
  Bin. Nothing here writes a rate itself.

The refusal stands aside during install, migrate, patch and test runs (``doc_events`` fire in
ERPNext's own test bootstrap) and for a document whose ``flags.ignore_receipt_price_guard`` is
set, the way out for accounting working in a console. It reads only standard ERPNext fields.

Tabs, per ``CLAUDE.md`` (new file).
"""

import html
import json

import frappe
from frappe import _
from frappe.utils import cint, get_fullname

from erpnext_enhancements.inventory_enhancements import receipt_price_rules as rules

#: ``frappe.flags`` under which the refusal and the warning stand aside.
SKIP_FLAGS = ("in_install", "in_migrate", "in_patch", "in_test", "in_import")

#: Set on a document's ``flags`` to let accounting past the refusal on purpose.
IGNORE_FLAG = "ignore_receipt_price_guard"


def _stands_aside(doc):
	if any(getattr(frappe.flags, flag, False) for flag in SKIP_FLAGS):
		return True
	return bool(getattr(getattr(doc, "flags", None), IGNORE_FLAG, False))


def _stock_items(codes):
	codes = sorted({code for code in codes if code})
	if not codes:
		return set()
	return set(frappe.get_all("Item", filters={"name": ("in", codes), "is_stock_item": 1}, pluck="name"))


def _unpriced(doc):
	"""The document's unpriced lines, or ``[]`` when the rule does not judge it."""
	if not rules.applies(
		doc.doctype,
		is_return=cint(doc.get("is_return")),
		is_internal=cint(doc.get("is_internal_supplier")),
		update_stock=cint(doc.get("update_stock")),
	):
		return []
	rows = [row for row in doc.get("items") or [] if row.get("item_code")]
	return rules.unpriced_lines(rows, _stock_items(row.get("item_code") for row in rows))


def refuse_unpriced_stock_lines(doc, method=None):
	"""``before_submit``: refuse a stock item's line with no price, naming it and what to do."""
	if _stands_aside(doc):
		return
	lines = _unpriced(doc)
	if lines:
		frappe.throw(rules.submit_refusal(doc.doctype, lines), title=_("A line has no price"))


def warn_unpriced_stock_lines(doc, method=None):
	"""``validate``: on a draft, say it will not submit as it is. Never raises."""
	try:
		if _stands_aside(doc) or cint(doc.get("docstatus")) != 0:
			return
		# Being submitted right now: the refusal speaks instead.
		if getattr(doc, "_action", None) == "submit":
			return
		if not getattr(frappe.local, "request", None):
			return
		lines = _unpriced(doc)
		if lines:
			frappe.msgprint(
				html.escape(rules.draft_warning(doc.doctype, lines), quote=False),
				title=_("A line has no price"),
				indicator="orange",
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Receipt price warning failed")


# ---------------------------------------------------------------------------
# Receiving against an order
# ---------------------------------------------------------------------------


def _order_lines(po):
	return [
		{
			"name": row.name,
			"idx": row.idx,
			"item_code": row.item_code,
			"item_name": row.get("item_name"),
			"rate": row.rate,
			"uom": row.uom,
			"is_free_item": row.get("is_free_item"),
		}
		for row in po.get("items") or []
		if row.item_code
	]


def needing_price(po, wanted=None):
	"""The order lines, among ``wanted`` (all when ``None``), that need a price before they can be
	received: ``[{"purchase_order_item", "idx", "item_code", "item_name", "uom"}]``."""
	lines = _order_lines(po)
	return rules.order_lines_needing_price(
		lines, _stock_items(line["item_code"] for line in lines), wanted=wanted
	)


def may_price(po):
	"""Whether the caller may put a price on this order: write permission on it, which is what
	ERPNext's *Update Items* asks for."""
	try:
		return bool(frappe.has_permission("Purchase Order", "write", doc=po.name))
	except Exception:
		return False


def price_order_lines(po, prices):
	"""Put ``prices`` (``{order line: rate}``) on the order through ERPNext's *Update Items*.

	Refuses, in the receiver's words, when the caller may not edit the order. Everything else
	(the order's own status, a workflow, this app's approval gates) is ERPNext's refusal and
	reaches the person as ERPNext words it.
	"""
	if not prices:
		return
	if not may_price(po):
		frappe.throw(rules.cannot_price_refusal(po.name), frappe.PermissionError)
	from erpnext.controllers.accounts_controller import update_child_qty_rate

	update_child_qty_rate("Purchase Order", json.dumps(rules.price_changes(po.items, prices)), po.name)
	rows = ", ".join(str(row.idx) for row in po.items if row.name in prices)
	comment = rules.price_comment(get_fullname(frappe.session.user), rows)
	frappe.get_doc("Purchase Order", po.name).add_comment("Comment", html.escape(comment, quote=False))


def settle_before_receipt(po, wanted, typed):
	"""Deal with the unpriced lines among ``wanted`` before a receipt is built.

	``typed`` is ``{order line: {"rate", "no_charge"}}`` from the screen. Prices go on the order;
	the lines marked free are returned so the caller can tick ``allow_zero_valuation_rate`` on
	their receipt rows. Refuses when a line still has no answer, or a typed price is unusable.
	"""
	needing = needing_price(po, wanted=wanted)
	if not needing:
		return []
	prices, free, missing, problems = rules.settle(needing, typed)
	if problems:
		frappe.throw(
			"<br>".join(html.escape(problem, quote=False) for problem in problems),
			title=_("Nothing was received"),
		)
	if missing:
		frappe.throw(
			html.escape(rules.receive_refusal(po.name, missing), quote=False),
			title=_("Nothing was received"),
		)
	price_order_lines(po, prices)
	return free
