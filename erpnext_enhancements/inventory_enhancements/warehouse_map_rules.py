# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every judgement the Warehouse Map makes (v1.815.0). No Frappe, no I/O.

Nik, 2026-11-12: "we need a map for the location bins and a way to edit and create the map for
our warehouse locations at our office."

A map is one floor or area: a picture of the floor plan with boxes placed over it. A box stands
for a Warehouse. What is *inside* a box is never drawn by hand: a box tied to a bay shows that
bay's shelves and bins because the Warehouse tree already says which bins are on which shelf of
which bay. So a person places about thirty boxes and the map shows more than two hundred bins,
and a bin added in ERPNext later appears inside its bay with nobody touching the map.

What is decided here:

* :func:`clean_shape` / :func:`clean_shapes` -- what a box may be. The page sends whatever the
  editor holds; only the fields named here are kept, every number is made finite and held inside
  the drawing, and a box that cannot mean anything (a rack tied to nothing, a label with no
  words) is refused in a sentence that says which box.
* :func:`levels` -- a box's front view: the rows a person sees standing in front of it. Each
  group directly under the box's warehouse is one row (a shelf), holding every bin below it;
  bins directly under the box's own warehouse are a first row with no name. A shelf with no bins
  is kept: it is there in the building, and an empty row says so.
* :func:`units` -- the boxes worth placing: the highest group that is at most two steps above its
  bins (a bay over shelves over bins; a row of alcoves over bins) and every bin that hangs
  higher than that (a room). It is only a suggestion list for the editor; any warehouse can be
  placed.
* :func:`locate` -- which map and which box show a warehouse: the box for it, or else for the
  nearest group above it.
* :func:`bin_status` / :func:`summarize` -- a bin's color, from the same three counts the Bin
  Manager shows: a problem (an assigned item with none on hand, or stock nobody assigned),
  else has stock, else empty.

The floor plan picture is a real building's layout and this app's code is public, so the picture
is site data: :func:`background_problem` accepts a private file on this site and nothing else.

Indentation is tabs (new file).
"""

import math

from erpnext_enhancements.inventory_enhancements.stock_scan_rules import natural_key

SHAPE_RACK = "Rack"
SHAPE_ROOM = "Room"
SHAPE_LABEL = "Label"
SHAPE_TYPES = (SHAPE_RACK, SHAPE_ROOM, SHAPE_LABEL)

TOP = "Top"
BOTTOM = "Bottom"
LEFT = "Left"
RIGHT = "Right"

#: The role that may create and change maps (with System Manager). Everyone with a Stock Scan role
#: may look. Granted in the Desk; ``patches/seed_warehouse_map_editor_role`` only creates it.
EDITOR_ROLE = "Warehouse Map Editor"

DEFAULT_WIDTH = 1200.0
DEFAULT_HEIGHT = 800.0
MIN_CANVAS = 200.0
MAX_CANVAS = 20000.0
#: The smallest side a box may have, in map units: smaller cannot be tapped or picked up again.
MIN_SIZE = 6.0
MAX_SHAPES = 500
MAX_LABEL = 60
MAX_NAME = 60

#: A floor plan picture must be a private file on this site.
PRIVATE_PREFIX = "/private/files/"

#: A map's name is one part of the page's address, so it cannot hold what splits an address.
_NAME_REFUSED = "/\\?#%&"

STATUS_PROBLEM = "problem"
STATUS_STOCK = "stock"
STATUS_EMPTY = "empty"

#: How far above its bins a group may be and still be suggested as one box.
UNIT_HEIGHT = 2


class MapError(ValueError):
	"""A map or a box the rules refuse. The message is a sentence for the person."""


def text(value):
	return "" if value is None else str(value).strip()


def number(value, default=None):
	"""``value`` as a finite float, or ``default``."""
	try:
		out = float(value)
	except (TypeError, ValueError):
		return default
	return out if math.isfinite(out) else default


def _get(row, key, default=None):
	if row is None:
		return default
	getter = getattr(row, "get", None)
	if callable(getter):
		value = getter(key)
		return default if value is None else value
	return getattr(row, key, default)


# ---------------------------------------------------------------------------
# The map itself
# ---------------------------------------------------------------------------


def map_name_problem(name):
	"""Why ``name`` cannot be a map's name, or ``None``."""
	name = text(name)
	if not name:
		return "Give the map a name, like Shop Floor or Upstairs."
	if len(name) > MAX_NAME:
		return f"A map's name can be at most {MAX_NAME} characters."
	if any(ch in name for ch in _NAME_REFUSED):
		return "A map's name cannot contain / \\ ? # % or &."
	return None


def canvas_size(width, height):
	"""The drawing's size, held between :data:`MIN_CANVAS` and :data:`MAX_CANVAS`; a missing or
	zero side gets the default."""
	w = number(width) or DEFAULT_WIDTH
	h = number(height) or DEFAULT_HEIGHT
	return (
		round(min(max(w, MIN_CANVAS), MAX_CANVAS), 1),
		round(min(max(h, MIN_CANVAS), MAX_CANVAS), 1),
	)


def background_problem(url):
	"""Why ``url`` cannot be a map's floor plan picture, or ``None``.

	Only a private file on this site. A public file is readable by anyone who has its address
	with no sign-in, and a web address would send every viewer's browser to somebody else's
	server; the plan is a real building's layout, so neither.
	"""
	url = text(url)
	if not url:
		return None
	if not url.startswith(PRIVATE_PREFIX) or ".." in url:
		return (
			"Upload the floor plan as a private file, so only people signed in here can see it. "
			"A public file or a web address is not accepted."
		)
	return None


# ---------------------------------------------------------------------------
# Boxes
# ---------------------------------------------------------------------------


def clean_shape(raw, width, height, index=0):
	"""One box as it is stored, from whatever the editor sent. Raises :class:`MapError`."""
	n = index + 1
	kind = text(_get(raw, "shape_type")) or SHAPE_RACK
	if kind not in SHAPE_TYPES:
		raise MapError(f"Box {n}: a box is a rack, a room or a label.")
	warehouse = text(_get(raw, "warehouse"))
	label = text(_get(raw, "label"))[:MAX_LABEL]
	if kind == SHAPE_LABEL:
		warehouse = ""
		if not label:
			raise MapError(f"Box {n}: a label needs its words.")
	elif kind == SHAPE_RACK and not warehouse:
		raise MapError(f"Box {n}: a rack needs the row, bay or shelf it stands for.")
	elif kind == SHAPE_ROOM and not (warehouse or label):
		raise MapError(f"Box {n}: a room needs a name or a warehouse.")

	w = number(_get(raw, "w"))
	h = number(_get(raw, "h"))
	x = number(_get(raw, "x"))
	y = number(_get(raw, "y"))
	if w is None or h is None or x is None or y is None:
		raise MapError(f"Box {n}: its place or size is not a number.")
	w = min(max(w, MIN_SIZE), width)
	h = min(max(h, MIN_SIZE), height)
	x = min(max(x, 0.0), width - w)
	y = min(max(y, 0.0), height - h)
	rotation = (number(_get(raw, "rotation"), 0.0) or 0.0) % 360.0
	return {
		"shape_type": kind,
		"warehouse": warehouse or None,
		"label": label or None,
		"x": round(x, 1),
		"y": round(y, 1),
		"w": round(w, 1),
		"h": round(h, 1),
		"rotation": round(rotation, 1) % 360.0,
		"shelves_from": BOTTOM if text(_get(raw, "shelves_from")) == BOTTOM else TOP,
		"bins_from": RIGHT if text(_get(raw, "bins_from")) == RIGHT else LEFT,
	}


def clean_shapes(rows, width, height, known=None):
	"""Every box of one map, cleaned, in the order given.

	Refused: more than :data:`MAX_SHAPES`; one warehouse on two boxes (a tap on it would have two
	answers); and, when ``known`` (the warehouses that exist) is given, a box tied to a warehouse
	that is not there.
	"""
	rows = list(rows or [])
	if len(rows) > MAX_SHAPES:
		raise MapError(f"A map can hold at most {MAX_SHAPES} boxes. Split it into two maps.")
	out, seen = [], {}
	for index, raw in enumerate(rows):
		shape = clean_shape(raw, width, height, index)
		warehouse = shape["warehouse"]
		if warehouse:
			if warehouse in seen:
				raise MapError(
					f"{warehouse} is on this map twice (boxes {seen[warehouse] + 1} and {index + 1}). "
					"Remove one of them."
				)
			seen[warehouse] = index
			if known is not None and warehouse not in known:
				raise MapError(
					f"Box {index + 1}: {warehouse} is not a warehouse any more. "
					"Remove the box or tie it to another warehouse."
				)
		out.append(shape)
	return out


def elsewhere_problem(shapes, placed_elsewhere):
	"""Why these boxes clash with the other maps, or ``None``. ``placed_elsewhere`` is
	``{warehouse: map name}`` for every other map."""
	for shape in shapes or []:
		warehouse = _get(shape, "warehouse")
		if warehouse and warehouse in (placed_elsewhere or {}):
			return (
				f"{warehouse} is already on the map {placed_elsewhere[warehouse]}. "
				"A warehouse can be on one map only; remove it there first."
			)
	return None


# ---------------------------------------------------------------------------
# The warehouse tree
# ---------------------------------------------------------------------------


def _name_of(tree, name):
	return text(_get(tree.get(name), "warehouse_name")) or name


def is_group(row):
	return bool(int(number(_get(row, "is_group"), 0) or 0))


def usable(row):
	"""Whether a warehouse is drawn at all: not disabled, and not Transit (goods in transit sit
	on a truck, not a shelf; the labels page leaves them out for the same reason)."""
	if row is None:
		return False
	if int(number(_get(row, "disabled"), 0) or 0):
		return False
	return text(_get(row, "warehouse_type")) != "Transit"


def children_index(tree):
	"""``{parent: [children]}``, each list in shelf-number order (``Bin B1-2-9`` before
	``Bin B1-2-10``). The tree's own order is creation order, which on production already reads
	``Bin C2-3-6`` before ``Bin C2-3-5``."""
	out = {}
	for name, row in tree.items():
		parent = text(_get(row, "parent_warehouse"))
		out.setdefault(parent if parent in tree else "", []).append(name)
	for parent in out:
		out[parent].sort(key=lambda n: (natural_key(_name_of(tree, n)), n))
	return out


def leaves_under(name, tree, kids):
	"""Every usable bin under ``name`` (or ``name`` itself when it is one), in shelf order."""
	out, stack, seen = [], [name], set()
	while stack:
		at = stack.pop()
		if at in seen or at not in tree:
			continue
		seen.add(at)
		row = tree[at]
		if not usable(row):
			continue
		if not is_group(row):
			out.append(at)
			continue
		stack.extend(reversed(kids.get(at, [])))
	return out


def levels(name, tree, kids=None):
	"""A box's front view: ``[{"warehouse", "label", "bins": [...]}]``, top row first as the
	tree numbers them. Empty for a bin (a room that is one bin has nothing inside to show)."""
	row = tree.get(name)
	if row is None or not is_group(row):
		return []
	kids = children_index(tree) if kids is None else kids
	direct, groups = [], []
	for child in kids.get(name, []):
		child_row = tree[child]
		if not usable(child_row):
			continue
		(groups if is_group(child_row) else direct).append(child)
	out = []
	if direct:
		out.append({"warehouse": name, "label": "", "bins": direct})
	for group in groups:
		out.append({"warehouse": group, "label": _name_of(tree, group), "bins": leaves_under(group, tree, kids)})
	return out


def _heights(tree, kids):
	"""How many steps each warehouse is above its lowest bin: a bin 0, a shelf of bins 1."""
	out = {}

	def height(name, guard):
		if name in out:
			return out[name]
		if name in guard or not is_group(tree[name]):
			out[name] = 0
			return 0
		guard = guard | {name}
		out[name] = 1 + max((height(child, guard) for child in kids.get(name, [])), default=0)
		return out[name]

	for name in tree:
		height(name, frozenset())
	return out


def units(tree, skip=()):
	"""The boxes worth placing, in tree order: ``[{"warehouse", "warehouse_name", "is_group",
	"bins", "suggested_type"}]``.

	Walking down from each root: a group at most :data:`UNIT_HEIGHT` steps above its bins is one
	box (a bay), and nothing below it is listed; a bin met on the way down is its own box (a
	room). A root is never a box: on a company whose warehouses are all directly under the root,
	the root would otherwise swallow them. ``skip`` names warehouses to leave out with everything
	below them (vehicles and job sites: they are not in the building).
	"""
	skip = set(skip or ())
	kids = children_index(tree)
	heights = _heights(tree, kids)
	out = []

	def walk(name, is_root):
		row = tree[name]
		if name in skip or not usable(row):
			return
		group = is_group(row)
		if group and (is_root or heights[name] > UNIT_HEIGHT):
			for child in kids.get(name, []):
				walk(child, False)
			return
		bins = len(leaves_under(name, tree, kids)) if group else 1
		out.append(
			{
				"warehouse": name,
				"warehouse_name": _name_of(tree, name),
				"is_group": group,
				"bins": bins,
				"suggested_type": SHAPE_RACK if group and bins else SHAPE_ROOM,
			}
		)

	for root in kids.get("", []):
		walk(root, True)
	return out


def ancestors(name, tree):
	"""The groups above ``name``, nearest first."""
	out, seen = [], {name}
	at = text(_get(tree.get(name), "parent_warehouse"))
	while at and at in tree and at not in seen:
		seen.add(at)
		out.append(at)
		at = text(_get(tree[at], "parent_warehouse"))
	return out


def trail(name, tree):
	"""``Row 2 › Bay B1``: the groups above ``name``, outermost first, the tree's root dropped."""
	names = [_name_of(tree, a) for a in reversed(ancestors(name, tree))]
	return " › ".join(names[1:])


def descendants(name, tree, kids):
	out, stack, seen = [], list(kids.get(name, [])), {name}
	while stack:
		at = stack.pop()
		if at in seen:
			continue
		seen.add(at)
		out.append(at)
		stack.extend(kids.get(at, []))
	return out


def locate(warehouse, placed, tree, kids=None):
	"""Where ``warehouse`` is shown: ``{"map", "shape"}``, or ``None`` when no map shows it.

	``placed`` is ``{warehouse: map name}`` for every box on every map. The answer is the box for
	the warehouse itself, else the box for the nearest group above it (a bin is shown by its
	bay's box). A group nobody placed but whose parts are placed (a row whose bays are on the
	map) answers with the map that shows most of them and no box.
	"""
	warehouse = text(warehouse)
	if not warehouse or warehouse not in tree:
		return None
	for at in [warehouse, *ancestors(warehouse, tree)]:
		if at in placed:
			return {"map": placed[at], "shape": at}
	kids = children_index(tree) if kids is None else kids
	counts = {}
	for name in descendants(warehouse, tree, kids):
		if name in placed:
			counts[placed[name]] = counts.get(placed[name], 0) + 1
	if not counts:
		return None
	best = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
	return {"map": best, "shape": None}


def shown_by(warehouse, placed, tree, kids):
	"""How a unit is already on a map: ``{"map", "via"}``, ``via`` the boxes that show it (its
	own, the one for a group above it, or the ones for parts of it on that map). ``None`` means
	nothing of it is on any map yet. The editor reads ``via`` to know when a box removed since it
	opened puts the unit back on its list of things to place."""
	found = locate(warehouse, placed, tree, kids)
	if not found:
		return None
	if found["shape"]:
		return {"map": found["map"], "via": [found["shape"]]}
	parts = [name for name in descendants(warehouse, tree, kids) if placed.get(name) == found["map"]]
	return {"map": found["map"], "via": sorted(parts)}


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------


def _count(row, key):
	return int(number(_get(row, key), 0) or 0)


def bin_status(row):
	"""A bin's color. A problem is what the Bin Manager's Problems view lists for a bin: an
	assigned item with none on hand, or stock on hand that nobody assigned there."""
	if _count(row, "missing_items") > 0 or _count(row, "unassigned_items") > 0:
		return STATUS_PROBLEM
	if _count(row, "on_hand_items") > 0:
		return STATUS_STOCK
	return STATUS_EMPTY


def summarize(rows):
	"""A box's numbers from its bins' rows: how many bins, how many hold stock, how many have a
	problem, how many item lines are on hand, and the box's own color (a problem anywhere inside
	wins, so it can be seen from the floor view)."""
	rows = list(rows or [])
	stock = sum(1 for row in rows if _count(row, "on_hand_items") > 0)
	problem = sum(1 for row in rows if bin_status(row) == STATUS_PROBLEM)
	return {
		"bins": len(rows),
		"stock_bins": stock,
		"problem_bins": problem,
		"items": sum(_count(row, "on_hand_items") for row in rows),
		"status": STATUS_PROBLEM if problem else STATUS_STOCK if stock else STATUS_EMPTY,
	}
