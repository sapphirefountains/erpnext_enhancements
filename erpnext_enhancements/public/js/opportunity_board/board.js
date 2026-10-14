/*
 * form kit P6 (ERPNext TASK-2026-02774): the Opportunity board's decisions, as one pure ES module.
 * No frappe, no DOM, no window: tests/test_opportunity_board.py runs every function here under
 * node. The page that draws them is crm_enhancements/page/opportunity_board; the bundle that
 * carries them to it is public/js/opportunity_board.bundle.js (window.sf_opportunity_board).
 *
 * What lives here, and why each one is a decision rather than drawing:
 *
 *   routes          /desk/opportunity-board/<layout>[/<stage>][/move/<name>], each screen its own
 *                   history entry (Nik's rule, 2026-09-24: never break Back/Forward)
 *   prepare         the server payload (get_board) as the board's model: stage columns from the
 *                   status options, plus a column for any open status the options no longer list
 *                   (so every open deal is on the board, and the totals add up)
 *   ages            days in stage, from custom_stage_changed_on, and the amber / red levels at the
 *                   Sales Pipeline Dashboard thresholds; On Hold never glows, as on the TV board
 *   rows (P6.2)     a deal with several value streams is in every row it belongs to, marked "also
 *                   in ...", and each row counts it in full; the overall total counts it once
 *                   (Nik, 2026-10-13)
 *   forecast (P6.5) per stage: deals, value, and value weighted by each deal's own probability
 *                   (P5.5's stage default or Sales' override); On Hold is left out of the weighted
 *                   figure; and the deals expected to close this month and next
 *   drops           what a drop on a stage means: save the new status (open stages and On Hold),
 *                   open the guided Close step (Closed Won, Lost: they need ranks or reasons the
 *                   side panel, P6.6, will collect), nothing (its own stage), or refuse
 *
 * Pillar colours come from form_kit/summary.js's STREAM_PILLAR, itself held to
 * print_style.STREAM_PILLAR by test_guided_entry: one source.
 */

import { pillar_for } from "../form_kit/summary.js";

export const PAGE = "opportunity-board";
export const LAYOUTS = ["stages", "streams"];
export const DEFAULT_LAYOUT = "stages";
export const MOVE = "move";

// The rows of P6.2, in the order the task names them. A stream the site adds later gets a row
// after these; a deal with no stream gets the "No value stream" row (key NO_STREAM).
export const STREAM_ORDER = ["Design", "Build", "Service", "Events", "Products", "Delivery"];
export const NO_STREAM = "";

// Stage kinds. open and parked (On Hold) are board columns a card can be dropped on and saved;
// close (Closed Won, Lost) are drop targets that open the guided Close step; other is an open
// status the options no longer list: shown, so its deals are counted, but never a drop target.
export const KINDS = ["open", "parked", "other", "close"];
export const BOARD_KINDS = ["open", "parked", "other"];

// The phone layout (one stage at a time, tap to move) is below 768px, the design system's
// --sf-phone-max. The gesture numbers are the Project Planner's (PP.hold_ms, PP.drag_px): a
// touch lifts a card after a 300 ms hold, and a finger that moves first is scrolling.
export const PHONE_MAX = 767;
export const HOLD_MS = 300;
export const DRAG_PX = 6;

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
}

function num(value) {
	const n = Number(value);
	return Number.isFinite(n) ? n : 0;
}

// ------------------------------------------------------------------ routes

// "Proposal/Price Quote" -> "proposal-price-quote". Stage labels carry slashes, so the address
// holds a key, never the label.
export function stage_key(label) {
	const key = text(label)
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "");
	return key || "none";
}

// What the address asks for: { layout, stage, panel }, or null when it is not this page.
//   [PAGE]                                  the board, stage columns
//   [PAGE, layout]                          stages | streams
//   [PAGE, layout, stage]                   the phone's chosen stage tab
//   [PAGE, layout, MOVE, name]              the move panel for a deal
//   [PAGE, layout, stage, MOVE, name]       the same, over a chosen stage tab
export function parse_route(route) {
	if (!Array.isArray(route) || route[0] !== PAGE) return null;
	const layout = LAYOUTS.includes(text(route[1])) ? text(route[1]) : DEFAULT_LAYOUT;
	let stage = "";
	let panel = "";
	if (text(route[2]) === MOVE) {
		panel = text(route[3]);
	} else {
		stage = text(route[2]);
		if (text(route[3]) === MOVE) panel = text(route[4]);
	}
	return { layout, stage, panel };
}

// The route for a screen, as frappe.set_route takes it.
export function build_route(target) {
	const t = target || {};
	const route = [PAGE, LAYOUTS.includes(t.layout) ? t.layout : DEFAULT_LAYOUT];
	if (text(t.stage)) route.push(text(t.stage));
	if (text(t.panel)) route.push(MOVE, text(t.panel));
	return route;
}

// One string per screen, so "is that the screen already showing?" is a comparison.
export function route_key(target) {
	if (!target) return "";
	return [target.layout || DEFAULT_LAYOUT, target.stage || "", target.panel || ""].join("|");
}

export function is_phone(width) {
	return num(width) > 0 && num(width) <= PHONE_MAX;
}

// ------------------------------------------------------------------ dates and ages

// Whole days between two dates written YYYY-MM-DD (a datetime's date part is read), counted on
// the calendar, never negative. null when either is missing or unreadable. Both come from the
// server (`today` is the site's date, not the device's), so a phone in another time zone counts
// the same days as the TV board.
export function day_number(value) {
	const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(text(value));
	if (!m) return null;
	const ms = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
	return Number.isFinite(ms) ? Math.round(ms / 86400000) : null;
}

export function days_between(today, since) {
	const a = day_number(today);
	const b = day_number(since);
	if (a === null || b === null) return null;
	return Math.max(a - b, 0);
}

// 0 fresh, 1 amber, 2 red; sales_pipeline._stale_level's rule (a threshold of 0 switches its
// level off). A parked deal (On Hold) never glows: it is waiting on purpose.
export function stale_level(days, thresholds, kind) {
	if (kind === "parked" || days === null || days === undefined) return 0;
	const amber = num((thresholds || {}).amber);
	const red = num((thresholds || {}).red);
	if (red > 0 && days >= red) return 2;
	if (amber > 0 && days >= amber) return 1;
	return 0;
}

// "2026-10" for any date in October 2026; "" when unreadable.
export function month_key(value) {
	const m = /^(\d{4})-(\d{2})/.exec(text(value));
	return m ? `${m[1]}-${m[2]}` : "";
}

// The month `count` months after `key` ("2026-12", 1 -> "2027-01").
export function add_months(key, count) {
	const m = /^(\d{4})-(\d{2})$/.exec(text(key));
	if (!m) return "";
	const index = Number(m[1]) * 12 + (Number(m[2]) - 1) + Math.trunc(num(count));
	const year = Math.floor(index / 12);
	const month = (index % 12) + 1;
	return `${year}-${String(month).padStart(2, "0")}`;
}

// ------------------------------------------------------------------ the model

// The amount a card shows and every total adds: a number above zero, else null ("No amount";
// 58 of the 80 open deals on 2026-10-13 had none).
export function amount_of(deal) {
	const n = Number((deal || {}).amount);
	return Number.isFinite(n) && n > 0 ? n : null;
}

// The deal's own probability, 0 to 100. Blank is 0: a deal nobody has put a chance on adds
// nothing to the weighted forecast rather than its whole value.
export function probability_of(deal) {
	const n = Number((deal || {}).probability);
	if (!Number.isFinite(n)) return 0;
	return Math.min(Math.max(n, 0), 100);
}

// The value streams, each once, in the deal's own order: [{ stream, pillar }].
export function streams_of(deal) {
	const seen = new Set();
	const out = [];
	((deal || {}).streams || []).forEach((value) => {
		const stream = text(value);
		if (!stream || seen.has(stream)) return;
		seen.add(stream);
		out.push({ stream, pillar: pillar_for(stream) });
	});
	return out;
}

// The card's colour band: one segment per stream, split evenly; neutral when there is none.
export function band(deal) {
	const streams = streams_of(deal);
	return streams.length ? streams.map((s) => s.pillar) : ["neutral"];
}

// { done, total, percent, missing: [label] } for the meter, or null when the server sent none.
export function meter(completeness) {
	const c = completeness || null;
	if (!c || !(num(c.total) > 0)) return null;
	const total = num(c.total);
	const done = Math.min(Math.max(num(c.done), 0), total);
	return {
		done,
		total,
		percent: Math.round((done / total) * 100),
		missing: (Array.isArray(c.missing) ? c.missing : [])
			.map((m) => text(m && typeof m === "object" ? m.label : m))
			.filter(Boolean),
	};
}

function prepare_deal(raw, stage, today, thresholds) {
	const deal = Object.assign({}, raw);
	deal.name = text(raw.name);
	deal.status = text(raw.status);
	deal.stage = stage.key;
	deal.kind = stage.kind;
	deal.title = text(raw.title) || text(raw.account) || deal.name;
	deal.account = text(raw.account);
	deal.amount = amount_of(raw);
	deal.probability = probability_of(raw);
	deal.streams = streams_of(raw).map((s) => s.stream);
	deal.expected_closing = text(raw.expected_closing).slice(0, 10);
	deal.since = text(raw.since).slice(0, 10);
	deal.days = days_between(today, deal.since);
	deal.stale = stale_level(deal.days, thresholds, stage.kind);
	deal.modified = text(raw.modified);
	return deal;
}

// Stalest first (the TV board's order), then by title, so a column reads the same each time.
export function sort_deals(deals) {
	return deals.slice().sort((a, b) => {
		const da = a.days === null ? -1 : a.days;
		const db = b.days === null ? -1 : b.days;
		if (da !== db) return db - da;
		return a.title.localeCompare(b.title) || a.name.localeCompare(b.name);
	});
}

// The server payload as the board's model:
//   { stages: [{ key, label, kind }], deals: [deal], by_name, today, thresholds, currency }
// Stage columns are the server's (the status options, as the Sales Pipeline page reads them),
// in its order; an open deal whose status is not among them gets an "other" column of its own,
// after the open stages and before On Hold, so the board never drops a deal from its totals.
export function prepare(data) {
	const d = data || {};
	const thresholds = { amber: num((d.thresholds || {}).amber), red: num((d.thresholds || {}).red) };
	const today = text(d.today).slice(0, 10);
	const stages = [];
	const by_label = {};
	const add = (label, kind) => {
		const l = text(label);
		if (!l || by_label[l]) return;
		const stage = { key: stage_key(l), label: l, kind: KINDS.includes(kind) ? kind : "open" };
		// Two labels that slug alike ("On Hold" / "On-Hold") keep the first; the second is a
		// different status, so it gets a key of its own.
		let n = 2;
		while (stages.some((s) => s.key === stage.key)) stage.key = `${stage_key(l)}-${n++}`;
		by_label[l] = stage;
		stages.push(stage);
	};
	(d.stages || []).forEach((s) => add((s || {}).label, (s || {}).kind));
	const extra = [];
	(d.deals || []).forEach((raw) => {
		const status = text((raw || {}).status);
		if (status && !by_label[status] && !extra.includes(status)) extra.push(status);
	});
	if (extra.length) {
		// Insert after the last open stage, before On Hold and the closing stages.
		extra.sort();
		const before = stages.findIndex((s) => s.kind !== "open");
		const at = before === -1 ? stages.length : before;
		const others = [];
		extra.forEach((label) => {
			add(label, "other");
			others.push(stages.pop());
		});
		stages.splice(at, 0, ...others);
	}
	const deals = [];
	const by_name = {};
	(d.deals || []).forEach((raw) => {
		if (!raw || !text(raw.name) || by_name[text(raw.name)]) return;
		const stage = by_label[text(raw.status)];
		if (!stage || stage.kind === "close") return;
		const deal = prepare_deal(raw, stage, today, thresholds);
		deals.push(deal);
		by_name[deal.name] = deal;
	});
	return {
		stages,
		deals,
		by_name,
		today,
		thresholds,
		currency: text(d.currency) || "USD",
		truncated: !!d.truncated,
	};
}

export function stage_by_key(model, key) {
	return ((model || {}).stages || []).find((s) => s.key === key) || null;
}

export function board_stages(model) {
	return ((model || {}).stages || []).filter((s) => BOARD_KINDS.includes(s.kind));
}

export function closing_stages(model) {
	return ((model || {}).stages || []).filter((s) => s.kind === "close");
}

// The phone's stage: the one the address names when it is a board column, else the first.
export function current_stage(model, wanted) {
	const stages = board_stages(model);
	const hit = stages.find((s) => s.key === wanted);
	return hit ? hit.key : stages.length ? stages[0].key : "";
}

// { count, total } over deals, each counted once.
export function totals(deals) {
	let count = 0;
	let total = 0;
	(deals || []).forEach((deal) => {
		count += 1;
		total += amount_of(deal) || 0;
	});
	return { count, total };
}

// The plain board: one column per board stage, its deals (stalest first) and its totals.
export function columns(model) {
	const deals = (model || {}).deals || [];
	return board_stages(model).map((stage) => {
		const list = sort_deals(deals.filter((d) => d.stage === stage.key));
		return Object.assign({ stage, deals: list }, totals(list));
	});
}

// ------------------------------------------------------------------ rows by value stream (P6.2)

// The rows: [{ stream, pillar, items: [{ deal, also: [other streams] }], count, total }], the six
// of STREAM_ORDER always (a row with no deals says so), then any other stream a deal names, then
// "No value stream" when a deal has none. A deal is in every row of its streams and counts in
// full in each; the board's own total (totals(model.deals)) counts it once.
export function stream_rows(model) {
	const deals = (model || {}).deals || [];
	const extra = [];
	let none = false;
	deals.forEach((deal) => {
		if (!deal.streams.length) none = true;
		deal.streams.forEach((s) => {
			if (!STREAM_ORDER.includes(s) && !extra.includes(s)) extra.push(s);
		});
	});
	extra.sort();
	const order = STREAM_ORDER.concat(extra);
	if (none) order.push(NO_STREAM);
	return order.map((stream) => {
		const members =
			stream === NO_STREAM ? deals.filter((d) => !d.streams.length) : deals.filter((d) => d.streams.includes(stream));
		const items = sort_deals(members).map((deal) => ({ deal, also: deal.streams.filter((s) => s !== stream) }));
		return Object.assign(
			{ stream, pillar: stream === NO_STREAM ? "neutral" : pillar_for(stream), items },
			totals(members)
		);
	});
}

// One row's cells, one per board stage, in column order.
export function row_cells(model, row) {
	return board_stages(model).map((stage) => {
		const items = ((row || {}).items || []).filter((item) => item.deal.stage === stage.key);
		return Object.assign({ stage, items }, totals(items.map((item) => item.deal)));
	});
}

// ------------------------------------------------------------------ forecast (P6.5)

// A deal's probability-weighted value; 0 for On Hold (out of the forecast) and for no amount.
export function weighted_of(deal) {
	if (!deal || deal.kind === "parked") return 0;
	return ((amount_of(deal) || 0) * probability_of(deal)) / 100;
}

function figures(deals) {
	const out = totals(deals);
	out.weighted = (deals || []).reduce((sum, deal) => sum + weighted_of(deal), 0);
	return out;
}

// { stages: [{ stage, count, total, weighted }], overall: {...}, months: [{ key, count, total,
// weighted }, ...] }. A parked stage's weighted is null: it is not forecast, and the strip says
// so rather than showing a 0 that reads as "nothing likely". The months are this one and the
// next, by expected close date, on the site's `today`; a deal with no close date is in neither.
// On Hold deals count in a month's deals and value but, as everywhere, not in its weighted.
export function forecast(model) {
	const deals = (model || {}).deals || [];
	const stages = board_stages(model).map((stage) => {
		const f = figures(deals.filter((d) => d.stage === stage.key));
		if (stage.kind === "parked") f.weighted = null;
		return Object.assign({ stage }, f);
	});
	const this_month = month_key((model || {}).today);
	const months = this_month
		? [this_month, add_months(this_month, 1)].map((key) =>
				Object.assign({ key }, figures(deals.filter((d) => month_key(d.expected_closing) === key)))
		  )
		: [];
	return { stages, overall: figures(deals), months };
}

// ------------------------------------------------------------------ moving a card

// What dropping `deal` on the stage `to_key` means:
//   "none"    its own stage, or nothing under the pointer
//   "save"    an open stage or On Hold: save the new status (form_kit.api.save_step)
//   "close"   Closed Won or Lost: open the guided page's Close step, and put the card back
//   "refuse"  a column for a status the options no longer list
export function drop_action(model, deal, to_key) {
	if (!deal) return "none";
	const to = stage_by_key(model, to_key);
	if (!to) return "none";
	if (to.kind === "close") return "close";
	if (to.kind === "other") return "refuse";
	if (to.key === deal.stage) return "none";
	return "save";
}

// The model with one deal moved to `status`, as the card shows while the save is in flight:
// its clock starts again today and it does not glow. Pure: the old model is untouched, so a
// refused or failed save puts the card back by keeping the old one.
export function move_deal(model, name, status) {
	const m = model || {};
	const old = (m.by_name || {})[name];
	const stage = (m.stages || []).find((s) => s.label === status);
	if (!old || !stage || stage.kind === "close") return m;
	const deal = Object.assign({}, old, {
		status: stage.label,
		stage: stage.key,
		kind: stage.kind,
		since: m.today,
		days: m.today ? 0 : old.days,
		stale: 0,
	});
	return replace_deal(m, deal);
}

// The model with `deal` (same name) in place of the one it has.
export function replace_deal(model, deal) {
	const m = model || {};
	if (!deal || !(m.by_name || {})[deal.name]) return m;
	const deals = m.deals.map((d) => (d.name === deal.name ? deal : d));
	const by_name = Object.assign({}, m.by_name, { [deal.name]: deal });
	return Object.assign({}, m, { deals, by_name });
}

// The stages a deal can be sent to from the move panel: every board column but "other", then
// the closing stages; each { stage, action, current }.
export function move_choices(model, deal) {
	return (model || {}).stages
		? model.stages
				.filter((s) => s.kind !== "other")
				.map((stage) => ({ stage, action: drop_action(model, deal, stage.key), current: !!deal && deal.stage === stage.key }))
		: [];
}
