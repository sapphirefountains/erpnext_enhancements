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
};

// Model tables a template may define / replace (tiers included so the Tiered
// Fountain template lands its cascade rows).
const WFD_TEMPLATE_TABLES = ["basins", "features", "pipe_segments", "tiers"];

const WFD_PREVIEW_FIELDS = [
	"turnover_per_hr", "hazen_williams_c", "pipe_material", "static_lift_ft",
	"chem_water_type", "chem_chlorine_pct", "drain_nominal_size", "drain_slope_in_per_ft",
	"surge_pool_area_sf", "surge_basin_area_sf",
];
const WFD_TABLES = ["basins", "features", "pipe_segments", "pumps", "electrical_loads", "tiers"];

frappe.ui.form.on("Water Feature Design", {
	refresh(frm) {
		frm.add_custom_button(__("Open Wizard"), () => frappe.set_route("water-engineering-wizard"));
		if (!frm.is_new()) {
			frm.add_custom_button(__("Recalculate"), () => frm.save());
		}
		Object.keys(WFD_TEMPLATES).forEach((name) => {
			frm.add_custom_button(__(name), () => apply_template(frm, name), __("New from Template"));
		});

		// Re-run the live preview whenever a grid changes (cell edit / add / remove).
		WFD_TABLES.forEach((t) => {
			const grid = frm.fields_dict[t] && frm.fields_dict[t].grid;
			if (grid && grid.wrapper) {
				grid.wrapper.off("change.wfdprev").on("change.wfdprev", () => schedule_preview(frm));
			}
		});
		schedule_preview(frm);
	},
});

// Re-preview when a global / treatment / drainage input changes.
const _input_triggers = {};
WFD_PREVIEW_FIELDS.forEach((f) => {
	_input_triggers[f] = (frm) => schedule_preview(frm);
});
frappe.ui.form.on("Water Feature Design", _input_triggers);

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
	if (s.includes("exceed")) return "red";
	if (s.includes("increase")) return "orange";
	if (s.includes("okay")) return "green";
	return "gray";
}
function pill_color(status) {
	const s = (status || "").toLowerCase();
	if (s.includes("exceed")) return "#c0392b";
	if (s.includes("increase")) return "#c2700a";
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
			.map((s, i) => `
				<div style="display:flex;justify-content:space-between;align-items:center;padding:4px 0;border-top:1px solid var(--border-color)">
					<span>${esc(s.segment_label || __("Segment") + " " + (i + 1))}</span>
					<span><span style="color:var(--text-muted);font-size:12px">${num(s.velocity_fps)} ft/s</span> <span class="indicator-pill ${pill_class(s.velocity_status)}" style="margin-left:6px">${esc(s.velocity_status || "—")}</span></span>
				</div>`)
			.join("");
		segs = `<div style="margin-top:14px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Pipe segments")}</div>${rows}</div>`;
	}

	let warn = `<div style="margin-top:12px;color:var(--text-muted);font-size:12px">${__("No warnings.")}</div>`;
	if ((p.warnings || []).length) {
		warn = `<div style="margin-top:14px;font-size:12px"><div style="color:var(--text-muted);margin-bottom:4px">${__("Warnings")} (${p.warnings.length})</div>${p.warnings
			.map((w) => `<div style="display:flex;gap:6px;padding:2px 0"><span style="color:#c2700a">&#9888;</span><span>${esc(w)}</span></div>`)
			.join("")}</div>`;
	}

	let needs = "";
	if ((p.next_inputs_needed || []).length) {
		needs = `<div style="margin-top:10px;padding:6px 10px;border-radius:6px;background:rgba(127,127,127,.12);font-size:12px"><b>${__("Still needed")}:</b> ${p.next_inputs_needed.map(esc).join(", ")}</div>`;
	}

	const hasMath = (p.calc_results || []).length > 0;
	const mathToggle = hasMath
		? `<span class="wfd-math-toggle" style="cursor:pointer;font-size:12px;color:var(--primary,#2490ef);user-select:none;margin-right:12px">${WFD_MATH.show ? __("Hide the math") : __("Show the math")}</span>`
		: "";

	const empty = !d.basins?.length && !d.features?.length;
	const body = empty
		? `<div style="color:var(--text-muted);padding:6px 0">${__("Start by adding a basin or a feature — or use New from Template.")}</div>`
		: `<div style="overflow-x:auto">${canvas}</div>
			<div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:8px">
				${duty ? `<div style="flex:1;min-width:280px"><div style="font-size:12px;color:var(--text-muted);margin-bottom:2px">${__("Pump duty point")}</div>${duty}</div>` : ""}
				<div style="flex:1;min-width:280px">${tdhBar}${segs}</div>
			</div>
			${warn}${needs}${WFD_MATH.show ? math_html(p) : ""}`;

	frm.get_field("dashboard").$wrapper.html(`
		<div style="border:1px solid var(--border-color);border-radius:var(--border-radius-lg,8px);padding:14px 16px;background:var(--card-bg,var(--fg-color))">
			<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
				<div style="font-weight:600">${__("Hydraulic model")}</div>
				<div style="display:flex;align-items:center;gap:8px;min-width:150px">
					${mathToggle}
					<div style="flex:1;height:8px;border-radius:4px;background:rgba(128,128,128,.2)"><div style="width:${pct}%;height:8px;border-radius:4px;background:var(--primary,#2490ef)"></div></div>
					<span style="color:var(--text-muted);font-size:12px">${pct}%</span>
				</div>
			</div>
			${body}
		</div>`);

	frm.get_field("dashboard").$wrapper
		.find(".wfd-math-toggle")
		.off("click")
		.on("click", () => {
			WFD_MATH.show = !WFD_MATH.show;
			render_dashboard(WFD_MATH.frm, WFD_MATH.p);
		});
}

function apply_template(frm, name) {
	const tpl = WFD_TEMPLATES[name];
	if (!tpl) return;
	const has_rows = WFD_TEMPLATE_TABLES.some((t) => (frm.doc[t] || []).length);
	const fill = () => {
		(tpl.fields ? Object.keys(tpl.fields) : []).forEach((k) => frm.set_value(k, tpl.fields[k]));
		WFD_TEMPLATE_TABLES.forEach((t) => {
			frm.clear_table(t);
			(tpl[t] || []).forEach((row) => frm.add_child(t, Object.assign({}, row)));
			frm.refresh_field(t);
		});
		frm.dirty();
		schedule_preview(frm);
		frappe.show_alert({ message: __("Loaded template: {0}", [name]), indicator: "green" });
	};
	if (has_rows) {
		frappe.confirm(__("Replace the current basin, feature, piping, and tier rows with the {0} template?", [name]), fill);
	} else {
		fill();
	}
}
