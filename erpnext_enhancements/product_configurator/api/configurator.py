# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted desk endpoints for the Product Configurator.

Thin adapters over the doctype controller and ``erp_integration`` — every
endpoint gates on doctype permission; the ERPNext-mutating ones additionally
gate on the ``product_configurator_enabled`` master switch (previews and
option loading always work: the switch guards mutations, not math — the
"data at rest is ungated" convention).
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.feature_flags import throw_if_product_configurator_disabled

CONFIG_DOCTYPE = "Product Configuration"


def _require(ptype, doctype=CONFIG_DOCTYPE):
	if not frappe.has_permission(doctype, ptype):
		frappe.throw(
			_("Not permitted ({0} {1}).").format(ptype, doctype), frappe.PermissionError
		)


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


@frappe.whitelist()
def ensure_component_items(product):
	"""Create missing Suppliers and component Items for a product definition."""
	throw_if_product_configurator_disabled()
	_require("write", "Configurable Product")
	from erpnext_enhancements.product_configurator import erp_integration

	return erp_integration.ensure_component_items(product)
