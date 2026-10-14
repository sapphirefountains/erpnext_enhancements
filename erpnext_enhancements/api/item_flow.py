"""The guided Item flow's own reads and writes (form kit P8.1, ERPNext TASK-2026-02790).

The Item itself is created and saved by the form kit (``form_kit.api.create_record`` and
``save_step``), like any guided record. What the flow makes *beyond* the Item goes through here,
because none of it is a field of the Item form: the supplier's part number, purchase link and
default (an Item Supplier row and an Item Default), the last buying price (an Item Price on the
buying list), the default bin and its reorder rule (an Item Default and an Item Reorder row), and,
for a fixed-asset item, the Asset. The page that calls these is ``public/js/guided_item/``,
drawn into the guided page's steps through the form kit's extension registry; the same endpoints
are what a purchase order's side panel will call (P8.x).

Things this module is careful about:

* **Every write is the caller's own.** Each endpoint checks the DocType permission it needs before
  anything is written (Item write for the Item's rows, Item Price create or write for the price,
  Asset create for the Asset), and saves without ``ignore_permissions``, so the framework's own
  checks run a second time. Nothing here grants what the Desk would refuse; unlike
  ``api.procurement.save_item_link``, which writes a purchase link with ``ignore_permissions`` for
  a buyer without Item write, this flow is for the people who create Items. The one exception is
  the bin assignment (a Putaway Rule), which ``inventory_enhancements.bin_assignments`` writes
  behind its own gate, as it does for Stock Scan and the bin page (``_assign_bin``).
* **Nothing is made twice on a resend.** The Item's rows and the price are keyed on what they are
  about (the supplier, the company, the bin, the buying list and supplier), so a second identical
  request updates the same row to the same values. The Asset has no such key, since one Item can
  have many Assets, so it carries the page's id for the create in a hidden unique column
  (``Asset.custom_guided_client_id``), the shape ``form_kit.saver.create_record`` uses for a
  guided create: a resend from the same person returns the Asset the first request made, and a
  resend racing the first insert collides on the unique index and reads the winner.
* **A refusal comes before any write, in words.** Each block's values are checked by
  ``inventory_enhancements/item_flow_rules.py`` (pure, tested bench-free) first: a purchase link
  longer than the 140 characters its Data field holds, a reorder level with no quantity (ERPNext
  refuses that too, as "Row #1: Please set reorder quantity"), an Asset with no cost.
* **Reads are honest about what the caller can see.** Prices and Assets are read with
  ``get_list``, so a caller without Item Price or Asset read gets none, and the payload says what
  they may do (``can``) so the page offers only that.
* **The naming preview is Stock Scan's.** ``preview_item`` returns
  ``api.stock_scan.preview_new_item``: the naming guard's own ``blocking_findings`` call over every
  Item code, so the only refusals the name and code step shows are the two the guard enforces
  (a duplicate code, a name that is just the code), and every other finding is advice. It is gated
  on Item create rather than on Stock Scan's roles.

Payloads are in ``form_kit/README.md`` ("The Item flow"). Indentation is tabs (new file).
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate, strip_html_tags

from erpnext_enhancements.inventory_enhancements import item_flow_rules as flow_rules

#: The hidden unique column holding the page's id for an Asset it created.
ASSET_CLIENT_FIELD = "custom_guided_client_id"

#: The Item Supplier column holding the purchase link (``patches/add_item_purchase_links.py``).
PURCHASE_URL_FIELD = "purchase_url"

#: The reorder rule the stock block writes: a request to buy, never to transfer or manufacture.
REORDER_TYPE = "Purchase"

#: The savepoint the bin assignment runs under, so a refusal undoes only the assignment.
ASSIGN_SAVEPOINT = "item_flow_bin_assignment"


def _text(value):
	return cstr(value).strip()


def _item(item_code, ptype):
	"""The Item, after the caller's ``ptype`` permission on it is checked."""
	code = _text(item_code)
	if not code or not frappe.db.exists("Item", code):
		frappe.throw(_("There is no Item {0}.").format(frappe.bold(code or "?")), frappe.DoesNotExistError)
	item = frappe.get_doc("Item", code)
	item.check_permission(ptype)
	return item


def _company():
	"""The company defaults are kept for: the caller's default company, else the site's."""
	company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("company")
	if not company:
		companies = frappe.get_all("Company", pluck="name", order_by="creation asc", limit=1)
		company = companies[0] if companies else None
	return company


def _buying_price_list():
	"""Buying Settings' price list, else the oldest enabled buying list ("Standard Buying")."""
	price_list = frappe.db.get_single_value("Buying Settings", "buying_price_list")
	if not price_list:
		price_list = frappe.db.get_value(
			"Price List", {"buying": 1, "enabled": 1}, "name", order_by="creation asc"
		)
	return price_list


def _default_row(item, company):
	"""The Item's Item Default row for ``company``, added when there is none (one per company)."""
	for row in item.get("item_defaults") or []:
		if row.company == company:
			return row
	return item.append("item_defaults", {"company": company})


def _set(row, fieldname, value):
	"""Set ``fieldname`` on ``row``; True when that changed it. A resend changes nothing, so the
	Item is not saved again and every open copy of it keeps its ``modified``."""
	current = row.get(fieldname)
	if isinstance(value, float):
		if current not in (None, "") and flt(current) == value:
			return False
	elif cstr(current) == cstr(value):
		return False
	row.set(fieldname, value)
	return True


def _prices_by_supplier(item, price_list):
	"""``{supplier: Item Price row}`` on the buying list, in the stock unit, the newest per supplier.

	The same key ``_price_for`` writes to: a price in another unit is a different price, and an
	older row for the same supplier must never be the one shown, or the page would offer a stale
	rate (rows come newest first, so the first one per supplier is kept).
	"""
	if not price_list or not frappe.has_permission("Item Price", "read"):
		return {}
	rows = frappe.get_list(
		"Item Price",
		filters={"item_code": item.name, "price_list": price_list, "uom": item.stock_uom},
		fields=["name", "supplier", "price_list_rate", "uom", "currency"],
		order_by="modified desc",
	)
	out = {}
	for row in rows:
		out.setdefault(row.supplier or "", row)
	return out


def _price_for(item, price_list, supplier):
	"""The Item Price this block keeps for ``supplier`` on the buying list, in the stock unit, or
	None. The same key ERPNext's own duplicate check uses (item, list, unit, supplier), so the
	block can never make a second one."""
	if not price_list or not supplier:
		return None
	return (
		frappe.db.get_value(
			"Item Price",
			{"item_code": item.name, "price_list": price_list, "supplier": supplier, "uom": item.stock_uom},
			"name",
			order_by="modified desc",
		)
		or None
	)


@frappe.whitelist()
def naming_options():
	"""The name builder's pick lists: ``{"segments", "categories", "catalog",
	"consumable_groups", "visible", "total"}``.

	``catalog`` is every live Item Name the caller can read, as its segments
	(``item_flow_rules.catalog_segments``); the page narrows each position's choices to the
	chosen CATEGORY itself. ``categories`` is POL-0602's Appendix A.
	"""
	frappe.has_permission("Item", "read", throw=True)
	from erpnext_enhancements.inventory_enhancements import item_naming
	from erpnext_enhancements.inventory_enhancements import item_naming_rules as naming

	rows, meta = item_naming.read_corpus(include_deleted=False)
	return {
		"segments": list(naming.SEGMENTS),
		"categories": flow_rules.approved_categories(),
		"catalog": flow_rules.catalog_segments(rows),
		"consumable_groups": list(naming.CONSUMABLE_GROUPS),
		"visible": meta["visible"],
		"total": meta["total"],
	}


@frappe.whitelist()
def preview_item(item_code=None, item_name=None, item_group=None, stock_uom=None):
	"""The naming guard's verdict on a new Item before it is created, read-only.

	Stock Scan's quick-Item preview (``api.stock_scan.preview_new_item``): ``exists``, ``similar``,
	``blocking`` (the guard's exact two findings), ``will_refuse``, ``advice`` and ``checked``.
	"""
	frappe.has_permission("Item", "create", throw=True)
	from erpnext_enhancements.api.stock_scan import preview_new_item

	return preview_new_item(_text(item_code), item_name, item_group, stock_uom)


@frappe.whitelist()
def code_blocks(prefix):
	"""The PDT or SRV blocks in use, each with its lowest free number (``item_flow_rules.code_blocks``).

	Occupancy is read from every Item code and every Configurable Product, whoever is asking
	(``item_naming.read_reserved_codes``): a number is taken whether or not the caller can see the
	record holding it. Only the codes are read.
	"""
	frappe.has_permission("Item", "create", throw=True)
	from erpnext_enhancements.inventory_enhancements import item_naming

	family = _text(prefix).upper()
	codes = item_naming.read_reserved_codes()
	if family not in codes:
		frappe.throw(
			_("{0} is not a numbered code family. Use PDT or SRV.").format(frappe.bold(family or "?"))
		)
	return flow_rules.code_blocks(codes[family], family)


def _setup(item):
	"""What the supplier, stock and asset blocks show for ``item`` (see ``get_setup``)."""
	company = _company()
	price_list = _buying_price_list()
	prices = _prices_by_supplier(item, price_list)
	defaults = next((row for row in item.get("item_defaults") or [] if row.company == company), None)
	suppliers = [row for row in item.get("supplier_items") or [] if row.supplier]
	names = {}
	if suppliers:
		names = {
			row.name: row.supplier_name
			for row in frappe.get_all(
				"Supplier",
				filters={"name": ["in", [row.supplier for row in suppliers]]},
				fields=["name", "supplier_name"],
			)
		}
	has_url = frappe.get_meta("Item Supplier").has_field(PURCHASE_URL_FIELD)
	assets = []
	if cint(item.is_fixed_asset) and frappe.has_permission("Asset", "read"):
		assets = frappe.get_list(
			"Asset",
			filters={"item_code": item.name},
			fields=["name", "asset_name", "location", "docstatus", "status", "purchase_date"],
			order_by="creation desc",
			limit_page_length=20,
		)
	from erpnext_enhancements.inventory_enhancements import stock_scan_rules

	can_price = bool(price_list) and bool(
		frappe.has_permission("Item Price", "create") or frappe.has_permission("Item Price", "write")
	)
	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"modified": str(item.modified),
		"stock_uom": item.stock_uom,
		"is_stock_item": cint(item.is_stock_item),
		"is_fixed_asset": cint(item.is_fixed_asset),
		"asset_category": item.get("asset_category"),
		"company": company,
		"price_list": price_list,
		"currency": frappe.db.get_value("Price List", price_list, "currency") if price_list else None,
		"default_supplier": defaults.default_supplier if defaults else None,
		"default_warehouse": defaults.default_warehouse if defaults else None,
		"suppliers": [
			{
				"supplier": row.supplier,
				"supplier_name": names.get(row.supplier) or row.supplier,
				"supplier_part_no": row.supplier_part_no or "",
				"purchase_url": (row.get(PURCHASE_URL_FIELD) or "") if has_url else "",
				"rate": prices[row.supplier].price_list_rate if row.supplier in prices else None,
			}
			for row in suppliers
		],
		"reorder": [
			{
				"warehouse": row.warehouse,
				"level": row.warehouse_reorder_level,
				"qty": row.warehouse_reorder_qty,
				"material_request_type": row.material_request_type,
			}
			for row in item.get("reorder_levels") or []
		],
		"auto_reorder": cint(frappe.db.get_single_value("Stock Settings", "auto_indent")),
		"assets": [
			{
				"name": row.name,
				"asset_name": row.asset_name,
				"location": row.location,
				"docstatus": cint(row.docstatus),
				"status": row.status,
				"purchase_date": str(row.purchase_date) if row.purchase_date else None,
			}
			for row in assets
		],
		"can": {
			"write": 1 if frappe.has_permission("Item", "write", doc=item) else 0,
			"price": 1 if can_price else 0,
			"link": 1 if has_url else 0,
			"asset": 1 if frappe.has_permission("Asset", "create") else 0,
			"label": 1 if stock_scan_rules.LABEL_ROLES.intersection(frappe.get_roles()) else 0,
		},
	}


@frappe.whitelist()
def get_setup(item_code):
	"""The supplier, stock and asset blocks' view of one Item.

	``{"item_code", "item_name", "modified", "stock_uom", "is_stock_item", "is_fixed_asset",
	"asset_category", "company", "price_list", "currency", "default_supplier",
	"default_warehouse", "suppliers": [{"supplier", "supplier_name", "supplier_part_no",
	"purchase_url", "rate"}], "reorder": [{"warehouse", "level", "qty", "material_request_type"}],
	"auto_reorder", "assets": [...], "can": {"write", "price", "link", "asset", "label"}}``.
	"""
	return _setup(_item(item_code, "read"))


@frappe.whitelist(methods=["POST"])
def save_supplier(item_code, supplier, supplier_part_no=None, purchase_url=None, rate=None, make_default=1):
	"""Record who the Item is bought from: the Item Supplier row (part number and purchase link),
	the company's default supplier (when ``make_default``) and the buying price (when ``rate`` is
	given). Keyed on the supplier, so sending it twice changes nothing more. Returns ``get_setup``.
	"""
	item = _item(item_code, "write")
	supplier = _text(supplier)
	if not supplier or not frappe.db.exists("Supplier", supplier):
		frappe.throw(_("Choose the supplier from the list."))
	url = _text(purchase_url)
	problem = flow_rules.purchase_url_problem(url) or flow_rules.price_problem(rate)
	if problem:
		frappe.throw(_(problem))
	has_url = frappe.get_meta("Item Supplier").has_field(PURCHASE_URL_FIELD)

	price_list = _buying_price_list()
	price_name = None
	rate_value = None if rate in (None, "") else flt(rate)
	if rate_value is not None:
		if not price_list:
			frappe.throw(
				_("There is no buying price list to record the price on. Set one in Buying Settings.")
			)
		price_name = _price_for(item, price_list, supplier)
		# Refused before anything is written, so the Item's rows never land without their price.
		if price_name:
			frappe.get_doc("Item Price", price_name).check_permission("write")
		else:
			frappe.has_permission("Item Price", "create", throw=True)

	row = next((r for r in item.get("supplier_items") or [] if r.supplier == supplier), None)
	changed = False
	if row is None:
		row = item.append("supplier_items", {"supplier": supplier})
		changed = True
	if supplier_part_no is not None:
		changed = _set(row, "supplier_part_no", _text(supplier_part_no) or None) or changed
	if has_url and purchase_url is not None:
		changed = _set(row, PURCHASE_URL_FIELD, url or None) or changed
	if cint(make_default):
		company = _company()
		if company:
			changed = _set(_default_row(item, company), "default_supplier", supplier) or changed
	if changed:
		item.save()

	if rate_value is not None:
		if price_name:
			price = frappe.get_doc("Item Price", price_name)
			if flt(price.price_list_rate) != rate_value:
				price.price_list_rate = rate_value
				price.save()
		else:
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": item.name,
					"price_list": price_list,
					"supplier": supplier,
					"uom": item.stock_uom,
					"price_list_rate": rate_value,
				}
			).insert()
	return _setup(frappe.get_doc("Item", item.name))


@frappe.whitelist(methods=["POST"])
def save_stock(item_code, warehouse, reorder_level=None, reorder_qty=None):
	"""Record where a stock Item is kept: the company's default warehouse (its bin) and, when a
	level is given, the bin's reorder rule (a Purchase request). Keyed on the company and the bin.
	Moving to another bin moves the old bin's Purchase rule with it, unless the new bin has its
	own, when the old one is left and ``note`` says so. Returns ``get_setup`` plus ``note``.
	"""
	item = _item(item_code, "write")
	if not cint(item.is_stock_item):
		frappe.throw(_("{0} is not kept in stock, so it has no bin.").format(frappe.bold(item.name)))
	name = _text(warehouse)
	bin_row = (
		frappe.db.get_value(
			"Warehouse", name, ["name", "company", "is_group", "disabled", "warehouse_type"], as_dict=True
		)
		if name
		else None
	)
	if not bin_row:
		frappe.throw(_("Choose the bin from the list."))
	if cint(bin_row.is_group) or cint(bin_row.disabled) or bin_row.warehouse_type == "Transit":
		frappe.throw(
			_("{0} cannot hold stock: choose a bin, not a group, a disabled or a transit warehouse.").format(
				frappe.bold(name)
			)
		)
	problem = flow_rules.reorder_problem(reorder_level, reorder_qty)
	if problem:
		frappe.throw(_(problem))

	defaults = _default_row(item, bin_row.company)
	before = defaults.get("default_warehouse")
	changed = _set(defaults, "default_warehouse", name)
	row = _purchase_rule(item, name)
	note = None
	if before and before != name:
		old = _purchase_rule(item, before)
		if old is not None and row is None:
			# The item moved bins: its buy-more rule moves with it, rather than staying on a bin it
			# no longer lives in and raising requests for stock that is not there.
			old.warehouse = name
			old.warehouse_group = name
			row = old
			changed = True
			note = _("The reorder rule moved from {0} to {1}.").format(before, name)
		elif old is not None:
			note = _(
				"{0} still has its own reorder rule; remove it under Auto re-order below if it no longer applies."
			).format(before)
	if reorder_level not in (None, ""):
		if row is None:
			row = item.append(
				"reorder_levels",
				{"warehouse": name, "warehouse_group": name, "material_request_type": REORDER_TYPE},
			)
			changed = True
		changed = _set(row, "warehouse_reorder_level", flt(reorder_level)) or changed
		changed = _set(row, "warehouse_reorder_qty", flt(reorder_qty)) or changed
	if changed:
		item.save()
	unlisted = _assign_bin(item.name, name)
	out = _setup(frappe.get_doc("Item", item.name))
	out["note"] = " ".join(n for n in (note, unlisted) if n) or None
	return out


def _assign_bin(item_code, warehouse):
	"""Put the item on its bin the way the bin pages do; a sentence when it cannot be, else None.

	A bin is the item's in two records: the Item Default's ``default_warehouse``, written above as
	the caller, and an enabled Putaway Rule, which is what Stock Scan and the bin page count as
	assigned and what ERPNext's *Apply Putaway Rule* routes received stock by
	(``inventory_enhancements.bin_assignments``, v1.689.0). ``ensure_assignment`` is that module's
	plain helper for this call: idempotent, and it writes the rule past ERPNext's Putaway Rule
	permission behind the module's own gate (a stock role and Item read; the owner's decision of
	2026-10-14). The old bin's rule is left alone, as that module's own move leaves it while stock
	is still there: the bin page takes an item off a bin.

	A refusal (no stock role, a template item) must not undo the bin the person just saved, so it
	rolls back to a savepoint and comes back as the reason, for ``note``. Messages are muted while
	it runs, because a caught ``frappe.throw`` would still reach the browser as a red dialog.
	Anything else is not caught: the request fails and nothing of it is saved.
	"""
	# Imported here: the bin module pulls in Stock Scan's, which this flow needs only for this.
	from erpnext_enhancements.inventory_enhancements.bin_assignments import ensure_assignment

	muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	frappe.db.savepoint(ASSIGN_SAVEPOINT)
	try:
		ensure_assignment(item_code, warehouse)
	except (frappe.ValidationError, frappe.PermissionError) as refused:
		frappe.db.rollback(save_point=ASSIGN_SAVEPOINT)
		reason = strip_html_tags(cstr(refused)).strip() or _("the bin list refused it.")
		return _("It is not on the bin's list yet: {0}").format(reason)
	finally:
		frappe.flags.mute_messages = muted
	return None


def _purchase_rule(item, warehouse):
	"""The Item's Purchase reorder rule for ``warehouse``, or None (a Transfer rule is not ours)."""
	return next(
		(
			r
			for r in item.get("reorder_levels") or []
			if r.warehouse == warehouse and (r.material_request_type or REORDER_TYPE) == REORDER_TYPE
		),
		None,
	)


def _asset_made(client_id):
	"""The Asset the caller's earlier create with ``client_id`` made, or None."""
	if not frappe.get_meta("Asset").has_field(ASSET_CLIENT_FIELD):
		return None
	name = frappe.db.get_value("Asset", {ASSET_CLIENT_FIELD: client_id, "owner": frappe.session.user}, "name")
	return name or None


@frappe.whitelist(methods=["POST"])
def create_asset(item_code, client_id, location, net_purchase_amount, purchase_date=None, rentable=0):
	"""Make the draft Asset for a fixed-asset Item, so the two never come apart (ER-2026-420503).

	``client_id`` is the page's id for this create: sent again (a lost answer, a double tap), it
	returns the Asset the first request made, marked ``deduped``. The Asset takes the Item's name
	and category, the company, ``location``, the cost and the purchase date (today when not given),
	and ``custom_rentable`` for a rental fountain; it is left a draft for Accounts to submit.
	Returns ``{"name", "deduped"}``.
	"""
	item = _item(item_code, "read")
	if not cint(item.is_fixed_asset):
		frappe.throw(_("{0} is not a fixed-asset item.").format(frappe.bold(item.name)))
	if not item.get("asset_category"):
		frappe.throw(_("Give {0} its Asset Category first.").format(frappe.bold(item.name)))
	cid = flow_rules.client_id(client_id)
	if not cid:
		frappe.throw(_("The request did not carry its id. Reload and try again."))
	frappe.has_permission("Asset", "create", throw=True)
	if not frappe.get_meta("Asset").has_field(ASSET_CLIENT_FIELD):
		frappe.throw(
			_("Creating an Asset here is not set up on this site yet (Asset.{0}).").format(ASSET_CLIENT_FIELD)
		)
	problem = flow_rules.asset_problem(location, net_purchase_amount)
	if problem:
		frappe.throw(_(problem))
	place = _text(location)
	if not frappe.db.exists("Location", place):
		frappe.throw(_("Choose where it is kept from the list."))

	made = _asset_made(cid)
	if made:
		return {"name": made, "deduped": 1}

	asset = frappe.new_doc("Asset")
	asset.item_code = item.name
	asset.asset_name = item.item_name or item.name
	asset.asset_category = item.asset_category
	asset.company = _company()
	asset.location = place
	asset.purchase_date = getdate(purchase_date) if purchase_date else getdate(nowdate())
	asset.net_purchase_amount = flt(net_purchase_amount)
	# The site's default (Property Setter, v1.455.0): any other type cannot be saved as a draft.
	asset.asset_type = asset.get("asset_type") or "Existing Asset"
	if frappe.get_meta("Asset").has_field("custom_rentable"):
		asset.custom_rentable = 1 if cint(rentable) else 0
	asset.set(ASSET_CLIENT_FIELD, cid)
	try:
		asset.insert()
	except frappe.UniqueValidationError:
		# A resend that raced the first insert waited on the unique index and collided: a fresh
		# transaction sees the winner's commit. Anything else that collided is raised as it was.
		frappe.db.rollback()
		made = _asset_made(cid)
		if not made:
			raise
		return {"name": made, "deduped": 1}
	return {"name": asset.name, "deduped": 0}
