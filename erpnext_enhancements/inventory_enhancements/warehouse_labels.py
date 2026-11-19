# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The reads behind the printable warehouse QR labels (``www/warehouse-labels.html``).

A label goes on every warehouse that can hold stock: a **leaf** (``is_group = 0``), not
disabled, and not a ``Transit`` warehouse — goods in transit sit on a truck, not a shelf.
On production that is the ``Bin A1-3-1 - SF`` family under Inventory Room → Row → Bay → Shelf
(the group was called Inventory until v1.823.0), plus the Project Shelf (``Stores - SF`` until
v1.817.0), ``Receiving - SF`` (``Inventory Room - SF`` until v1.823.0) and the other standalone
rooms. A label printed under a name its warehouse no longer has must be printed again. The old
Inventory Room label is one: it opens the group that took the name, and Stock Scan says a label
that opens a group is out of date.

Since v1.833.0 it also reads the **rack QR sheet** (``www/rack-qr-sheet.html``, :func:`rack_sheets`):
one printed page per rack listing each shelf's code with its position, to hang low beside a rack
whose shelves are too high or too thin for a sticker. The same codes; the layout is
:mod:`rack_sheet_rules`.

Each label carries the warehouse's own name, the breadcrumb of its group parents, and a
QR code of ``<site>/stock-scan?w=<name>`` (:func:`stock_scan_rules.scan_url`), drawn by
:mod:`qr_svg`. The judgements — the URL, the breadcrumb, the sort, the sheet geometry —
are in :mod:`stock_scan_rules` and tested bench-free; this module only reads.
"""

import frappe
from frappe.utils import get_url

from erpnext_enhancements.inventory_enhancements import qr_svg, rack_sheet_rules, warehouse_map_rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules


def _tree():
	"""Every warehouse's ``(warehouse_name, parent_warehouse, is_group)``, keyed by name."""
	rows = frappe.get_all(
		"Warehouse",
		fields=[
			"name",
			"warehouse_name",
			"parent_warehouse",
			"is_group",
			"disabled",
			"warehouse_type",
			"lft",
			"rgt",
		],
		order_by="lft asc",
	)
	return {row.name: row for row in rows}


def ancestors(name, tree):
	"""The group parents of ``name``, outermost first, as ``warehouse_name``."""
	chain = []
	seen = set()
	parent = tree[name].parent_warehouse if name in tree else None
	while parent and parent in tree and parent not in seen:
		seen.add(parent)
		chain.append(tree[parent].warehouse_name or parent)
		parent = tree[parent].parent_warehouse
	return list(reversed(chain))


def group_options():
	"""The group warehouses a sheet can be limited to, in tree order, indented by depth."""
	tree = _tree()
	options = []
	for name, row in tree.items():
		if not row.is_group or row.disabled:
			continue
		depth = len(ancestors(name, tree))
		options.append({"value": name, "label": (" " * depth) + (row.warehouse_name or name)})
	return options


def label_rows(under=None, names=None):
	"""The labels to print, naturally sorted by breadcrumb then name.

	``names`` (explicit warehouses, e.g. from one Warehouse form) wins over ``under`` (a
	group warehouse whose leaves are wanted). With neither, every stock-holding location.
	A name that is not a stock-holding location is skipped rather than printed: a label
	on a group warehouse would open a page that can never hold stock.
	"""
	tree = _tree()
	base_url = get_url()

	if names:
		wanted = [name for name in dict.fromkeys(names) if name in tree]
	else:
		wanted = list(tree)
		if under and under in tree:
			top = tree[under]
			wanted = [name for name in wanted if tree[name].lft > top.lft and tree[name].rgt < top.rgt]

	rows = []
	for name in wanted:
		row = tree[name]
		if row.is_group or row.disabled or (row.warehouse_type or "") == "Transit":
			continue
		trail_parts = ancestors(name, tree)
		title = row.warehouse_name or name
		url = rules.scan_url(base_url, name)
		rows.append(
			{
				"warehouse": name,
				"title": title,
				"trail": rules.location_trail(trail_parts),
				"url": url,
				"svg": qr_svg.qr_svg(url, title=title),
				"_sort": [rules.natural_key(part) for part in trail_parts] + [rules.natural_key(title)],
			}
		)
	rows.sort(key=lambda row: row["_sort"])
	for row in rows:
		row.pop("_sort", None)
	return rows


def _map_boxes():
	"""The Warehouse Map's boxes, for a rack's shelf order and whether a group is a room. ``{}`` when
	the map's doctype is not installed or holds nothing: every group is then a rack with shelf 1 at
	the top."""
	try:
		shapes = frappe.get_all(
			"Warehouse Floor Map Shape",
			filters={"parenttype": "Warehouse Floor Map"},
			fields=["warehouse", "shape_type", "shelves_from", "bins_from", "bin_order"],
			order_by="parent asc, idx asc",
		)
	except Exception:
		return {}
	return rack_sheet_rules.box_settings(shapes)


def rack_sheets(names):
	"""The pages of the rack QR sheet for the group warehouses ``names``, in the order given.

	Each page is ``{"rack", "title", "trail", "how_to_read", "rows", "layout"}``; each row carries
	``shelf``, ``label``, ``position_words`` and ``codes`` (``warehouse``, ``title``, ``url``, ``svg``).
	A name that is not an enabled group is skipped; a group whose map box is a Room gives one page per
	rack inside it, and one named after the room for the shelves standing in it loose
	(:func:`rack_sheet_rules.racks_for`). ``fits`` False on a page's ``layout`` means it runs onto a
	second sheet; the template repeats its header there.
	"""
	tree = _tree()
	kids = warehouse_map_rules.children_index(tree)
	boxes = _map_boxes()
	base_url = get_url()
	pages, seen = [], set()
	for selected in dict.fromkeys(names or ()):
		for entry in rack_sheet_rules.racks_for(selected, tree, boxes, kids):
			rack, loose = entry["rack"], entry["loose"]
			if (rack, loose) in seen:
				continue
			seen.add((rack, loose))
			box = boxes.get(rack) or {}
			rows = rack_sheet_rules.rack_rows(rack, tree, kids, box, loose_only=loose)
			out_rows = []
			for row in rows:
				codes = []
				for location in row["locations"]:
					title = tree[location].warehouse_name or location
					url = rules.scan_url(base_url, location)
					codes.append(
						{
							"warehouse": location,
							"title": title,
							"url": url,
							"svg": qr_svg.qr_svg(url, title=title),
						}
					)
				out_rows.append(
					{
						"shelf": row["shelf"],
						"label": row["label"],
						"position_words": rack_sheet_rules.position_words(row),
						# The shelf is itself the one location on it (the Atrium's shelves): no name over its code.
						"is_location": row["locations"] == [row["warehouse"]],
						"codes": codes,
					}
				)
			pages.append(
				{
					"rack": rack,
					"loose": loose,
					"title": tree[rack].warehouse_name or rack,
					"trail": rules.location_trail(ancestors(rack, tree)),
					"how_to_read": rack_sheet_rules.how_to_read(
						box.get("shelves_from") or rack_sheet_rules.TOP
					),
					"rows": out_rows,
					"layout": rack_sheet_rules.layout(rows),
				}
			)
	return pages
