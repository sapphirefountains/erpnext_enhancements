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
from frappe.utils import cint, cstr, flt, get_datetime, getdate, now_datetime, nowdate, strip_html

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


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def _check_access():
	if not rules.SCAN_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to use Stock Scan."), frappe.PermissionError)


def _is_supervisor():
	return bool(rules.SUPERVISOR_ROLES.intersection(frappe.get_roles()))


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


def _item_payload(item_code, location=None):
	"""Everything the item card shows, at ``location`` when there is one."""
	item = _item(item_code)
	if not item:
		return None
	payload = {
		"item_code": item.name,
		"item_name": item.item_name or item.name,
		"description": _plain_description(item),
		"image": item.image or None,
		"stock_uom": item.stock_uom,
		"whole_number": _whole_number(item.stock_uom),
		"blocked": rules.item_refusal(item, nowdate()),
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
	if not payload["blocked"]:
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
# Undo and history
# ---------------------------------------------------------------------------


def _age_minutes(posted_at):
	if not posted_at:
		return None
	return (now_datetime() - get_datetime(posted_at)).total_seconds() / 60.0


def _undo_refusal(doc, settings=None):
	settings = settings or get_settings()
	return rules.undo_refusal(
		doc.status,
		doc.posted_by == frappe.session.user,
		_is_supervisor(),
		_age_minutes(doc.posted_at),
		settings.get("undo_window_minutes"),
	)


@frappe.whitelist(methods=["POST"])
def undo(log):
	"""Reverse a save by cancelling its voucher, as the session user.

	The person who saved it may undo it within the Undo Window; a Stock Manager may undo any
	save. ERPNext still decides whether the voucher *can* be cancelled — undoing a receipt
	whose stock has since been taken would drive the location negative, and it says so.
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
		"can_undo": refusal is None,
		"undo_refusal": refusal,
	}


def _message(doc, repeated=False, undone=False, already_canceled=False):
	amount = f"{rules.plain(doc.qty)} {doc.stock_uom or ''}".strip()
	item = doc.item_name or doc.item_code
	if already_canceled:
		return _("{0} had already been canceled in the Desk, so nothing moved now. Marked undone.").format(
			doc.voucher_no
		)
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
	return {
		"log": _log_row(doc, names),
		"item": _item_payload(doc.item_code, location) if location else None,
		"message": _message(doc, repeated=repeated, undone=undone, already_canceled=already_canceled),
		"repeated": bool(repeated),
	}


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
		},
		"recent": _recent(),
		"initial": initial,
		"today": str(getdate()),
	}
