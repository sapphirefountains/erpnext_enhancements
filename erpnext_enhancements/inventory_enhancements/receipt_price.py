# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A price is required when a stock item is received (v1.803.0): the hooks and the reads.

Every judgement and every sentence is ``receipt_price_rules``; this module finds which items keep
stock, and talks to ERPNext.

* :func:`refuse_unpriced_stock_lines` -- ``Purchase Receipt`` and ``Purchase Invoice``
  ``before_submit``. The one refusal, and it covers every way a receipt gets made, because each of
  them ends in the same submit: the Purchase Receipt form, the order's Receive Items dialog, the
  Stock Scan page (and the store-run sheet's *Receive on PO*, which is the same endpoint), and the
  drafts the packing-slip and vendor-bill intake create. Also ``Stock Entry`` ``before_submit``,
  for a Material Receipt row that would come in at $0 (``receipt_price_rules`` says how one can).
* :func:`price_block` says why a person may not price an order (no write on it, or they raised
  the request it fills), and :func:`ask_purchasing` leaves purchasing a ToDo for the lines such a
  person had to set aside. Those lines are left unreceived; they never come in at $0.
* :func:`warn_unpriced_stock_lines` -- the same two doctypes' ``validate``, on a draft, in a web
  request only. Says the draft saved and will not submit as it is. Never raises.
* :func:`needing_price` and :func:`price_order_lines` -- for the two receiving screens, which ask
  for the price *before* a receipt exists and put it on the order line through ERPNext's own
  *Update Items* (``update_child_qty_rate``). That function checks write permission on the order,
  re-runs this app's submit gates (``hooks.py``: the approval threshold and the requester
  separation both listen on ``on_update_after_submit``), recalculates the order and recounts its
  Bin. Nothing here writes a rate itself. A line received as free on purpose changes no price,
  so it gets a Comment on the order instead: who, and which rows.

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
from erpnext_enhancements.inventory_enhancements.maintain_stock_rules import NOT_A_PERSON

#: The role that may write a Purchase Order on this site (``po_segregation.CREATOR_ROLE``): who
#: is asked for a price when the order's own author can't be.
PRICING_ROLE = "PO Creator"

#: The most people one ToDo round asks.
MAX_ASKED = 5

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
		purpose=doc.get("purpose"),
	):
		return []
	rows = [row for row in doc.get("items") or [] if row.get("item_code")]
	rows = rules.incoming_rows(doc.doctype, rows)
	return rules.unpriced_lines(
		rows,
		_stock_items(row.get("item_code") for row in rows),
		rate_field=rules.rate_field(doc.doctype),
	)


def refuse_unpriced_stock_lines(doc, method=None):
	"""``before_submit``: refuse a stock item's line with no price, naming it and what to do."""
	if _stands_aside(doc):
		return
	lines = _unpriced(doc)
	if lines:
		frappe.throw(
			html.escape(rules.submit_refusal(doc.doctype, lines), quote=False), title=_("A line has no price")
		)


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


def price_block(po):
	"""Why the caller may not put a price on this order, or ``None`` when they may.

	``rules.BLOCK_NO_WRITE``: no write permission on the order, which ERPNext's *Update Items*
	asks for. ``rules.BLOCK_REQUESTER``: they raised a Material Request the order fills, and this
	app's separation-of-duties gate (``po_segregation``, re-run by *Update Items*) refuses them.
	Asked here so the screens never offer a price box that could only fail.
	"""
	try:
		if not frappe.has_permission("Purchase Order", "write", doc=po.name):
			return rules.BLOCK_NO_WRITE
	except Exception:
		return rules.BLOCK_NO_WRITE
	try:
		from erpnext_enhancements import po_segregation

		if (
			po_segregation.is_enforcement_enabled()
			and not po_segregation.is_separation_exempt()
			and po_segregation.get_conflicting_material_requests(po)
		):
			return rules.BLOCK_REQUESTER
	except Exception:
		# The gate itself decides when the price is written; this is only the early answer.
		pass
	return None


def may_price(po):
	"""Whether the caller may put a price on this order (:func:`price_block` says why not)."""
	return price_block(po) is None


def may_mark_free(po):
	"""Whether the caller may receive a line as "no charge" from the two screens."""
	return may_price(po) if rules.NO_CHARGE_NEEDS_ORDER_WRITE else True


def _people(users):
	"""The enabled, real people among ``users``, the caller left out, in the order given."""
	wanted = [u for u in users if u and u != frappe.session.user and u not in NOT_A_PERSON]
	if not wanted:
		return []
	enabled = set(frappe.get_all("User", filters={"name": ("in", wanted), "enabled": 1}, pluck="name"))
	seen, out = set(), []
	for user in wanted:
		if user in enabled and user not in seen:
			seen.add(user)
			out.append(user)
	return out


def _who_to_ask(po):
	"""Who is asked for the price: the person who wrote the order, when they can still price it;
	otherwise everyone who holds :data:`PRICING_ROLE`."""
	holders = frappe.get_all("Has Role", filters={"role": PRICING_ROLE, "parenttype": "User"}, pluck="parent")
	owner = po.get("owner")
	if owner in holders and _people([owner]):
		return [owner]
	return _people(sorted(holders))[:MAX_ASKED]


def ask_purchasing(po, lines):
	"""Leave purchasing a ToDo on the order naming the lines that need a price.

	Returns the full names of the people who now have one, whether it was made just now or was
	already open (an order is not asked about twice). Never raises: a ToDo that can't be made must
	not be the reason a delivery can't be received.
	"""
	try:
		text = rules.price_todo(po.name, lines, get_fullname(frappe.session.user))
		asked = []
		for user in _who_to_ask(po):
			open_already = frappe.get_all(
				"ToDo",
				filters={
					"reference_type": "Purchase Order",
					"reference_name": po.name,
					"allocated_to": user,
					"status": "Open",
					"description": ("like", f"%{rules.TODO_MARKER}%"),
				},
				pluck="name",
				limit=1,
			)
			if not open_already:
				frappe.get_doc(
					{
						"doctype": "ToDo",
						"allocated_to": user,
						"assigned_by": frappe.session.user,
						"reference_type": "Purchase Order",
						"reference_name": po.name,
						"description": html.escape(text, quote=False),
						"priority": "High",
						"status": "Open",
					}
				).insert(ignore_permissions=True)
			asked.append(get_fullname(user))
		return asked
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Receipt price: asking purchasing failed")
		return []


def price_order_lines(po, prices):
	"""Put ``prices`` (``{order line: rate}``) on the order through ERPNext's *Update Items*.

	Refuses, in the receiver's words, when the caller may not price the order. Everything else
	(the order's own status, a workflow, the approval threshold) is ERPNext's or that gate's
	refusal, and reaches the person as it words it.
	"""
	if not prices:
		return
	block = price_block(po)
	if block:
		frappe.throw(html.escape(rules.block_sentence(block, po.name), quote=False), frappe.PermissionError)
	from erpnext.controllers.accounts_controller import update_child_qty_rate

	# Read the order again, now: Update Items deletes any row it is not sent, so the rows must be
	# the ones the order holds at this moment, not the ones it held when the request began.
	fresh = frappe.get_doc("Purchase Order", po.name)
	update_child_qty_rate("Purchase Order", json.dumps(rules.price_changes(fresh.items, prices)), po.name)
	rows = ", ".join(str(row.idx) for row in fresh.items if row.name in prices)
	comment = rules.price_comment(get_fullname(frappe.session.user), rows)
	frappe.get_doc("Purchase Order", po.name).add_comment("Comment", html.escape(comment, quote=False))


def settle_before_receipt(po, wanted, typed, hold=True):
	"""Deal with the unpriced lines among ``wanted`` before a receipt is built.

	``typed`` is ``{order line: {"rate", "no_charge", "reason"}}`` from the screen. Returns
	``{"free", "held", "held_lines", "block", "asked", "message"}``:

	* a price a person may set goes on the order (:func:`price_order_lines`);
	* ``free`` are the lines marked free by a person who may, for the caller to tick
	  ``allow_zero_valuation_rate`` on; each leaves a Comment on the order with the reason;
	* ``held`` are the lines this person can neither price nor mark free. They are left out of the
	  receipt, and purchasing is asked for the price (:func:`ask_purchasing`). ``message`` says so.

	Refuses when a person who *may* price left a line unanswered, or typed something unusable.
	With ``hold=False`` (the Stock Scan page, which receives one line and asks first) a line that
	would be held refuses instead, and no ToDo is made here: the page made it when it asked.
	"""
	out = {"free": [], "held": [], "held_lines": [], "block": None, "asked": [], "message": ""}
	needing = needing_price(po, wanted=wanted)
	if not needing:
		return out
	block = price_block(po)
	free_ok = block is None or not rules.NO_CHARGE_NEEDS_ORDER_WRITE
	settled = rules.settle(needing, typed, can_price=block is None, can_mark_free=free_ok)
	if settled["problems"]:
		frappe.throw(
			"<br>".join(html.escape(problem, quote=False) for problem in settled["problems"]),
			title=_("Nothing was received"),
		)
	if settled["missing"]:
		frappe.throw(
			html.escape(rules.receive_refusal(po.name, settled["missing"]), quote=False),
			title=_("Nothing was received"),
		)
	held = settled["held"]
	if held and not hold:
		frappe.throw(
			html.escape(rules.held_message(po.name, held, block, [], False), quote=False),
			title=_("Nothing was received"),
		)
	price_order_lines(po, settled["prices"])
	if settled["free"]:
		# A free line changes no price, so it would leave no trace on the order. Say who, which
		# rows and why.
		idx = {line["purchase_order_item"]: line["idx"] for line in needing}
		by_reason = {}
		for name, reason in settled["free"]:
			by_reason.setdefault(reason, []).append(str(idx[name]))
		order = frappe.get_doc("Purchase Order", po.name)
		for reason, rows in by_reason.items():
			comment = rules.free_comment(get_fullname(frappe.session.user), ", ".join(rows), reason)
			order.add_comment("Comment", html.escape(comment, quote=False))
		out["free"] = [name for name, _reason in settled["free"]]
	if held:
		out["held"] = [line["purchase_order_item"] for line in held]
		out["held_lines"] = held
		out["block"] = block
		out["asked"] = ask_purchasing(po, held)
		received_some = bool(set(wanted) - set(out["held"]))
		out["message"] = rules.held_message(po.name, held, block, out["asked"], received_some)
	return out
