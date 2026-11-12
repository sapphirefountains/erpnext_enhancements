# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where a project's material is now: the part that reads the database (v1.806.0).

The rule, and why it is the stock ledger's rows that name the job and not the Bin table, is in
``project_stock_rules`` (no Frappe, tested bench-free). This module asks the questions:

* :func:`places_for` -- the Procurement Tracker's "where it is now": for every item of one
  project, the places it is (Project Shelf, the job's site, a named bin, taken to the job,
  returned to the supplier). **Set-based: a fixed handful of queries for the whole project,
  never one per line.**
* :func:`shelf_stock` -- what is on the Project Shelf for one project.
* :func:`shelf_warehouse` -- the Project Shelf, read so that a site without the setting (the
  field is added by another release) simply has no shelf.

**The Project Shelf answer has one door.** :func:`shelf_stock` and :func:`shelf_by_project` are
the adapter the Bin Manager's views call, and they answer from the Project Shelf's own module,
``inventory_enhancements/project_shelf.py`` (``warehouse()`` and ``shelf_stock()``, settled
2026-11-12). That module arrives with another release, so it is imported inside the functions
that use it and its absence is not an error: until it is there, the same question is answered
here from the stock ledger by the same rule.

**Supplier Returns** (``inventory_enhancements/supplier_returns.returned_lines``, another
release again) are read the same way for the tracker's "returned to the supplier": imported when
asked, and no module means no returns on record.

Nothing here writes, and nothing here checks who is asking: each caller has its own gate (the
tracker's Project read, the Bin Manager's role check).

Indentation is tabs (new file).
"""

import frappe

from erpnext_enhancements.inventory_enhancements import project_stock_rules as rules

SETTINGS = "Inventory Scanner Settings"

#: The Single's field naming the Project Shelf. Read with ``Document.get``, which answers
#: ``None`` for a field the site does not have yet.
SHELF_FIELD = "project_shelf_warehouse"
BONEYARD_FIELD = "boneyard_warehouse"

#: The Employee field naming a person's truck or van (``device_management/setup.py``).
VEHICLE_FIELD = "custom_default_vehicle_warehouse"


def _name(value):
	return str(value or "").strip()


def _setting(fieldname):
	"""One Inventory Scanner Settings value, or ``None``. Never raises: a field this site does
	not have, or a Single that was never saved, is an unset value."""
	try:
		return _name(frappe.get_cached_doc(SETTINGS).get(fieldname)) or None
	except Exception:
		return None


def _shelf_module():
	"""The Project Shelf's own module, or ``None`` on a site whose code does not have it yet."""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf
	except ImportError:
		return None
	return project_shelf


def shelf_warehouse():
	"""The Project Shelf warehouse, or ``None`` when the site has none set. The Project Shelf
	module's own answer (an enabled leaf named by the setting) when that module is here."""
	module = _shelf_module()
	if module is not None:
		try:
			return _name(module.warehouse()) or None
		except Exception:
			return None
	return _setting(SHELF_FIELD)


def _returns(project):
	"""The project's Supplier Returns by item (``project_stock_rules.return_summary``), or
	``{}``: no Supplier Return module on this site yet, or nothing returned."""
	try:
		from erpnext_enhancements.inventory_enhancements import supplier_returns
	except ImportError:
		return {}
	try:
		return rules.return_summary(supplier_returns.returned_lines(project=project))
	except Exception:
		# The function is written never to raise. If it ever does, the tracker still has the
		# ledger's own return rows.
		return {}


def boneyard_warehouse():
	return _setting(BONEYARD_FIELD)


def vehicles():
	"""Every warehouse that is somebody's truck or van, as a set of names. Never raises: the
	Employee field is another module's, and a site without it has no vehicles."""
	try:
		if not frappe.db.has_column("Employee", VEHICLE_FIELD):
			return set()
		rows = frappe.get_all("Employee", filters=[[VEHICLE_FIELD, "is", "set"]], fields=[VEHICLE_FIELD])
	except Exception:
		return set()
	return {row.get(VEHICLE_FIELD) for row in rows if row.get(VEHICLE_FIELD)}


def _sites(project):
	"""``(own, every)``: the project's own site warehouses, and every job site."""
	# Imported when asked: job_sites imports frappe.utils-free code only, but the tracker's
	# package is loaded by suites whose frappe stub is small.
	from erpnext_enhancements.inventory_enhancements import job_sites

	try:
		site_map = job_sites.site_map()
		every = set(job_sites.site_warehouses())
	except Exception:
		return set(), set()
	own = {warehouse for warehouse, job in site_map.items() if job == project}
	return own, every | set(site_map)


def ledger_rows(project):
	"""The project's stock ledger, added up by item, warehouse and what the voucher did.

	One query. A canceled voucher's rows are out (``is_cancelled``), and the join to the voucher
	is only there to tell a take from a transfer and a return from a receipt.
	"""
	rows = frappe.db.sql(
		"""
		select sle.item_code, sle.warehouse, sle.voucher_type,
			coalesce(se.purpose, '') as purpose,
			coalesce(pr.is_return, 0) as is_return,
			sum(sle.actual_qty) as qty
		from `tabStock Ledger Entry` sle
		left join `tabStock Entry` se
			on sle.voucher_type = 'Stock Entry' and se.name = sle.voucher_no
		left join `tabPurchase Receipt` pr
			on sle.voucher_type = 'Purchase Receipt' and pr.name = sle.voucher_no
		where sle.project = %(project)s and sle.is_cancelled = 0
		group by sle.item_code, sle.warehouse, sle.voucher_type, se.purpose, pr.is_return
		""",
		{"project": project},
		as_dict=True,
	)
	return [
		{
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"kind": rules.ledger_kind(row.voucher_type, row.purpose, row.is_return, row.qty),
			"qty": float(row.qty or 0),
		}
		for row in rows
		if row.item_code
	]


def _on_hand(warehouses):
	"""``{(item_code, warehouse): qty}`` for everything on hand at ``warehouses``. One query."""
	warehouses = [w for w in set(warehouses or ()) if w]
	if not warehouses:
		return {}
	rows = frappe.get_all(
		"Bin",
		filters={"warehouse": ["in", warehouses], "actual_qty": [">", 0]},
		fields=["item_code", "warehouse", "actual_qty"],
	)
	return {(row.item_code, row.warehouse): float(row.actual_qty or 0) for row in rows}


def _warehouse_names(warehouses):
	warehouses = [w for w in set(warehouses or ()) if w]
	if not warehouses:
		return {}
	rows = frappe.get_all("Warehouse", filters={"name": ["in", warehouses]}, fields=["name", "warehouse_name"])
	return {row.name: row.warehouse_name or row.name for row in rows}


def _items(item_codes):
	item_codes = [c for c in set(item_codes or ()) if c]
	if not item_codes:
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", "item_name", "stock_uom", "is_stock_item"],
	)
	return {row.name: row for row in rows}


def _truthy(value):
	try:
		return bool(int(value or 0))
	except (TypeError, ValueError):
		return False


def places_for(project, item_codes=None):
	"""Where each item of ``project`` is now.

	Returns ``{"items": {item_code: {...}}, "shelf": name or None, "sites": [names]}``. Each
	item is ``{"is_stock_item", "stock_uom", "places": [place, ...]}`` with places as
	``project_stock_rules.item_places`` builds them. An item in ``item_codes`` that ERPNext does
	not know is absent; one the ledger names and ``item_codes`` does not (a part taken for the job
	that was never on a purchasing document) is included.

	The queries, whatever the number of lines: the ledger (1), the sites (3, all small), the Bin
	rows at the warehouses involved (1), their names (1), the items (1), and the job's Supplier
	Returns (1, once that module is installed).
	"""
	project = _name(project)
	if not project:
		return {"items": {}, "shelf": None, "sites": []}
	shelf = shelf_warehouse()
	boneyard = boneyard_warehouse()
	own, every = _sites(project)
	ledger = ledger_rows(project)

	warehouses = {row["warehouse"] for row in ledger if row["warehouse"]} | own
	on_hand = _on_hand(warehouses)
	names = _warehouse_names(warehouses)

	def kind_of(warehouse):
		return rules.warehouse_kind(
			warehouse, shelf=shelf, boneyard=boneyard, own_sites=own, all_sites=every
		)

	by_item = {}
	for row in ledger:
		by_item.setdefault(row["item_code"], []).append(row)
	returns = _returns(project)
	codes = (
		set(item_codes or ())
		| set(by_item)
		| set(returns)
		| {code for code, _warehouse in on_hand if _warehouse in own}
	)
	items = _items(codes)
	out = {}
	for code in codes:
		item = items.get(code)
		if not item:
			continue
		stock = _truthy(item.is_stock_item)
		here = {warehouse: qty for (item_code, warehouse), qty in on_hand.items() if item_code == code}
		out[code] = {
			"is_stock_item": stock,
			"item_name": item.item_name or code,
			"stock_uom": item.stock_uom,
			"places": rules.item_places(
				by_item.get(code),
				here,
				own_sites=own,
				kind_of=kind_of,
				names=names,
				returned=(returns.get(code) or {}).get("gone"),
			)
			if stock
			else [],
			# Each Supplier Return that names the item for this job, a non-stock item's included:
			# what was sent back is known even where the item's place is not.
			"supplier_returns": (returns.get(code) or {}).get("returns") or [],
		}
	return {"items": out, "shelf": shelf, "sites": sorted(own)}


def _own_shelf_rows(project=None):
	"""The fallback behind :func:`shelf_stock` and :func:`shelf_by_project` for a site whose code
	has no Project Shelf module yet: every job's ledger balance at the shelf (or one job's), never
	more than the shelf holds. Rows as ``project_stock_rules.capped_shelf_rows`` shapes them."""
	shelf = _setting(SHELF_FIELD)
	if not shelf:
		return []
	values = {"shelf": shelf}
	only = ""
	if project:
		only = " and sle.project = %(project)s"
		values["project"] = project
	rows = frappe.db.sql(
		f"""
		select sle.project, sle.item_code, sum(sle.actual_qty) as qty
		from `tabStock Ledger Entry` sle
		where sle.warehouse = %(shelf)s and sle.is_cancelled = 0
			and coalesce(sle.project, '') <> ''{only}
		group by sle.project, sle.item_code
		having sum(sle.actual_qty) > 0
		""",
		values,
		as_dict=True,
	)
	if not rows:
		return []
	on_hand = {code: qty for (code, _warehouse), qty in _on_hand([shelf]).items()}
	items = _items({row.item_code for row in rows})
	# Every item gets its on-hand as the ceiling: the same cut capped_shelf_rows makes from the
	# Project Shelf module's "untagged" list.
	ceilings = [{"item_code": row.item_code, "on_hand": on_hand.get(row.item_code, 0.0)} for row in rows]
	shaped = [
		{
			"project": row.project,
			"item_code": row.item_code,
			"item_name": (items.get(row.item_code) or {}).get("item_name"),
			"stock_uom": (items.get(row.item_code) or {}).get("stock_uom"),
			"qty": float(row.qty or 0),
		}
		for row in rows
	]
	return rules.capped_shelf_rows(shaped, ceilings)


def _shelf_rows(project=None):
	"""Each job's share of the Project Shelf, from the Project Shelf module when it is here.

	THE ADAPTER. ``project_shelf.shelf_stock(project=...)`` reports each job's ledger balance at
	the shelf and, as ``untagged``, the items whose on-hand and tagged totals differ.
	``project_stock_rules.capped_shelf_rows`` cuts a row to what the shelf holds where they do.
	"""
	module = _shelf_module()
	if module is None:
		return _own_shelf_rows(project)
	try:
		answer = module.shelf_stock(project=project or None) or {}
	except Exception:
		return []
	return rules.capped_shelf_rows(answer.get("rows"), answer.get("untagged"))


def shelf_stock(project):
	"""What is on the Project Shelf for ``project``: ``[{"item_code", "item_name", "stock_uom",
	"qty", "recorded"}]``, by item name. Empty when the site has no Project Shelf.

	``qty`` is what can be handed over; ``recorded`` is the job's ledger balance, above ``qty``
	when the shelf holds less than the records say is the job's (:func:`_shelf_rows`).
	"""
	project = _name(project)
	if not project:
		return []
	rows = [
		{key: row[key] for key in ("item_code", "item_name", "stock_uom", "qty", "recorded")}
		for row in _shelf_rows(project)
		if row.get("project") in (project, None)
	]
	rows.sort(key=lambda r: rules.natural_key(r["item_name"] or ""))
	return rows


def shelf_by_project():
	"""``{project: {item_code: qty}}``: every job's share of the Project Shelf, for the Bin
	Manager's list of jobs and its Problems view. The same door as :func:`shelf_stock`."""
	out = {}
	for row in _shelf_rows():
		if row.get("project"):
			out.setdefault(row["project"], {})[row["item_code"]] = row["qty"]
	return out
