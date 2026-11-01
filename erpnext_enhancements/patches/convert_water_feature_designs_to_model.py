# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Convert every schema-1 Water Feature Design into a schema-2 ``model_json`` (v1.768.0).

Water Feature Design no longer declares its nine child tables (basins, features, tiers, pipe
segments, pumps, electrical loads, venue fixtures, equipment, calc results): the design model
in ``model_json`` replaced them (``docs/fountain-designer/PLAN.md`` 3.2). The rows themselves
are still in their tables, because the child DocTypes stay until phase H, so this reads them
straight from there with ``get_all`` (``frappe.get_doc`` no longer loads them), builds the old
engine input with the controller's ``legacy_inputs``, and runs ``engine.model.migrate``.

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
		legacy_inputs,
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
