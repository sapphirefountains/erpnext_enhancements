# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bin assignments: which items a bin holds, and changing that (v1.689.0).

ER-2026-1319376 asked for a way to take an item off a bin on ``/stock-scan`` and move it to
another; ER-2026-1320545 for a Desk page to manage one bin's items. This module is the server
side both pages share. A "bin" is a leaf Warehouse (``Storage Location`` has no rows).

**An assignment is an ERPNext Putaway Rule.** One enabled rule per (item, warehouse) says the
item lives on that bin. The people using it never see the rule, its capacity or its priority:
they put an item on a bin and take it off. A Putaway Rule rather than a doctype of our own,
because it is ERPNext's own record of "this item is kept here", it carries the company, it is
already unique per pair, and a Purchase Receipt or Stock Entry with *Apply Putaway Rule*
ticked routes stock by it. Taking an item off disables its rule rather than deleting it:
ERPNext counts a disabled rule as still there (one rule per pair, disabled included), and a
receipt that routed stock by a rule keeps a Link to it, so a delete would be refused later.

Every rule this module writes has the same shape:

* **capacity** :data:`CAPACITY` in the stock unit (so ``conversion_factor`` 1 and
  ``stock_capacity`` the same number). Not a real capacity; see the constant.
* **priority** 1, **company** the warehouse's, **uom** the item's stock unit.

What the endpoints do, each idempotent on the page's ``client_ref`` and recorded on a
``Stock Scan Log`` row that Undo reads (``api.stock_scan.undo``):

* :func:`assign` -- put an item on a bin.
* :func:`unassign` -- take it off: disable its rule there **and** clear an Item Default whose
  warehouse is that bin (for the bin's company), because the listing reads both. Refused
  while stock is still recorded there: move it first. A pair with no rule at all (listed as
  undecided) is taken off by recording a *disabled* rule.
* :func:`move_to` -- push stock out of the bin being managed into another, a submitted
  Material Transfer built by the same helper as *Move here*; with ``make_home=1`` the
  assignment moves too (a rule at the destination; the source is taken off only if the move
  empties it).
* :func:`send_to_boneyard` -- send stock the bin no longer keeps to the Boneyard warehouse
  (Inventory Scanner Settings; a Material Transfer through the same helper) and take the item
  off the source bin if that empties it. Never makes a rule at the Boneyard.
* :func:`set_count` -- correct what is on hand: a submitted **Stock Reconciliation** for one
  item at one bin, never a Material Issue, so the ledger says it was a count and the stock
  value moves at the item's rate.

:func:`ensure_assignment` is the plain helper underneath, also called by the guided Item flow
(``api.item_flow.save_stock``). No logging, no ``client_ref``.

The same principles as ``api.stock_scan``, whose helpers this reuses (the reference check, the
log row, the transfer builder, the rate and the accounts): every Stock Entry and Stock
Reconciliation is inserted, submitted and canceled **as the session user**, never with
``ignore_permissions``; refusals are sentences raised before anything is written; the log row
goes in before anything else, in the same transaction.

**Permissions: one module gate, and the one exception it buys.** The owner decided on
2026-10-14 to open bin assignments to everyone who already works the inventory and can see the
items, rather than only to Stock Manager (who alone may write a Putaway Rule in stock ERPNext)
and Item Manager (who alone may write an Item on production). So the assignment records sit
behind :func:`require_manage`: one of ``stock_scan_rules.SCAN_ROLES`` **and** read on Item.
Past that gate this module writes exactly two things with ``ignore_permissions`` -- the Putaway
Rule (create, enable, disable, and deleting a disabled rule an undo made redundant) and the
``default_warehouse`` of the matching Item Default row -- and nothing else: no other field of
the Item, no other doctype, and never a stock voucher, which still posts as the user, so moving
and counting stock needs exactly the permission it needed before. It is the one exception to
"permissions are the framework's", kept that narrow on purpose, and every such write is logged
with the state before it and undoable.

The Item Default write is one child row (``frappe.db.set_value`` on ``Item Default``, the
Item's ``modified`` moved and its cache cleared -- the same thing ERPNext's own
``set_item_default`` does) rather than ``Item.save()``: a full save runs every Item validator,
including this app's naming guard, which refuses some older items outright, and a stock person
taking a part off a shelf should not be told the part's name breaks a naming rule. Moving
``modified`` makes a form someone has open on that Item refuse its next save as stale, rather
than quietly writing the old warehouse back.

Indentation is tabs (new file).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, now_datetime

from erpnext_enhancements.api import stock_scan as scan
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

RULE = "Putaway Rule"

#: The capacity every rule gets, in the item's stock unit. A Putaway Rule must have a capacity
#: above 0 and at least what is already there (ERPNext's "Insufficient Capacity"), and it is
#: read in two places that bite if it is a real number:
#:
#: * a receipt or transfer with *Apply Putaway Rule* ticked fills each rule only up to its
#:   free space and splits the rest elsewhere, so a capacity equal to today's stock would send
#:   the next delivery anywhere but the bin it belongs on;
#: * ``StockController.validate_putaway_capacity`` runs on **every** Purchase Receipt, Stock
#:   Entry and Stock Reconciliation once any rule exists, ticked or not, and refuses "Over
#:   Receipt" past the free space -- and on v16 it reads ``rule.disabled`` where the field is
#:   ``disable``, so it checks disabled rules too.
#:
#: Nobody here manages bin capacity, so the number is simply one no bin will reach (the most
#: of any item in any warehouse on production was 200 on 2026-10-14).
CAPACITY = 1_000_000

#: Every rule's priority. ERPNext needs one >= 1; with *Apply Putaway Rule* ticked it fills the
#: lowest number first. All equal, because nothing here ranks one bin above another.
PRIORITY = 1

#: The two kinds of change a save records in ``Stock Scan Log.rule_changes``.
CHANGE_RULE = "rule"
CHANGE_ITEM_DEFAULT = "item_default"
#: Not a change: what a move or a boneyard trip did about its source bin (released, or kept
#: because stock is still there), for the result's sentence. Undo skips it.
CHANGE_SOURCE = "source"

_ITEM_FIELDS = ["name", "item_name", "stock_uom", "is_stock_item", "disabled", "has_variants"]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def can_manage():
	"""Whether the caller may put items on bins and take them off: the module gate (one of
	``SCAN_ROLES`` and read on Item). The pages hide the buttons without it
	(``api.stock_scan._can_manage`` puts the same answer in the boot and location payloads)."""
	return scan._can_manage()


def require_manage():
	"""Refuse, in a sentence, a caller outside the module gate. Every rule and Item Default
	write here, and every undo of one, comes after this."""
	if not can_manage():
		frappe.throw(
			_(
				"Putting items on bins and taking them off needs a stock role and permission to see items. Ask a Stock Manager."
			),
			frappe.PermissionError,
		)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _item(item_code):
	code = cstr(item_code).strip()
	row = frappe.db.get_value("Item", code, _ITEM_FIELDS, as_dict=True) if code else None
	if not row:
		frappe.throw(_("There is no item {0}.").format(code or _("with that code")))
	return row


def _assignable(item_code, warehouse):
	"""``(item, location)`` that may be assigned, or throw ERPNext's refusals as sentences.

	Checked here rather than caught from ERPNext: "Invalid Warehouse" cannot happen because the
	rule takes the warehouse's own company, and "Duplicate" cannot because an existing rule is
	re-enabled instead of a second one made. :func:`_check_capacity` covers the third.
	"""
	item = _item(item_code)
	label = item.item_name or item.name
	if cint(item.disabled):
		frappe.throw(_("{0} is disabled, so it cannot be put on a bin.").format(label))
	if not cint(item.is_stock_item):
		frappe.throw(_("{0} is not kept in stock, so it cannot be put on a bin.").format(label))
	if cint(item.has_variants):
		frappe.throw(_("{0} is a template. Put one of its variants on the bin instead.").format(label))
	name = cstr(warehouse).strip()
	location = scan._warehouse(name)
	problem = scan._location_problem(location, name)
	if problem:
		frappe.throw(problem)
	if not location.company:
		frappe.throw(_("{0} belongs to no company, so nothing can be put on it.").format(name))
	_refuse_boneyard(location)
	return item, location


def _refuse_boneyard(location):
	"""Nothing is ever assigned to the boneyard: stock there lists because it is on hand, and
	drops off when it is gone. Shared by :func:`_assignable` and *make it the item's home*."""
	if location.name == cstr(scan.get_settings().get("boneyard_warehouse")).strip():
		frappe.throw(
			_("{0} is the boneyard. Nothing is put on it; send stock there with Send to boneyard.").format(
				location.warehouse_name or location.name
			)
		)


def _rule_for(item_code, warehouse):
	"""The pair's Putaway Rule, enabled first, or ``None``. ERPNext allows one per pair whether
	it is disabled or not, so this is the rule to re-enable rather than a reason to make one."""
	rows = frappe.get_all(
		RULE,
		filters={"item_code": item_code, "warehouse": warehouse},
		fields=["name", "disable"],
		order_by="disable asc, creation asc",
		limit=1,
	)
	return rows[0] if rows else None


def _enabled_rules(item_code, warehouse):
	return frappe.get_all(
		RULE,
		filters={"item_code": item_code, "warehouse": warehouse, "disable": 0},
		pluck="name",
		order_by="creation asc",
	)


def _balance(item_code, warehouse):
	"""What is on hand: the Bin's ``actual_qty`` (ERPNext's capacity check reads the ledger
	balance as of today, which is the same number unless a voucher is dated in the future)."""
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))


def _check_capacity(item, location, adding=0.0):
	"""ERPNext's "Insufficient Capacity", said first: never in practice (see :data:`CAPACITY`)."""
	if _balance(item.name, location.name) + flt(adding) > CAPACITY:
		frappe.throw(
			_(
				"{0} has more of {1} than a bin assignment allows. Ask a Stock Manager to look at the count."
			).format(location.warehouse_name or location.name, item.item_name or item.name)
		)


def _defaults_at(item_code, warehouse, company):
	"""The Item Default rows that make ``warehouse`` this item's default for ``company``."""
	rows = frappe.db.sql(
		"""
		select name from `tabItem Default`
		where parent = %(item)s and parenttype = 'Item' and parentfield = 'item_defaults'
			and default_warehouse = %(warehouse)s and company = %(company)s
		order by idx
		""",
		{"item": item_code, "warehouse": warehouse, "company": company},
	)
	return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Writes (each returns the change it made, for Undo)
# ---------------------------------------------------------------------------


def _set_capacity(rule, stock_uom):
	"""Give a rule :data:`CAPACITY` in the stock unit.

	``stock_capacity`` is set here because ERPNext's ``validate`` checks it *before* it computes
	it (``validate_capacity`` runs ahead of ``set_stock_capacity``), so a new rule left to
	ERPNext reads 0 and is refused "Insufficient Capacity" wherever there is stock."""
	rule.uom = stock_uom
	rule.conversion_factor = 1
	rule.capacity = CAPACITY
	rule.stock_capacity = CAPACITY


def _save_rule(rule, stock_uom, balance):
	"""Save an existing rule, past the module gate (``ignore_permissions``; see the module
	docstring). A rule made by hand in the Desk with a real capacity below what is now on hand
	would be refused on any save, a disable included, so it is given :data:`CAPACITY` first."""
	if flt(rule.stock_capacity) < flt(balance):
		_set_capacity(rule, stock_uom)
	rule.save(ignore_permissions=True)


def _ensure(item, location):
	"""Make the pair assigned. ``(rule name, change)``; the change is ``None`` when it already was."""
	existing = _rule_for(item.name, location.name)
	if existing and not cint(existing.disable):
		return existing.name, None
	balance = _balance(item.name, location.name)
	if existing:
		require_manage()
		rule = frappe.get_doc(RULE, existing.name)
		rule.disable = 0
		_save_rule(rule, item.stock_uom, balance)
		return rule.name, _rule_change(rule.name, location, created=0, disable=1)
	name = _new_rule(item, location, disable=0)
	return name, _rule_change(name, location)


def _rule_change(name, location, created=1, disable=None, created_disabled=0):
	change = {"kind": CHANGE_RULE, "rule": name, "created": created, "disable": disable}
	change["warehouse"] = location.name
	if created_disabled:
		change["created_disabled"] = 1
	return change


def _new_rule(item, location, disable):
	"""Insert the pair's rule (:data:`CAPACITY`, :data:`PRIORITY`), past the module gate."""
	require_manage()
	rule = frappe.new_doc(RULE)
	rule.item_code = item.name
	rule.warehouse = location.name
	rule.company = location.company
	rule.priority = PRIORITY
	rule.disable = disable
	_set_capacity(rule, item.stock_uom)
	rule.insert(ignore_permissions=True)
	return rule.name


def _disable_rules(item, location):
	"""Disable the pair's enabled rules (one, unless a duplicate slipped past ERPNext)."""
	changes = []
	names = _enabled_rules(item.name, location.name)
	if not names:
		return changes
	require_manage()
	balance = _balance(item.name, location.name)
	for name in names:
		rule = frappe.get_doc(RULE, name)
		rule.disable = 1
		_save_rule(rule, item.stock_uom, balance)
		changes.append(_rule_change(name, location, created=0, disable=0))
	return changes


def _undecided(item_code, warehouse):
	"""Whether the pair lists only because nobody has decided on it yet: a Bin row and no
	Putaway Rule at all (``api.stock_scan._items_at``'s fifth clause)."""
	return bool(
		frappe.db.exists("Bin", {"item_code": item_code, "warehouse": warehouse})
		and not _rule_for(item_code, warehouse)
	)


def _release(item, location, defaults):
	"""Take the item off ``location``: what *unassign* does, and the source side of a move that
	empties it. Disables the enabled rule; where the pair has **no rule at all** (stock first
	put there after v1.689.0, listed as undecided), records the decision as a new **disabled**
	rule, because without one an emptied Bin row keeps listing; and clears ``defaults``."""
	changes = _disable_rules(item, location)
	if not changes and not _rule_for(item.name, location.name):
		name = _new_rule(item, location, disable=1)
		changes.append(_rule_change(name, location, created=1, created_disabled=1))
	return changes + _clear_defaults(item.name, defaults, location.name)


def _source_note(location, left, released):
	"""The marker a move records about its source in ``rule_changes`` (Undo skips it): whether
	the source was released, or kept because stock is still there, and how much."""
	return {"kind": CHANGE_SOURCE, "warehouse": location.name, "released": int(released), "left": left}


def _touch_item(item_code):
	"""Move the Item's ``modified`` and drop its cached copy after a child-row write."""
	frappe.db.set_value(
		"Item",
		item_code,
		{"modified": now_datetime(), "modified_by": frappe.session.user},
		update_modified=False,
	)
	frappe.clear_document_cache("Item", item_code)


def _clear_defaults(item_code, rows, warehouse):
	"""Clear ``default_warehouse`` on these Item Default rows, and only that field: the row's
	other defaults (supplier, price list, accounts) stay. See the module docstring for why this
	is a row write rather than ``Item.save()``."""
	changes = []
	for name in rows:
		frappe.db.set_value("Item Default", name, "default_warehouse", None, update_modified=False)
		changes.append({"kind": CHANGE_ITEM_DEFAULT, "row": name, "item": item_code, "warehouse": warehouse})
	if changes:
		_touch_item(item_code)
	return changes


def _dump(changes):
	return json.dumps([change for change in changes if change], sort_keys=True)


def parse_changes(value):
	"""``Stock Scan Log.rule_changes`` as a list (empty when the save changed none).

	Written only by this module. If it cannot be read, Undo refuses rather than marking the
	save undone with its bin changes still in place."""
	if value in (None, ""):
		return []
	try:
		changes = json.loads(value) if isinstance(value, str) else value
	except ValueError:
		changes = None
	if not isinstance(changes, list) or not all(isinstance(c, dict) for c in changes):
		frappe.throw(
			_(
				"This save's record of its bin changes cannot be read, so it cannot be undone here. Ask a Stock Manager."
			)
		)
	return changes


def restore(changes):
	"""Put back what a save changed, newest change first, past the module gate (the caller,
	``api.stock_scan.undo``, has run :func:`require_manage`; the writes are the module's two).

	* A rule the save **created** is disabled, not deleted: disabled is how every reader here
	  and in ERPNext treats "no assignment", and a delete would be refused once a receipt has
	  routed stock by the rule.
	* A rule the save created **disabled** (:func:`_release` on a pair nobody had decided on) is
	  deleted while it is still disabled: the pair had no rule, and only
	  with none does an emptied Bin row list again as undecided. Nothing can link to it, because
	  ERPNext routes stock only by enabled rules. If someone has enabled it since, it stays.
	* A rule the save enabled or disabled gets its previous ``disable`` back.
	* An Item Default the save cleared gets its warehouse back, but only while it is still
	  blank: if somebody has since given the item another default warehouse, theirs stands.

	A rule deleted in the Desk since is skipped, and so is the ``source`` marker. Called by
	``api.stock_scan.undo`` after :func:`require_manage` and after any voucher is canceled, in
	the same transaction.
	"""
	touched = set()
	for change in reversed(changes):
		if change.get("kind") == CHANGE_RULE:
			name = change.get("rule")
			if not name or not frappe.db.exists(RULE, name):
				continue
			rule = frappe.get_doc(RULE, name)
			require_manage()
			if cint(change.get("created_disabled")):
				if cint(rule.disable):
					frappe.delete_doc(RULE, name, ignore_permissions=True)
				continue
			target = 1 if cint(change.get("created")) else cint(change.get("disable"))
			if cint(rule.disable) != target:
				rule.disable = target
				stock_uom = frappe.db.get_value("Item", rule.item_code, "stock_uom")
				_save_rule(rule, stock_uom, _balance(rule.item_code, rule.warehouse))
		elif change.get("kind") == CHANGE_ITEM_DEFAULT:
			row = frappe.db.get_value(
				"Item Default", change.get("row"), ["parent", "default_warehouse"], as_dict=True
			)
			if not row or row.parent != change.get("item") or row.default_warehouse:
				continue
			require_manage()
			frappe.db.set_value(
				"Item Default",
				change.get("row"),
				"default_warehouse",
				change.get("warehouse"),
				update_modified=False,
			)
			touched.add(row.parent)
	for item_code in touched:
		_touch_item(item_code)


# ---------------------------------------------------------------------------
# The plain helper
# ---------------------------------------------------------------------------


def ensure_assignment(item_code, warehouse):
	"""Put ``item_code`` on the bin ``warehouse``; return the Putaway Rule's name.

	Idempotent: the existing enabled rule is returned as it is, a disabled one is re-enabled,
	and otherwise one is created (:data:`CAPACITY`, :data:`PRIORITY`, the stock unit, the
	warehouse's company). Passes the module gate (:func:`require_manage`: a stock role and read
	on Item) only when it changes something -- an existing enabled rule comes back to anyone --
	and then writes the rule past ERPNext's own Putaway Rule permission, as every assignment
	here does (module docstring). Refuses in a sentence -- an unknown, disabled,
	non-stock or template item, a group, disabled or transit warehouse, a caller who may not --
	before writing. Writes no log row and takes no ``client_ref``: it is a building block for
	callers that keep their own record (the guided Item flow, ``api.item_flow.save_stock``).
	Keep this signature stable; that flow calls it.
	"""
	item, location = _assignable(item_code, warehouse)
	existing = _rule_for(item.name, location.name)
	if not (existing and not cint(existing.disable)):
		_check_capacity(item, location)
	name, _change = _ensure(item, location)
	return name


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def assign(item_code, warehouse, client_ref):
	"""Put an item on a bin."""
	scan._check_access()
	ref = scan._client_ref(client_ref)
	done = scan._already_saved(ref)
	if done:
		return done

	item, location = _assignable(item_code, warehouse)
	require_manage()
	if _enabled_rules(item.name, location.name):
		frappe.throw(
			_("{0} is already on {1}.").format(
				item.item_name or item.name, location.warehouse_name or location.name
			)
		)
	_check_capacity(item, location)

	log = scan._begin_log(ref, rules.ACTION_ASSIGN, item, 0, location)
	rule, change = _ensure(item, location)
	scan._finish_log(log, None, None, putaway_rule=rule, rule_changes=_dump([change]))
	return scan._result(log.name)


@frappe.whitelist(methods=["POST"])
def unassign(item_code, warehouse, client_ref):
	"""Take an item off a bin: disable its rule there and clear an Item Default pointing at it.

	Refused while stock is still recorded there (``stock_scan_rules.unassign_refusal``): the
	page offers Move first. An item still **on order** to the bin is taken off, and the result
	says it stays listed until that order is received or closed (``api.stock_scan._items_at``).
	A disabled or non-stock item may still be taken off. An item listed only because it has a
	Bin row there and no rule at all is taken off by recording a **disabled** rule
	(:func:`_release`).
	"""
	scan._check_access()
	ref = scan._client_ref(client_ref)
	done = scan._already_saved(ref)
	if done:
		return done

	item = _item(item_code)
	location = scan._location(warehouse)
	require_manage()
	label, where = item.item_name or item.name, location.warehouse_name or location.name
	on_hand, _reserved = scan._bin(item.name, location.name)
	problem = rules.unassign_refusal(on_hand, item.stock_uom, label, where)
	if problem:
		frappe.throw(problem)
	assigned = _enabled_rules(item.name, location.name)
	defaults = _defaults_at(item.name, location.name, location.company)
	if not assigned and not defaults and not _undecided(item.name, location.name):
		frappe.throw(_("{0} is not on {1}.").format(label, where))

	log = scan._begin_log(ref, rules.ACTION_UNASSIGN, item, 0, location)
	changes = _release(item, location, defaults)
	scan._finish_log(log, None, None, putaway_rule=_first_rule(changes), rule_changes=_dump(changes))
	return scan._result(log.name)


def _first_rule(changes):
	return next((c["rule"] for c in changes if c and c.get("kind") == CHANGE_RULE), None)


def _plan_source(item, source, qty):
	"""``(release, left, defaults)`` for a move out of ``source``, decided before anything is
	posted: the source is released (its rule disabled, its Item Default cleared) only when the
	move empties it. Stock left behind keeps the bin's claim on it -- the same rule that refuses
	*unassign* with stock on hand."""
	left = max(_balance(item.name, source.name) - flt(qty), 0.0)
	release = left <= rules.TOLERANCE
	defaults = _defaults_at(item.name, source.name, source.company) if release else []
	return release, (0.0 if release else left), defaults


@frappe.whitelist(methods=["POST"])
def move_to(item_code, from_warehouse, to_warehouse, qty, client_ref, make_home=0):
	"""Push stock out of the bin being managed (``from_warehouse``) into another bin: a submitted
	Material Transfer, the same voucher *Move here* posts (``api.stock_scan._transfer_entry``).

	``make_home=1`` also moves the assignment: the item is put on ``to_warehouse`` and, **when
	the move empties** ``from_warehouse``, taken off it (:func:`_release`: its rule disabled, an
	Item Default pointing at it cleared). A partial move keeps the source assigned and says so
	(``log.source_kept`` and the message). The boneyard is refused as a home before anything is
	posted. Off by default; the pages tick it when the whole quantity moves.
	"""
	scan._check_access()
	ref = scan._client_ref(client_ref)
	done = scan._already_saved(ref)
	if done:
		return done

	location = scan._location(from_warehouse)
	destination = scan._location(to_warehouse)
	item = scan._stock_item(item_code)
	qty = scan._checked_qty(qty, item)
	source = scan._transfer_source(location.name, destination, item, qty)
	home = bool(cint(make_home))
	release, left, defaults = False, 0.0, []
	if home:
		_refuse_boneyard(destination)
		require_manage()
		_check_capacity(item, destination, adding=qty)
		release, left, defaults = _plan_source(item, source, qty)
	scan._require("Stock Entry", "create", "submit")

	log = scan._begin_log(ref, rules.ACTION_MOVE_TO, item, qty, destination, from_warehouse=source.name)
	se = scan._transfer_entry(rules.ACTION_MOVE_TO, item, qty, source, destination)
	scan._post(se)
	rule, changes = None, []
	if home:
		rule, change = _ensure(item, destination)
		changes = [change, *(_release(item, source, defaults) if release else [])]
		changes.append(_source_note(source, left, release))
	scan._finish_log(
		log, "Stock Entry", se.name, putaway_rule=rule, rule_changes=_dump(changes) if home else None
	)
	return scan._result(log.name)


def _boneyard_for(source):
	"""The Boneyard warehouse for stock leaving ``source``, or throw a sentence.

	Read from Inventory Scanner Settings (``boneyard_warehouse``, through ``get_settings``),
	which ``patches/add_boneyard_warehouse`` fills with the ``Boneyard`` warehouse it creates.
	It must be a usable location (a leaf, enabled, not transit), of ``source``'s company, and
	not ``source`` itself. Blank or disabled is a Stock Manager's to fix, so the sentence says so.
	"""
	name = cstr(scan.get_settings().get("boneyard_warehouse")).strip()
	row = scan._warehouse(name) if name else None
	if not row or cint(row.disabled):
		frappe.throw(
			_(
				"There is no boneyard to send stock to. A Stock Manager has to set the Boneyard Warehouse in Inventory Scanner Settings."
			)
		)
	problem = scan._location_problem(row, name)
	if problem:
		frappe.throw(
			_(
				"The boneyard in Inventory Scanner Settings cannot take stock: {0} Ask a Stock Manager."
			).format(problem)
		)
	if row.company != source.company:
		frappe.throw(
			_("The boneyard {0} belongs to {1}, not {2}. Ask a Stock Manager.").format(
				row.warehouse_name or row.name, row.company, source.company
			)
		)
	if row.name == source.name:
		frappe.throw(_("This is the boneyard already."))
	return row


@frappe.whitelist(methods=["POST"])
def send_to_boneyard(item_code, from_warehouse, qty, client_ref):
	"""Send stock a bin no longer keeps to the boneyard, and take the item off that bin.

	A submitted Material Transfer of ``qty`` from ``from_warehouse`` into the Boneyard set in
	Inventory Scanner Settings (:func:`_boneyard_for`), built by the same helper as *Move To*
	and *Move here*. When that empties the source bin, the item is taken off it as
	:func:`unassign` would (:func:`_release`); when stock is left there, the source keeps it and
	the result says so (``log.source_kept``). **No rule is ever made at the Boneyard**: stock
	there lists because it is on hand, and drops off when it is gone. Undo cancels the transfer
	and restores exactly what was changed (``api.stock_scan.undo``).

	Needs the module gate (it is an assignment action) as well as Stock Entry create and submit
	(the transfer posts as the user), whether or not the item had a rule at the source.
	"""
	scan._check_access()
	ref = scan._client_ref(client_ref)
	done = scan._already_saved(ref)
	if done:
		return done

	location = scan._location(from_warehouse)
	item = scan._stock_item(item_code)
	qty = scan._checked_qty(qty, item)
	boneyard = _boneyard_for(location)
	source = scan._transfer_source(location.name, boneyard, item, qty)
	require_manage()
	release, left, defaults = _plan_source(item, source, qty)
	scan._require("Stock Entry", "create", "submit")

	log = scan._begin_log(ref, rules.ACTION_BONEYARD, item, qty, boneyard, from_warehouse=source.name)
	se = scan._transfer_entry(rules.ACTION_BONEYARD, item, qty, source, boneyard)
	scan._post(se)
	changes = _release(item, source, defaults) if release else []
	changes.append(_source_note(source, left, release))
	scan._finish_log(
		log,
		"Stock Entry",
		se.name,
		putaway_rule=_first_rule(changes),
		rule_changes=_dump(changes),
	)
	return scan._result(log.name)


def _require_count():
	if not (
		frappe.has_permission("Stock Reconciliation", "create")
		and frappe.has_permission("Stock Reconciliation", "submit")
	):
		frappe.throw(
			_(
				"Correcting a count posts a Stock Reconciliation, which needs the Stock Manager role. Ask a Stock Manager."
			),
			frappe.PermissionError,
		)


def _reconciliation(item, qty, rate, location):
	"""The unsaved Stock Reconciliation setting ``item`` at ``location`` to ``qty``.

	* ``expense_account`` from the one resolver every stock voucher here uses
	  (``stock_accounts.difference_account``): ERPNext's own default is the Company's Stock
	  Adjustment Account, which production does not have, and ``validate_expense_account``
	  refuses a reconciliation without one.
	* ``valuation_rate`` only when the bin is empty now (``rate``): from zero there is no
	  current rate for ERPNext to keep, and its own fallback is a buying price or the Item's
	  Valuation Rate, or zero. Otherwise it is left blank, which ERPNext reads as "keep the
	  current rate", so a recount changes the quantity and not the value of each unit.
	"""
	settings = scan.get_settings()
	sr = frappe.new_doc("Stock Reconciliation")
	sr.purpose = "Stock Reconciliation"
	sr.company = location.company
	sr.expense_account = scan._difference_account(location.company, item.name)
	cost_center = scan._cost_center(settings.get("scan_cost_center"), location.company)
	if cost_center:
		sr.cost_center = cost_center
	row = {"item_code": item.name, "warehouse": location.name, "qty": qty}
	if rate:
		row["valuation_rate"] = rate
	sr.append("items", row)
	return sr


@frappe.whitelist(methods=["POST"])
def set_count(item_code, warehouse, qty, client_ref):
	"""Correct what is on hand at a bin to ``qty`` (zero allowed): a submitted Stock
	Reconciliation for one item at one warehouse.

	Not a Material Issue: a count is not parts used, it must not charge a job or read as
	consumption, and it has to be able to go *up* as well as down. Refused in a sentence when
	it matches what the system already shows, when it is below what is reserved there, and when
	the bin is empty and the item has no cost on record (``api.stock_scan._receipt_rate``).
	"""
	scan._check_access()
	ref = scan._client_ref(client_ref)
	done = scan._already_saved(ref)
	if done:
		return done

	location = scan._location(warehouse)
	item = scan._stock_item(item_code)
	count, problem = rules.check_count(
		qty, whole_number=scan._whole_number(item.stock_uom), uom=item.stock_uom
	)
	if problem:
		frappe.throw(problem)
	on_hand, reserved = scan._bin(item.name, location.name)
	problem = rules.check_recount(
		count, on_hand, reserved, item.stock_uom, location.warehouse_name or location.name
	)
	if problem:
		frappe.throw(problem)
	rate = None
	if on_hand <= rules.TOLERANCE and count > rules.TOLERANCE:
		rate = scan._receipt_rate(item.name, location.name, location.company)
		if rate <= 0:
			frappe.throw(
				_(
					"There is no cost on record for {0}, so a count cannot add it. Ask a Stock Manager to set its Valuation Rate."
				).format(item.item_name or item.name)
			)
	_require_count()

	log = scan._begin_log(ref, rules.ACTION_COUNT, item, count, location, qty_before=on_hand)
	sr = _reconciliation(item, count, rate, location)
	scan._post(sr)
	scan._finish_log(log, "Stock Reconciliation", sr.name)
	return scan._result(log.name)
