"""Whitelisted endpoints for the Stock Scan page (``/stock-scan``).

Every stock-holding Warehouse carries a QR label (``www/warehouse-labels.html``) that
encodes ``<site>/stock-scan?w=<warehouse>``. Scanning it with a phone's camera opens the
page on that location: the items kept there, each with a − / + stepper and Save. Nothing
here is a draft — a Save changes inventory the moment it returns, which is what the
people using it asked for (a technician grabbing parts, a receiver putting stock away),
and every save can be undone from the page for a while afterwards.

What a Save posts, by what the person did:

* **Take** (−) → a submitted **Stock Entry, Material Issue** from the location, tagged
  with the job the technician picked for the run, if any. The cost lands on the job
  through ERPNext's own ``update_cost_in_project``.
* **Receive** (+, against an open order line) → a submitted **Purchase Receipt** into the
  scanned location, through ``api.procurement.receive_order_line`` — the same checks,
  the same mapper and the same over-receipt rule as the order's Receive Items dialog.
* **Add Without PO** (+, no order) → a submitted **Stock Entry, Material Receipt** at the
  item's current cost, and the log row is flagged ``needs_review`` for a Stock Manager.
* **Move** (put-away) → a submitted **Stock Entry, Material Transfer** from wherever the
  stock is recorded into the scanned location. On the day this shipped every bin on
  production was empty and all stock sat in ``Stores - SF``, so this is how the bins fill.
* **Store Run** (+, "Bought on a store run", v1.535.0) → a submitted **Purchase Receipt**
  with no purchase order, one per line, from a Supplier ticked *Store-Run Vendor*, at the
  price on the paper receipt, flagged ``needs_review`` for Purchasing. Every receipt of one
  trip carries the same run id (``custom_store_run``), the receipt photo and the receipt's
  total. A part not in ERPNext can be created as a quick Item on the same save
  (:func:`check_new_item` previews the naming guard's verdict). See :func:`store_run`.

Each save also writes a ``Stock Scan Log`` row: the page's history, its Undo, and the
review queue.

Things this module is careful about, each of which the obvious version gets wrong:

* **Permissions are the framework's.** After the role gate (``stock_scan_rules.SCAN_ROLES``)
  every voucher is inserted, submitted and cancelled **without** ``ignore_permissions``,
  so a user who could not post the voucher in the Desk cannot post it from here either,
  and User Permission restrictions on Warehouse or Project still apply. Only the log row,
  which is this page's own record, is written with ``ignore_permissions``.
* **A retried save does not post twice.** The page mints a ``client_ref`` per save and
  sends the same one on a retry. It is unique on the log, and the log row is inserted
  *before* the voucher in the same transaction, so a double tap or a retry after a
  dropped connection finds the first save instead of making a second.
* **``stock_entry_type`` is always set.** It is mandatory on v16 and ``purpose`` is
  fetched from it on insert; setting only ``purpose`` raises ``MandatoryError``.
* **Every row carries a difference account.** Production's Company has no Stock
  Adjustment Account, so ERPNext's own chain (Item Default → Item Group → Company) comes
  up empty and a Material Issue refuses to insert. :func:`_difference_account` walks that
  chain, then the Inventory Scanner Settings account, then the company's one Stock
  Adjustment account.
* **A receipt carries an explicit rate.** A Material Receipt with no ``basic_rate`` is
  refused at submit with "Valuation Rate Missing" — or, submitted straight from new,
  silently posts stock at zero value. :func:`_receipt_rate` finds one or the save is
  refused with a sentence.
* **Taking more than is on hand is refused before a document exists**, in words that
  say what to do (``stock_scan_rules.check_take``). ERPNext would refuse it too, with
  either "Insufficient Stock" or "Valuation Rate Missing" depending on the bin's history.
* **Serial, batch, variant, customer-provided and non-stock items are refused up front**
  (``stock_scan_rules.item_refusal``). ERPNext would auto-pick serial numbers FIFO on an
  issue, which is wrong for serialised equipment.
* **``resolve`` never throws to answer a question.** A caught ``frappe.throw`` still
  queues a message (it msgprints before raising), so an unknown or unusable scan comes back
  as ``{kind: "unknown", message}`` and the boot payload resolves the same way.
  ``get_location`` and ``get_item`` do throw on a bad location or an unknown item — the page
  asks for those by name, and shows the refusal inline like any other.

Indentation is tabs (new file; ``api/README.md`` lists the package's mix).
"""

import re

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	cstr,
	flt,
	formatdate,
	get_datetime,
	getdate,
	now_datetime,
	nowdate,
	strip_html,
)

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
	get_settings,
)
from erpnext_enhancements.inventory_enhancements.stock_accounts import difference_account

LOG = "Stock Scan Log"

#: How many of the caller's own saves the page lists under Recent.
RECENT_LIMIT = 12

#: Rows a search returns. The page is a phone screen; past this people type more letters.
SEARCH_LIMIT = 25

#: Items a location lists. A shelf bin holds a handful; ``Stores - SF`` holds more, and a
#: page past this is not something anyone scrolls on a phone. ``truncated`` says so.
LOCATION_ITEM_LIMIT = 150

#: What a ``client_ref`` may look like. The page mints ``ss-<time>-<random>``.
_CLIENT_REF = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")

#: Purchase Receipt custom fields behind a store run (``patches/add_store_run_receipt_fields``):
#: the run id every receipt of one trip carries, the photo of the paper receipt, and the
#: receipt's total with tax. ``tests/test_stock_scan_surface.py`` pins these names to the patch.
RUN_FIELD = "custom_store_run"
PHOTO_FIELD = "custom_receipt_photo"
TOTAL_FIELD = "custom_receipt_total"

#: The Supplier flag that makes a supplier a walk-in counter (``patches/split_service_kpi_dashboard``).
SUPPLIER_FLAG = "custom_store_run_vendor"

#: Neighbours ``check_new_item`` reads before dropping QuickBooks tombstones and the code
#: itself, and how many it shows.
SIMILAR_READ = 25
SIMILAR_SHOW = 5


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def _check_access():
	if not rules.SCAN_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to use Stock Scan."), frappe.PermissionError)


def _is_supervisor():
	return bool(rules.SUPERVISOR_ROLES.intersection(frappe.get_roles()))


def _is_purchasing():
	"""Purchasing or Accounts: who may undo a store-run line after its window (``undo_refusal``)."""
	return bool(rules.STORE_RUN_UNDO_ROLES.intersection(frappe.get_roles()))


def _require(doctype, *ptypes):
	"""Refuse in the user's terms before building a voucher they could not submit."""
	for ptype in ptypes:
		if not frappe.has_permission(doctype, ptype):
			frappe.throw(
				_("You need permission to {0} a {1}. Ask a Stock Manager for the Stock User role.").format(
					_(ptype), _(doctype)
				),
				frappe.PermissionError,
			)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

_WAREHOUSE_FIELDS = [
	"name",
	"warehouse_name",
	"company",
	"is_group",
	"disabled",
	"warehouse_type",
	"lft",
	"rgt",
]


def _warehouse(name):
	name = cstr(name).strip()
	if not name:
		return None
	return frappe.db.get_value("Warehouse", name, _WAREHOUSE_FIELDS, as_dict=True)


def _location_problem(row, name=""):
	"""Why ``row`` cannot be scanned as a location, or ``None``."""
	if not row:
		return _("There is no location called {0}.").format(name or _("that"))
	label = row.warehouse_name or row.name
	if row.is_group:
		return _(
			"{0} is a group of locations, not a shelf or bin. Scan the label on one of the locations inside it."
		).format(label)
	if row.disabled:
		return _("{0} is disabled.").format(label)
	if (row.warehouse_type or "") == "Transit":
		return _("{0} is a transit warehouse, not a place stock is kept.").format(label)
	return None


def _location(name):
	"""The warehouse row for a scannable location, or throw in the user's terms."""
	row = _warehouse(name)
	problem = _location_problem(row, cstr(name).strip())
	if problem:
		frappe.throw(problem)
	return row


def _ancestor_names(row):
	"""The location's group parents, outermost first, as ``warehouse_name``."""
	if not row or row.lft is None:
		return []
	parents = frappe.get_all(
		"Warehouse",
		filters={"lft": ["<", row.lft], "rgt": [">", row.rgt]},
		fields=["name", "warehouse_name"],
		order_by="lft asc",
	)
	return [parent.warehouse_name or parent.name for parent in parents]


def _warehouse_names(names):
	names = [name for name in set(names or []) if name]
	if not names:
		return {}
	rows = frappe.get_all("Warehouse", filters={"name": ["in", names]}, fields=["name", "warehouse_name"])
	return {row.name: row.warehouse_name or row.name for row in rows}


def _location_payload(row):
	items, truncated = _items_at(row)
	return {
		"warehouse": row.name,
		"warehouse_name": row.warehouse_name or row.name,
		"trail": rules.location_trail(_ancestor_names(row)),
		"company": row.company,
		"items": items,
		"truncated": truncated,
	}


def _items_at(row):
	"""The items kept at a location: every Bin there (an emptied bin still lists its item,
	so the next receiver can restock it) plus every item whose default warehouse it is.

	Only stock items that are not disabled; the item's own refusal is shown when opened.
	Ordered by what is on hand first, then name.

	One query, filtered to stock items in SQL: ``Stores - SF`` is the default warehouse of
	741 Items on production, almost all of them non-stock, so capping the defaults before
	filtering them would drop real stock items off the end.
	"""
	items = frappe.db.sql(
		"""
		select i.name, i.item_name, i.image, i.stock_uom, coalesce(b.actual_qty, 0) as on_hand
		from `tabItem` i
		left join `tabBin` b on b.item_code = i.name and b.warehouse = %(warehouse)s
		where i.disabled = 0 and i.is_stock_item = 1
			and (
				b.name is not null
				or exists (
					select 1 from `tabItem Default` d
					where d.parent = i.name and d.parenttype = 'Item'
						and d.default_warehouse = %(warehouse)s and d.company = %(company)s
				)
			)
		""",
		{"warehouse": row.name, "company": row.company},
		as_dict=True,
	)
	if not items:
		return [], False
	rows = [
		{
			"item_code": item.name,
			"item_name": item.item_name or item.name,
			"image": item.image or None,
			"stock_uom": item.stock_uom,
			"on_hand": flt(item.on_hand),
		}
		for item in items
	]
	rows.sort(key=lambda r: (r["on_hand"] <= 0, rules.natural_key(r["item_name"])))
	return rows[:LOCATION_ITEM_LIMIT], len(rows) > LOCATION_ITEM_LIMIT


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

_ITEM_FIELDS = [
	"name",
	"item_name",
	"description",
	"image",
	"stock_uom",
	"is_stock_item",
	"disabled",
	"end_of_life",
	"has_serial_no",
	"has_batch_no",
	"has_variants",
	"is_customer_provided_item",
]


def _item(item_code):
	item_code = cstr(item_code).strip()
	if not item_code:
		return None
	return frappe.db.get_value("Item", item_code, _ITEM_FIELDS, as_dict=True)


def _stock_item(item_code):
	"""The Item row for a save, or throw the item's refusal."""
	item = _item(item_code)
	problem = rules.item_refusal(item, nowdate()) if item else _("There is no item {0}.").format(item_code)
	if problem:
		frappe.throw(problem)
	return item


def _whole_number(uom):
	return bool(cint(frappe.get_cached_value("UOM", uom, "must_be_whole_number"))) if uom else False


def _bin(item_code, warehouse):
	row = frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["actual_qty", "reserved_stock"],
		as_dict=True,
	)
	return flt(row.actual_qty) if row else 0.0, flt(row.reserved_stock) if row else 0.0


def _available(item_code, warehouse):
	"""On hand less anything reserved: what a take or a move may use."""
	on_hand, reserved = _bin(item_code, warehouse)
	return on_hand - reserved


def _elsewhere(item_code, company, exclude=None):
	"""Where else the item is on hand, most first — the sources a Move can draw from."""
	bins = frappe.get_all(
		"Bin",
		filters={"item_code": item_code, "actual_qty": [">", 0]},
		fields=["warehouse", "actual_qty", "reserved_stock"],
	)
	names = [row.warehouse for row in bins if row.warehouse != exclude]
	if not names:
		return []
	meta = {
		row.name: row
		for row in frappe.get_all(
			"Warehouse",
			filters={"name": ["in", names]},
			fields=["name", "warehouse_name", "company", "is_group", "disabled"],
		)
	}
	out = []
	for row in bins:
		wh = meta.get(row.warehouse)
		if not wh or wh.is_group or wh.disabled or (company and wh.company != company):
			continue
		out.append(
			{
				"warehouse": row.warehouse,
				"warehouse_name": wh.warehouse_name or row.warehouse,
				"on_hand": flt(row.actual_qty),
				"available": flt(row.actual_qty) - flt(row.reserved_stock),
			}
		)
	out.sort(key=lambda r: (-r["on_hand"], rules.natural_key(r["warehouse_name"])))
	return out


def _open_order_lines(item_code, company):
	"""Open Purchase Order lines for one item in one company, oldest promise first.

	"Open" is ERPNext's own "still on order" rule (``stock_balance.get_purchase_order_qty``)
	plus On Hold, which a receipt refuses: submitted, not Closed / On Hold / Delivered,
	something still to come, not drop-shipped, not subcontracted, not an internal transfer.
	An exclusion list rather than an allow-list of statuses, because v16 shows a PO waiting
	on an advance as "To Pay" even when nothing has arrived. Pending is compared with
	``procurement_quantities.TOLERANCE`` — the planner's own — so this never offers a line
	the receipt would then call "already fully received".

	Query builder, not ``get_all``: the filter compares two columns. It bypasses
	permissions, so read on Purchase Order is checked first; ``receive_items`` checks it
	again on the one order actually received against.
	"""
	from frappe.query_builder.functions import Coalesce

	from erpnext_enhancements.procurement_quantities import TOLERANCE

	if not company or not frappe.has_permission("Purchase Order", "read"):
		return []
	po = frappe.qb.DocType("Purchase Order")
	poi = frappe.qb.DocType("Purchase Order Item")
	rows = (
		frappe.qb.from_(poi)
		.inner_join(po)
		.on(po.name == poi.parent)
		.select(
			poi.name.as_("purchase_order_item"),
			poi.parent.as_("purchase_order"),
			poi.idx,
			poi.qty,
			poi.received_qty,
			poi.uom,
			poi.conversion_factor,
			poi.schedule_date,
			poi.expected_delivery_date,
			po.transaction_date,
			po.supplier,
			po.supplier_name,
			po.status,
		)
		.where(poi.parenttype == "Purchase Order")
		.where(poi.item_code == item_code)
		.where(po.company == company)
		.where(po.docstatus == 1)
		.where(po.status.notin(["Closed", "On Hold", "Delivered"]))
		.where(poi.qty > poi.received_qty)
		.where(Coalesce(poi.delivered_by_supplier, 0) == 0)
		.where(Coalesce(po.is_subcontracted, 0) == 0)
		.where(Coalesce(po.is_internal_supplier, 0) == 0)
		.limit(50)
	).run(as_dict=True)

	lines = []
	for row in rows:
		pending = flt(row.qty) - flt(row.received_qty)
		if pending <= TOLERANCE:
			continue
		lines.append(
			{
				"purchase_order": row.purchase_order,
				"purchase_order_item": row.purchase_order_item,
				"idx": row.idx,
				"supplier": row.supplier,
				"supplier_name": row.supplier_name or row.supplier,
				"schedule_date": str(row.schedule_date) if row.schedule_date else None,
				"expected_delivery_date": str(row.expected_delivery_date)
				if row.expected_delivery_date
				else None,
				"transaction_date": str(row.transaction_date) if row.transaction_date else None,
				"uom": row.uom,
				"conversion_factor": flt(row.conversion_factor) or 1.0,
				"ordered_qty": flt(row.qty),
				"pending_qty": pending,
				"pending_stock_qty": rules.pending_stock_qty(row),
			}
		)
	lines.sort(key=rules.order_line_sort_key)
	return lines


def _plain_description(item):
	text = strip_html(cstr(item.description or "")).strip()
	text = re.sub(r"\s+", " ", text)
	if not text or text == (item.item_name or "").strip():
		return None
	return text[:280] + ("…" if len(text) > 280 else "")


def _reorder_level(item_code):
	"""The item's highest reorder level in ERPNext: above 0 makes it a *stocked item*, the KPI
	dashboard's own definition, and the store-run reason is read against it."""
	row = frappe.db.sql(
		"""
		select max(coalesce(warehouse_reorder_level, 0)) from `tabItem Reorder`
		where parent = %(item)s and parenttype = 'Item'
		""",
		{"item": item_code},
	)
	return flt(row[0][0]) if row and row[0] else 0.0


def _item_payload(item_code, location=None):
	"""Everything the item card shows, at ``location`` when there is one.

	``store_run_ok`` says whether it can be a line of a store run: a non-stock item can
	(``stock_scan_rules.store_run_item_refusal``), though its stepper stays hidden.
	"""
	item = _item(item_code)
	if not item:
		return None
	store_run_refusal = rules.store_run_item_refusal(item, nowdate())
	payload = {
		"item_code": item.name,
		"item_name": item.item_name or item.name,
		"description": _plain_description(item),
		"image": item.image or None,
		"stock_uom": item.stock_uom,
		"whole_number": _whole_number(item.stock_uom),
		"blocked": rules.item_refusal(item, nowdate()),
		"is_stock_item": cint(item.is_stock_item),
		"store_run_ok": store_run_refusal is None,
		"reorder_level": _reorder_level(item.name),
		"warehouse": None,
		"warehouse_name": None,
		"on_hand": None,
		"available": None,
		"elsewhere": [],
		"open_orders": [],
	}
	company = location.company if location else None
	if location:
		on_hand, reserved = _bin(item.name, location.name)
		payload.update(
			{
				"warehouse": location.name,
				"warehouse_name": location.warehouse_name or location.name,
				"on_hand": on_hand,
				"available": on_hand - reserved,
			}
		)
	# A non-stock item gets its open order lines too: a store run for something already on a
	# purchase order at that store is the pickup, and the page says so before it is bought twice.
	if not payload["blocked"] or payload["store_run_ok"]:
		payload["elsewhere"] = _elsewhere(item.name, company, exclude=location.name if location else None)
		if location:
			payload["open_orders"] = _open_order_lines(item.name, company)
	return payload


# ---------------------------------------------------------------------------
# Accounts, cost center, rate, job
# ---------------------------------------------------------------------------


#: The account a Stock Entry row's other side posts to. Shared with the maintenance
#: workflow's consumables issue, so every Stock Entry this app builds resolves it one way:
#: Item Default → Item Group → the Inventory Scanner Settings account → Company → the
#: company's one Stock Adjustment account. Production's Company has none of its own, which
#: is why ERPNext's default chain alone refuses the entry. See ``stock_accounts``.
_difference_account = difference_account


def _cost_center(configured, company):
	"""The settings' cost center when it belongs to this company; else ERPNext's default chain
	(the job's cost center, the item's, the company's) by leaving the row blank."""
	if configured and frappe.db.get_value("Cost Center", configured, "company") == company:
		return configured
	return None


def _receipt_rate(item_code, warehouse, company):
	"""What one unit added without a purchase order is worth.

	The last rate this item carried at this location; else the average across the
	company's stock of it; else its last purchase rate or its Valuation Rate. Never its
	selling price (``get_valuation_rate`` with ``fallbacks`` would reach for
	``standard_rate``), and never zero: a zero-cost receipt quietly makes every later
	issue of the item cost nothing.
	"""
	from erpnext.stock.stock_ledger import get_valuation_rate

	rate = get_valuation_rate(
		item_code,
		warehouse,
		"Stock Entry",
		None,
		allow_zero_rate=True,
		company=company,
		fallbacks=False,
		raise_error_if_no_rate=False,
	)
	if flt(rate) > 0:
		return flt(rate)
	row = frappe.db.sql(
		"""
		select sum(b.stock_value) / nullif(sum(b.actual_qty), 0)
		from `tabBin` b
		inner join `tabWarehouse` w on w.name = b.warehouse
		where b.item_code = %(item_code)s and w.company = %(company)s and b.actual_qty > 0
		""",
		{"item_code": item_code, "company": company},
	)
	if row and flt(row[0][0]) > 0:
		return flt(row[0][0])
	item = (
		frappe.db.get_value("Item", item_code, ["last_purchase_rate", "valuation_rate"], as_dict=True) or {}
	)
	return flt(item.get("last_purchase_rate")) or flt(item.get("valuation_rate"))


def _project(project, company, required=False):
	"""The job a take is charged to, checked; ``None`` when none was picked."""
	project = cstr(project).strip()
	if not project:
		if required:
			frappe.throw(_("Pick the job these parts are for before taking them."))
		return None
	row = frappe.db.get_value("Project", project, ["name", "status", "company", "project_name"], as_dict=True)
	if not row:
		frappe.throw(_("There is no job {0}.").format(project))
	if row.status in rules.CLOSED_JOB_STATUSES:
		frappe.throw(
			_("{0} is {1}. Pick a job that is still in progress.").format(
				row.project_name or row.name, _(row.status)
			)
		)
	if row.company and company and row.company != company:
		frappe.throw(
			_("{0} belongs to {1}, not {2}.").format(row.project_name or row.name, row.company, company)
		)
	return row.name


# ---------------------------------------------------------------------------
# Saves
# ---------------------------------------------------------------------------


def _client_ref(value):
	ref = cstr(value).strip()
	if not _CLIENT_REF.match(ref):
		frappe.throw(_("This save is missing its reference. Reload the page and try again."))
	return ref


def _already_saved(ref):
	"""The result of an earlier save with this ``client_ref``, or ``None``."""
	row = frappe.db.get_value(LOG, {"client_ref": ref}, ["name", "posted_by"], as_dict=True)
	if not row:
		return None
	if row.posted_by != frappe.session.user:
		frappe.throw(_("This save belongs to someone else. Reload the page."), frappe.PermissionError)
	return _result(row.name, repeated=True)


def _who():
	return frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user


def _begin_log(ref, action, item, qty, location, scanned_code=None, **extra):
	"""Insert the log row first, so a duplicate ``client_ref`` fails before any voucher exists.

	It is in the same transaction as the voucher: if posting the voucher fails, the request
	rolls back and the row goes with it.
	"""
	values = {
		"doctype": LOG,
		"posted_at": now_datetime(),
		"posted_by": frappe.session.user,
		"action": action,
		"status": "Posted",
		"item_code": item.name,
		"qty": qty,
		"stock_uom": item.stock_uom,
		"warehouse": location.name,
		"client_ref": ref,
		"scanned_code": cstr(scanned_code).strip()[:140] or None,
	}
	values.update({key: value for key, value in extra.items() if value not in (None, "")})
	log = frappe.get_doc(values)
	try:
		log.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another request with this client_ref got here first and is still posting (a retry
		# sent while the first attempt was mid-submit; the insert waited on its unique key).
		# That attempt is about to commit, so this is NOT a refusal the page may answer with a
		# fresh reference: raised as DuplicateEntryError (HTTP 409), which the page treats like
		# a dropped connection and retries with the SAME reference -- and the retry then gets
		# the first save back from _already_saved instead of posting a second.
		frappe.throw(
			_("This save is still being recorded. Wait a moment, then try again."),
			frappe.DuplicateEntryError,
		)
	return log


def _finish_log(log, voucher_type, voucher_no, **extra):
	values = {"voucher_type": voucher_type, "voucher_no": voucher_no}
	values.update(extra)
	log.db_set(values, update_modified=False)


def _stock_entry(stock_entry_type, company, project=None, remarks=None):
	"""A new Stock Entry. ``stock_entry_type`` is mandatory on v16 and ``purpose`` is fetched
	from it on insert, so the type is set and the purpose is left alone."""
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = stock_entry_type
	se.company = company
	if project:
		se.project = project
	if remarks:
		se.remarks = remarks
	return se


def _post(se):
	"""Insert then submit, as the session user. No ``ignore_permissions``: the framework decides."""
	se.insert()
	se.submit()
	return se


@frappe.whitelist(methods=["POST"])
def take(item_code, warehouse, qty, client_ref, project=None, scanned_code=None):
	"""Take parts from a location: a submitted Material Issue, optionally charged to a job."""
	_check_access()
	ref = _client_ref(client_ref)
	done = _already_saved(ref)
	if done:
		return done

	location = _location(warehouse)
	item = _stock_item(item_code)
	qty = _checked_qty(qty, item)
	settings = get_settings()
	project = _project(project, location.company, required=cint(settings.get("require_project_for_take")))
	problem = rules.check_take(
		qty, _available(item.name, location.name), location.warehouse_name or location.name
	)
	if problem:
		frappe.throw(problem)
	_require("Stock Entry", "create", "submit")

	log = _begin_log(ref, rules.ACTION_TAKE, item, qty, location, scanned_code, project=project)
	se = _stock_entry(
		"Material Issue",
		location.company,
		project=project,
		remarks=rules.remark(rules.ACTION_TAKE, _who(), location.name, qty, item.stock_uom, project=project),
	)
	se.append(
		"items",
		{
			"item_code": item.name,
			"qty": qty,
			"s_warehouse": location.name,
			"expense_account": _difference_account(
				location.company, item.name, settings.get("take_expense_account")
			),
			"cost_center": _cost_center(settings.get("scan_cost_center"), location.company),
		},
	)
	_post(se)
	_finish_log(log, "Stock Entry", se.name)
	return _result(log.name)


@frappe.whitelist(methods=["POST"])
def add(
	item_code,
	warehouse,
	qty,
	client_ref,
	purchase_order_item=None,
	without_po=0,
	project=None,
	scanned_code=None,
):
	"""Add stock to a location: received against an open order line, or added without one.

	``purchase_order_item`` receives against that line (a Purchase Receipt into this
	location; the job comes from the order line, not the page). ``without_po=1`` adds it as
	a Material Receipt at the item's current cost and flags the log for review. With
	neither, nothing is posted: the page must say which, because receiving stock that is on
	an order *without* the order double-counts it the day the order's own receipt arrives.
	"""
	_check_access()
	ref = _client_ref(client_ref)
	done = _already_saved(ref)
	if done:
		return done

	location = _location(warehouse)
	item = _stock_item(item_code)
	qty = _checked_qty(qty, item)
	who = _who()

	if cstr(purchase_order_item).strip():
		from erpnext_enhancements.api.procurement import receive_order_line

		line = cstr(purchase_order_item).strip()
		purchase_order = frappe.db.get_value("Purchase Order Item", line, "parent")
		log = _begin_log(
			ref,
			rules.ACTION_RECEIVE,
			item,
			qty,
			location,
			scanned_code,
			purchase_order=purchase_order,
			purchase_order_item=line,
		)
		result = receive_order_line(
			line,
			qty,
			location.name,
			item_code=item.name,
			remarks=rules.remark(
				rules.ACTION_RECEIVE, who, location.name, qty, item.stock_uom, purchase_order=purchase_order
			),
		)
		_finish_log(log, "Purchase Receipt", result.get("purchase_receipt"))
		return _result(log.name)

	if not cint(without_po):
		frappe.throw(
			_("Say where these came from: one of the open purchase orders, or not on a purchase order.")
		)

	settings = get_settings()
	project = _project(project, location.company)
	# With a job, this is parts coming BACK from that job: the credit goes to the account a
	# take charges (the Parts Taken chain), so the job's cost nets in the ledger — the take
	# debited it, this credits the same account, both tagged with the project. Without a
	# job it is found or unplanned stock and offsets to the Added Without PO account.
	# ERPNext's Project.total_consumed_material_cost counts Material Issue rows only and is
	# not reduced by a receipt; that figure is hidden on this site's Project form and read by
	# nothing here, and the ledger by project is right.
	offset_setting = settings.get("take_expense_account") if project else settings.get("add_offset_account")
	rate = _receipt_rate(item.name, location.name, location.company)
	if rate <= 0:
		frappe.throw(
			_(
				"There is no cost on record for {0}, so it cannot be added without a purchase order. Receive it on its purchase order, or ask a Stock Manager to set its Valuation Rate."
			).format(item.item_name or item.name)
		)
	_require("Stock Entry", "create", "submit")

	log = _begin_log(
		ref, rules.ACTION_ADD_WITHOUT_PO, item, qty, location, scanned_code, project=project, needs_review=1
	)
	se = _stock_entry(
		"Material Receipt",
		location.company,
		project=project,
		remarks=rules.remark(
			rules.ACTION_ADD_WITHOUT_PO, who, location.name, qty, item.stock_uom, project=project
		),
	)
	se.append(
		"items",
		{
			"item_code": item.name,
			"qty": qty,
			"t_warehouse": location.name,
			"basic_rate": rate,
			"expense_account": _difference_account(location.company, item.name, offset_setting),
			"cost_center": _cost_center(settings.get("scan_cost_center"), location.company),
		},
	)
	_post(se)
	_finish_log(log, "Stock Entry", se.name)
	return _result(log.name)


@frappe.whitelist(methods=["POST"])
def move_here(item_code, warehouse, from_warehouse, qty, client_ref, scanned_code=None):
	"""Put stock away: a submitted Material Transfer from where it is recorded into this location."""
	_check_access()
	ref = _client_ref(client_ref)
	done = _already_saved(ref)
	if done:
		return done

	location = _location(warehouse)
	item = _stock_item(item_code)
	qty = _checked_qty(qty, item)
	source = _warehouse(from_warehouse)
	if not source or source.is_group or source.disabled:
		frappe.throw(_("There is no location {0} to move stock from.").format(cstr(from_warehouse)))
	if source.name == location.name:
		frappe.throw(_("That is this location. Pick where the stock is recorded now."))
	if source.company != location.company:
		frappe.throw(_("{0} belongs to {1}, not {2}.").format(source.name, source.company, location.company))
	available = _available(item.name, source.name)
	if qty > available + rules.TOLERANCE:
		frappe.throw(
			_("Only {0} {1} of {2} is recorded at {3}.").format(
				rules.plain(available),
				item.stock_uom,
				item.item_name or item.name,
				source.warehouse_name or source.name,
			)
		)
	_require("Stock Entry", "create", "submit")
	settings = get_settings()

	log = _begin_log(ref, rules.ACTION_MOVE, item, qty, location, scanned_code, from_warehouse=source.name)
	se = _stock_entry(
		"Material Transfer",
		location.company,
		remarks=rules.remark(
			rules.ACTION_MOVE, _who(), location.name, qty, item.stock_uom, from_warehouse=source.name
		),
	)
	se.from_warehouse = source.name
	se.to_warehouse = location.name
	se.append(
		"items",
		{
			"item_code": item.name,
			"qty": qty,
			"s_warehouse": source.name,
			"t_warehouse": location.name,
			# Mandatory even on a transfer (validate_difference_account); both sides of the GL
			# net to nothing when the two warehouses share an inventory account, as they do here.
			"expense_account": _difference_account(
				location.company, item.name, settings.get("take_expense_account")
			),
			"cost_center": _cost_center(settings.get("scan_cost_center"), location.company),
		},
	)
	_post(se)
	_finish_log(log, "Stock Entry", se.name)
	return _result(log.name)


def _checked_qty(value, item):
	qty, problem = rules.check_qty(value, whole_number=_whole_number(item.stock_uom), uom=item.stock_uom)
	if problem:
		frappe.throw(problem)
	return qty


# ---------------------------------------------------------------------------
# Store runs: "Bought on a store run" (v1.535.0, POL-0602 §4.7-4.8)
# ---------------------------------------------------------------------------


def _store_run_columns():
	"""Whether the fields a store run writes exist yet (a fresh bench before its patch, a
	deploy mid-migrate). ``has_column`` takes a doctype and all three doctypes are certain."""
	return bool(
		frappe.db.has_column("Supplier", SUPPLIER_FLAG)
		and all(frappe.db.has_column("Purchase Receipt", f) for f in (RUN_FIELD, PHOTO_FIELD, TOTAL_FIELD))
		and frappe.db.has_column(LOG, "store_run")
	)


def _po_required():
	return cstr(frappe.db.get_single_value("Buying Settings", "po_required")) == "Yes"


def _store_run_ready():
	if not _store_run_columns():
		frappe.throw(_("Store runs are being set up. Try again after the update."))
	if _po_required():
		frappe.throw(
			_(
				"Every receipt needs a purchase order (Buying Settings), so store runs can't be recorded here. Ask Purchasing."
			)
		)


def _run_ref(value):
	run = cstr(value).strip()
	if not (_CLIENT_REF.match(run) and rules.is_run_ref(run)):
		frappe.throw(_("This store run is missing its reference. Reload the page and try again."))
	return run


def _store_supplier(supplier):
	"""The Supplier row for a store, or throw: ticked *Store-Run Vendor*, not disabled, not on hold."""
	name = cstr(supplier).strip()
	row = (
		frappe.db.get_value(
			"Supplier", name, ["name", "supplier_name", "disabled", "on_hold", SUPPLIER_FLAG], as_dict=True
		)
		if name
		else None
	)
	if not row or not cint(row.get(SUPPLIER_FLAG)) or cint(row.disabled) or cint(row.on_hold):
		frappe.throw(
			_("{0} is not a store on the store-run list. Ask Purchasing to add it.").format(name or _("That"))
		)
	return row


def _full_name(user):
	return frappe.db.get_value("User", user, "full_name") or user


def _run_head(run):
	"""The first line recorded on a run (Undone lines count: the run still existed)."""
	rows = frappe.get_all(
		LOG,
		filters={"store_run": run},
		fields=[
			"name",
			"posted_by",
			"posted_at",
			"supplier",
			"bought_on",
			"receipt_number",
			"receipt_total",
			"receipt_photo",
		],
		order_by="posted_at asc",
		limit=1,
	)
	return rows[0] if rows else None


def _same_run(run, supplier, bought_on):
	"""The run's first line when ``run`` exists, checked against this line; ``None`` for a new run.

	Anyone may add to a run on the day it was started -- two people who shopped together, or a
	run continued after lunch, is still one trip (the KPI would otherwise count it twice) --
	and the person who started it may finish it the next morning. The store and the day
	bought must be the run's.
	"""
	head = _run_head(run)
	if not head:
		return None
	if head.supplier != supplier:
		frappe.throw(
			_("This run is for {0}. Finish it and start a new run for {1}.").format(head.supplier, supplier)
		)
	if head.bought_on and getdate(head.bought_on) != getdate(bought_on):
		frappe.throw(
			_("This run was bought on {0}. Start a new run for what was bought on {1}.").format(
				formatdate(head.bought_on), formatdate(bought_on)
			)
		)
	if head.posted_by != frappe.session.user and not rules.run_is_open(head.posted_at, getdate()):
		frappe.throw(_("This store run was started on another day. Start a new one."))
	return head


def _receipt_photo(value, run):
	"""The receipt photo's URL, checked: a private File this person uploaded and nothing has
	claimed yet, or the photo already on a receipt of this same run (a second line, or a line
	added by the other person on the trip). Anything else -- another person's upload, a file
	attached to some other document, a public file -- is refused, so the page cannot be used
	to pull a private file onto a receipt. Bound parameters only."""
	url = cstr(value).strip()
	# 140: the log's Receipt Photo is a Data field. The page names a shrunk photo receipt.jpg.
	if url.startswith("/private/files/") and len(url) <= 140:
		rows = frappe.db.sql(
			"""
			select f.name from `tabFile` f
			where f.file_url = %(url)s and f.is_private = 1
				and (
					(
						f.owner = %(user)s
						and coalesce(f.attached_to_doctype, '') = ''
						and coalesce(f.attached_to_name, '') = ''
					)
					or (
						f.attached_to_doctype = 'Purchase Receipt'
						and exists (
							select 1 from `tabPurchase Receipt` pr
							where pr.name = f.attached_to_name and pr.custom_store_run = %(run)s
						)
					)
				)
			limit 1
			""",
			{"url": url, "user": frappe.session.user, "run": run},
		)
		if rows:
			return url
	frappe.throw(_("Take the receipt photo again."))


def _store_run_item(item_code):
	"""The Item row for a store-run line, or throw its refusal. A non-stock item is allowed."""
	item = _item(item_code)
	problem = (
		rules.store_run_item_refusal(item, nowdate())
		if item
		else _("There is no item {0}.").format(item_code)
	)
	if problem:
		frappe.throw(problem)
	return item


def _leaf_groups():
	return set(frappe.get_all("Item Group", filters={"is_group": 0}, pluck="name"))


def _item_groups_by_use():
	"""Leaf Item Groups, the most used first: the quick-item picker's order."""
	rows = frappe.db.sql(
		"""
		select g.name, count(i.name) as used
		from `tabItem Group` g
		left join `tabItem` i on i.item_group = g.name
		where g.is_group = 0
		group by g.name
		order by used desc, g.name asc
		""",
		as_dict=True,
	)
	return [row.name for row in rows]


def _quick_uoms():
	"""The quick-item units that are enabled, Stock Settings' default unit first."""
	enabled = set(
		frappe.get_all(
			"UOM", filters={"name": ["in", list(rules.QUICK_ITEM_UOMS)], "enabled": 1}, pluck="name"
		)
	)
	uoms = [uom for uom in rules.QUICK_ITEM_UOMS if uom in enabled]
	default = frappe.db.get_single_value("Stock Settings", "stock_uom")
	if default in uoms:
		uoms.remove(default)
		uoms.insert(0, default)
	return uoms


def _quick_item_spec(value):
	data = frappe.parse_json(value) if isinstance(value, str) else value
	if not isinstance(data, dict):
		frappe.throw(_("The new item's details are missing. Fill them in again."))
	return {key: cstr(data.get(key)).strip() for key in ("item_code", "item_name", "item_group", "stock_uom")}


def _check_quick_item(spec):
	"""Refuse a quick Item before anything is written, in the technician's words."""
	problem = rules.quick_item_problem(spec, _leaf_groups(), _quick_uoms())
	if problem:
		frappe.throw(problem)
	if frappe.db.exists("Item", spec["item_code"]):
		frappe.throw(_("{0} is already in ERPNext. Pick it instead.").format(spec["item_code"]))
	if not frappe.has_permission("Item", "create"):
		frappe.throw(
			_(
				"Creating an item needs the Item Manager role. Ask Purchasing to add it, then record the purchase."
			),
			frappe.PermissionError,
		)


def _quick_item(spec, location):
	"""Create the quick Item, as the session user, inside the save's transaction.

	No ``ignore_permissions`` and no ``flags.ignore_naming_guard``: the person at the screen
	chose the code and the name, so ``item_naming_guard`` judges them exactly as it would in
	the Desk (from 2026-10-01 it refuses a duplicate code or a name that is just the code). If
	the receipt then fails, the request rolls back and the Item goes with it.

	``is_stock_item`` is set because the site's Property Setter defaults it to 0. The Item
	Default is the bin it was bought into, for this company: without one, ERPNext copies the
	Item Group's defaults or the global default warehouse (``Stores - SF``), and the item would
	list at Stores with nothing there. No leaf group carries defaults on production.
	"""
	item = frappe.new_doc("Item")
	item.item_code = spec["item_code"]
	item.item_name = spec["item_name"]
	item.item_group = spec["item_group"]
	item.stock_uom = spec["stock_uom"]
	item.is_stock_item = 1
	item.is_purchase_item = 1
	item.append("item_defaults", {"company": location.company, "default_warehouse": location.name})
	item.insert()
	return item


def _prior_unstocked_runs(item_code, run):
	"""Earlier RUNS (not lines) in the repeat window that bought this item as "Not something we
	stock". The current run is excluded, so one part split over two bins is not a repeat."""
	row = frappe.db.sql(
		"""
		select count(distinct store_run) from `tabStock Scan Log`
		where item_code = %(item)s and store_run_reason = %(reason)s and status = 'Posted'
			and posted_at >= %(since)s and coalesce(store_run, '') not in ('', %(run)s)
		""",
		{
			"item": item_code,
			"reason": rules.REASON_NOT_STOCKED,
			"since": add_days(now_datetime(), -rules.REPEAT_WINDOW_DAYS),
			"run": run,
		},
	)
	return cint(row[0][0]) if row and row[0] else 0


def _store_run_receipt(
	item,
	qty,
	rate,
	location,
	supplier,
	bought_on,
	run,
	photo,
	receipt_number,
	receipt_total,
	reason,
	project,
	who,
	non_stock,
	settings,
):
	"""The one Purchase Receipt builder in this module: one line of one store run.

	* **No purchase order** (Buying Settings ``po_required`` is "No"; checked first).
	* **The price on the paper receipt, before tax**: ``rate`` and ``price_list_rate`` both, with
	  ``ignore_pricing_rule`` -- ``rate`` is not one of v16's ``force_item_fields`` and
	  ``set_missing_item_details`` fills only blanks, so it survives a Standard Buying price.
	  ``uom`` and ``conversion_factor`` are the stock unit's, because an Item with a purchase
	  UOM would otherwise read the page's count as boxes.
	* **No tax.** The site's default purchase template (``US ST 6% - SF``) is the setup
	  wizard's placeholder: 6% is not Utah's rate and its account has never been posted to.
	  QuickBooks books the tax on these purchases into the goods' own expense account. The
	  receipt's real total, tax included, rides on ``custom_receipt_total`` instead.
	* **No project**, on the header or the row. The job is on the log and in the remarks; a
	  row project would be copied into the Purchase Invoice made from this receipt and count
	  in the Project's purchase cost, while the Take that issues the parts to the job counts
	  them again as consumed material.
	* **Yesterday** is posted at 23:59 of yesterday; today leaves ERPNext's own time.
	"""
	pr = frappe.new_doc("Purchase Receipt")
	pr.supplier = supplier
	pr.company = location.company
	pr.posting_date = bought_on
	if getdate(bought_on) < getdate():
		pr.set_posting_time = 1
		pr.posting_time = "23:59:00"
	pr.set_warehouse = location.name
	pr.supplier_delivery_note = receipt_number or None
	pr.ignore_pricing_rule = 1
	pr.remarks = rules.remark(
		rules.ACTION_STORE_RUN,
		who,
		location.name,
		qty,
		item.stock_uom,
		project=project,
		supplier=supplier,
		reason=reason,
		run=run,
		non_stock=non_stock,
	)
	pr.set(RUN_FIELD, run)
	pr.set(PHOTO_FIELD, photo)
	pr.set(TOTAL_FIELD, receipt_total)
	pr.append(
		"items",
		{
			"item_code": item.name,
			"qty": qty,
			"uom": item.stock_uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": 1,
			"rate": rate,
			"price_list_rate": rate,
			"warehouse": location.name,
			"cost_center": _cost_center(settings.get("scan_cost_center"), location.company),
		},
	)
	return pr


@frappe.whitelist(methods=["POST"])
def store_run(
	run,
	supplier,
	warehouse,
	qty,
	rate,
	reason,
	receipt_photo,
	client_ref,
	item_code=None,
	new_item=None,
	project=None,
	no_job=0,
	bought="today",
	receipt_number=None,
	receipt_total=None,
	scanned_code=None,
):
	"""Record one line of a store run: a submitted Purchase Receipt with no PO, flagged for review.

	One save is one line (POL-0602 §4.7: the store, the item, quantity, price, job and reason,
	with a photo of the receipt). ``run`` ties the lines of one trip together; the first line
	of a run also carries its header -- the day bought, the receipt number and the receipt's
	total with tax -- and every later line takes the header from the run, not from the page.

	Exactly one of ``item_code`` (an existing Item; a non-stock one is allowed) and
	``new_item`` (``{item_code, item_name, item_group, stock_uom}``, created here as a quick
	Item, POL-0602 §4.8). A job is required unless ``no_job`` says there was none (safety or
	shop), and always for "Only for this job".

	Every refusal comes before the log row. The quick Item is created inside the same
	transaction, as the user, so a refused receipt takes the Item with it.
	"""
	_check_access()
	ref = _client_ref(client_ref)
	done = _already_saved(ref)
	if done:
		return done

	_store_run_ready()
	run = _run_ref(run)
	store = _store_supplier(supplier)
	bought_on = rules.purchase_date(bought, getdate())
	if not bought_on:
		frappe.throw(_("Choose Today or Yesterday. Older store runs are recorded in the Desk by Purchasing."))
	location = _location(warehouse)
	head = _same_run(run, store.name, bought_on)
	reason = cstr(reason).strip()
	if reason not in rules.STORE_RUN_REASONS:
		frappe.throw(_("Pick why these were bought: {0}.").format(", ".join(rules.STORE_RUN_REASONS)))
	rate, problem = rules.check_price(rate)
	if problem:
		frappe.throw(problem)
	if head:
		receipt_total = flt(head.receipt_total)
		receipt_number = head.receipt_number or None
	else:
		receipt_total, problem = rules.check_receipt_total(receipt_total)
		if problem:
			frappe.throw(problem)
		receipt_number = cstr(receipt_number).strip()[:140] or None
	no_job = cint(no_job)
	if cstr(project).strip():
		no_job = 0
	elif reason == rules.REASON_ONLY_THIS_JOB:
		frappe.throw(_("Pick the job these were bought for."))
	elif not no_job:
		frappe.throw(_("Pick the job these were bought for, or choose No job (safety or shop)."))
	project = _project(project, location.company)
	photo = _receipt_photo(receipt_photo, run)

	spec = None
	if cstr(item_code).strip() and new_item:
		frappe.throw(_("Pick an existing item or describe a new one, not both."))
	if new_item:
		spec = _quick_item_spec(new_item)
		_check_quick_item(spec)
		qty = _checked_qty(qty, frappe._dict(stock_uom=spec["stock_uom"]))
	else:
		item = _store_run_item(item_code)
		qty = _checked_qty(qty, item)
	_require("Purchase Receipt", "create", "submit")

	settings = get_settings()
	if spec:
		item = _store_run_item(_quick_item(spec, location).name)
	non_stock = not cint(item.is_stock_item)
	repeat = rules.repeat_unstocked(reason, _prior_unstocked_runs(item.name, run))
	log = _begin_log(
		ref,
		rules.ACTION_STORE_RUN,
		item,
		qty,
		location,
		scanned_code,
		project=project,
		needs_review=1,
		supplier=store.name,
		rate=rate,
		receipt_total=receipt_total,
		receipt_number=receipt_number,
		receipt_photo=photo,
		bought_on=bought_on,
		recorded_late=cint(rules.recorded_late(bought_on, getdate())),
		store_run=run,
		store_run_reason=reason,
		no_job=no_job,
		reorder_level_seen=_reorder_level(item.name),
		non_stock_item=cint(non_stock),
		created_item=cint(bool(spec)),
		repeat_unstocked=cint(repeat),
	)
	pr = _store_run_receipt(
		item,
		qty,
		rate,
		location,
		store.name,
		bought_on,
		run,
		photo,
		receipt_number,
		receipt_total,
		reason,
		project,
		_who(),
		non_stock,
		settings,
	)
	_post(pr)
	_finish_log(log, "Purchase Receipt", pr.name)
	return _result(log.name)


@frappe.whitelist(methods=["POST"])
def check_new_item(item_code, item_name=None, item_group=None, stock_uom=None):
	"""Preview a quick Item before it is created: is it already here, what is it like, and will
	the naming guard refuse it. Read-only.

	* ``exists`` -- an Item with exactly this code: use it instead (the page disables Save).
	* ``similar`` -- up to five neighbours by name (``item_naming.inspect_item_naming``, the
	  advisor's own call), QuickBooks tombstones dropped: 135 of them sit in the corpus, all
	  non-stock, and they filled the list. A non-stock neighbour can still be recorded against
	  (``usable`` with ``stocked`` 0); anything else unusable carries its refusal.
	* ``blocking`` -- ``item_naming_rules.blocking_findings`` over every Item code: the naming
	  guard's exact call, so the preview and the refusal on save cannot disagree. Plain text.
	* ``will_refuse`` -- the guard is in force (from ``NAMING_GO_LIVE``) and something blocks.
	* ``advice`` -- the other findings, which never stop a save.
	* ``checked`` -- False when the corpus was too large to read; the guard still runs on save.
	"""
	_check_access()
	from erpnext_enhancements.inventory_enhancements import item_naming, item_naming_guard
	from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming

	code = cstr(item_code).strip()
	name = cstr(item_name).strip()
	out = {
		"checked": False,
		"exists": None,
		"similar": [],
		"blocking": [],
		"advice": [],
		"will_refuse": False,
		"refuse_from": naming.NAMING_GO_LIVE,
		"suggested_group": None,
	}
	if not code and not name:
		out["checked"] = True
		return out

	if code:
		existing = _item(code)
		if existing:
			refusal = rules.store_run_item_refusal(existing, nowdate())
			out["exists"] = {
				"item_code": existing.name,
				"item_name": existing.item_name or existing.name,
				"stocked": cint(existing.is_stock_item),
				"usable": refusal is None,
				"refusal": refusal,
			}
		blocking = naming.blocking_findings(code, name, frappe.get_all("Item", pluck="name"))
		out["blocking"] = [{"code": f["code"], "message": f["message"]} for f in blocking]
		out["will_refuse"] = bool(item_naming_guard.in_force() and blocking)

	result = item_naming.inspect_item_naming(
		item_code=code or None,
		item_name=name or None,
		item_group=cstr(item_group).strip() or None,
		stock_uom=cstr(stock_uom).strip() or None,
		similar_limit=SIMILAR_READ,
	)
	if not result.get("success"):
		return out

	skip = {naming.DUPLICATE_CODE_EXACT} | set(naming.BLOCKING_CODES)
	out["advice"] = [
		{"code": f.get("code"), "severity": f.get("severity"), "message": f.get("message")}
		for f in result.get("findings") or ()
		if f.get("code") not in skip
	]
	candidates = [
		row
		for row in result.get("similar") or ()
		if naming.DELETED_MARKER not in cstr(row.get("item_code")).lower() and row.get("item_code") != code
	]
	details = {}
	codes = [row.get("item_code") for row in candidates]
	if codes:
		details = {
			row.name: row
			for row in frappe.get_all("Item", filters={"name": ["in", codes]}, fields=_ITEM_FIELDS)
		}
	for row in candidates:
		detail = details.get(row.get("item_code"))
		if not detail:
			continue
		refusal = rules.store_run_item_refusal(detail, nowdate())
		out["similar"].append(
			{
				"item_code": detail.name,
				"item_name": detail.item_name or detail.name,
				"item_group": row.get("item_group"),
				"score": row.get("score"),
				"stocked": cint(detail.is_stock_item),
				"usable": refusal is None,
				"refusal": refusal,
			}
		)
		if len(out["similar"]) >= SIMILAR_SHOW:
			break
	leaves = _leaf_groups()
	for row in out["similar"]:
		if row.get("item_group") in leaves:
			out["suggested_group"] = row["item_group"]
			break
	out["checked"] = True
	return out


# ---------------------------------------------------------------------------
# Undo and history
# ---------------------------------------------------------------------------


def _age_minutes(posted_at):
	if not posted_at:
		return None
	return (now_datetime() - get_datetime(posted_at)).total_seconds() / 60.0


def _undo_refusal(doc, settings=None):
	settings = settings or get_settings()
	store_run = doc.action == rules.ACTION_STORE_RUN
	return rules.undo_refusal(
		doc.status,
		doc.posted_by == frappe.session.user,
		_is_supervisor(),
		_age_minutes(doc.posted_at),
		settings.get("undo_window_minutes"),
		store_run=store_run,
		is_purchasing=store_run and _is_purchasing(),
		reviewed=bool(cint(doc.get("reviewed"))),
	)


@frappe.whitelist(methods=["POST"])
def undo(log):
	"""Reverse a save by cancelling its voucher, as the session user.

	The person who saved it may undo it within the Undo Window; a Stock Manager may undo any
	save. ERPNext still decides whether the voucher *can* be cancelled — undoing a receipt
	whose stock has since been taken would drive the location negative, and it says so.

	A store-run line is one receipt, so Undo cancels that line's receipt and leaves the rest
	of the run alone; a quick Item created with it stays, for Purchasing's weekly review. Who
	may undo one is narrower (``stock_scan_rules.undo_refusal``): never once reviewed, and
	after the window only Purchasing or Accounts.
	"""
	_check_access()
	doc = frappe.get_doc(LOG, cstr(log).strip())
	refusal = _undo_refusal(doc)
	if refusal:
		frappe.throw(refusal)

	already_canceled = False
	if doc.voucher_type and doc.voucher_no and frappe.db.exists(doc.voucher_type, doc.voucher_no):
		voucher = frappe.get_doc(doc.voucher_type, doc.voucher_no)
		if voucher.docstatus == 1:
			voucher.cancel()
		elif voucher.docstatus == 2:
			# Canceled in the Desk already. If it was then amended, the amendment still moves
			# stock, and marking this save undone would say the stock is back when it is not.
			replacement = _submitted_amendment(doc.voucher_type, doc.voucher_no)
			if replacement:
				frappe.throw(
					_(
						"{0} was canceled and replaced by {1} in the Desk, so there is nothing here to undo. Reverse {1} there if it is wrong."
					).format(doc.voucher_no, replacement)
				)
			already_canceled = True

	doc.flags.stock_scan_undo = True
	doc.status = "Undone"
	doc.undone_by = frappe.session.user
	doc.undone_at = now_datetime()
	doc.save(ignore_permissions=True)
	return _result(doc.name, undone=True, already_canceled=already_canceled)


def _submitted_amendment(doctype, name):
	"""The submitted document that (eventually) amends ``name``, or ``None``.

	Follows the ``amended_from`` chain — an amendment can itself be canceled and amended.
	"""
	seen = {name}
	current = name
	while True:
		row = frappe.db.get_value(doctype, {"amended_from": current}, ["name", "docstatus"], as_dict=True)
		if not row or row.name in seen:
			return None
		if row.docstatus == 1:
			return row.name
		seen.add(row.name)
		current = row.name


def _log_row(doc, names=None, settings=None):
	names = names or {}
	refusal = _undo_refusal(doc, settings)
	return {
		"name": doc.name,
		"action": doc.action,
		"status": doc.status,
		"item_code": doc.item_code,
		"item_name": doc.item_name or doc.item_code,
		"qty": flt(doc.qty),
		"stock_uom": doc.stock_uom,
		"warehouse": doc.warehouse,
		"warehouse_name": names.get(doc.warehouse) or doc.warehouse,
		"from_warehouse": doc.from_warehouse,
		"from_warehouse_name": names.get(doc.from_warehouse) or doc.from_warehouse,
		"project": doc.project,
		"purchase_order": doc.purchase_order,
		"voucher_type": doc.voucher_type,
		"voucher_no": doc.voucher_no,
		"posted_at": str(doc.posted_at) if doc.posted_at else None,
		"needs_review": cint(doc.needs_review),
		"reviewed": cint(doc.get("reviewed")),
		"can_undo": refusal is None,
		"undo_refusal": refusal,
		# Store runs (v1.535.0); None / 0 on every other action.
		"supplier": doc.get("supplier"),
		"rate": flt(doc.get("rate")) or None,
		"receipt_total": flt(doc.get("receipt_total")) or None,
		"store_run": doc.get("store_run"),
		"store_run_reason": doc.get("store_run_reason"),
		"bought_on": str(doc.get("bought_on")) if doc.get("bought_on") else None,
		"recorded_late": cint(doc.get("recorded_late")),
		"no_job": cint(doc.get("no_job")),
		"non_stock_item": cint(doc.get("non_stock_item")),
		"created_item": cint(doc.get("created_item")),
		"repeat_unstocked": cint(doc.get("repeat_unstocked")),
	}


def _store_run_message(doc, amount, item, undone=False):
	store = doc.get("supplier") or _("the store")
	if undone:
		text = _("Undone: the store-run receipt for {0} of {1} is canceled.").format(amount, item)
		if cint(doc.get("created_item")):
			text += " " + _("The new item {0} stays for Purchasing to review.").format(doc.item_code)
		return text
	text = _("Recorded {0} of {1} from {2} at {3} each.").format(
		amount, item, store, rules.money(doc.get("rate"))
	)
	if cint(doc.get("non_stock_item")):
		text += " " + _("It is not a stock item, so no stock was added.")
	text += " " + _("Purchasing will review it.")
	if cint(doc.get("created_item")):
		text += " " + _("New item {0} created.").format(doc.item_code)
	if cint(doc.get("repeat_unstocked")):
		text += " " + _(
			"Second time in {0} days as '{1}': Purchasing will look at adding it to the kit."
		).format(rules.REPEAT_WINDOW_DAYS, rules.REASON_NOT_STOCKED)
	return text


def _message(doc, repeated=False, undone=False, already_canceled=False):
	amount = f"{rules.plain(doc.qty)} {doc.stock_uom or ''}".strip()
	item = doc.item_name or doc.item_code
	if already_canceled:
		return _("{0} had already been canceled in the Desk, so nothing moved now. Marked undone.").format(
			doc.voucher_no
		)
	if doc.action == rules.ACTION_STORE_RUN:
		text = _store_run_message(doc, amount, item, undone=undone)
		return _("Already saved. {0}").format(text) if repeated else text
	if undone:
		return _("Undone: {0} of {1} is back as it was.").format(amount, item)
	if doc.action == rules.ACTION_TAKE:
		text = _("Took {0} of {1}.").format(amount, item)
	elif doc.action == rules.ACTION_RECEIVE:
		text = _("Received {0} of {1} on {2}.").format(amount, item, doc.purchase_order or _("its order"))
	elif doc.action == rules.ACTION_ADD_WITHOUT_PO and doc.project:
		text = _("Returned {0} of {1} from {2}. A Stock Manager will review it.").format(
			amount, item, doc.project
		)
	elif doc.action == rules.ACTION_ADD_WITHOUT_PO:
		text = _("Added {0} of {1}. A Stock Manager will review it.").format(amount, item)
	else:
		text = _("Moved {0} of {1} here.").format(amount, item)
	if repeated:
		text = _("Already saved. {0}").format(text)
	return text


def _result(log_name, repeated=False, undone=False, already_canceled=False):
	doc = frappe.get_doc(LOG, log_name)
	location = _warehouse(doc.warehouse)
	names = _warehouse_names([doc.warehouse, doc.from_warehouse])
	store_run = doc.get("store_run") if doc.action == rules.ACTION_STORE_RUN else None
	return {
		"log": _log_row(doc, names),
		"item": _item_payload(doc.item_code, location) if location else None,
		"message": _message(doc, repeated=repeated, undone=undone, already_canceled=already_canceled),
		"repeated": bool(repeated),
		"run": _run_summary(store_run) if store_run else None,
	}


def _run_summary(run):
	"""A store run as the page shows it: its header, how many lines and what they cost before
	tax (Posted lines only), who started it, and whether it still takes lines. Aggregates are
	``frappe.db.sql``: Frappe 16 refuses ``sum(...)`` as a string field in ``get_all``."""
	head = _run_head(run)
	if not head:
		return None
	row = frappe.db.sql(
		"""
		select count(*), coalesce(sum(qty * rate), 0), max(posted_at)
		from `tabStock Scan Log`
		where store_run = %(run)s and status = 'Posted'
		""",
		{"run": run},
	)
	lines, amount, last_at = row[0] if row else (0, 0, None)
	latest = frappe.db.sql(
		"""
		select receipt_photo from `tabStock Scan Log`
		where store_run = %(run)s and coalesce(receipt_photo, '') <> ''
		order by posted_at desc limit 1
		""",
		{"run": run},
	)
	return {
		"run": run,
		"supplier": head.supplier,
		"supplier_name": frappe.db.get_value("Supplier", head.supplier, "supplier_name") or head.supplier,
		"bought": str(head.bought_on) if head.bought_on else None,
		"receipt_photo": (latest[0][0] if latest else None) or head.receipt_photo,
		"receipt_number": head.receipt_number or None,
		"receipt_total": flt(head.receipt_total),
		"lines": cint(lines),
		"amount": flt(amount),
		"started_by": head.posted_by,
		"started_by_name": _full_name(head.posted_by),
		"started_on": str(getdate(head.posted_at)) if head.posted_at else None,
		"last_at": str(last_at) if last_at else None,
		"open": rules.run_is_open(head.posted_at, getdate()),
	}


def _open_runs():
	"""Store runs started today, by anyone, the caller's own first, then the newest: the page
	offers "Add to ..." for each. Found through today's lines, then kept only if the run's
	FIRST line is today (a run begun yesterday is not open)."""
	rows = frappe.db.sql(
		"""
		select l.store_run, max(l.posted_at) as last_at
		from `tabStock Scan Log` l
		where l.store_run in (
			select t.store_run from `tabStock Scan Log` t
			where t.action = %(action)s and t.posted_at >= %(today)s and coalesce(t.store_run, '') <> ''
		)
		group by l.store_run
		having min(l.posted_at) >= %(today)s
		order by max(l.posted_at) desc
		limit 10
		""",
		{"action": rules.ACTION_STORE_RUN, "today": getdate()},
		as_dict=True,
	)
	runs = [summary for summary in (_run_summary(row.store_run) for row in rows) if summary]
	user = frappe.session.user
	runs.sort(key=lambda r: (r["started_by"] != user, -_sort_stamp(r.get("last_at"))))
	return runs[: rules.MAX_OPEN_RUNS]


def _sort_stamp(value):
	try:
		return get_datetime(value).timestamp() if value else 0.0
	except Exception:
		return 0.0


def _recent():
	settings = get_settings()
	rows = frappe.get_all(
		LOG,
		filters={"posted_by": frappe.session.user},
		fields=["name"],
		order_by="posted_at desc",
		limit=RECENT_LIMIT,
	)
	docs = [frappe.get_doc(LOG, row.name) for row in rows]
	names = _warehouse_names([d.warehouse for d in docs] + [d.from_warehouse for d in docs])
	return [_log_row(d, names, settings) for d in docs]


@frappe.whitelist(methods=["POST"])
def get_recent():
	"""The caller's latest saves, newest first, each saying whether it can still be undone."""
	_check_access()
	return _recent()


# ---------------------------------------------------------------------------
# Reads: resolve a scan, open a location or an item, search
# ---------------------------------------------------------------------------


def _storage_location_warehouse(code):
	row = frappe.db.get_value("Storage Location", {"barcode": code, "disabled": 0}, "warehouse")
	if not row and frappe.db.exists("Storage Location", code):
		row = frappe.db.get_value("Storage Location", code, "warehouse")
	return row


def _resolve(code, context=None):
	"""Classify a scanned or typed code. Never throws; an unknown code is a sentence."""
	kind, value = rules.parse_scan(code)
	if not value:
		return {"kind": "unknown", "message": _("Nothing was scanned.")}
	context_row = _warehouse(context) if context else None
	if context_row and _location_problem(context_row):
		context_row = None

	if kind in ("warehouse", "storage_location"):
		name = value if kind == "warehouse" else _storage_location_warehouse(value)
		row = _warehouse(name)
		problem = _location_problem(row, value)
		if problem:
			return {"kind": "unknown", "message": problem}
		return {"kind": "location", "location": _location_payload(row)}

	if kind == "item":
		payload = _item_payload(value, context_row)
		if not payload:
			return {"kind": "unknown", "message": _("There is no item {0}.").format(value)}
		return {"kind": "item", "item": payload}

	# Bare text: a warehouse name typed or printed by hand, a Storage Location, an item
	# barcode, an item code — in that order, the same order the count scanner uses for the
	# last three. A Warehouse name and an Item code share no namespace on this site.
	row = _warehouse(value)
	if row and not _location_problem(row):
		return {"kind": "location", "location": _location_payload(row)}
	storage = _storage_location_warehouse(value)
	if storage:
		row = _warehouse(storage)
		if row and not _location_problem(row):
			return {"kind": "location", "location": _location_payload(row)}
	item_code = frappe.db.get_value("Item Barcode", {"barcode": value}, "parent")
	if not item_code and frappe.db.exists("Item", value):
		item_code = value
	if item_code:
		return {"kind": "item", "item": _item_payload(item_code, context_row)}
	if row:
		return {"kind": "unknown", "message": _location_problem(row, value)}
	return {"kind": "unknown", "message": _("Nothing matches {0}. Try searching instead.").format(value[:60])}


@frappe.whitelist(methods=["POST"])
def resolve(code, warehouse=None):
	"""What a scan or a typed code is: a location, an item (at ``warehouse`` when given), or unknown."""
	_check_access()
	return _resolve(code, warehouse)


@frappe.whitelist(methods=["POST"])
def get_location(warehouse):
	"""A location and the items kept there."""
	_check_access()
	return _location_payload(_location(warehouse))


@frappe.whitelist(methods=["POST"])
def get_item(item_code, warehouse=None):
	"""An item's card, at ``warehouse`` when given: on hand, where else it is, open orders."""
	_check_access()
	location = _location(warehouse) if cstr(warehouse).strip() else None
	payload = _item_payload(item_code, location)
	if not payload:
		frappe.throw(_("There is no item {0}.").format(cstr(item_code)))
	return payload


def _search_words(query):
	return [word for word in re.split(r"\s+", cstr(query).strip()) if word][:5]


@frappe.whitelist(methods=["POST"])
def search_items(query, warehouse=None):
	"""Items whose code or name contains every word typed, or whose barcode is exactly it.

	Stock items first (they are the ones the page can move), then by name. Each carries its
	quantity at ``warehouse`` when given, and its total on hand across the company.
	"""
	_check_access()
	words = _search_words(query)
	if not words:
		return []
	exact = cstr(query).strip()
	params = {f"w{n}": f"%{word}%" for n, word in enumerate(words)}
	params["exact"] = exact
	conditions = " and ".join(
		f"(i.item_code like %(w{n})s or i.item_name like %(w{n})s)" for n in range(len(words))
	)
	rows = frappe.db.sql(
		f"""
		select i.name as item_code, i.item_name, i.image, i.stock_uom, i.is_stock_item,
			(i.name = %(exact)s) as exact_code
		from `tabItem` i
		where i.disabled = 0
			and (
				({conditions})
				or i.name = %(exact)s
				or i.name in (select b.parent from `tabItem Barcode` b where b.barcode = %(exact)s)
			)
		order by exact_code desc, i.is_stock_item desc, i.item_name asc
		limit {SEARCH_LIMIT}
		""",
		params,
		as_dict=True,
	)
	if not rows:
		return []

	codes = [row.item_code for row in rows]
	location = _warehouse(warehouse) if cstr(warehouse).strip() else None
	totals, here = {}, {}
	for bin_row in frappe.get_all(
		"Bin", filters={"item_code": ["in", codes]}, fields=["item_code", "warehouse", "actual_qty"]
	):
		totals[bin_row.item_code] = totals.get(bin_row.item_code, 0.0) + flt(bin_row.actual_qty)
		if location and bin_row.warehouse == location.name:
			here[bin_row.item_code] = flt(bin_row.actual_qty)
	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name or row.item_code,
			"image": row.image or None,
			"stock_uom": row.stock_uom,
			"is_stock_item": cint(row.is_stock_item),
			"on_hand_total": totals.get(row.item_code, 0.0),
			"on_hand_here": here.get(row.item_code, 0.0) if location else None,
		}
		for row in rows
	]


@frappe.whitelist(methods=["POST"])
def search_locations(query):
	"""Locations whose name contains every word typed, for putting an item somewhere by hand."""
	_check_access()
	words = _search_words(query)
	if not words:
		return []
	rows = frappe.get_all(
		"Warehouse",
		filters=[["is_group", "=", 0], ["disabled", "=", 0]]
		+ [["warehouse_name", "like", f"%{word}%"] for word in words],
		fields=["name", "warehouse_name", "warehouse_type", "lft", "rgt"],
		limit=SEARCH_LIMIT * 2,
	)
	rows = [row for row in rows if (row.warehouse_type or "") != "Transit"]
	rows.sort(key=lambda row: rules.natural_key(row.warehouse_name or row.name))
	return [
		{
			"warehouse": row.name,
			"warehouse_name": row.warehouse_name or row.name,
			"trail": rules.location_trail(_ancestor_names(row)),
		}
		for row in rows[:SEARCH_LIMIT]
	]


@frappe.whitelist(methods=["POST"])
def search_projects(query=None):
	"""Jobs still in progress that the caller can see, for the take's job picker.

	Every status but the closed ones (``stock_scan_rules.CLOSED_JOB_STATUSES``) — this
	site's projects are ``Active``, not ERPNext's ``Open``. Active jobs first, then the
	most recently touched. A user who cannot read Projects gets an empty list, not an error.

	``get_all`` behind a doctype-level ``has_permission``, not ``get_list``: this site gives
	every user a User Permission on their own Employee record, applied to all doctypes, and
	Project carries two Employee links (``custom_project_owner``, ``custom_technical_lead``).
	``get_list`` would therefore hide every job owned or led by someone else — the jobs a
	technician is most often sent to. ``take`` already accepts any job still in progress;
	the Project Dashboard lists projects the same way for the same reason.
	"""
	_check_access()
	words = _search_words(query)
	filters = [["status", "not in", sorted(rules.CLOSED_JOB_STATUSES)]]
	or_filters = None
	if words:
		like = f"%{' '.join(words)}%"
		or_filters = [["name", "like", like], ["project_name", "like", like], ["customer", "like", like]]
	if not frappe.has_permission("Project", "read"):
		return []
	rows = frappe.get_all(
		"Project",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "project_name", "customer", "status"],
		order_by="modified desc",
		limit=SEARCH_LIMIT * 2,
	)
	rows.sort(key=lambda row: row.status != rules.ACTIVE_JOB_STATUS)
	return [
		{
			"project": row.name,
			"project_name": row.project_name or row.name,
			"customer": row.customer,
			"status": row.status,
		}
		for row in rows[:SEARCH_LIMIT]
	]


# ---------------------------------------------------------------------------
# The page's boot payload
# ---------------------------------------------------------------------------


def boot_payload(args=None):
	"""What ``www/stock_scan.py`` injects as ``window.EE_STOCK_SCAN_BOOT``.

	``args`` is the page's query: ``w`` (a warehouse — what every label carries), ``item``
	or ``loc`` (a Storage Location). Resolving it here saves the first scan a round trip,
	which on a warehouse's phone signal is most of the wait.
	"""
	args = args or {}
	settings = get_settings()
	initial = None
	for key, kind in rules.QUERY_KINDS:
		value = cstr(args.get(key)).strip()
		if not value:
			continue
		if kind == "item":
			payload = _item_payload(value)
			initial = (
				{"kind": "item", "item": payload}
				if payload
				else {"kind": "unknown", "message": _("There is no item {0}.").format(value)}
			)
		else:
			name = value if kind == "warehouse" else _storage_location_warehouse(value)
			row = _warehouse(name)
			problem = _location_problem(row, value)
			initial = (
				{"kind": "unknown", "message": problem}
				if problem
				else {"kind": "location", "location": _location_payload(row)}
			)
		break

	user = frappe.session.user
	return {
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name") or user,
		"is_supervisor": _is_supervisor(),
		"settings": {
			"require_project_for_take": cint(settings.get("require_project_for_take")),
			"undo_window_minutes": cint(settings.get("undo_window_minutes")),
			# 0 only when a Stock Manager ticked the off switch (Inventory Scanner Settings).
			"browser_history": 0 if cint(settings.get("stock_scan_disable_browser_back")) else 1,
		},
		"recent": _recent(),
		"initial": initial,
		"today": str(getdate()),
		"store_run": _store_run_boot(),
	}


def _store_run_boot():
	"""What "Bought on a store run" needs on the page, or ``None`` to leave the door off.

	Off until a Supplier is ticked *Store-Run Vendor* and the receipt fields exist, and while
	Buying Settings requires a PO on every receipt. **Never raises**: this runs inside the
	page's own boot, and one exception here -- a migrate half-way, a changed table -- would take
	Stock Scan down for everybody, not just the store-run door. So it logs and returns None.
	"""
	try:
		if not _store_run_columns() or _po_required():
			return None
		rows = frappe.get_all(
			"Supplier",
			filters={SUPPLIER_FLAG: 1},
			fields=["name", "supplier_name", "creation", "disabled", "on_hold"],
		)
		suppliers = rules.store_picker(rows)
		if not suppliers:
			return None
		return {
			"suppliers": suppliers,
			"reasons": [{"value": r, "hint": rules.REASON_HINTS[r]} for r in rules.STORE_RUN_REASONS],
			"item_groups": _item_groups_by_use(),
			"uoms": _quick_uoms(),
			"can_create_items": bool(frappe.has_permission("Item", "create")),
			"can_record": bool(
				frappe.has_permission("Purchase Receipt", "create")
				and frappe.has_permission("Purchase Receipt", "submit")
			),
			"open_runs": _open_runs(),
			"refuse_from": _naming_go_live(),
		}
	except Exception:
		frappe.log_error(title="Stock Scan: the store-run door failed to load")
		return None


def _naming_go_live():
	from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming

	return naming.NAMING_GO_LIVE
