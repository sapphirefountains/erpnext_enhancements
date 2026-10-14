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
 *                   open the side panel's required fields (Closed Won, Lost: they need ranks or
 *                   reasons, P6.6), nothing (its own stage), or refuse
 *
 * Part C (v1.672.0):
 *
 *   panel (P6.6)    the side panel's routes (edit, a closing move, quick add, bulk) and which closing
 *                   stage needs what
 *   quick add (P6.7) which columns take a "+"
 *   bulk (P6.7)     the selection (kept per tab), what a bulk request carries, and what its per-deal
 *                   answer leaves selected
 *
 * Part B (v1.663.0):
 *
 *   views (P6.3)    the board, a sortable table and a timeline of close dates by month, plus the
 *                   attention list, each its own route; the filters are page state, so switching
 *                   views keeps them
 *   attention (P6.4) stale, past the expected close date, no amount, or missing what the stage
 *                   asks for (P4.6); sorted by value; the README's SQL check names the same deals
 *   focus (P6.8)    quick filters, On Hold folded, snoozed deals hidden until their date and then
 *                   marked returning, and the shape of a saved view (VIEW_SCHEMA, held equal to
 *                   api/planner_views_prefs.SCHEMA["board"])
 *
 * Pillar colours come from form_kit/summary.js's STREAM_PILLAR, itself held to
 * print_style.STREAM_PILLAR by test_guided_entry: one source.
 */

import { pillar_for } from "../form_kit/summary.js";

export const PAGE = "opportunity-board";
export const LAYOUTS = ["stages", "streams"];
export const DEFAULT_LAYOUT = "stages";
export const MOVE = "move";
// Part C (v1.672.0): the side panel's routes (form kit P4.4, P6.6, P6.7), each over any view.
//   .../edit/<name>[/<step>]          a deal in the side panel (the step is replaced, never pushed)
//   .../closing/<name>/<stage key>    a drop on Closed Won or Lost: its required fields, then the move
//   .../new/<stage key>               quick add, in that column's stage
//   .../bulk/<action>                 a bulk action on the selected deals
export const EDIT = "edit";
export const CLOSING = "closing";
export const NEW = "new";
export const BULK = "bulk";
export const PANEL_WORDS = [MOVE, EDIT, CLOSING, NEW, BULK];
export const BULK_ACTIONS = ["stage", "owner", "close_date", "snooze"];
// The most deals one bulk request carries (the server holds the same number).
export const MAX_BULK = 100;

// The rows of P6.2, in the order the task names them. A stream the site adds later gets a row
// after these; a deal with no stream gets the "No value stream" row (key NO_STREAM).
export const STREAM_ORDER = ["Design", "Build", "Service", "Events", "Products", "Delivery"];
export const NO_STREAM = "";

// Stage kinds. open and parked (On Hold) are board columns a card can be dropped on and saved;
// close (Closed Won, Lost) are drop targets that open their required fields in the side panel
// (part C; before it, the guided Close step); other is an open
// status the options no longer list: shown, so its deals are counted, but never a drop target.
export const KINDS = ["open", "parked", "other", "close"];
export const BOARD_KINDS = ["open", "parked", "other"];
// What a closing stage's move must carry (the server's `gate`): Closed Won's ranks, or Lost's reasons.
export const GATES = ["won", "lost"];

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
	// A stage called "New" or "Move" would read as a panel in the address.
	if (PANEL_WORDS.includes(key)) return `${key}-stage`;
	return key || "none";
}

// The views of P6.3 (and P6.4's list), each its own route so Back walks between them. The board's
// own route segment is its layout, as it was before the other views existed, so an address saved
// from part A still opens the same screen.
export const VIEWS = ["board", "table", "timeline", "attention"];
export const OTHER_VIEWS = ["table", "timeline", "attention"];

// What the address asks for: { view, layout, stage, panel, side, bare }, or null when it is not this
// page. `panel` is the move panel's deal; `side` the side panel (part C), or null:
//   { kind: "edit", name, step } | { kind: "closing", name, stage } | { kind: "new", stage }
//   | { kind: "bulk", action }
//   [PAGE]                                  the board, stage columns (bare: a remembered view may apply)
//   [PAGE, layout]                          the board: stages | streams
//   [PAGE, layout, stage]                   the board, on the phone's chosen stage tab
//   [PAGE, layout, MOVE, name]              the deal panel for a deal, over the board
//   [PAGE, layout, stage, MOVE, name]       the same, over a chosen stage tab
//   [PAGE, view]                            table | timeline | attention (layout "": the page keeps its own)
//   [PAGE, view, MOVE, name]                the deal panel over one of them
//   [..., EDIT, name, step?]                the side panel, editing a deal, over any of the above
//   [..., CLOSING, name, stage]             the side panel: a move into a closing stage
//   [..., NEW, stage]                       the side panel: quick add in that stage
//   [..., BULK, action]                     the side panel: a bulk action on the selected deals
function parse_panel(parts) {
	const word = text(parts[0]);
	if (word === MOVE) return { panel: text(parts[1]), side: null };
	if (word === EDIT && text(parts[1])) return { panel: "", side: { kind: EDIT, name: text(parts[1]), step: text(parts[2]) } };
	if (word === CLOSING && text(parts[1]) && text(parts[2])) {
		return { panel: "", side: { kind: CLOSING, name: text(parts[1]), stage: text(parts[2]) } };
	}
	if (word === NEW && text(parts[1])) return { panel: "", side: { kind: NEW, stage: text(parts[1]) } };
	if (word === BULK && BULK_ACTIONS.includes(text(parts[1]))) return { panel: "", side: { kind: BULK, action: text(parts[1]) } };
	return { panel: "", side: null };
}

export function parse_route(route) {
	if (!Array.isArray(route) || route[0] !== PAGE) return null;
	const first = text(route[1]);
	if (OTHER_VIEWS.includes(first)) {
		const { panel, side } = parse_panel(route.slice(2));
		return { view: first, layout: "", stage: "", panel, side, bare: false };
	}
	const layout = LAYOUTS.includes(first) ? first : DEFAULT_LAYOUT;
	let stage = "";
	let rest = route.slice(2);
	if (!PANEL_WORDS.includes(text(route[2]))) {
		stage = text(route[2]);
		rest = route.slice(3);
	}
	const { panel, side } = parse_panel(rest);
	return { view: "board", layout, stage, panel, side, bare: !first };
}

function side_parts(side) {
	const s = side || null;
	if (!s) return [];
	if (s.kind === EDIT && text(s.name)) return text(s.step) ? [EDIT, text(s.name), text(s.step)] : [EDIT, text(s.name)];
	if (s.kind === CLOSING && text(s.name) && text(s.stage)) return [CLOSING, text(s.name), text(s.stage)];
	if (s.kind === NEW && text(s.stage)) return [NEW, text(s.stage)];
	if (s.kind === BULK && BULK_ACTIONS.includes(s.action)) return [BULK, s.action];
	return [];
}

// The route for a screen, as frappe.set_route takes it. A side panel wins over the move panel: one
// panel at a time.
export function build_route(target) {
	const t = target || {};
	const side = side_parts(t.side);
	const route = OTHER_VIEWS.includes(t.view) ? [PAGE, t.view] : [PAGE, LAYOUTS.includes(t.layout) ? t.layout : DEFAULT_LAYOUT];
	if (!OTHER_VIEWS.includes(t.view) && text(t.stage)) route.push(text(t.stage));
	if (side.length) return route.concat(side);
	if (text(t.panel)) route.push(MOVE, text(t.panel));
	return route;
}

// The side panel's identity, without its step: the step is replaced in the address as the person
// moves through the deal, and it is still the same panel (so a close still steps back off it).
export function side_key(side) {
	const s = side || null;
	if (!s) return "";
	if (s.kind === EDIT) return `${EDIT}:${text(s.name)}`;
	if (s.kind === CLOSING) return `${CLOSING}:${text(s.name)}:${text(s.stage)}`;
	if (s.kind === NEW) return `${NEW}:${text(s.stage)}`;
	if (s.kind === BULK) return `${BULK}:${text(s.action)}`;
	return "";
}

// One string per screen, so "is that the screen already showing?" is a comparison.
export function route_key(target) {
	if (!target) return "";
	const view = OTHER_VIEWS.includes(target.view) ? target.view : "board";
	const layout = view === "board" ? target.layout || DEFAULT_LAYOUT : "";
	const side = side_parts(target.side).join("/");
	return [view, layout, view === "board" ? target.stage || "" : "", side ? "" : target.panel || "", side].join("|");
}

// Whether the last-used view's layout may apply on arrival. An explicit board address (/stages,
// /streams, with or without a stage tab) wins, as the rule on the planners has it; a bare address,
// or one of the other views (whose routes carry no layout), takes the remembered one. The filters
// and the sort are not in any address, so they always come back.
export function last_layout_applies(target) {
	return !target || !!target.bare || target.view !== "board";
}

// A click on the board's layout switch: the route to go to, and whether it is a new one. When the
// address already says that layout there is nothing to navigate to, so the page must redraw itself
// (a click that changed this.layout without a redraw did nothing).
export function layout_change(target, layout) {
	const t = target || {};
	const next = {
		view: "board",
		layout: LAYOUTS.includes(layout) ? layout : DEFAULT_LAYOUT,
		stage: t.view === "board" ? text(t.stage) : "",
		panel: "",
	};
	const now = t.view === "board" && !t.bare ? parse_route(build_route(t)) : t;
	return { next, navigate: route_key(parse_route(build_route(next))) !== route_key(now) };
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
	deal.owner = text(raw.owner);
	deal.owner_name = text(raw.owner_name) || deal.owner;
	deal.snoozed_until = text(raw.snoozed_until).slice(0, 10);
	deal.snooze_reason = text(raw.snooze_reason);
	deal.snooze = snooze_state(deal, today);
	return deal;
}

// P6.8. "" (not snoozed), "snoozed" (hidden until its date, which is after today) or "returning"
// (the date has come: back on the board, marked, until someone wakes it or snoozes it again).
export function snooze_state(deal, today) {
	const until = text((deal || {}).snoozed_until).slice(0, 10);
	if (!until) return "";
	const a = day_number(until);
	const b = day_number(today);
	if (a === null || b === null) return "";
	return a > b ? "snoozed" : "returning";
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
	const add = (label, kind, gate) => {
		const l = text(label);
		if (!l || by_label[l]) return;
		const stage = { key: stage_key(l), label: l, kind: KINDS.includes(kind) ? kind : "open" };
		// Part C: a closing stage says which required fields its move needs ("won" | "lost").
		if (stage.kind === "close") stage.gate = GATES.includes(gate) ? gate : "won";
		// Two labels that slug alike ("On Hold" / "On-Hold") keep the first; the second is a
		// different status, so it gets a key of its own.
		let n = 2;
		while (stages.some((s) => s.key === stage.key)) stage.key = `${stage_key(l)}-${n++}`;
		by_label[l] = stage;
		stages.push(stage);
	};
	(d.stages || []).forEach((s) => add((s || {}).label, (s || {}).kind, (s || {}).gate));
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
		can_snooze: !!d.can_snooze,
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
//   "close"   Closed Won or Lost: open the side panel's required fields for it (part C, P6.6); the
//             card stays where it is until the move is saved (the guided Close step if the panel's
//             files did not load)
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

// Whether a get_board payload may be drawn. `started` is the move counter when the load began,
// `epoch` its value now, `busy` how many saves (and drags) are under way. A load that began before a
// move, or that lands while one is saving, describes the board from before it: drawing it would put
// the moved card back in its old column with its old `modified`, and the save's new `modified`
// would then land on that stale copy. The page drops it and loads again once nothing is busy.
export function load_is_current(started, epoch, busy) {
	return Number(started) === Number(epoch) && !(Number(busy) > 0);
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

// ------------------------------------------------------------------ focus: filters (P6.8)

// The filter bar the board, table, timeline and attention list share. Kept by the page, not the
// address, so switching views keeps them (P6.3), and remembered per person on the server with the
// view and layout (api/planner_views_prefs.py, planner "board": the planners' own saved-views store).
//   mine           only deals whose Opportunity Owner is the person looking
//   owner          one owner (a user id)
//   stream         one value stream, or NO_STREAM_FILTER for deals with none
//   min_amount     "over $X": an amount above this; a deal with no amount is left out
//   closing_month  expected to close in the site's current month
//   show_snoozed   snoozed deals shown (marked) rather than hidden
//   hold_open      the On Hold column unfolded (folded into a count by default: 36 of 74 open deals
//                  sat there on 2026-10-14)
export const NO_STREAM_FILTER = "-";
export const DEFAULT_FILTERS = {
	mine: false,
	owner: "",
	stream: "",
	min_amount: "",
	closing_month: false,
	show_snoozed: false,
	hold_open: false,
};
export const SORT_KEYS = ["title", "stage", "amount", "probability", "weighted", "closing", "days", "owner", "complete"];
export const DEFAULT_SORT = { sort: "closing", desc: false };

// What a saved view may hold, key for key api/planner_views_prefs.SCHEMA["board"] (the test holds
// the two equal): `{key: ["bool"] | ["text"] | ["enum", values]}`.
export const VIEW_SCHEMA = {
	view: ["enum", VIEWS],
	layout: ["enum", LAYOUTS],
	mine: ["bool"],
	owner: ["text"],
	stream: ["text"],
	min_amount: ["text"],
	closing_month: ["bool"],
	show_snoozed: ["bool"],
	hold_open: ["bool"],
	sort: ["enum", SORT_KEYS],
	desc: ["bool"],
};

function flag(value) {
	return value === true || value === 1 || value === "1" || value === "true";
}

// "$25,000" -> 25000; anything that is not a positive number -> 0 (no limit).
export function amount_limit(value) {
	const n = Number(text(value).replace(/[$,\s]/g, ""));
	return Number.isFinite(n) && n > 0 ? n : 0;
}

// Only the filter keys, each of its type, defaults for the rest.
export function clean_filters(raw) {
	const r = raw && typeof raw === "object" ? raw : {};
	return {
		mine: flag(r.mine),
		owner: text(r.owner),
		stream: text(r.stream),
		min_amount: amount_limit(r.min_amount) ? String(amount_limit(r.min_amount)) : "",
		closing_month: flag(r.closing_month),
		show_snoozed: flag(r.show_snoozed),
		hold_open: flag(r.hold_open),
	};
}

// How many filters narrow the deals (hold_open and show_snoozed only fold or widen: not counted).
export function active_filters(filters) {
	const f = clean_filters(filters);
	return [f.mine, !!f.owner, !!f.stream, !!f.min_amount, f.closing_month].filter(Boolean).length;
}

// Whether one deal passes the filters, for `user` on `today`. Snoozing is separate (filtered).
export function passes(deal, filters, user, today) {
	const f = clean_filters(filters);
	if (!deal) return false;
	if (f.mine && (!text(user) || deal.owner !== text(user))) return false;
	if (f.owner && deal.owner !== f.owner) return false;
	if (f.stream === NO_STREAM_FILTER) {
		if ((deal.streams || []).length) return false;
	} else if (f.stream && !(deal.streams || []).includes(f.stream)) return false;
	const limit = amount_limit(f.min_amount);
	if (limit && !((amount_of(deal) || 0) > limit)) return false;
	if (f.closing_month && (!month_key(today) || month_key(deal.expected_closing) !== month_key(today))) return false;
	return true;
}

// The model as the filters show it: the same stages, today and thresholds, `deals` narrowed, and
// `by_name` still the whole board (a panel may be open on a deal the filters hide). Also `snoozed`,
// how many snoozed deals pass the filters, and `hidden`, how many of those are hidden. Every view
// and the forecast strip draw from this, so the strip forecasts what is shown.
export function filtered(model, filters, user) {
	const m = model || {};
	const f = clean_filters(filters);
	const passing = (m.deals || []).filter((deal) => passes(deal, f, user, m.today));
	const snoozed = passing.filter((deal) => deal.snooze === "snoozed").length;
	const deals = f.show_snoozed ? passing : passing.filter((deal) => deal.snooze !== "snoozed");
	return Object.assign({}, m, { deals, snoozed, hidden: passing.length - deals.length, filters: f });
}

// The owners on the board, for the owner filter: [{ id, name }] by name. A `selected` owner no deal
// has now (a saved view's, or a deal reassigned since) is kept as a choice, named `selected_name`
// when the page knows it, so the select shows the filter that is really applied, never "Anyone".
export function owners(model, selected, selected_name) {
	const seen = {};
	((model || {}).deals || []).forEach((deal) => {
		if (deal.owner && !seen[deal.owner]) seen[deal.owner] = deal.owner_name || deal.owner;
	});
	const keep = text(selected);
	if (keep && !seen[keep]) seen[keep] = text(selected_name) || keep;
	return Object.keys(seen)
		.map((id) => ({ id, name: seen[id] }))
		.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

// The streams on the board, for the stream filter, in row order; NO_STREAM_FILTER last when a deal
// has none. A `selected` stream no deal has is kept, after the rest, for the same reason as owners.
export function stream_choices(model, selected) {
	const choices = present_streams(model);
	const keep = text(selected);
	if (keep && !choices.includes(keep)) choices.push(keep);
	return choices;
}

function present_streams(model) {
	const found = new Set();
	let none = false;
	((model || {}).deals || []).forEach((deal) => {
		if (!(deal.streams || []).length) none = true;
		(deal.streams || []).forEach((s) => found.add(s));
	});
	const known = STREAM_ORDER.filter((s) => found.has(s));
	const extra = Array.from(found)
		.filter((s) => !STREAM_ORDER.includes(s))
		.sort();
	return known.concat(extra, none ? [NO_STREAM_FILTER] : []);
}

// Only the keys VIEW_SCHEMA allows, each of its type (a saved view as the page applies it).
export function pick_view(state) {
	const out = {};
	const s = state && typeof state === "object" ? state : {};
	Object.keys(VIEW_SCHEMA).forEach((key) => {
		if (!Object.prototype.hasOwnProperty.call(s, key)) return;
		const rule = VIEW_SCHEMA[key];
		const value = s[key];
		if (rule[0] === "bool") out[key] = flag(value);
		else if (rule[0] === "enum") {
			if (typeof value === "string" && rule[1].includes(value)) out[key] = value;
		} else if (value === null || value === undefined) out[key] = "";
		else if (typeof value === "string" || typeof value === "number") out[key] = text(value).slice(0, 140);
	});
	return out;
}

// ------------------------------------------------------------------ needs attention (P6.4)

// Why a deal needs attention, in a fixed order; [] when it does not:
//   stale       past the glow thresholds (level 1 or 2; On Hold never glows, so is never stale)
//   overdue     its expected close date is before today
//   no_amount   no amount above zero
//   incomplete  missing what its stage asks for (form kit P4.6's completeness)
// A deal that is snoozed (its date still ahead) needs nothing until it returns.
export const REASONS = ["stale", "overdue", "no_amount", "incomplete"];

export function attention_reasons(deal, today) {
	if (!deal || deal.snooze === "snoozed") return [];
	const out = [];
	if (deal.stale > 0) out.push("stale");
	const close = day_number(deal.expected_closing);
	const now = day_number(today);
	if (close !== null && now !== null && close < now) out.push("overdue");
	if (amount_of(deal) === null) out.push("no_amount");
	const m = meter(deal.completeness);
	if (m && m.done < m.total) out.push("incomplete");
	return out;
}

// The list, highest value first (no amount last), then by title: { items: [{ deal, reasons }],
// counts per reason, total (each deal once) }.
export function attention(model) {
	const m = model || {};
	const items = (m.deals || [])
		.map((deal) => ({ deal, reasons: attention_reasons(deal, m.today) }))
		.filter((item) => item.reasons.length);
	items.sort((a, b) => {
		const va = amount_of(a.deal);
		const vb = amount_of(b.deal);
		if (va !== vb) {
			if (va === null) return 1;
			if (vb === null) return -1;
			return vb - va;
		}
		return a.deal.title.localeCompare(b.deal.title) || a.deal.name.localeCompare(b.deal.name);
	});
	const counts = {};
	REASONS.forEach((reason) => {
		counts[reason] = items.filter((item) => item.reasons.includes(reason)).length;
	});
	return { items, counts, total: items.length };
}

// ------------------------------------------------------------------ the table (P6.3)

function sort_value(model, deal, key) {
	switch (key) {
		case "title":
			return deal.title.toLowerCase();
		case "stage": {
			const index = ((model || {}).stages || []).findIndex((s) => s.key === deal.stage);
			return index === -1 ? null : index;
		}
		case "amount":
			return amount_of(deal);
		case "probability":
			return deal.probability;
		case "weighted":
			return amount_of(deal) === null || deal.kind === "parked" ? null : weighted_of(deal);
		case "closing":
			return day_number(deal.expected_closing);
		case "days":
			return deal.days === undefined ? null : deal.days;
		case "owner":
			return (deal.owner_name || "").toLowerCase() || null;
		case "complete": {
			const m = meter(deal.completeness);
			return m ? m.percent : null;
		}
		default:
			return null;
	}
}

// The deals sorted by a column. A blank (no amount, no close date) sorts last whichever way the
// column is turned, so turning it never buries the deals that have one. Ties go by title.
export function table_rows(model, sort) {
	const key = SORT_KEYS.includes((sort || {}).sort) ? sort.sort : DEFAULT_SORT.sort;
	const desc = flag((sort || {}).desc);
	const deals = ((model || {}).deals || []).slice();
	deals.sort((a, b) => {
		const va = sort_value(model, a, key);
		const vb = sort_value(model, b, key);
		const blank_a = va === null || va === undefined || va === "";
		const blank_b = vb === null || vb === undefined || vb === "";
		if (blank_a !== blank_b) return blank_a ? 1 : -1;
		if (!blank_a && va !== vb) {
			const order = va < vb ? -1 : 1;
			return desc ? -order : order;
		}
		return a.title.localeCompare(b.title) || a.name.localeCompare(b.name);
	});
	return deals;
}

// A click on a column head: the same column turns around; another starts ascending, except the
// numbers people read biggest-first (amount, probability, weighted, days, complete).
export function next_sort(sort, key) {
	const current = sort || DEFAULT_SORT;
	if (!SORT_KEYS.includes(key)) return { sort: current.sort, desc: !!current.desc };
	if (current.sort === key) return { sort: key, desc: !current.desc };
	return { sort: key, desc: ["amount", "probability", "weighted", "days", "complete"].includes(key) };
}

// ------------------------------------------------------------------ the timeline (P6.3)

// Expected close dates by month: [{ key: "2026-10", past, current, deals (by date, then title),
// count, total, weighted }], earliest first, then { key: "" } for the deals with no close date.
// Only months that hold a deal are listed. `past` is a month before the site's current one: every
// deal in it is overdue.
export function timeline(model) {
	const m = model || {};
	const now = month_key(m.today);
	const groups = {};
	(m.deals || []).forEach((deal) => {
		const key = month_key(deal.expected_closing);
		(groups[key] = groups[key] || []).push(deal);
	});
	const keys = Object.keys(groups)
		.filter(Boolean)
		.sort();
	if (groups[""]) keys.push("");
	return keys.map((key) => {
		const deals = groups[key].slice().sort((a, b) => {
			const da = day_number(a.expected_closing);
			const db = day_number(b.expected_closing);
			if (da !== db) return (da === null ? Infinity : da) - (db === null ? Infinity : db);
			return a.title.localeCompare(b.title) || a.name.localeCompare(b.name);
		});
		return Object.assign({ key, past: !!key && !!now && key < now, current: !!key && key === now, deals }, figures(deals));
	});
}

// ------------------------------------------------------------------ snoozing (P6.8)

// The earliest date a snooze may run to: tomorrow, on the site's date.
export function snooze_min(today) {
	const n = day_number(today);
	if (n === null) return "";
	return new Date((n + 1) * 86400000).toISOString().slice(0, 10);
}

// The latest date a snooze may run to: the site's today plus MAX_SNOOZE_DAYS, the server's own cap
// (opportunity_board.MAX_SNOOZE_DAYS; the test holds the two equal).
export const MAX_SNOOZE_DAYS = 366;

export function snooze_max(today) {
	const n = day_number(today);
	if (n === null) return "";
	return new Date((n + MAX_SNOOZE_DAYS) * 86400000).toISOString().slice(0, 10);
}

// The model with one deal's snooze set (or cleared, with until ""), as the server saved it.
export function set_snooze(model, name, until, reason, modified) {
	const m = model || {};
	const old = (m.by_name || {})[name];
	if (!old) return m;
	const deal = Object.assign({}, old, {
		snoozed_until: text(until).slice(0, 10),
		snooze_reason: text(until) ? text(reason) : "",
		modified: text(modified) || old.modified,
	});
	deal.snooze = snooze_state(deal, m.today);
	return replace_deal(m, deal);
}

// ------------------------------------------------------------------ the side panel (part C)

// Whether a column takes quick add's "+" (P6.7): an open stage or On Hold. Never a closing stage
// (those need their required fields first) nor a status the options no longer list.
export function can_add(model, key) {
	const stage = stage_by_key(model, key);
	return !!stage && (stage.kind === "open" || stage.kind === "parked");
}

// The closing stage a key names (with its `gate`, "won" | "lost"), or null.
export function closing_stage(model, key) {
	const stage = stage_by_key(model, key);
	return stage && stage.kind === "close" ? stage : null;
}

// Whether the side panel an address asks for can be shown on this board. When it cannot (a deal
// that has left the board, a stage that is gone, a bulk action with nothing selected), the page puts
// the view back in its place, as it does for the move panel. An edit can open any deal the person
// can read: the panel reads it itself.
export function side_ok(model, side, picks) {
	const s = side || null;
	if (!s) return false;
	if (s.kind === EDIT) return !!text(s.name);
	if (s.kind === CLOSING) return !!((model || {}).by_name || {})[text(s.name)] && !!closing_stage(model, s.stage);
	if (s.kind === NEW) return can_add(model, s.stage);
	if (s.kind === BULK) return BULK_ACTIONS.includes(s.action) && (picks || []).length > 0;
	return false;
}

// The model without one deal: a deal moved to Closed Won or Lost leaves the open board at once,
// before the refresh confirms it.
export function remove_deal(model, name) {
	const m = model || {};
	if (!(m.by_name || {})[name]) return m;
	const by_name = Object.assign({}, m.by_name);
	delete by_name[name];
	return Object.assign({}, m, { deals: (m.deals || []).filter((d) => d.name !== name), by_name });
}

// ------------------------------------------------------------------ bulk (P6.7)

// The selection, kept per tab (sessionStorage, guarded by the page) so a reload on a bulk panel's
// address finds what was selected.
export const PICKS_KEY = "ob-picks";

export function toggle_pick(picks, name) {
	const list = (picks || []).filter(Boolean);
	const n = text(name);
	if (!n) return list;
	return list.includes(n) ? list.filter((p) => p !== n) : list.concat([n]);
}

// Only deals on the board, each once, at most MAX_BULK.
export function clean_picks(model, picks) {
	const by_name = (model || {}).by_name || {};
	const out = [];
	(Array.isArray(picks) ? picks : []).forEach((value) => {
		const n = text(value);
		if (n && by_name[n] && !out.includes(n) && out.length < MAX_BULK) out.push(n);
	});
	return out;
}

// The stages a bulk change of stage offers: every board column but an unlisted status, then the
// closing stages (which ask for their required fields first, as a drop does).
export function bulk_stages(model) {
	return ((model || {}).stages || []).filter((s) => s.kind !== "other");
}

// What a bulk request carries: the deals, each with the `modified` the board has for it (the same
// lock a move takes), the action and its values.
export function bulk_request(model, picks, action, values) {
	const by_name = (model || {}).by_name || {};
	const names = clean_picks(model, picks);
	const modified = {};
	names.forEach((name) => (modified[name] = by_name[name].modified));
	return { names, modified, action, values: values || {} };
}

// The server's per-deal answer, sorted: { ok: [name], conflicts: [{name, modified_by}], failed:
// [{name, message}], total }. A deal the answer does not mention failed (it was never reached).
export function bulk_outcome(names, results) {
	const by = {};
	(Array.isArray(results) ? results : []).forEach((r) => {
		if (r && text(r.name)) by[text(r.name)] = r;
	});
	const out = { ok: [], conflicts: [], failed: [], total: (names || []).length };
	(names || []).forEach((name) => {
		const r = by[name];
		if (r && r.ok) out.ok.push(name);
		else if (r && r.conflict) out.conflicts.push({ name, modified_by: text(r.modified_by) });
		else out.failed.push({ name, message: text(r && r.message) });
	});
	return out;
}

// What stays selected after a bulk action: the deals it did not change, so the person can look and
// try again.
export function after_bulk(picks, outcome) {
	const done = new Set((outcome || {}).ok || []);
	return (picks || []).filter((name) => !done.has(name));
}
