# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Warehouse Map page's endpoints (v1.815.0): a drawn map of where the bins are.

Nik, 2026-11-12: "we need a map for the location bins and a way to edit and create the map for
our warehouse locations at our office. I'll provide the floor plan layout and you help me create
the map from that."

The page is ``inventory_enhancements/page/warehouse_map`` (``/desk/warehouse-map``); a map is a
``Warehouse Map`` record. Every judgement is in ``inventory_enhancements/warehouse_map_rules.py``
(pure, tested bench-free); the rules about what may be *stored* run in the DocType's own
``validate``, so this file and the Desk form cannot disagree.

Reads (GET):

* :func:`boot` -- the maps there are, and whether the caller may edit.
* :func:`get_map` -- one map: its picture, its boxes, each box's front view (the shelves and bins
  under its warehouse, from the Warehouse tree) and every bin's counts and color.
* :func:`locate` -- which map and box show a warehouse ("Show on map" from a bin, a report, the
  Warehouse form).
* :func:`find_item` -- every place an item is on hand or assigned, each with the map and box that
  show it ("Show on map" from the Item form and Stock Scan, and the page's own search).
* :func:`editor_data` -- what the editor offers: the boxes worth placing and where each already
  is, and every warehouse for the picker.

Writes (POST): :func:`save_map` and :func:`delete_map`. Nothing else is written, and neither
touches a Warehouse, a Putaway Rule or stock.

Things this module is careful about:

* **The numbers are the Bin Manager's.** A bin's counts come from the same two queries
  (``bin_assignments._bin_rows`` and ``bin_views.decorate``), so the map and the Bin Manager
  cannot disagree about which bins hold stock or have a problem.
* **Who may look, who may change.** Every read asks the Stock Scan role gate
  (``api.stock_scan._check_access``) and read on Warehouse Map. A change is saved **as the
  caller** (``doc.save()``, never ``ignore_permissions``), so it needs the DocType's own write
  permission: the Warehouse Map Editor role, or System Manager. The role adds editing to someone
  who already works the inventory; by itself it does not open the page.
* **The floor plan picture is a private file.** It is attached to the map's record, so Frappe
  serves it to exactly the people who can read the map (``File.has_permission``), and the
  DocType refuses a public file or a web address.
* **A stale editor cannot overwrite a newer map.** :func:`save_map` takes the ``modified`` stamp
  the editor was opened with and refuses when the record has moved on since.
* **Only named fields are written.** The editor's payload is a dict from the browser; the keys
  in :data:`_MAP_FIELDS` are read out of it and each box is rebuilt by
  ``warehouse_map_rules.clean_shape``. Nothing else in the payload reaches the record.
* **A disabled map is hidden from everyone but editors**, here and in ``locate`` and
  ``find_item``, so a map being drawn is not shown half-finished.

Tabs, per ``CLAUDE.md`` (new file).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from erpnext_enhancements.api import stock_scan as scan
from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as rules

MAP = "Warehouse Map"
SHAPE = "Warehouse Map Shape"

#: The map's own fields the editor may send. ``map_name`` is handled apart (a rename).
_MAP_FIELDS = ("sort_order", "disabled", "width", "height", "background")

#: A box's fields. Each is cleaned by ``warehouse_map_rules.clean_shape`` in the DocType's validate.
_SHAPE_FIELDS = ("shape_type", "warehouse", "label", "x", "y", "w", "h", "rotation", "shelves_from", "bins_from")

#: Kinds of place that are not in the building: listed beside the map, never drawn on it.
_OFF_MAP_KINDS = ("vehicle", "job_site")


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------


def _check_read():
	scan._check_access()
	if not frappe.has_permission(MAP, "read"):
		frappe.throw(_("You are not permitted to see the warehouse map."), frappe.PermissionError)


def can_edit():
	"""Whether the caller may create and change maps: the DocType's own create and write."""
	try:
		return bool(frappe.has_permission(MAP, "write") and frappe.has_permission(MAP, "create"))
	except Exception:
		return False


def _require_edit():
	_check_read()
	if not can_edit():
		frappe.throw(
			_("Changing the warehouse map needs the Warehouse Map Editor role. Ask a System Manager."),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Reads shared by the endpoints
# ---------------------------------------------------------------------------


def _tree():
	"""``{warehouse: row}`` for every warehouse. One read."""
	rows = frappe.get_all(
		"Warehouse",
		fields=["name", "warehouse_name", "parent_warehouse", "is_group", "disabled", "warehouse_type"],
	)
	return {row.name: row for row in rows}


def _map_rows(editor):
	"""The maps the caller may see, in the order the page lists them."""
	rows = frappe.get_all(MAP, fields=["name", "map_name", "sort_order", "disabled", "modified"])
	if not editor:
		rows = [row for row in rows if not cint(row.disabled)]
	rows.sort(key=lambda row: (cint(row.sort_order), rules.natural_key(row.map_name or row.name)))
	return rows


def _placed(maps):
	"""``{warehouse: map}`` for every box on the maps named."""
	maps = list(maps or [])
	if not maps:
		return {}
	rows = frappe.get_all(
		SHAPE,
		filters={"parenttype": MAP, "parent": ["in", maps]},
		fields=["parent", "warehouse"],
	)
	return {row.warehouse: row.parent for row in rows if row.warehouse}


def _bin_rows():
	"""``{warehouse: bin}`` for every bin, with the Bin Manager's own counts and kinds: the two
	reads ``bin_assignments.manager_boot`` makes, and nothing else of that boot."""
	# Imported here: both modules import this package's neighbours, and a map with no counts is
	# still a map.
	from erpnext_enhancements.inventory_enhancements import bin_assignments, bin_views

	settings = scan.get_settings()
	boneyard = cstr(settings.get("boneyard_warehouse")).strip() or None
	bins = [
		{
			"warehouse": row.name,
			"warehouse_name": row.warehouse_name or row.name,
			"on_hand_items": cint(row.on_hand_items),
			"assigned_items": cint(row.assigned_items),
			"is_boneyard": bool(boneyard and row.name == boneyard),
		}
		for row in bin_assignments._bin_rows()
	]
	try:
		bin_views.decorate(bins)
	except Exception:
		frappe.log_error(title="Warehouse Map: the bins' kinds and problem counts could not be read")
	return {row["warehouse"]: row for row in bins}


def _bin_payload(row, name, tree):
	row = row or {}
	return {
		"warehouse_name": row.get("warehouse_name") or rules._name_of(tree, name),
		"kind": row.get("kind") or "",
		"on_hand_items": cint(row.get("on_hand_items")),
		"assigned_items": cint(row.get("assigned_items")),
		"missing_items": cint(row.get("missing_items")),
		"unassigned_items": cint(row.get("unassigned_items")),
		"status": rules.bin_status(row),
	}


def _not_in_building(tree):
	"""Every warehouse that is a vehicle or a job site, with everything under them: they are
	listed beside the map and never suggested as a box. Each part is read on its own, so one
	that cannot be read leaves the other."""
	out = set()
	try:
		from erpnext_enhancements.inventory_enhancements import job_sites

		out.update(job_sites.site_warehouses())
		group = job_sites.group()
		if group:
			out.add(group.name)
	except Exception:
		pass
	try:
		from erpnext_enhancements.inventory_enhancements import bin_views

		out.update(
			bin_views._vehicle_leaves(
				{name: (row.warehouse_name or name, row.parent_warehouse) for name, row in tree.items()}
			)
		)
	except Exception:
		pass
	return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def boot():
	"""The maps there are, in order, and whether the caller may edit them."""
	_check_read()
	editor = can_edit()
	return {
		"maps": [
			{
				"name": row.name,
				"title": row.map_name or row.name,
				"disabled": bool(cint(row.disabled)),
			}
			for row in _map_rows(editor)
		],
		"can_edit": editor,
	}


def _map_doc(name):
	name = cstr(name).strip()
	if not name or not frappe.db.exists(MAP, name):
		frappe.throw(_("There is no map called {0}.").format(name or _("that")))
	doc = frappe.get_doc(MAP, name)
	# The same sentence as a map that does not exist: a map being drawn is not announced.
	if cint(doc.disabled) and not can_edit():
		frappe.throw(_("There is no map called {0}.").format(name))
	return doc


@frappe.whitelist(methods=["GET"])
def get_map(name):
	"""One map as the page draws it.

	``map`` (name, title, size, picture, ``modified`` for the editor's save), ``shapes`` (each box
	with its warehouse's name and trail, its front view ``levels``, its ``summary`` and whether its
	warehouse is ``missing``), ``bins`` (every bin under any box: its name, counts and color) and
	``off_map`` (vehicles and job sites, which are not in the building).
	"""
	_check_read()
	doc = _map_doc(name)
	tree = _tree()
	kids = rules.children_index(tree)
	bin_rows = _bin_rows()

	shapes, bins = [], {}
	for row in doc.get("shapes") or []:
		warehouse = row.warehouse or None
		node = tree.get(warehouse) if warehouse else None
		shape = {
			"id": row.name,
			"shape_type": row.shape_type,
			"warehouse": warehouse,
			"warehouse_name": rules._name_of(tree, warehouse) if node else (warehouse or ""),
			"label": row.label or "",
			"x": flt(row.x),
			"y": flt(row.y),
			"w": flt(row.w),
			"h": flt(row.h),
			"rotation": flt(row.rotation),
			"shelves_from": row.shelves_from or rules.TOP,
			"bins_from": row.bins_from or rules.LEFT,
			"missing": bool(warehouse and not rules.usable(node)),
			"is_group": bool(node and rules.is_group(node)),
			"trail": rules.trail(warehouse, tree) if node else "",
			"levels": [],
			"summary": None,
		}
		if node and rules.usable(node):
			leaves = rules.leaves_under(warehouse, tree, kids)
			shape["levels"] = rules.levels(warehouse, tree, kids)
			shape["summary"] = rules.summarize([bin_rows.get(leaf) or {} for leaf in leaves])
			for leaf in leaves:
				bins[leaf] = _bin_payload(bin_rows.get(leaf), leaf, tree)
		shapes.append(shape)

	off_map = [
		{
			"warehouse": row["warehouse"],
			"warehouse_name": row.get("warehouse_name") or row["warehouse"],
			"kind": row.get("kind"),
			"project_name": row.get("project_name") or "",
			"on_hand_items": cint(row.get("on_hand_items")),
		}
		for row in bin_rows.values()
		if row.get("kind") in _OFF_MAP_KINDS
	]
	off_map.sort(key=lambda row: (row["kind"] or "", rules.natural_key(row["warehouse_name"])))

	return {
		"map": {
			"name": doc.name,
			"title": doc.map_name or doc.name,
			"width": flt(doc.width) or rules.DEFAULT_WIDTH,
			"height": flt(doc.height) or rules.DEFAULT_HEIGHT,
			"background": doc.background or "",
			"sort_order": cint(doc.sort_order),
			"disabled": bool(cint(doc.disabled)),
			"modified": cstr(doc.modified),
		},
		"shapes": shapes,
		"bins": bins,
		"off_map": off_map,
	}


def _visible_maps():
	return [row.name for row in _map_rows(can_edit())]


@frappe.whitelist(methods=["GET"])
def locate(warehouse):
	"""Which map and box show ``warehouse``: ``{"warehouse", "warehouse_name", "is_group", "map",
	"shape"}``, ``map`` ``None`` when no map shows it. An unknown warehouse is refused in a
	sentence."""
	_check_read()
	warehouse = cstr(warehouse).strip()
	tree = _tree()
	if warehouse not in tree:
		frappe.throw(_("There is no location called {0}.").format(warehouse or _("that")))
	found = rules.locate(warehouse, _placed(_visible_maps()), tree) or {}
	return {
		"warehouse": warehouse,
		"warehouse_name": rules._name_of(tree, warehouse),
		"is_group": rules.is_group(tree[warehouse]),
		"map": found.get("map"),
		"shape": found.get("shape"),
	}


@frappe.whitelist(methods=["GET"])
def find_item(item_code):
	"""Every place ``item_code`` is on hand or assigned (``bin_views.item_locations``, the Bin
	Manager's own item filter), each with the map and box that show it.

	``places`` are sorted with the ones a map shows first, then by how many are there. A place no
	map shows (a vehicle, a job site, a bin nobody placed yet) is still listed, with ``map``
	``None``, so the page can say where the item is even when it cannot point at it.
	"""
	_check_read()
	from erpnext_enhancements.inventory_enhancements import bin_views

	answer = bin_views.item_locations(item_code)
	tree = _tree()
	kids = rules.children_index(tree)
	placed = _placed(_visible_maps())
	places = []
	for warehouse, here in (answer.get("locations") or {}).items():
		found = rules.locate(warehouse, placed, tree, kids) or {}
		places.append(
			{
				"warehouse": warehouse,
				"warehouse_name": rules._name_of(tree, warehouse),
				"trail": rules.trail(warehouse, tree) if warehouse in tree else "",
				"on_hand": flt(here.get("on_hand")),
				"assigned": bool(here.get("assigned")),
				"map": found.get("map"),
				"shape": found.get("shape"),
			}
		)
	places.sort(
		key=lambda row: (0 if row["map"] else 1, -row["on_hand"], rules.natural_key(row["warehouse_name"]))
	)
	return {
		"item_code": answer.get("item_code"),
		"item_name": answer.get("item_name"),
		"stock_uom": answer.get("stock_uom"),
		"is_stock_item": bool(answer.get("is_stock_item")),
		"places": places,
	}


@frappe.whitelist(methods=["GET"])
def editor_data():
	"""What the editor offers. ``units``: the boxes worth placing (``warehouse_map_rules.units``),
	each with the map that already shows it (``on_map``, or ``None``) and the boxes that do
	(``via``). ``warehouses``: every warehouse that can
	be drawn, for the picker, with its trail and whether it is a group."""
	_require_edit()
	tree = _tree()
	kids = rules.children_index(tree)
	placed = _placed([row.name for row in _map_rows(True)])
	units = rules.units(tree, skip=_not_in_building(tree))
	for unit in units:
		unit["trail"] = rules.trail(unit["warehouse"], tree)
		shown = rules.shown_by(unit["warehouse"], placed, tree, kids) or {}
		unit["on_map"] = shown.get("map")
		unit["via"] = shown.get("via") or []
	warehouses = [
		{
			"name": name,
			"warehouse_name": row.warehouse_name or name,
			"trail": rules.trail(name, tree),
			"is_group": rules.is_group(row),
		}
		for name, row in tree.items()
		if rules.usable(row)
	]
	warehouses.sort(key=lambda row: (rules.natural_key(row["trail"]), rules.natural_key(row["warehouse_name"])))
	return {"units": units, "warehouses": warehouses}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _payload(value):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = None
	if not isinstance(value, dict):
		frappe.throw(_("The map could not be read. Reload the page and try again."))
	return value


@frappe.whitelist(methods=["POST"])
def save_map(payload, name=None, modified=None):
	"""Create a map (no ``name``) or save one, as the caller.

	``payload``: ``map_name``, the keys in :data:`_MAP_FIELDS`, and ``shapes`` (the whole list of
	boxes: it replaces what is stored). ``modified`` is the stamp the editor was opened with; a
	record changed since is refused rather than overwritten. A new ``map_name`` on an existing map
	renames it. Answers ``{"name", "modified"}``.
	"""
	_require_edit()
	data = _payload(payload)
	name = cstr(name).strip()
	title = rules.text(data.get("map_name"))

	if name:
		if not frappe.db.exists(MAP, name):
			frappe.throw(_("There is no map called {0}.").format(name))
		doc = frappe.get_doc(MAP, name)
		if modified and cstr(doc.modified) != cstr(modified):
			frappe.throw(
				_(
					"This map was changed by someone else after you opened it. Choose Refresh to load their version, then make your change again."
				),
				frappe.TimestampMismatchError,
			)
		if title and title != doc.name:
			problem = rules.map_name_problem(title)
			if problem:
				frappe.throw(_(problem))
			if frappe.db.exists(MAP, title):
				frappe.throw(_("There is already a map called {0}.").format(title))
			doc = frappe.get_doc(MAP, frappe.rename_doc(MAP, name, title))
			doc.map_name = title
	else:
		problem = rules.map_name_problem(title)
		if problem:
			frappe.throw(_(problem))
		if frappe.db.exists(MAP, title):
			frappe.throw(_("There is already a map called {0}.").format(title))
		doc = frappe.new_doc(MAP)
		doc.map_name = title

	for field in _MAP_FIELDS:
		if field in data:
			doc.set(field, data.get(field))
	if "shapes" in data:
		shapes = data.get("shapes")
		if not isinstance(shapes, list):
			frappe.throw(_("The map could not be read. Reload the page and try again."))
		doc.set("shapes", [])
		for shape in shapes:
			# Only the fields a box has; the DocType's validate cleans and checks each one.
			shape = shape if isinstance(shape, dict) else {}
			doc.append("shapes", {key: shape.get(key) for key in _SHAPE_FIELDS})
	doc.save()
	return {"name": doc.name, "modified": cstr(doc.modified)}


@frappe.whitelist(methods=["POST"])
def delete_map(name):
	"""Delete a map and its picture, as the caller (the DocType's own delete permission). No
	warehouse and no stock is touched: a map only ever pointed at them."""
	_require_edit()
	name = cstr(name).strip()
	if not name or not frappe.db.exists(MAP, name):
		frappe.throw(_("There is no map called {0}.").format(name or _("that")))
	frappe.delete_doc(MAP, name)
	return {"deleted": name}
