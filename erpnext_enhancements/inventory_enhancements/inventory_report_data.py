# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the four inventory reports read in common (v1.805.0).

Location Contents, Where Is This Item, Project Materials and Job Site Contents all need the same
few facts: which warehouse is the Project Shelf, which are job sites and whose, which is the
Boneyard, which are vehicles, and whose stock is on the shelf. They are read here once, so the
four reports cannot give four answers. What the rows mean is decided in
``inventory_report_rules`` (no Frappe, tested bench-free).

**The Project Shelf, before and after it is set up.** Nik's decision of 2026-11-12 is one shared
shelf for stock bought for jobs: one warehouse, named in
``Inventory Scanner Settings.project_shelf_warehouse``. That field is added by a separate change.
:func:`project_shelf` reads it from the settings document with ``.get``, so on a site where the
field does not exist yet (or is blank, or names something that is not a single enabled location)
it answers ``None``, nothing is the Project Shelf, and each report says "No Project Shelf is set"
in the note over its table. Nothing here raises for a missing field.

**Whose stock is on the shelf: one adapter.** ERPNext keeps on-hand by item and warehouse
(``tabBin``) and has no job there, so the job comes from the stock ledger.
:func:`shelf_stock_by_project` is the only place the reports ask that question. The Project Shelf
change ships its own module, ``inventory_enhancements/project_shelf.py``. When that module is
installed the adapter asks it (``project_shelf.warehouse`` for which warehouse is the shelf,
``project_shelf.shelf_stock`` for each job's net quantity), so the reports and the shelf's own
screens read one answer. Before it is installed the adapter reads the same ledger rows itself
(:func:`_own_shelf_claims`). Both paths do the same arithmetic, so the swap changes no number.

**Returns to a supplier: one seam.** :func:`supplier_returns_by_item` is where the Supplier
Return record will be read once it exists. Until then it answers nothing, and Project Materials
shows the returns ERPNext already knows: its own return receipts.

**Permissions.** These reads are plain SQL and ``get_all``: each report is gated by its roles and
by report permission on its reference doctype, as ``api.stock_scan._items_at`` is gated by the
Stock Scan roles. They do not apply User Permissions. That matches the Bin Manager and Stock Scan,
which list a location's stock to anyone the page admits.

Indentation is tabs (new file).
"""

import frappe

from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules
from erpnext_enhancements.inventory_enhancements import job_sites

SETTINGS = "Inventory Scanner Settings"
SHELF_FIELD = "project_shelf_warehouse"
BONEYARD_FIELD = "boneyard_warehouse"
VEHICLE_FIELD = "custom_default_vehicle_warehouse"

#: How many names go into one ``in (...)`` list.
CHUNK = 1000

NO_SHELF_NOTE = (
	"No Project Shelf is set (Inventory Scanner Settings, Project Shelf Warehouse), "
	"so nothing is shown as on the Project Shelf."
)


def chunks(values, size=CHUNK):
	"""``values`` de-duplicated, sorted and cut into lists of at most ``size``."""
	names = sorted({v for v in values or () if v})
	return [names[i : i + size] for i in range(0, len(names), size)]


def _setting(field):
	"""One field of Inventory Scanner Settings, or ``None``. Never raises: the field may not
	exist on this site yet, and a document's ``.get`` answers ``None`` for a field it does not
	have where ``frappe.db.get_single_value`` would throw."""
	try:
		value = frappe.get_cached_doc(SETTINGS).get(field)
	except Exception:
		return None
	value = str(value or "").strip()
	return value or None


def _single_location(name):
	"""``name`` when it is one enabled location (not a group), else ``None``."""
	if not name:
		return None
	row = frappe.db.get_value("Warehouse", name, ["name", "is_group", "disabled"], as_dict=True)
	if not row or rules._on(row.get("is_group")) or rules._on(row.get("disabled")):
		return None
	return row.get("name")


def project_shelf():
	"""The Project Shelf warehouse, or ``None`` when none is set (see the module docstring).
	The Project Shelf change's own answer when its module is installed, else this module's
	reading of the setting. Never raises."""
	module = _shelf_module()
	if module is not None:
		try:
			return module.warehouse() or None
		except Exception:
			pass
	return _single_location(_setting(SHELF_FIELD))


def boneyard():
	"""The Boneyard warehouse (``bin_assignments.send_to_boneyard``'s target), or ``None``."""
	return _single_location(_setting(BONEYARD_FIELD))


def warehouse_rows():
	"""Every warehouse, with what the reports need to walk the tree and name a place."""
	return frappe.get_all(
		"Warehouse",
		fields=[
			"name",
			"warehouse_name",
			"parent_warehouse",
			"is_group",
			"disabled",
			"warehouse_type",
			"company",
		],
	)


def vehicle_warehouses(warehouses):
	"""The warehouses that are a vehicle's stock: each one an active employee has as their
	vehicle (``Employee.custom_default_vehicle_warehouse``), and everything under it. A set of
	names; empty before that field exists."""
	try:
		if not frappe.db.has_column("Employee", VEHICLE_FIELD):
			return set()
	except Exception:
		return set()
	rows = frappe.get_all("Employee", filters=[[VEHICLE_FIELD, "is", "set"]], fields=[VEHICLE_FIELD])
	out = set()
	for row in rows:
		name = row.get(VEHICLE_FIELD)
		if not name:
			continue
		out.add(name)
		out.update(rules.leaves_under(warehouses, name))
	return out


def places():
	"""The facts every report shares, read once per run."""
	warehouses = warehouse_rows()
	site_projects = job_sites.site_map()
	return frappe._dict(
		warehouses=warehouses,
		names={w.get("name"): (w.get("warehouse_name") or w.get("name")) for w in warehouses},
		parents={w.get("name"): w.get("parent_warehouse") for w in warehouses},
		shelf=project_shelf(),
		boneyard=boneyard(),
		sites=job_sites.site_warehouses(),
		site_projects=site_projects,
		vehicles=vehicle_warehouses(warehouses),
		sites_ready=job_sites.ready(),
	)


def kind_of(warehouse, where):
	"""``rules.place_kind`` for one warehouse, from :func:`places`'s answer."""
	return rules.place_kind(
		warehouse,
		shelf=where.shelf,
		sites=where.sites,
		boneyard=where.boneyard,
		vehicles=where.vehicles,
	)


def item_info(item_codes):
	"""``{item_code: {item_name, item_group, stock_uom, is_stock_item, disabled}}``."""
	out = {}
	for part in chunks(item_codes):
		rows = frappe.get_all(
			"Item",
			filters={"name": ["in", part]},
			fields=["name", "item_name", "item_group", "stock_uom", "is_stock_item", "disabled"],
		)
		for row in rows:
			out[row.get("name")] = row
	return out


def project_info(projects):
	"""``{project: {project_name, customer, project_type, status}}``."""
	out = {}
	for part in chunks(projects):
		rows = frappe.get_all(
			"Project",
			filters={"name": ["in", part]},
			fields=["name", "project_name", "customer", "project_type", "status"],
		)
		for row in rows:
			out[row.get("name")] = row
	return out


def item_groups_under(item_group):
	"""The Item Group and every group below it, as a list of names."""
	if not item_group:
		return []
	return [item_group, *(frappe.db.get_descendants("Item Group", item_group) or [])]


def assignments(item_codes):
	"""Where each item is assigned, anywhere: ``(rules_by_item, defaults_by_item)``, each
	``{item_code: {warehouse, ...}}``. Enabled Putaway Rules, and Item Default warehouses."""
	rules_by_item, defaults_by_item = {}, {}
	for part in chunks(item_codes):
		for row in frappe.get_all(
			"Putaway Rule",
			filters={"item_code": ["in", part], "disable": 0},
			fields=["item_code", "warehouse"],
		):
			rules_by_item.setdefault(row.get("item_code"), set()).add(row.get("warehouse"))
		for row in frappe.get_all(
			"Item Default",
			filters={"parent": ["in", part], "parenttype": "Item", "default_warehouse": ["is", "set"]},
			fields=["parent", "default_warehouse"],
		):
			defaults_by_item.setdefault(row.get("parent"), set()).add(row.get("default_warehouse"))
	return rules_by_item, defaults_by_item


def site_bins(where, projects=None, item_codes=None):
	"""What is at the job sites: ``tabBin`` rows with anything on hand or on order, each with the
	job its site belongs to (``""`` for a place under the Job Sites group that has no job of its
	own). ``projects`` and ``item_codes`` narrow it; ``None`` means all.

	Returns ``[{project, warehouse, item_code, qty, on_order, value}, ...]``.
	"""
	sites = sorted(where.sites)
	if projects is not None:
		wanted = set(projects)
		sites = [name for name in sites if where.site_projects.get(name) in wanted]
	if not sites:
		return []
	codes = None if item_codes is None else sorted({c for c in item_codes if c})
	if codes is not None and not codes:
		return []
	item_clause = "and item_code in %(items)s" if codes else ""
	out = []
	for part in chunks(sites):
		params = {"sites": tuple(part)}
		if codes:
			params["items"] = tuple(codes)
		for row in frappe.db.sql(
			f"""
			select item_code, warehouse, actual_qty, ordered_qty, stock_value
			from `tabBin`
			where warehouse in %(sites)s and (actual_qty <> 0 or ordered_qty > 0) {item_clause}
			""",
			params,
			as_dict=True,
		):
			out.append(
				{
					"project": where.site_projects.get(row.get("warehouse")) or "",
					"warehouse": row.get("warehouse"),
					"item_code": row.get("item_code"),
					"qty": rules._num(row.get("actual_qty")),
					"on_order": rules._num(row.get("ordered_qty")),
					"value": rules._num(row.get("stock_value")),
				}
			)
	return out


# --- The Project Shelf, by job --------------------------------------------------------------


def _shelf_module():
	"""The Project Shelf change's own module (``inventory_enhancements/project_shelf.py``), or
	``None`` on a site where it is not installed yet."""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf as module
	except ImportError:
		return None
	return module


def shelf_stock_by_project(shelf, item_codes=None, projects=None):
	"""THE ADAPTER: what is on the Project Shelf, split by the job it is for.

	Returns ``[{item_code, project, qty, value}, ...]``. ``project`` is ``""`` for the row that
	holds what no job's ledger rows account for. ``item_codes`` and ``projects`` narrow the
	answer; ``None`` means all. Every report asks this function and nothing else.

	**Two facts, two sources.** How many there are is ``tabBin`` at the shelf. Whose they are is
	the stock ledger: the net quantity each job's rows put on the shelf (:func:`_shelf_claims`).
	``rules.shelf_shares`` puts the two side by side without changing either: each job's share is
	its ledger net, and the no-job row is what is on hand less every job's share, so the rows
	always add up to what is on the shelf. That last row is negative when a count left fewer
	than the jobs' rows claim; the reports say so rather than scale the jobs down, so a job's
	figure here is the same figure the Project Shelf's own screens show.

	Value is each share's quantity at the shelf's valuation rate for the item.
	"""
	if not shelf:
		return []
	codes = None if item_codes is None else sorted({c for c in item_codes if c})
	if codes is not None and not codes:
		return []
	bins = _shelf_bins(shelf, codes)
	claims = _shelf_claims(shelf, codes)
	wanted = None if projects is None else set(projects)
	out = []
	for code in sorted(set(bins) | set(claims)):
		on_hand, rate = bins.get(code, (0.0, 0.0))
		for project, qty in rules.shelf_shares(on_hand, claims.get(code)):
			if wanted is not None and project not in wanted:
				continue
			out.append({"item_code": code, "project": project, "qty": qty, "value": qty * rate})
	return out


def _shelf_bins(shelf, codes):
	"""``{item_code: (on hand, valuation rate)}`` at the shelf."""
	item_clause = "and item_code in %(items)s" if codes else ""
	params = {"shelf": shelf}
	if codes:
		params["items"] = tuple(codes)
	rows = frappe.db.sql(
		f"""
		select item_code, actual_qty, valuation_rate
		from `tabBin`
		where warehouse = %(shelf)s {item_clause}
		""",
		params,
		as_dict=True,
	)
	return {
		row.get("item_code"): (rules._num(row.get("actual_qty")), rules._num(row.get("valuation_rate")))
		for row in rows
	}


def _shelf_claims(shelf, codes):
	"""``{item_code: {project: net ledger quantity}}`` at the shelf.

	Asked of the Project Shelf change's ``project_shelf.shelf_stock`` when that module is
	installed, so these reports and its screens read one answer. Before it is installed, or if
	its answer is not for this shelf or not in the agreed shape, this module's own reading of the
	same ledger rows is used (:func:`_own_shelf_claims`).
	"""
	module = _shelf_module()
	if module is not None:
		claims = _claims_from_module(module, shelf, codes)
		if claims is not None:
			return claims
	return _own_shelf_claims(shelf, codes)


def _claims_from_module(module, shelf, codes):
	"""``project_shelf.shelf_stock``'s rows as claims, or ``None`` when they cannot be used.

	The agreed shape (the Project Shelf session's note of 2026-11-12):
	``shelf_stock(project=None, item_code=None)`` returns ``{"warehouse": ..., "rows": [{"project",
	"item_code", "qty", ...}]}``, ``qty`` being what came onto the shelf under the job less what
	left under it. One item is asked for by name; several are filtered here.
	"""
	try:
		answer = module.shelf_stock(item_code=codes[0] if codes and len(codes) == 1 else None)
		if not isinstance(answer, dict) or answer.get("warehouse") != shelf:
			return None
		wanted = None if codes is None else set(codes)
		claims = {}
		for row in answer.get("rows") or ():
			code, project = row.get("item_code"), row.get("project")
			if not code or not project or (wanted is not None and code not in wanted):
				continue
			by_project = claims.setdefault(code, {})
			by_project[project] = by_project.get(project, 0.0) + rules._num(row.get("qty"))
		return claims
	except Exception:
		return None


def _own_shelf_claims(shelf, codes):
	"""This module's own reading: the net of ``actual_qty`` over the live ledger rows at the
	shelf, by the Project each row carries. One grouped query on the one warehouse, so it stays
	small however long the ledger gets. Rows with no Project are not a claim; what they leave
	shows up as the gap between the claims and ``tabBin``."""
	item_clause = "and item_code in %(items)s" if codes else ""
	params = {"shelf": shelf}
	if codes:
		params["items"] = tuple(codes)
	rows = frappe.db.sql(
		f"""
		select item_code, project, sum(actual_qty) as qty
		from `tabStock Ledger Entry`
		where warehouse = %(shelf)s and is_cancelled = 0 and coalesce(project, '') <> '' {item_clause}
		group by item_code, project
		""",
		params,
		as_dict=True,
	)
	claims = {}
	for row in rows:
		claims.setdefault(row.get("item_code"), {})[row.get("project")] = rules._num(row.get("qty"))
	return claims


# --- Returns to a supplier ------------------------------------------------------------------


def supplier_returns_by_item(project):
	"""THE SEAM for the Supplier Return record: ``{item_code: qty returned}`` for one project.

	The Supplier Return doctype is built by a separate change and does not exist yet, so this
	answers nothing. Project Materials already counts ERPNext's own return receipts (a Purchase
	Receipt with *Is Return*), so when this is filled in it must add only what those receipts do
	not show. If a Supplier Return posts its stock through a return receipt, leave this empty:
	the column is already right, and reading both would count each return twice.
	"""
	return {}
