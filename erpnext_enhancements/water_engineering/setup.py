# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate setup for Water Engineering.

``create_pump_item_fields`` adds the pump-spec fields the engine's pump selector
reads (rated flow/head + nameplate) to Item, gated to the "Pumps" item group.
Idempotent, like the other ``setup`` field creators wired in hooks.py
``after_migrate``.

``seed_pump_catalog`` creates the "Pumps" Item Group and a starter pump catalog
from DOC-0028 (Design Part Numbers). It is NOT auto-run — an operator runs it
once after deploy (``bench --site SITE execute
erpnext_enhancements.water_engineering.setup.seed_pump_catalog``) so the design
spine can resolve a pump. Each pump's rated flow is derived from the GPH in its
DOC-0028 description (GPH / 60); the head ("max lift") is not in the source data,
so it is left blank and the selector matches on flow + flags a pump-curve check.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# DOC-0028 "Part Numbers", Category == Pump. Flow is the GPH in the description.
PUMP_CATALOG = [
	{"item_code": "500014", "item_name": "Pump, Aquasurge 2000", "gph": 2000, "vendor": "Aquascape", "vendor_no": "AQU-98125"},
	{"item_code": "500035", "item_name": "Pump, 66 GPH, Submersible", "gph": 66, "vendor": "Fountain Tech", "vendor_no": "FT-70-I"},
	{"item_code": "500141", "item_name": "Pump, 5811 GPH, Submersible", "gph": 5811, "vendor": "Atlantic", "vendor_no": "A-21"},
	{"item_code": "500144", "item_name": "Pump, 1200 GPH, Submersible", "gph": 1200, "vendor": "Little Giant", "vendor_no": "505025"},
	{"item_code": "500202", "item_name": "Pump, 4000 GPH, Submersible, Torpedo", "gph": 4000, "vendor": "", "vendor_no": "T4000"},
]


def create_pump_item_fields():
	"""Pump-spec fields on Item, shown only for the Pumps item group."""
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_pump_section",
					"label": "Pump Specifications",
					"fieldtype": "Section Break",
					"insert_after": "stock_uom",
					"depends_on": "eval:doc.item_group=='Pumps'",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_rated_gpm",
					"label": "Rated Flow (GPM)",
					"fieldtype": "Float",
					"insert_after": "custom_pump_section",
					"description": "Max flow used by the Water Feature Design pump selector.",
				},
				{
					"fieldname": "custom_rated_tdh_ft",
					"label": "Rated Head (ft TDH)",
					"fieldtype": "Float",
					"insert_after": "custom_rated_gpm",
					"description": "Max head from the pump curve. Blank = selector matches on flow only.",
				},
				{
					"fieldname": "custom_pump_hp",
					"label": "HP",
					"fieldtype": "Float",
					"insert_after": "custom_rated_tdh_ft",
				},
				{
					"fieldname": "custom_pump_phase",
					"label": "Phase",
					"fieldtype": "Select",
					"options": "\n1\n3",
					"insert_after": "custom_pump_hp",
				},
				{
					"fieldname": "custom_pump_voltage",
					"label": "Voltage",
					"fieldtype": "Data",
					"insert_after": "custom_pump_phase",
				},
				{
					"fieldname": "custom_pump_fla_amps",
					"label": "FLA (A)",
					"fieldtype": "Float",
					"insert_after": "custom_pump_voltage",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()


def seed_pump_catalog():
	"""Create the Pumps item group + the DOC-0028 starter pump items. Idempotent;
	run once per site after deploy. Returns a summary dict."""
	create_pump_item_fields()

	if not frappe.db.exists("Item Group", "Pumps"):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": "Pumps",
				"parent_item_group": "All Item Groups",
				"is_group": 0,
			}
		).insert(ignore_permissions=True)

	created, skipped = [], []
	for pump in PUMP_CATALOG:
		if frappe.db.exists("Item", pump["item_code"]):
			skipped.append(pump["item_code"])
			continue
		vendor = f" — {pump['vendor']} {pump['vendor_no']}".rstrip() if (pump["vendor"] or pump["vendor_no"]) else ""
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": pump["item_code"],
				"item_name": pump["item_name"],
				"item_group": "Pumps",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"description": f"{pump['item_name']}{vendor} (DOC-0028).",
				"custom_rated_gpm": round(pump["gph"] / 60.0, 2),
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)

	frappe.db.commit()
	return {"created": created, "skipped": skipped}
