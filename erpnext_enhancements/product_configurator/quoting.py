# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Quote a configured product: start a configuration from a deal, then make its draft Quotation.

The first E-Stop sales slice (v1.825.0; decision 2026-11-18, "the Sales Order is the record"). A sale
runs Opportunity -> Product Configuration -> draft Quotation -> ERPNext's own Make Sales Order. The
whitelisted adapters are in ``api/configurator.py``: the Opportunity's "Quote <product>" action and
the configuration's "Create Quotation" button call them today, and the phone quick-quote page and
the Triton tool are meant to call the same two later, which is why :func:`start_configuration`
also takes the options as data.

What this module is careful about:

* **No commits** (the module's atomicity contract, README). :func:`create_quotation` makes the Item,
  BOM and price (``erp_integration.generate``) and the Quotation in one request, so a Quotation that
  fails to save takes the Item and BOM with it. The attached Pricing Summary that follows runs in a
  savepoint: if it fails it rolls back only itself, says so in ``warnings``, and the quote stands.
* **A quote never decides that a deal is a product sale.** It writes nothing on the Opportunity.
  It returns ``suggest_product_sale``, and the form asks the person, who ticks Product Sale (No
  Project) or not (``crm_enhancements/product_sale.py`` says why a configured quote is not enough).
* **Refusals in plain words.** Two of the people who will quote did not yet hold the Product
  Engineer role on 2026-11-18, and frappe's own refusal ("No permission for Product Configuration")
  tells them nothing they can act on. Every refusal here says what is missing and who can fix it.
* **ERPNext's own mapping where it fits.** When the Quotation is for the deal's own party it starts
  from ``erpnext.crm...opportunity.make_quotation``, so the contact, the addresses and the default
  sales taxes come across the way Create > Quotation brings them; the configured line replaces the
  empty row that mapping adds. Otherwise it starts blank and gets the same default taxes.
* **One quote per configuration.** A configuration whose Quotation is still live (draft or
  submitted) returns that Quotation instead of making a second one, and says when the configuration
  has changed since. A canceled or deleted Quotation frees it.
* **The product rules are pure** (``engine/quote.py``): the line format, who the quote is for, the
  line's prices. The bench-free suite holds them.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from erpnext_enhancements.product_configurator import erp_integration
from erpnext_enhancements.product_configurator.engine.quote import (
	apply_selections,
	description_html,
	description_lines,
	missing_permission_message,
	quotation_header,
	quotation_line,
	quotation_party,
	uses_opportunity_mapping,
)

CONFIG_DOCTYPE = "Product Configuration"
PRICING_PRINT_FORMAT = "Product Configuration - Pricing Summary"

#: The option-row fields a caller's selections are applied to and written back from.
OPTION_FIELDS = (
	"option_key",
	"option_type",
	"option_label",
	"choice_code",
	"choice_label",
	"module_key",
	"selected",
	"qty",
)


# ------------------------------------------------------------------ refusals
def product_engineer_refusal():
	return _(
		"Quoting a product needs the Product Engineer role, and your user doesn't have it yet. "
		"Ask a System Manager to add it to your user."
	)


def require_configurator(ptype="create", doc=None):
	"""Refuse in plain words unless the person may ``ptype`` Product Configurations."""
	if not frappe.has_permission(CONFIG_DOCTYPE, ptype, doc=doc):
		frappe.throw(
			product_engineer_refusal(), frappe.PermissionError, title=_("Product Engineer role needed")
		)


def _require_quotation_create():
	if not frappe.has_permission("Quotation", "create"):
		frappe.throw(
			missing_permission_message([("Quotation", "create")]),
			frappe.PermissionError,
			title=_("Not allowed yet"),
		)


def _parse(value):
	if isinstance(value, str):
		value = value.strip()
		return json.loads(value) if value else {}
	return value or {}


# --------------------------------------------------------------- start
def enabled_products():
	"""``[{name, product_name}]`` for the Configurable Products that are switched on, by name."""
	return frappe.get_all(
		"Configurable Product",
		filters={"disabled": 0},
		fields=["name", "product_name"],
		order_by="name asc",
	)


def pick_product(product=None):
	"""The product to configure: the one asked for, or the only enabled one."""
	names = [row.name for row in enabled_products()]
	if product:
		if product not in names:
			frappe.throw(
				_(
					"{0} is not a product the configurator can quote: it is switched off or does not exist."
				).format(frappe.bold(product))
			)
		return product
	if len(names) == 1:
		return names[0]
	if not names:
		frappe.throw(_("No product is set up in the configurator yet."))
	frappe.throw(_("Pick which product to quote: {0}.").format(", ".join(names)))


def _opportunity_row(opportunity):
	if not opportunity:
		return frappe._dict()
	row = frappe.db.get_value(
		"Opportunity",
		opportunity,
		["name", "opportunity_from", "party_name", "company", "status"],
		as_dict=True,
	)
	return row or frappe._dict()


def start_configuration(opportunity=None, product=None, customer=None, selections=None):
	"""Make a Product Configuration for a deal (or a customer) and return its name.

	The party comes from the Opportunity: its Customer, or its Lead (a Prospect is quoted through the
	Opportunity itself). ``customer`` sets one directly, for a caller with no deal. ``selections``,
	``{option_key: choice_code | qty}``, picks the options at once; without it the configuration
	starts on the product's defaults (``PDT-0040-1-1-1-2-0`` for the E-Stop). Always a new
	configuration: a deal can quote two different setups.
	"""
	require_configurator("create")
	product = pick_product(product)

	opp = frappe._dict()
	if opportunity:
		if not frappe.db.exists("Opportunity", opportunity):
			frappe.throw(_("Opportunity {0} was not found.").format(frappe.bold(opportunity)))
		if not frappe.has_permission("Opportunity", "read", doc=opportunity):
			frappe.throw(
				_("You can't open Opportunity {0}, so you can't quote it.").format(frappe.bold(opportunity)),
				frappe.PermissionError,
			)
		opp = _opportunity_row(opportunity)
	if customer and not frappe.db.exists("Customer", customer):
		frappe.throw(_("Account {0} was not found.").format(frappe.bold(customer)))
	if not opportunity and not customer:
		frappe.throw(_("Pick the Opportunity or the Account to quote."))

	doc = frappe.new_doc(CONFIG_DOCTYPE)
	doc.product = product
	doc.opportunity = opportunity or None
	if customer:
		doc.customer = customer
	elif opp.get("opportunity_from") == "Customer":
		doc.customer = opp.party_name
	elif opp.get("opportunity_from") == "Lead":
		doc.lead = opp.party_name
	if opp.get("company"):
		doc.company = opp.company

	doc.sync_option_rows()
	wanted = _parse(selections)
	if wanted:
		rows = [{field: row.get(field) for field in OPTION_FIELDS} for row in doc.options]
		unknown = apply_selections(rows, wanted)
		if unknown:
			frappe.throw(
				_("These options are not on {0}, or not one of its choices: {1}.").format(
					frappe.bold(product), ", ".join(str(key) for key in unknown)
				)
			)
		doc.set("options", rows)

	doc.insert()
	return {
		"configuration": doc.name,
		"product": product,
		"part_number": doc.part_number,
		"sell_price": doc.sell_price,
		"warnings": [w for w in (doc.warnings_text or "").split("\n") if w],
	}


# --------------------------------------------------------------- quote
def _live_quotation(cfg):
	"""The configuration's Quotation while it is a draft or submitted, else ``None``."""
	name = cfg.get("quotation")
	if not name:
		return None
	docstatus = frappe.db.get_value("Quotation", name, "docstatus")
	if docstatus is None or int(docstatus) == 2:
		return None
	return name


def _still_matches(quotation, cfg):
	"""Whether ``quotation`` still quotes the configuration as it is now (part number and price)."""
	rows = frappe.get_all(
		"Quotation Item",
		filters={"parent": quotation, "parenttype": "Quotation"},
		fields=["item_code", "rate"],
	)
	price = flt(cfg.sell_price, 2)
	return any(row.item_code == cfg.part_number and flt(row.rate, 2) == price for row in rows)


def _default_taxes(quotation):
	"""The company's default sales-tax template, as ERPNext's Opportunity mapping adds it."""
	try:
		from erpnext.controllers.accounts_controller import _get_default_taxes_and_charges

		taxes = _get_default_taxes_and_charges("Sales Taxes and Charges Template", company=quotation.company)
	except Exception:
		return
	if taxes and taxes.get("taxes"):
		quotation.update(taxes)


def _new_quotation(cfg, opp, header, line):
	if uses_opportunity_mapping(
		cfg.opportunity,
		header["quotation_to"],
		header["party_name"],
		opp.get("opportunity_from"),
		opp.get("party_name"),
	):
		from erpnext.crm.doctype.opportunity.opportunity import make_quotation

		quotation = make_quotation(cfg.opportunity)
	else:
		quotation = frappe.new_doc("Quotation")
	quotation.update(header)
	# The mapping adds one empty row for a deal with no items of its own; the configured line is the quote.
	quotation.set("items", [line])
	if not quotation.get("taxes"):
		_default_taxes(quotation)
	quotation.run_method("set_missing_values")
	quotation.run_method("calculate_taxes_and_totals")
	return quotation


def create_quotation(configuration):
	"""Make (or return) the draft Quotation for a saved configuration.

	Generates the configured Item, its default BOM and its selling price first (``generate``), then a
	draft Quotation with the configured Item at the configured price, the one line format, the
	finished-goods warehouse, the Opportunity linked, and the configuration linked both ways. The
	Pricing Summary is attached as a private PDF. Returns ``{quotation, existing, item, bom,
	item_price, attachment, warnings}``.
	"""
	from erpnext_enhancements.feature_flags import throw_if_product_configurator_disabled

	throw_if_product_configurator_disabled()
	if not configuration or not frappe.db.exists(CONFIG_DOCTYPE, configuration):
		frappe.throw(_("Product Configuration {0} was not found.").format(frappe.bold(configuration or "")))
	require_configurator("write", doc=configuration)
	_require_quotation_create()

	cfg = frappe.get_doc(CONFIG_DOCTYPE, configuration)
	existing = _live_quotation(cfg)
	if existing:
		warnings = []
		if not _still_matches(existing, cfg):
			warnings.append(
				_(
					"Quotation {0} was made before this configuration last changed, so it doesn't match it. "
					"Delete or cancel that Quotation, then press Create Quotation again."
				).format(existing)
			)
		return {"quotation": existing, "existing": True, "warnings": warnings}

	opp = _opportunity_row(cfg.opportunity)
	quotation_to, party_name = quotation_party(
		cfg.customer, cfg.lead, opp.get("opportunity_from"), opp.get("party_name")
	)
	if not party_name:
		frappe.throw(
			_(
				"Set an Account or a Lead on the configuration, or link it to an Opportunity, before quoting it."
			),
			title=_("Who is this quote for?"),
		)

	generated = erp_integration.generate(cfg.name)
	warehouse = erp_integration.finished_goods_warehouse(cfg.company)
	lines = description_lines(
		cfg.part_number,
		[row.as_dict() for row in cfg.options],
		cfg.additional_description,
		flt(cfg.additional_cost),
	)
	line = quotation_line(generated["item"], cfg.sell_price, description_html(lines), cfg.name, warehouse)
	price_list = (
		erp_integration.SELLING_PRICE_LIST
		if frappe.db.exists("Price List", erp_integration.SELLING_PRICE_LIST)
		else None
	)
	header = quotation_header(
		quotation_to,
		party_name,
		cfg.company,
		opportunity=cfg.opportunity,
		price_list=price_list,
		transaction_date=nowdate(),
	)
	quotation = _new_quotation(cfg, opp, header, line)
	quotation.insert()
	frappe.db.set_value(CONFIG_DOCTYPE, cfg.name, "quotation", quotation.name, update_modified=False)

	warnings = []
	if not warehouse:
		warnings.append(
			_(
				"No finished-goods warehouse was found, so the quote line has none and its Sales Order "
				"will ask for one. Set Manufacturing Settings > Default Finished Goods Warehouse."
			)
		)
	attachment = _attach_pricing_summary(cfg.name, quotation.name, warnings)
	# Whether to ask the person if the deal is a product sale (no project). Asked, never set: a
	# configured quote on a deal says nothing about the rest of it (crm_enhancements/product_sale.py).
	suggest = False
	if cfg.opportunity:
		from erpnext_enhancements.crm_enhancements.product_sale import deal_state

		suggest = deal_state(cfg.opportunity)["suggested"]

	return {
		"quotation": quotation.name,
		"existing": False,
		"item": generated["item"],
		"bom": generated["bom"],
		"item_price": generated["item_price"],
		"attachment": attachment,
		"opportunity": cfg.opportunity,
		"suggest_product_sale": suggest,
		"warnings": warnings,
	}


# --------------------------------------------------------------- extras
def _attach_pricing_summary(configuration, quotation, warnings):
	"""The configuration's Pricing Summary as a private PDF on the Quotation; its URL, or ``None``.

	Prices and the parts list, no costs or markup (setup_print_formats.PRICING_HTML). Private, so it
	goes to a customer only when somebody ticks it in the email composer.
	"""
	frappe.db.savepoint("pc_quote_attach")
	try:
		with erp_integration.muted_messages():
			pdf = frappe.get_print(
				CONFIG_DOCTYPE, configuration, print_format=PRICING_PRINT_FORMAT, as_pdf=True
			)
			if not pdf:
				raise ValueError("the print came back empty")
			safe = str(configuration).replace("/", "-")
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"{safe}-pricing-summary.pdf",
					"content": pdf,
					"is_private": 1,
					"attached_to_doctype": "Quotation",
					"attached_to_name": quotation,
				}
			).insert(ignore_permissions=True)
		return file_doc.file_url
	except Exception:
		frappe.db.rollback(save_point="pc_quote_attach")
		frappe.log_error(title="Product quote: Pricing Summary not attached", message=frappe.get_traceback())
		warnings.append(
			_(
				"The Pricing Summary could not be attached. Print it from the configuration's View menu "
				"and attach it to the Quotation by hand."
			)
		)
		return None
