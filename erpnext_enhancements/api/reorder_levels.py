# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reorder levels on the two inventory audit screens (v1.720.0).

Nik, 2026-10-22: "From the Inventory audit screen include the reorder threshold amount so each
items reorder amount can be adjusted easily without leaving the screen." Both screens that are
called that (the Bin Manager and the Inventory Scanner Audit) show each item's reorder rule and
let a person change it in place; this module is the server side they share.

**What the rule is.** ERPNext keeps it on the Item, in the ``reorder_levels`` child table
(Item Reorder), one row per warehouse: ``warehouse_reorder_level`` is the trigger (buy more when
the stock falls to it) and ``warehouse_reorder_qty`` is how many to buy. "Threshold" could mean
either, so the screens show and edit both. No custom field stands in for it (checked on
production, 2026-10-22: 116 items have a rule, exactly one each, 83 on ``Inventory Room - SF``
and 33 on a bin).

**Which row an item shows.** The rule on the warehouse being looked at when there is one (ERPNext
allows one per warehouse, whatever its type), else the item's own rule wherever it sits (the 83
items whose rule is on the room, listed on a bin), labelled with its warehouse so nobody mistakes it
for the bin's: a Purchase rule before a Transfer or Manufacture one. An item with no rule at all gets
one made on the warehouse being looked at, as a Purchase request, the first time a number is saved.

**How it is written.** Through the Item document (``Item.save()``) as the session user, so
ERPNext's own validation runs -- a level with no quantity ("Row #1: Please set reorder
quantity"), a duplicate row for a warehouse -- and the Item's permission is the framework's:
write on Item, checked here before anything is read or changed. Never SQL on the child table. The
role gate is the one the two screens already have (``stock_scan_rules.SCAN_ROLES``); it lets a
person *see* the rule, and ``can_edit`` (write on Item) says whether the fields are inputs or
plain text.

**A stale page does not overwrite.** The screen sends the Item's ``modified`` that it read the
rule with; if the Item has changed since (a purchaser edited it, a bin assignment touched it),
nothing is written and the answer says to reload. Frappe checks the same stamp again when it
saves, so a change between this check and the save is refused too.

Setting the same numbers again changes nothing and saves nothing (the Item's ``modified`` keeps
still, so other open copies of it stay current), which also makes a resend after a lost reply
harmless: these are absolute values, not increments.

Tab-indented, like ``item_flow``, whose ``_item`` (the Item after the caller's permission) and
``_set`` (set a field, say whether that changed it) it reuses.
"""

import json

import frappe
from frappe import _
from frappe.utils import cstr, flt, get_datetime

from erpnext_enhancements.api import item_flow
from erpnext_enhancements.inventory_enhancements import item_flow_rules as flow_rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

#: The rule the screens edit, and the one a new row is made as (``item_flow.REORDER_TYPE``).
REORDER_TYPE = item_flow.REORDER_TYPE

#: Items one read may ask about: a bin lists at most 150 (``api.stock_scan.LOCATION_ITEM_LIMIT``).
MAX_ITEMS = 300


def _check_access():
	if not rules.SCAN_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to use Stock Scan."), frappe.PermissionError)


def can_edit():
	"""Whether the caller may change an Item at all. The Item is checked again on every save."""
	return bool(frappe.has_permission("Item", "write"))


def _codes(item_codes):
	"""The item codes asked about, as a list of unique strings (JSON text or a list)."""
	if isinstance(item_codes, str):
		try:
			item_codes = json.loads(item_codes or "[]")
		except ValueError:
			frappe.throw(_("The item list could not be read."))
	if item_codes is None:
		return []
	if not isinstance(item_codes, (list, tuple)):
		frappe.throw(_("The item list could not be read."))
	codes = []
	for code in item_codes:
		code = cstr(code).strip()
		if code and code not in codes:
			codes.append(code)
	if len(codes) > MAX_ITEMS:
		frappe.throw(_("Ask about {0} items at most at a time.").format(MAX_ITEMS))
	return codes


def _same_stamp(a, b):
	"""Whether two ``modified`` values are the same moment (text from JSON, or a datetime)."""
	if not a or not b:
		return False
	try:
		return get_datetime(a) == get_datetime(b)
	except (TypeError, ValueError):
		return False


def _is_purchase(row):
	return (row.get("material_request_type") or REORDER_TYPE) == REORDER_TYPE


def _pick(rows, warehouse):
	"""The rule an item shows at ``warehouse``: its rule there (a Purchase one first, though ERPNext
	allows only one per warehouse), else its first Purchase rule anywhere, else its first rule.
	``rows`` are in table order."""
	here = [r for r in rows if warehouse and r.get("warehouse") == warehouse]
	for pool in (
		[r for r in here if _is_purchase(r)],
		here,
		[r for r in rows if _is_purchase(r)],
		rows,
	):
		if pool:
			return pool[0]
	return None


def _entry(item_code, rows, modified, warehouse, names):
	"""One item's reorder payload for ``warehouse``.

	``has_rule`` false means no row yet (``level``/``qty`` are None and ``row_warehouse`` is where a
	save would make it: ``warehouse``). ``elsewhere`` says the rule shown is another warehouse's.
	``modified`` is the Item's stamp, to send back with a save.
	"""
	row = _pick(rows, warehouse)
	where = (row.get("warehouse") if row else None) or warehouse or None
	return {
		"item_code": item_code,
		"has_rule": bool(row),
		"row_warehouse": where,
		"warehouse_label": names.get(where) or where,
		"elsewhere": bool(row and warehouse and where != warehouse),
		"level": flt(row.get("warehouse_reorder_level")) if row else None,
		"qty": flt(row.get("warehouse_reorder_qty")) if row else None,
		"material_request_type": (row.get("material_request_type") or REORDER_TYPE) if row else None,
		"modified": cstr(modified),
	}


def _warehouse_names(names):
	names = [name for name in set(names) if name]
	if not names:
		return {}
	rows = frappe.get_all("Warehouse", filters={"name": ["in", names]}, fields=["name", "warehouse_name"])
	return {row.name: row.warehouse_name or row.name for row in rows}


@frappe.whitelist(methods=["POST"])
def get_reorder_levels(warehouse=None, item_codes=None):
	"""The reorder rule of each item in ``item_codes``, as the screen for ``warehouse`` shows it.

	``{"levels": {item_code: entry}, "can_edit": bool}``, an entry as :func:`_entry` builds it.
	An item that does not exist is left out. ``warehouse`` may be empty (a count with no location
	yet): an item's own rule still shows, and an item with none has nowhere to make one.
	"""
	_check_access()
	warehouse = cstr(warehouse).strip() or None
	codes = _codes(item_codes)
	if not codes:
		return {"levels": {}, "can_edit": can_edit()}
	items = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "modified"])
	rows = frappe.get_all(
		"Item Reorder",
		filters={"parent": ["in", codes], "parenttype": "Item", "parentfield": "reorder_levels"},
		fields=[
			"parent",
			"warehouse",
			"warehouse_reorder_level",
			"warehouse_reorder_qty",
			"material_request_type",
		],
		order_by="parent asc, idx asc",
	)
	by_item = {}
	for row in rows:
		by_item.setdefault(row.parent, []).append(row)
	names = _warehouse_names([warehouse] + [row.warehouse for row in rows])
	return {
		"levels": {
			item.name: _entry(item.name, by_item.get(item.name, []), item.modified, warehouse, names)
			for item in items
		},
		"can_edit": can_edit(),
	}


def _amount(value, label):
	"""``value`` as a quantity, or None when it was not sent. Blank is not sent."""
	number = flow_rules._number(value)
	if number is None:
		return None
	if number == "bad":
		frappe.throw(_("The reorder {0} must be a number.").format(label))
	if number < 0:
		frappe.throw(_("The reorder {0} cannot be below 0.").format(label))
	return float(number)


def _make_row_ok(warehouse):
	"""A rule can be made only on a warehouse that holds stock: a leaf, enabled, not transit."""
	row = (
		frappe.db.get_value(
			"Warehouse", warehouse, ["name", "is_group", "disabled", "warehouse_type"], as_dict=True
		)
		if warehouse
		else None
	)
	if not row:
		frappe.throw(_("Choose the bin first: this item has no reorder rule to change yet."))
	if flt(row.is_group) or flt(row.disabled) or row.warehouse_type == "Transit":
		frappe.throw(
			_("{0} cannot hold stock, so it cannot have a reorder rule: choose a bin.").format(
				frappe.bold(warehouse)
			)
		)


@frappe.whitelist(methods=["POST"])
def set_reorder_level(item_code, warehouse, modified, level=None, qty=None, view_warehouse=None):
	"""Set an item's reorder ``level`` and/or ``qty`` on the rule for ``warehouse``.

	``warehouse`` is the warehouse of the rule the screen showed (its ``row_warehouse``); an item
	with no rule there gets one, as a Purchase request. ``modified`` is the Item's stamp from the
	read: a changed Item refuses the save, with the sentence to reload. A value left out (None or
	blank) is kept; on a new rule a value left out is 0. ``view_warehouse`` is the warehouse the
	screen is looking at (``warehouse`` when not given): the answer's ``elsewhere`` is read against it.
	Returns ``{"saved", "message", "entry"}``;
	``saved`` is False when the numbers were already those.

	Everything that can be refused is, in the caller's terms, before anything is written; the
	write is one ``Item.save()``, whose own validation is ERPNext's.
	"""
	_check_access()
	item = item_flow._item(item_code, "write")
	warehouse = cstr(warehouse).strip()
	level_value, qty_value = _amount(level, _("level")), _amount(qty, _("quantity"))
	if level_value is None and qty_value is None:
		frappe.throw(_("Give a reorder level or a quantity."))
	if not _same_stamp(item.modified, modified):
		frappe.throw(
			_(
				"{0} was changed by someone else since this page loaded it. Nothing was saved; the page "
				"shows its current numbers, so check them and enter yours again."
			).format(frappe.bold(item.name)),
			frappe.TimestampMismatchError,
		)

	rows = item.get("reorder_levels") or []
	target = _pick([r for r in rows if r.get("warehouse") == warehouse], warehouse)
	created = target is None
	if created:
		_make_row_ok(warehouse)
		target = item.append(
			"reorder_levels",
			{
				"warehouse": warehouse,
				"warehouse_group": warehouse,
				"material_request_type": REORDER_TYPE,
				"warehouse_reorder_level": 0.0,
				"warehouse_reorder_qty": 0.0,
			},
		)
	changed = created
	new_level = flt(target.warehouse_reorder_level) if level_value is None else level_value
	new_qty = flt(target.warehouse_reorder_qty) if qty_value is None else qty_value
	if new_level > 0 and new_qty <= 0:
		# ERPNext says this too ("Please set reorder quantity"), after the row is built; in words first.
		frappe.throw(_("Say how many to buy each time the stock falls to the reorder level."))
	changed = item_flow._set(target, "warehouse_reorder_level", new_level) or changed
	changed = item_flow._set(target, "warehouse_reorder_qty", new_qty) or changed
	if changed:
		item.save()

	view = cstr(view_warehouse).strip() or warehouse
	saved_rows = [r.as_dict() for r in item.get("reorder_levels") or []]
	names = _warehouse_names([view] + [r.get("warehouse") for r in saved_rows])
	entry = _entry(item.name, saved_rows, item.modified, view, names)
	return {
		"saved": bool(changed),
		"message": _("Saved.") if changed else _("Nothing to change."),
		"entry": entry,
	}
