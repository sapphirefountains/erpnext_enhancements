/**
 * The pay application's lines editor (estimating milestone 2), after the v2 "PayApp2" mockup.
 *
 * The project manager opens a phase and types the percent of each line completed THIS period (Nik,
 * 2026-11-06); the amount follows. Phases with work this period are open, the rest folded. The Amount
 * Due panel is the G702 in plain words. Accounting reviews, then certifies (submits).
 *
 * Numbers while typing come from pay_app_math.js, the twin of the server engine; every save prices the
 * application again on the server, and its numbers are the ones stored. Lines with a percent that
 * would take them past their scheduled value are highlighted, and certifying refuses them.
 *
 * Desk links are /desk/, never /app/ (a full page load on v16). Every value drawn is escaped.
 */

import * as pm from "./pay_app_math.js";
import { DEFAULT_PHASE_LABELS } from "./editor.js";

const API = "erpnext_enhancements.api.estimating.";

const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
const money = (n) =>
	(Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctText = (n) => (n === null || n === undefined ? "–" : `${Math.round((Number(n) || 0) * 10) / 10}%`);
const num = (v) => {
	const n = Number(String(v === null || v === undefined ? "" : v).replace(/[%,\s]/g, ""));
	return Number.isFinite(n) ? n : 0;
};

const CHEVRON =
	'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 6 6 6-6 6"/></svg>';
const CHEVRON_OPEN =
	'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>';

function state(frm) {
	if (!frm.__sf_pay || frm.__sf_pay.name !== frm.doc.name) frm.__sf_pay = { name: frm.doc.name, open: null };
	return frm.__sf_pay;
}

function schedule(frm) {
	try {
		return JSON.parse(frm.doc.billing_context || "{}");
	} catch (e) {
		return {};
	}
}

export function compute(frm) {
	const lines = frm.doc.lines || [];
	const previous = {};
	// D and the retainage already held, as the server stored them at the last save (keep holding).
	for (const l of lines) previous[l.line_key] = { work: l.from_previous || 0, retainage_work: l.retainage_previous || 0 };
	try {
		return pm.price_application(schedule(frm), frm.doc, lines, previous);
	} catch (e) {
		// A retainage override typed as text in the table view: the server refuses it by name on save.
		return null;
	}
}

export function group_of(line) {
	if (Number(line.is_change_order)) return "co";
	const n = Math.trunc(Number(line.phase_number) || 0);
	return n >= 100 ? n - (n % 100) : 0;
}

export function groups(lines) {
	const out = new Map();
	for (const line of lines) {
		const g = group_of(line);
		if (!out.has(g)) out.set(g, []);
		out.get(g).push(line);
	}
	return [...out.entries()].sort(([a], [b]) => (a === "co" ? 1 : b === "co" ? -1 : a - b));
}

function group_title(g) {
	if (g === "co") return __("Change orders");
	if (!g) return __("Lines");
	return `${g} ${DEFAULT_PHASE_LABELS[g] || __("Phase {0}", [g])}`;
}

function editable(frm) {
	return frm.doc.docstatus === 0 && frm.perm[0] && frm.perm[0].write;
}

/** In milestone billing the base lines bill the milestone's percent; change orders are typed like progress. */
export function row_editable(frm, line) {
	return editable(frm) && (schedule(frm).billing_mode !== "Milestones" || !!Number(line.is_change_order));
}

function over(row) {
	const c = pm.cents(row.scheduled_value);
	const g = pm.cents(row.total_completed);
	return (c >= 0 && g > c) || (c < 0 && g < c);
}

function rowHtml(frm, line, priced, can) {
	const c = Number(line.scheduled_value) || 0;
	const prev = c ? (Number(line.from_previous) * 100) / c : null;
	const others = Number(line.by_others);
	const tag = others
		? `<span class="tag t-oth">${esc(__("BY OTHERS"))}</span>`
		: Number(line.is_change_order)
		? `<span class="tag">${esc(__("CHANGE ORDER"))}</span>`
		: "";
	const input = others
		? `<span class="muted">${esc(__("by others"))}</span>`
		: can
		? `<input type="text" inputmode="decimal" data-f="pct" aria-label="${esc(__("This period %"))}" value="${esc(
				line.this_period_pct ? Number(line.this_period_pct) : ""
		  )}" placeholder="0"><small>%</small><button class="btn-fill" type="button" data-act="rest" title="${esc(
				__("Bill what is left")
		  )}">${esc(__("REST"))}</button>`
		: `<span>${esc(pctText(line.this_period_pct))}</span>`;
	return `<div class="row${others ? " oth" : ""}${over(priced) ? " over" : ""}" data-key="${esc(line.line_key)}" data-name="${esc(
		line.name
	)}">
		<div class="desc"><span><b>${esc(line.item_no)}</b> ${esc(line.description)}</span>${tag ? `<span class="sub">${tag}</span>` : ""}</div>
		<div class="n"><span class="lab">${esc(__("Scheduled"))}</span>${money(c)}</div>
		<div class="n"><span class="lab">${esc(__("Previous"))}</span>${esc(pctText(prev))}</div>
		<div class="q"><span class="lab">${esc(__("This period %"))}</span>${input}</div>
		<div class="n amt"><span class="lab">${esc(__("This period"))}</span><span data-v="amt">${money(priced.this_period)}</span></div>
	</div>`;
}

function overText(count) {
	return count === 1
		? __("1 line goes past its scheduled value once added to previous applications. Lower the highlighted line.")
		: __("{0} lines go past their scheduled value once added to previous applications. Lower the highlighted lines.", [count]);
}

function sumCents(rows, key) {
	return rows.reduce((s, r) => s + pm.cents(r[key]), 0);
}

function sideHtml(frm, totals) {
	const ret = totals.work_retainage_pct ? ` ${pctText(totals.work_retainage_pct)}` : "";
	const certified = frm.doc.certified_less
		? `<div class="muted">${esc(__("Certified by the general contractor for {0}.", [money(frm.doc.amount_certified)]))}</div>`
		: "";
	return `<div class="card"><h2>${esc(__("Amount due"))}</h2>
		<div class="kv">
			<span class="k">${esc(__("Work completed to date"))}</span><span class="v" data-t="completed">${money(totals.total_completed_stored)}</span>
			<span class="k">${esc(__("Less retainage"))}${esc(ret)}</span><span class="v" data-t="ret">−${money(totals.total_retainage)}</span>
			<span class="k">${esc(__("Less previous payments"))}</span><span class="v">−${money(totals.less_previous_certificates)}</span>
			<span class="k"><b>${esc(__("Due on this application"))}</b></span><span class="v big" data-t="due">${money(totals.current_payment_due)}</span>
		</div>
		${certified}
		<div class="muted">${esc(__("Contract sum to date {0} · {1} complete", [money(totals.contract_sum_to_date), pctText(totals.percent_complete)]))}</div>
		<div class="muted">${esc(__("Retainage rate from: {0}", [totals.retainage_source]))}</div>
	</div>`;
}

function barHtml(frm, totals) {
	const btn = (act, label, primary) =>
		`<button class="btn${primary ? " primary" : ""}" type="button" data-act="${act}">${esc(label)}</button>`;
	const parts = [`<span class="bar-total">${esc(__("This period"))} <span data-t="this">${money(totals.this_period_total)}</span></span>`];
	if (frm.doc.docstatus === 0 && frm.perm[0] && frm.perm[0].write) {
		parts.push(btn("save", __("SAVE")));
		if (frm.doc.status !== "To Review") parts.push(btn("send", __("SEND TO ACCOUNTING"), true));
		else {
			parts.push(btn("back", __("BACK TO PROGRESS")));
			if (frm.perm[0].submit) parts.push(btn("certify", __("CERTIFY"), true));
		}
	}
	return `<div class="bar">${parts.join("")}</div>`;
}

export function draw(frm) {
	const field = frm.fields_dict.lines_editor;
	if (!field || !field.$wrapper) return;
	const root = field.$wrapper.get(0);
	const lines = frm.doc.lines || [];
	if (frm.is_new() || !lines.length) {
		field.$wrapper.html(`<div class="muted">${esc(__("Save to bring in the schedule's lines."))}</div>`);
		return;
	}
	const priced = compute(frm);
	if (!priced) {
		field.$wrapper.html(
			`<div class="alert alert-warning">${esc(__("A retainage override is not a percent. Correct it in the table below."))}</div>`
		);
		return;
	}
	const byKey = {};
	lines.forEach((l, i) => (byKey[l.line_key] = priced.lines[i]));
	const st = state(frm);
	const grouped = groups(lines);
	if (st.open === null) {
		st.open = new Set(
			lines.length <= 12 ? grouped.map(([g]) => String(g)) : grouped.filter(([, ls]) => ls.some((l) => Number(l.this_period_pct))).map(([g]) => String(g))
		);
	}
	const can = editable(frm);
	const progress = schedule(frm).billing_mode !== "Milestones";
	const overCount = priced.lines.filter(over).length;
	const milestone = schedule(frm).billing_mode === "Milestones";
	const head = `<div class="cardhead"><h2>${esc(__("Work completed this period"))}</h2>
		<span class="muted">${esc(
			milestone
				? __("Billed by milestone: every line bills the milestone's percent. Change orders bill by the percent typed on them.")
				: can
				? __("Open a phase and type the percent of each line completed this period. The amount follows.")
				: __("As the project manager marked them.")
		)}</span>
		<span class="tools">${can && progress && frm.doc.previous_application ? `<button class="btn sm" type="button" data-act="copy">${esc(__("COPY LAST MONTH"))}</button>` : ""}</span></div>
		<div class="banner warn" data-over ${overCount ? "" : "hidden"}>${esc(overText(overCount))}</div>`;
	const body = grouped
		.map(([g, ls]) => {
			const open = st.open.has(String(g));
			const rows = ls.map((l) => byKey[l.line_key]);
			return `<div class="phase" data-g="${esc(g)}">
				<button class="ph" type="button" data-act="toggle" aria-expanded="${open}">${open ? CHEVRON_OPEN : CHEVRON}
					<span class="t">${esc(group_title(g))}</span>
					<span class="s">${money(pm.dollars(sumCents(rows, "scheduled_value")))}</span>
					<span class="a" data-g-amt="${esc(g)}">${money(pm.dollars(sumCents(rows, "this_period")))}</span></button>
				<div class="lines" ${open ? "" : "hidden"}>
					<div class="row hd"><span class="l">${esc(__("Item / description"))}</span><span>${esc(__("Scheduled"))}</span><span>${esc(
				__("Previous")
			)}</span><span>${esc(__("This period %"))}</span><span>${esc(__("This period"))}</span></div>
					${ls.map((l) => rowHtml(frm, l, byKey[l.line_key], row_editable(frm, l))).join("")}
				</div></div>`;
		})
		.join("");
	const t = priced.totals;
	const total = `<div class="ph total"><span class="sp"></span><span class="t">${esc(__("All phases"))}</span>
		<span class="s">${money(t.scheduled_total)}</span><span class="a" data-t="this2">${money(t.this_period_total)}</span></div>`;
	root.innerHTML = `<div class="sf-est sf-pay"><div class="cols">
		<div class="main"><div class="card lines-card">${head}${body}${total}</div></div>
		<div class="side">${sideHtml(frm, t)}</div></div>${barHtml(frm, t)}</div>`;
	bind(frm, root);
}

/** Update every number in place after an edit; the inputs stay where the finger is. */
export function numbers(frm) {
	const field = frm.fields_dict.lines_editor;
	if (!field || !field.$wrapper) return;
	const root = field.$wrapper.get(0);
	const lines = frm.doc.lines || [];
	const priced = compute(frm);
	if (!priced || !root.querySelector(".sf-pay")) {
		draw(frm);
		return;
	}
	const byKey = {};
	lines.forEach((l, i) => (byKey[l.line_key] = priced.lines[i]));
	root.querySelectorAll(".row[data-key]").forEach((el) => {
		const r = byKey[el.dataset.key];
		if (!r) return;
		el.classList.toggle("over", over(r));
		const amt = el.querySelector('[data-v="amt"]');
		if (amt) amt.textContent = money(r.this_period);
	});
	for (const [g, ls] of groups(lines)) {
		const el = root.querySelector(`[data-g-amt="${CSS.escape(String(g))}"]`);
		if (el) el.textContent = money(pm.dollars(sumCents(ls.map((l) => byKey[l.line_key]), "this_period")));
	}
	const t = priced.totals;
	const set = (k, v) => root.querySelectorAll(`[data-t="${k}"]`).forEach((el) => (el.textContent = v));
	set("this", money(t.this_period_total));
	set("this2", money(t.this_period_total));
	set("completed", money(t.total_completed_stored));
	set("ret", `−${money(t.total_retainage)}`);
	set("due", money(t.current_payment_due));
	const overCount = priced.lines.filter(over).length;
	const banner = root.querySelector("[data-over]");
	if (banner) {
		banner.hidden = !overCount;
		banner.textContent = overText(overCount);
	}
}

async function setPct(frm, name, value) {
	frm.__pay_editing = true;
	try {
		await frappe.model.set_value("Pay Application Line", name, "this_period_pct", value);
	} finally {
		frm.__pay_editing = false;
	}
}

async function saved(frm) {
	// frm.save() at v16.56 resolves even when the server refused, so the form's state is the answer.
	if (frm.is_dirty() || frm.is_new()) await frm.save();
	return !(frm.is_dirty() || frm.is_new());
}

async function act(frm, action, el) {
	if (action === "toggle") {
		const g = el.closest(".phase").dataset.g;
		const st = state(frm);
		if (st.open.has(g)) st.open.delete(g);
		else st.open.add(g);
		draw(frm);
	} else if (action === "rest") {
		const row = el.closest(".row");
		const line = (frm.doc.lines || []).find((l) => l.name === row.dataset.name);
		if (!line) return;
		const c = pm.cents(line.scheduled_value);
		const left = pm.percent(c - pm.cents(line.from_previous), c) || 0;
		await setPct(frm, line.name, left);
		const input = row.querySelector('input[data-f="pct"]');
		if (input) input.value = left;
		numbers(frm);
	} else if (action === "copy") {
		const prev = await frappe.db.get_doc("Pay Application", frm.doc.previous_application);
		const last = {};
		for (const l of prev.lines || []) last[l.line_key] = Number(l.this_period_pct) || 0;
		for (const l of frm.doc.lines || []) {
			if (!Number(l.by_others) && last[l.line_key] !== undefined) await setPct(frm, l.name, last[l.line_key]);
		}
		draw(frm);
	} else if (action === "save") {
		await frm.save();
	} else if (action === "send") {
		if (!(await saved(frm))) return;
		await frappe.call({ method: API + "send_for_review", args: { pay_application: frm.doc.name }, freeze: true });
		await frm.reload_doc();
		frappe.show_alert({ message: __("Sent to accounting."), indicator: "green" });
	} else if (action === "back") {
		if (!(await saved(frm))) return;
		await frappe.call({ method: API + "back_to_progress", args: { pay_application: frm.doc.name }, freeze: true });
		await frm.reload_doc();
	} else if (action === "certify") {
		if (!(await saved(frm))) return;
		frm.savesubmit();
	}
}

function bind(frm, root) {
	root.__sf_pay_frm = frm;
	if (root.__sf_pay_bound) return;
	root.__sf_pay_bound = true;
	root.addEventListener("change", async (e) => {
		const el = e.target;
		if (!el.dataset || el.dataset.f !== "pct") return;
		const f = root.__sf_pay_frm;
		const row = el.closest(".row");
		await setPct(f, row.dataset.name, num(el.value));
		numbers(f);
	});
	root.addEventListener("click", (e) => {
		const el = e.target.closest("[data-act]");
		if (!el) return;
		e.preventDefault();
		act(root.__sf_pay_frm, el.dataset.act, el);
	});
}

export function mount(frm) {
	draw(frm);
}

export { draw as redraw };
