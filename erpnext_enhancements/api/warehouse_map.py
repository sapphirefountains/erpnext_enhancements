# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Warehouse Map page's endpoints (v1.818.0): a drawn map of where the bins are.

Nik, 2026-11-12: "we need a map for the location bins and a way to edit and create the map for
our warehouse locations at our office. I'll provide the floor plan layout and you help me create
the map from that."

The page is ``inventory_enhancements/page/warehouse_map`` (``/desk/warehouse-map``); a map is a
``Warehouse Floor Map`` record. Every judgement is in ``inventory_enhancements/warehouse_map_rules.py``
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
* :func:`box_levels` -- one warehouse's front view, for a box the editor has just tied or just
  made something in (the stored map does not describe it yet), in the box's own order when it has
  one.
* :func:`suggest_location` -- the name a new bay, shelf or bin would take there (v1.820.0).
* :func:`walk` -- one floor's walk for a count (v1.821.0, slice 3): its boxes in walk order, each
  box's rows as a person facing it reads them, and the items expected in each bin (Stock Scan's
  own ``_items_at``, the Count Desk's list). It counts nothing and posts nothing.

Free space (v1.821.0, slice 3) is not an endpoint of its own: :func:`get_map` gives each bin its
``space`` (``warehouse_map_rules.space_state``: free, kept for an item, in use, or not a bin to
fill) and each box its ``space`` summary, from the Bin Manager's own counts.

Writes (POST): :func:`save_map` and :func:`delete_map` write the map's own record and nothing
else. :func:`create_location` (v1.820.0, slice 2) makes a bay, a shelf or a bin: a Warehouse, and
nothing else (no Putaway Rule, no stock, no map record).

Things this module is careful about:

* **The numbers are the Bin Manager's.** A bin's counts come from the same two queries
  (``bin_assignments._bin_rows`` and ``bin_views.decorate``), so the map and the Bin Manager
  cannot disagree about which bins hold stock or have a problem.
* **Who may look, who may change.** Every read asks the Stock Scan role gate
  (``api.stock_scan._check_access``) and read on Warehouse Floor Map. A change is saved **as the
  caller** (``doc.save()``, never ``ignore_permissions``), so it needs the DocType's own write
  permission: the Warehouse Map Editor role, or System Manager. The role adds editing to someone
  who already works the inventory; by itself it does not open the page.
* **The floor plan picture is a private file.** It is attached to the map's record, so Frappe
  serves it to exactly the people who can read the map (``File.has_permission``). The DocType
  refuses an address that is not this map's own private File, and a ``File`` doc_event
  (``inventory_enhancements/warehouse_map_files.py``) refuses a public File attached to a map.
* **A stale editor cannot overwrite a newer map.** :func:`save_map` must be given the ``modified``
  stamp the editor was opened with and refuses when the record has moved on since. It is not
  optional: a caller that left it out would otherwise skip the check.
* **The app's own sentence, not Frappe's.** ``Document.save`` checks Links *before* ``validate``
  (``document.py`` ``_save``, read at v16.56.0), so a box tied to a deleted warehouse would be
  refused as "Could not find Row #3: Warehouse", and a ``warehouse`` that is not text would trip
  an ``assert``. :func:`save_map` therefore cleans the boxes with the rules first.
* **Making a location is the map editor's right, and goes through the Warehouse itself.**
  Nikolas, 2026-11-12, asked that the editor "can also create bins". On production only Item
  Manager may create a Warehouse, so :func:`create_location` checks the map editor's rule (the
  same one every save checks) and read on the parent warehouse, and then inserts with
  ``frappe.get_doc({...}).insert(ignore_permissions=True)``: the permission is the only thing
  skipped. ERPNext's own ``Warehouse.validate`` (the parent must be a group of the same company,
  the stock account must resolve), its nested-set update, every ``doc_event`` of this app and
  every Link check still run. No raw SQL and no background job (a deploy empties the queue). It
  only makes: nothing here renames, moves, disables or deletes a warehouse; a location made by
  mistake is deleted in the Desk by an Item Manager while nothing has been kept in it.
* **Only named fields are written.** The editor's payload is a dict from the browser; the keys
  in :data:`_MAP_FIELDS` are read out of it and each box is rebuilt by
  ``warehouse_map_rules.clean_shape``. Nothing else in the payload reaches the record.
* **A disabled map is left off the page for everyone but editors**, here and in ``locate`` and
  ``find_item``, so a map being drawn is not shown half-finished. That is all it is: the stock
  roles can read the DocType, so its own list in the Desk and the REST API still show a disabled
  map and its picture to them. A half-drawn map is not a secret (the same people see it the day
  it is finished), and hiding it there would take a ``has_permission`` hook and a permission
  query that could not be run on a bench when this was written.

Tabs, per ``CLAUDE.md`` (new file).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt

from erpnext_enhancements.api import stock_scan as scan
from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as rules

MAP = "Warehouse Floor Map"
SHAPE = "Warehouse Floor Map Shape"

#: The map's own fields the editor may send. ``map_name`` is handled apart (a rename).
_MAP_FIELDS = ("sort_order", "disabled", "width", "height", "background")

#: A box's fields. Each is cleaned by ``warehouse_map_rules.clean_shape`` in the DocType's validate.
_SHAPE_FIELDS = (
	"shape_type",
	"warehouse",
	"label",
	"x",
	"y",
	"w",
	"h",
	"rotation",
	"shelves_from",
	"bins_from",
	"bin_order",
	"walk_order",
)

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
		fields=["name", "warehouse_name", "parent_warehouse", "is_group", "disabled", "warehouse_type", "company"],
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
		# Free space (v1.821.0): free, kept, in_use, or "" for a place that is not a bin to fill.
		"space": rules.space_state(row),
	}


def _not_in_building(tree, strict=False):
	"""Every warehouse that is a vehicle or a job site, with everything under them: they are
	listed beside the map and never suggested as a box. Each part is read on its own, so one
	that cannot be read leaves the other.

	``strict`` (v1.820.0, for making a location): a lookup that fails raises instead of leaving
	its part out, because there an empty answer would let a place be made under a vehicle or a job
	site. The listing beside the map keeps the forgiving form."""
	out = set()
	try:
		from erpnext_enhancements.inventory_enhancements import job_sites

		out.update(job_sites.site_warehouses())
		group = job_sites.group()
		if group:
			out.add(group.name)
	except Exception:
		if strict:
			raise
	try:
		from erpnext_enhancements.inventory_enhancements import bin_views

		out.update(
			bin_views._vehicle_leaves(
				{name: (row.warehouse_name or name, row.parent_warehouse) for name, row in tree.items()}
			)
		)
	except Exception:
		if strict:
			raise
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
			# The box's own order, where it has one; the page lays it over the automatic order.
			"bin_order": rules.clean_order(row.get("bin_order")),
			"missing": bool(warehouse and not rules.usable(node)),
			# Why: "deleted" (the box has to go), "disabled" or "transit" (the box may stay).
			"missing_reason": rules.missing_reason(node) if warehouse else "",
			"is_group": bool(node and rules.is_group(node)),
			"trail": rules.trail(warehouse, tree) if node else "",
			"levels": [],
			"summary": None,
			"space": None,
			# Its stored place in the walk (0: by where it stands), and the place it is walked in.
			"walk_order": rules.walk_number(row.get("walk_order")),
			"walk": 0,
		}
		if node and rules.usable(node):
			leaves = rules.leaves_under(warehouse, tree, kids)
			shape["levels"] = rules.levels(warehouse, tree, kids, shape["bin_order"])
			shape["summary"] = rules.summarize([bin_rows.get(leaf) or {} for leaf in leaves])
			shape["space"] = rules.space_summary([bin_rows.get(leaf) or {} for leaf in leaves])
			for leaf in leaves:
				bins[leaf] = _bin_payload(bin_rows.get(leaf), leaf, tree)
		shapes.append(shape)
	walked = [i for i in rules.walk_order(shapes) if not shapes[i]["missing"]]
	for n, i in enumerate(walked, 1):
		shapes[i]["walk"] = n

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
			# Whether an editor has put the boxes' walk in order, rather than where they stand.
			"walk_set": any(shape["walk_order"] for shape in shapes),
		},
		"shapes": shapes,
		"bins": bins,
		"off_map": off_map,
		# Whether the bins' kinds were read: without them nothing can be called free space.
		"kinds_read": any(row.get("kind") for row in bin_rows.values()),
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
	outside = _not_in_building(tree)
	units = rules.units(tree, skip=outside)
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
			# For "New bay" (v1.820.0): where the warehouse hangs, and whether it is somewhere a
			# place can be made from the map at all (not a vehicle or a job site).
			"parent": row.parent_warehouse or "",
			"outside": name in outside,
			# What can be made inside it today (warehouse_map_rules.takes), for the editor's buttons.
			**(rules.takes(name, tree, kids) if rules.is_group(row) else {"takes_group": False, "takes_bin": False}),
		}
		for name, row in tree.items()
		if rules.usable(row)
	]
	warehouses.sort(key=lambda row: (rules.natural_key(row["trail"]), rules.natural_key(row["warehouse_name"])))
	return {"units": units, "warehouses": warehouses}


@frappe.whitelist(methods=["GET"])
def box_levels(warehouse, order=None):
	"""One warehouse's front view (``warehouse_map_rules.levels``): ``{"is_group", "levels",
	"names"}``, ``names`` each row's and bin's plain name. For the editor, which shows a box's
	shelves and bins so they can be put in order by hand, and which may have tied the box, or made
	a shelf or a bin in it, a moment ago.

	``order`` is the box's own order as the editor holds it (one warehouse a line), or nothing for
	the automatic order. Since v1.820.0: a bin made in a box that has its own order is read back
	in that order, the new bin at the end of its shelf, as the floor will show it."""
	_require_edit()
	warehouse = cstr(warehouse).strip()
	tree = _tree()
	if warehouse not in tree:
		frappe.throw(_("There is no location called {0}.").format(warehouse or _("that")))
	levels = rules.levels(warehouse, tree, rules.children_index(tree), rules.clean_order(order) or None)
	return _levels_answer(warehouse, tree, levels)


def _levels_answer(warehouse, tree, levels):
	names = {}
	for level in levels:
		names[level["warehouse"]] = rules._name_of(tree, level["warehouse"])
		for leaf in level["bins"]:
			names[leaf] = rules._name_of(tree, leaf)
	return {"is_group": rules.is_group(tree[warehouse]), "levels": levels, "names": names}


# ---------------------------------------------------------------------------
# Making a location (v1.820.0, slice 2)
# ---------------------------------------------------------------------------


def _suffix(company):
	"""The suffix ERPNext's ``Warehouse.autoname`` puts on a name: `` - <abbr>`` of the company."""
	company = cstr(company).strip()
	if not company:
		return ""
	abbr = cstr(frappe.get_cached_value("Company", company, "abbr")).strip()
	return f" - {abbr}" if abbr else ""


def _kind(kind):
	kind = cstr(kind).strip().lower()
	if kind not in rules.KINDS:
		frappe.throw(_("A new location is a bay, a shelf or a bin."))
	return kind


#: Said when the vehicles and job sites could not be looked up: nothing is made on a guess.
_LOOKUP_FAILED = "Whether {0} is a vehicle or a job site could not be checked just now, so nothing can be made there. Try again."


def _shelf_warehouse():
	"""The Project Shelf's warehouse, for its own refusal sentence; ``None`` when there is none or it
	cannot be read (it is a bin either way, and refused as one)."""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf

		return project_shelf.warehouse()
	except Exception:
		return None


def _may_read(parent, tree):
	"""Read on the warehouse itself (its own row, so a User Permission on Warehouse counts). Asked
	before anything is said about it. A name that is not a warehouse is answered by the rules."""
	return parent not in tree or bool(frappe.has_permission("Warehouse", "read", parent))


def _parent_problem(parent, tree, kind):
	"""``(row, problem)``: whether anything can be made in ``parent``, then whether a ``kind`` can
	go there given what it holds (``warehouse_map_rules.placement_problem``). The vehicles and job
	sites are looked up strictly: a failed lookup is a problem of its own, never an empty list."""
	row = tree.get(parent)
	if row is None:
		return row, rules.parent_problem(row, parent)
	try:
		outside = _not_in_building(tree, strict=True)
	except Exception:
		frappe.log_error(title="Warehouse Map: vehicles and job sites could not be looked up")
		return row, _LOOKUP_FAILED.format(rules._name_of(tree, parent))
	problem = rules.parent_problem(row, parent, outside, shelf=_shelf_warehouse())
	if problem:
		return row, problem
	return row, rules.placement_problem(parent, kind, tree, rules.children_index(tree))


@frappe.whitelist(methods=["GET"])
def suggest_location(parent, kind):
	"""The name a new ``kind`` (bay, shelf or bin) inside ``parent`` would take
	(``warehouse_map_rules.suggest_name``): ``{"parent", "parent_name", "kind", "warehouse_name",
	"suffix", "problem"}``. ``warehouse_name`` is ``""`` where the places already there have no
	number to follow. ``problem`` is the sentence that says why nothing can be made there, and the
	name is then empty: the editor shows it in place of the name rather than as an error."""
	_require_edit()
	kind = _kind(kind)
	tree = _tree()
	parent = cstr(parent).strip()
	out = {"parent": parent, "parent_name": rules._name_of(tree, parent), "kind": kind, "warehouse_name": "", "suffix": "", "problem": ""}
	# Read on the parent first, so nothing is said about a place the caller may not see, and the
	# editor shows no suggestion for one it would refuse at Make it.
	if not _may_read(parent, tree):
		out["problem"] = _("You are not permitted to see {0}.").format(parent)
		return out
	row, problem = _parent_problem(parent, tree, kind)
	if problem:
		out["problem"] = _(problem)
		return out
	spec = rules.KINDS[kind]
	out["suffix"] = _suffix(row.company)
	out["warehouse_name"] = rules.suggest_name(
		rules.siblings_of(parent, tree, rules.children_index(tree), spec["is_group"]),
		rules._name_of(tree, parent),
		spec["word"],
		taken=tree.keys(),
		suffix=out["suffix"],
	)
	return out


def _insert(warehouse_name, parent, is_group, company):
	"""One Warehouse, through the document. The map editor's rule is the permission (the caller
	has checked it); nothing else is skipped: not validate, not the Links, not a hook."""
	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": warehouse_name,
			"parent_warehouse": parent,
			"is_group": 1 if is_group else 0,
			"company": company,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# A clash only the database sees (two people at once, or names the database counts as the
		# same, as with accents): the request rolls back whole, and the person reads the app's sentence.
		frappe.throw(_("There is already a location called {0}.").format(warehouse_name))
	return doc.name


@frappe.whitelist(methods=["POST"])
def create_location(parent, kind, warehouse_name, children=0):
	"""Make a bay, a shelf or a bin (``kind``) called ``warehouse_name`` inside ``parent``, and
	with it ``children`` shelves (for a bay) or bins (for a shelf), named in turn by the same rule.

	Answers ``{"warehouse", "warehouse_name", "parent", "is_group", "trail", "created", "unit",
	"levels", "names"}``: ``unit`` is the new place as the editor's list of things to place shows
	one, and ``levels``/``names`` its front view, so a new bay's box is drawn with its shelves at
	once.

	Who: the map editor's rule (``_require_edit``: the Stock Scan gate, read on the map, and the
	map's own write and create) and read on ``parent``. Not create on Warehouse: that is the point
	of the editor making bins (Nikolas, 2026-11-12). What: new Warehouses, through
	``Document.insert``, so ERPNext's and this app's rules all hold. Each is the company of
	``parent``; nothing else is copied from it.

	Refused, in the app's own sentence, before anything is written: a ``parent`` nothing can be
	made inside (``warehouse_map_rules.parent_problem``), a name the rules refuse
	(``location_name_problem``) and a name any warehouse already has, disabled ones included. It
	is all one request, so a refusal from ERPNext part way through (the stock account, say) rolls
	the whole request back and leaves nothing half made.
	"""
	_require_edit()
	kind = _kind(kind)
	tree = _tree()
	parent = cstr(parent).strip()
	if not _may_read(parent, tree):
		frappe.throw(_("You are not permitted to see {0}.").format(parent), frappe.PermissionError)
	row, problem = _parent_problem(parent, tree, kind)
	if problem:
		frappe.throw(_(problem))
	company = cstr(row.company).strip()
	suffix = _suffix(company)
	name = rules.bare_name(warehouse_name, suffix)
	problem = rules.location_name_problem(name)
	if problem:
		frappe.throw(_(problem))
	taken = {key.lower(): key for key in tree}
	clash = taken.get(f"{name}{suffix}".lower()) or taken.get(name.lower())
	if clash:
		# The record that exists, in its own capitals, not what was typed.
		frappe.throw(_("There is already a location called {0}.").format(clash))
	spec = rules.KINDS[kind]
	count = max(0, min(cint(children), rules.MAX_CHILDREN)) if spec["children"] else 0

	made = _insert(name, parent, spec["is_group"], company)
	created = [made]
	if count:
		child = rules.KINDS[spec["children"]]
		names, used = [], set(taken.values()) | {made}
		for _n in range(count):
			child_name = rules.suggest_name(names, name, child["word"], taken=used, suffix=suffix)
			if not child_name:
				break
			done = _insert(child_name, made, child["is_group"], company)
			names.append(child_name)
			used.add(done)
			created.append(done)

	tree = _tree()
	kids = rules.children_index(tree)
	is_group = bool(spec["is_group"])
	shown = rules._name_of(tree, made) if made in tree else name
	answer = _levels_answer(made, tree, rules.levels(made, tree, kids)) if made in tree else {}
	# What the new places and the parent can take now, so the editor's buttons follow at once (an empty
	# group that has just had a shelf made in it no longer takes a bin).
	places = {name: rules.takes(name, tree, kids) for name in [parent, *created] if name in tree and rules.is_group(tree[name])}
	trail = rules.trail(made, tree) if made in tree else ""
	return {
		"warehouse": made,
		"warehouse_name": shown,
		"parent": parent,
		"is_group": is_group,
		"trail": trail,
		"created": created,
		"unit": {
			"warehouse": made,
			"warehouse_name": shown,
			"is_group": is_group,
			"bins": len(rules.leaves_under(made, tree, kids)) if is_group else 1,
			# A new bay is a rack whatever is in it yet; ``units`` would call an empty group a room.
			"suggested_type": rules.SHAPE_RACK if kind == rules.KIND_BAY else rules.SHAPE_ROOM,
			"trail": trail,
			"on_map": None,
			"via": [],
		},
		"levels": answer.get("levels") or [],
		"names": answer.get("names") or {},
		"places": places,
	}


# ---------------------------------------------------------------------------
# The walk for a count (v1.821.0, slice 3)
# ---------------------------------------------------------------------------

#: The most bins one floor's walk lists. Past this the walk says it was cut. The Count Desk takes 400
#: bins in one count; a floor here has about 220.
WALK_LIMIT = 600


def _walk_items(row):
	"""The items expected in one bin: Stock Scan's own list (``api.stock_scan._items_at``, uncapped,
	the list the Count Desk's sheet is made from), in the few words a walk needs."""
	listing = scan._items_at(row, limit=None)[0]
	return [
		{
			"item_code": item.get("item_code"),
			"item_name": item.get("item_name") or item.get("item_code"),
			"stock_uom": item.get("stock_uom") or "",
			"on_hand": flt(item.get("on_hand")),
			"assigned": bool(item.get("assigned")),
		}
		for item in listing or []
	]


@frappe.whitelist(methods=["GET"])
def walk(name):
	"""One floor's walk for a count, bin by bin.

	``{"map", "floors", "stops", "bins", "truncated"}``: ``floors`` is every map the caller may
	see, in the page's order, so the walk goes on to the next floor; ``stops`` is the floor's boxes
	in walk order (``warehouse_map_rules.walk_order``: an editor's order, else rows in turn, each
	the other way), each with its ``rows`` as a person facing the box reads them (top row first,
	left bin first, or as the box says) and every bin with the items expected there. A bin shown by
	two boxes is walked once, at the first.

	Who: the same as the map (the Stock Scan gate, read on the map). What it shows is what Stock
	Scan already shows those roles about a bin. It writes nothing: a count is recorded in the Count
	Desk or on the phone, as before.
	"""
	_check_read()
	doc = _map_doc(name)
	tree = _tree()
	kids = rules.children_index(tree)
	rows = list(doc.get("shapes") or [])
	stops, seen, count, truncated = [], set(), 0, False
	for index in rules.walk_order(rows):
		row = rows[index]
		node = tree.get(row.warehouse)
		if not rules.usable(node):
			continue
		if rules.is_group(node):
			levels = rules.levels(row.warehouse, tree, kids, rules.clean_order(row.get("bin_order")) or None)
			seen_rows = rules.rows_as_seen(levels, row.shelves_from or rules.TOP, row.bins_from or rules.LEFT)
		else:
			seen_rows = [{"warehouse": row.warehouse, "label": "", "bins": [row.warehouse]}]
		out_rows = []
		for level in seen_rows:
			bins = []
			for leaf in level["bins"]:
				if leaf in seen or leaf not in tree:
					continue
				if count >= WALK_LIMIT:
					truncated = True
					break
				seen.add(leaf)
				count += 1
				bins.append({"warehouse": leaf, "warehouse_name": rules._name_of(tree, leaf), "items": _walk_items(tree[leaf])})
			if bins:
				out_rows.append({"warehouse": level["warehouse"], "label": level["label"], "bins": bins})
			if truncated:
				break
		if out_rows:
			stops.append(
				{
					"warehouse": row.warehouse,
					"warehouse_name": rules._name_of(tree, row.warehouse),
					"label": row.label or "",
					"trail": rules.trail(row.warehouse, tree),
					"rows": out_rows,
				}
			)
		if truncated:
			break
	return {
		"map": {"name": doc.name, "title": doc.map_name or doc.name},
		"floors": [{"name": m.name, "title": m.map_name or m.name} for m in _map_rows(can_edit())],
		"stops": stops,
		"bins": count,
		"truncated": truncated,
	}


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
	boxes: it replaces what is stored). ``modified`` is the stamp the editor was opened with and is
	required for an existing map; a record changed since is refused rather than overwritten. A new
	``map_name`` on an existing map renames it. Answers ``{"name", "modified"}``.
	"""
	_require_edit()
	data = _payload(payload)
	name = cstr(name).strip()
	title = rules.text(data.get("map_name"))

	if name:
		if not frappe.db.exists(MAP, name):
			frappe.throw(_("There is no map called {0}.").format(name))
		doc = frappe.get_doc(MAP, name)
		if not cstr(modified).strip():
			frappe.throw(_("The map could not be read. Reload the page and try again."))
		if cstr(doc.modified) != cstr(modified):
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
			# A change of capitals only ("shop floor" to "Shop Floor") finds its own row here: the
			# database compares names without regard to case.
			if title.lower() != doc.name.lower() and frappe.db.exists(MAP, title):
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
		# Cleaned here as well as in the DocType's validate, and before the rows exist: Frappe
		# checks a row's Links before validate runs, so this is where a box tied to a deleted
		# warehouse gets the app's sentence, and where a value that is not text is made text.
		width, height = rules.canvas_size(doc.width, doc.height)
		known = set(frappe.get_all("Warehouse", pluck="name"))
		try:
			cleaned = rules.clean_shapes(
				[shape if isinstance(shape, dict) else {} for shape in shapes], width, height, known
			)
		except rules.MapError as error:
			frappe.throw(_(str(error)))
		doc.set("shapes", [])
		for shape in cleaned:
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
