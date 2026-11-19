/**
 * The pay application's G703 and G702, line for line the same as
 * erpnext_enhancements/estimating/pay_app_math.py (read its docstring for the model and the reasons:
 * column D is the earlier applications' WORK completed, stored material is a balance, retainage steps
 * by the percent complete at the START of the period and earlier retainage stays held at its own rate
 * (Nik, 2026-11-19: keep holding), finishing a line closes it to the cent).
 *
 * The editor uses this only to show totals while a project manager types; the server prices the
 * application again on every save and its numbers are the ones stored. Both are held to the same
 * cases (tests/data/pay_app_math_cases.json).
 */

import { round_money, parse_rate } from "./estimate_math.js";

const num = (v) => {
	const n = Number(v);
	return Number.isFinite(n) ? n : 0;
};

export function cents(value) {
	return Math.round(round_money(value) * 100);
}

export function dollars(c) {
	return c / 100;
}

export function share(base, pct) {
	if (!base || !num(pct)) return 0;
	return cents((base * num(pct)) / 10000);
}

export function percent(part, whole) {
	if (!whole) return null;
	return round_money((part * 100) / whole, 2);
}

export function step_in_force(steps, pct_start) {
	let best = null;
	for (const s of steps || []) {
		const from = num(s.from_percent_complete);
		if (from <= num(pct_start) + 1e-9 && (best === null || from > num(best.from_percent_complete))) best = s;
	}
	return best;
}

export function resolve_rates(schedule, application, pct_start) {
	if (!schedule.retainage_applies) return [0, 0, "none"];
	let work = num(schedule.retainage_pct);
	let stored = num(schedule.stored_retainage_pct);
	let source = "schedule";
	const step = step_in_force(schedule.steps, pct_start);
	if (step) {
		work = num(step.work_pct);
		stored = num(step.stored_pct);
		source = `step from ${round_money(num(step.from_percent_complete), 2)}%`;
	}
	const wo = parse_rate(application.work_retainage_override);
	const so = parse_rate(application.stored_retainage_override);
	if (wo !== null) {
		work = wo;
		source = "application";
	}
	if (so !== null) stored = so;
	return [work, stored, source];
}

export function price_application(schedule, application, lines, previous) {
	const stored_on = !!schedule.stored_materials;
	const prepared = (lines || []).map((line) => {
		const entry = (previous || {})[line.line_key];
		if (entry !== null && typeof entry === "object") {
			const held = entry.retainage_work;
			return [line, cents(line.scheduled_value), cents(entry.work), held === null || held === undefined ? null : cents(held)];
		}
		return [line, cents(line.scheduled_value), cents(entry || 0), null];
	});
	const contract_to_date = prepared.reduce((s, [, c]) => s + c, 0);
	const start_pct = percent(prepared.reduce((s, [, , d]) => s + d, 0), contract_to_date) || 0;
	const [job_work, job_stored, source] = resolve_rates(schedule, application, start_pct);

	const out = prepared.map(([line, c, d, prior]) => {
		const by_others = !!num(line.by_others);
		const pct = by_others ? 0 : num(line.this_period_pct);
		const remaining = percent(c - d, c);
		const e = pct && remaining !== null && round_money(pct, 2) === remaining ? c - d : share(c, pct);
		const f = stored_on && !by_others ? cents(line.stored) : 0;
		const g = d + e + f;
		let wp = parse_rate(line.work_retainage_override);
		let sp = parse_rate(line.stored_retainage_override);
		if (!schedule.retainage_applies) {
			wp = 0;
			sp = 0;
		}
		wp = wp === null ? job_work : wp;
		sp = sp === null ? job_stored : sp;
		// Keep holding (Nik, 2026-11-19): earlier retainage stays at the rate it was held at.
		const held = prior === null ? share(d, wp) : prior;
		let rw = held + share(e, wp);
		if (c >= 0 && rw < 0) rw = 0;
		const rs = share(f, sp);
		return {
			line_key: line.line_key,
			scheduled_value: dollars(c),
			from_previous: dollars(d),
			retainage_previous: dollars(held),
			this_period_pct: pct,
			this_period: dollars(e),
			stored: dollars(f),
			total_completed: dollars(g),
			percent_complete: percent(g, c),
			balance: dollars(c - g),
			work_completed_to_date: dollars(d + e),
			work_retainage_pct: wp,
			stored_retainage_pct: sp,
			retainage_work: dollars(rw),
			retainage_stored: dollars(rs),
			retainage: dollars(rw + rs),
			_c: c,
			_d: d,
			_e: e,
			_f: f,
			_g: g,
			_rw: rw,
			_rs: rs,
			_co: !!num(line.is_change_order),
		};
	});

	const sum = (k, pick) => out.reduce((s, r) => s + (pick ? (pick(r) ? r[k] : 0) : r[k]), 0);
	const original = cents(schedule.contract_sum);
	const change = sum("_c", (r) => r._co);
	const completed = sum("_g");
	const ret_work = sum("_rw");
	const ret_stored = sum("_rs");
	const earned = completed - ret_work - ret_stored;
	const prev_cert = cents(application.less_previous_certificates);
	const totals = {
		original_contract_sum: dollars(original),
		net_change_orders: dollars(change),
		contract_sum_to_date: dollars(original + change),
		scheduled_total: dollars(contract_to_date),
		from_previous_total: dollars(sum("_d")),
		this_period_total: dollars(sum("_e")),
		stored_total: dollars(sum("_f")),
		total_completed_stored: dollars(completed),
		balance_total: dollars(out.reduce((s, r) => s + r._c - r._g, 0)),
		retainage_work: dollars(ret_work),
		retainage_stored: dollars(ret_stored),
		total_retainage: dollars(ret_work + ret_stored),
		total_earned_less_retainage: dollars(earned),
		less_previous_certificates: dollars(prev_cert),
		current_payment_due: dollars(earned - prev_cert),
		balance_to_finish: dollars(original + change - earned),
		percent_complete: percent(completed, original + change),
		percent_complete_at_start: start_pct,
		work_retainage_pct: job_work,
		stored_retainage_pct: job_stored,
		retainage_source: source,
	};
	for (const r of out) for (const k of ["_c", "_d", "_e", "_f", "_g", "_rw", "_rs", "_co"]) delete r[k];
	return { lines: out, totals };
}
