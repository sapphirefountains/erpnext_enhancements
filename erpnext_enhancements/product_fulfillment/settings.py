# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reading Product Build Settings, and the facts about Items the rest of the module shares.

``get_settings`` loads the whole Single through ``frappe.get_cached_doc``. A Single nobody has saved
has no ``tabSingles`` rows, and only a whole-document load falls back to the JSON defaults;
``frappe.db.get_single_value`` would read the serial series as None and the test switch as 0. If
the DocType is not installed yet (the first migrate of this release runs patches before model
sync can be trusted) the defaults below stand in, so no hook ever raises for want of it.
"""

import frappe

from erpnext_enhancements.product_fulfillment import rules

SETTINGS = "Product Build Settings"

#: What a site without the DocType reads. Mirrors the JSON defaults.
FALLBACK = {
	"make_work_orders": 1,
	"build_warehouse": None,
	"wip_warehouse": None,
	"finished_warehouse": None,
	"serial_no_series": rules.DEFAULT_SERIAL_SERIES,
	"warranty_days": rules.DEFAULT_WARRANTY_DAYS,
	"require_test": 1,
	"test_template": "Pre-Shipment Functional Test - E-Stop",
}


#: Fields whose blank means "use the default", not "none". A cleared Pre-Shipment Test Template
#: stays blank on purpose (any template counts), and cleared warehouses stay blank.
_BLANK_MEANS_DEFAULT = ("make_work_orders", "require_test", "warranty_days")


def get_settings():
	"""Product Build Settings as a plain dict. Never raises.

	A saved Single hands back None for a field it has no row for, so the switches and the warranty
	fall back to their defaults when None; a serial series that ERPNext could not count with falls
	back to ``rules.DEFAULT_SERIAL_SERIES``.
	"""
	try:
		doc = frappe.get_cached_doc(SETTINGS)
	except Exception:
		return frappe._dict(FALLBACK)
	out = {key: doc.get(key) for key in FALLBACK}
	for key in _BLANK_MEANS_DEFAULT:
		if out[key] is None:
			out[key] = FALLBACK[key]
	if not rules.valid_serial_series(out["serial_no_series"]):
		out["serial_no_series"] = rules.DEFAULT_SERIAL_SERIES
	return frappe._dict(out)


def manufacturing_defaults():
	"""Manufacturing Settings' default WIP and finished-goods warehouses (both empty on prod)."""
	out = {}
	for field in ("default_wip_warehouse", "default_fg_warehouse"):
		try:
			out[field] = frappe.db.get_single_value("Manufacturing Settings", field)
		except Exception:
			out[field] = None
	return out


def has_column(doctype, column):
	"""``frappe.db.has_column`` that answers False instead of raising on a missing table."""
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def item_facts(item_codes):
	"""``{item_code: {is_stock_item, has_serial_no, source_configuration, default_bom}}``.

	``custom_source_configuration`` is the configurator's own mark, added in ``after_migrate``; a
	site without the column has no configured Items, so every answer is "not configured".
	"""
	codes = sorted({code for code in item_codes or () if code})
	if not codes:
		return {}
	source = "custom_source_configuration" if has_column("Item", "custom_source_configuration") else "NULL"
	rows = frappe.db.sql(
		f"""
		SELECT name, is_stock_item, has_serial_no, default_bom, {source} AS source_configuration
		FROM `tabItem`
		WHERE name IN %(codes)s
		""",
		{"codes": tuple(codes)},
		as_dict=True,
	)
	return {row.name: row for row in rows}


def needs_test(facts):
	"""``rules.needs_test`` on one row of ``item_facts``."""
	return bool(facts) and rules.needs_test(facts.get("is_stock_item"), facts.get("source_configuration"))
