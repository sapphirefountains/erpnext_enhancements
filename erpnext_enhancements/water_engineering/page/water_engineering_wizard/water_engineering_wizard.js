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
 *
 * Loading, empty, error and no-access states come from sf_kit (v1.645.0), and the loads that
 * report in their own panel (the access check, the design list, a design's state) use its quiet
 * call. Calculate keeps frappe.call's dialogs: that failure answers a click.
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
	we_open(page, $(page.body));
};

function we_open(page, $body) {
	we_kit().then((sf) => {
		sf.state.render($body, "loading", { title: __("Loading the Water Engineering Wizard…") });
		sf.call(WE_API + "check_permission", {}, { quiet: true }).then(
			(allowed) => {
				if (!allowed) {
					sf.state.render($body, "no-access", {
						title: __("You do not have access to Water Feature Designs."),
					});
					return;
				}
				render_shell(page, $body);
			},
			(err) =>
				we_failed(sf, $body, err, __("Could not open the Water Engineering Wizard."), () => we_open(page, $body))
		);
	});
}

// sf_kit from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never
// rejects: a kit that never arrived resolves to a plain stand-in (frappe.call and a line of plain
// text), so the wizard carries on. A Desk opened before the deploy that added the loader has no
// sf_kit_ready yet, and asks frappe.require for the kit itself.
function we_kit() {
	if (typeof window.sf_kit_ready === "function") return window.sf_kit_ready();
	return new Promise((resolve) =>
		frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () =>
			resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : we_bare_kit())
		)
	);
}

// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
// A line of text and a button, so the page still says something instead of throwing.
function we_bare_kit() {
	const failed = (x) => ({ status: (x && x.status) || 0, message: "", exc_type: "", session_expired: (x && x.status) === 401 });
	const render = (el, kind, o) => {
		const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ""));
		if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __("Try again")).on("click", o.on_action).appendTo($el);
	};
	const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
	return { esc: (v) => frappe.utils.escape_html(v == null ? "" : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: "orange" }) };
}

// A load that failed: no access on a 403, a way back in when the session has lapsed (the quiet
// path puts up no sign-in dialog of frappe's), otherwise what went wrong with Try again.
function we_failed(sf, $el, err, title, retry) {
	err = err || {};
	if (err.session_expired) {
		return sf.state.render($el, "error", {
			title: __("You have been signed out."),
			body: __("Reload the page to sign in again."),
			action_label: __("Reload"),
			on_action: () => window.location.reload(),
		});
	}
	if (err.status === 403) {
		return sf.state.render($el, "no-access", {
			title: __("You do not have access to Water Feature Designs."),
			body: err.message,
		});
	}
	return sf.state.render($el, "error", { title, body: err.message, on_action: retry });
}

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
		// Back to "Select a design…": a load still in flight is dropped (load_design_state), so its
		// loading panel goes too.
		else $body.find("#we-design-state").empty();
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
	// frappe.call's own dialog says why a calculation failed; the last result stays.
	we_kit().then((sf) =>
		sf
			.call(WE_API + "run_calc", { calc: calc, inputs: JSON.stringify(inputs) }, { freeze: true })
			.then(
				(message) => render_result($body.find("#we-calc-result"), message),
				() => {}
			)
	);
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

// The list fills the picker; its loading, empty and error states show where a design's state
// goes, which is empty until one is picked. Each option is built with jQuery's val() and text(),
// so a design name with a quote or a "<" in it stays one option with its own value.
function load_design_list($body) {
	we_kit().then((sf) => {
		const $state = $body.find("#we-design-state");
		sf.state.render($state, "loading", { title: __("Loading designs…") });
		sf.call(WE_API + "get_design_state", {}, { quiet: true }).then(
			(message) => {
				const designs = (message || {}).designs || [];
				const $pick = $body.find("#we-design-pick");
				$pick.empty();
				$("<option></option>").val("").text(__("Select a design…")).appendTo($pick);
				designs.forEach((d) => {
					const label = `${d.name} — ${d.design_title || d.project || ""} (${d.status})`;
					$("<option></option>").val(d.name).text(label).appendTo($pick);
				});
				if (designs.length) {
					$state.empty();
				} else {
					sf.state.render($state, "empty", {
						title: __("No Water Feature Designs yet."),
						body: __("Start one with New Design."),
					});
				}
			},
			(err) => we_failed(sf, $state, err, __("Could not load the designs."), () => load_design_list($body))
		);
	});
}

// A reply is drawn only while the picker still shows the design it was asked for: pick one, then
// another before the first answers, and the first reply (or its error) must not paint over the
// second. A reply for a design no longer picked is dropped, since the later request owns the panel.
function load_design_state($body, design) {
	const picked = () => $body.find("#we-design-pick").val() === design;
	we_kit().then((sf) => {
		if (!picked()) return;
		const $state = $body.find("#we-design-state");
		sf.state.render($state, "loading", { title: __("Loading {0}…", [design]) });
		sf.call(WE_API + "get_design_state", { design }, { quiet: true }).then(
			(message) => {
				if (picked()) render_design_state($state, message);
			},
			(err) => {
				if (!picked()) return;
				we_failed(sf, $state, err, __("Could not load {0}.", [design]), () => load_design_state($body, design));
			}
		);
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

	// Design-health chips + the top issues (same typed payload the form's
	// Design Health panel renders) — a design with a red chip is findable here
	// without opening the form.
	const chips =
		(st.blocker_count ? `<span class="we-badge we-bad">${st.blocker_count} ${__("blockers")}</span> ` : "") +
		(st.warning_count ? `<span class="we-badge we-warn">${st.warning_count} ${__("warnings")}</span> ` : "") +
		(!st.blocker_count && !st.warning_count ? `<span class="we-badge we-ok">${__("No issues")}</span> ` : "") +
		(st.issue_ready ? `<span class="we-badge we-ok">${__("Package ready")}</span>` : "");
	const top_issues = (st.issues || [])
		.filter((i) => i.severity !== "info")
		.slice(0, 6)
		.map((i) => `<div class="we-issue we-issue-${esc(i.severity)}">${esc(i.title)}</div>`)
		.join("");

	// Itemized readiness (the two gates) — what's still owed, per section.
	const readiness = st.readiness || {};
	const ready_rows = (readiness.sections || [])
		.filter((s) => s.state !== "n/a" && s.state !== "complete")
		.map((s) => {
			const missing = (s.missing || []).map((m) => esc(m.label)).join("; ");
			return `<div class="we-ready-row">&#9675; <b>${esc(s.label)}</b>${missing ? ` — ${missing}` : ""}</div>`;
		})
		.join("");
	const needs = ready_rows
		? `<div class="we-warn"><b>${__("Still needed")}:</b>${ready_rows}</div>`
		: `<div class="we-ok-note">${__("All readiness items satisfied.")}</div>`;

	$el.html(`
		<div class="we-state">
			<div class="we-row">
				<b>${esc(st.design)}</b> — ${esc(st.design_title || "")}
				<span class="we-badge we-${st.completion_percent >= 100 ? "ok" : "warn"}">${st.completion_percent || 0}%</span>
				<button class="btn btn-default btn-xs" id="we-open-form">${__("Open in Form")}</button>
			</div>
			<div class="we-chips">${chips}</div>
			${top_issues}
			<table class="we-rollups">${roll}</table>
			${needs}
		</div>
	`);
	$el.find("#we-open-form").on("click", () => frappe.set_route("Form", "Water Feature Design", st.design));
}
