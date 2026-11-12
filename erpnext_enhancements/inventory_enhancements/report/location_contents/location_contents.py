# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Location Contents: what is at a location, and what should be (v1.805.0).

Pick a bin, or a group (a shelf, a bay, a row, all of the inventory): one row per item that is
there or should be there, with on hand, on order, whether it is assigned there, and a plain
status. Leave the location blank for every location at once.

**The list is the Bin Manager's list.** For each location this report calls
``api.stock_scan._items_at``, the function behind the Bin Manager and Stock Scan's location
screen, with no row cap, and reads what it returns. It does not carry a copy of the listing rule,
so the two cannot disagree about which items belong to a bin: on hand there, assigned there by an
enabled Putaway Rule, its Item Default warehouse, on order to there, or a Bin row nobody has
decided on. Like the Bin Manager, that means stock items that are not disabled.

**What this report adds is the status**, ``inventory_report_rules.location_status``:

| Status | Means |
|---|---|
| As expected | on hand here, and assigned here |
| Assigned here, none on hand | the bin is this item's and is empty |
| Here, not assigned | on hand here, and no bin is this item's |
| Here, assigned to another bin | on hand here, and its bin is elsewhere (named in *Assigned To*) |
| On order to here | none on hand, and an order is on its way to this location |
| Empty, not decided | it was here once, is gone, and nobody has kept it on the bin or taken it off |
| Held here | on hand at the Project Shelf, a job site or the Boneyard, where nothing is assigned |
| Nothing here | listed at one of those three places with none on hand |

"Only problems" keeps four of them: assigned here with none on hand, here and not assigned, here
but assigned to another bin, and empty and not decided (``inventory_report_rules.PROBLEMS``).
The cards over the table count every row at the chosen locations, whatever the Status and "Only
problems" filters hide, so a short list still says how big the whole picture is.

**"Assigned to another bin" names real bins.** An enabled Putaway Rule anywhere else is the answer
when there is one. With none, the item's Item Default warehouse counts, unless it is a group, the
Project Shelf, a job site or the Boneyard.

**The Project Shelf, a job site and the Boneyard are never an item's bin**, so stock there is
"Held here" and not a problem. Before a Project Shelf is set
(``inventory_report_data.project_shelf``), no location is the shelf: the warehouse that will become
it is judged as an ordinary bin, and the note over the table says so.

**Cost.** ``_items_at`` is one query per location, each a pass over the stock items. Locations
with no Bin row, no enabled Putaway Rule and no Item Default pointing at them are skipped first,
since none of that function's clauses can list anything there (three small ``distinct`` queries
decide it). With an Item chosen, only the locations that item touches are asked. The whole
inventory is one call per bin that holds or expects something; if the item list grows a hundred
times over, the place to batch is ``_items_at`` itself, in ``api/stock_scan.py``.

**A Script Report** because every filter is optional (a Query Report's SQL fails on a blank one
on Frappe 16.53 and later). Opens at ``/desk/query-report/Location Contents``.
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import inventory_report_data as data
from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = data.places()
	rows = get_rows(filters, where)
	shown = [row for row in rows if keep(row, filters)]
	return get_columns(), shown, get_message(where), None, get_summary(rows)


def get_columns():
	return [
		{
			"label": _("Location"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
		{"label": _("Under"), "fieldname": "under", "fieldtype": "Data", "width": 130},
		{"label": _("Kind"), "fieldname": "kind", "fieldtype": "Data", "width": 105},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 140,
		},
		{"label": _("On Hand"), "fieldname": "on_hand", "fieldtype": "Float", "width": 90},
		{"label": _("On Order"), "fieldname": "on_order", "fieldtype": "Float", "width": 90},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Assigned Here"), "fieldname": "assigned_here", "fieldtype": "Data", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 220},
		{"label": _("Assigned To"), "fieldname": "other_bins", "fieldtype": "Data", "width": 200},
	]


def items_at(warehouse):
	"""The Bin Manager's list for one location: ``api.stock_scan._items_at`` with no row cap.
	Imported when asked: that module is the whole Stock Scan API."""
	from erpnext_enhancements.api import stock_scan

	items, _truncated = stock_scan._items_at(frappe._dict(warehouse), limit=None)
	return items


def locations_with_anything(item_code=None):
	"""The warehouses ``_items_at`` could list something at: any Bin row, any enabled Putaway
	Rule, any Item Default. Every clause of that function needs one of the three, so a location
	outside this set lists nothing and is not asked. With ``item_code``, only that item's.
	"""
	params = {"item": item_code}
	item_bin = "where item_code = %(item)s" if item_code else ""
	item_rule = "and item_code = %(item)s" if item_code else ""
	item_default = "and parent = %(item)s" if item_code else ""
	names = set()
	for query in (
		f"select distinct warehouse from `tabBin` {item_bin}",
		f"select distinct warehouse from `tabPutaway Rule` where disable = 0 {item_rule}",
		f"""select distinct default_warehouse from `tabItem Default`
			where parenttype = 'Item' and default_warehouse is not null {item_default}""",
	):
		names.update(row[0] for row in frappe.db.sql(query, params) if row[0])
	return names


def get_rows(filters, where):
	location = (filters.get("location") or "").strip()
	if location and location not in where.names:
		frappe.throw(_("There is no location called {0}.").format(location))
	item_code = (filters.get("item_code") or "").strip() or None
	by_name = {w.get("name"): w for w in where.warehouses}
	wanted = locations_with_anything(item_code)
	leaves = [name for name in rules.leaves_under(where.warehouses, location or None) if name in wanted]

	listed = []
	for name in leaves:
		for item in items_at(by_name[name]):
			if item_code and item.get("item_code") != item_code:
				continue
			listed.append((name, item))
	if not listed:
		return []

	codes = {item.get("item_code") for _name, item in listed}
	items = data.item_info(codes)
	groups = set(data.item_groups_under(filters.get("item_group")))
	rules_by_item, defaults_by_item = data.assignments(codes)
	not_bins = set(where.sites) | {w.get("name") for w in where.warehouses if rules._on(w.get("is_group"))}
	not_bins.update(name for name in (where.shelf, where.boneyard) if name)

	rows = []
	for name, item in listed:
		code = item.get("item_code")
		info = items.get(code) or {}
		if groups and info.get("item_group") not in groups:
			continue
		kind = data.kind_of(name, where)
		elsewhere = rules.other_bins(code, name, rules_by_item, defaults_by_item, not_bins)
		status = rules.location_status(item, holding=rules.is_holding(kind), elsewhere=elsewhere)
		rows.append(
			{
				"warehouse": name,
				"under": where.names.get(where.parents.get(name)) or "",
				"kind": kind,
				"item_code": code,
				"item_name": item.get("item_name") or info.get("item_name") or code,
				"item_group": info.get("item_group") or "",
				"on_hand": item.get("on_hand") or 0,
				"on_order": item.get("on_order") or 0,
				"stock_uom": item.get("stock_uom") or info.get("stock_uom") or "",
				"assigned_here": rules.assigned_here(item),
				"status": status,
				"other_bins": ", ".join(where.names.get(w) or w for w in elsewhere),
				"is_problem": 1 if rules.is_problem(status) else 0,
			}
		)
	rows.sort(key=lambda r: sort_key(r, where))
	return rows


def sort_key(row, where):
	"""By where the location is, then the rows to act on, then the item's name."""
	name = row["warehouse"]
	return (
		scan_rules.natural_key(row["under"]),
		scan_rules.natural_key(where.names.get(name) or name),
		rules.status_rank(row["status"]),
		scan_rules.natural_key(row["item_name"]),
	)


def keep(row, filters):
	"""Whether the Status and "Only problems" filters keep a row."""
	status = (filters.get("status") or "").strip()
	if status and row["status"] != status:
		return False
	if rules._on(filters.get("only_problems")) and not row["is_problem"]:
		return False
	return True


def get_message(where):
	return data.message(None if where.shelf else _(data.NO_SHELF_NOTE))


def get_summary(rows):
	"""The cards: every row at the chosen locations, before Status and "Only problems"."""
	summary = rules.location_summary(rows)
	by_status = summary["by_status"]
	return [
		{"label": _("Locations"), "value": summary["locations"], "datatype": "Int", "indicator": "Blue"},
		{"label": _("Items listed"), "value": summary["rows"], "datatype": "Int", "indicator": "Blue"},
		{"label": _("As expected"), "value": summary["ok"], "datatype": "Int", "indicator": "Green"},
		{
			"label": _("Assigned, none on hand"),
			"value": by_status[rules.STATUS_EMPTY],
			"datatype": "Int",
			"indicator": "Orange",
		},
		{
			"label": _("Here, not assigned"),
			"value": by_status[rules.STATUS_UNASSIGNED],
			"datatype": "Int",
			"indicator": "Red",
		},
		{
			"label": _("Here, assigned elsewhere"),
			"value": by_status[rules.STATUS_ELSEWHERE],
			"datatype": "Int",
			"indicator": "Red",
		},
	]
