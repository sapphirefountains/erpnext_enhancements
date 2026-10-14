"""Put every item on the bins it already lists at, as Putaway Rules (v1.689.0).

From v1.689.0 the Stock Scan page lists an item at a location when it is on hand there, on
order to there, its default warehouse, or **assigned** there by an enabled Putaway Rule
(``api.stock_scan._items_at``). Until then it listed every item with *any* ``tabBin`` row at the
location, even at zero. Production had no Putaway Rules, so without this patch every item
sitting at zero in a bin it belongs to would drop off that bin's list the moment the release
deployed -- the empty bins are the ones a receiver most needs to find.

So: **one enabled rule per (enabled stock item, enabled leaf warehouse) pair that has a
``tabBin`` row today**, unless the pair already has a rule (enabled or disabled: ERPNext allows
one per pair, and a disabled one is somebody's decision). On 2026-10-14 that was 536 pairs:
399 at the ``Bin …`` warehouses (390 with stock, 9 at zero), 83 at ``Inventory Room - SF``
(55 / 28) and 54 at ``Stores - SF`` (12 / 42); no transit warehouse had a row and none was
negative. :data:`ONLY_WAREHOUSES_STARTING_WITH` cuts it to the ``Bin …`` warehouses if the two
rooms turn out not to be "bins" (an open question for the owner); with it set, items in the
rooms list there only while they have stock, are on order, or have the room as their default.

Each rule is shaped exactly as ``inventory_enhancements.bin_assignments`` makes them
(``CAPACITY`` in the stock unit, priority 1, the warehouse's company) and inserted as the
patch's user (Administrator) with **the Putaway Rule controller's validate skipped**
(``flags.ignore_validate``). Its five checks are made here instead, once, from rows this patch
has already read -- duplicate (pairs with a rule are skipped), warehouse and company (the rule
takes the warehouse's own company), capacity (a pair holding more than ``CAPACITY`` is skipped
and reported), priority (1) and stock capacity (set) -- because ``validate_capacity`` runs a
stock-ledger balance query per rule, 536 of them to confirm what one ``tabBin`` read already
says. Frappe's own checks (mandatory fields, links) still run on insert.

**Dry run.** Nik can preview the rows read-only, before deploying, with the same selection::

    select b.item_code, b.warehouse, w.company, b.actual_qty
    from `tabBin` b
    join `tabItem` i on i.name = b.item_code
        and i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
    join `tabWarehouse` w on w.name = b.warehouse
        and w.is_group = 0 and w.disabled = 0 and coalesce(w.warehouse_type, '') <> 'Transit'
    left join `tabPutaway Rule` r on r.item_code = b.item_code and r.warehouse = b.warehouse
    where r.name is null
    order by b.warehouse, b.item_code;

(add ``and b.warehouse like 'Bin %'`` for the bins-only cut.)

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the
deploy. A missing Putaway Rule table (a fresh install before ERPNext's) makes it a no-op; each
insert runs in its own savepoint, and a pair that fails is skipped, counted and listed in one
Error Log (written only when something was skipped). Safe twice: the second run finds every
pair already has its rule.
"""

import frappe

#: ``None``: every enabled leaf warehouse with a Bin row. Set to ``"Bin "`` to leave the two
#: rooms (``Inventory Room - SF``, ``Stores - SF``) out of the backfill.
ONLY_WAREHOUSES_STARTING_WITH = None

SAVEPOINT = "backfill_bin_putaway_rules"
LOG = "backfill_bin_putaway_rules"


def _say(text):
	"""Print, and never raise: a console that cannot encode a name must not abort the deploy."""
	try:
		print(text)
	except Exception:
		pass


def select_pairs(bins, items, warehouses, existing, capacity, prefix=None):
	"""The pairs to give a rule, and the ones refused. Pure: no frappe, so a test can run it.

	``bins``: rows with ``item_code``, ``warehouse``, ``actual_qty`` (one per ``tabBin`` row).
	``items``: ``{item_code: row}`` with ``is_stock_item``, ``disabled``, ``has_variants``,
	``stock_uom``. ``warehouses``: ``{name: row}`` with ``is_group``, ``disabled``, ``company``,
	``warehouse_type``. ``existing``: the set of ``(item_code, warehouse)`` pairs that already
	have a rule, enabled or not.

	Returns ``(pairs, refused)``: ``pairs`` a sorted list of dicts (``item_code``, ``warehouse``,
	``company``, ``stock_uom``), ``refused`` a list of ``(item_code, warehouse, reason)`` for
	pairs in scope that ERPNext would refuse. Pairs out of scope (a disabled item, a group
	warehouse, a rule already there) are simply not returned.
	"""
	pairs, refused, seen = [], [], set()
	for row in bins:
		item_code, warehouse = row.get("item_code"), row.get("warehouse")
		key = (item_code, warehouse)
		if not item_code or not warehouse or key in seen or key in existing:
			continue
		seen.add(key)
		item, wh = items.get(item_code), warehouses.get(warehouse)
		if not item or not wh:
			continue
		if int(item.get("disabled") or 0) or not int(item.get("is_stock_item") or 0):
			continue
		if int(item.get("has_variants") or 0):
			continue
		if int(wh.get("is_group") or 0) or int(wh.get("disabled") or 0):
			continue
		if (wh.get("warehouse_type") or "") == "Transit":
			continue
		if prefix and not str(warehouse).startswith(prefix):
			continue
		if not wh.get("company"):
			refused.append((item_code, warehouse, "the warehouse has no company"))
			continue
		if float(row.get("actual_qty") or 0) > capacity:
			refused.append((item_code, warehouse, "more on hand than the rule's capacity"))
			continue
		pairs.append(
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"company": wh.get("company"),
				"stock_uom": item.get("stock_uom"),
			}
		)
	pairs.sort(key=lambda p: (p["warehouse"], p["item_code"]))
	return pairs, refused


def _read():
	bins = frappe.db.sql(
		"select item_code, warehouse, actual_qty from `tabBin`",
		as_dict=True,
	)
	items = {
		row.name: row
		for row in frappe.db.sql(
			"""
			select name, is_stock_item, disabled, has_variants, stock_uom from `tabItem`
			where name in (select distinct item_code from `tabBin`)
			""",
			as_dict=True,
		)
	}
	warehouses = {
		row.name: row
		for row in frappe.db.sql(
			"select name, is_group, disabled, company, warehouse_type from `tabWarehouse`",
			as_dict=True,
		)
	}
	existing = {
		(row[0], row[1]) for row in frappe.db.sql("select item_code, warehouse from `tabPutaway Rule`")
	}
	return bins, items, warehouses, existing


def _insert(pair, capacity, priority):
	rule = frappe.new_doc("Putaway Rule")
	rule.item_code = pair["item_code"]
	rule.warehouse = pair["warehouse"]
	rule.company = pair["company"]
	rule.priority = priority
	rule.uom = pair["stock_uom"]
	rule.conversion_factor = 1
	rule.capacity = capacity
	rule.stock_capacity = capacity
	rule.disable = 0
	rule.flags.ignore_validate = True
	rule.insert(ignore_permissions=True)


def execute():
	created, failed, refused = 0, [], []
	try:
		if not frappe.db.table_exists("Putaway Rule"):
			_say(f"{LOG}: there is no Putaway Rule table; nothing to do.")
			return
		from erpnext_enhancements.inventory_enhancements.bin_assignments import CAPACITY, PRIORITY

		pairs, refused = select_pairs(*_read(), capacity=CAPACITY, prefix=ONLY_WAREHOUSES_STARTING_WITH)
		for pair in pairs:
			try:
				frappe.db.savepoint(SAVEPOINT)
				_insert(pair, CAPACITY, PRIORITY)
				created += 1
			except Exception as exc:
				try:
					frappe.db.rollback(save_point=SAVEPOINT)
				except Exception:
					pass
				failed.append((pair["item_code"], pair["warehouse"], str(exc)[:200]))
	except Exception:
		try:
			frappe.log_error(title="Bin Putaway Rule backfill failed", message=frappe.get_traceback())
		except Exception:
			pass
		_say(f"{LOG}: aborted early; see the Error Log.")
		return
	_say(f"{LOG}: created {created} rules; skipped {len(refused) + len(failed)}.")
	skipped = refused + failed
	if skipped:
		try:
			frappe.log_error(
				title="Bin Putaway Rule backfill skipped some pairs",
				message="\n".join(f"{item} @ {warehouse}: {reason}" for item, warehouse, reason in skipped),
			)
		except Exception:
			pass
