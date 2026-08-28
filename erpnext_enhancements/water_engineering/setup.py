# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate setup for Water Engineering.

``create_pump_item_fields`` adds the pump-spec fields the engine's pump selector
reads (rated flow/head + nameplate) to Item, gated to the "Pumps" item group.
Idempotent, like the other ``setup`` field creators wired in hooks.py
``after_migrate``.

``ensure_pump_catalog`` is the ``after_migrate`` entry: it creates those fields
and seeds the "Pumps" Item Group + a starter catalog from DOC-0028 (Design Part
Numbers). It runs on every migrate — so Frappe Cloud (where ``bench execute``
isn't available) gets the catalog automatically on deploy — and is idempotent
(skips existing item codes, never overwrites) and guarded (a seed error only
logs, never breaks the deploy). ``seed_pump_catalog`` is the same thing callable
directly (bench console / FAC ``run_python_code``) if a manual run is ever
wanted. Each pump's rated flow is derived from the GPH in its DOC-0028
description (GPH / 60); the head ("max lift") is not in the source data, so it is
left blank and the selector matches on flow + flags a pump-curve check.
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
				{
					"fieldname": "custom_pump_cut_sheet",
					"label": "Cut Sheet",
					"fieldtype": "Attach",
					"insert_after": "custom_pump_fla_amps",
				},
				{
					"fieldname": "custom_pump_curve",
					"label": "Pump Curve",
					"fieldtype": "Table",
					"options": "Pump Curve Point",
					"insert_after": "custom_pump_cut_sheet",
					"description": "Flow/head points read off the manufacturer curve; the design selector interpolates the head at the design flow.",
				},
				{
					"fieldname": "custom_pump_curve_chart",
					"label": "Pump Curve Chart",
					"fieldtype": "HTML",
					"insert_after": "custom_pump_curve",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()


def _seed_pump_items():
	"""Create the Pumps item group + the DOC-0028 starter pump items. Idempotent
	(skips existing item codes; never overwrites). Returns a summary dict."""
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


def seed_pump_catalog():
	"""Create the pump-spec fields + the DOC-0028 starter catalog. Idempotent.
	Auto-run on migrate via ``ensure_pump_catalog`` (so Frappe Cloud gets it on
	deploy, no shell needed); also callable directly. Returns a summary dict."""
	create_pump_item_fields()
	return _seed_pump_items()


def ensure_pump_catalog():
	"""after_migrate entry: ensure the pump-spec Item fields and seed the starter
	catalog. The seed is guarded so a data hiccup can never break a deploy/migrate
	(it only logs) — the fields, which are schema, are created unguarded."""
	create_pump_item_fields()
	try:
		result = _seed_pump_items()
		if result.get("created"):
			frappe.logger().info(f"[water_engineering] seeded pumps: {result['created']}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Water Engineering pump catalog seed")


# --------------------------------------------------------------------------
# Aquatic-equipment catalog (filters, heaters, chem feed, controllers, skimmers,
# VGB drains, therapy jets, gauges, panel devices). Pumps keep their own fieldset
# (gated on item_group 'Pumps'); these are the rest of the health-dept equipment
# schedule, gated on a single ``custom_equipment_class`` driver so the schedule +
# electrical calcs can resolve each device's specs. Same idempotent + guarded
# after_migrate pattern as the pump catalog.
# --------------------------------------------------------------------------

EQUIPMENT_CLASS_OPTIONS = (
	"\nFilter\nHeater\nChemical Feed Pump\nChemical Controller\nSkimmer\n"
	"Suction Outlet (VGB)\nTherapy Jet\nReturn Inlet\nGauge / Meter\n"
	"Panel Electrical Device\nControl Hardware"
)
_ELEC_CLASSES = "['Heater','Chemical Feed Pump','Chemical Controller','Panel Electrical Device','Control Hardware']"


def create_equipment_item_fields():
	"""Aquatic-equipment spec fields on Item, gated by custom_equipment_class."""
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_aquatic_section",
					"label": "Aquatic Equipment",
					"fieldtype": "Section Break",
					"insert_after": "custom_pump_curve_chart",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_equipment_class",
					"label": "Equipment Class",
					"fieldtype": "Select",
					"options": EQUIPMENT_CLASS_OPTIONS,
					"insert_after": "custom_aquatic_section",
					"description": "Drives the equipment schedule + which spec fields apply.",
				},
				{
					"fieldname": "custom_model_no",
					"label": "Model / Part No.",
					"fieldtype": "Data",
					"insert_after": "custom_equipment_class",
					"depends_on": "eval:doc.custom_equipment_class",
				},
				{
					"fieldname": "custom_nsf_listing",
					"label": "NSF / VGB Listing",
					"fieldtype": "Data",
					"insert_after": "custom_model_no",
					"depends_on": "eval:doc.custom_equipment_class",
				},
				{
					"fieldname": "custom_iapmo_listing",
					"label": "IAPMO / UPC Listing",
					"fieldtype": "Data",
					"insert_after": "custom_nsf_listing",
					"depends_on": "eval:doc.custom_equipment_class",
				},
				{
					"fieldname": "custom_spec_cut_sheet",
					"label": "Cut Sheet",
					"fieldtype": "Attach",
					"insert_after": "custom_iapmo_listing",
					"depends_on": "eval:doc.custom_equipment_class",
				},
				{
					"fieldname": "custom_equipment_elec_cb",
					"fieldtype": "Column Break",
					"insert_after": "custom_spec_cut_sheet",
				},
				{
					"fieldname": "custom_elec_voltage",
					"label": "Voltage",
					"fieldtype": "Data",
					"insert_after": "custom_equipment_elec_cb",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_elec_phase",
					"label": "Phase",
					"fieldtype": "Select",
					"options": "\n1\n3",
					"insert_after": "custom_elec_voltage",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_elec_hz",
					"label": "Hz",
					"fieldtype": "Select",
					"options": "\n60\n50",
					"insert_after": "custom_elec_phase",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_elec_fla_amps",
					"label": "FLA (A)",
					"fieldtype": "Float",
					"insert_after": "custom_elec_hz",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_elec_watts",
					"label": "Watts",
					"fieldtype": "Float",
					"insert_after": "custom_elec_fla_amps",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_elec_hp",
					"label": "HP",
					"fieldtype": "Float",
					"insert_after": "custom_elec_watts",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_requires_gfci",
					"label": "Requires GFCI",
					"fieldtype": "Check",
					"insert_after": "custom_elec_hp",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_nec680_bond",
					"label": "Bond/Ground per NEC 680",
					"fieldtype": "Check",
					"insert_after": "custom_requires_gfci",
					"depends_on": f"eval:{_ELEC_CLASSES}.includes(doc.custom_equipment_class)",
				},
				{
					"fieldname": "custom_filter_area_sqft",
					"label": "Filter Area (sq ft)",
					"fieldtype": "Float",
					"insert_after": "custom_nec680_bond",
					"depends_on": "eval:doc.custom_equipment_class=='Filter'",
				},
				{
					"fieldname": "custom_filter_max_flow_gpm",
					"label": "Filter Max Flow (GPM)",
					"fieldtype": "Float",
					"insert_after": "custom_filter_area_sqft",
					"depends_on": "eval:doc.custom_equipment_class=='Filter'",
				},
				{
					"fieldname": "custom_filter_media",
					"label": "Filter Media",
					"fieldtype": "Select",
					"options": "\nCartridge\nSand\nHigh-Rate Sand\nDE",
					"insert_after": "custom_filter_max_flow_gpm",
					"depends_on": "eval:doc.custom_equipment_class=='Filter'",
				},
				{
					"fieldname": "custom_heater_fuel",
					"label": "Heater Fuel",
					"fieldtype": "Select",
					"options": "\nElectric\nGas",
					"insert_after": "custom_filter_media",
					"depends_on": "eval:doc.custom_equipment_class=='Heater'",
				},
				{
					"fieldname": "custom_heater_kw",
					"label": "Heater kW",
					"fieldtype": "Float",
					"insert_after": "custom_heater_fuel",
					"depends_on": "eval:doc.custom_equipment_class=='Heater'",
				},
				{
					"fieldname": "custom_heater_btu_hr",
					"label": "Heater BTU/hr",
					"fieldtype": "Float",
					"insert_after": "custom_heater_kw",
					"depends_on": "eval:doc.custom_equipment_class=='Heater'",
				},
				{
					"fieldname": "custom_vgb_open_area_sqin",
					"label": "VGB Open Area (sq in)",
					"fieldtype": "Float",
					"insert_after": "custom_heater_btu_hr",
					"depends_on": "eval:doc.custom_equipment_class=='Suction Outlet (VGB)'",
				},
				{
					"fieldname": "custom_vgb_max_flow_gpm",
					"label": "VGB Max Flow (GPM)",
					"fieldtype": "Float",
					"insert_after": "custom_vgb_open_area_sqin",
					"depends_on": "eval:doc.custom_equipment_class=='Suction Outlet (VGB)'",
				},
				{
					"fieldname": "custom_vgb_rated",
					"label": "VGB Compliant",
					"fieldtype": "Check",
					"insert_after": "custom_vgb_max_flow_gpm",
					"depends_on": "eval:doc.custom_equipment_class=='Suction Outlet (VGB)'",
				},
				{
					"fieldname": "custom_jet_flow_gpm",
					"label": "Jet Flow (GPM)",
					"fieldtype": "Float",
					"insert_after": "custom_vgb_rated",
					"depends_on": "eval:doc.custom_equipment_class=='Therapy Jet'",
				},
				{
					"fieldname": "custom_jet_pressure_psi",
					"label": "Jet Pressure (PSI)",
					"fieldtype": "Float",
					"insert_after": "custom_jet_flow_gpm",
					"depends_on": "eval:doc.custom_equipment_class=='Therapy Jet'",
				},
				{
					"fieldname": "custom_skimmer_max_flow_gpm",
					"label": "Skimmer Max Flow (GPM)",
					"fieldtype": "Float",
					"insert_after": "custom_jet_pressure_psi",
					"depends_on": "eval:doc.custom_equipment_class=='Skimmer'",
				},
				{
					"fieldname": "custom_meter_kind",
					"label": "Meter Kind",
					"fieldtype": "Select",
					"options": "\nThermometer\nFlowmeter\nVacuum\nPressure",
					"insert_after": "custom_skimmer_max_flow_gpm",
					"depends_on": "eval:doc.custom_equipment_class=='Gauge / Meter'",
				},
				{
					"fieldname": "custom_meter_range",
					"label": "Meter Range",
					"fieldtype": "Data",
					"insert_after": "custom_meter_kind",
					"depends_on": "eval:doc.custom_equipment_class=='Gauge / Meter'",
				},
				{
					"fieldname": "custom_feed_rate_gpd",
					"label": "Feed Rate (GPD)",
					"fieldtype": "Float",
					"insert_after": "custom_meter_range",
					"depends_on": "eval:doc.custom_equipment_class=='Chemical Feed Pump'",
				},
				{
					"fieldname": "custom_controller_channels",
					"label": "Controller Channels",
					"fieldtype": "Data",
					"insert_after": "custom_feed_rate_gpd",
					"depends_on": "eval:doc.custom_equipment_class=='Chemical Controller'",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()


AQUATIC_EQUIPMENT_GROUPS = [
	"Filters", "Heaters", "Chemical Feed Pumps", "Chemical Controllers",
	"Skimmers", "Suction Outlets", "Therapy Jets", "Return Inlets",
	"Gauges & Meters", "Panel Electrical Devices", "Control Hardware",
]

# Fika Reflexology Spa reference equipment (Salt Lake County Health Dept, 2023).
EQUIPMENT_CATALOG = [
	{
		"item_code": "FILTER-PENTAIR-CC150", "item_name": "Cartridge Filter, Pentair CC-150",
		"item_group": "Filters", "equipment_class": "Filter",
		"specs": {"custom_model_no": "CC-150", "custom_nsf_listing": "NSF/ANSI 50",
				  "custom_filter_area_sqft": 150, "custom_filter_max_flow_gpm": 56,
				  "custom_filter_media": "Cartridge"},
	},
	{
		"item_code": "HEATER-COATES-11KW", "item_name": "Electric Heater, Coates 11kW",
		"item_group": "Heaters", "equipment_class": "Heater",
		"specs": {"custom_model_no": "11KW 240V 1PH", "custom_heater_fuel": "Electric",
				  "custom_heater_kw": 11, "custom_elec_voltage": "240", "custom_elec_phase": "1",
				  "custom_elec_watts": 11000, "custom_elec_fla_amps": 45.83, "custom_nec680_bond": 1},
	},
	{
		"item_code": "HEATER-TRIANGLE-FM80", "item_name": "Gas Heater, Triangle FM-80",
		"item_group": "Heaters", "equipment_class": "Heater",
		"specs": {"custom_model_no": "FM-80", "custom_heater_fuel": "Gas", "custom_heater_btu_hr": 80000},
	},
	{
		"item_code": "CHEMPUMP-STENNER-45M1", "item_name": "Chemical Feed Pump, Stenner 45M1",
		"item_group": "Chemical Feed Pumps", "equipment_class": "Chemical Feed Pump",
		"specs": {"custom_model_no": "45M1", "custom_elec_voltage": "120", "custom_elec_phase": "1",
				  "custom_elec_hz": "60", "custom_elec_fla_amps": 1.7, "custom_elec_hp": 0.0333,
				  "custom_requires_gfci": 1},
	},
	{
		"item_code": "CHEMCTRL-IPS-M820", "item_name": "Chemical Controller, IPS M820",
		"item_group": "Chemical Controllers", "equipment_class": "Chemical Controller",
		"specs": {"custom_model_no": "M820", "custom_elec_voltage": "120", "custom_elec_fla_amps": 5,
				  "custom_controller_channels": "ORP / pH"},
	},
	{
		"item_code": "SKIMMER-HAYWARD-1084FVE", "item_name": "Skimmer, Hayward 1084FVE",
		"item_group": "Skimmers", "equipment_class": "Skimmer",
		"specs": {"custom_model_no": "1084FVE", "custom_skimmer_max_flow_gpm": 75},
	},
	{
		"item_code": "DRAIN-WATERWAY-640358", "item_name": "Main Drain (VGB), Waterway 640-358xV",
		"item_group": "Suction Outlets", "equipment_class": "Suction Outlet (VGB)",
		"specs": {"custom_model_no": "640-358xV", "custom_nsf_listing": "VGB / ANSI-APSP-16",
				  "custom_vgb_open_area_sqin": 9.02, "custom_vgb_rated": 1},
	},
	{
		"item_code": "JET-WATERWAY-210-4120", "item_name": "Therapy Jet, Waterway 210-4120",
		"item_group": "Therapy Jets", "equipment_class": "Therapy Jet",
		"specs": {"custom_model_no": "210-4120", "custom_jet_flow_gpm": 8, "custom_jet_pressure_psi": 15},
	},
	{
		"item_code": "METER-BLUEWHITE-F30200PR", "item_name": "Flowmeter, Blue-White F30200PR",
		"item_group": "Gauges & Meters", "equipment_class": "Gauge / Meter",
		"specs": {"custom_model_no": "F30200PR", "custom_meter_kind": "Flowmeter", "custom_meter_range": "15-70 GPM"},
	},
	{
		"item_code": "THERMO-PASCO-1450", "item_name": "In-line Thermometer, Pasco 1450",
		"item_group": "Gauges & Meters", "equipment_class": "Gauge / Meter",
		"specs": {"custom_model_no": "1450", "custom_meter_kind": "Thermometer", "custom_meter_range": "40-240 F"},
	},
	{
		"item_code": "GAUGE-PASCO-1463A", "item_name": "Vacuum/Pressure Gauge, Pasco 1463A",
		"item_group": "Gauges & Meters", "equipment_class": "Gauge / Meter",
		"specs": {"custom_model_no": "1463A", "custom_meter_kind": "Vacuum", "custom_meter_range": "compound"},
	},
	{
		"item_code": "ELEC-GFCI-120", "item_name": "GFCI Receptacle, 120VAC",
		"item_group": "Panel Electrical Devices", "equipment_class": "Panel Electrical Device",
		"specs": {"custom_elec_voltage": "120", "custom_requires_gfci": 1, "custom_nec680_bond": 1},
	},
]


def _seed_equipment_items():
	"""Create the Aquatic Equipment item-group tree + the Fika reference items.
	Idempotent (skips existing item codes; never overwrites)."""
	if not frappe.db.exists("Item Group", "Aquatic Equipment"):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": "Aquatic Equipment",
				"parent_item_group": "All Item Groups",
				"is_group": 1,
			}
		).insert(ignore_permissions=True)
	for group in AQUATIC_EQUIPMENT_GROUPS:
		if not frappe.db.exists("Item Group", group):
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": group,
					"parent_item_group": "Aquatic Equipment",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

	created, skipped = [], []
	for eq in EQUIPMENT_CATALOG:
		if frappe.db.exists("Item", eq["item_code"]):
			skipped.append(eq["item_code"])
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": eq["item_code"],
				"item_name": eq["item_name"],
				"item_group": eq["item_group"],
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"custom_equipment_class": eq["equipment_class"],
				"description": f"{eq['item_name']} — Fika Reflexology Spa reference equipment.",
				**eq.get("specs", {}),
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)

	frappe.db.commit()
	return {"created": created, "skipped": skipped}


def seed_equipment_catalog():
	"""Create the equipment-spec fields + seed the Fika reference catalog.
	Idempotent; auto-run on migrate via ``ensure_equipment_catalog`` and also
	callable directly (bench console / FAC)."""
	create_equipment_item_fields()
	return _seed_equipment_items()


def ensure_equipment_catalog():
	"""after_migrate entry: ensure the equipment-spec Item fields and seed the
	Fika reference catalog. Fields (schema) are created unguarded; the seed is
	guarded so a data hiccup can never break a deploy/migrate."""
	create_equipment_item_fields()
	try:
		result = _seed_equipment_items()
		if result.get("created"):
			frappe.logger().info(f"[water_engineering] seeded aquatic equipment: {result['created']}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Water Engineering equipment catalog seed")


# Generic starter Nozzle Profiles (Cd + orifice area). These are the same generic
# estimates the legacy assistant used — clearly flagged so engineers replace them
# with manufacturer cut-sheet data. Orifice nozzle flow needs sourced Cd/orifice,
# so a starter catalog lets orifice features compute immediately.
NOZZLE_PROFILE_SEED = [
	{"profile_name": "Generic Smooth Bore", "nozzle_type": "Smooth Bore", "cd": 0.97, "area": 0.20},
	{"profile_name": "Generic Aerating", "nozzle_type": "Aerating", "cd": 0.70, "area": 0.55},
	{"profile_name": "Generic Geyser", "nozzle_type": "Geyser", "cd": 0.62, "area": 0.90},
	{"profile_name": "Generic Spray", "nozzle_type": "Spray", "cd": 0.85, "area": 0.30},
	{"profile_name": "Generic Cascade", "nozzle_type": "Cascade", "cd": 0.80, "area": 0.40},
]


def seed_nozzle_profiles():
	"""Create the generic starter Nozzle Profiles. Idempotent (skips existing)."""
	created, skipped = [], []
	for prof in NOZZLE_PROFILE_SEED:
		if frappe.db.exists("Nozzle Profile", prof["profile_name"]):
			skipped.append(prof["profile_name"])
			continue
		frappe.get_doc(
			{
				"doctype": "Nozzle Profile",
				"profile_name": prof["profile_name"],
				"nozzle_type": prof["nozzle_type"],
				"is_generic_estimate": 1,
				"discharge_coefficient": prof["cd"],
				"orifice_area_in2": prof["area"],
				"notes": "Generic estimate — replace Cd/orifice with manufacturer cut-sheet data.",
			}
		).insert(ignore_permissions=True)
		created.append(prof["profile_name"])
	frappe.db.commit()
	return {"created": created, "skipped": skipped}


def ensure_nozzle_profiles():
	"""after_migrate entry: seed the generic starter Nozzle Profiles (guarded)."""
	try:
		result = seed_nozzle_profiles()
		if result.get("created"):
			frappe.logger().info(f"[water_engineering] seeded nozzle profiles: {result['created']}")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Water Engineering nozzle profile seed")


# Workspace triage cards over the denormalized issue counters recompute()
# writes onto every design (see issues.py) — "which designs need attention"
# without opening a single one.
WATER_NUMBER_CARDS = [
	{
		"name": "Water Designs with Blockers",
		"label": "Designs with Blockers",
		"document_type": "Water Feature Design",
		"filters_json": '[["Water Feature Design","blocker_count",">",0],["Water Feature Design","docstatus","<",2]]',
		"color": "#CB2929",
	},
	{
		"name": "Water Designs Ready to Issue",
		"label": "Designs Ready to Issue",
		"document_type": "Water Feature Design",
		"filters_json": '[["Water Feature Design","issue_ready","=",1],["Water Feature Design","status","!=","Issued"],["Water Feature Design","docstatus","=",0]]',
		"color": "#29CD42",
	},
]


def ensure_water_number_cards():
	"""after_migrate entry: upsert the Water Engineering workspace Number Cards
	(idempotent + guarded, like the other water_engineering setup entries)."""
	try:
		for spec in WATER_NUMBER_CARDS:
			values = {
				"label": spec["label"],
				"type": "Document Type",
				"document_type": spec["document_type"],
				"function": "Count",
				"filters_json": spec["filters_json"],
				"is_public": 1,
				"show_percentage_stats": 0,
				"color": spec["color"],
			}
			if frappe.db.exists("Number Card", spec["name"]):
				card = frappe.get_doc("Number Card", spec["name"])
				card.update(values)
				card.save(ignore_permissions=True)
			else:
				card = frappe.new_doc("Number Card")
				card.update(values)
				# insert(set_name=...) survives autoname; assigning card.name
				# before save() does NOT (set_new_name wipes it -> the workspace
				# block would point at a card that doesn't exist).
				card.insert(ignore_permissions=True, set_name=spec["name"])
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Water Engineering number cards")
