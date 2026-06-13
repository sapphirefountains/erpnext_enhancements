"""Whitelisted endpoints for the Inventory Scanner Audit desk page.

The page (``inventory_enhancements/page/inventory_scanner_audit``) lets an
inventory clerk scan shelf/bin location and item barcodes (keyboard-wedge or the
device camera) and type the counted quantity for a physical stock count. Counts
accumulate in a resumable ``Inventory Count Session``; each line snapshots the
system on-hand quantity (``erpnext.stock.utils.get_stock_balance``) and its
variance at scan time. Finalizing aggregates the lines per (item, warehouse)
into a **draft** Stock Reconciliation for a Stock Manager to review and submit —
nothing touches the stock ledger here.

Access is gated to ``ALLOWED_ROLES``; the identity is always taken from
``frappe.session.user`` (never trusted from the client). After the role gate,
session/reconciliation writes use ``ignore_permissions=True`` so a clerk who is
not a full Stock User can still build the draft (house pattern, mirroring
``api.time_kiosk``). Submitting the reconciliation stays a Stock Manager action
in the normal Stock Reconciliation form.

Locations resolve to a single warehouse: stock in ERPNext is tracked at the
warehouse level, not per bin, so counts across several bins of one warehouse sum
into one reconciliation row. Serialized/batch items are out of scope for v1
(plain warehouse quantity only) and are flagged to the clerk in the UI.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
	get_settings,
)

# Roles permitted to run inventory counts. "Inventory Clerk" is seeded by
# patches.create_inventory_clerk_role; the page + doctypes also gate on these.
ALLOWED_ROLES = {"System Manager", "Stock Manager", "Inventory Clerk"}
# Roles allowed to act on a session they do not own (supervisors).
SUPERVISOR_ROLES = {"System Manager", "Stock Manager"}


def _check_access():
	"""Throw unless the caller holds one of ALLOWED_ROLES."""
	if not ALLOWED_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(_("You are not permitted to use the Inventory Scanner."), frappe.PermissionError)


# ---------------------------------------------------------------------------
# Bootstrap + scan resolution
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_bootstrap():
	"""Initial payload for the page: resolved settings + the caller's open session."""
	_check_access()
	settings = get_settings()
	return {
		"user": frappe.session.user,
		"settings": {
			"default_warehouse": settings.get("default_warehouse"),
			"require_variance_reason": cint(settings.get("require_variance_reason")),
			"block_negative_counts": cint(settings.get("block_negative_counts")),
			"allow_unknown_item": cint(settings.get("allow_unknown_item")),
			"enable_camera_scan": cint(settings.get("enable_camera_scan")),
		},
		"session": _session_payload(_active_session_name()),
	}


@frappe.whitelist()
def resolve_scan(code, warehouse=None):
	"""Classify a raw scanned/typed code as a location, an item, or unknown.

	Storage Locations are checked first (by ``barcode`` then by code/name), then
	Items (by Item Barcode child then by exact ``item_code``). Location and item
	code-spaces should therefore be kept distinct. When the scan resolves to an
	item and ``warehouse`` is supplied (the active location's warehouse), the
	current on-hand ``system_qty`` is included so the page can show the variance
	before the clerk types a count.
	"""
	_check_access()
	code = (code or "").strip()
	if not code:
		frappe.throw(_("Empty scan."))

	# 1) Storage Location — by printed barcode, then by its code (the record name).
	loc = frappe.db.get_value(
		"Storage Location",
		{"barcode": code, "disabled": 0},
		["name", "warehouse", "location_name"],
		as_dict=True,
	)
	if not loc and frappe.db.exists("Storage Location", code):
		loc = frappe.db.get_value(
			"Storage Location", code, ["name", "warehouse", "location_name"], as_dict=True
		)
	if loc:
		return {
			"type": "location",
			"storage_location": loc.name,
			"warehouse": loc.warehouse,
			"warehouse_name": frappe.db.get_value("Warehouse", loc.warehouse, "warehouse_name") or loc.warehouse,
			"location_name": loc.location_name or loc.name,
		}

	# 2) Item — by barcode child table, then by exact item code.
	item_code = frappe.db.get_value("Item Barcode", {"barcode": code}, "parent")
	if not item_code and frappe.db.exists("Item", code):
		item_code = code
	if item_code:
		item = frappe.db.get_value(
			"Item",
			item_code,
			["item_name", "stock_uom", "disabled", "has_serial_no", "has_batch_no"],
			as_dict=True,
		)
		payload = {
			"type": "item",
			"item_code": item_code,
			"item_name": item.item_name,
			"uom": item.stock_uom,
			"disabled": cint(item.disabled),
			"has_serial_no": cint(item.has_serial_no),
			"has_batch_no": cint(item.has_batch_no),
		}
		if warehouse:
			payload["system_qty"] = _system_qty(item_code, warehouse)
		return payload

	return {"type": "unknown", "code": code}


@frappe.whitelist()
def lookup_item(query, limit=10):
	"""Manual item search fallback for un-barcoded items (filters values are bound)."""
	_check_access()
	query = (query or "").strip()
	if not query:
		return []
	like = "%{0}%".format(query)
	return frappe.get_all(
		"Item",
		filters={"disabled": 0},
		or_filters={"item_code": ["like", like], "item_name": ["like", like]},
		fields=["name as item_code", "item_name", "stock_uom as uom"],
		limit=cint(limit) or 10,
		order_by="modified desc",
	)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_active_session():
	"""Return the caller's open session (with lines + summary) or None."""
	_check_access()
	return _session_payload(_active_session_name())


@frappe.whitelist()
def start_session(default_warehouse=None):
	"""Create an Open session for the caller, or resume their existing one.

	Enforces one open session per user (mirrors the single-active-interval rule
	in ``api.time_kiosk``)."""
	_check_access()
	existing = _active_session_name()
	if existing:
		return _session_payload(existing)

	settings = get_settings()
	company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
	doc = frappe.get_doc(
		{
			"doctype": "Inventory Count Session",
			"counted_by": frappe.session.user,
			"company": company,
			"default_warehouse": default_warehouse or settings.get("default_warehouse"),
			"status": "Open",
			"start_time": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)
	return _session_payload(doc.name)


@frappe.whitelist()
def add_count(session, item_code, counted_qty, storage_location=None, warehouse=None, scanned_barcode=None, reason=None):
	"""Record one counted line (upserting per location+item+warehouse).

	Resolves the warehouse (explicit arg -> the location's warehouse -> the
	session/default warehouse), snapshots the system on-hand qty, validates
	against the negative/variance-reason settings, and saves the session.
	"""
	_check_access()
	doc = _get_open_session(session)
	settings = get_settings()

	counted_qty = flt(counted_qty)
	if cint(settings.get("block_negative_counts")) and counted_qty < 0:
		frappe.throw(_("Counted quantity cannot be negative."))

	item = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True)
	if not item:
		frappe.throw(_("Item {0} not found.").format(item_code))

	# Counts post at warehouse granularity; a bin resolves to its warehouse.
	if storage_location and not warehouse:
		warehouse = frappe.db.get_value("Storage Location", storage_location, "warehouse")
	warehouse = warehouse or doc.default_warehouse or settings.get("default_warehouse")
	if not warehouse:
		frappe.throw(_("Scan a location first, or set a default warehouse, before counting items."))

	system_qty = _system_qty(item_code, warehouse)
	variance = counted_qty - system_qty

	reason = (reason or "").strip()
	if cint(settings.get("require_variance_reason")) and variance != 0 and not reason:
		frappe.throw(
			_("{0}: counted {1} but system shows {2}. A reason is required for the variance.").format(
				item_code, counted_qty, system_qty
			)
		)

	values = {
		"storage_location": storage_location,
		"warehouse": warehouse,
		"item_code": item_code,
		"item_name": item.item_name,
		"uom": item.stock_uom,
		"system_qty": system_qty,
		"counted_qty": counted_qty,
		"variance": variance,
		"scanned_barcode": scanned_barcode,
		"scan_time": now_datetime(),
		"reason": reason,
	}

	# Upsert: one line per (storage_location, item, warehouse).
	existing = None
	for row in doc.lines:
		if (
			row.item_code == item_code
			and (row.storage_location or None) == (storage_location or None)
			and row.warehouse == warehouse
		):
			existing = row
			break
	if existing:
		existing.update(values)
	else:
		doc.append("lines", values)

	doc.save(ignore_permissions=True)
	return _session_payload(doc.name)


@frappe.whitelist()
def remove_line(session, idx):
	"""Drop a counted line (by its 1-based grid idx) from an open session."""
	_check_access()
	doc = _get_open_session(session)
	idx = cint(idx)
	doc.lines = [row for row in doc.lines if row.idx != idx]
	doc.save(ignore_permissions=True)
	return _session_payload(doc.name)


@frappe.whitelist()
def cancel_session(session):
	"""Abandon an open session without producing a reconciliation."""
	_check_access()
	doc = _get_open_session(session)
	doc.status = "Cancelled"
	doc.end_time = now_datetime()
	doc.save(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def finalize_session(session):
	"""Aggregate the session's lines into a DRAFT Stock Reconciliation.

	Lines are summed per (item, warehouse) — multiple bins of one item in a
	warehouse collapse into a single reconciliation row carrying the counted
	total and the current valuation rate. The reconciliation is inserted as a
	draft (docstatus 0); a Stock Manager reviews and submits it.
	"""
	_check_access()
	doc = _get_open_session(session)
	if not doc.lines:
		frappe.throw(_("Nothing to reconcile — no items have been counted."))

	company = doc.company or frappe.defaults.get_global_default("company")
	if not company:
		frappe.throw(_("Set a Company on the session before finalizing."))

	aggregated = _aggregate_counts(doc)
	sr = _build_reconciliation(company, aggregated)
	sr.insert(ignore_permissions=True)  # DRAFT — left for a Stock Manager to submit.

	# Stamp the session in place; the lines are unchanged, so skip a full re-save.
	doc.db_set("stock_reconciliation", sr.name)
	doc.db_set("status", "Finalized")
	doc.db_set("end_time", now_datetime())

	return {
		"session": _session_payload(doc.name),
		"stock_reconciliation": sr.name,
		"reconciliation_url": "/app/stock-reconciliation/{0}".format(sr.name),
		"rows": len(aggregated),
	}


def _aggregate_counts(doc):
	"""Sum counted qty per (item_code, warehouse) across the session's lines.

	Stock is tracked at warehouse level, so multiple bins of one item in a
	warehouse collapse into a single key.
	"""
	aggregated = {}
	for row in doc.lines:
		key = (row.item_code, row.warehouse)
		aggregated[key] = aggregated.get(key, 0.0) + flt(row.counted_qty)
	return aggregated


def _build_reconciliation(company, aggregated):
	"""Build (without inserting) a draft Stock Reconciliation from aggregated counts.

	One row per (item, warehouse) carrying the counted total and the current
	valuation rate. The company's stock-adjustment account is pre-filled when set
	so the draft is ready for a Stock Manager to submit.
	"""
	sr = frappe.new_doc("Stock Reconciliation")
	sr.purpose = "Stock Reconciliation"
	sr.company = company
	expense_account = frappe.get_cached_value("Company", company, "stock_adjustment_account")
	if expense_account:
		sr.expense_account = expense_account
	for (item_code, warehouse), counted in aggregated.items():
		_qty, valuation_rate = _qty_and_rate(item_code, warehouse)
		sr.append(
			"items",
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"qty": counted,
				"valuation_rate": valuation_rate,
			},
		)
	return sr


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _active_session_name(user=None):
	user = user or frappe.session.user
	return frappe.db.get_value("Inventory Count Session", {"counted_by": user, "status": "Open"}, "name")


def _get_open_session(session):
	"""Load an Open session the caller is allowed to act on, else throw."""
	doc = frappe.get_doc("Inventory Count Session", session)
	if doc.counted_by != frappe.session.user and not SUPERVISOR_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(_("This count session belongs to another user."), frappe.PermissionError)
	if doc.status != "Open":
		frappe.throw(_("Count session {0} is {1}, not open.").format(doc.name, doc.status))
	return doc


def _system_qty(item_code, warehouse):
	"""Current on-hand quantity for an item + warehouse."""
	from erpnext.stock.utils import get_stock_balance

	return flt(get_stock_balance(item_code, warehouse))


def _qty_and_rate(item_code, warehouse):
	"""Return (current qty, current valuation rate) for a reconciliation row."""
	from erpnext.stock.utils import get_stock_balance

	result = get_stock_balance(item_code, warehouse, with_valuation_rate=True)
	if isinstance(result, (list, tuple)):
		return flt(result[0]), flt(result[1])
	return flt(result), 0.0


def _session_payload(name):
	"""Serialize a session (header + lines + variance summary) for the client."""
	if not name:
		return None
	doc = frappe.get_doc("Inventory Count Session", name)
	lines = [
		{
			"idx": row.idx,
			"storage_location": row.storage_location,
			"warehouse": row.warehouse,
			"item_code": row.item_code,
			"item_name": row.item_name,
			"uom": row.uom,
			"system_qty": flt(row.system_qty),
			"counted_qty": flt(row.counted_qty),
			"variance": flt(row.variance),
			"reason": row.reason,
			"scan_time": str(row.scan_time) if row.scan_time else None,
		}
		for row in doc.lines
	]
	with_variance = sum(1 for row in doc.lines if flt(row.variance) != 0)
	return {
		"name": doc.name,
		"status": doc.status,
		"counted_by": doc.counted_by,
		"company": doc.company,
		"default_warehouse": doc.default_warehouse,
		"start_time": str(doc.start_time) if doc.start_time else None,
		"stock_reconciliation": doc.stock_reconciliation,
		"lines": lines,
		"summary": {
			"lines": len(lines),
			"with_variance": with_variance,
			"total_abs_variance": sum(abs(flt(row.variance)) for row in doc.lines),
		},
	}
