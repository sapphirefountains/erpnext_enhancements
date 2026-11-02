# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Convert every schema-1 Water Feature Design into a schema-2 ``model_json`` (v1.768.0).

Water Feature Design no longer declares its nine child tables (basins, features, tiers, pipe
segments, pumps, electrical loads, venue fixtures, equipment, calc results): the design model
in ``model_json`` replaced them (``docs/fountain-designer/PLAN.md`` 3.2). The rows themselves
stayed in their tables until phase H dropped them (v1.771.0), so this reads them
straight from there with ``get_all`` (``frappe.get_doc`` no longer loads them), builds the old
engine input with ``legacy_inputs`` (below), and runs ``engine.model.migrate``.

It writes only with ``db.set_value`` and ``update_modified=False``: no validate, no derive, no
child row touched. The old rollups stay as they were until the design is next saved, which
re-derives it. The v1 statuses map onto the new ones (Inputs Gathered -> Draft, Calculated ->
Derived), so the Select stays valid on the next save.

**It deletes nothing and never raises**: a patch that raises aborts ``bench migrate``, which on
this repo is the deploy. A design that cannot be converted is logged and gets a line in its
``notes`` saying so; the model-2 controller then reports it as one blocker instead of deriving
it. Safe twice: a design whose ``model_json`` is already schema 2 is skipped.
"""

import json

import frappe
from frappe.utils import cint, flt

DOCTYPE = "Water Feature Design"

# Parent field -> child DocType, as the v1 JSON declared them.
CHILD_TABLES = {
	"basins": "Water Feature Basin",
	"features": "Water Feature Nozzle",
	"tiers": "Water Feature Tier",
	"pipe_segments": "Water Feature Pipe Segment",
	"pumps": "Water Feature Pump",
}

STATUS_MAP = {"Inputs Gathered": "Draft", "Calculated": "Derived"}


def execute():
	try:
		if not frappe.db.exists("DocType", DOCTYPE) or not frappe.db.has_column(DOCTYPE, "model_json"):
			return
		names = frappe.get_all(DOCTYPE, pluck="name")
	except Exception:
		frappe.log_error(title="Water Feature Design model conversion", message=frappe.get_traceback())
		return
	for name in names:
		try:
			convert(name)
		except Exception:
			frappe.log_error(
				title=f"Water Feature Design model conversion: {name}", message=frappe.get_traceback()
			)
			_mark(name, "it raised; see the Error Log")


def _rows(name, field, child):
	"""The design's stored rows of one v1 child table. A table that does not exist has no rows; any
	other failure raises, so ``execute`` logs it and marks the design not converted rather than
	stamping it "converted from 0 rows" with an empty model."""
	if not frappe.db.table_exists(child):
		return []
	return frappe.get_all(
		child,
		filters={"parent": name, "parenttype": DOCTYPE, "parentfield": field},
		fields=["*"],
		order_by="idx asc",
	)


def convert(name):
	from erpnext_enhancements.water_engineering.doctype.water_feature_design.water_feature_design import (
		stored_model,
	)
	from erpnext_enhancements.water_engineering.engine import model as model_engine

	parent = (
		frappe.db.sql(f"select * from `tab{DOCTYPE}` where name = %s", (name,), as_dict=True) or [None]
	)[0]
	if not parent:
		return
	updates = {}
	status = parent.get("status")
	if status in STATUS_MAP:
		updates["status"] = STATUS_MAP[status]

	existing = stored_model(parent.get("model_json"))
	if existing and not model_engine.is_legacy(existing):
		if updates:
			frappe.db.set_value(DOCTYPE, name, updates, update_modified=False)
		return

	rows = {field: _rows(name, field, child) for field, child in CHILD_TABLES.items()}
	model = model_engine.migrate(legacy_inputs(parent, rows))
	model["mode"] = "full"
	problems = [p.to_dict() for p in model_engine.validate(model) if p.severity == "error"]
	features = model.get("features") or []
	updates.update(
		{
			"model_json": json.dumps(model),
			"mode": "Full",
			"family": features[0].get("family") if features else "",
		}
	)
	note = (
		f"Converted to the Fountain Designer model on {frappe.utils.today()} from "
		f"{sum(len(v) for v in rows.values())} schema-1 rows; nothing was deleted."
	)
	if problems:
		note += " The converted model has problems to fix: " + "; ".join(p["message"] for p in problems[:5])
	updates["notes"] = ((parent.get("notes") or "") + "\n" + note).strip()
	frappe.db.set_value(DOCTYPE, name, updates, update_modified=False)


def _mark(name, why):
	"""Note why a design was not converted, and still move its v1 status onto the new options: a
	status the Select no longer offers would make every later save of the record fail."""
	try:
		row = frappe.db.get_value(DOCTYPE, name, ["notes", "status"], as_dict=True) or {}
		line = f"Not converted to the Fountain Designer model on {frappe.utils.today()}: {why}."
		updates = {"notes": ((row.get("notes") or "") + "\n" + line).strip()}
		if row.get("status") in STATUS_MAP:
			updates["status"] = STATUS_MAP[row["status"]]
		frappe.db.set_value(DOCTYPE, name, updates, update_modified=False)
	except Exception:
		pass


def _json_list(text):
	"""A JSON list field parsed; [] on empty or garbage."""
	try:
		data = json.loads(text or "[]")
	except (TypeError, ValueError):
		return []
	return data if isinstance(data, list) else []


def legacy_inputs(doc, rows):
	"""The schema-1 engine input (``model.migrate``'s first shape) from a record's parent fields
	and its child rows, ``rows = {table: [row dicts]}``, as read straight from the child tables:
	the parent no longer declares them, so this patch reads them with ``get_all``. It lived on the
	controller until v1.771.0, when phase H retired the child DocTypes and left this patch the only
	reader of the schema-1 rows."""

	def val(key, default=None):
		v = doc.get(key) if hasattr(doc, "get") else getattr(doc, key, None)
		return default if v in (None, "") else v

	default_material = val("pipe_material", "SCH40 PVC")
	tiers = [
		{
			"diameter_in": flt(t.get("diameter_in")),
			"rim_height_in": flt(t.get("rim_height_in")),
			"spill_gpm_per_ft": flt(t.get("spill_gpm_per_ft")) or 0.5,
		}
		for t in rows.get("tiers") or []
	]
	features = []
	for f in rows.get("features") or []:
		row = {
			"feature_type": f.get("feature_type") or "Weir",
			"feature_label": f.get("feature_label"),
			"weir_length_ft": flt(f.get("weir_length_ft")),
			"head_in": flt(f.get("head_in")),
			"contractions": cint(f.get("end_contractions")) or 2,
			"nozzle_count": cint(f.get("nozzle_count")),
			"gpm_each": flt(f.get("gpm_each")),
			"nozzle_profile": f.get("nozzle_profile") or "",
			"supply_head_ft": flt(f.get("supply_head_ft")),
		}
		if "tier" in (row["feature_type"] or "").lower() or "cascade" in (row["feature_type"] or "").lower():
			row["tiers"] = tiers
		features.append(row)
	return {
		"basins": [
			{
				"basin_label": b.get("basin_label"),
				"shape": b.get("shape") or "Rectangular",
				"length_in": flt(b.get("length_in")),
				"width_in": flt(b.get("width_in")),
				"height_in": flt(b.get("height_in")),
				"diameter_in": flt(b.get("diameter_in")),
				"surface_area_sf": flt(b.get("surface_area_sf")),
				"volume_gal_override": flt(b.get("volume_gal_override")),
			}
			for b in rows.get("basins") or []
		],
		"features": features,
		"pipe_segments": [
			{
				"label": s.get("segment_label"),
				"flow_gpm": flt(s.get("flow_gpm")),
				"nominal_size": s.get("nominal_size"),
				"material": s.get("material") or default_material,
				"length_ft": flt(s.get("pipe_length_ft")),
				"line_type": s.get("line_type") or "Discharge",
				"fittings": _json_list(s.get("fittings_json")),
				"components": _json_list(s.get("components_json")),
			}
			for s in rows.get("pipe_segments") or []
		],
		"pumps": [{"pump_item": p.get("pump_item")} for p in rows.get("pumps") or [] if p.get("pump_item")],
		"static_lift_ft": flt(val("static_lift_ft", 0)),
		"turnovers_per_hr": flt(val("turnover_per_hr", 0)) or 2,
		"hazen_williams_c": cint(val("hazen_williams_c", 0)) or 130,
		"pipe_material": default_material,
		"venue_type": val("venue_type", "") or "",
		"governing_code": val("governing_code", "") or "",
	}
