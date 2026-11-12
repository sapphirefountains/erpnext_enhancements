# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where Is This Item: every place an item is, and whose it is (v1.805.0).

Pick an item, or type a few words of its code or name: one row per place it is on hand or on
order, split the way Stock Scan splits it and one step further:

| Kind | What it is | Whose |
|---|---|---|
| Shop bin | a shelf bin or any other shop location | the shop's |
| Project Shelf | the one shared shelf for stock bought for jobs | one row per job, from the stock ledger |
| Job site | a job's own warehouse | that job's (``Warehouse.custom_job_site_project``) |
| Vehicle | an employee's vehicle warehouse, and anything under it | the shop's, out on the road |
| Boneyard | stock a bin no longer keeps | nobody's |

Each row has what is on hand and on order there, its value, whether the place is one of the
item's bins, the item's bins by name, and the last movement at that place with its voucher.
With nothing chosen the report lists every item that is on hand or on order anywhere.

**Assigned places with none on hand list too** (the box is ticked to begin with), so an item's
empty bin is on its list of places: that is where the next one goes.

**The Project Shelf is one warehouse, so its split by job is not in ``tabBin``.** It comes from
``inventory_report_data.shelf_stock_by_project``: each job's share, and a last row under no job
for what no job claims, which also carries what is on order to the shelf (an order's quantity is
kept by warehouse, not by job). Before a Project Shelf is set, no row is of that kind and the
note over the table says so.

**The last movement** is the newest live Stock Ledger row for the item at the place
(``posting_datetime``, then ``creation``). On the Project Shelf it is the newest row carrying
that row's job. Canceled entries are left out, as everywhere else.

**A search is capped** at ``MAX_ITEMS`` items and the unfiltered list at ``MAX_ROWS`` rows, and
the note says when either cut something off. The stock ledger is read grouped by item and
warehouse on its own index, so the read stays the same shape however long the ledger gets.

**A Script Report** because every filter is optional. Opens at
``/desk/query-report/Where Is This Item``.
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import inventory_report_data as data
from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules

#: The most items a search or an Item Group may bring back.
MAX_ITEMS = 500

#: The most place rows the unfiltered list reads from ``tabBin``.
MAX_ROWS = 5000

NO_JOB = "(no job)"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = data.places()
	rows, notes = get_rows(filters, where)
	if not where.shelf:
		notes.append(_(data.NO_SHELF_NOTE))
	return get_columns(), rows, "<br>".join(notes) or None, None, get_summary(rows)


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 230},
		{"label": _("Kind"), "fieldname": "kind", "fieldtype": "Data", "width": 105},
		{
			"label": _("Location"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
		{"label": _("Under"), "fieldname": "under", "fieldtype": "Data", "width": 120},
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 110,
		},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 180},
		{"label": _("On Hand"), "fieldname": "on_hand", "fieldtype": "Float", "width": 90},
		{"label": _("On Order"), "fieldname": "on_order", "fieldtype": "Float", "width": 90},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "width": 100},
		{"label": _("Its Bin"), "fieldname": "assigned_here", "fieldtype": "Data", "width": 110},
		{"label": _("Assigned Bins"), "fieldname": "assigned_bins", "fieldtype": "Data", "width": 190},
		{"label": _("Last Moved"), "fieldname": "last_moved", "fieldtype": "Date", "width": 105},
		{
			"label": _("Last Voucher Type"),
			"fieldname": "last_voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 140,
		},
		{
			"label": _("Last Voucher"),
			"fieldname": "last_voucher",
			"fieldtype": "Dynamic Link",
			"options": "last_voucher_type",
			"width": 170,
		},
	]


def search_words(text):
	"""The words of a search, at most six, each at least two characters."""
	return [word for word in str(text or "").split() if len(word) >= 2][:6]


def find_items(filters):
	"""The item codes the filters name, and whether the list was cut at ``MAX_ITEMS``.

	``None`` means no item filter at all (every item with stock). An Item alone is that item,
	whatever else is filled in. A search matches every word in the code or the name; an Item
	Group covers the groups under it. Disabled items are found too: a disabled item with stock
	on a shelf is exactly what somebody is looking for.
	"""
	item_code = (filters.get("item_code") or "").strip()
	if item_code:
		return [item_code], False
	words = search_words(filters.get("search"))
	groups = data.item_groups_under(filters.get("item_group"))
	if not words and not groups:
		return None, False
	conditions, params = [], {"limit": MAX_ITEMS + 1}
	for index, word in enumerate(words):
		key = f"w{index}"
		conditions.append(f"(name like %({key})s or item_name like %({key})s)")
		params[key] = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
	if groups:
		conditions.append("item_group in %(groups)s")
		params["groups"] = tuple(groups)
	found = frappe.db.sql(
		"select name from `tabItem` where " + " and ".join(conditions) + " order by name limit %(limit)s",
		params,
	)
	codes = [row[0] for row in found]
	return codes[:MAX_ITEMS], len(codes) > MAX_ITEMS


def bin_rows(item_codes):
	"""``tabBin`` rows with anything on hand or on order: for the given items, or (``None``) for
	every item, capped at ``MAX_ROWS + 1`` so the caller can tell the list was cut."""
	fields = "item_code, warehouse, actual_qty, ordered_qty, stock_value"
	if item_codes is None:
		return frappe.db.sql(
			f"""
			select {fields} from `tabBin`
			where actual_qty <> 0 or ordered_qty > 0
			order by item_code, warehouse
			limit %(limit)s
			""",
			{"limit": MAX_ROWS + 1},
			as_dict=True,
		)
	out = []
	for part in data.chunks(item_codes):
		out.extend(
			frappe.db.sql(
				f"""
				select {fields} from `tabBin`
				where item_code in %(items)s and (actual_qty <> 0 or ordered_qty > 0)
				""",
				{"items": tuple(part)},
				as_dict=True,
			)
		)
	return out


def last_movements(item_codes, shelf=None):
	"""The newest live ledger row per place: ``({(item, warehouse): row}, {(item, project): row})``.

	The second map is for the Project Shelf, by the job each row carries. One grouped pass finds
	each pair's newest ``posting_datetime``; the join reads those rows back, and the newest
	``creation`` wins among rows posted at the same moment.
	"""
	by_place, by_job = {}, {}
	parts = [None] if item_codes is None else data.chunks(item_codes)
	for part in parts:
		item_clause = "and item_code in %(items)s" if part else ""
		params = {"items": tuple(part)} if part else {}
		found = frappe.db.sql(
			f"""
			select s.item_code, s.warehouse, s.posting_date, s.voucher_type, s.voucher_no, s.creation
			from `tabStock Ledger Entry` s
			join (
				select item_code, warehouse, max(posting_datetime) as stamp
				from `tabStock Ledger Entry`
				where is_cancelled = 0 {item_clause}
				group by item_code, warehouse
			) m on m.item_code = s.item_code and m.warehouse = s.warehouse
				and m.stamp = s.posting_datetime
			where s.is_cancelled = 0
			""",
			params,
			as_dict=True,
		)
		_newest(by_place, found, lambda r: (r.get("item_code"), r.get("warehouse")))
		if not shelf:
			continue
		found = frappe.db.sql(
			f"""
			select s.item_code, coalesce(s.project, '') as project, s.posting_date, s.voucher_type,
				s.voucher_no, s.creation
			from `tabStock Ledger Entry` s
			join (
				select item_code, coalesce(project, '') as project, max(posting_datetime) as stamp
				from `tabStock Ledger Entry`
				where is_cancelled = 0 and warehouse = %(shelf)s {item_clause}
				group by item_code, coalesce(project, '')
			) m on m.item_code = s.item_code and m.project = coalesce(s.project, '')
				and m.stamp = s.posting_datetime
			where s.is_cancelled = 0 and s.warehouse = %(shelf)s
			""",
			{**params, "shelf": shelf},
			as_dict=True,
		)
		_newest(by_job, found, lambda r: (r.get("item_code"), r.get("project") or ""))
	return by_place, by_job


def _newest(into, found, key):
	for row in found:
		held = into.get(key(row))
		if held is None or str(row.get("creation") or "") > str(held.get("creation") or ""):
			into[key(row)] = row


def get_rows(filters, where):
	notes = []
	item_codes, cut = find_items(filters)
	if cut:
		notes.append(
			_("More than {0} items match. Showing the first {0}; add a word to narrow it.").format(MAX_ITEMS)
		)
	if item_codes is not None and not item_codes:
		return [], notes
	bins = bin_rows(item_codes)
	if item_codes is None and len(bins) > MAX_ROWS:
		bins = bins[:MAX_ROWS]
		notes.append(
			_("Showing the first {0} places. Choose an item or search to see the rest.").format(MAX_ROWS)
		)
	codes = {row.get("item_code") for row in bins}
	if item_codes is not None:
		codes.update(item_codes)
	if not codes:
		return [], notes

	items = data.item_info(codes)
	rules_by_item, defaults_by_item = data.assignments(codes)
	shelf_shares = {}
	if where.shelf:
		for share in data.shelf_stock_by_project(where.shelf, item_codes=item_codes):
			shelf_shares.setdefault(share.get("item_code"), []).append(share)
	by_place, by_job = last_movements(item_codes, where.shelf)

	places = {(row.get("item_code"), row.get("warehouse")): row for row in bins}
	if rules._on(filters.get("include_empty")):
		for code in codes:
			for warehouse in rules_by_item.get(code, set()) | defaults_by_item.get(code, set()):
				places.setdefault((code, warehouse), {"item_code": code, "warehouse": warehouse})

	projects = {share.get("project") for shares in shelf_shares.values() for share in shares}
	projects.update(where.site_projects.get(warehouse) for _code, warehouse in places)
	project_names = data.project_info(projects)

	rows = []
	for (code, warehouse), place in places.items():
		if code not in items:
			continue
		info = items[code]
		kind = data.kind_of(warehouse, where)
		is_rule = warehouse in rules_by_item.get(code, ())
		is_default = warehouse in defaults_by_item.get(code, ())
		base = {
			"item_code": code,
			"item_name": info.get("item_name") or code,
			"kind": kind,
			"warehouse": warehouse,
			"under": where.names.get(where.parents.get(warehouse)) or "",
			"stock_uom": info.get("stock_uom") or "",
			"assigned_here": rules.assigned_here({"assigned": is_rule, "is_default": is_default}),
			"assigned_bins": ", ".join(
				where.names.get(w) or w
				for w in sorted(rules_by_item.get(code, ()) or defaults_by_item.get(code, ()))
			),
		}
		if kind == rules.KIND_SHELF:
			rows.extend(_shelf_rows(base, place, shelf_shares.get(code) or [], by_job, project_names))
			continue
		project = where.site_projects.get(warehouse) or ""
		rows.append(
			{
				**base,
				"project": project,
				"project_name": (project_names.get(project) or {}).get("project_name") or "",
				"on_hand": rules._num(place.get("actual_qty")),
				"on_order": rules._num(place.get("ordered_qty")),
				"value": rules._num(place.get("stock_value")),
				**_moved(by_place.get((code, warehouse))),
			}
		)
	rows.sort(key=lambda r: sort_key(r, where))
	return rows, notes


def _shelf_rows(base, place, shares, by_job, project_names):
	"""The Project Shelf's rows for one item: one per job with a share, then one under no job for
	what no job claims. What is on order to the shelf sits on the no-job row (it is kept by
	warehouse, not by job), which is added for that alone when no stock is unclaimed."""
	out = []
	on_order = rules._num(place.get("ordered_qty"))
	has_no_job = False
	for share in sorted(shares, key=lambda s: (not s.get("project"), s.get("project") or "")):
		project = share.get("project") or ""
		has_no_job = has_no_job or not project
		out.append(
			{
				**base,
				"project": project,
				"project_name": (project_names.get(project) or {}).get("project_name")
				or ("" if project else _(NO_JOB)),
				"on_hand": rules._num(share.get("qty")),
				"on_order": 0.0 if project else on_order,
				"value": rules._num(share.get("value")),
				**_moved(by_job.get((base["item_code"], project))),
			}
		)
	if not has_no_job and (on_order or not out):
		out.append(
			{
				**base,
				"project": "",
				"project_name": _(NO_JOB),
				"on_hand": 0.0,
				"on_order": on_order,
				"value": 0.0,
				**_moved(by_job.get((base["item_code"], ""))),
			}
		)
	return out


def _moved(entry):
	entry = entry or {}
	return {
		"last_moved": entry.get("posting_date"),
		"last_voucher_type": entry.get("voucher_type"),
		"last_voucher": entry.get("voucher_no"),
	}


def sort_key(row, where):
	"""By item, then the kind of place (the shop first), then the place, then the job."""
	return (
		scan_rules.natural_key(row["item_name"]),
		row["item_code"],
		rules.kind_rank(row["kind"]),
		scan_rules.natural_key(row["under"]),
		scan_rules.natural_key(where.names.get(row["warehouse"]) or row["warehouse"]),
		not row["project"],
		row["project"],
	)


def get_summary(rows):
	"""The cards. For one item they are quantities by kind of place. For several items a sum
	across different units means nothing, so they count the places instead."""
	items = {row["item_code"] for row in rows}
	one = len(items) == 1

	def measure(kind):
		picked = [row for row in rows if row["kind"] == kind and row["on_hand"] > 0]
		return sum(row["on_hand"] for row in picked) if one else len(picked)

	datatype = "Float" if one else "Int"
	suffix = "" if one else " " + _("(places)")
	return [
		{"label": _("Items"), "value": len(items), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("In shop bins") + suffix,
			"value": measure(rules.KIND_SHOP),
			"datatype": datatype,
			"indicator": "Green",
		},
		{
			"label": _("On the Project Shelf") + suffix,
			"value": measure(rules.KIND_SHELF),
			"datatype": datatype,
			"indicator": "Blue",
		},
		{
			"label": _("On job sites") + suffix,
			"value": measure(rules.KIND_SITE),
			"datatype": datatype,
			"indicator": "Orange",
		},
		{
			"label": _("In vehicles") + suffix,
			"value": measure(rules.KIND_VEHICLE),
			"datatype": datatype,
			"indicator": "Grey",
		},
		{
			"label": _("In the Boneyard") + suffix,
			"value": measure(rules.KIND_BONEYARD),
			"datatype": datatype,
			"indicator": "Grey",
		},
	]
