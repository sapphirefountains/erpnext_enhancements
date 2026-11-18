# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted desk endpoints for the Product Configurator.

Thin adapters over the doctype controller and ``erp_integration`` — every
endpoint gates on doctype permission; the ERPNext-mutating ones additionally
gate on the ``product_configurator_enabled`` master switch (previews and
option loading always work: the switch guards mutations, not math — the
"data at rest is ungated" convention).

Quoting (v1.825.0): ``start_configuration`` and ``create_quotation`` are the two
calls a sale makes, from the Opportunity's "Quote <product>" action and the
configuration's "Create Quotation" button today, and from the phone quick-quote
page and the Triton tool later. They are thin adapters over ``quoting.py``,
which says what each is careful about. ``start_configuration`` makes a
configuration (data at rest, so it is not gated by the switch);
``create_quotation`` makes ERPNext records, so it is.

Refusals read in plain words: what the person can't do and who can fix it, not
"Not permitted (write Product Configuration)".
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.feature_flags import throw_if_product_configurator_disabled

CONFIG_DOCTYPE = "Product Configuration"


def _require(ptype, doctype=CONFIG_DOCTYPE):
	if frappe.has_permission(doctype, ptype):
		return
	if doctype == CONFIG_DOCTYPE and ptype != "read":
		from erpnext_enhancements.product_configurator.quoting import product_engineer_refusal

		message = product_engineer_refusal()
	elif doctype == "Configurable Product" and ptype != "read":
		message = _(
			"Changing a product definition needs the Product Engineer role, and your user doesn't have "
			"it yet. Ask a System Manager to add it to your user."
		)
	else:
		message = _("You can't see {0} records yet. Ask a System Manager for the Product Engineer role.").format(
			_(doctype)
		)
	frappe.throw(message, frappe.PermissionError, title=_("Product Engineer role needed"))


def _parse(payload):
	if isinstance(payload, str):
		return json.loads(payload or "{}")
	return payload or {}


@frappe.whitelist()
def get_product_options(product):
	"""Default option rows for a product — populates a new configuration form."""
	_require("read")
	doc = frappe.new_doc(CONFIG_DOCTYPE)
	doc.product = product
	doc.sync_option_rows()
	return {
		"rows": [
			{
				"option_key": row.option_key,
				"option_type": row.option_type,
				"option_label": row.option_label,
				"choice_code": row.choice_code,
				"choice_label": row.choice_label,
				"module_key": row.module_key,
				"selected": row.selected,
				"qty": row.qty,
			}
			for row in doc.options
		]
	}


@frappe.whitelist()
def preview_configuration(payload):
	"""Re-price a configuration in memory (no save) for the live form preview.

	Runs the exact controller ``recompute()`` the save path runs, so the
	preview can never disagree with the saved result. Selection errors come
	back as ``{"error": ...}`` so the form can show them inline while the
	user is mid-edit.
	"""
	_require("read")
	data = _parse(payload)
	doc = frappe.new_doc(CONFIG_DOCTYPE)
	doc.product = data.get("product")
	doc.additional_description = data.get("additional_description")
	doc.additional_cost = data.get("additional_cost") or 0
	for row in data.get("options") or []:
		doc.append(
			"options",
			{
				"option_key": row.get("option_key"),
				"option_type": row.get("option_type"),
				"choice_code": row.get("choice_code") or "",
				"selected": 1 if row.get("selected") else 0,
				"qty": row.get("qty") or 0,
			},
		)
	doc.sync_option_rows()  # fills labels/module keys, keeps the posted values
	try:
		doc.recompute()
	except frappe.ValidationError as e:
		return {"error": str(e)}
	return {
		"part_number": doc.part_number,
		"sell_price": doc.sell_price,
		"sell_price_exact": doc.sell_price_exact,
		"total_cost": doc.total_cost,
		"lines": [
			{
				"module_label": ln.module_label,
				"qty": ln.qty,
				"unit_price": ln.unit_price,
				"line_price": ln.line_price,
			}
			for ln in doc.price_lines
		],
		"warnings": (doc.warnings_text or "").split("\n") if doc.warnings_text else [],
	}


@frappe.whitelist()
def generate_erpnext_records(configuration):
	"""Create/refresh the Item, default BOM and selling Item Price."""
	throw_if_product_configurator_disabled()
	_require("write")
	from erpnext_enhancements.product_configurator import erp_integration

	return erp_integration.generate(configuration)


@frappe.whitelist(methods=["POST"])
def start_configuration(opportunity=None, product=None, customer=None, selections=None):
	"""Start a Product Configuration for a deal (or an Account); returns ``{configuration, ...}``.

	``product`` may be left out while only one Configurable Product is enabled. ``selections`` is
	``{option_key: choice_code | qty}`` (a dict or its JSON) for callers with no form.
	"""
	from erpnext_enhancements.product_configurator import quoting

	return quoting.start_configuration(
		opportunity=opportunity, product=product, customer=customer, selections=selections
	)


@frappe.whitelist(methods=["POST"])
def create_quotation(configuration):
	"""Make (or return) the configuration's draft Quotation, generating its Item, BOM and price first."""
	from erpnext_enhancements.product_configurator import quoting

	return quoting.create_quotation(configuration)


@frappe.whitelist()
def ensure_component_items(product):
	"""Create missing Suppliers and component Items for a product definition."""
	throw_if_product_configurator_disabled()
	_require("write", "Configurable Product")
	from erpnext_enhancements.product_configurator import erp_integration

	return erp_integration.ensure_component_items(product)
