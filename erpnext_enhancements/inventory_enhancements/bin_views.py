# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Bin Manager's views beyond one bin (v1.806.1): what kind of place each location is, what
a project has on the Project Shelf and at its job site, and where the records disagree with the
shelves.

Nik, 2026-11-12: "In the Bin Manager page, having more filters and different views would be
good. So for example I want to be able to see by Project which items are at the Job Site, etc."

Everything here is a **read**. The page's writes are the ones it already had
(``bin_assignments.move_to`` and friends, and ``api.stock_scan.job_site`` to open a job's site),
so every move made from the new views is logged and can be undone exactly as before.

* :func:`decorate` -- called by ``bin_assignments.manager_boot``: each bin's ``kind`` (shop bin,
  Project Shelf, job site, boneyard, vehicle), a job site's project with its name and customer,
  and the three numbers behind the picker's quick filters and the Job sites view (assigned items
  with nothing on hand, stock with no assignment, and the value on hand).
* :func:`item_locations` -- the picker's *by item* filter: every location that holds an item or
  has it assigned.
* :func:`project_index` and :func:`project_view` -- the *By project* view: which jobs have
  material somewhere, and one job's Project Shelf and job site side by side.
* :func:`problems` -- the *Problems* view: assigned but empty, stock with no assignment, and
  stock still held for a finished job (on the Project Shelf, or at its site).

**Who may ask.** Every endpoint is behind ``api.stock_scan._check_access`` (the Stock Scan
roles), like every endpoint the page already calls. A project's *customer* is added only for a
caller who can read Projects; a job's name was already shown to these roles on a site's screen.
:func:`project_view` takes a project by name, so it is stricter: a project's status, type and
customer go only to a caller who may read *that* Project, and anyone else gets what the
warehouses already show (the id, and the name on the job's site).
Stock *value* is added only for a caller who can read the Stock Ledger, the permission ERPNext's
own stock reports ask for.

**The Project Shelf's numbers come through one door**, ``project_stock.shelf_stock`` and
``project_stock.shelf_by_project``, which answer from the Project Shelf's own module
(``project_shelf.shelf_stock``) once it is installed. There is no shelf query in this file. A
job's row is what the shelf can hand over; where the records say the job has more than the shelf
holds, the row carries both figures (``qty`` and ``recorded``) and the page says so.

**Never the reason the page fails.** :func:`decorate` answers with what it could read: a site
without the Project Shelf setting, without the job-site field or without the Employee vehicle
field simply has none of that kind, and each part is read on its own so one that fails does not
take the others with it.

Indentation is tabs (new file).
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from erpnext_enhancements.api import stock_scan as scan
from erpnext_enhancements.inventory_enhancements import job_site_rules as site_rules
from erpnext_enhancements.inventory_enhancements import job_sites, project_stock
from erpnext_enhancements.inventory_enhancements import project_stock_rules as place_rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

#: Rows one list of the Problems view carries. Past this the list says it was cut.
PROBLEM_LIMIT = 300

#: Kinds of place where "stock with no assignment" is how the place works, not a problem:
#: nothing is ever assigned to a job site, the boneyard or the shared Project Shelf.
_UNASSIGNED_KINDS = frozenset({place_rules.KIND_BIN, place_rules.KIND_VEHICLE})


def _quietly(fn, default):
	"""``fn()``, or ``default`` when it raises. For the optional parts of :func:`decorate`."""
	try:
		return fn()
	except Exception:
		return default


def can_see_value():
	"""Whether the caller may see what stock is worth: read on the Stock Ledger, which is what
	ERPNext's own Stock Balance asks for."""
	return bool(_quietly(lambda: frappe.has_permission("Stock Ledger Entry", "read"), False))


def shelf_job_moves():
	"""Whether a move onto or off the Project Shelf names its job on this site: the Project
	Shelf's module is installed and ``bin_assignments.move_to`` takes a ``project``.

	Asked of the code that is actually installed, because the two arrive with another release
	(v1.804.0) and this page must work on either side of it. False means the page moves stock
	exactly as it did before: no job is asked for and none is sent. Never raises.
	"""
	try:
		import inspect

		from erpnext_enhancements.inventory_enhancements import bin_assignments, project_shelf

		takes_job = "project" in inspect.signature(bin_assignments.move_to).parameters
		return bool(project_shelf) and takes_job
	except Exception:
		return False


def _can_read_projects():
	return bool(_quietly(lambda: frappe.has_permission("Project", "read"), False))


def _may_read_project(name):
	"""Whether the caller may read this one Project: the role, and any User Permission that
	narrows it. Anything that cannot be answered is a no."""
	return bool(_quietly(lambda: frappe.has_permission("Project", "read", doc=name), False))


def _projects(names):
	"""``{project: row}`` for the jobs named, with the customer only for those who may read
	Projects."""
	names = sorted({n for n in names or () if n})
	if not names:
		return {}
	fields = ["name", "project_name", "status", "project_type"]
	if _can_read_projects():
		fields.append("customer")
	rows = frappe.get_all("Project", filters={"name": ["in", names]}, fields=fields)
	return {row.name: row for row in rows}


def _project_payload(row, name=None):
	if not row:
		return {"project": name, "project_name": name, "customer": None, "status": None, "closed": False}
	return {
		"project": row.name,
		"project_name": row.get("project_name") or row.name,
		"customer": row.get("customer") or None,
		"status": row.get("status"),
		"project_type": row.get("project_type"),
		"closed": row.get("status") in rules.CLOSED_JOB_STATUSES,
	}


# ---------------------------------------------------------------------------
# The boot's extras
# ---------------------------------------------------------------------------


def _tree():
	"""``{warehouse: (warehouse_name, parent_warehouse)}`` for every warehouse. One read."""
	rows = frappe.get_all("Warehouse", fields=["name", "warehouse_name", "parent_warehouse"])
	return {row.name: (row.warehouse_name or row.name, row.parent_warehouse) for row in rows}


def _vehicle_leaves(tree):
	"""Every warehouse that is a truck or a van: one an Employee's vehicle field names, anything
	under one, and (the fallback, for a site that never filled that field) anything whose own
	name or a group above it reads as a vehicle."""
	named = project_stock.vehicles()
	out = set()
	for name in tree:
		chain, seen, at = [], set(), name
		while at and at not in seen and len(seen) < 64:
			seen.add(at)
			chain.append(at)
			at = (tree.get(at) or (None, None))[1]
		if named.intersection(chain) or place_rules.looks_like_vehicle(
			[(tree.get(link) or (link,))[0] for link in chain]
		):
			out.add(name)
	return out


def _counts():
	"""``{warehouse: {"missing_items", "unassigned_items", "stock_value"}}``: assigned items with
	nothing on hand, items on hand with no assignment, and the value on hand. One query, three
	grouped derived tables, stock items that are not disabled only (the bin's own list shows no
	others)."""
	rows = frappe.db.sql(
		"""
		select w.name,
			coalesce(m.items, 0) as missing_items,
			coalesce(u.items, 0) as unassigned_items,
			coalesce(v.value, 0) as stock_value
		from `tabWarehouse` w
		left join (
			select pr.warehouse, count(distinct pr.item_code) as items
			from `tabPutaway Rule` pr
			join `tabItem` i on i.name = pr.item_code and i.disabled = 0 and i.is_stock_item = 1
			left join `tabBin` b on b.item_code = pr.item_code and b.warehouse = pr.warehouse
			where pr.disable = 0 and coalesce(b.actual_qty, 0) <= 0
			group by pr.warehouse
		) m on m.warehouse = w.name
		left join (
			select b.warehouse, count(*) as items
			from `tabBin` b
			join `tabItem` i on i.name = b.item_code and i.disabled = 0 and i.is_stock_item = 1
			left join (
				select distinct warehouse, item_code from `tabPutaway Rule` where disable = 0
			) pr on pr.item_code = b.item_code and pr.warehouse = b.warehouse
			where b.actual_qty > 0 and pr.item_code is null
			group by b.warehouse
		) u on u.warehouse = w.name
		left join (
			select warehouse, sum(stock_value) as value from `tabBin`
			where actual_qty > 0 group by warehouse
		) v on v.warehouse = w.name
		where w.is_group = 0 and w.disabled = 0
		""",
		as_dict=True,
	)
	return {row.name: row for row in rows}


def decorate(bins):
	"""Add to each of ``manager_boot``'s bins what the views need, and return the boot's own
	extra keys.

	On each bin: ``kind`` (``project_stock_rules.warehouse_kind``), ``project`` /
	``project_name`` / ``customer`` / ``project_closed`` for a job's site, ``missing_items``,
	``unassigned_items`` (0 at a place nothing is ever assigned to) and, for a caller who may see
	it, ``stock_value``.

	Returned: ``shelf_warehouse`` / ``shelf_name``, ``job_sites`` (whether sites are set up),
	``can_see_value``, and ``shelf_job_moves`` (:func:`shelf_job_moves`: whether the page asks
	which job a move onto or off the Project Shelf is for, and sends it).

	Nothing is kept at the Project Shelf, a job site or the boneyard as an item's bin, so
	``assigned_items`` and ``missing_items`` are 0 there whatever Putaway Rules are left over
	from before (production's shelf was ``Stores``, with 54 of them).
	"""
	shelf = project_stock.shelf_warehouse()
	# The boot has already decided which bin is the boneyard (set, and not disabled): one answer.
	boneyard = next((row["warehouse"] for row in bins if row.get("is_boneyard")), None)
	site_map = _quietly(job_sites.site_map, {})
	sites = _quietly(lambda: set(job_sites.site_warehouses()), set()) | set(site_map)
	tree = _quietly(_tree, {})
	vehicles = _quietly(lambda: _vehicle_leaves(tree), set())
	counts = _quietly(_counts, {})
	projects = _quietly(lambda: _projects(site_map.values()), {})
	value = can_see_value()

	shelf_name = None
	for row in bins:
		name = row["warehouse"]
		kind = place_rules.warehouse_kind(
			name, shelf=shelf, boneyard=boneyard, all_sites=sites, vehicles=vehicles
		)
		row["kind"] = kind
		if kind == place_rules.KIND_SHELF:
			shelf_name = row.get("warehouse_name") or name
		project = site_map.get(name)
		if project:
			info = _project_payload(projects.get(project), project)
			row.update(
				{
					"project": project,
					"project_name": info["project_name"],
					"customer": info["customer"],
					"project_closed": info["closed"],
				}
			)
		count = counts.get(name) or {}
		keeps = kind in _UNASSIGNED_KINDS
		if not keeps:
			row["assigned_items"] = 0
		row["missing_items"] = cint(count.get("missing_items")) if keeps else 0
		row["unassigned_items"] = cint(count.get("unassigned_items")) if keeps else 0
		if value:
			row["stock_value"] = flt(count.get("stock_value"))
	return {
		"shelf_warehouse": shelf if shelf_name else None,
		"shelf_name": shelf_name,
		"job_sites": bool(_quietly(job_sites.enabled, False)),
		"can_see_value": value,
		"shelf_job_moves": bool(shelf_name) and shelf_job_moves(),
	}


# ---------------------------------------------------------------------------
# By item
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def item_locations(item_code):
	"""Every location that holds ``item_code`` or has it assigned, for the picker's item filter:
	``{"item_code", "item_name", "stock_uom", "locations": {warehouse: {"on_hand", "assigned"}}}``.

	Two small reads. An unknown item is refused in a sentence; a non-stock item is answered with
	no locations and ``is_stock_item`` false, so the page can say why the list is empty.
	"""
	scan._check_access()
	item = scan._item(item_code)
	if not item:
		frappe.throw(_("There is no item {0}.").format(cstr(item_code).strip() or _("with that code")))
	locations = {}
	for row in frappe.get_all(
		"Bin", filters={"item_code": item.name, "actual_qty": [">", 0]}, fields=["warehouse", "actual_qty"]
	):
		locations[row.warehouse] = {"on_hand": flt(row.actual_qty), "assigned": False}
	for row in frappe.get_all(
		"Putaway Rule", filters={"item_code": item.name, "disable": 0}, fields=["warehouse"]
	):
		locations.setdefault(row.warehouse, {"on_hand": 0.0, "assigned": False})["assigned"] = True
	return {
		"item_code": item.name,
		"item_name": item.item_name or item.name,
		"stock_uom": item.stock_uom,
		"is_stock_item": bool(cint(item.is_stock_item)),
		"locations": locations,
	}


# ---------------------------------------------------------------------------
# By project
# ---------------------------------------------------------------------------


def _own_sites(project):
	"""The project's site warehouses as rows, the first one made first; ``[]`` before the
	Warehouse field exists."""
	names = [warehouse for warehouse, job in _quietly(job_sites.site_map, {}).items() if job == project]
	if not names:
		return []
	rows = frappe.get_all(
		"Warehouse",
		filters={"name": ["in", names]},
		fields=["name", "warehouse_name", "disabled", "is_group", "creation"],
		order_by="creation asc",
	)
	return [row for row in rows if not cint(row.is_group)]


def _on_hand_rows(warehouse):
	"""What is on hand at one location, by item name: ``[{"item_code", "item_name", "stock_uom",
	"qty"}]``, with ``value`` for a caller who may see it."""
	rows = frappe.db.sql(
		"""
		select b.item_code, i.item_name, i.stock_uom, b.actual_qty, b.stock_value
		from `tabBin` b
		join `tabItem` i on i.name = b.item_code
		where b.warehouse = %(warehouse)s and b.actual_qty > 0
		""",
		{"warehouse": warehouse},
		as_dict=True,
	)
	value = can_see_value()
	out = []
	for row in rows:
		line = {
			"item_code": row.item_code,
			"item_name": row.item_name or row.item_code,
			"stock_uom": row.stock_uom,
			"qty": flt(row.actual_qty),
		}
		if value:
			line["value"] = flt(row.stock_value)
		out.append(line)
	out.sort(key=lambda r: rules.natural_key(r["item_name"]))
	return out


def _send_refusal(project, has_site, site_closed):
	"""Why material cannot be sent to this job's site from the page, as a sentence, or ``None``.
	The same judgements ``job_sites.ensure_site`` and ``refuse_send`` make when the move is tried,
	asked up front so the button is not offered only to be refused."""
	if not _quietly(job_sites.ready, False):
		return _("Job sites are being set up.")
	if site_closed:
		return _("This job's site was closed. Ask a Stock Manager to open it again.")
	if project is None:
		# The caller may not read this Project, so nothing about it (that it is finished, what
		# type it is) is said ahead of time. The move itself still refuses what it must, in the
		# sentence the server has always given these roles when they try.
		if not has_site and not _quietly(job_sites.group, None):
			return _(
				"Job sites are not set up yet. A Stock Manager needs to choose the Job Sites warehouse in Inventory Scanner Settings."
			)
		return None
	if has_site:
		return site_rules.send_refusal(
			project.name, project.get("project_name"), project.get("status"), rules.CLOSED_JOB_STATUSES
		)
	if not _quietly(job_sites.group, None):
		return _(
			"Job sites are not set up yet. A Stock Manager needs to choose the Job Sites warehouse in Inventory Scanner Settings."
		)
	return site_rules.site_refusal(
		project.name,
		project.get("project_name"),
		project.get("status"),
		project.get("project_type"),
		_quietly(job_sites.allowed_types, site_rules.DEFAULT_PROJECT_TYPES),
		rules.CLOSED_JOB_STATUSES,
		has_site=False,
	)


@frappe.whitelist(methods=["GET"])
def project_view(project):
	"""One job's material in the two places the shop holds it for the job: the Project Shelf and
	the job's own site.

	``{"project": {...}, "shelf": {"warehouse", "warehouse_name", "items"} or None,
	"site": {"warehouse", "warehouse_name", "items", "disabled"} or None, "send_refusal": sentence or
	None}``. ``shelf`` is ``None`` on a site with no Project Shelf set; ``site`` is ``None`` until
	material is first sent to the job (the page opens it then, through ``api.stock_scan.job_site``).
	Shelf items are this job's share (``project_stock.shelf_stock``: ``qty`` is what the shelf
	can hand over, ``recorded`` the job's ledger balance, higher when the shelf holds less than
	the records say); site items are everything on hand at the site.

	**The project's own details go only to someone who may read that Project** (the role and
	any User Permission: ``frappe.has_permission`` with the document). The endpoint takes a name
	from the caller, and a Stock Scan role is not Project read. Anyone else gets what the
	warehouses already show them: the id they asked for, the name the job's site carries when
	it has one, and the stock. No status, type or customer, no "this job is finished" ahead of
	time, and the same answer for a job they may not read and one that does not exist, so the
	endpoint cannot be used to learn which project ids are real.
	"""
	scan._check_access()
	name = cstr(project).strip()
	if not name:
		frappe.throw(_("There is no job {0}.").format(_("with that name")))
	row = frappe.db.get_value(
		"Project", name, ["name", "project_name", "status", "project_type", "customer"], as_dict=True
	)
	readable = bool(row) and _may_read_project(row.name)
	if not row and _can_read_projects():
		# Someone who can list Projects anyway is told plainly.
		frappe.throw(_("There is no job {0}.").format(name))
	sites = _own_sites(name)
	if readable:
		info = _project_payload(row)
		row_for_rules = row
	else:
		info = _project_payload(None, name)
		if row and sites:
			# A job's site shows these roles the job's name on its own screen (job_sites.site_info).
			info["project_name"] = row.get("project_name") or name
		row_for_rules = None

	shelf = project_stock.shelf_warehouse()
	shelf_row = scan._warehouse(shelf) if shelf else None
	shelf_out = None
	if shelf_row and not cint(shelf_row.disabled) and not cint(shelf_row.is_group):
		shelf_out = {
			"warehouse": shelf_row.name,
			"warehouse_name": shelf_row.warehouse_name or shelf_row.name,
			"items": project_stock.shelf_stock(name),
		}

	site_out = None
	if sites:
		site = sites[0]
		site_out = {
			"warehouse": site.name,
			"warehouse_name": site.warehouse_name or site.name,
			"disabled": bool(cint(site.disabled)),
			"items": _on_hand_rows(site.name),
		}
	return {
		"project": info,
		"shelf": shelf_out,
		"site": site_out,
		"send_refusal": _send_refusal(row_for_rules, bool(site_out), bool(site_out and site_out["disabled"])),
	}


@frappe.whitelist(methods=["GET"])
def project_index():
	"""The jobs that have material on the Project Shelf or at a job site, for the *By project*
	view's list: ``[{"project", "project_name", "customer", "status", "closed", "site",
	"site_items", "shelf_items"}]``, jobs still in progress first, then by name."""
	scan._check_access()
	shelf_share = project_stock.shelf_by_project()
	site_map = _quietly(job_sites.site_map, {})
	site_items = {}
	if site_map:
		for row in frappe.db.sql(
			"""
			select warehouse, count(*) as item_count from `tabBin`
			where actual_qty > 0 and warehouse in %(warehouses)s group by warehouse
			""",
			{"warehouses": tuple(site_map)},
			as_dict=True,
		):
			site_items[row.warehouse] = cint(row.item_count)
	by_project = {}
	for warehouse, project in site_map.items():
		entry = by_project.setdefault(project, {"site": warehouse, "site_items": 0, "shelf_items": 0})
		entry["site_items"] += site_items.get(warehouse, 0)
	for project, held in shelf_share.items():
		entry = by_project.setdefault(project, {"site": None, "site_items": 0, "shelf_items": 0})
		entry["shelf_items"] = len(held)
	projects = _projects(by_project)
	out = []
	for project, entry in by_project.items():
		info = _project_payload(projects.get(project), project)
		out.append({**info, **entry})
	out.sort(key=lambda r: (r["closed"], rules.natural_key(r["project_name"] or r["project"] or "")))
	return out


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


def _cut(rows):
	return rows[:PROBLEM_LIMIT], len(rows) > PROBLEM_LIMIT


def _usable():
	"""Names of the enabled leaf warehouses that are not transit: the ones the picker lists."""
	rows = frappe.get_all(
		"Warehouse",
		filters={"is_group": 0, "disabled": 0},
		fields=["name", "warehouse_name", "warehouse_type"],
	)
	return {
		row.name: row.warehouse_name or row.name
		for row in rows
		if (row.warehouse_type or "") != "Transit"
	}


@frappe.whitelist(methods=["GET"])
def problems():
	"""Where the records and the shelves disagree, as four lists the page shows with a link to
	the bin that fixes each row:

	* ``assigned_empty`` -- an item assigned to a bin (an enabled Putaway Rule) with nothing on
	  hand there: restock it, or take it off the bin. Never a job site, the boneyard or the Project
	  Shelf either: a rule left over there is not a bin to restock.
	* ``unassigned_stock`` -- stock on hand at a shop bin or a vehicle with no assignment there:
	  put the item on the bin, or move it to its own. Never a job site, the boneyard or the Project
	  Shelf, where nothing is assigned by design.
	* ``finished_shelf`` -- stock still on the Project Shelf for a job that is finished.
	* ``finished_site`` -- stock still at the site of a job that is finished.

	Each list is cut at :data:`PROBLEM_LIMIT` and says so (``truncated``). Stock items that are
	not disabled only.
	"""
	scan._check_access()
	shelf = project_stock.shelf_warehouse()
	boneyard = project_stock.boneyard_warehouse()
	site_map = _quietly(job_sites.site_map, {})
	sites = _quietly(lambda: set(job_sites.site_warehouses()), set()) | set(site_map)
	names = _usable()

	empty = frappe.db.sql(
		"""
		select distinct pr.warehouse, pr.item_code, i.item_name, i.stock_uom
		from `tabPutaway Rule` pr
		join `tabItem` i on i.name = pr.item_code and i.disabled = 0 and i.is_stock_item = 1
		left join `tabBin` b on b.item_code = pr.item_code and b.warehouse = pr.warehouse
		where pr.disable = 0 and coalesce(b.actual_qty, 0) <= 0
		""",
		as_dict=True,
	)
	# Nothing is kept at a job site, the boneyard or the Project Shelf as an item's bin, so a
	# rule left over there (the shelf was once Stores) is not an empty bin to restock.
	skip = sites | {shelf, boneyard}
	assigned_empty = [
		{
			"warehouse": row.warehouse,
			"warehouse_name": names[row.warehouse],
			"item_code": row.item_code,
			"item_name": row.item_name or row.item_code,
			"stock_uom": row.stock_uom,
		}
		for row in empty
		if row.warehouse in names and row.warehouse not in skip
	]

	loose = frappe.db.sql(
		"""
		select b.warehouse, b.item_code, i.item_name, i.stock_uom, b.actual_qty
		from `tabBin` b
		join `tabItem` i on i.name = b.item_code and i.disabled = 0 and i.is_stock_item = 1
		left join (
			select distinct warehouse, item_code from `tabPutaway Rule` where disable = 0
		) pr on pr.item_code = b.item_code and pr.warehouse = b.warehouse
		where b.actual_qty > 0 and pr.item_code is null
		""",
		as_dict=True,
	)
	unassigned_stock = [
		{
			"warehouse": row.warehouse,
			"warehouse_name": names[row.warehouse],
			"item_code": row.item_code,
			"item_name": row.item_name or row.item_code,
			"stock_uom": row.stock_uom,
			"qty": flt(row.actual_qty),
		}
		for row in loose
		if row.warehouse in names and row.warehouse not in skip
	]

	shelf_share = project_stock.shelf_by_project() if shelf else {}
	projects = _projects(set(shelf_share) | set(site_map.values()))
	closed = {name for name, row in projects.items() if row.get("status") in rules.CLOSED_JOB_STATUSES}

	finished_shelf = []
	held_codes = {code for project in closed for code in shelf_share.get(project, {})}
	items = project_stock._items(held_codes) if held_codes else {}
	for project in sorted(closed):
		info = _project_payload(projects.get(project), project)
		for code, qty in (shelf_share.get(project) or {}).items():
			item = items.get(code) or {}
			finished_shelf.append(
				{
					"project": project,
					"project_name": info["project_name"],
					"status": info["status"],
					"warehouse": shelf,
					"item_code": code,
					"item_name": item.get("item_name") or code,
					"stock_uom": item.get("stock_uom"),
					"qty": qty,
				}
			)

	finished_site = []
	for warehouse, project in site_map.items():
		if project not in closed:
			continue
		info = _project_payload(projects.get(project), project)
		for line in _on_hand_rows(warehouse):
			finished_site.append(
				{
					"project": project,
					"project_name": info["project_name"],
					"status": info["status"],
					"warehouse": warehouse,
					"warehouse_name": names.get(warehouse) or warehouse,
					"item_code": line["item_code"],
					"item_name": line["item_name"],
					"stock_uom": line["stock_uom"],
					"qty": line["qty"],
				}
			)

	def by_place(row):
		return (rules.natural_key(row.get("warehouse_name") or ""), rules.natural_key(row["item_name"]))

	def by_job(row):
		return (rules.natural_key(row.get("project_name") or ""), rules.natural_key(row["item_name"]))

	out = {}
	for key, rows, order in (
		("assigned_empty", assigned_empty, by_place),
		("unassigned_stock", unassigned_stock, by_place),
		("finished_shelf", finished_shelf, by_job),
		("finished_site", finished_site, by_job),
	):
		rows.sort(key=order)
		shown, truncated = _cut(rows)
		out[key] = {"rows": shown, "total": len(rows), "truncated": truncated}
	return out
