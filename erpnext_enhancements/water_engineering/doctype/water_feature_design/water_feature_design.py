# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Water Feature Design doctype.

A Water Feature Design is the submittable document that accumulates a fountain's
Phase-1 hydraulic design: basin geometry, water features, piping segments, and
the resulting pump + electrical selection. The single frappe<->engine bridge is
``recompute()``: it converts the child rows to a plain-dict input, runs the pure
``water_engineering.engine`` spine, and writes the headline rollups, the
per-row computed columns, and a ``calc_results`` audit trail back onto the doc.

The same engine backs the desk wizard (``api/water_design.py``) and the FAC MCP
tools, so chat, desk, and form all produce byte-identical math.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from erpnext_enhancements.water_engineering.api.water_design import nozzle_profile_params, pump_curves
from erpnext_enhancements.water_engineering.engine import (
	basin_volume,
	chemistry_targets,
	chlorinator_feed,
	component_loss,
	fitting_minor_loss,
	hazen_williams_loss,
	manning_drain_flow,
	nozzle_array_flow,
	nozzle_flow,
	pipe_velocity,
	run_spine,
	surge_basin_volume,
	velocity_status,
	weir_flow,
)
from erpnext_enhancements.water_engineering.engine.data.pipe_specs import get_pipe_spec
from erpnext_enhancements.water_engineering.engine.units import gallons_to_pounds


class WaterFeatureDesign(Document):
	def validate(self):
		self.recompute()
		self.completion_percent = compute_completion_percent(self)

	def before_submit(self):
		if not self.get("basins") and not self.get("features"):
			frappe.throw(
				_("Add at least one basin or feature before submitting the design."),
				title=_("Nothing to Calculate"),
			)

	# ---------------------------------------------------------------- bridge
	def recompute(self):
		"""Run the pure engine over the current child rows and write results back.

		Never raises out of validate on a calc error — a bad row records a
		warning instead of blocking the save (the audit trail and
		``next_inputs_needed`` surface the problem)."""
		try:
			out = run_spine(_engine_inputs(self))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Water Feature Design recompute")
			self.has_warnings = 1
			self.next_inputs_needed = _("Calculation error — see Error Log.")
			return

		self.total_basin_gallons = out.get("total_basin_gallons")
		self.required_circulation_gpm = out.get("required_circulation_gpm")
		self.design_flow_gpm = out.get("design_flow_gpm")
		self.computed_tdh_ft = out.get("tdh_ft")
		self.next_inputs_needed = "\n".join(out.get("next_inputs_needed") or [])
		self.has_warnings = 1 if out.get("warnings") else 0

		selected = out.get("selected_pump")
		self.selected_pump = selected if (selected and frappe.db.exists("Item", selected)) else None

		self._write_audit_trail(out.get("results") or [])
		self._compute_chemistry()
		self._compute_drainage()
		self._fill_basin_rows()
		self._fill_feature_rows()
		self._fill_segment_rows()
		self._mark_selected_pump_row()

	def _compute_chemistry(self):
		"""Phase-2 water treatment sized off the system (basin) volume; appends
		its envelopes to the audit trail written by _write_audit_trail."""
		volume = flt(self.total_basin_gallons)
		feed = chlorinator_feed(volume, flt(self.chem_chlorine_pct) or 10)
		targets = chemistry_targets(self.chem_water_type or "Outdoor")
		self.chlorinator_feed_gph = feed.value or 0
		self.chemistry_targets_summary = "; ".join(targets.steps)
		for r in (feed, targets):
			self.append(
				"calc_results",
				{
					"calc": r.calc,
					"value": _fmt(r.value),
					"unit": r.unit,
					"formula": r.formula,
					"steps": "\n".join(r.steps),
					"citations": ", ".join(r.citations),
					"warnings": "\n".join(r.warnings),
				},
			)

	def _write_audit_trail(self, results):
		self.set("calc_results", [])
		for r in results:
			self.append(
				"calc_results",
				{
					"calc": r.get("calc"),
					"value": _fmt(r.get("value")),
					"unit": r.get("unit"),
					"formula": r.get("formula"),
					"steps": "\n".join(r.get("steps") or []),
					"citations": ", ".join(r.get("citations") or []),
					"warnings": "\n".join(r.get("warnings") or []),
				},
			)

	def _compute_drainage(self):
		"""Phase-3 gravity drain capacity + surge-basin volume (both optional)."""
		envelopes = []
		if self.drain_nominal_size:
			r = manning_drain_flow(self.drain_nominal_size, flt(self.drain_slope_in_per_ft) or 0.25)
			self.drain_capacity_gpm = r.value or 0
			envelopes.append(r)
		else:
			self.drain_capacity_gpm = 0
		if flt(self.surge_basin_area_sf) > 0:
			r = surge_basin_volume(flt(self.surge_pool_area_sf), flt(self.surge_basin_area_sf))
			self.surge_basin_gallons = r.value or 0
			envelopes.append(r)
		else:
			self.surge_basin_gallons = 0
		for r in envelopes:
			self.append(
				"calc_results",
				{
					"calc": r.calc,
					"value": _fmt(r.value),
					"unit": r.unit,
					"formula": r.formula,
					"steps": "\n".join(r.steps),
					"citations": ", ".join(r.citations),
					"warnings": "\n".join(r.warnings),
				},
			)

	def _fill_basin_rows(self):
		for row in self.get("basins") or []:
			r = basin_volume(
				row.shape or "Rectangular",
				length_in=flt(row.length_in),
				width_in=flt(row.width_in),
				height_in=flt(row.height_in),
				diameter_in=flt(row.diameter_in),
			)
			row.volume_gal = r.value or 0
			row.weight_lb = gallons_to_pounds(r.value) if r.value else 0

	def _fill_feature_rows(self):
		for row in self.get("features") or []:
			ftype = (row.feature_type or "Weir").lower()
			if "weir" in ftype:
				r = weir_flow(flt(row.weir_length_ft), flt(row.head_in), cint(row.end_contractions) or 2)
			elif "array" in ftype:
				r = nozzle_array_flow(cint(row.nozzle_count), flt(row.gpm_each))
			else:
				params = nozzle_profile_params(row.nozzle_profile) if row.nozzle_profile else {}
				r = nozzle_flow(
					flt(row.supply_head_ft),
					cd=params.get("cd"),
					orifice_area_in2=params.get("orifice_area_in2"),
					orifice_diameter_in=params.get("orifice_diameter_in"),
					rated_gpm=params.get("rated_gpm"),
					rated_head_ft=params.get("rated_head_ft"),
					nozzle_profile=row.nozzle_profile or "",
				)
			row.flow_gpm = r.value or 0

	def _fill_segment_rows(self):
		default_material = self.pipe_material or "SCH40 PVC"
		hw_c = cint(self.hazen_williams_c) or 130
		for row in self.get("pipe_segments") or []:
			material = row.material or default_material
			spec = get_pipe_spec(material, row.nominal_size)
			if not spec:
				row.velocity_fps = 0
				row.velocity_status = ""
				row.head_loss_ft = 0
				continue
			id_in = spec["id_in"]
			flow = flt(row.flow_gpm)
			velocity = pipe_velocity(flow, id_in).value
			row.velocity_fps = velocity
			row.velocity_status = velocity_status(
				velocity, row.line_type or "Discharge",
				spec["max_suction_fps"], spec["max_discharge_fps"], spec["legal_fps"],
			)
			major = hazen_williams_loss(flow, flt(row.pipe_length_ft), id_in, hw_c).value
			minor = fitting_minor_loss(velocity, _loads(row.fittings_json)).value
			comp = component_loss(flow, _loads(row.components_json)).value
			row.head_loss_ft = major + minor + comp

	def _mark_selected_pump_row(self):
		for row in self.get("pumps") or []:
			row.is_selected = 1 if (self.selected_pump and row.pump_item == self.selected_pump) else 0


# ------------------------------------------------------------- pure helpers
# (no frappe dependency beyond the doc shape -> unit-testable with a stub doc)


def _loads(text):
	"""Parse a JSON list field; return [] on empty/garbage."""
	if not text:
		return []
	try:
		data = json.loads(text)
		return data if isinstance(data, list) else []
	except (ValueError, TypeError):
		return []


def _fmt(value):
	"""Render a calc value for the audit Data column."""
	if value is None:
		return ""
	if isinstance(value, float):
		return f"{value:.4f}".rstrip("0").rstrip(".")
	return str(value)


def _feature_dict(f):
	"""One feature row -> engine input dict, resolving the Nozzle Profile (Cd /
	orifice / rated GPM) for orifice features so the engine can compute flow."""
	row = {
		"feature_type": f.feature_type or "Weir",
		"weir_length_ft": flt(f.weir_length_ft),
		"head_in": flt(f.head_in),
		"contractions": cint(f.end_contractions) or 2,
		"nozzle_count": cint(f.nozzle_count),
		"gpm_each": flt(f.gpm_each),
		"nozzle_profile": f.nozzle_profile or "",
		"supply_head_ft": flt(f.supply_head_ft),
	}
	if f.nozzle_profile:
		row.update(nozzle_profile_params(f.nozzle_profile))
	return row


def _engine_inputs(doc):
	"""Build the pure-engine input dict from the doc's child rows."""
	default_material = doc.pipe_material or "SCH40 PVC"
	basins = [
		{
			"shape": b.shape or "Rectangular",
			"length_in": flt(b.length_in),
			"width_in": flt(b.width_in),
			"height_in": flt(b.height_in),
			"diameter_in": flt(b.diameter_in),
		}
		for b in doc.get("basins") or []
	]
	features = [_feature_dict(f) for f in doc.get("features") or []]
	segments = [
		{
			"label": s.segment_label,
			"flow_gpm": flt(s.flow_gpm),
			"nominal_size": s.nominal_size,
			"material": s.material or default_material,
			"length_ft": flt(s.pipe_length_ft),
			"line_type": s.line_type or "Discharge",
			"fittings": _loads(s.fittings_json),
			"components": _loads(s.components_json),
		}
		for s in doc.get("pipe_segments") or []
	]
	# Explicit pump rows win; otherwise auto-source from the ERPNext Items catalog
	# (item_group "Pumps") so a seeded catalog resolves the pump automatically.
	candidates = [
		{
			"item_code": p.pump_item,
			"part_number": p.part_number,
			"description": p.pump_description,
			"rated_gpm": flt(p.rated_gpm),
			"rated_tdh_ft": flt(p.rated_tdh_ft),
		}
		for p in doc.get("pumps") or []
	] or _catalog_pump_candidates()
	return {
		"basins": basins,
		"features": features,
		"pipe_segments": segments,
		"static_lift_ft": flt(doc.static_lift_ft),
		"turnovers_per_hr": flt(doc.turnover_per_hr) or 2,
		"hazen_williams_c": cint(doc.hazen_williams_c) or 130,
		"pump_candidates": candidates or None,
	}


def _catalog_pump_candidates():
	"""Pump candidates from the Items catalog (item_group 'Pumps') with the
	pump-spec custom fields. Empty if the fields/items aren't set up yet (the
	get_all raises on an unknown column before migrate creates them)."""
	try:
		items = frappe.get_all(
			"Item",
			filters={"item_group": "Pumps", "disabled": 0},
			fields=[
				"item_code", "item_name", "custom_rated_gpm", "custom_rated_tdh_ft",
				"custom_pump_hp", "custom_pump_phase", "custom_pump_voltage",
			],
		)
	except Exception:
		return []
	candidates = [
		{
			"item_code": it.get("item_code"),
			"description": it.get("item_name"),
			"rated_gpm": it.get("custom_rated_gpm"),
			"rated_tdh_ft": it.get("custom_rated_tdh_ft"),
			"hp": it.get("custom_pump_hp"),
			"phase": it.get("custom_pump_phase"),
			"voltage": it.get("custom_pump_voltage"),
		}
		for it in items
	]
	curves = pump_curves([c["item_code"] for c in candidates])
	for c in candidates:
		if curves.get(c["item_code"]):
			c["curve"] = curves[c["item_code"]]
	return candidates


def compute_completion_percent(doc):
	"""Rough 0-100 of how fleshed-out the design is: basin, features, piping,
	and a resolved pump are the four Phase-1 milestones."""
	done = sum(
		bool(x)
		for x in (
			doc.get("basins"),
			doc.get("features"),
			doc.get("pipe_segments"),
			doc.selected_pump or doc.get("pumps"),
		)
	)
	return round(done / 4 * 100, 1)
