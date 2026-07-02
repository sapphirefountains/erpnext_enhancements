// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Modeling UX for the Water Feature Design form:
//   - LIVE preview: as you edit basins / features / piping / inputs, the design
//     is recomputed in memory server-side (no save) and the rollups, per-row
//     velocity/flow/head-loss, completion, warnings, and a schematic dashboard
//     all update — so modeling is responsive instead of save-and-wait.
//   - Quick-start templates pre-fill a common fountain type — one per water
//     feature type we build (weir family, jet/array family, and tiered cascade).
// The authoritative recompute still runs on save (validate); this preview calls
// the SAME engine path, so what you see live equals what gets saved.
//
// Each template's `feature_type` matches the Water Feature Nozzle select exactly,
// so the engine routes it to the right flow calc (see feature_flow_category).
// Segment `flow_gpm` is left blank on purpose: a blank segment carries the full
// design flow (see the controller's _fill_segment_rows), so the piping sizes to
// the computed circulation without hand-entering it.

const WFD_TEMPLATES = {
	// --- Weir family (water spilling over a crest; Francis formula) -----------
	"Weir basin": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 4 },
		basins: [{ basin_label: "Catch basin", shape: "Rectangular", length_in: 120, width_in: 60, height_in: 18 }],
		features: [{ feature_label: "Weir", feature_type: "Weir", weir_length_ft: 6, head_in: 0.25, end_contractions: 2 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 50, line_type: "Discharge" },
		],
	},
	"Spilling weir (scupper)": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 5 },
		basins: [{ basin_label: "Catch basin", shape: "Rectangular", length_in: 144, width_in: 48, height_in: 18 }],
		features: [{ feature_label: "Scupper", feature_type: "Spilling Weir", weir_length_ft: 8, head_in: 0.5, end_contractions: 0 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 45, line_type: "Discharge" },
		],
	},
	"Vanishing edge (weir wall)": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 8 },
		basins: [{ basin_label: "Catch trough", shape: "Rectangular", length_in: 240, width_in: 18, height_in: 12 }],
		features: [{ feature_label: "Vanishing edge", feature_type: "Weir", weir_length_ft: 20, head_in: 0.25, end_contractions: 0 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '4"', material: "SCH40 PVC", pipe_length_ft: 60, line_type: "Discharge" },
		],
	},
	"Waterwall (sheet)": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 12 },
		basins: [{ basin_label: "Catch trough", shape: "Rectangular", length_in: 120, width_in: 24, height_in: 14 }],
		features: [{ feature_label: "Water wall", feature_type: "Waterwall", weir_length_ft: 8, head_in: 0.25, end_contractions: 0 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 40, line_type: "Discharge" },
		],
	},
	// --- Jet / array family (discrete streams) -------------------------------
	"Nozzle-array pool": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 6 },
		basins: [{ basin_label: "Pool", shape: "Cylindrical", diameter_in: 120, height_in: 24 }],
		features: [{ feature_label: "Jet ring", feature_type: "Nozzle Array", nozzle_count: 6, gpm_each: 8 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 40, line_type: "Discharge" },
		],
	},
	"Orifice nozzle jet": {
		// Orifice flow needs a Nozzle Profile (Cd / orifice size) from the catalog —
		// pick one on the feature row; the supply head is pre-filled (~10 psi).
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 10 },
		basins: [{ basin_label: "Pool", shape: "Cylindrical", diameter_in: 96, height_in: 24 }],
		features: [{ feature_label: "Center jet — pick a Nozzle Profile", feature_type: "Orifice Nozzle", supply_head_ft: 23 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 35, line_type: "Discharge" },
		],
	},
	"Splash pad": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 8 },
		basins: [{ basin_label: "Collection tank", shape: "Rectangular", length_in: 120, width_in: 120, height_in: 12 }],
		features: [{ feature_label: "Ground jets", feature_type: "Splash Pad", nozzle_count: 12, gpm_each: 5 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 50, line_type: "Discharge" },
		],
	},
	"Rain curtain": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 11 },
		basins: [{ basin_label: "Catch trough", shape: "Rectangular", length_in: 144, width_in: 18, height_in: 14 }],
		features: [{ feature_label: "Rain bar", feature_type: "Rain Curtain", nozzle_count: 48, gpm_each: 0.5 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 45, line_type: "Discharge" },
		],
	},
	// --- Tiered cascade (sized from the Tiers table, not a feature row) -------
	"Tiered fountain (cascade)": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 7 },
		basins: [{ basin_label: "Base pool", shape: "Cylindrical", diameter_in: 96, height_in: 18 }],
		features: [{ feature_label: "Cascade", feature_type: "Tiered Fountain" }],
		tiers: [
			{ tier_label: "Top bowl", diameter_in: 24, rim_height_in: 48, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Middle bowl", diameter_in: 40, rim_height_in: 30, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Bottom bowl", diameter_in: 60, rim_height_in: 14, spill_gpm_per_ft: 0.5 },
		],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 30, line_type: "Discharge" },
		],
	},
	// --- Named composite types (each maps to one of the feature flow calcs) ---
	"Reflecting pool": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 4 },
		basins: [{ basin_label: "Reflecting basin", shape: "Rectangular", length_in: 240, width_in: 120, height_in: 12 }],
		features: [{ feature_label: "Aerating bubblers", feature_type: "Nozzle Array", nozzle_count: 4, gpm_each: 3 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 50, line_type: "Discharge" },
		],
	},
	"Bubbler / boil": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 5 },
		basins: [{ basin_label: "Pool", shape: "Cylindrical", diameter_in: 96, height_in: 30 }],
		features: [{ feature_label: "Boil jets", feature_type: "Nozzle Array", nozzle_count: 5, gpm_each: 18 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 40, line_type: "Discharge" },
		],
	},
	"Geyser / foam jet": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 20 },
		basins: [{ basin_label: "Catch pool", shape: "Cylindrical", diameter_in: 144, height_in: 36 }],
		features: [{ feature_label: "Foam jet / geyser", feature_type: "Nozzle Array", nozzle_count: 1, gpm_each: 80 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '4"', material: "SCH40 PVC", pipe_length_ft: 45, line_type: "Discharge" },
		],
	},
	"Laminar clear-stream": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 8 },
		basins: [{ basin_label: "Pool", shape: "Rectangular", length_in: 120, width_in: 60, height_in: 18 }],
		features: [{ feature_label: "Laminar jets", feature_type: "Nozzle Array", nozzle_count: 4, gpm_each: 12 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 40, line_type: "Discharge" },
		],
	},
	"Interactive plaza jets": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 9 },
		basins: [{ basin_label: "Collection tank", shape: "Rectangular", length_in: 180, width_in: 180, height_in: 12 }],
		features: [{ feature_label: "Deck jets", feature_type: "Splash Pad", nozzle_count: 20, gpm_each: 6 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '4"', material: "SCH40 PVC", pipe_length_ft: 55, line_type: "Discharge" },
		],
	},
	"Wall spout / mask": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 7 },
		basins: [{ basin_label: "Catch trough", shape: "Rectangular", length_in: 96, width_in: 30, height_in: 16 }],
		features: [{ feature_label: "Wall spout", feature_type: "Spilling Weir", weir_length_ft: 2, head_in: 0.75, end_contractions: 0 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 35, line_type: "Discharge" },
		],
	},
	"Pond / naturalistic": {
		fields: { turnover_per_hr: 1, pipe_material: "SCH40 PVC", static_lift_ft: 4 },
		basins: [{ basin_label: "Pond", shape: "Rectangular", length_in: 360, width_in: 240, height_in: 36 }],
		features: [{ feature_label: "Aerator", feature_type: "Nozzle Array", nozzle_count: 2, gpm_each: 10 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '4"', material: "SCH40 PVC", pipe_length_ft: 60, line_type: "Discharge" },
		],
	},
	"Pondless urn": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 6 },
		basins: [{ basin_label: "Hidden sump", shape: "Rectangular", length_in: 48, width_in: 48, height_in: 24 }],
		features: [{ feature_label: "Urn bubbler", feature_type: "Nozzle Array", nozzle_count: 1, gpm_each: 12 }],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '2"', material: "SCH40 PVC", pipe_length_ft: 25, line_type: "Discharge" },
		],
	},
	"Grand cascade (multi-tier)": {
		fields: { turnover_per_hr: 2, pipe_material: "SCH40 PVC", static_lift_ft: 9 },
		basins: [{ basin_label: "Base pool", shape: "Cylindrical", diameter_in: 144, height_in: 24 }],
		features: [{ feature_label: "Grand cascade", feature_type: "Tiered Fountain" }],
		tiers: [
			{ tier_label: "Tier 1 (top)", diameter_in: 24, rim_height_in: 60, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Tier 2", diameter_in: 36, rim_height_in: 46, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Tier 3", diameter_in: 52, rim_height_in: 32, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Tier 4", diameter_in: 70, rim_height_in: 18, spill_gpm_per_ft: 0.5 },
			{ tier_label: "Tier 5 (base)", diameter_in: 90, rim_height_in: 8, spill_gpm_per_ft: 0.5 },
		],
		pipe_segments: [
			{ segment_label: "Pump discharge", nominal_size: '3"', material: "SCH40 PVC", pipe_length_ft: 35, line_type: "Discharge" },
		],
	},
};

// The fountain_type Select options on the doctype MUST stay in sync with these
// keys (the field switches a design to the matching template).
const WFD_TEMPLATE_NAMES = Object.keys(WFD_TEMPLATES);

// Model tables a template may define / replace (tiers included so the Tiered
// Fountain template lands its cascade rows).
const WFD_TEMPLATE_TABLES = ["basins", "features", "pipe_segments", "tiers"];

const WFD_PREVIEW_FIELDS = [
	"turnover_per_hr", "hazen_williams_c", "pipe_material", "static_lift_ft",
	"chem_water_type", "chem_chlorine_pct", "chem_cya_ppm", "chem_free_cl_ppm",
	"drain_nominal_size", "drain_slope_in_per_ft",
	"surge_pool_area_sf", "surge_basin_area_sf",
];
const WFD_TABLES = ["basins", "features", "pipe_segments", "pumps", "electrical_loads", "tiers"];

frappe.ui.form.on("Water Feature Design", {
	refresh(frm) {
		frm.add_custom_button(__("Open Wizard"), () => frappe.set_route("water-engineering-wizard"));
		if (!frm.is_new()) {
			frm.add_custom_button(__("Recalculate"), () => frm.save());
		}

		// Blockers get a headline above the form — visible from every tab.
		if (!frm.is_new() && cint(frm.doc.blocker_count) > 0) {
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline_alert(
				__("{0} blocking issue(s) — this design cannot be Reviewed, Issued, or submitted. See Design Health.", [
					cint(frm.doc.blocker_count),
				]),
				"red"
			);
		}
		WFD_TEMPLATE_NAMES.forEach((name) => {
			frm.add_custom_button(__(name), () => apply_template(frm, name), __("New from Template"));
		});

		// Remember the current Fountain Type so a cancelled switch can revert it.
		frm._wfd_ft = frm.doc.fountain_type;

		// Re-run the live preview whenever a grid changes (cell edit / add / remove).
		WFD_TABLES.forEach((t) => {
			const grid = frm.fields_dict[t] && frm.fields_dict[t].grid;
			if (grid && grid.wrapper) {
				grid.wrapper.off("change.wfdprev").on("change.wfdprev", () => schedule_preview(frm));
			}
		});
		wfd_sync_loss_summaries(frm);
		schedule_preview(frm);
	},

	// Switching Fountain Type loads that type's starter template (replacing the
	// model rows). Skip when WE set the field from inside apply_template.
	fountain_type(frm) {
		if (frm._wfd_template_applying) return;
		const name = frm.doc.fountain_type;
		if (name && WFD_TEMPLATES[name]) {
			apply_template(frm, name, true);
		} else {
			frm._wfd_ft = name; // blank / unmapped: just remember it
		}
	},
});

// Re-preview when a global / treatment / drainage input changes.
const _input_triggers = {};
WFD_PREVIEW_FIELDS.forEach((f) => {
	_input_triggers[f] = (frm) => schedule_preview(frm);
});
frappe.ui.form.on("Water Feature Design", _input_triggers);

// Pipe-segment fittings/components are picked from a friendly dialog (dropdown +
// quantity), not hand-typed JSON. The buttons live on each segment row; the
// dropdown options come straight from the engine's K-factor / coefficient tables
// (get_loss_catalog), so the desk choices can't drift from the math.
frappe.ui.form.on("Water Feature Pipe Segment", {
	edit_fittings(frm, cdt, cdn) {
		wfd_edit_losses(frm, cdt, cdn, "fittings");
	},
	edit_components(frm, cdt, cdn) {
		wfd_edit_losses(frm, cdt, cdn, "components");
	},
});

let _wfd_timer = null;
function schedule_preview(frm) {
	if (frm._wfd_applying) return;
	clearTimeout(_wfd_timer);
	_wfd_timer = setTimeout(() => run_preview(frm), 450);
}

function run_preview(frm) {
	const payload = { fields: {} };
	WFD_PREVIEW_FIELDS.forEach((f) => (payload.fields[f] = frm.doc[f]));
	WFD_TABLES.forEach((t) => (payload[t] = frm.doc[t] || []));
	// Ack keys ride along so the live panel doesn't re-count signed-off warnings.
	payload.issue_acks = (frm.doc.issue_acks || []).map((a) => ({ issue_key: a.issue_key }));

	frappe.call({
		method: "erpnext_enhancements.water_engineering.api.water_design.preview_design",
		args: { payload: payload },
		callback: (r) => {
			if (r && r.message && !r.message.error) apply_preview(frm, r.message);
		},
	});
}

function apply_preview(frm, p) {
	frm._wfd_applying = true;
	try {
		const ru = p.rollups || {};
		Object.keys(ru).forEach((k) => (frm.doc[k] = ru[k]));
		frm.doc.completion_percent = p.completion_percent;
		frm.doc.has_warnings = p.has_warnings ? 1 : 0;
		frm.doc.next_inputs_needed = (p.next_inputs_needed || []).join("\n");

		(p.basins || []).forEach((b, i) => {
			if (frm.doc.basins && frm.doc.basins[i]) {
				frm.doc.basins[i].volume_gal = b.volume_gal;
				frm.doc.basins[i].weight_lb = b.weight_lb;
			}
		});
		(p.features || []).forEach((f, i) => {
			if (frm.doc.features && frm.doc.features[i]) frm.doc.features[i].flow_gpm = f.flow_gpm;
		});
		(p.pipe_segments || []).forEach((s, i) => {
			const row = frm.doc.pipe_segments && frm.doc.pipe_segments[i];
			if (row) {
				row.velocity_fps = s.velocity_fps;
				row.velocity_status = s.velocity_status;
				row.head_loss_ft = s.head_loss_ft;
				row.pressure_status = s.pressure_status;
				row.pressure_margin_psi = s.pressure_margin_psi;
			}
		});

		["total_basin_gallons", "required_circulation_gpm", "design_flow_gpm", "computed_tdh_ft",
			"selected_pump", "chlorinator_feed_gph", "drain_capacity_gpm", "surge_basin_gallons",
			"completion_percent", "next_inputs_needed", "basins", "features", "pipe_segments"].forEach((f) =>
			frm.refresh_field(f)
		);

		render_dashboard(frm, p);
		setTimeout(() => paint_segment_grid(frm, p), 60);
	} finally {
		frm._wfd_applying = false;
	}
}

function pill_class(status) {
	const s = (status || "").toLowerCase();
	if (s.includes("exceed") || s.includes("cavitation") || s.includes("entrapment")) return "red";
	if (s.includes("increase") || s.includes("marginal")) return "orange";
	if (s.includes("below")) return "blue";
	if (s.includes("okay")) return "green";
	return "gray";
}
function pill_color(status) {
	const s = (status || "").toLowerCase();
	if (s.includes("exceed") || s.includes("cavitation") || s.includes("entrapment")) return "#c0392b";
	if (s.includes("increase") || s.includes("marginal")) return "#c2700a";
	if (s.includes("below")) return "#2490ef";
	if (s.includes("okay")) return "#1f9d55";
	return "var(--text-muted)";
}

function paint_segment_grid(frm, p) {
	const grid = frm.fields_dict.pipe_segments && frm.fields_dict.pipe_segments.grid;
	if (!grid || !grid.grid_rows) return;
	(p.pipe_segments || []).forEach((s, i) => {
		try {
			const gr = grid.grid_rows[i];
			const col = gr && gr.columns && gr.columns.velocity_status;
			if (col && col.$content) col.$content.css({ "font-weight": 500, color: pill_color(s.velocity_status) });
		} catch (e) {
			/* grid internals vary by version — coloring is a progressive enhancement */
		}
	});
}

// "Show the math" toggle state (kept across re-renders so the last preview can
// be redrawn without a server round-trip when the user flips it).
const WFD_MATH = { show: false, p: null, frm: null };

// Render the full math behind every calc — formula, inputs (with provenance),
// step-by-step working, citations, warnings — from preview_design's calc_results
// (the same envelope the Calculation Audit print format renders).
function math_html(p) {
	const esc = frappe.utils.escape_html;
	const rows = p.calc_results || [];
	if (!rows.length) return `<div style="color:var(--text-muted);font-size:12px;margin-top:8px">${__("No calculations yet.")}</div>`;
	const fmt = (v) => (v == null || v === "" ? "—" : esc(String(v)));
	const inputs_tbl = (txt) => {
		const lines = (txt || "").split("\n").filter(Boolean);
		if (!lines.length) return "";
		return `<table style="width:100%;border-collapse:collapse;font-size:11px;margin-top:3px">${lines
			.map((ln) => {
				const c = ln.split("\t");
				return `<tr><td style="padding:1px 6px;color:var(--text-muted)">${esc(c[0] || "")}</td><td style="padding:1px 6px;text-align:right;font-weight:600">${esc(c[1] || "")}</td><td style="padding:1px 6px;color:var(--text-muted)">${esc(c[2] || "")}</td><td style="padding:1px 6px;color:var(--text-muted)">${esc(c[3] || "")}</td></tr>`;
			})
			.join("")}</table>`;
	};
	const cards = rows
		.map(
			(r) => `
			<div style="border:1px solid var(--border-color);border-radius:6px;margin:8px 0;padding:8px 10px;background:var(--fg-color,var(--card-bg))">
				<div style="display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid var(--border-color);padding-bottom:3px">
					<b style="font-size:12px">${esc(r.calc)}</b>
					<span style="font-size:12px"><b>${fmt(r.value)}</b> <span style="color:var(--text-muted)">${esc(r.unit || "")}</span>${r.status ? ` · ${esc(r.status)}` : ""}</span>
				</div>
				${r.formula ? `<div style="margin-top:4px;font-size:11px"><span style="color:var(--text-muted)">${__("Formula")}:</span> <code>${esc(r.formula)}</code></div>` : ""}
				${inputs_tbl(r.inputs_text)}
				${(r.steps || []).length ? `<div style="margin-top:4px;font-size:11px"><span style="color:var(--text-muted)">${__("Working")}:</span><pre style="margin:2px 0;white-space:pre-wrap;font-size:11px">${esc((r.steps || []).join("\n"))}</pre></div>` : ""}
				${(r.citations || []).length ? `<div style="font-size:11px;color:var(--text-muted)">${__("Source")}: ${esc((r.citations || []).join(", "))}</div>` : ""}
				${(r.warnings || []).length ? `<div style="font-size:11px;color:#c2700a;margin-top:2px">&#9888; ${esc((r.warnings || []).join("; "))}</div>` : ""}
			</div>`
		)
		.join("");
	return `<div style="margin-top:12px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Calculations")} (${rows.length})</div>${cards}</div>`;
}

function render_dashboard(frm, p) {
	WFD_MATH.p = p;
	WFD_MATH.frm = frm;
	const d = frm.doc;
	const esc = frappe.utils.escape_html;
	const num = (v, dp) => (v == null || v === "" || v === 0 ? "—" : Number(v).toFixed(dp == null ? 2 : dp));
	const pct = Math.round(d.completion_percent || 0);
	const ru = p.rollups || {};

	const cstate = p.canvas || {};
	const WF = window.WaterFountain;
	const canvas = WF ? WF.canvasSvg(cstate) : "";
	const duty = WF && ru.selected_pump ? WF.dutySvg({ curve: cstate.curve || [], duty_flow: cstate.duty_flow, duty_head: cstate.duty_head, pump: ru.selected_pump }) : "";

	const tdh = ru.computed_tdh_ft || 0;
	const stat = p.static_lift_ft || 0;
	const fric = Math.max(tdh - stat, 0);
	let tdhBar = "";
	if (tdh > 0) {
		const sp = Math.round((stat / tdh) * 100);
		tdhBar = `
			<div style="margin-top:14px">
				<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--text-muted);margin-bottom:3px"><span>${__("TDH breakdown")}</span><span>${num(tdh)} ft</span></div>
				<div style="display:flex;height:14px;border-radius:4px;overflow:hidden;background:rgba(128,128,128,.15)">
					<div style="width:${sp}%;background:#5e9bd6"></div><div style="width:${100 - sp}%;background:#e8a13a"></div>
				</div>
				<div style="font-size:11px;color:var(--text-muted);margin-top:3px"><span style="color:#5e9bd6">&#9632;</span> ${__("Static")} ${num(stat)} ft &nbsp; <span style="color:#e8a13a">&#9632;</span> ${__("Friction")} ${num(fric)} ft</div>
			</div>`;
	}

	let segs = "";
	if ((p.pipe_segments || []).length) {
		const rows = p.pipe_segments
			.map((s, i) => {
				const pressure = s.pressure_status && !/okay/i.test(s.pressure_status)
					? ` <span class="indicator-pill ${pill_class(s.pressure_status)}" style="margin-left:4px">${esc(s.pressure_status)}</span>`
					: "";
				return `
				<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-top:1px solid var(--border-color)">
					<span>${esc(s.segment_label || __("Segment") + " " + (i + 1))}</span>
					<span><span style="color:var(--text-muted);font-size:12px">${num(s.velocity_fps)} ft/s</span> <span class="indicator-pill ${pill_class(s.velocity_status)}" style="margin-left:6px">${esc(s.velocity_status || "—")}</span>${pressure}</span>
				</div>`;
			})
			.join("");
		segs = `<div style="margin-top:14px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Pipe segments")}</div>${rows}</div>`;
	}

	const health = wfd_health_html(frm, p);

	const hasMath = (p.calc_results || []).length > 0;
	const mathToggle = hasMath
		? `<span class="wfd-math-toggle" style="cursor:pointer;font-size:12px;color:var(--primary,#2490ef);user-select:none;margin-right:12px">${WFD_MATH.show ? __("Hide the math") : __("Show the math")}</span>`
		: "";

	const empty = !d.basins?.length && !d.features?.length;
	const body = empty
		? `<div style="color:var(--text-muted);padding:6px 0">${__("Start by adding a basin or a feature — or use New from Template.")}</div>${health}`
		: `<div style="overflow-x:auto">${canvas}</div>
			<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:8px">
				${duty ? `<div style="flex:1;min-width:280px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Pump duty point")}</div>${duty}</div>` : ""}
				<div style="flex:1;min-width:280px">${tdhBar}${segs}</div>
			</div>
			${health}${WFD_MATH.show ? math_html(p) : ""}`;

	frm.get_field("dashboard").$wrapper.html(`
		<div style="border:1px solid var(--border-color);border-radius:var(--border-radius-lg,8px);padding:14px 16px;background:var(--card-bg,var(--fg-color))">
			<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
				<div style="font-weight:600">${__("Hydraulic model")}</div>
				<div style="display:flex;align-items:center;gap:8px;min-width:150px">
					${mathToggle}
					${wfd_chips_html(p)}
					<div style="flex:1;height:8px;border-radius:4px;background:rgba(128,128,128,.2)"><div style="width:${pct}%;height:8px;border-radius:4px;background:var(--primary,#2490ef)"></div></div>
					<span style="color:var(--text-muted);font-size:12px">${pct}%</span>
				</div>
			</div>
			${body}
		</div>`);

	const $wrapper = frm.get_field("dashboard").$wrapper;
	$wrapper
		.find(".wfd-math-toggle")
		.off("click")
		.on("click", () => {
			WFD_MATH.show = !WFD_MATH.show;
			render_dashboard(WFD_MATH.frm, WFD_MATH.p);
		});
	wfd_wire_health(frm, $wrapper, p);
}

// ------------------------------------------------------ Design Health panel
// One place that answers "what is wrong and where, and what do I still owe":
// severity chips, the typed issue list (click-to-jump, per-warning acknowledge),
// and the itemized readiness checklist behind completion_percent + issue_ready.

const WFD_SECTION_ANCHORS = {
	project: "design_title",
	basin: "basins",
	features: "features",
	tiers: "tiers",
	piping: "pipe_segments",
	pump: "pumps",
	electrical: "electrical_loads",
	safety: "pipe_segments",
	chemistry: "chem_water_type",
	drainage: "drain_nominal_size",
	deliverables: "pipe_segments",
};

const WFD_SEVERITY_STYLE = {
	blocker: { dot: "#c0392b", label: __("Blocker") },
	warning: { dot: "#c2700a", label: __("Warning") },
	info: { dot: "#2490ef", label: __("Advisory") },
};

function wfd_chips_html(p) {
	const issues = p.issues || [];
	const n = (sev) => issues.filter((i) => i.severity === sev).length;
	const chip = (count, cls, label) =>
		count ? `<span class="indicator-pill ${cls}" style="white-space:nowrap">${count} ${label}</span>` : "";
	const parts =
		chip(n("blocker"), "red", __("blockers")) +
		chip(n("warning"), "orange", __("warnings")) +
		chip(n("info"), "blue", __("advisories"));
	return parts || `<span class="indicator-pill green" style="white-space:nowrap">${__("No issues")}</span>`;
}

function wfd_health_html(frm, p) {
	const esc = frappe.utils.escape_html;
	const issues = p.issues || [];
	const readiness = p.readiness || {};
	const acked = {};
	(frm.doc.issue_acks || []).forEach((a) => (acked[a.issue_key] = a));

	// --- issues, grouped in section (design-procession) order ---------------
	const order = (readiness.sections || []).map((s) => s.key);
	if (!order.includes("safety")) order.push("safety");
	const groups = {};
	issues.forEach((i) => {
		(groups[i.section] = groups[i.section] || []).push(i);
	});
	const section_label = (key) => {
		const s = (readiness.sections || []).find((x) => x.key === key);
		return s ? s.label : key;
	};

	let issue_html = "";
	Object.keys(groups)
		.sort((a, b) => order.indexOf(a) - order.indexOf(b))
		.forEach((key) => {
			const rows = groups[key]
				.map((i) => {
					const idx = issues.indexOf(i);
					const sev = WFD_SEVERITY_STYLE[i.severity] || WFD_SEVERITY_STYLE.info;
					const ack = acked[i.key];
					const can_ack = i.severity === "warning" && !frm.is_new() && !ack;
					const meta = [
						i.detail ? esc(i.detail) : "",
						i.fix_hint ? `<b>${__("Fix")}:</b> ${esc(i.fix_hint)}` : "",
						i.citation ? `<span style="color:var(--text-muted)">${__("Source")}: ${esc(i.citation)}</span>` : "",
					].filter(Boolean);
					return `
					<details class="wfd-issue" style="padding:3px 0;${ack ? "opacity:.55" : ""}">
						<summary style="display:flex;align-items:baseline;gap:7px;cursor:pointer;list-style:none">
							<span style="flex-shrink:0;width:8px;height:8px;border-radius:50%;background:${sev.dot};display:inline-block;position:relative;top:-1px" title="${sev.label}"></span>
							<span style="flex:1">${esc(i.title)}${ack ? ` <span style="color:var(--text-muted);font-size:11px">— ${__("acknowledged by {0}", [esc(ack.acknowledged_by || "")])}</span>` : ""}</span>
							${i.ref ? `<a class="wfd-jump" data-i="${idx}" style="font-size:11px;white-space:nowrap">${__("Go")}</a>` : ""}
							${can_ack ? `<a class="wfd-ack" data-i="${idx}" style="font-size:11px;white-space:nowrap;color:#c2700a">${__("Acknowledge…")}</a>` : ""}
						</summary>
						${meta.length ? `<div style="margin:3px 0 3px 15px;font-size:11px;color:var(--text-color)">${meta.join("<br>")}</div>` : ""}
					</details>`;
				})
				.join("");
			issue_html += `
				<div style="margin-top:6px">
					<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em">${esc(section_label(key))}</div>
					${rows}
				</div>`;
		});

	// --- readiness checklist (two gates, itemized) ---------------------------
	const gate_rows = (gate, title) => {
		const rows = (readiness.sections || [])
			.filter((s) => s.gate === gate && s.state !== "n/a")
			.map((s) => {
				const ok = s.state === "complete";
				const blocked = s.state === "blocked";
				const icon = blocked
					? `<span style="color:#c0392b">&#9679;</span>`
					: ok
						? `<span style="color:#1f9d55">&#10003;</span>`
						: `<span style="color:var(--text-muted)">&#9675;</span>`;
				const missing = (s.missing || [])
					.map(
						(m) => `
						<div style="margin-left:18px;font-size:11px;color:var(--text-muted)">
							${esc(m.label)}${m.why ? ` — ${esc(m.why)}` : ""}
							${m.field ? ` <a class="wfd-goto" data-field="${esc(m.field)}" style="font-size:11px">${__("Go")}</a>` : ""}
						</div>`
					)
					.join("");
				return `<div style="padding:2px 0">${icon} ${esc(s.label)}${missing}</div>`;
			})
			.join("");
		return rows
			? `<div style="flex:1;min-width:220px"><div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;margin-bottom:2px">${title}</div>${rows}</div>`
			: "";
	};
	const calc_ok = readiness.calc_ready;
	const issue_ok = readiness.issue_ready;
	const checklist = (readiness.sections || []).length
		? `<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;padding:8px 10px;border-radius:6px;background:rgba(127,127,127,.08);font-size:12px">
			${gate_rows("calc", __("To calculate") + (calc_ok ? " ✓" : ""))}
			${gate_rows("issue", __("To issue the package") + (issue_ok ? " ✓" : ""))}
		</div>`
		: "";

	const head = issues.length
		? `<div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Issues")} (${issues.length})</div>`
		: `<div style="font-size:12px;color:var(--text-muted)">${__("No open issues.")}</div>`;
	return `<div class="wfd-health" style="margin-top:14px;font-size:12px">${head}${issue_html}${checklist}</div>`;
}

function wfd_wire_health(frm, $wrapper, p) {
	const issues = p.issues || [];
	$wrapper.find(".wfd-jump").on("click", function (e) {
		e.preventDefault();
		e.stopPropagation();
		const issue = issues[cint($(this).data("i"))];
		if (issue) wfd_jump(frm, issue.ref);
	});
	$wrapper.find(".wfd-goto").on("click", function (e) {
		e.preventDefault();
		e.stopPropagation();
		wfd_jump(frm, { field: $(this).data("field") });
	});
	$wrapper.find(".wfd-ack").on("click", function (e) {
		e.preventDefault();
		e.stopPropagation();
		const issue = issues[cint($(this).data("i"))];
		if (!issue) return;
		frappe.prompt(
			[
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("Note (why this warning is acceptable)"),
				},
			],
			(values) => {
				frappe
					.call({
						method: "erpnext_enhancements.water_engineering.api.water_design.acknowledge_issue",
						args: { design: frm.doc.name, issue_key: issue.key, note: values.note },
					})
					.then(() => frm.reload_doc());
			},
			__("Acknowledge: {0}", [issue.title]),
			__("Acknowledge")
		);
	});
}

// Scroll to (and flash) the thing an issue points at: a parent field, or a
// specific child-table row. Grid internals are semi-internal — everything is
// wrapped so a framework change degrades to a section scroll, never an error.
function wfd_jump(frm, ref) {
	if (!ref) return;
	wfd_flash_style();
	try {
		if (ref.table) {
			const grid = frm.fields_dict[ref.table] && frm.fields_dict[ref.table].grid;
			const gr =
				grid &&
				((ref.row_name && grid.grid_rows_by_docname && grid.grid_rows_by_docname[ref.row_name]) ||
					(grid.grid_rows || [])[ref.row_idx]);
			if (gr && gr.row && gr.row[0]) {
				gr.row[0].scrollIntoView({ behavior: "smooth", block: "center" });
				gr.row.addClass("wfd-flash");
				setTimeout(() => gr.row.removeClass("wfd-flash"), 1800);
				return;
			}
			frm.scroll_to_field(ref.table);
			return;
		}
		if (ref.field) frm.scroll_to_field(ref.field);
	} catch (e) {
		try {
			frm.scroll_to_field(ref.field || ref.table);
		} catch (e2) {
			/* nothing to anchor to */
		}
	}
}

let _wfd_flash_css = false;
function wfd_flash_style() {
	if (_wfd_flash_css) return;
	_wfd_flash_css = true;
	$("<style>")
		.text(
			".wfd-flash{outline:2px solid var(--primary,#2490ef);outline-offset:-2px;transition:outline .3s} " +
				".wfd-issue summary::-webkit-details-marker{display:none} " +
				".wfd-issue summary a{text-decoration:none}"
		)
		.appendTo(document.head);
}

// Load a template's rows + inputs into the form. `from_field` = the user picked
// it via the Fountain Type field (vs the New from Template button), so a cancelled
// confirm reverts the field instead of leaving a label that doesn't match the rows.
function apply_template(frm, name, from_field) {
	const tpl = WFD_TEMPLATES[name];
	if (!tpl) return;
	const has_rows = WFD_TEMPLATE_TABLES.some((t) => (frm.doc[t] || []).length);
	// Set fountain_type without re-triggering its change handler.
	const set_type = (val) => {
		frm._wfd_template_applying = true;
		try {
			frm.set_value("fountain_type", val);
		} finally {
			frm._wfd_template_applying = false;
		}
	};
	const fill = () => {
		set_type(name);
		(tpl.fields ? Object.keys(tpl.fields) : []).forEach((k) => frm.set_value(k, tpl.fields[k]));
		WFD_TEMPLATE_TABLES.forEach((t) => {
			frm.clear_table(t);
			(tpl[t] || []).forEach((row) => frm.add_child(t, Object.assign({}, row)));
			frm.refresh_field(t);
		});
		frm._wfd_ft = name;
		frm.dirty();
		schedule_preview(frm);
		frappe.show_alert({ message: __("Loaded template: {0}", [name]), indicator: "green" });
	};
	if (has_rows) {
		frappe.confirm(
			__("Replace the current basin, feature, piping, and tier rows with the {0} template?", [name]),
			fill,
			() => {
				if (from_field) set_type(frm._wfd_ft || ""); // cancelled: revert the field
			}
		);
	} else {
		fill();
	}
}

// ---------------------------------------------------------- loss pickers
// Fittings/valves and equipment/components add minor + component head loss to a
// pipe segment. The engine sizes them from K-factors / coefficients keyed by an
// exact catalog name — so instead of hand-typing JSON, the designer picks from a
// dropdown (the catalog) + a quantity, and we serialize that to the stored JSON.

const WFD_LOSS_META = {
	fittings: {
		json: "fittings_json",
		summary: "fittings_summary",
		title: "Fittings & Valves",
		add: "Add fitting / valve",
		blurb: "Elbows, tees, valves, entrances/exits — head loss from each item's K-factor.",
		hint: (o) => (o.k != null ? `K ${o.k}` : ""),
	},
	components: {
		json: "components_json",
		summary: "components_summary",
		title: "Equipment & Components",
		add: "Add equipment",
		blurb: "Filters, skimmers, heaters, valves — head loss per GPM from each item's coefficient.",
		hint: (o) => (o.coeff != null ? `${Number(o.coeff).toFixed(3)} ft/GPM` : ""),
	},
};

let _wfd_loss_catalog = null;
function wfd_loss_catalog() {
	if (_wfd_loss_catalog) return Promise.resolve(_wfd_loss_catalog);
	return frappe
		.call("erpnext_enhancements.water_engineering.api.water_design.get_loss_catalog")
		.then((r) => {
			_wfd_loss_catalog = r.message || { fittings: [], components: [] };
			return _wfd_loss_catalog;
		});
}

function wfd_parse_loss(text) {
	if (!text) return [];
	try {
		const data = JSON.parse(text);
		return Array.isArray(data) ? data.filter((r) => r && r.type) : [];
	} catch (e) {
		return [];
	}
}

function wfd_summarize_loss(list) {
	return (list || [])
		.filter((r) => r.type)
		.map((r) => `${cint(r.qty) || 1}× ${r.type}`)
		.join(", ");
}

// Populate the human-readable summaries from the stored JSON (e.g. for designs
// saved before the picker existed). Direct row assignment — no dirty, no preview.
function wfd_sync_loss_summaries(frm) {
	let changed = false;
	(frm.doc.pipe_segments || []).forEach((row) => {
		["fittings", "components"].forEach((kind) => {
			const m = WFD_LOSS_META[kind];
			if (!row[m.summary] && row[m.json]) {
				row[m.summary] = wfd_summarize_loss(wfd_parse_loss(row[m.json]));
				changed = true;
			}
		});
	});
	if (changed) frm.refresh_field("pipe_segments");
}

function wfd_edit_losses(frm, cdt, cdn, kind) {
	const row = locals[cdt][cdn];
	const m = WFD_LOSS_META[kind];
	wfd_loss_catalog().then((cat) => {
		const options = (kind === "fittings" ? cat.fittings : cat.components) || [];
		wfd_open_loss_dialog(m, options, wfd_parse_loss(row[m.json]), (list) => {
			frappe.model.set_value(cdt, cdn, m.json, list.length ? JSON.stringify(list) : "");
			frappe.model.set_value(cdt, cdn, m.summary, wfd_summarize_loss(list));
			schedule_preview(frm);
		});
	});
}

function wfd_open_loss_dialog(m, options, existing, on_apply) {
	const esc = frappe.utils.escape_html;
	const option_tags = (selected) =>
		[`<option value="">${__("— select —")}</option>`]
			.concat(
				options.map((o) => {
					const hint = m.hint(o);
					const text = o.type + (hint ? `  (${hint})` : "");
					return `<option value="${esc(o.type)}"${o.type === selected ? " selected" : ""}>${esc(text)}</option>`;
				})
			)
			.join("");

	const row_html = (r) => `
		<div class="wfd-loss-row" style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
			<select class="wfd-loss-type form-control input-sm" style="flex:1">${option_tags(r ? r.type : "")}</select>
			<input type="number" min="1" step="1" class="wfd-loss-qty form-control input-sm" style="width:84px"
				value="${r ? cint(r.qty) || 1 : 1}" aria-label="${__("Quantity")}">
			<button class="btn btn-default btn-xs wfd-loss-del" title="${__("Remove")}">&times;</button>
		</div>`;

	const dialog = new frappe.ui.Dialog({
		title: __(m.title),
		size: "large",
		primary_action_label: __("Apply"),
		primary_action: () => {
			const list = [];
			dialog.$body.find(".wfd-loss-row").each(function () {
				const type = $(this).find(".wfd-loss-type").val();
				const qty = cint($(this).find(".wfd-loss-qty").val());
				if (type && qty > 0) list.push({ type: type, qty: qty });
			});
			on_apply(list);
			dialog.hide();
		},
	});

	dialog.$body.html(`
		<div style="font-size:12px;color:var(--text-muted);margin-bottom:10px">${__(m.blurb)}</div>
		<div class="wfd-loss-list">${(existing.length ? existing : [null]).map(row_html).join("")}</div>
		<button class="btn btn-default btn-sm wfd-loss-add" style="margin-top:6px">+ ${__(m.add)}</button>
	`);

	dialog.$body.find(".wfd-loss-add").on("click", () => {
		$(row_html(null)).appendTo(dialog.$body.find(".wfd-loss-list"));
	});
	dialog.$body.on("click", ".wfd-loss-del", function () {
		$(this).closest(".wfd-loss-row").remove();
	});
	dialog.show();
}
