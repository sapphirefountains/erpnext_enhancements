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

from erpnext_enhancements.water_engineering import issues as design_issues
from erpnext_enhancements.water_engineering.api.water_design import nozzle_profile_params, pump_curves
from erpnext_enhancements.water_engineering.engine import (
	basin_volume,
	chemistry_targets,
	chlorinator_feed,
	component_loss,
	feature_flow_category,
	fitting_minor_loss,
	hazen_williams_loss,
	manning_drain_flow,
	nozzle_array_flow,
	nozzle_flow,
	pipe_pressure_check,
	pipe_velocity,
	run_spine,
	surge_basin_volume,
	tiered_fountain_flow,
	velocity_status,
	weir_flow,
)
from erpnext_enhancements.water_engineering.engine.constants import FT_PER_PSI
from erpnext_enhancements.water_engineering.engine.data.pipe_specs import get_pipe_spec
from erpnext_enhancements.water_engineering.engine.units import gallons_to_pounds


class WaterFeatureDesign(Document):
	def validate(self):
		self.recompute()
		self.completion_percent = compute_completion_percent(self)
		self._drop_stale_acks()
		self._enforce_status_gates()

	def before_submit(self):
		if not self.get("basins") and not self.get("features"):
			frappe.throw(
				_("Add at least one basin or feature before submitting the design."),
				title=_("Nothing to Calculate"),
			)
		issues = self._live_issues()
		blockers = [i for i in issues if i.get("severity") == "blocker"]
		if blockers:
			frappe.throw(
				_("Resolve these blocking issues before submitting:")
				+ "<br>"
				+ "<br>".join(f"• {frappe.utils.escape_html(i.get('title') or '')}" for i in blockers),
				title=_("Blocking Issues"),
			)
		unacked = design_issues.unacknowledged_warnings(self, issues)
		if unacked:
			frappe.throw(
				_("Acknowledge each open warning in Design Health (or resolve it) before submitting:")
				+ "<br>"
				+ "<br>".join(f"• {frappe.utils.escape_html(i.get('title') or '')}" for i in unacked),
				title=_("Unacknowledged Warnings"),
			)
		# Advisories / still-needed inputs stay non-blocking — say so and proceed.
		if (self.next_inputs_needed or "").strip():
			frappe.msgprint(
				_("Submitting with open items: {0}").format(
					self.next_inputs_needed.replace("\n", "; ")
				),
				title=_("Design Not Fully Resolved"),
				indicator="orange",
			)

	# --------------------------------------------------------------- gating
	def _live_issues(self):
		"""The issues recompute just produced (parsed once, cached per validate)."""
		if getattr(self, "_issues_cache", None) is None:
			try:
				self._issues_cache = json.loads(self.design_issues_json or "[]")
			except ValueError:
				self._issues_cache = []
		return self._issues_cache

	def _drop_stale_acks(self):
		"""A stale acknowledgement must never grandfather a new problem: keep only
		acks whose issue key still matches a live issue."""
		live = {i.get("key") for i in self._live_issues()}
		kept = [a for a in (self.get("issue_acks") or []) if a.issue_key in live]
		if len(kept) != len(self.get("issue_acks") or []):
			self.set("issue_acks", kept)

	def _enforce_status_gates(self):
		"""Reviewed/Issued are earned states: no blockers may exist, and Issued
		additionally requires the package readiness gate + every warning
		acknowledged. (Submission re-checks in before_submit.)

		The gate fires only on the status TRANSITION — a doc already sitting in
		Reviewed/Issued (e.g. saved before these gates existed, or one that
		gained a finding from external state) must stay saveable, and the
		acknowledge endpoint's own save must not trip the gate it remedies.
		before_submit re-checks everything regardless of transition."""
		if self.status not in ("Reviewed", "Issued"):
			return
		before = self.get_doc_before_save()
		if before and (before.status or "") == (self.status or ""):
			return
		issues = self._live_issues()
		blockers = [i for i in issues if i.get("severity") == "blocker"]
		if blockers:
			frappe.throw(
				_("Cannot set status {0} with blocking issues open:").format(_(self.status))
				+ "<br>"
				+ "<br>".join(f"• {frappe.utils.escape_html(i.get('title') or '')}" for i in blockers),
				title=_("Blocking Issues"),
			)
		if self.status == "Issued":
			unacked = design_issues.unacknowledged_warnings(self, issues)
			if unacked:
				frappe.throw(
					_("Acknowledge each open warning in Design Health before issuing:")
					+ "<br>"
					+ "<br>".join(
						f"• {frappe.utils.escape_html(i.get('title') or '')}" for i in unacked
					),
					title=_("Unacknowledged Warnings"),
				)
			if not self.issue_ready:
				missing = []
				try:
					readiness = json.loads(self.readiness_json or "{}")
				except ValueError:
					readiness = {}
				for section in readiness.get("sections") or []:
					for m in section.get("missing") or []:
						missing.append(m.get("label") or "")
				frappe.throw(
					_("The design package is not complete enough to issue:")
					+ "<br>"
					+ "<br>".join(f"• {frappe.utils.escape_html(m)}" for m in missing if m),
					title=_("Package Not Ready"),
				)

	# ---------------------------------------------------------------- bridge
	def recompute(self):
		"""Run the pure engine over the current child rows and write results back.

		Never raises out of validate on a calc error — a bad row records a
		warning instead of blocking the save (the audit trail and
		``next_inputs_needed`` surface the problem)."""
		self._issues_cache = None
		try:
			out = run_spine(_engine_inputs(self))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Water Feature Design recompute")
			self.next_inputs_needed = _("Calculation error — see Error Log.")
			self._apply_issue_state(extra=[design_issues.calc_error_issue()])
			return

		self.total_basin_gallons = out.get("total_basin_gallons")
		self.required_circulation_gpm = out.get("required_circulation_gpm")
		self.design_flow_gpm = out.get("design_flow_gpm")
		self.computed_tdh_ft = out.get("tdh_ft")
		self.next_inputs_needed = "\n".join(out.get("next_inputs_needed") or [])

		extra_issues = []
		selected = out.get("selected_pump")
		if selected and not frappe.db.exists("Item", selected):
			# Don't drop a recommendation silently — say why it vanished.
			self.next_inputs_needed = (
				(self.next_inputs_needed + "\n") if self.next_inputs_needed else ""
			) + _("Recommended pump {0} is not in the Item catalog.").format(selected)
			self.selected_pump = None
			extra_issues.append(design_issues._issue(
				"PUMP_NOT_IN_CATALOG", design_issues.WARNING,
				f"Recommended pump {selected} is not in the Item catalog",
				"pump", scope=str(selected),
				fix_hint="Create the pump Item (item_group 'Pumps') or pick a cataloged candidate.",
				calc="select_pump",
			))
		else:
			self.selected_pump = selected or None

		self._write_audit_trail(out.get("results") or [])
		self._compute_chemistry()
		self._compute_drainage()
		self._fill_basin_rows()
		self._fill_feature_rows()
		self._fill_segment_rows()
		self._fill_segment_pressure()
		self._mark_selected_pump_row()
		self._apply_issue_state(extra=extra_issues)

	def _apply_issue_state(self, extra=None):
		"""Derive + persist the typed issues, readiness, and the denormalized
		counters (list view / workspace cards / Triton read them without loading
		the doc). ``has_warnings`` now means "any blocker or warning issue" —
		deliberately wider than the old spine-warnings-only flag, so chemistry /
		drainage findings finally flip it (the SOURCE_DATA_AUDIT 'CYA never
		warns' gap)."""
		issues = design_issues.build_issues(self, extra=extra)
		readiness = design_issues.build_readiness(self, issues)
		counts = design_issues.summarize(issues)
		self.design_issues_json = json.dumps(issues)
		self.readiness_json = json.dumps(readiness)
		self.blocker_count = counts["blocker_count"]
		self.warning_count = counts["warning_count"]
		self.issue_summary = counts["summary"]
		self.issue_ready = 1 if readiness.get("issue_ready") else 0
		self.has_warnings = 1 if (counts["blocker_count"] or counts["warning_count"]) else 0
		self._issues_cache = issues

	def _fill_segment_pressure(self):
		"""Per-segment pressure-rating status (DOC-0049 Pipe Specs): the pump puts
		~TDH ft (= TDH/2.31 psi) on the discharge side; every discharge run's pipe
		must be rated for it. Surfaced as row fields so the grid (not just the
		audit trail) shows an under-rated pipe."""
		tdh = flt(self.computed_tdh_ft)
		system_psi = tdh / FT_PER_PSI if tdh > 0 else 0
		default_material = self.pipe_material or "SCH40 PVC"
		for row in self.get("pipe_segments") or []:
			is_discharge = (row.line_type or "Discharge").lower().startswith("dis")
			if not (system_psi and is_discharge and row.nominal_size):
				row.pressure_status = ""
				row.pressure_margin_psi = 0
				continue
			chk = pipe_pressure_check(row.material or default_material, row.nominal_size, system_psi)
			row.pressure_status = chk.status or ""
			row.pressure_margin_psi = flt(chk.value)

	def _compute_chemistry(self):
		"""Phase-2 water treatment sized off the system (basin) volume; appends
		its envelopes to the audit trail written by _write_audit_trail. The
		planned CYA / free-chlorine levels thread through so the DOC-0119
		CYA-coupled chlorine floor can actually warn."""
		volume = flt(self.total_basin_gallons)
		feed = chlorinator_feed(volume, flt(self.chem_chlorine_pct) or 10)
		targets = chemistry_targets(
			self.chem_water_type or "Outdoor",
			flt(self.chem_cya_ppm) or None,
			flt(self.chem_free_cl_ppm) or None,
		)
		self.chlorinator_feed_gph = feed.value or 0
		self.chemistry_targets_summary = "; ".join(targets.steps)
		for r in (feed, targets):
			self.append("calc_results", _calc_row(r))

	def _write_audit_trail(self, results):
		self.set("calc_results", [])
		for r in results:
			self.append("calc_results", _calc_row(r))

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
			self.append("calc_results", _calc_row(r))

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
		tier_rows = [{"diameter_in": flt(t.diameter_in)} for t in self.get("tiers") or []]
		tier_gpm = (flt(self.tiers[0].spill_gpm_per_ft) or 0.5) if self.get("tiers") else 0.5
		for row in self.get("features") or []:
			category = feature_flow_category(row.feature_type or "Weir")
			if category == "tiered":
				r = tiered_fountain_flow(tier_rows, tier_gpm)
			elif category == "weir":
				r = weir_flow(flt(row.weir_length_ft), flt(row.head_in), cint(row.end_contractions) or 2)
			elif category == "array":
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
			# A blank segment flow carries the full system (design) flow — keeps the
			# per-row velocity/head-loss honest instead of showing 0.
			flow = flt(row.flow_gpm) or flt(self.design_flow_gpm)
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


def _calc_row(r):
	"""One CalcResult (object or already-serialized dict) -> a Water Feature Calc
	Result child row. Captures the headline value plus the FULL math — formula,
	ordered steps, inputs with provenance, citations, warnings, and the status
	band — so the two Print Formats (results summary + calculation audit) can
	render the design's end values and the exact working behind them."""
	d = r.to_dict() if hasattr(r, "to_dict") else r
	inputs = d.get("inputs") or {}
	# Tab-delimited "name\tvalue\tunit\tsource (ref)" per input — the Calculation
	# Audit print format splits this to a table (the print Jinja sandbox can't
	# parse the JSON), while inputs_json keeps the exact structured data.
	inputs_text = "\n".join(
		"\t".join(
			[
				str(name),
				_fmt(meta.get("value")),
				str(meta.get("unit") or ""),
				str(meta.get("source") or "") + (f" — {meta.get('ref')}" if meta.get("ref") else ""),
			]
		)
		for name, meta in inputs.items()
	)
	return {
		"calc": d.get("calc"),
		"value": _fmt(d.get("value")),
		"unit": d.get("unit"),
		"status": d.get("status") or "",
		"formula": d.get("formula"),
		"steps": "\n".join(d.get("steps") or []),
		"inputs_text": inputs_text,
		"inputs_json": json.dumps(inputs),
		"citations": ", ".join(d.get("citations") or []),
		"warnings": "\n".join(d.get("warnings") or []),
	}


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
	tiers = [
		{
			"diameter_in": flt(t.diameter_in),
			"rim_height_in": flt(t.rim_height_in),
			"spill_gpm_per_ft": flt(t.spill_gpm_per_ft) or 0.5,
		}
		for t in doc.get("tiers") or []
	]
	features = []
	for f in doc.get("features") or []:
		fd = _feature_dict(f)
		if feature_flow_category(f.feature_type or "") == "tiered":
			fd["tiers"] = tiers
			fd["gpm_per_ft"] = tiers[0]["spill_gpm_per_ft"] if tiers else 0.5
		features.append(fd)
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
