# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""ERPNext generation for Product Configurations — Item, BOM, Selling Price.

Atomicity contract (the document-merge fail-closed philosophy): one whitelisted
request is one implicit transaction, so there is deliberately **no**
``frappe.db.commit()`` anywhere in this module — if the Item Price upsert
throws last, the Item, the BOM, the ``Item.default_bom`` pointer and the
config's link-backs all roll back together. "Item created but BOM failed"
is structurally impossible.

Inserts run *without* ``ignore_permissions`` — the framework enforces Item /
BOM / Item Price permissions; :func:`_assert_generation_permitted` just fails
fast with one clean message before anything is written.

Costing model: component Items carry the seeded ``valuation_rate`` and BOMs
use ``rm_cost_as_per = "Valuation Rate"`` — on a site with no purchase history
ERPNext falls back to ``Item.valuation_rate`` (BOM cost equals the seed), and
once real receipts exist the real moving average silently takes over. Labor is
deliberately NOT in the BOM (no operations): the configurator's selling price
already carries labor + markup, and BOM operations would double-count it. To
add operations later: one Workstation at the product's labor rate, one
"Assembly" Operation, ``with_operations = 1``.
"""

import frappe
from frappe import _
from frappe.utils import flt

CONFIG_DOCTYPE = "Product Configuration"
SELLING_PRICE_LIST = "Standard Selling"
BUYING_PRICE_LIST = "Standard Buying"
DEFAULT_SUPPLIER_GROUP = "Distributor"


def generate(configuration_name):
	"""Create/refresh the Item, default BOM and selling Item Price for a config."""
	cfg = frappe.get_doc(CONFIG_DOCTYPE, configuration_name)
	_assert_config_ready(cfg)
	_assert_generation_permitted()

	ensure_component_items(cfg.product)

	item_code = _ensure_product_item(cfg)
	bom_name = _ensure_bom(cfg, item_code)
	price_name = _upsert_selling_price(cfg, item_code)

	cfg.mark_generated(item_code, bom_name, price_name)
	return {"item": item_code, "bom": bom_name, "item_price": price_name}


def ensure_component_items(product_name):
	"""Create missing Suppliers, Item Groups and component Items for a product.

	Reuse-if-exists everywhere; never overwrites an existing record (live
	purchasing data beats the seed). Safe to run repeatedly.
	"""
	product = frappe.get_doc("Configurable Product", product_name)
	out = {"items_created": [], "items_reused": [], "suppliers_created": []}

	_ensure_item_group(product.component_item_group or "E-Stop Components")
	_ensure_item_group(product.item_group or "Configured Products")

	suppliers = {}
	for row in product.components or []:
		name = (row.supplier_name or "").strip()
		if name and name not in suppliers:
			suppliers[name] = _ensure_supplier(name, out)

	seen = set()
	for row in product.components or []:
		code = (row.item_code or "").strip()
		if not code or code in seen:
			continue
		seen.add(code)
		if frappe.db.exists("Item", code):
			out["items_reused"].append(code)
			continue
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": (row.component_name or code)[:140],
				"item_group": product.component_item_group or "E-Stop Components",
				"stock_uom": row.uom or "Nos",
				"is_stock_item": 1,
				"is_purchase_item": 1,
				"is_sales_item": 0,
				"description": _component_description(row),
				"valuation_rate": flt(row.unit_cost),
			}
		)
		supplier = suppliers.get((row.supplier_name or "").strip())
		if supplier:
			item.append(
				"supplier_items",
				{"supplier": supplier, "supplier_part_no": row.manufacturer_part_no or ""},
			)
		item.insert()
		out["items_created"].append(code)
		_ensure_buying_price(code, row.uom or "Nos", flt(row.unit_cost))

	return out


# --------------------------------------------------------------------- guards
def _assert_config_ready(cfg):
	if not (cfg.part_number or "").strip():
		frappe.throw(_("The configuration has no part number — save it first."))
	if not (cfg.parts or []):
		frappe.throw(
			_("The configuration has no parts — the product definition needs component rows.")
		)
	if flt(cfg.sell_price) <= 0:
		frappe.throw(_("The configuration has no selling price."))


def _assert_generation_permitted():
	"""Fail fast with one clean message instead of a mid-flight PermissionError."""
	missing = []
	for doctype, ptype in (
		("Supplier", "create"),
		("Item", "create"),
		("BOM", "create"),
		("BOM", "submit"),
		("Item Price", "create"),
	):
		if not frappe.has_permission(doctype, ptype):
			missing.append(f"{doctype} ({ptype})")
	if missing:
		frappe.throw(
			_(
				"You need these permissions to generate ERPNext records: {0}. "
				"Ask an administrator to grant the relevant stock/manufacturing roles."
			).format(", ".join(missing)),
			frappe.PermissionError,
		)


# ------------------------------------------------------------------ masters
def _ensure_item_group(group_name):
	if not group_name or frappe.db.exists("Item Group", group_name):
		return
	frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": group_name,
			"parent_item_group": "All Item Groups",
			"is_group": 0,
		}
	).insert()


def _ensure_supplier(supplier_name, out):
	existing = frappe.db.exists("Supplier", {"supplier_name": supplier_name})
	if existing:
		return existing
	if not frappe.db.exists("Supplier Group", DEFAULT_SUPPLIER_GROUP):
		frappe.get_doc(
			{
				"doctype": "Supplier Group",
				"supplier_group_name": DEFAULT_SUPPLIER_GROUP,
				"parent_supplier_group": "All Supplier Groups",
				"is_group": 0,
			}
		).insert()
	supplier = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": supplier_name,
			"supplier_group": DEFAULT_SUPPLIER_GROUP,
			"supplier_type": "Company",
			"country": "United States",
		}
	)
	supplier.insert()
	out["suppliers_created"].append(supplier.name)
	return supplier.name


def _component_description(row):
	bits = [row.component_name or row.item_code]
	if row.manufacturer:
		bits.append(f"Mfr: {row.manufacturer}")
	if row.manufacturer_part_no:
		bits.append(f"Mfr part no: {row.manufacturer_part_no}")
	if row.supplier_name:
		bits.append(f"Vendor: {row.supplier_name}")
	if row.notes:
		bits.append(row.notes)
	return " | ".join(bits)


def _ensure_buying_price(item_code, uom, rate):
	"""Standard Buying price — procurement convenience, not BOM costing."""
	if rate <= 0 or not frappe.db.exists("Price List", BUYING_PRICE_LIST):
		return
	if frappe.db.exists(
		"Item Price", {"item_code": item_code, "price_list": BUYING_PRICE_LIST, "uom": uom}
	):
		return
	frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": BUYING_PRICE_LIST,
			"uom": uom,
			"price_list_rate": rate,
		}
	).insert()


# ------------------------------------------------------------ configured item
def _ensure_product_item(cfg):
	item_code = cfg.part_number
	product = frappe.get_cached_doc("Configurable Product", cfg.product)
	item_name = f"{product.product_name} {item_code}"[:140]
	description = _compose_description(cfg, product)

	if frappe.db.exists("Item", item_code):
		item = frappe.get_doc("Item", item_code)
		ours = item.get("custom_source_configuration") or item.item_group == (
			product.item_group or "Configured Products"
		)
		if not ours:
			frappe.throw(
				_(
					"Item {0} already exists and was not created by the configurator — "
					"rename or remove it before generating."
				).format(item_code),
				title=_("Item Code Taken"),
			)
		item.db_set("item_name", item_name, update_modified=False)
		item.db_set("description", description, update_modified=False)
		return item_code

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": product.item_group or "Configured Products",
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"description": description,
			"custom_source_configuration": cfg.name,
		}
	)
	item.insert()
	return item_code


def _compose_description(cfg, product):
	"""Human-readable decode — what sales sees on a Quotation line."""
	bits = [product.product_name]
	for row in cfg.options:
		if row.option_type == "Choice" and row.selected:
			bits.append(f"{row.option_label}: {row.choice_label or row.choice_code}")
		elif row.option_type == "Quantity":
			bits.append(f"{row.option_label}: {int(row.qty or 0)}")
	if flt(cfg.additional_cost):
		bits.append(f"Additional: {cfg.additional_description or 'see configuration'}")
	return " | ".join(bits)


# --------------------------------------------------------------------- BOM
def _aggregate_components(cfg):
	"""BOM rows aggregated by item code — ERPNext rejects duplicate rows.

	(The 2-pole terminal appears in the relay, contactor AND timer modules.)
	"""
	agg = {}
	for row in cfg.parts:
		code = (row.item_code or "").strip()
		qty = flt(row.qty)
		if not code or qty <= 0:
			continue
		if code in agg:
			agg[code]["qty"] += qty
		else:
			agg[code] = {"item_code": code, "qty": qty, "uom": row.uom or "Nos"}
	return sorted(agg.values(), key=lambda r: r["item_code"])


def _current_default_bom(item_code):
	return frappe.db.get_value(
		"BOM",
		{"item": item_code, "docstatus": 1, "is_active": 1, "is_default": 1},
		"name",
	)


def _bom_rows_match(bom_name, rows):
	existing = frappe.get_all(
		"BOM Item", filters={"parent": bom_name}, fields=["item_code", "qty"]
	)
	current = sorted((r.item_code, flt(r.qty)) for r in existing)
	wanted = sorted((r["item_code"], flt(r["qty"])) for r in rows)
	return current == wanted


def _ensure_bom(cfg, item_code):
	rows = _aggregate_components(cfg)
	if not rows:
		frappe.throw(_("No BOM rows — every part in the configuration has zero quantity."))

	existing = _current_default_bom(item_code)
	if existing and _bom_rows_match(existing, rows):
		# Same structure — just refresh rates per rm_cost_as_per.
		frappe.get_doc("BOM", existing).update_cost()
		return existing

	company = cfg.company or frappe.defaults.get_global_default("default_company")
	if not company:
		frappe.throw(_("Set a Company on the configuration (no default company found)."))

	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": item_code,
			"company": company,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"conversion_rate": 1,
			"quantity": 1,
			"uom": "Nos",
			"rm_cost_as_per": "Valuation Rate",
			"with_operations": 0,
			"is_active": 1,
			"is_default": 1,
			"items": rows,
		}
	)
	bom.insert()
	bom.submit()  # on_submit repoints Item.default_bom and unsets other defaults

	if existing:
		# Historical Work Orders keep their reference; the old version just
		# stops being offered (is_active is allow_on_submit in core).
		frappe.db.set_value("BOM", existing, "is_active", 0)

	zero_rated = [row.item_code for row in bom.items if flt(row.rate) <= 0]
	if zero_rated:
		frappe.msgprint(
			_(
				"These components resolved to a zero rate on the BOM (no valuation "
				"yet?): {0}"
			).format(", ".join(zero_rated)),
			indicator="orange",
		)
	return bom.name


# --------------------------------------------------------------- item price
def _upsert_selling_price(cfg, item_code):
	if not frappe.db.exists("Price List", SELLING_PRICE_LIST):
		frappe.msgprint(
			_("Price List {0} not found — selling price not recorded.").format(
				SELLING_PRICE_LIST
			),
			indicator="orange",
		)
		return None
	rate = flt(cfg.sell_price, 2)
	name = frappe.db.exists(
		"Item Price",
		{"item_code": item_code, "price_list": SELLING_PRICE_LIST, "uom": "Nos"},
	)
	if name:
		frappe.db.set_value("Item Price", name, "price_list_rate", rate)
		return name
	price = frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": SELLING_PRICE_LIST,
			"uom": "Nos",
			"price_list_rate": rate,
		}
	)
	price.insert()
	return price.name
