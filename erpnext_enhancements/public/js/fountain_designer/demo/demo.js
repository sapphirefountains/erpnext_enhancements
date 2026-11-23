// Demo mode (?demo=1): every designer call answered in the browser from the fixture in this folder, so
// the page can be shown, trained on and tested before phase E's api/designer.py exists, and on a site
// with no designs at all. Nothing here reaches the server, and nothing it "saves" outlives the tab
// (sessionStorage, when the browser allows it).
//
// It answers in the contract's shapes (README.md, "The contract"), and it is a little alive: the
// headline inputs scale the flows, heads, power and budget of the kit's reply, an issue whose first
// remedy the model now holds is cleared, an acknowledged warning counts as signed off, and the duty
// chart is redrawn at the scaled duty point. None of that is engineering; the label in the header says
// "Demo data".
//
// create_demo(fixture, env) -> transport(method, args) -> Promise. fixture: { boot, catalog, designs,
// replies: { <kit>: derive reply }, calc: { <calc>: { inputs, result } } }. env: { set_timeout, delay, storage }.

import { METHOD_BASE } from "../api.js";
import { clone, get_in, merge_derived, set_in } from "../model.js";

const STORE_KEY = "sf_fountain_designer_demo";

export function create_demo(fixture, env) {
	const e = env || {};
	const delay = e.delay == null ? 250 : e.delay;
	const wait = (value) =>
		new Promise((resolve) => (e.set_timeout ? e.set_timeout(() => resolve(value), delay) : resolve(value)));
	const fail = (status, message) =>
		wait(null).then(() => Promise.reject({ status, message, exc_type: status === 404 ? "DoesNotExistError" : "ValidationError", session_expired: false }));

	const store = load_store(e.storage, fixture);
	const persist = () => save_store(e.storage, store);
	let quotation_no = 0;
	let request_no = 0;

	const handlers = {
		// A demo design is not a record, so its papers have nothing to print: no print links.
		boot: () => Object.assign(clone(fixture.boot), { prints: [] }),
		get_design: (args) => {
			if (!args.name) {
				return {
					designs: Object.values(store.designs)
						.map((d) => list_row(d))
						.sort((a, b) => String(b.modified).localeCompare(String(a.modified))),
				};
			}
			const d = store.designs[args.name];
			if (!d) return null;
			return {
				name: d.name,
				design_title: d.design_title,
				mode: d.mode,
				status: d.status,
				kit: d.model.kit,
				project: d.project || null,
				opportunity: d.opportunity || null,
				customer: d.customer || null,
				docstatus: 0,
				can_write: true,
				modified: d.modified,
				model: clone(d.model),
			};
		},
		derive: (args) => derive(fixture, parse(args.model_json)),
		save_design: (args) => {
			const model = parse(args.model_json);
			const fields = parse(args.fields_json) || {};
			if (!model || model.schema !== 2) return { _error: [417, "The design model is not schema 2."] };
			let name = args.name;
			if (name && !store.designs[name]) return { _error: [404, `Design ${name} was not found.`] };
			if (!name) {
				store.seq += 1;
				name = "DEMO-NEW-" + String(store.seq).padStart(3, "0");
			}
			const reply = derive(fixture, model);
			const d = Object.assign(store.designs[name] || { name }, {
				design_title: fields.design_title || (store.designs[name] && store.designs[name].design_title) || name,
				project: fields.project !== undefined ? fields.project : store.designs[name] && store.designs[name].project,
				opportunity: fields.opportunity !== undefined ? fields.opportunity : store.designs[name] && store.designs[name].opportunity,
				customer: fields.customer !== undefined ? fields.customer : store.designs[name] && store.designs[name].customer,
				mode: model.mode === "full" ? "Full" : "Quick",
				status: "Derived",
				model,
				rollups: reply.rollups,
				modified: now_stamp(),
			});
			store.designs[name] = d;
			persist();
			return { name, status: d.status, mode: d.mode, modified: d.modified, design_title: d.design_title };
		},
		push_quotation: (args) => {
			const d = store.designs[args.name];
			if (!d) return { _error: [404, `Design ${args.name} was not found.`] };
			const bom = derive(fixture, d.model).bom || [];
			quotation_no += 1;
			return push_reply("Quotation", "DEMO-QTN-" + String(quotation_no).padStart(4, "0"), bom);
		},
		push_material_request: (args) => {
			const d = store.designs[args.name];
			if (!d) return { _error: [404, `Design ${args.name} was not found.`] };
			const bom = derive(fixture, d.model).bom || [];
			request_no += 1;
			return push_reply("Material Request", "DEMO-MR-" + String(request_no).padStart(4, "0"), bom);
		},
		catalog_search: (args) => {
			const rows = (fixture.catalog && fixture.catalog[args.equipment_class]) || [];
			const q = String(args.q || "").toLowerCase().trim();
			return rows
				.filter((r) => !q || [r.item, r.item_name, r.description].some((t) => String(t || "").toLowerCase().includes(q)))
				.map((r) => Object.assign({ equipment_class: args.equipment_class }, r));
		},
		// A demo design is not a record: nothing to print, so nothing to email.
		email_draft: (args) => {
			if (!store.designs[args.name]) return { _error: [404, `Design ${args.name} was not found.`] };
			return { demo: true, subject: "", recipients: "", message: "", print_format: args.print_format || null, formats: [], attachment: null };
		},
		// The calculator cannot run the engine in a browser: the fixture holds a real answer for a few calcs at
		// example inputs (calc.json), returned with those inputs whatever was typed, and said so on the page.
		run_calc: (args) => {
			const example = fixture.calc && fixture.calc[args.calc];
			if (!example) {
				const have = Object.keys(fixture.calc || {}).join(", ");
				return { _error: [417, `In the demo the calculator only shows worked examples (${have}). Open a real design to run the others.`] };
			}
			return Object.assign(clone(example.result), { demo_inputs: clone(example.inputs) });
		},
		acknowledge_issue: (args) => {
			const d = store.designs[args.design];
			if (!d) return { _error: [404, `Design ${args.design} was not found.`] };
			const issue = (derive(fixture, d.model).issues || []).find((i) => i.key === args.issue_key);
			if (!issue) return { _error: [417, `Issue ${args.issue_key} is not open on this design (it may have cleared — reload).`] };
			if (issue.severity !== "warning") return { _error: [417, "Only a warning can be acknowledged."] };
			const acks = (d.model.acks || []).filter((a) => a.issue_key !== args.issue_key);
			acks.push({ issue_key: args.issue_key, note: args.note || "", by: "demo@example.com", on: now_stamp() });
			d.model = set_in(d.model, "acks", acks);
			persist();
			return { acks: clone(acks) };
		},
	};

	return function transport(method, args) {
		const name = String(method || "").startsWith(METHOD_BASE) ? method.slice(METHOD_BASE.length) : null;
		const handler = name && handlers[name];
		if (!handler) return fail(404, `The demo has no answer for ${method}.`);
		let out;
		try {
			out = handler(args || {});
		} catch (err) {
			return fail(500, "The demo could not answer.");
		}
		if (out && out._error) return fail(out._error[0], out._error[1]);
		if (out === null) return fail(404, `Design ${args && args.name} was not found.`);
		return wait(out);
	};
}

function parse(value) {
	if (value == null) return null;
	if (typeof value !== "string") return value;
	try {
		return JSON.parse(value);
	} catch (e) {
		return null;
	}
}

function now_stamp() {
	const d = new Date();
	const p = (n) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function list_row(d) {
	const roll = d.rollups || {};
	return {
		name: d.name,
		design_title: d.design_title,
		kit: d.model && d.model.kit,
		mode: d.mode,
		status: d.status,
		family: d.model && d.model.features && d.model.features[0] ? d.model.features[0].family : null,
		project: d.project || null,
		opportunity: d.opportunity || null,
		customer: d.customer || null,
		blocker_count: roll.blocker_count != null ? roll.blocker_count : d.blocker_count || 0,
		warning_count: roll.warning_count != null ? roll.warning_count : d.warning_count || 0,
		issue_ready: roll.issue_ready != null ? (roll.issue_ready ? 1 : 0) : d.issue_ready || 0,
		budget_total: roll.budget_total != null ? roll.budget_total : d.budget_total || 0,
		modified: d.modified,
	};
}

function push_reply(doctype, name, bom) {
	const lines = bom.filter((l) => l.item).length;
	return { doctype, name, url: null, demo: true, lines, text_lines: bom.length - lines };
}

// The three fixture designs, each with its kit's starter model, plus whatever this tab saved.
function load_store(storage, fixture) {
	const store = { seq: 0, designs: {} };
	const kits = {};
	for (const kit of (fixture.boot && fixture.boot.kits) || []) kits[kit.key] = kit;
	for (const row of (fixture.designs && fixture.designs.designs) || []) {
		const kit = kits[row.kit];
		const model = clone(kit ? kit.model : {});
		model.mode = row.mode === "Full" ? "full" : "quick";
		store.designs[row.name] = Object.assign({}, row, { model });
	}
	try {
		const saved = storage ? JSON.parse(storage.getItem(STORE_KEY) || "null") : null;
		if (saved && saved.designs) {
			store.seq = saved.seq || 0;
			Object.assign(store.designs, saved.designs);
		}
	} catch (e) {
		// Private mode or a full store: the demo starts from the fixture.
	}
	return store;
}

function save_store(storage, store) {
	try {
		if (storage) storage.setItem(STORE_KEY, JSON.stringify(store));
	} catch (e) {
		// Not remembered past this page; the demo still works.
	}
}

// --- the derive answer ------------------------------------------------------------------------------

export function derive(fixture, model) {
	const sent = model && typeof model === "object" ? model : {};
	const base = fixture.replies && fixture.replies[sent.kit];
	if (!base) return blank_reply(sent);
	const kit = ((fixture.boot && fixture.boot.kits) || []).find((k) => k.key === sent.kit);
	const reply = clone(base);
	reply.model = merge_derived(sent, base.model);
	const f = factors(kit, sent);
	scale(reply, f);
	apply_remedies_and_acks(reply, reply.model);
	if (f.flow !== 1 || f.head !== 1) {
		const r = reply.rollups;
		reply.charts.duty_svg = duty_svg(r.operating_flow_gpm, r.operating_head_ft);
	}
	return reply;
}

// How far the headline inputs are from the kit's own: the first number scales the flow, the second the
// head, and the look steps the flow a quarter per option from the kit's.
export function factors(kit, model) {
	const out = { flow: 1, head: 1 };
	if (!kit || !kit.model) return out;
	const numbers = (kit.headline || []).filter((h) => h.kind === "number" || h.kind === "int");
	const ratio = (h) => {
		const mine = Number(get_in(model, h.path));
		const theirs = Number(get_in(kit.model, h.path));
		return Number.isFinite(mine) && mine > 0 && Number.isFinite(theirs) && theirs > 0 ? mine / theirs : 1;
	};
	if (numbers[0]) out.flow = ratio(numbers[0]);
	if (numbers[1]) out.head = ratio(numbers[1]);
	const look = (kit.headline || []).find((h) => h.kind === "tiles");
	if (look) {
		const values = (look.options || []).map((o) => o.value);
		const at = values.indexOf(get_in(model, look.path));
		const home = values.indexOf(get_in(kit.model, look.path));
		if (at >= 0 && home >= 0) out.flow *= 1 + 0.25 * (at - home);
	}
	return out;
}

function round(n, digits) {
	const p = Math.pow(10, digits);
	return Math.round(n * p) / p;
}

function scale(reply, f) {
	if (f.flow === 1 && f.head === 1) return;
	const r = reply.rollups;
	const head = 0.5 + 0.5 * f.head;
	for (const key of ["design_flow_gpm", "operating_flow_gpm"]) if (r[key] != null) r[key] = round(r[key] * f.flow, 0);
	for (const key of ["tdh_ft", "operating_head_ft"]) if (r[key] != null) r[key] = round(r[key] * head, 1);
	if (r.pump_kw != null) r.pump_kw = round(r.pump_kw * f.flow * head, 2);
	if (r.om_cost_per_year != null) r.om_cost_per_year = round(r.om_cost_per_year * (0.6 + 0.4 * f.flow), 0);
	const k = 0.5 + 0.5 * f.flow;
	for (const line of reply.bom || []) {
		if (line.section === "Labor" || line.section === "Basin") continue;
		line.rate = round(line.rate * k, 2);
		line.amount = round(line.qty * line.rate, 2);
	}
	// The demo carries no contingency, so its budget is the BOM's sum (README, rollups).
	r.budget_total = r.bom_subtotal = round((reply.bom || []).reduce((sum, l) => sum + l.amount, 0), 2);
	const feature = reply.values && reply.values.features && reply.values.features.f1;
	if (feature && feature.flow_gpm != null) feature.flow_gpm = round(feature.flow_gpm * f.flow, 0);
	const pump = reply.values && reply.values.pumps && reply.values.pumps.p1;
	if (pump) {
		pump.flow_gpm = r.operating_flow_gpm;
		pump.head_ft = r.operating_head_ft;
		pump.kw = r.pump_kw;
	}
}

// An issue is cleared once the model holds every value of its first remedy; a warning with an ack in
// model.acks is signed off. The counts, the verdict and the package gate follow.
function apply_remedies_and_acks(reply, model) {
	const acks = {};
	for (const a of model.acks || []) if (a && a.issue_key) acks[a.issue_key] = a;
	reply.issues = (reply.issues || []).filter((issue) => {
		const fix = issue.remedies && issue.remedies[0] && issue.remedies[0].set;
		if (!fix) return true;
		return !Object.entries(fix).every(([path, value]) => JSON.stringify(get_in(model, path)) === JSON.stringify(value));
	});
	for (const issue of reply.issues) {
		const a = issue.severity === "warning" ? acks[issue.key] : null;
		issue.acknowledged = a ? { note: a.note, by: a.by, on: a.on } : null;
	}
	const blockers = reply.issues.filter((i) => i.severity === "blocker").length;
	const warnings = reply.issues.filter((i) => i.severity === "warning").length;
	const open = reply.issues.filter((i) => i.severity === "warning" && !i.acknowledged).length;
	const r = reply.rollups;
	r.blocker_count = blockers;
	r.warning_count = warnings;
	r.issue_ready = !blockers && !open;
	reply.verdict = Object.assign({}, reply.verdict, {
		package_ready: r.issue_ready,
		level: blockers ? "bad" : warnings && open ? "warn" : "good",
		headline: blockers
			? blockers === 1
				? "Not buildable yet: one code blocker."
				: `Not buildable yet: ${blockers} blockers.`
			: open
				? open === 1
					? "Works, with one warning to sign off."
					: `Works, with ${open} warnings to sign off.`
				: "Good to quote.",
	});
	if (!reply.readiness) return;
	reply.readiness.issue_ready = r.issue_ready;
	// A section whose blocker or warning has gone is ready; the package row says what is still open.
	const live = (section) => reply.issues.some((i) => i.section === section && (i.severity === "blocker" || (i.severity === "warning" && !i.acknowledged)));
	for (const s of reply.readiness.sections || []) {
		if (s.key === "deliverables") {
			const missing = [];
			if (blockers) missing.push(blockers === 1 ? "Resolve 1 blocking issue" : `Resolve ${blockers} blocking issues`);
			if (open) missing.push(open === 1 ? "Acknowledge 1 open warning" : `Acknowledge ${open} open warnings`);
			s.status = missing.length ? "missing" : "ok";
			s.missing = missing;
		} else if ((s.status === "blocked" || s.status === "warn") && !live(s.key)) {
			s.status = "ok";
			s.missing = [];
		}
	}
}

function blank_reply(model) {
	const features = (model && model.features) || [];
	return {
		model: clone(model),
		results: {},
		values: {},
		rollups: {
			design_flow_gpm: null,
			operating_flow_gpm: null,
			tdh_ft: null,
			operating_head_ft: null,
			selected_pump: null,
			pump_kw: null,
			total_basin_gallons: null,
			budget_total: null,
			om_cost_per_year: null,
			blocker_count: 0,
			warning_count: 0,
			completion_percent: 0,
			issue_ready: false,
		},
		issues: [
			{
				key: "DEMO_NO_KIT",
				code: "DEMO_NO_KIT",
				severity: "info",
				title: features.length ? "The demo only works out its three kits" : "Add a feature to start",
				detail: "Demo data: a design from the lobby wall, plaza or pond kit shows the full verdict.",
				section: "features",
				fix_hint: "",
				citation: "",
				remedies: [],
				acknowledged: null,
			},
		],
		readiness: { sections: [], calc_ready: false, issue_ready: false },
		verdict: { level: "warn", headline: "Not worked out yet.", package_ready: false },
		bom: [],
		charts: { duty_svg: null, schematic_svg: null, oneline_svg: null },
		outputs: { schedules: [], packet: null, om: null, winterization: null, startup: [] },
	};
}

// The same drawing as the fixture's, at a new duty point (a pump curve and a system curve through it).
export function duty_svg(q, h) {
	if (!(q > 0 && h > 0)) return null;
	const W = 480;
	const H = 300;
	const L = 52;
	const R = 16;
	const T = 28;
	const B = 44;
	const h0 = h * 1.6;
	const hs = h * 0.35;
	const qmax = Math.ceil((q * 1.55) / 10) * 10;
	const hmax = Math.ceil((h0 * 1.15) / 10) * 10;
	const a = (h0 - h) / (q * q);
	const b = (h - hs) / (q * q);
	const f = (n) => String(Math.round(n * 10) / 10);
	const sx = (x) => L + ((W - L - R) * x) / qmax;
	const sy = (y) => T + (H - T - B) * (1 - y / hmax);
	const pump = [];
	const system = [];
	for (let i = 0; i <= 40; i += 1) {
		const x = (qmax * i) / 40;
		const hp = h0 - a * x * x;
		if (hp >= 0) pump.push(`${f(sx(x))},${f(sy(hp))}`);
		const hy = hs + b * x * x;
		if (hy <= hmax) system.push(`${f(sx(x))},${f(sy(hy))}`);
	}
	const title = `Duty point: ${f(q)} gpm at ${f(h)} ft (demo)`;
	const grid = [];
	for (let i = 0; i <= 5; i += 1) {
		const y = f(sy((hmax * i) / 5));
		grid.push(`<line class="fd-grid" x1="${L}" y1="${y}" x2="${W - R}" y2="${y}" stroke="currentColor" stroke-opacity="0.15"/>`);
		grid.push(`<text class="fd-label" x="${L - 6}" y="${y}" text-anchor="end" dominant-baseline="middle" font-size="11" fill="currentColor">${f((hmax * i) / 5)}</text>`);
		grid.push(`<text class="fd-label" x="${f(sx((qmax * i) / 5))}" y="${H - B + 16}" text-anchor="middle" font-size="11" fill="currentColor">${f((qmax * i) / 5)}</text>`);
	}
	return (
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}"><title>${title}</title>` +
		grid.join("") +
		`<line class="fd-axis" x1="${L}" y1="${T}" x2="${L}" y2="${H - B}" stroke="currentColor"/>` +
		`<line class="fd-axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}" stroke="currentColor"/>` +
		`<text class="fd-label" x="${(L + W - R) / 2}" y="${H - 8}" text-anchor="middle" font-size="12" fill="currentColor">Flow (gpm)</text>` +
		`<polyline class="fd-curve-pump" data-series="pump" points="${pump.join(" ")}" fill="none" stroke="currentColor" stroke-width="2.5"/>` +
		`<polyline class="fd-curve-system" data-series="system" points="${system.join(" ")}" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="6 4"/>` +
		`<circle class="fd-point" data-point="duty" cx="${f(sx(q))}" cy="${f(sy(h))}" r="6" fill="currentColor"/>` +
		`<text class="fd-label fd-point-label" x="${f(sx(q) + 10)}" y="${f(sy(h) - 10)}" font-size="12" font-weight="700" fill="currentColor">${f(q)} gpm @ ${f(h)} ft</text>` +
		`</svg>`
	);
}
