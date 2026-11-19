/**
 * The Estimate's lines editor (milestone 1 of the estimating build; the v2 "Estimate2" mockup).
 *
 * Mounted by the Estimate form script (estimating/doctype/estimate/estimate.js) into the form's
 * `lines_editor` HTML field. It edits frm.doc.lines and frm.doc.phases through frappe.model.set_value,
 * so saving, the dirty flag, the timeline and permissions are all the form's own; the table view
 * underneath shows the same rows.
 *
 * What it is careful about:
 *
 *   - Phones (Nik, 2026-11-06: every screen fully usable on a phone). Below 720px of its own width the
 *     line grid becomes labelled cards, inputs are 44px tall with a 16px font (smaller makes iOS zoom),
 *     and Save / Issue quotation stick to the bottom.
 *   - Typing never loses a tap. An edit updates the numbers in place; when it must redraw (a line moves
 *     phase, a type changes) it redraws on the next tick and puts the cursor back where it went, so the
 *     field you tapped while the last one was blurring still gets the focus.
 *   - The numbers are a preview. estimate_math.js is the twin of the server's engine, held to the same
 *     cases; the server prices again on save and its numbers are the ones stored.
 *   - Cost, markup and margin draw only for someone who can read the cost fields (permlevel 1), so the
 *     same editor is safe the day sales or project roles are given read access.
 *   - Nothing typed is ever inserted as HTML: every description, code and label goes through esc().
 */

import * as em from "./estimate_math.js";

/** Default phase labels; the twin of rules.DEFAULT_PHASE_LABELS (the node suite holds them equal). */
export const DEFAULT_PHASE_LABELS = {
	100: "Design and planning",
	110: "Predesign",
	120: "Schematic design",
	130: "Design development",
	140: "Construction documents",
	200: "Groundwork and prep",
	300: "Rough-in",
	400: "Structural vessel",
	500: "Mechanical room",
	600: "Controls and electrical",
	700: "Features and finishes",
	800: "Maintenance",
};

const BASIS = ["", "Lump sum", "Catalog", "Quote", "Historical", "Allowance", "Parametric", "Design"];
const OVERRIDES = [
	["markup_override", "Markup %"],
	["fee_override", "Fee %"],
	["contingency_override", "Contingency %"],
	["insurance_override", "Insurance %"],
	["commission_override", "Commission %"],
];
const RATE_FIELDS = [
	"labor_rate",
	"tax_pct",
	"markup_material",
	"markup_equipment",
	"markup_labor",
	"markup_subcontract",
	"markup_expense",
	"contingency_pct",
	"insurance_pct",
	"fee_pct",
	"commission_pct",
];
export const OPEN_ALL_UNDER = 14;

const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
const money = (n) =>
	(Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (n) => `${Math.round((Number(n) || 0) * 10) / 10}%`;
const qtyText = (n) => String(Math.round((Number(n) || 0) * 1000) / 1000);

export function phase_label(frm, number) {
	const row = (frm.doc.phases || []).find((p) => em.phase_number(p.phase_number) === number);
	return (row && row.label) || DEFAULT_PHASE_LABELS[number] || (number ? `Phase ${number}` : "Lines");
}

/** Phases to draw, top level first with their sub-phases after, from phase rows and line phases. */
export function phase_tree(phases, lines) {
	const numbers = new Set();
	for (const p of phases || []) {
		const n = em.phase_number(p.phase_number);
		if (n) em.phase_chain(n).forEach((x) => numbers.add(x));
	}
	let unphased = false;
	for (const l of lines || []) {
		const n = em.phase_number(l.phase_number);
		if (n) em.phase_chain(n).forEach((x) => numbers.add(x));
		else unphased = true;
	}
	const tops = [...numbers].filter((n) => em.top_phase(n) === n).sort((a, b) => a - b);
	const tree = tops.map((top) => ({
		number: top,
		children: [...numbers].filter((n) => n !== top && em.top_phase(n) === top).sort((a, b) => a - b),
	}));
	if (unphased || !tree.length) tree.push({ number: 0, children: [] });
	return tree;
}

function state(frm) {
	// Kept on the form object, which Desk reuses for every Estimate opened in the tab, so it starts over
	// when the document changes (after New Revision, say) instead of carrying one estimate's folds to the next.
	const s = frm.__estimate_editor;
	if (!s || s.doc !== frm.doc.name) {
		frm.__estimate_editor = { doc: frm.doc.name, open: null, more: new Set(), codes: s ? s.codes : null };
	}
	return frm.__estimate_editor;
}

function canCost(frm) {
	return !!(frm.perm && frm.perm[1] && frm.perm[1].read);
}

function priced(frm) {
	return em.price_estimate(frm.doc, frm.doc.phases || [], frm.doc.lines || []);
}

function lineResults(frm) {
	const p = priced(frm);
	const byName = {};
	(frm.doc.lines || []).forEach((l, i) => (byName[l.name] = p.lines[i]));
	return { p, byName };
}

async function loadCodes(frm) {
	const s = state(frm);
	if (s.codes) return s.codes;
	if (window.__sf_estimate_codes) return (s.codes = window.__sf_estimate_codes);
	try {
		const r = await frappe.call({ method: "erpnext_enhancements.api.estimating.cost_codes", type: "GET" });
		s.codes = window.__sf_estimate_codes = r.message || [];
	} catch (e) {
		s.codes = [];
	}
	return s.codes;
}

// ------------------------------------------------------------------------------------------ drawing

function lineRow(frm, line, result, cost) {
	const k = (f) => `${line.name}:${f}`;
	const ro = !frm.__estimate_editable;
	const dis = ro ? " disabled" : "";
	const types = em.COST_TYPES.map(
		(t) => `<option${t === line.cost_type ? " selected" : ""}>${esc(t)}</option>`
	).join("");
	const basis = BASIS.map(
		(b) => `<option value="${esc(b)}"${b === (line.basis || "") ? " selected" : ""}>${esc(b || "Basis")}</option>`
	).join("");
	const byOthers = !!line.by_others;
	const needs = result && result.needs_cost;
	const more = state(frm).more.has(line.name);
	const tag = byOthers ? "BY OTHERS" : String(line.cost_type || "").toUpperCase();
	const flags = [
		line.basis === "Allowance" ? "allowance" : "",
		line.basis && line.basis !== "Allowance" ? String(line.basis).toLowerCase() : "",
	]
		.filter(Boolean)
		.join(" · ");

	let costCell = "";
	if (cost) {
		costCell = `<span class="n cst"><span class="lab">Unit cost</span>${
			byOthers
				? ""
				: `<input type="number" inputmode="decimal" min="0" step="0.01" aria-label="Unit cost" data-key="${k(
						"unit_cost"
				  )}" data-field="unit_cost" class="${needs ? "need" : ""}" value="${
						line.unit_cost ? esc(line.unit_cost) : ""
				  }" placeholder="needed"${dis}>`
		}<button type="button" class="btn-fill" data-act="fill" data-row="${esc(line.name)}"${
			needs && line.sheet_price && !ro ? "" : " hidden"
		}>FROM PRICE</button></span>`;
	}
	const moreBlock = more
		? `<div class="more">
			<label><span>Phase</span><input type="number" inputmode="numeric" min="0" step="10" data-key="${k(
				"phase_number"
			)}" data-field="phase_number" value="${esc(em.phase_number(line.phase_number) || "")}" placeholder="0"${dis}></label>
			<label><span>Sheet price</span><input type="number" inputmode="decimal" min="0" step="0.01" data-key="${k(
				"sheet_price"
			)}" data-field="sheet_price" value="${line.sheet_price ? esc(line.sheet_price) : ""}"${dis}></label>
			<label><span>Unit</span><input type="text" data-key="${k("uom")}" data-field="uom" value="${esc(
				line.uom || ""
			)}"${dis}></label>
			${
				cost
					? OVERRIDES.map(
							([f, label]) =>
								`<label><span>${esc(label)}</span><input type="text" inputmode="decimal" data-key="${k(
									f
								)}" data-field="${f}" value="${esc(line[f] || "")}" placeholder="${esc(
									inheritedRate(frm, line, f)
								)}"${dis}></label>`
					  ).join("")
					: ""
			}
			<label class="chk"><input type="checkbox" data-key="${k("by_others")}" data-field="by_others"${
				byOthers ? " checked" : ""
			}${dis}><span>By others (not billed)</span></label>
			${ro ? "" : `<button type="button" class="btn sm danger" data-act="delete" data-row="${esc(line.name)}">REMOVE LINE</button>`}
		</div>`
		: "";

	return `<div class="row${byOthers ? " oth" : ""}${line.basis === "Design" ? " design" : ""}" data-row="${esc(
		line.name
	)}">
		<span class="desc"><input type="text" class="desc-in" aria-label="Description" data-key="${k(
			"description"
		)}" data-field="description" value="${esc(line.description || "")}" placeholder="Describe the line"${dis}>
			<span class="sub">
				<span class="tag t-${esc(String(line.cost_type || "").toLowerCase())}${byOthers ? " t-oth" : ""}">${esc(tag)}</span>
				<select aria-label="Cost type" data-key="${k("cost_type")}" data-field="cost_type"${dis}>${types}</select>
				<input type="text" class="code" list="sf-est-codes" aria-label="Cost code" data-key="${k(
					"cost_code"
				)}" data-field="cost_code" value="${esc(line.cost_code || "")}" placeholder="cost code"${dis}>
				<select aria-label="Basis" data-key="${k("basis")}" data-field="basis"${dis}>${basis}</select>
				${flags ? `<span class="flag">${esc(flags)}</span>` : ""}
			</span>
		</span>
		<span class="q"><span class="lab">Qty</span>${
			byOthers
				? ""
				: `<input type="number" inputmode="decimal" min="0" step="any" aria-label="Quantity" data-key="${k(
						"qty"
				  )}" data-field="qty" value="${esc(qtyText(line.qty))}"${dis}><small>${esc(line.uom || "")}</small>`
		}</span>
		${costCell}
		${cost ? `<span class="n"><span class="lab">Markup</span><span data-cell="markup"></span></span>` : ""}
		<span class="n"><span class="lab">Unit price</span><span data-cell="unit_price"></span></span>
		<span class="n"><span class="lab">Line total</span><b data-cell="amount"></b></span>
		${cost ? `<span class="n"><span class="lab">Margin</span><span data-cell="margin"></span></span>` : ""}
		<span class="act"><button type="button" class="btn-more" data-act="more" data-row="${esc(
			line.name
		)}" aria-expanded="${more}" title="More">${more ? "LESS" : "MORE"}</button></span>
		${moreBlock}
	</div>`;
}

function inheritedRate(frm, line, field) {
	const name = field.replace("_override", "");
	const phases = {};
	for (const p of frm.doc.phases || []) phases[em.phase_number(p.phase_number)] = p;
	const probe = Object.assign({}, line, { [field]: "" });
	try {
		const v = name === "markup" ? em.resolve_markup(probe, frm.doc) : em.resolve(name, probe, phases, frm.doc);
		return `${v}`;
	} catch (e) {
		return "";
	}
}

function header(cost) {
	return `<div class="row hd"><span class="l">Description</span><span>Qty</span>${
		cost ? "<span>Unit cost</span><span>Markup</span>" : ""
	}<span>Unit price</span><span>Line total</span>${cost ? "<span>Margin</span>" : ""}<span></span></div>`;
}

function phaseBlock(frm, number, nested, linesHere, cost) {
	const s = state(frm);
	const open = s.open.has(number);
	const ro = !frm.__estimate_editable;
	const title = number ? `${number} ${phase_label(frm, number)}` : phase_label(frm, 0);
	let body = "";
	if (open) {
		body =
			(linesHere.length ? header(cost) : "") +
			linesHere.map((l) => lineRow(frm, l, null, cost)).join("") +
			(ro
				? ""
				: `<div class="addrow"><button class="btn sm" type="button" data-act="add" data-phase="${number}">+ ADD LINE${
						number ? ` TO ${number}` : ""
				  }</button></div>`);
	}
	return `<div class="phase${nested ? " nested" : ""}" data-phase="${number}">
		<button type="button" class="ph" data-act="toggle" data-phase="${number}" aria-expanded="${open}">
			<svg viewBox="0 0 20 20" aria-hidden="true" style="transform:${open ? "rotate(90deg)" : "none"}"><path d="M7 4l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2"></path></svg>
			<span class="t">${esc(title)}</span>
			<span class="s" data-phase-sum="${number}"></span>
			<span class="a" data-phase-amt="${number}"></span>
		</button>
		${body}
	</div>`;
}

function draw(frm) {
	const wrap = frm.fields_dict.lines_editor && frm.fields_dict.lines_editor.$wrapper;
	if (!wrap) return;
	const s = state(frm);
	const cost = canCost(frm);
	const lines = frm.doc.lines || [];
	const tree = phase_tree(frm.doc.phases, lines);
	frm.__estimate_editable = !!(frm.doc.status !== "Superseded" && frm.perm && frm.perm[0] && frm.perm[0].write);

	if (s.open === null) {
		// Collapsed by default except phases with work to do (Nik, 2026-11-06); a short estimate opens whole.
		s.open = new Set();
		const p = priced(frm);
		for (const node of tree) {
			for (const n of [node.number, ...node.children]) {
				const b = p.phases[String(n)];
				if (lines.length < OPEN_ALL_UNDER || (b && b.missing_cost) || !b) s.open.add(n);
			}
		}
	}

	const byPhase = {};
	for (const l of lines) (byPhase[em.phase_number(l.phase_number)] = byPhase[em.phase_number(l.phase_number)] || []).push(l);
	const blocks = tree
		.map(
			(node) =>
				phaseBlock(frm, node.number, false, byPhase[node.number] || [], cost) +
				node.children.map((c) => phaseBlock(frm, c, true, byPhase[c] || [], cost)).join("")
		)
		.join("");

	const zeroRates = cost && !RATE_FIELDS.some((f) => Number(frm.doc[f]));
	const ro = !frm.__estimate_editable;
	const html = `<div class="sf-est${cost ? "" : " nocost"}">
		${
			zeroRates
				? `<div class="banner warn"><span><b>Every rate on this estimate is zero</b>, so prices equal cost. Set the markups and overheads under Rates below, or on the rate card${
						frm.doc.rate_card
							? ` <a href="/desk/estimate-rate-card/${encodeURIComponent(frm.doc.rate_card)}">${esc(frm.doc.rate_card)}</a>`
							: ""
				  } for every new estimate.</span></div>`
				: ""
		}
		${frm.doc.status === "Superseded" ? `<div class="banner"><span><b>Superseded.</b> This revision stays as it was quoted; change the newer one.</span></div>` : ""}
		<div class="cols">
			<div class="main">
				<div class="card lines-card">
					<div class="cardhead">
						<h2>Lines</h2>
						<span class="muted" data-cell="linecount"></span>
						<span class="tools">
							${ro ? "" : `<button class="btn sm" type="button" data-act="template">APPLY TEMPLATE</button>`}
							${ro ? "" : `<button class="btn sm" type="button" data-act="addphase">+ PHASE</button>`}
						</span>
					</div>
					<div class="banner fillall" data-cell="fillall" hidden><span data-cell="filltext"></span>
						<button class="btn sm amber" type="button" data-act="fillall">BACK-FILL ALL FROM PRICE</button></div>
					${blocks}
					<div class="ph total"><span class="sp"></span><span class="t">All phases</span><span class="s" data-cell="allsum"></span><span class="a" data-cell="alltotal"></span></div>
				</div>
			</div>
			<div class="side">
				<div class="card" data-cell="totals"></div>
				<div class="card" data-cell="ready"></div>
			</div>
		</div>
		${
			ro
				? ""
				: `<div class="bar"><span class="bar-total" data-cell="bartotal"></span>
			<button class="btn" type="button" data-act="save">SAVE</button>
			<button class="btn primary" type="button" data-act="issue">ISSUE QUOTATION</button></div>`
		}
		<datalist id="sf-est-codes">${(s.codes || [])
			.map((c) => `<option value="${esc(c.code)}">${esc(c.title)}</option>`)
			.join("")}</datalist>
	</div>`;
	wrap.html(html);
	bind(frm, wrap.get(0));
	numbers(frm);
}

/** Update every computed figure in place; no inputs are touched, so focus and typing survive. */
function numbers(frm) {
	const wrap = frm.fields_dict.lines_editor && frm.fields_dict.lines_editor.$wrapper;
	if (!wrap) return;
	const root = wrap.get(0);
	const cost = canCost(frm);
	const { p, byName } = lineResults(frm);
	const set = (sel, text, scope = root) => {
		const el = scope.querySelector(sel);
		if (el) el.textContent = text;
	};

	for (const line of frm.doc.lines || []) {
		const row = root.querySelector(`.row[data-row="${CSS.escape(line.name)}"]`);
		const r = byName[line.name];
		if (!row || !r) continue;
		const byOthers = !!line.by_others;
		set('[data-cell="amount"]', byOthers ? "not billed" : r.amount ? money(r.amount) : "[PRICE]", row);
		set('[data-cell="unit_price"]', byOthers ? "by others" : r.unit_price ? money(r.unit_price) : "—", row);
		if (cost) {
			set('[data-cell="markup"]', byOthers ? "" : r.priced ? pct(r.markup_pct + r.contingency_pct + r.insurance_pct + r.fee_pct) : "price only", row);
			set('[data-cell="margin"]', byOthers ? "" : r.priced ? pct(r.margin_pct) : "—", row);
			const input = row.querySelector('input[data-field="unit_cost"]');
			if (input) input.classList.toggle("need", !!r.needs_cost);
			const fill = row.querySelector('[data-act="fill"]');
			if (fill) fill.hidden = !(r.needs_cost && Number(line.sheet_price) && frm.__estimate_editable);
		}
	}
	for (const [number, b] of Object.entries(p.phases)) {
		const amt = root.querySelector(`[data-phase-amt="${number}"]`);
		if (amt) amt.textContent = money(b.amount);
		const sum = root.querySelector(`[data-phase-sum="${number}"]`);
		if (sum) {
			const lines = `${b.line_count} ${b.line_count === 1 ? "line" : "lines"}`;
			sum.textContent = b.missing_cost
				? `${lines} · ${b.missing_cost} without cost`
				: cost
				? `${lines} · cost ${money(b.cost_amount)}`
				: lines;
		}
	}
	root.querySelectorAll("[data-phase-amt]").forEach((el) => {
		if (!p.phases[el.dataset.phaseAmt]) el.textContent = money(0);
	});

	const t = p.totals;
	set('[data-cell="linecount"]', `${t.line_count} ${t.line_count === 1 ? "line" : "lines"} · a dashed cost is still needed`);
	set('[data-cell="alltotal"]', money(t.amount));
	set('[data-cell="bartotal"]', money(t.amount));
	set('[data-cell="allsum"]', cost ? `cost ${money(t.cost_amount)} · margin ${t.priced_amount ? pct(t.margin_pct) : "—"}` : "");

	const fillable = (frm.doc.lines || []).filter((l) => byName[l.name] && byName[l.name].needs_cost && Number(l.sheet_price));
	const fillBanner = root.querySelector('[data-cell="fillall"]');
	if (fillBanner) {
		fillBanner.hidden = !(cost && fillable.length && frm.__estimate_editable);
		set(
			'[data-cell="filltext"]',
			`${fillable.length} ${fillable.length === 1 ? "line carries" : "lines carry"} a sheet price but no cost. Back-fill divides the price by the line's rates so the margin shows; adjust any line after.`
		);
	}

	const totals = root.querySelector('[data-cell="totals"]');
	if (totals) {
		totals.innerHTML = `<h2>Totals</h2><div class="kv">
			<span class="k">Price, all lines</span><span class="v big">${money(t.amount)}</span>
			${
				cost
					? `<span class="k">Cost, priced lines</span><span class="v">${money(t.cost_amount)}</span>
			<span class="k">Margin, priced lines</span><span class="v">${t.priced_amount ? `${money(t.margin_amount)} · ${pct(t.margin_pct)}` : "—"}</span>
			<span class="k">Commission, internal</span><span class="v">${money(t.commission_amount)}</span>
			<span class="k">Margin after commission</span><span class="v">${t.priced_amount ? `${money(t.net_margin_amount)} · ${pct(t.net_margin_pct)}` : "—"}</span>`
					: ""
			}
			<span class="k">Lines without a cost yet</span><span class="v ${t.missing_cost ? "bad" : "good"}">${t.missing_cost}</span>
		</div>${
			cost
				? `<div class="muted">Markup by cost type, then contingency ${esc(frm.doc.contingency_pct || 0)}%, insurance ${esc(
						frm.doc.insurance_pct || 0
				  )}% and fee ${esc(frm.doc.fee_pct || 0)}% on the cost of work; use tax ${esc(
						frm.doc.tax_pct || 0
				  )}% on material and equipment. Cost and margin show only to Estimator, Finance and Executive roles.</div>`
				: ""
		}`;
	}
	const ready = root.querySelector('[data-cell="ready"]');
	if (ready) {
		const ok = !t.missing_cost && !t.missing_code;
		ready.innerHTML = `<h2>Ready to accept?</h2>
			<div class="chk"><i class="${t.missing_cost ? "bad" : "good"}"></i><span>Cost on every line: <b>${
			t.missing_cost ? `${t.missing_cost} missing` : "complete"
		}</b></span></div>
			<div class="chk"><i class="${t.missing_code ? "warn" : "good"}"></i><span>Cost code on every line: <b>${
			t.missing_code ? `${t.missing_code} missing` : "complete"
		}</b></span></div>
			<div class="chk"><i class="good"></i><span>Allowances named on the quotation: <b>${t.allowance_count}</b></span></div>
			<div class="muted">${
				ok
					? "Every line is priced and coded. This revision can be accepted into a contract."
					: "A quotation can be issued now. Accepting it into a contract needs every line priced and coded."
			}${frm.doc.quotation ? ` Quotation: <a href="/desk/quotation/${encodeURIComponent(frm.doc.quotation)}">${esc(frm.doc.quotation)}</a>.` : ""}</div>`;
	}
}

// ------------------------------------------------------------------------------------------ editing

function lineDoc(frm, name) {
	return (frm.doc.lines || []).find((l) => l.name === name);
}

function redrawSoon(frm) {
	// Next tick: let the field the person tapped take the focus first, then put it back after drawing.
	setTimeout(() => {
		const active = document.activeElement;
		const key = active && active.dataset ? active.dataset.key : null;
		draw(frm);
		if (key) {
			const el = frm.fields_dict.lines_editor.$wrapper.get(0).querySelector(`[data-key="${CSS.escape(key)}"]`);
			if (el) el.focus();
		}
	}, 0);
}

async function onChange(frm, el) {
	// The form script redraws the editor when a line field changes in the table view; while the editor
	// itself is writing, that would destroy the field the person just tapped, so it is told to stand by.
	frm.__estimate_editing = true;
	try {
		await applyChange(frm, el);
	} finally {
		frm.__estimate_editing = false;
	}
}

async function applyChange(frm, el) {
	const row = el.closest(".row");
	if (!row) return;
	const line = lineDoc(frm, row.dataset.row);
	if (!line) return;
	const field = el.dataset.field;
	let value = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value;
	if (["qty", "unit_cost", "sheet_price"].includes(field)) value = Math.max(0, Number(value) || 0);
	if (field === "phase_number") value = em.phase_number(value);
	if (field === "cost_code") value = String(value || "").replace(/\s+/g, " ").trim().toUpperCase();
	await frappe.model.set_value(line.doctype, line.name, field, value);

	let structural = ["phase_number", "by_others", "cost_type", "basis"].includes(field);
	if (field === "cost_code") {
		const codes = state(frm).codes || [];
		const hit = codes.find((c) => c.code === value);
		if (hit && hit.cost_type && hit.cost_type !== line.cost_type) {
			await frappe.model.set_value(line.doctype, line.name, "cost_type", hit.cost_type);
			structural = true;
		}
	}
	if ((field === "cost_type" || field === "cost_code") && line.cost_type === "Labor" && !Number(line.unit_cost)) {
		if (Number(frm.doc.labor_rate)) await frappe.model.set_value(line.doctype, line.name, "unit_cost", frm.doc.labor_rate);
		if (!line.uom || line.uom === "LS") await frappe.model.set_value(line.doctype, line.name, "uom", "h");
		structural = true;
	}
	if (field === "phase_number" && value) state(frm).open.add(value);
	if (structural) redrawSoon(frm);
	else numbers(frm);
}

function addLine(frm, phase) {
	const row = frappe.model.add_child(frm.doc, "Estimate Line", "lines");
	row.phase_number = phase || 0;
	row.cost_type = "Material";
	row.qty = 1;
	row.uom = "LS";
	frm.dirty();
	frm.refresh_field("lines");
	state(frm).open.add(phase || 0);
	draw(frm);
	const el = frm.fields_dict.lines_editor.$wrapper.get(0).querySelector(`[data-key="${CSS.escape(row.name)}:description"]`);
	if (el) el.focus();
}

function removeLine(frm, name) {
	const line = lineDoc(frm, name);
	if (!line) return;
	frappe.model.clear_doc(line.doctype, line.name);
	frm.dirty();
	frm.refresh_field("lines");
	state(frm).more.delete(name);
	draw(frm);
}

function rates_for(frm, line) {
	const phases = {};
	for (const p of frm.doc.phases || []) phases[em.phase_number(p.phase_number)] = p;
	return em.line_rates(line, phases, frm.doc);
}

async function backfill(frm, lines) {
	frm.__estimate_editing = true;
	try {
		for (const line of lines) {
			const uc = em.backfill_unit_cost(line.sheet_price, line.qty, line.cost_type, rates_for(frm, line));
			await frappe.model.set_value(line.doctype, line.name, "unit_cost", uc);
		}
	} finally {
		frm.__estimate_editing = false;
	}
	draw(frm);
}

function addPhase(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Add a phase"),
		fields: [
			{
				fieldname: "phase_number",
				fieldtype: "Int",
				label: __("Phase number"),
				reqd: 1,
				description: __("100 to 700. A design stage of a design-build job is 110 to 140, inside 100."),
			},
			{ fieldname: "label", fieldtype: "Data", label: __("Label"), description: __("Blank: the usual name.") },
		],
		primary_action_label: __("Add"),
		primary_action(v) {
			const n = em.phase_number(v.phase_number);
			if (!n) return;
			for (const num of em.phase_chain(n).reverse()) {
				if (!(frm.doc.phases || []).some((p) => em.phase_number(p.phase_number) === num)) {
					const row = frappe.model.add_child(frm.doc, "Estimate Phase", "phases");
					row.phase_number = num;
					row.label = num === n && v.label ? v.label : DEFAULT_PHASE_LABELS[num] || `Phase ${num}`;
				}
			}
			frm.dirty();
			frm.refresh_field("phases");
			state(frm).open.add(n);
			d.hide();
			draw(frm);
		},
	});
	d.show();
}

export async function apply_template(frm, template) {
	if (!template) {
		frappe.prompt(
			{ fieldname: "template", fieldtype: "Link", options: "Estimate Template", label: __("Template"), reqd: 1 },
			(v) => apply_template(frm, v.template),
			__("Apply a template")
		);
		return;
	}
	const tpl = await frappe.db.get_doc("Estimate Template", template);
	const lines = tpl.lines || [];
	const labels = {};
	for (const p of tpl.phases || []) labels[em.phase_number(p.phase_number)] = p.label;
	const fields = [];
	const groups = {};
	lines.forEach((l, i) => (groups[em.phase_number(l.phase_number)] = groups[em.phase_number(l.phase_number)] || []).push([l, i]));
	for (const number of Object.keys(groups).map(Number).sort((a, b) => a - b)) {
		fields.push({
			fieldtype: "Section Break",
			label: number ? esc([number, labels[number] || DEFAULT_PHASE_LABELS[number] || ""].join(" ")) : __("Lines"),
		});
		for (const [l, i] of groups[number]) {
			fields.push({
				fieldname: `l_${i}`,
				fieldtype: "Check",
				default: l.include ? 1 : 0,
				// Frappe draws a control's label as HTML, so a typed description is escaped here too.
				label: esc([l.description, "·", qtyText(l.qty || 1), l.uom || ""].join(" ").trim()),
			});
		}
	}
	if (!fields.length) {
		frappe.msgprint(__("Template {0} has no lines yet.", [esc(template)]));
		return;
	}
	const d = new frappe.ui.Dialog({
		title: __("{0}: tick what this job needs", [esc(template)]),
		fields,
		size: "large",
		primary_action_label: __("Add ticked lines"),
		primary_action(values) {
			for (const p of tpl.phases || []) {
				const n = em.phase_number(p.phase_number);
				if (!(frm.doc.phases || []).some((x) => em.phase_number(x.phase_number) === n)) {
					const row = frappe.model.add_child(frm.doc, "Estimate Phase", "phases");
					row.phase_number = n;
					row.label = p.label;
					row.scope_text = p.scope_text;
				}
			}
			let added = 0;
			lines.forEach((l, i) => {
				if (!values[`l_${i}`]) return;
				const row = frappe.model.add_child(frm.doc, "Estimate Line", "lines");
				row.phase_number = em.phase_number(l.phase_number);
				row.description = l.description;
				row.cost_type = l.cost_type || "Material";
				row.cost_code = l.cost_code || "";
				row.qty = l.qty || 1;
				row.uom = l.uom || "LS";
				row.basis = l.basis || "";
				row.unit_cost = l.unit_cost || (row.cost_type === "Labor" ? Number(frm.doc.labor_rate) || 0 : 0);
				added += 1;
			});
			if (!frm.doc.template) frm.doc.template = template;
			frm.dirty();
			frm.refresh_field("phases");
			frm.refresh_field("lines");
			state(frm).open = null;
			d.hide();
			draw(frm);
			frappe.show_alert({ message: __("Added {0} lines from {1}.", [added, esc(template)]), indicator: "green" });
		},
	});
	d.show();
}

export async function copy_rates(frm, card) {
	if (!card) return;
	const doc = await frappe.db.get_doc("Estimate Rate Card", card);
	for (const f of RATE_FIELDS) frm.doc[f] = doc[f] || 0;
	frm.dirty();
	RATE_FIELDS.forEach((f) => frm.refresh_field(f));
	draw(frm);
}

// Save first, and say whether the form is now clean. frm.save() at v16 swallows a server refusal (a
// cost code the catalog does not know, say) and resolves anyway, so "it resolved" proves nothing: the
// form is still dirty or new after a failed save, and carrying on would issue the quotation from the
// last stored version and then reload over the unsaved edits.
export async function saved(frm) {
	if (frm.is_dirty() || frm.is_new()) {
		try {
			await frm.save();
		} catch (e) {
			return false;
		}
	}
	return !(frm.is_dirty() || frm.is_new());
}

async function issue(frm) {
	if (!(await saved(frm))) return;
	const r = await frappe.call({
		method: "erpnext_enhancements.api.estimating.issue_quotation",
		args: { estimate: frm.doc.name },
		freeze: true,
		freeze_message: __("Issuing the quotation"),
	});
	if (!r || !r.message) return;
	await frm.reload_doc();
	frappe.show_alert({
		message: __("Quotation {0} is ready as a draft.", [
			`<a href="/desk/quotation/${encodeURIComponent(r.message)}">${esc(r.message)}</a>`,
		]),
		indicator: "green",
	}, 10);
}

export async function new_revision(frm) {
	if (!(await saved(frm))) return;
	frappe.confirm(
		__("Start revision {0}? This one becomes Superseded and read-only, as the record of what was quoted.", [
			(frm.doc.revision || 1) + 1,
		]),
		async () => {
			const r = await frappe.call({
				method: "erpnext_enhancements.api.estimating.new_revision",
				args: { estimate: frm.doc.name },
				freeze: true,
			});
			if (r && r.message) frappe.set_route("Form", "Estimate", r.message);
		}
	);
}

function bind(frm, root) {
	// The field's wrapper outlives every draw (draw replaces its contents), so the listeners go on once
	// and read the form from the wrapper; binding per draw stacked them, and one tap added two lines.
	root.__sf_estimate_frm = frm;
	if (root.__sf_estimate_bound) return;
	root.__sf_estimate_bound = true;
	root.addEventListener("change", (e) => {
		const el = e.target;
		if (el.dataset && el.dataset.field) onChange(root.__sf_estimate_frm, el);
	});
	root.addEventListener("click", (e) => {
		const frm = root.__sf_estimate_frm;
		const btn = e.target.closest("[data-act]");
		if (!btn) return;
		const act = btn.dataset.act;
		const s = state(frm);
		if (act === "toggle") {
			const n = Number(btn.dataset.phase);
			s.open.has(n) ? s.open.delete(n) : s.open.add(n);
			draw(frm);
		} else if (act === "add") addLine(frm, Number(btn.dataset.phase));
		else if (act === "more") {
			const n = btn.dataset.row;
			s.more.has(n) ? s.more.delete(n) : s.more.add(n);
			draw(frm);
		} else if (act === "delete") removeLine(frm, btn.dataset.row);
		else if (act === "fill") {
			const line = lineDoc(frm, btn.dataset.row);
			if (line) backfill(frm, [line]);
		} else if (act === "fillall") {
			const { byName } = lineResults(frm);
			backfill(frm, (frm.doc.lines || []).filter((l) => byName[l.name] && byName[l.name].needs_cost && Number(l.sheet_price)));
		} else if (act === "template") apply_template(frm, frm.doc.template);
		else if (act === "addphase") addPhase(frm);
		else if (act === "save") frm.save();
		else if (act === "issue") issue(frm);
	});
}

/** Draw the editor into the form; called from the form's refresh and after any table edit. */
export async function mount(frm) {
	await loadCodes(frm);
	draw(frm);
}

/** Refresh only the numbers (a rate changed in the Rates section, a row edited in the table view). */
export function refresh(frm) {
	if (frm.fields_dict.lines_editor && frm.fields_dict.lines_editor.$wrapper.find(".sf-est").length) numbers(frm);
	else draw(frm);
}

export { draw as redraw, issue };
