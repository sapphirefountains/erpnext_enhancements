# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Switch the existing physical items to Maintain Stock, with a list a person approves (v1.803.0).

The *Switch Items to Maintain Stock* page (``page/maintain_stock_switch``) is the screen; the
endpoints are in ``api/maintain_stock.py``. Nothing here runs on migrate: no item changes until a
person presses the button.

* **The preview** (:func:`preview`) lists every enabled item that keeps no stock with one of three
  outcomes and the reason (``maintain_stock_bulk_rules``). The safety checks are the single-item
  action's own (``maintain_stock_rules.evaluate``), fed by :func:`bulk_facts`, which reads the
  same facts as ``maintain_stock.gather_facts`` for every item at once (one grouped query a fact
  instead of thirty queries an item). A test holds the two readers equal. The preview is only
  ever a display: applying never trusts it.
* **A person's choice** (:func:`set_decision`) for one item beats the name rule and is kept on a
  ``Maintain Stock Decision`` row, so a list reviewed over several sittings keeps its edits.
* **Applying** (:func:`queue_switch`) marks the chosen rows ``Queued`` and starts a background job
  (:func:`process_queue`). The job switches each item through ``maintain_stock.turn_on``: the row
  lock, every check again, the flag, the open orders recounted, the Comment on the item. It
  commits after each item, so a check that fails for one item refuses that item only.

  **It survives a deploy.** A deploy flushes the job queue and restarts the workers (``CLAUDE.md``).
  The work list is the ``Queued`` rows, not the job's arguments, so nothing is lost: the scheduler
  (:func:`resume_queue`, every ten minutes) starts the job again while any row is Queued, and the
  job picks up where the rows say. An item the killed job had not committed is still Queued and
  still non-stock; one it had committed is Switched. Nothing is switched twice: ``turn_on`` refuses
  an item that is already on.
* **The before-and-after list** is the same rows, as CSV (``maintain_stock_bulk_rules.to_csv``):
  before applying it is the list to approve; afterwards each row says Switched or Refused and why.

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.inventory_enhancements import maintain_stock
from erpnext_enhancements.inventory_enhancements import maintain_stock_bulk_rules as bulk_rules
from erpnext_enhancements.inventory_enhancements import maintain_stock_rules as rules

DECISION = maintain_stock.DECISION

#: The background job's id: one switch job at a time, however often it is asked for.
JOB_ID = "maintain_stock_bulk_switch"
JOB_METHOD = "erpnext_enhancements.inventory_enhancements.maintain_stock_bulk.process_queue"

#: How long one job works before it stops and leaves the rest to the next one (seconds).
JOB_BUDGET = 20 * 60

#: Every item the page lists: enabled, and keeping no stock.
SCOPE = "i.is_stock_item = 0 and i.disabled = 0"


# ---------------------------------------------------------------------------
# The facts, for every item at once
# ---------------------------------------------------------------------------


def _counts(query, values=None):
	"""``{item_code: count}`` from a query whose rows are ``(item_code, count)``."""
	return {row[0]: cint(row[1]) for row in frappe.db.sql(query, values or {})}


def _submitted_counts(child):
	if child == "BOM":
		return _counts(
			"select c.item, count(*) from `tabBOM` c join `tabItem` i on i.name = c.item "
			f"where {SCOPE} and c.docstatus = 1 group by c.item"
		)
	return _counts(
		f"select c.item_code, count(distinct c.parent) from `tab{child}` c "
		f"join `tabItem` i on i.name = c.item_code where {SCOPE} and c.docstatus = 1 group by c.item_code"
	)


def _open_where(child):
	"""The FROM and WHERE of ERPNext's "still open" rule for ``child``, and its values."""
	rule = rules.OPEN_RULES[child]
	outstanding, done = rule["outstanding"]
	statuses = ", ".join(f"%(status{i})s" for i in range(len(rule["closed_statuses"])))
	values = {f"status{i}": s for i, s in enumerate(rule["closed_statuses"])}
	where = (
		f"from `tab{child}` c join `tab{rule['parent']}` p on p.name = c.parent "
		f"join `tabItem` i on i.name = c.item_code "
		f"where {SCOPE} and p.docstatus = 1 and c.{outstanding} > c.{done} "
		f"and p.status not in ({statuses})"
	)
	if rule["skip_drop_ship"]:
		where += " and coalesce(c.delivered_by_supplier, 0) = 0"
	return where, values, done


def _open_lines():
	"""``{item_code: [line]}`` for the open Purchase Order and Material Request lines."""
	out = {}
	for child in rules.RECOUNT_DOCTYPES:
		where, values, done = _open_where(child)
		query = (
			f"select c.item_code, c.name, c.parent, c.idx, c.warehouse, p.company, c.{done} "
			f"{where} order by c.parent, c.idx"
		)
		for row in frappe.db.sql(query, values):
			out.setdefault(row[0], []).append(
				{
					"doctype": child,
					"name": row[1],
					"parent": row[2],
					"idx": row[3],
					"warehouse": row[4] or "",
					"company": row[5] or "",
					"received": (row[6] or 0) if child == "Purchase Order Item" else 0,
				}
			)
	return out


def _packed_counts():
	groups = {}
	for parent in rules.PACKED_SUBMITTED_PARENTS:
		groups[parent] = _counts(
			"select c.item_code, count(distinct c.parent) from `tabPacked Item` c "
			f"join `tabItem` i on i.name = c.item_code where {SCOPE} "
			"and c.parenttype = %(parent)s and c.docstatus = 1 group by c.item_code",
			{"parent": parent},
		)
	groups["Sales Order"] = _counts(
		"select c.item_code, count(distinct c.parent) from `tabPacked Item` c "
		"join `tabItem` i on i.name = c.item_code "
		"join `tabSales Order` so on so.name = c.parent "
		"join `tabSales Order Item` soi on soi.name = c.parent_detail_docname "
		f"where {SCOPE} and c.parenttype = 'Sales Order' and so.docstatus = 1 "
		"and so.status <> 'Closed' and soi.qty > soi.delivered_qty "
		"and coalesce(soi.delivered_by_supplier, 0) = 0 group by c.item_code"
	)
	return groups


def _group(count):
	return {"count": cint(count), "examples": []}


def bulk_facts(items):
	"""``{item_code: facts}`` for ``items`` (rows of ``tabItem``), shaped exactly like
	``maintain_stock.gather_facts``' answer, with counts and no example documents."""
	ledger = _counts(
		"select c.item_code, count(distinct c.voucher_no) from `tabStock Ledger Entry` c "
		f"join `tabItem` i on i.name = c.item_code where {SCOPE} and c.is_cancelled = 0 group by c.item_code"
	)
	bundles = _counts(
		"select c.new_item_code, count(*) from `tabProduct Bundle` c "
		f"join `tabItem` i on i.name = c.new_item_code where {SCOPE} group by c.new_item_code"
	)
	submitted = {
		child: _submitted_counts(child) for child in (*rules.SUBMITTED_DOCTYPES, rules.SALES_INVOICE)
	}
	receipts = _submitted_counts("Purchase Receipt Item")
	returns = _counts(
		"select c.item_code, count(distinct c.parent) from `tabPurchase Receipt Item` c "
		"join `tabPurchase Receipt` p on p.name = c.parent join `tabItem` i on i.name = c.item_code "
		f"where {SCOPE} and c.docstatus = 1 and p.is_return = 1 group by c.item_code"
	)
	invoices_with_stock = _counts(
		"select c.item_code, count(distinct c.parent) from `tabSales Invoice Item` c "
		"join `tabSales Invoice` p on p.name = c.parent join `tabItem` i on i.name = c.item_code "
		f"where {SCOPE} and c.docstatus = 1 and p.update_stock = 1 group by c.item_code"
	)
	open_counts = {}
	for child in rules.OPEN_RULES:
		where, values, _done = _open_where(child)
		open_counts[child] = _counts(
			f"select c.item_code, count(distinct c.parent) {where} group by c.item_code", values
		)
	open_lines = _open_lines()
	packed = _packed_counts()
	lock_list = maintain_stock.erpnext_lock_list()

	facts = {}
	for item in items:
		code = item.get("name")
		lines = open_lines.get(code) or []
		facts[code] = {
			"item_code": code,
			"item_name": item.get("item_name"),
			"is_stock_item": cint(item.get("is_stock_item")),
			"is_fixed_asset": cint(item.get("is_fixed_asset")),
			"variant_of": item.get("variant_of") or "",
			"has_variants": cint(item.get("has_variants")),
			"stock_ledger": _group(ledger.get(code)),
			"bundles": _group(bundles.get(code)),
			"packed": {parent: _group(counts.get(code)) for parent, counts in packed.items()},
			"submitted": {child: _group(counts.get(code)) for child, counts in submitted.items()},
			"open": {child: _group(counts.get(code)) for child, counts in open_counts.items()},
			"open_lines": lines,
			# Only an item with an open line that names no warehouse needs the lookup.
			"fill_warehouses": maintain_stock._fill_warehouses(code, lines)
			if any(not line["warehouse"] for line in lines)
			else {},
			"sales_invoices_with_stock": _group(invoices_with_stock.get(code)),
			"receipts": _group(receipts.get(code)),
			"receipt_returns": _group(returns.get(code)),
			"lock_list": lock_list,
		}
	return facts


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------

_DECISION_FIELDS = [
	"name",
	"item_code",
	"decision",
	"status",
	"result",
	"queued_by",
	"queued_on",
	"switched_on",
	"relax_sales_invoices",
]


def _decisions():
	return {row.item_code: row for row in frappe.get_all(DECISION, fields=_DECISION_FIELDS)}


def _items():
	return frappe.db.sql(
		"select i.name, i.item_name, i.item_group, i.is_stock_item, i.is_fixed_asset, "
		f"i.variant_of, i.has_variants from `tabItem` i where {SCOPE} order by i.name",
		as_dict=True,
	)


def preview(relax_sales_invoices=False):
	"""Every enabled non-stock item with its outcome, and the counts.

	``{"rows", "counts", "sales_invoices_only", "relax_sales_invoices", "queued"}``.
	``sales_invoices_only`` counts the physical items held back by nothing but Sales Invoices that
	never updated stock: the items the page's tick box would add.
	"""
	relax = bool(cint(relax_sales_invoices))
	items = _items()
	facts = bulk_facts(items)
	decisions = _decisions()
	rows, invoices_only = [], 0
	for item in items:
		code = item.get("name")
		saved = decisions.get(code) or {}
		strict = rules.evaluate(facts[code])
		checks = rules.evaluate(facts[code], relax_sales_invoices=True) if relax else strict
		described = {
			"item_code": code,
			"item_name": item.get("item_name"),
			"item_group": item.get("item_group"),
			"is_fixed_asset": item.get("is_fixed_asset"),
		}
		out = bulk_rules.outcome(described, checks, saved.get("decision") or "")
		wanted = bulk_rules.outcome(described, strict, saved.get("decision") or "")
		if (
			wanted["outcome"] == bulk_rules.OUTCOME_BLOCKED
			and bulk_rules.blocked_only_by_sales_invoices(strict)
			and not facts[code]["sales_invoices_with_stock"]["count"]
		):
			invoices_only += 1
		rows.append(
			{
				"item_code": code,
				"item_name": item.get("item_name") or "",
				"item_group": item.get("item_group") or "",
				"outcome": out["outcome"],
				"reason": out["reason"],
				"rule": out["rule"],
				"rule_reason": out["rule_reason"],
				"overridden": out["overridden"],
				"decision": saved.get("decision") or "",
				"status": saved.get("status") or "",
				"result": saved.get("result") or "",
				"fixed_asset": cint(item.get("is_fixed_asset")),
			}
		)
	return {
		"rows": rows,
		"counts": bulk_rules.summarize(rows),
		"sales_invoices_only": invoices_only,
		"relax_sales_invoices": relax,
		"queued": sum(1 for row in rows if row["status"] == bulk_rules.STATUS_QUEUED),
	}


def results():
	"""The rows an apply has finished or still holds: Switched, Refused and Queued, newest first.

	A switched item no longer shows on the preview (it keeps stock now), so this is where the
	"after" half of the list comes from.
	"""
	rows = frappe.get_all(
		DECISION,
		filters={
			"status": (
				"in",
				[bulk_rules.STATUS_SWITCHED, bulk_rules.STATUS_REFUSED, bulk_rules.STATUS_QUEUED],
			)
		},
		fields=[*_DECISION_FIELDS, "rule"],
		order_by="modified desc",
	)
	names = [row.item_code for row in rows]
	items = {}
	if names:
		for item in frappe.get_all(
			"Item", filters={"name": ("in", names)}, fields=["name", "item_name", "item_group"]
		):
			items[item.name] = item
	out = []
	for row in rows:
		item = items.get(row.item_code) or {}
		out.append(
			{
				"item_code": row.item_code,
				"item_name": item.get("item_name") or "",
				"item_group": item.get("item_group") or "",
				"outcome": bulk_rules.OUTCOME_SWITCH,
				"reason": row.get("rule") or "",
				"decision": row.get("decision") or "",
				"status": row.get("status") or "",
				"result": row.get("result") or "",
				"queued_by": row.get("queued_by") or "",
				"switched_on": str(row.get("switched_on") or ""),
			}
		)
	return out


def status():
	"""``{"queued", "switched", "refused"}``: where the current apply stands."""
	counts = {key: 0 for key in ("queued", "switched", "refused")}
	for row in frappe.db.sql(f"select status, count(*) from `tab{DECISION}` group by status"):
		key = (row[0] or "").lower()
		if key in counts:
			counts[key] = cint(row[1])
	return counts


# ---------------------------------------------------------------------------
# A person's choice, and applying
# ---------------------------------------------------------------------------


def _row(item_code):
	"""The item's Maintain Stock Decision row, made (unsaved) when there is none."""
	if frappe.db.exists(DECISION, item_code):
		return frappe.get_doc(DECISION, item_code)
	doc = frappe.new_doc(DECISION)
	doc.item_code = item_code
	return doc


def _save(doc):
	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def set_decision(item_code, decision):
	"""Keep a person's choice for one item: ``Switch``, ``Stay non-stock``, or blank to follow the
	rule again. Changes nothing on the Item."""
	decision = (decision or "").strip()
	if decision and decision not in bulk_rules.DECISIONS:
		frappe.throw(_("Pick Switch, Stay non-stock, or nothing."))
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} doesn't exist.").format(item_code), frappe.DoesNotExistError)
	doc = _row(item_code)
	if doc.status == bulk_rules.STATUS_QUEUED:
		frappe.throw(_("{0} is queued to switch. Stop the run first, then change it.").format(item_code))
	doc.decision = decision
	doc.decided_by = frappe.session.user if decision else None
	doc.decided_on = now_datetime() if decision else None
	_save(doc)
	return {"item_code": item_code, "decision": decision}


def queue_switch(picked=None, relax_sales_invoices=False):
	"""Queue every ``switch`` row of the preview (narrowed to ``picked``) and start the job.

	The preview is computed again here: what the page showed is only what was true when it loaded.
	"""
	relax = bool(cint(relax_sales_invoices))
	rows = preview(relax_sales_invoices=relax)["rows"]
	reasons = {row["item_code"]: row["reason"] for row in rows}
	codes = bulk_rules.to_apply(rows, picked=picked)
	now, user = now_datetime(), frappe.session.user
	for code in codes:
		doc = _row(code)
		doc.status = bulk_rules.STATUS_QUEUED
		doc.queued_by = user
		doc.queued_on = now
		doc.relax_sales_invoices = 1 if relax else 0
		doc.rule = reasons.get(code) or ""
		doc.result = ""
		_save(doc)
	if codes:
		_enqueue()
	return {"queued": len(codes)}


def stop():
	"""Take every Queued row off the list. Items already switched stay switched."""
	names = frappe.get_all(DECISION, filters={"status": bulk_rules.STATUS_QUEUED}, pluck="name")
	for name in names:
		frappe.db.set_value(DECISION, name, {"status": "", "result": "Stopped before it was switched."})
	return {"stopped": len(names)}


def _enqueue():
	frappe.enqueue(
		JOB_METHOD,
		queue="long",
		timeout=JOB_BUDGET + 300,
		job_id=JOB_ID,
		deduplicate=True,
		enqueue_after_commit=True,
	)


def resume_queue():
	"""Scheduler, every ten minutes: start the job again while any row is still Queued.

	This is what makes an apply survive a deploy. Never raises, and does nothing on a site that
	has no such table yet.
	"""
	try:
		if not frappe.db.table_exists(DECISION):
			return
		if frappe.db.count(DECISION, {"status": bulk_rules.STATUS_QUEUED}):
			_enqueue()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Maintain Stock bulk switch: resume failed")


def _switch_one(row):
	"""Switch one queued item through the single-item switch, and write down what happened."""
	try:
		out = maintain_stock.turn_on(
			row.item_code,
			relax_sales_invoices=bool(cint(row.relax_sales_invoices)),
			user=row.queued_by,
			via=rules.VIA_BULK,
		)
		frappe.db.set_value(
			DECISION,
			row.name,
			{
				"status": bulk_rules.STATUS_SWITCHED,
				"switched_on": now_datetime(),
				"invoices_before_switch": cint(out.get("invoices_before_switch")),
				"result": "Maintain Stock is on.",
			},
		)
		frappe.db.commit()
		return True
	except Exception as error:
		frappe.db.rollback()
		if isinstance(error, frappe.ValidationError):
			result = bulk_rules.refused_result(error)
		else:
			frappe.log_error(frappe.get_traceback(), "Maintain Stock bulk switch failed")
			result = "Something went wrong while switching it. The Error Log has the details."
		frappe.db.set_value(DECISION, row.name, {"status": bulk_rules.STATUS_REFUSED, "result": result})
		frappe.db.commit()
		return False


def process_queue():
	"""The background job: switch the Queued rows, oldest first, one commit an item.

	Reads the list a batch at a time, so a row taken off the list meanwhile (:func:`stop`) is
	left alone. Stops when the list is empty or the time budget is spent; the scheduler starts
	the next one.
	"""
	import time

	started = time.monotonic()
	while time.monotonic() - started < JOB_BUDGET:
		rows = frappe.get_all(
			DECISION,
			filters={"status": bulk_rules.STATUS_QUEUED},
			fields=["name", "item_code", "queued_by", "relax_sales_invoices"],
			order_by="queued_on asc, name asc",
			limit=bulk_rules.BATCH_SIZE,
		)
		if not rows:
			return
		for row in rows:
			# Read again: Stop may have taken it off the list since the batch was read.
			if frappe.db.get_value(DECISION, row.name, "status") != bulk_rules.STATUS_QUEUED:
				continue
			_switch_one(row)
