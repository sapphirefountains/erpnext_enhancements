# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Shelf: the one warehouse where parts bought for a job wait (v1.809.0).

The owner's decision of 2026-11-12, and every reason, is in ``project_shelf_rules`` (no
Frappe, tested bench-free). This module is the part that reads the database.

* **What makes a warehouse the Project Shelf is one setting**,
  ``Inventory Scanner Settings.project_shelf_warehouse``. Not its name and not its place in
  the tree. Blank means there is no shelf, and every function here answers "nothing", so
  every caller behaves as it did before this release.
* **Its stock is not shop stock** (:func:`not_shop_warehouses`): the same exclusion a job
  site has, and the one set every total reads, so the two cannot disagree.
* **It is never an item's bin** (:func:`refuse_as_bin`): parts wait on it for a job and
  leave. The boneyard and a job site have the same rule for the same reason.
* **Whose stock is on it is read from the Stock Ledger** (:func:`shelf_stock`): the project
  on each row at the shelf. ``project_shelf_rules`` says how that can drift and how the
  drift is reported instead of hidden.

**Other modules call three functions and must not reach past them:** :func:`warehouse`,
:func:`not_shop_warehouses` and :func:`shelf_stock`. Their signatures and return shapes are
fixed; the reports and the Procurement Tracker read them.

Nothing here raises to answer a question: these run inside Stock Scan's boot, the Reorder
Board and the Purchase Order's save, and one exception would take all three down.

Indentation is tabs (new file).
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import job_sites
from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules

SETTING = "project_shelf_warehouse"
FALLBACK_SETTING = "overhead_receiving_warehouse"


def _settings():
	# Imported when asked: the Reorder Board imports this module, and its bench-free suite
	# runs on a small frappe stub that cannot load the settings controller at import time.
	from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
		get_settings,
	)

	return get_settings()


def _name(value):
	return str(value or "").strip()


def _on(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def _leaf(setting):
	"""The enabled leaf warehouse a setting names, as a row, or ``None``. Never raises."""
	try:
		name = _name(_settings().get(setting))
		if not name:
			return None
		row = frappe.db.get_value(
			"Warehouse", name, ["name", "warehouse_name", "company", "is_group", "disabled"], as_dict=True
		)
		if not row or _on(row.is_group) or _on(row.disabled):
			return None
		return row
	except Exception:
		return None


def row():
	"""The Project Shelf as a warehouse row (``name``, ``warehouse_name``, ``company``), or
	``None``: the setting is blank, or names something that is not an enabled leaf."""
	return _leaf(SETTING)


def warehouse():
	"""The Project Shelf's warehouse name, or ``None`` when there is no shelf. Never raises."""
	shelf = row()
	return shelf.name if shelf else None


def label():
	"""What the shelf is called on a screen: its ``warehouse_name``, or ``None``."""
	shelf = row()
	return (shelf.warehouse_name or shelf.name) if shelf else None


def is_shelf(name):
	"""Whether ``name`` is the Project Shelf."""
	name = _name(name)
	return bool(name) and name == warehouse()


def fallback_warehouse():
	"""Where an Overhead purchase lands when its item has no bin yet
	(``Inventory Scanner Settings.overhead_receiving_warehouse``), or ``None``."""
	found = _leaf(FALLBACK_SETTING)
	return found.name if found else None


def not_shop_warehouses():
	"""Every warehouse whose stock is not stock the shop has, as a set of names: each job
	site (``job_sites.site_warehouses``) and the Project Shelf. Never raises."""
	try:
		sites = job_sites.site_warehouses()
	except Exception:
		sites = set()
	return rules.not_shop(sites, warehouse())


def former_name():
	"""The docname the shelf had before it was renamed, when nothing else has that name now.
	A label printed for ``Stores`` still opens the shelf through this
	(``api.stock_scan._warehouse``). ``None`` when there is nothing to honour."""
	try:
		shelf = warehouse()
		old = rules.former_docname(shelf) if shelf else None
		if not old or frappe.db.exists("Warehouse", old):
			return None
		return old
	except Exception:
		return None


def refuse_as_bin(location):
	"""The Project Shelf is never an item's bin: nothing is assigned to it and it is nobody's
	home. Stock lists there because it is on hand, and drops off when it is gone."""
	if location and is_shelf(location.name):
		frappe.throw(
			_(
				"{0} is the Project Shelf. Parts wait there for their job; nothing is kept there as its bin."
			).format(location.get("warehouse_name") or location.name)
		)


# ---------------------------------------------------------------------------
# What is on the shelf, by job
# ---------------------------------------------------------------------------

_EMPTY = {
	"warehouse": None,
	"rows": [],
	"projects": [],
	"untagged": [],
	"untagged_movements": [],
	"exact": True,
}


def _empty(shelf=None):
	out = {key: (list(value) if isinstance(value, list) else value) for key, value in _EMPTY.items()}
	out["warehouse"] = shelf
	return out


def _lookup(doctype, fields, names):
	"""``{name: row}`` for the given record names, in one query."""
	names = sorted({name for name in names if name})
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", *fields])
	return {found.name: found for found in rows}


def shelf_stock(project=None, item_code=None):
	"""What is on the Project Shelf, by job. **The one function that answers both "what is on
	the shelf for project X" and "which projects have stock on the shelf".**

	Read from the Stock Ledger: the live rows at the shelf warehouse, grouped by the project
	on each row (``project_shelf_rules.shelf_summary`` has the arithmetic and its limits).
	``project`` and ``item_code`` are optional filters. Returns::

		{
			"warehouse": the shelf's warehouse name, or None when there is no shelf,
			"rows": [{project, project_name, item_code, item_name, stock_uom,
			          qty, tagged, over_tagged, qty_in, qty_out, last_movement}],
			"projects": [{project, project_name, status, project_type, items, qty}],
			"untagged": [{item_code, item_name, stock_uom, on_hand, tagged, qty, over_tagged}],
			"untagged_movements": [{posting_date, voucher_type, voucher_no, item_code, qty, kind}],
			"exact": bool,
		}

	**Limits.** ERPNext keeps on-hand by item and warehouse, with no project. These numbers
	are derived from the project on each ledger row, so a movement with no project (a count, a
	transfer made by hand in the Desk) makes them drift. The drift is in ``untagged`` and
	``untagged_movements`` and is never hidden. ``rows[].qty`` is a job's tag balance **capped
	by what is physically on the shelf**, so it is never negative and an item's rows never add
	up to more than its on-hand; the uncapped balance is ``rows[].tagged`` (it can be negative)
	and what the cap took off is ``over_tagged``. ``exact`` is true only when the tags account
	for every unit. Show it, or the ``untagged`` list, beside any total.

	Never raises: a failure is logged and the answer is the empty shape with ``exact`` false.
	Reads the ledger without permission checks, like a report; the caller decides who may see
	the answer.
	"""
	shelf = warehouse()
	if not shelf:
		return _empty()
	try:
		params = {"warehouse": shelf}
		condition = ""
		if _name(item_code):
			condition = " and item_code = %(item_code)s"
			params["item_code"] = _name(item_code)
		ledger = frappe.db.sql(
			f"""
			select item_code, project, actual_qty, qty_after_transaction,
				voucher_type, voucher_no, posting_date
			from `tabStock Ledger Entry`
			where warehouse = %(warehouse)s and is_cancelled = 0{condition}
			order by posting_datetime asc, creation asc
			""",
			params,
			as_dict=True,
		)
		on_hand = {
			found.item_code: found.actual_qty
			for found in frappe.get_all(
				"Bin",
				filters={"warehouse": shelf, **({"item_code": _name(item_code)} if _name(item_code) else {})},
				fields=["item_code", "actual_qty"],
			)
		}
		summary = rules.shelf_summary(ledger, on_hand, project=project, item_code=item_code)
		items = _lookup(
			"Item",
			["item_name", "stock_uom"],
			[r["item_code"] for r in summary["rows"]] + [r["item_code"] for r in summary["untagged"]],
		)
		jobs = _lookup(
			"Project", ["project_name", "status", "project_type"], [r["project"] for r in summary["rows"]]
		)
		for found in [*summary["rows"], *summary["untagged"]]:
			item = items.get(found["item_code"]) or {}
			found["item_name"] = item.get("item_name") or found["item_code"]
			found["stock_uom"] = item.get("stock_uom")
		for found in summary["rows"]:
			found["project_name"] = (jobs.get(found["project"]) or {}).get("project_name") or found["project"]
		projects = rules.projects_summary(summary["rows"])
		for found in projects:
			job = jobs.get(found["project"]) or {}
			found["project_name"] = job.get("project_name") or found["project"]
			found["status"] = job.get("status")
			found["project_type"] = job.get("project_type")
		return {
			"warehouse": shelf,
			"rows": summary["rows"],
			"projects": projects,
			"untagged": summary["untagged"],
			"untagged_movements": summary["untagged_movements"],
			"exact": summary["exact"],
		}
	except Exception:
		try:
			frappe.log_error(title="Project Shelf: could not read what is on the shelf")
		except Exception:
			pass
		out = _empty(shelf)
		out["exact"] = False
		return out


def holding(item_code, stock=None):
	"""``{"jobs": [{"project", "project_name", "qty"}], "exact": bool}`` for one item on the
	shelf: the jobs that hold it (:func:`holders`), and whether the tags account for every
	unit of it (``project_shelf_rules.item_exact``). ``exact`` false means some of what is
	there has no job on record, or more is tagged than is there: the jobs are shown, and none
	is picked for the person."""
	item_code = _name(item_code)
	if not item_code:
		return {"jobs": [], "exact": True}
	stock = stock if stock is not None else shelf_stock(item_code=item_code)
	return {"jobs": holders(item_code, stock), "exact": rules.item_exact(stock, item_code)}


def holders(item_code, stock=None):
	"""The jobs that hold one item on the shelf, most first:
	``[{"project", "project_name", "qty"}]``. ``stock`` is a :func:`shelf_stock` answer the
	caller already has; without it the item's own rows are read."""
	item_code = _name(item_code)
	if not item_code:
		return []
	stock = stock if stock is not None else shelf_stock(item_code=item_code)
	names = {found["project"]: found.get("project_name") for found in stock.get("rows") or ()}
	return [
		{"project": name, "project_name": names.get(name) or name, "qty": qty}
		for name, qty in rules.holders(stock.get("rows") or (), item_code)
	]
