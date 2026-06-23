/**
 * @file Client controller for the "Water Engineering Wizard" desk page.
 * @description
 * Frappe auto-loads this and calls
 * `frappe.pages["water-engineering-wizard"].on_page_load` when the page opens
 * (page defined by water_engineering_wizard.json).
 *
 * Two panels, both backed by the SAME pure engine as the FAC MCP tools via the
 * whitelisted endpoints in water_engineering/api/water_design.py:
 *   - Quick Calculator: pick a Phase-1 calc, enter inputs, and see the result
 *     WITH its math (formula, steps, citations, warnings, A/B/C options).
 *   - Designs: load a Water Feature Design's running state (rollups, what's still
 *     needed, audit trail) and jump to the full form for detailed row editing.
 *
 * Access is gated server-side by `check_permission` (read on Water Feature
 * Design); theming uses Frappe CSS variables so Light + Timeless Night both work.
 */

const WE_API = "erpnext_enhancements.water_engineering.api.water_design.";

// Quick-calculator field schemas. `kind` drives the input widget; `opts` lists
// Select choices.
const WE_CALCS = {
	basin_volume: {
		label: "Basin volume & weight",
		fields: [
			{ name: "shape", label: "Shape", kind: "select", opts: ["rectangular", "cylindrical"] },
			{ name: "length_in", label: "Length (in)", kind: "number" },
			{ name: "width_in", label: "Width (in)", kind: "number" },
			{ name: "diameter_in", label: "Diameter (in)", kind: "number" },
			{ name: "height_in", label: "Height (in)", kind: "number" },
		],
	},
	turnover_gpm: {
		label: "Turnover -> circulation GPM",
		fields: [
			{ name: "volume_gal", label: "Volume (gal)", kind: "number" },
			{ name: "turnovers_per_hr", label: "Turnovers / hr", kind: "number", def: 2 },
		],
	},
	weir_flow: {
		label: "Weir / slot flow (Francis)",
		fields: [
			{ name: "length_ft", label: "Weir length (ft)", kind: "number" },
			{ name: "head_in", label: "Head (in)", kind: "number" },
			{ name: "contractions", label: "End contractions", kind: "number", def: 2 },
		],
	},
	size_pipe: {
		label: "Pipe sizing & velocity",
		fields: [
			{ name: "flow_gpm", label: "Flow (GPM)", kind: "number" },
			{ name: "length_ft", label: "Run length (ft)", kind: "number" },
			{ name: "material", label: "Material", kind: "select", opts: ["SCH40 PVC", "SCH80 PVC", "Type K Copper"] },
			{ name: "line", label: "Line", kind: "select", opts: ["discharge", "suction"] },
		],
	},
	hazen_williams_loss: {
		label: "Hazen-Williams friction loss",
		fields: [
			{ name: "flow_gpm", label: "Flow (GPM)", kind: "number" },
			{ name: "length_ft", label: "Length (ft)", kind: "number" },
			{ name: "id_in", label: "Pipe ID (in)", kind: "number" },
		],
	},
};

frappe.pages["water-engineering-wizard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Water Engineering Wizard"),
		single_column: true,
	});
	const $body = $(page.body);

	frappe.call(WE_API + "check_permission").then((r) => {
		if (!r.message) {
			$body.html(
				`<div class="we-denied">${__("You do not have access to Water Feature Designs.")}</div>`
			);
			return;
		}
		render_shell(page, $body);
	});
};

function render_shell(page, $body) {
	$body.html(`
		<div class="we-wrap">
			<div class="we-col we-calc">
				<div class="we-card">
					<div class="we-card-head">${__("Quick Calculator")}</div>
					<div class="we-row">
						<label>${__("Calculation")}</label>
						<select class="we-input" id="we-calc-pick"></select>
					</div>
					<div id="we-calc-fields"></div>
					<button class="btn btn-primary btn-sm" id="we-calc-run">${__("Calculate")}</button>
					<div id="we-calc-result"></div>
				</div>
			</div>
			<div class="we-col we-designs">
				<div class="we-card">
					<div class="we-card-head">
						${__("Designs")}
						<button class="btn btn-default btn-xs" id="we-new">${__("New Design")}</button>
					</div>
					<div class="we-row">
						<label>${__("Open")}</label>
						<select class="we-input" id="we-design-pick"></select>
					</div>
					<div id="we-design-state"></div>
				</div>
			</div>
		</div>
	`);

	// Quick calculator
	const $pick = $body.find("#we-calc-pick");
	Object.keys(WE_CALCS).forEach((k) => $pick.append(`<option value="${k}">${WE_CALCS[k].label}</option>`));
	$pick.on("change", () => render_calc_fields($body, $pick.val()));
	render_calc_fields($body, $pick.val());
	$body.find("#we-calc-run").on("click", () => run_calc($body));

	// Designs
	$body.find("#we-new").on("click", () => frappe.new_doc("Water Feature Design"));
	load_design_list($body);
	$body.find("#we-design-pick").on("change", function () {
		if (this.value) load_design_state($body, this.value);
	});
}

function render_calc_fields($body, calc) {
	const fields = WE_CALCS[calc].fields;
	const html = fields
		.map((f) => {
			if (f.kind === "select") {
				const opts = f.opts.map((o) => `<option value="${o}">${o}</option>`).join("");
				return `<div class="we-row"><label>${f.label}</label><select class="we-input we-field" data-name="${f.name}">${opts}</select></div>`;
			}
			const val = f.def != null ? f.def : "";
			return `<div class="we-row"><label>${f.label}</label><input type="number" step="any" class="we-input we-field" data-name="${f.name}" value="${val}"></div>`;
		})
		.join("");
	$body.find("#we-calc-fields").html(html);
}

function run_calc($body) {
	const calc = $body.find("#we-calc-pick").val();
	const inputs = {};
	$body.find(".we-field").each(function () {
		const name = $(this).data("name");
		let v = $(this).val();
		if ($(this).attr("type") === "number") v = v === "" ? 0 : parseFloat(v);
		inputs[name] = v;
	});
	frappe.call({
		method: WE_API + "run_calc",
		args: { calc: calc, inputs: JSON.stringify(inputs) },
		freeze: true,
	}).then((r) => render_result($body.find("#we-calc-result"), r.message));
}

function render_result($el, res) {
	if (!res) {
		$el.html("");
		return;
	}
	const esc = frappe.utils.escape_html;
	const valTxt = res.value == null ? "—" : `${res.value} ${esc(res.unit || "")}`;
	const status = res.status
		? `<span class="we-badge we-${we_status_class(res.status)}">${esc(res.status)}</span>`
		: "";
	const steps = (res.steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
	const cites = (res.citations || []).map((c) => esc(c)).join(" · ");
	const warns = (res.warnings || []).length
		? `<div class="we-warn">${res.warnings.map((w) => esc(w)).join("<br>")}</div>`
		: "";
	const opts = (res.options || []).length
		? `<div class="we-opts"><div class="we-sub">${__("Options")}</div>` +
		  res.options
				.map((o, i) => {
					const key = o.key || String.fromCharCode(65 + i);
					const star = o.recommended ? " ★" : "";
					const d = o.detail || {};
					const meta = Object.keys(d)
						.map((k) => `${esc(k)}=${esc(String(d[k]))}`)
						.join(", ");
					return `<div class="we-opt"><b>${esc(key)}${star}</b> ${esc(o.label || "")}<span class="we-muted"> ${esc(meta)}</span></div>`;
				})
				.join("") +
		  "</div>"
		: "";
	$el.html(`
		<div class="we-result">
			<div class="we-value">${valTxt} ${status}</div>
			<div class="we-formula">${esc(res.formula || "")}</div>
			<ul class="we-steps">${steps}</ul>
			${warns}
			${opts}
			<div class="we-cite">${cites}</div>
		</div>
	`);
}

function we_status_class(status) {
	if (/exceeds/i.test(status)) return "bad";
	if (/increase/i.test(status)) return "warn";
	return "ok";
}

function load_design_list($body) {
	frappe.call(WE_API + "get_design_state").then((r) => {
		const $pick = $body.find("#we-design-pick");
		$pick.html(`<option value="">${__("Select a design…")}</option>`);
		((r.message || {}).designs || []).forEach((d) => {
			const label = `${d.name} — ${d.design_title || d.project || ""} (${d.status})`;
			$pick.append(`<option value="${d.name}">${frappe.utils.escape_html(label)}</option>`);
		});
	});
}

function load_design_state($body, design) {
	frappe.call({ method: WE_API + "get_design_state", args: { design } }).then((r) => {
		render_design_state($body.find("#we-design-state"), r.message);
	});
}

function render_design_state($el, st) {
	if (!st || !st.design) {
		$el.html("");
		return;
	}
	const esc = frappe.utils.escape_html;
	const ro = st.rollups || {};
	const roll = [
		["Basin (gal)", ro.total_basin_gallons],
		["Circulation (GPM)", ro.required_circulation_gpm],
		["Design Flow (GPM)", ro.design_flow_gpm],
		["TDH (ft)", ro.computed_tdh_ft],
		["Pump", ro.selected_pump],
	]
		.map(([k, v]) => `<tr><td>${k}</td><td>${v == null ? "—" : esc(String(v))}</td></tr>`)
		.join("");
	const needs = (st.next_inputs_needed || []).length
		? `<div class="we-warn"><b>${__("Still needed")}:</b> ${st.next_inputs_needed.map(esc).join(", ")}</div>`
		: `<div class="we-ok-note">${__("All Phase-1 inputs provided.")}</div>`;
	$el.html(`
		<div class="we-state">
			<div class="we-row">
				<b>${esc(st.design)}</b> — ${esc(st.design_title || "")}
				<span class="we-badge we-${st.completion_percent >= 100 ? "ok" : "warn"}">${st.completion_percent || 0}%</span>
				<button class="btn btn-default btn-xs" id="we-open-form">${__("Open in Form")}</button>
			</div>
			<table class="we-rollups">${roll}</table>
			${needs}
		</div>
	`);
	$el.find("#we-open-form").on("click", () => frappe.set_route("Form", "Water Feature Design", st.design));
}
