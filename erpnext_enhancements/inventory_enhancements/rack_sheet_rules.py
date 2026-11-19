# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The printable rack QR sheet: which racks, which shelves in what order, how big (v1.833.0). No Frappe.

From the 2026-11-19 operations review: the north-facing Atrium shelves are too high, and their side
blocks too thin, for a QR sticker. So each rack gets one printed page listing every shelf's QR code
with its position, laminated and hung low beside the rack. ``www/rack-qr-sheet.html`` prints it; the
Warehouse form's **Rack QR Sheet** button opens it on a group warehouse.

**The codes are the labels' codes.** Each one encodes ``<site>/stock-scan?w=<warehouse>``
(``stock_scan_rules.scan_url``), drawn by ``qr_svg``, so a phone's camera opens Stock Scan on that
shelf exactly as a sticker on the shelf would, and the in-page scanner reads it the same way.

**What a rack's rows are.** The rack's own children, in the order a person sees them
(``warehouse_map_rules.children_index``: ``Shelf NLR1-2`` before ``Shelf NLR1-10``):

* a child that is a location (a leaf: the Atrium and Build Room shelves, ``Shelf NLR1-1``,
  ``Shelf BR-NR1-1``) is one shelf with one code;
* a child that is a group (a bay's ``Shelf B1-2``) is one shelf with a code for every location under
  it, left to right.

**Shelf 1 is the top shelf** (Nik, 2026-11-12, and the map's default); the sheet lists rows top to
bottom as you face the rack. **A shelf's number is the one its name ends in** (``Shelf NLR1-3`` is
shelf 3), so a disabled shelf in the middle leaves a gap instead of renumbering the shelves below it
beside codes captioned with their real names; a shelf whose name ends in no number takes its place in
the order. Where the Warehouse Map has a box for the rack, the box decides, as the map's own front
view does (``warehouse_map_rules.rows_as_seen``): *Shelf 1 is at the* Bottom turns the rows over,
*Bin 1 is at the* Right turns each row around, and the box's own order (*Order As You Face It*) comes
first.

**One page per rack.** The group the button is pressed on is the rack. A group whose map box is a
**Room** (production's Atrium and Build Room) prints one page for each group inside it instead, so
the Atrium's button prints its six racks, and one more page, named after the room, for any shelves
standing in the room loose rather than on a rack. A disabled or Transit location is left off, as on
the labels.

**How big.** The codes are as large as the page allows, up to :data:`MAX_QR` (1.8 in, readable from
a step back) and never below :data:`MIN_QR` (0.8 in, about a shelf sticker's code). A shelf with many
locations wraps onto more lines; a rack that cannot fit on one page at the smallest size runs onto a
second page, a shelf never split between them, and the rack's name and how to read the sheet repeat
at the top of every page it takes.

Tabs, per ``CLAUDE.md`` (new file).
"""

from __future__ import annotations

import math
import re

from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as map_rules

ROOM = map_rules.SHAPE_ROOM
TOP = map_rules.TOP
BOTTOM = map_rules.BOTTOM
LEFT = map_rules.LEFT
RIGHT = map_rules.RIGHT

#: The printed page: US Letter, portrait, with a margin a home or office printer can reach.
PAGE_WIDTH = 8.5
PAGE_HEIGHT = 11.0
MARGIN = 0.4
#: Room on each row for the shelf number, and the codes' share of the width.
POSITION_WIDTH = 1.25
CODES_WIDTH = PAGE_WIDTH - 2 * MARGIN - POSITION_WIDTH - 0.25
#: The page's header (rack name, room, how to read it) and footer, and the rows' space between them.
HEADER_HEIGHT = 1.25
FOOTER_HEIGHT = 0.35
ROWS_HEIGHT = PAGE_HEIGHT - 2 * MARGIN - HEADER_HEIGHT - FOOTER_HEIGHT
#: Under each code: the location's name. Over a shelf of several locations: the shelf's name.
CAPTION_HEIGHT = 0.32
NAME_HEIGHT = 0.22
#: Between codes on a line, and between rows.
GAP = 0.18
ROW_PADDING = 0.16
MAX_QR = 1.8
MIN_QR = 0.8


def _get(row, key, default=None):
	return map_rules._get(row, key, default)


def box_settings(shapes):
	"""``{warehouse: {"shape_type", "shelves_from", "bins_from", "order"}}`` from the Warehouse Map's
	boxes (``Warehouse Floor Map Shape`` rows). A warehouse placed twice keeps its first box."""
	out = {}
	for shape in shapes or ():
		warehouse = map_rules.text(_get(shape, "warehouse"))
		if not warehouse or warehouse in out:
			continue
		out[warehouse] = {
			"shape_type": map_rules.text(_get(shape, "shape_type")),
			"shelves_from": BOTTOM if map_rules.text(_get(shape, "shelves_from")) == BOTTOM else TOP,
			"bins_from": RIGHT if map_rules.text(_get(shape, "bins_from")) == RIGHT else LEFT,
			"order": map_rules.clean_order(_get(shape, "bin_order")) or None,
		}
	return out


def _usable_group(name, tree):
	row = tree.get(name)
	return row is not None and map_rules.is_group(row) and map_rules.usable(row)


def racks_for(selected, tree, boxes, kids=None):
	"""The pages a sheet for ``selected`` prints, in order, as ``[{"rack", "loose"}]``.

	``selected`` itself (``loose`` False), or, when its map box is a Room, every group inside it that holds
	a location, after a first page for the room itself (``loose`` True) when locations stand in the room
	directly, off any rack: that page lists only those. ``[]`` for a location or an unknown name.
	"""
	if not _usable_group(selected, tree):
		return []
	kids = map_rules.children_index(tree) if kids is None else kids
	box = (boxes or {}).get(selected) or {}
	if box.get("shape_type") != ROOM:
		return [{"rack": selected, "loose": False}]
	children = [c for c in kids.get(selected, []) if map_rules.usable(tree.get(c))]
	pages = []
	if any(not map_rules.is_group(tree[c]) for c in children):
		pages.append({"rack": selected, "loose": True})
	pages.extend(
		{"rack": child, "loose": False}
		for child in children
		if map_rules.is_group(tree[child]) and map_rules.leaves_under(child, tree, kids)
	)
	return pages


_TRAILING_NUMBER = re.compile(r"(\d+)\s*$")


def shelf_number(label):
	"""The number a shelf's name ends in (``Shelf NLR1-3`` → 3), or ``None``."""
	match = _TRAILING_NUMBER.search(map_rules.text(label))
	return int(match.group(1)) if match else None


def rack_rows(rack, tree, kids=None, box=None, loose_only=False):
	"""One rack's shelves, top to bottom as you face it.

	Each row is ``{"shelf", "warehouse", "label", "locations", "position"}``: ``shelf`` the shelf's
	number (the number its name ends in, else its place in the rack's order, 1 first), ``locations`` the
	warehouses to print a code for, left to right, and ``position`` ``"top"``, ``"bottom"`` or ``""`` (one
	shelf, or one between). ``loose_only``: only the locations standing directly in ``rack`` (a room's
	shelves that are on no rack), never its groups.
	"""
	if not _usable_group(rack, tree):
		return []
	kids = map_rules.children_index(tree) if kids is None else kids
	box = box or {}
	order = box.get("order")
	children = map_rules.ordered([c for c in kids.get(rack, []) if map_rules.usable(tree.get(c))], order)
	if loose_only:
		children = [c for c in children if not map_rules.is_group(tree[c])]
	rows = []
	for place, child in enumerate(children, start=1):
		label = map_rules.text(_get(tree[child], "warehouse_name")) or child
		named = shelf_number(label)
		number = named if named is not None else place
		if map_rules.is_group(tree[child]):
			locations = map_rules.ordered(map_rules.leaves_under(child, tree, kids), order)
			if box.get("bins_from") == RIGHT:
				locations = list(reversed(locations))
		else:
			locations = [child]
		rows.append(
			{
				"shelf": number,
				"warehouse": child,
				"label": label,
				"locations": locations,
				"position": "",
			}
		)
	if box.get("shelves_from") == BOTTOM:
		rows.reverse()
	if len(rows) > 1:
		rows[0]["position"] = "top"
		rows[-1]["position"] = "bottom"
	return rows


def layout(rows):
	"""``{"qr", "per_line", "lines", "fits"}``: the side of every code on this sheet in inches, how many
	codes share a line, how many lines the rows take, and whether they fit on one page."""
	most = max((len(r.get("locations") or []) for r in rows or ()), default=0)
	if not most:
		return {"qr": MAX_QR, "per_line": 1, "lines": len(rows or ()), "fits": True}
	# As many to a line as fit at the smallest size, but never more than the busiest shelf has.
	fit = max(1, int((CODES_WIDTH + GAP) // (MIN_QR + GAP)))
	per_line = min(most, fit)
	by_width = (CODES_WIDTH - (per_line - 1) * GAP) / per_line
	lines = sum(max(1, math.ceil(len(r.get("locations") or []) / per_line)) for r in rows)
	# A shelf that is not itself the one location on it carries its name over its codes.
	named = sum(1 for r in rows if (r.get("locations") or []) != [r.get("warehouse")])
	room = ROWS_HEIGHT - len(rows) * ROW_PADDING - named * NAME_HEIGHT
	by_height = room / lines - CAPTION_HEIGHT - GAP
	qr = min(MAX_QR, by_width, by_height)
	fits = qr >= MIN_QR - 1e-9
	qr = max(MIN_QR, qr)
	return {"qr": round(math.floor(qr * 100) / 100, 2), "per_line": per_line, "lines": lines, "fits": fits}


def position_words(row):
	"""The words beside a shelf's number: ``"Top shelf"``, ``"Bottom shelf"`` or ``""``."""
	position = row.get("position")
	if position == "top":
		return "Top shelf"
	if position == "bottom":
		return "Bottom shelf"
	return ""


def how_to_read(shelves_from=TOP):
	"""The sentence under the rack's name."""
	end = "bottom" if shelves_from == BOTTOM else "top"
	return (
		f"Scan the code for the shelf you take from or add to. Shelf 1 is the {end} shelf; "
		"each code opens Stock Scan on that shelf."
	)
