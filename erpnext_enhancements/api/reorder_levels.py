# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reorder levels on the two inventory audit screens (v1.725.0).

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

**What a rule may be (review of #1338, 2026-10-22).** Since v1.730.0
``Stock Settings.auto_indent`` is off (``patches/disable_auto_material_requests``), so ERPNext raises no Material
Request from a rule on its own. Every rule this module writes drives the **Reorder Board** and its daily digest
to Purchase Managers (``inventory_enhancements/reorder_board.py``): a buyer picks from the board and creates
the Draft request. (This module was written while auto-indent was on and the same limits were reviewed for
that; they stand, because a wrong rule still puts the wrong lines in front of a buyer.)

* **Both numbers given; Buy above 0; Reorder at may be 0.** A rule is made only when the level *and*
  the quantity are given. ``warehouse_reorder_qty`` is never saved as 0 or less: a rule that buys
  nothing is no rule, and one is switched off by *removing* it (:func:`remove_reorder_rule`), which the
  screens ask about first. ``warehouse_reorder_level`` may be **0**. Nik, 2026-10-22: "The reorder at
  line is refusing to be assigned a 0 even though running out of all stock is a very normal spot to
  reorder items." The review of #1338 had refused a level of 0 as a request raised "whenever stock
  reaches 0"; that is exactly what he wants. ERPNext's trigger (``erpnext/stock/reorder_item.py``,
  version-16) is ``if (reorder_level or reorder_qty) and projected_qty <= reorder_level``, and
  ``get_items_for_reorder`` does not filter on level > 0: a rule at 0 with a quantity is below its
  reorder point once projected stock reaches 0 or below (the Reorder Board lists it by the same rule),
  and because projected stock counts open Material Requests and Purchase Orders it is not offered a
  second time. A level below 0 is still refused. (A rule counts as a rule when it exists, level 0
  included: the KPI dashboards' "stocked items" and the Stock Scan reason read it that way since
  v1.732.0.)
* **A sane ceiling** (:data:`MAX_AMOUNT`, ``stock_scan_rules.MAX_QTY``, 100,000): a barcode scanned
  into a focused box and followed by the scanner's Enter is a number of twelve digits. And a unit
  that is counted in whole numbers (``UOM.must_be_whole_number``) takes whole numbers.
* **The group a new rule checks stock in** is the one its neighbours use (:func:`_default_group`),
  not the bin itself: ERPNext counts the projected stock of ``warehouse_group`` against the level.
  A rule that checks the bin alone orders an item that has plenty on the shelf next door. Existing
  rules are never changed.

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

#: The most a level or a quantity may be: the Stock Scan page's own ceiling on a quantity. The editor
#: (``public/js/reorder_levels/logic.js`` ``MAX_AMOUNT``) holds the same number; a test keeps them equal.
MAX_AMOUNT = rules.MAX_QTY


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


def _entry(item_code, rows, modified, warehouse, names, uom=None, whole=False):
	"""One item's reorder payload for ``warehouse``.

	``has_rule`` false means no row yet (``level``/``qty`` are None and ``row_warehouse`` is where a
	save would make it: ``warehouse``). ``elsewhere`` says the rule shown is another warehouse's.
	``modified`` is the Item's stamp, to send back with a save. ``stock_uom`` is the unit the numbers
	are in, and ``whole_number`` whether that unit is counted in whole numbers.
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
		"stock_uom": uom,
		"whole_number": bool(whole),
	}


def _whole_number_units(units):
	"""Which of ``units`` are counted in whole numbers (``UOM.must_be_whole_number``)."""
	units = [unit for unit in set(units) if unit]
	if not units:
		return set()
	rows = frappe.get_all("UOM", filters={"name": ["in", units]}, fields=["name", "must_be_whole_number"])
	return {row.name for row in rows if flt(row.must_be_whole_number)}


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
	items = frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "modified", "stock_uom"])
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
	whole = _whole_number_units([item.stock_uom for item in items])
	return {
		"levels": {
			item.name: _entry(
				item.name,
				by_item.get(item.name, []),
				item.modified,
				warehouse,
				names,
				item.stock_uom,
				item.stock_uom in whole,
			)
			for item in items
		},
		"can_edit": can_edit(),
	}


def _amount(value, label, whole=False, unit=None):
	"""``value`` as a quantity, or None when it was not sent. Blank is not sent.

	Refused: not a number (``nan`` and ``inf`` included), negative, above :data:`MAX_AMOUNT`, and
	with a fraction in a unit that is counted in whole numbers.
	"""
	number = flow_rules._number(value)
	if number is None:
		return None
	if number == "bad":
		frappe.throw(_("The reorder {0} must be a number.").format(label))
	if number < 0:
		frappe.throw(_("The reorder {0} cannot be below 0.").format(label))
	if number > MAX_AMOUNT:
		frappe.throw(
			_("The reorder {0} cannot be more than {1}. Was that a barcode?").format(label, f"{MAX_AMOUNT:,}")
		)
	if whole and number != int(number):
		frappe.throw(
			_("{0} is counted in whole numbers, so the reorder {1} must be a whole number.").format(
				frappe.bold(unit or _("The unit")), label
			)
		)
	return float(number)


def _ancestors(parents, name):
	"""``name``'s group parents, nearest first (``parents`` maps a warehouse to its parent)."""
	out = []
	while parents.get(name) and parents[name] not in out:
		out.append(parents[name])
		name = parents[name]
	return out


def _default_group(warehouse):
	"""The ``warehouse_group`` a new rule on ``warehouse`` should check stock in.

	The one its neighbours use: among the existing rules on warehouses at the **same depth** in the
	tree, under the nearest group ancestor that has any, the most common ``warehouse_group`` (ties
	by name). On this site that is ``Inventory - SF`` for the bins under the Rows and ``All
	Warehouses - SF`` for ``Inventory Room - SF``. Rules whose group is their own warehouse (one bin
	has such a rule, the shape this function exists to avoid) are not asked. With no neighbours at
	all: the warehouse's parent group, else the warehouse itself. Read, never written.
	"""
	parents = {
		row.name: row.parent_warehouse
		for row in frappe.get_all("Warehouse", fields=["name", "parent_warehouse"])
	}
	mine = _ancestors(parents, warehouse)
	existing = frappe.get_all(
		"Item Reorder",
		filters={"parenttype": "Item", "parentfield": "reorder_levels"},
		fields=["warehouse", "warehouse_group"],
	)
	chains = {}
	for rule in existing:
		group = cstr(rule.warehouse_group).strip()
		if group and group != rule.warehouse and rule.warehouse not in chains:
			chains[rule.warehouse] = _ancestors(parents, rule.warehouse)
	for ancestor in mine:
		votes = {}
		for rule in existing:
			group = cstr(rule.warehouse_group).strip()
			chain = chains.get(rule.warehouse)
			if group and group != rule.warehouse and chain and len(chain) == len(mine) and ancestor in chain:
				votes[group] = votes.get(group, 0) + 1
		if votes:
			return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
	return mine[0] if mine else warehouse


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


def _stale(item, modified):
	"""Refuse a page whose copy of the Item is older than the Item."""
	if not _same_stamp(item.modified, modified):
		frappe.throw(
			_(
				"{0} was changed by someone else since this page loaded it. Nothing was saved; the page "
				"shows its current numbers, so check them and enter yours again."
			).format(frappe.bold(item.name)),
			frappe.TimestampMismatchError,
		)


def _answer(item, view, changed, message):
	saved_rows = [r.as_dict() for r in item.get("reorder_levels") or []]
	names = _warehouse_names([view] + [r.get("warehouse") for r in saved_rows])
	whole = item.stock_uom in _whole_number_units([item.stock_uom])
	return {
		"saved": bool(changed),
		"message": message,
		"entry": _entry(item.name, saved_rows, item.modified, view, names, item.stock_uom, whole),
	}


@frappe.whitelist(methods=["POST"])
def set_reorder_level(item_code, warehouse, modified, level=None, qty=None, view_warehouse=None):
	"""Set an item's reorder ``level`` and/or ``qty`` on the rule for ``warehouse``.

	``warehouse`` is the warehouse of the rule the screen showed (its ``row_warehouse``); an item
	with no rule there gets one, as a Purchase request, **only when both numbers are given**, in
	the group :func:`_default_group` finds. ``modified`` is the Item's stamp from the read: a
	changed Item refuses the save, with the sentence to reload. On an existing rule a value left
	out (None or blank) is kept. ``qty`` is never saved as 0 or less (to stop reordering,
	:func:`remove_reorder_rule`); ``level`` may be 0, which reorders when stock runs out, but not below
	0 (see the module docstring). ``view_warehouse`` is the warehouse the screen is looking at
	(``warehouse`` when not given): the answer's ``elsewhere`` is read against it. Returns
	``{"saved", "message", "entry"}``; ``saved`` is False when the numbers were already those.

	Everything that can be refused is, in the caller's terms, before anything is written; the
	write is one ``Item.save()``, whose own validation is ERPNext's.
	"""
	_check_access()
	item = item_flow._item(item_code, "write")
	warehouse = cstr(warehouse).strip()
	whole = item.stock_uom in _whole_number_units([item.stock_uom])
	level_value = _amount(level, _("level"), whole, item.stock_uom)
	qty_value = _amount(qty, _("quantity"), whole, item.stock_uom)
	if level_value is None and qty_value is None:
		frappe.throw(_("Give a reorder level or a quantity."))
	_stale(item, modified)

	rows = item.get("reorder_levels") or []
	target = _pick([r for r in rows if r.get("warehouse") == warehouse], warehouse)
	created = target is None
	if created and (level_value is None or qty_value is None):
		frappe.throw(_("Enter both Reorder at and Buy to create a reorder rule."))
	new_level = flt(target.warehouse_reorder_level) if level_value is None else level_value
	new_qty = flt(target.warehouse_reorder_qty) if qty_value is None else qty_value
	unchanged = (
		not created
		and flt(target.warehouse_reorder_level) == new_level
		and flt(target.warehouse_reorder_qty) == new_qty
	)
	if not unchanged:
		# Reorder at 0 is a rule: it raises a request once projected stock reaches 0 (Nik, 2026-10-22, and
		# ERPNext's own trigger; see the module docstring). It is the quantity that must be more than 0.
		if new_level < 0:
			frappe.throw(_("The reorder level cannot be below 0."))
		if new_qty <= 0:
			frappe.throw(_("Buy must be more than 0. To stop reordering, remove the rule."))
	if created:
		_make_row_ok(warehouse)
		target = item.append(
			"reorder_levels",
			{
				"warehouse": warehouse,
				"warehouse_group": _default_group(warehouse),
				"material_request_type": REORDER_TYPE,
				"warehouse_reorder_level": 0.0,
				"warehouse_reorder_qty": 0.0,
			},
		)
	changed = created
	changed = item_flow._set(target, "warehouse_reorder_level", new_level) or changed
	changed = item_flow._set(target, "warehouse_reorder_qty", new_qty) or changed
	if changed:
		item.save()
	view = cstr(view_warehouse).strip() or warehouse
	return _answer(item, view, changed, _("Saved.") if changed else _("Nothing to change."))


@frappe.whitelist(methods=["POST"])
def remove_reorder_rule(item_code, warehouse, modified, view_warehouse=None):
	"""Remove an item's reorder rule on ``warehouse``: the one way to stop it reordering.

	The row comes out through the Item document (``Item.remove`` then ``Item.save()``, as the
	user), after the same permission and stale-page checks as :func:`set_reorder_level`. The
	screens ask "Remove this reorder rule?" before calling this. Returns what a save returns;
	``entry`` is then the item's next rule (if it has another), else none.
	"""
	_check_access()
	item = item_flow._item(item_code, "write")
	warehouse = cstr(warehouse).strip()
	_stale(item, modified)
	target = _pick(
		[r for r in item.get("reorder_levels") or [] if r.get("warehouse") == warehouse], warehouse
	)
	if target is None:
		frappe.throw(
			_("{0} has no reorder rule on {1}.").format(frappe.bold(item.name), frappe.bold(warehouse or "?"))
		)
	item.remove(target)
	item.save()
	return _answer(item, cstr(view_warehouse).strip() or warehouse, True, _("Removed."))
