/**
 * The Estimate's pricing engine in the browser: a line-for-line twin of
 * erpnext_enhancements/estimating/estimate_math.py, so the totals move as you type.
 *
 * The server prices again on save and its numbers are the ones stored; this copy only has to agree
 * with it, and scripts/test_estimate_math.mjs holds both to the cases in
 * erpnext_enhancements/tests/data/estimate_math_cases.json. Read the Python module's docstring for
 * the model and the reasons behind it; change both files together or the build fails.
 *
 * No frappe, no DOM: an ES module the bundle imports and node can import too.
 */

export const COST_TYPES = ["Material", "Equipment", "Labor", "Subcontract", "Expense"];
export const TAXED_COST_TYPES = ["Material", "Equipment"];
export const OVERHEADS = ["contingency", "insurance", "fee", "commission"];
export const MARKUP_FIELDS = {
	Material: "markup_material",
	Equipment: "markup_equipment",
	Labor: "markup_labor",
	Subcontract: "markup_subcontract",
	Expense: "markup_expense",
};

const num = (v) => {
	const n = Number(v);
	return Number.isFinite(n) ? n : 0;
};

/** Banker's rounding after rounding away float noise, as frappe and the Python twin do. */
export function round_money(value, precision = 2) {
	value = num(value);
	if (!value) return 0;
	const sign = value < 0 ? -1 : 1;
	const multiplier = 10 ** precision;
	let scaled = Number((Math.abs(value) * multiplier).toFixed(8));
	const floor = Math.floor(scaled);
	if (scaled - floor === 0.5) scaled = floor % 2 === 0 ? floor : floor + 1;
	else scaled = Math.floor(scaled + 0.5);
	return (sign * scaled) / multiplier;
}

/** "" / null -> null (inherit); "5", "5%", 5 -> 5. Throws on anything else. */
export function parse_rate(raw) {
	if (raw === null || raw === undefined) return null;
	if (typeof raw === "number") return raw;
	const text = String(raw).replace(/%/g, "").replace(/,/g, "").trim();
	if (!text) return null;
	const value = Number(text);
	if (!Number.isFinite(value)) throw new Error(`Not a rate: ${raw}`);
	return value;
}

/** A phase as the integer Task.custom_phase_number stores; 0 means "no phase". */
export function phase_number(value) {
	const n = Math.trunc(Number(value || 0));
	return Number.isFinite(n) && n > 0 ? n : 0;
}

/** 120 -> [120, 100]; 300 -> [300]; 0 -> []. A non-hundred nests under its hundred. */
export function phase_chain(value) {
	const n = phase_number(value);
	if (!n) return [];
	const parent = Math.floor(n / 100) * 100;
	return parent && parent !== n ? [n, parent] : [n];
}

export function top_phase(value) {
	const chain = phase_chain(value);
	return chain.length ? chain[chain.length - 1] : 0;
}

export function resolve(name, line, phases_by_number, estimate) {
	const own = parse_rate(line[`${name}_override`]);
	if (own !== null) return own;
	for (const number of phase_chain(line.phase_number)) {
		const phase = phases_by_number[number];
		if (phase) {
			const value = parse_rate(phase[`${name}_override`]);
			if (value !== null) return value;
		}
	}
	return num(estimate[`${name}_pct`]);
}

export function resolve_markup(line, estimate) {
	const own = parse_rate(line.markup_override);
	if (own !== null) return own;
	const field = MARKUP_FIELDS[line.cost_type || ""];
	return field ? num(estimate[field]) : 0;
}

export function price_line(qty, unit_cost, cost_type, rates = {}) {
	const {
		markup_pct = 0,
		tax_pct = 0,
		contingency_pct = 0,
		insurance_pct = 0,
		fee_pct = 0,
		commission_pct = 0,
		sheet_price = 0,
		by_others = false,
	} = rates;
	qty = num(qty);
	unit_cost = num(unit_cost);
	const out = {
		base_cost: 0,
		tax_amount: 0,
		cost_amount: 0,
		markup_amount: 0,
		cost_of_work: 0,
		contingency_amount: 0,
		insurance_amount: 0,
		fee_amount: 0,
		amount: 0,
		unit_price: 0,
		commission_amount: 0,
		margin_amount: 0,
		margin_pct: 0,
		needs_cost: false,
		priced: false,
	};
	if (by_others) return out;

	if (!unit_cost) {
		out.needs_cost = true;
		out.amount = round_money(num(sheet_price));
		out.unit_price = qty ? round_money(out.amount / qty) : 0;
		out.commission_amount = round_money((out.amount * num(commission_pct)) / 100);
		return out;
	}

	const base = round_money(qty * unit_cost);
	const tax = TAXED_COST_TYPES.includes(cost_type) ? round_money((base * num(tax_pct)) / 100) : 0;
	const cost = round_money(base + tax);
	const markup = round_money((cost * num(markup_pct)) / 100);
	const cow = round_money(cost + markup);
	const contingency = round_money((cow * num(contingency_pct)) / 100);
	const insurance = round_money((cow * num(insurance_pct)) / 100);
	const fee = round_money((cow * num(fee_pct)) / 100);
	const amount = round_money(cow + contingency + insurance + fee);
	const margin = round_money(amount - cost);
	return Object.assign(out, {
		base_cost: base,
		tax_amount: tax,
		cost_amount: cost,
		markup_amount: markup,
		cost_of_work: cow,
		contingency_amount: contingency,
		insurance_amount: insurance,
		fee_amount: fee,
		amount,
		unit_price: qty ? round_money(amount / qty) : 0,
		commission_amount: round_money((amount * num(commission_pct)) / 100),
		margin_amount: margin,
		margin_pct: amount ? round_money((margin / amount) * 100, 1) : 0,
		priced: true,
	});
}

export function price_factor(cost_type, rates = {}) {
	const tax = TAXED_COST_TYPES.includes(cost_type) ? num(rates.tax_pct) / 100 : 0;
	const add_ons = (num(rates.contingency_pct) + num(rates.insurance_pct) + num(rates.fee_pct)) / 100;
	return (1 + tax) * (1 + num(rates.markup_pct) / 100) * (1 + add_ons);
}

export function backfill_unit_cost(price, qty, cost_type, rates = {}) {
	const q = num(qty) || 1;
	const factor = price_factor(cost_type, rates);
	if (!factor) return 0;
	price = num(price);
	const first = round_money(price / q / factor);
	const candidates = [first, round_money(first + 0.01), round_money(first - 0.01)].filter((c) => c > 0);
	let best = candidates[0];
	let best_diff = Infinity;
	for (const c of candidates) {
		const diff = Math.abs(price_line(q, c, cost_type, rates).amount - price);
		if (diff < best_diff) {
			best = c;
			best_diff = diff;
		}
	}
	return best;
}

export function line_rates(line, phases_by_number, estimate) {
	const rates = {};
	for (const name of OVERHEADS) rates[`${name}_pct`] = resolve(name, line, phases_by_number, estimate);
	rates.markup_pct = resolve_markup(line, estimate);
	rates.tax_pct = num(estimate.tax_pct);
	return rates;
}

const SUMMED = [
	"amount",
	"cost_amount",
	"markup_amount",
	"cost_of_work",
	"tax_amount",
	"contingency_amount",
	"insurance_amount",
	"fee_amount",
	"commission_amount",
];

export function price_estimate(estimate, phases, lines) {
	const phases_by_number = {};
	for (const p of phases || []) phases_by_number[phase_number(p.phase_number)] = p;
	const results = [];
	const phase_totals = {};
	const totals = {
		amount: 0,
		cost_amount: 0,
		priced_amount: 0,
		markup_amount: 0,
		cost_of_work: 0,
		tax_amount: 0,
		contingency_amount: 0,
		insurance_amount: 0,
		fee_amount: 0,
		commission_amount: 0,
		line_count: 0,
		missing_cost: 0,
		missing_code: 0,
		allowance_count: 0,
	};
	const bucket = (number) =>
		(phase_totals[number] = phase_totals[number] || {
			amount: 0,
			cost_amount: 0,
			line_count: 0,
			missing_cost: 0,
			labor_lines: 0,
		});

	for (const line of lines || []) {
		const rates = line_rates(line, phases_by_number, estimate);
		const by_others = !!Number(line.by_others || 0) || line.by_others === true;
		const result = price_line(line.qty, line.unit_cost, line.cost_type, {
			...rates,
			sheet_price: line.sheet_price,
			by_others,
		});
		Object.assign(result, rates);
		results.push(result);
		if (by_others) continue;

		totals.line_count += 1;
		for (const key of SUMMED) totals[key] += result[key];
		if (result.priced) totals.priced_amount += result.amount;
		if (result.needs_cost) totals.missing_cost += 1;
		if (!String(line.cost_code || "").trim()) totals.missing_code += 1;
		if (line.basis === "Allowance") totals.allowance_count += 1;

		const chain = phase_chain(line.phase_number);
		for (const number of chain.length ? chain : [0]) {
			const b = bucket(String(number));
			b.amount += result.amount;
			b.cost_amount += result.cost_amount;
			b.line_count += 1;
			b.missing_cost += result.needs_cost ? 1 : 0;
			b.labor_lines += line.cost_type === "Labor" ? 1 : 0;
		}
	}

	for (const key of Object.keys(totals)) {
		if (!["line_count", "missing_cost", "missing_code", "allowance_count"].includes(key)) {
			totals[key] = round_money(totals[key]);
		}
	}
	for (const b of Object.values(phase_totals)) {
		b.amount = round_money(b.amount);
		b.cost_amount = round_money(b.cost_amount);
	}

	const priced = results.filter((r) => r.priced);
	const priced_cost = round_money(priced.reduce((s, r) => s + r.cost_amount, 0));
	const priced_commission = round_money(priced.reduce((s, r) => s + r.commission_amount, 0));
	totals.margin_amount = round_money(totals.priced_amount - priced_cost);
	totals.margin_pct = totals.priced_amount
		? round_money((totals.margin_amount / totals.priced_amount) * 100, 1)
		: 0;
	totals.net_margin_amount = round_money(totals.margin_amount - priced_commission);
	totals.net_margin_pct = totals.priced_amount
		? round_money((totals.net_margin_amount / totals.priced_amount) * 100, 1)
		: 0;
	return { lines: results, phases: phase_totals, totals };
}
