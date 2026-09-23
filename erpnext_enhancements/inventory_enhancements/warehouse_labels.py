# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The reads behind the printable warehouse QR labels (``www/warehouse-labels.html``).

A label goes on every warehouse that can hold stock: a **leaf** (``is_group = 0``), not
disabled, and not a ``Transit`` warehouse — goods in transit sit on a truck, not a shelf.
On production that is the ``Bin A1-3-1 - SF`` family under Inventory → Row → Bay → Shelf,
plus ``Stores - SF``, ``Inventory Room - SF`` and the other standalone rooms.

Each label carries the warehouse's own name, the breadcrumb of its group parents, and a
QR code of ``<site>/stock-scan?w=<name>`` (:func:`stock_scan_rules.scan_url`), drawn by
:mod:`qr_svg`. The judgements — the URL, the breadcrumb, the sort, the sheet geometry —
are in :mod:`stock_scan_rules` and tested bench-free; this module only reads.
"""

import frappe
from frappe.utils import get_url

from erpnext_enhancements.inventory_enhancements import qr_svg
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
