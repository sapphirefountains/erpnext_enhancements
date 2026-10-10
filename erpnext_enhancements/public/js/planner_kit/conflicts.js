/*
 * Planner kit: the Conflict center (Phase 6D UI, TASK-2026-02470), shared by both planners.
 *
 * Nik, 2026-10-09: "one list of everything wrong right now" (double bookings, people over their
 * hours, work on a day off or on a personal block, truck and equipment clashes, missing
 * qualifications) with one-click fixes. The list is `api/planner_conflicts.get_conflicts(start, end,
 * planner)`: the server works out every conflict AND every fix, naming the existing endpoint and the
 * exact arguments to send. This module draws the list and hands a fix back to the page, which sends
 * it through its own `send` path, so the reason prompt, Draft mode and Undo all apply:
 *
 *   const center = planner_kit.conflicts.center({
 *     planner: "project" | "maintenance", owner, push, width,
 *     range: () => ({start, end, label}) | null,       // the dates on screen; null off the calendar
 *     can_schedule: () => bool, draft: () => bool,
 *     block_note: (name) => text,                      // a block's note, ONLY from the page's block_notes
 *     run_fix: ({conflict, fix, args, picked}) => Promise,   // the page's own write path
 *     open_item: (conflict, item) => ...,              // its side panel, or a read-only link elsewhere
 *     exclude: (conflict, fix) => ({key: reason}),     // who cannot be picked (already on it)
 *     pick_context: (conflict, fix) => text,           // "In place of Austin on Dig"
 *     on_list: (list) => ...,                          // the toolbar count, the markers on cards
 *   });
 *   center.open(focus)  // focus: {key} | {ref} | {date}
 *   center.schedule() · refresh() · list() · count() · find(doctype, name, key) · is_open()
 *
 * Rules kept here, because each fails quietly:
 *
 *   - "Pick someone who's free" never picks. The who-is-free answer is listed (free people first,
 *     busy ones greyed with their reason) and nothing is selected, focused for submit or applied
 *     until a person is tapped; `fix_args` refuses a pick fix without the person tapped.
 *   - "Keep it with a reason" needs a reason: an empty one never reaches the server. A stale
 *     conflict (its records changed since the list loaded) is refused by the server; the list is
 *     reloaded and says so.
 *   - A fix is looked up again in the newest list when it runs, so it carries the newest `modified`.
 *   - A block's note comes only from `block_note()`; the conflict's own copy is never read.
 *
 * The renderers are pure, for tests/test_planner_phase6d_ui.py.
 */

import { escape, t, hours, day_label, glow_key, glow_keys } from "./escape.js";

export const METHODS = {
	get: "erpnext_enhancements.api.planner_conflicts.get_conflicts",
	acknowledge: "erpnext_enhancements.api.planner_conflicts.acknowledge_conflict",
	who_is_free: "erpnext_enhancements.api.project_planner.who_is_free",
};

// After a load the count waits for the page to settle; an open drawer reloads sooner.
const COUNT_MS = 700;
const OPEN_MS = 80;

const ICON = (paths) =>
	`<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">${paths}</svg>`;

// Every kind the server sends, in its order, with a short name and a small icon (fixed markup).
export const KIND_INFO = {
	overbooked: { label: "Over hours", icon: ICON('<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>') },
	overlap: {
		label: "Double-booked",
		icon: ICON('<rect x="1.5" y="2.5" width="8" height="8" rx="1.5"/><rect x="6.5" y="5.5" width="8" height="8" rx="1.5"/>'),
	},
	day_off: {
		label: "Day off",
		icon: ICON('<rect x="2" y="3" width="12" height="11" rx="1.5"/><path d="M2 6.5h12M6.2 8.8l3.6 3.4M9.8 8.8l-3.6 3.4"/>'),
	},
	blocked: { label: "Unavailable", icon: ICON('<circle cx="8" cy="8" r="6"/><path d="M3.8 3.8l8.4 8.4"/>') },
	equipment: {
		label: "Equipment",
		icon: ICON('<path d="M1.5 4h8v7h-8zM9.5 6.5h3l2 2.5v2h-5"/><circle cx="4.5" cy="12" r="1.3"/><circle cx="11.5" cy="12" r="1.3"/>'),
	},
	qualification: {
		label: "Qualification",
		icon: ICON('<circle cx="8" cy="6.5" r="4"/><path d="M5.6 9.8l-1 4.7L8 13l3.4 1.5-1-4.7"/>'),
	},
};

const ITEM_KINDS = { task: "Task", rental: "Rental", visit: "Visit", travel: "Travel" };

// ---------------------------------------------------------------------- pure helpers

// How many conflicts still need looking at: the ones nobody kept.
export function open_count(list) {
	return (Array.isArray(list) ? list : []).filter((conflict) => conflict && !conflict.acknowledged).length;
}

export function kept_count(list) {
	return (Array.isArray(list) ? list : []).filter((conflict) => conflict && conflict.acknowledged).length;
}

// The conflicts by day, in date order, each day's in the server's order. `index` is the position in
// the list the server sent, which is what the buttons carry. Kept ones only when asked for.
export function group_by_day(list, show_kept) {
	const groups = [];
	const by_date = {};
	(Array.isArray(list) ? list : []).forEach((conflict, index) => {
		if (!conflict || (conflict.acknowledged && !show_kept)) return;
		const date = String(conflict.date || "");
		if (!by_date[date]) {
			by_date[date] = { date, conflicts: [] };
			groups.push(by_date[date]);
		}
		by_date[date].conflicts.push({ conflict, index });
	});
	return groups.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}

// What clicking a record does: its own planner opens it there; anything else is a read-only link.
export function item_link(item) {
	item = item || {};
	if (item.own) return { action: "open", label: t("Open") };
	if (item.planner === "maintenance") return { action: "maintenance", label: t("Open in Maintenance Planner") };
	if (item.planner === "project") return { action: "project", label: t("Open in Project Planner") };
	if (item.planner === "travel") return { action: "trip", label: t("Open trip") };
	if (item.planner === "rental") return { action: "task", label: t("Open task") };
	return { action: "", label: "" };
}

// The fixes for one record of a conflict, with their positions in `conflict.fixes`.
export function fixes_for_item(conflict, item) {
	return ((conflict && conflict.fixes) || [])
		.map((fix, index) => ({ fix, index }))
		.filter(({ fix }) => fix && fix.type !== "keep" && fix.doctype === item.doctype && fix.name === item.name);
}

// `{"Task|TASK-1": [{key, kind, date, resource, user}], "key|<projected key>": [...]}` for the
// conflicts nobody kept: what puts a marker on a card.
export function index_by_ref(list) {
	const out = {};
	const add = (ref, conflict) => {
		(out[ref] = out[ref] || []).push({
			key: conflict.key,
			kind: conflict.kind,
			date: conflict.date,
			resource: conflict.resource || null,
			user: conflict.user || null,
		});
	};
	(Array.isArray(list) ? list : []).forEach((conflict) => {
		if (!conflict || conflict.acknowledged) return;
		(conflict.items || []).forEach((item) => {
			if (!item || !item.name) return;
			add(`${item.doctype}|${item.name}`, conflict);
			if (item.key) add(`key|${item.key}`, conflict);
		});
	});
	return out;
}

// The arguments a fix sends: exactly the server's, plus, for "Pick someone who's free", the person
// the user tapped. Null when a pick is needed and nobody was tapped: nothing is filled in for them.
export function fix_args(fix, picked) {
	if (!fix || !fix.args || typeof fix.args !== "object") return null;
	const args = JSON.parse(JSON.stringify(fix.args));
	if (fix.type === "pick_free") {
		const pick = fix.pick || {};
		const value = picked && pick.field ? picked[pick.field] : null;
		if (!pick.arg || !value) return null;
		args[pick.arg] = value;
	}
	return args;
}

// Who could take it, from a who_is_free answer: everyone free on every day asked about first (most
// hours spare first), then everyone else with the first reason they are not. Several days (a
// qualification gap over a task's span) count a person free only when they are free on all of them.
export function free_people(answer) {
	const days = answer && Array.isArray(answer.days) ? answer.days : [];
	const several = days.length > 1;
	const order = [];
	const people = {};
	days.forEach((day) => {
		[
			["free", true],
			["not_free", false],
		].forEach(([list, ok]) => {
			(day[list] || []).forEach((entry) => {
				if (!entry) return;
				const key = entry.resource || entry.user || entry.label;
				if (!key) return;
				let person = people[key];
				if (!person) {
					person = people[key] = {
						resource: entry.resource || null,
						user: entry.user || null,
						label: entry.label || String(key),
						group: entry.group || null,
						free: true,
						free_hours: null,
						reason: "",
						seen: 0,
					};
					order.push(key);
				}
				person.seen += 1;
				const spare = Math.max(0, Number(entry.free_hours) || 0);
				person.free_hours = person.free_hours === null ? spare : Math.min(person.free_hours, spare);
				if (!ok && person.free) {
					person.free = false;
					const why = entry.reason || t("Busy");
					person.reason = several ? `${day_label(day.date)}: ${why}` : why;
				}
			});
		});
	});
	const all = order.map((key) => people[key]);
	all.forEach((person) => {
		if (person.free && person.seen < days.length) {
			person.free = false;
			person.reason = t("Not on the planner every day");
		}
		delete person.seen;
		if (person.free_hours === null) person.free_hours = 0;
	});
	const free = all
		.filter((person) => person.free)
		.sort((a, b) => b.free_hours - a.free_hours || String(a.label).localeCompare(String(b.label)));
	const busy = all.filter((person) => !person.free).sort((a, b) => String(a.label).localeCompare(String(b.label)));
	return free.concat(busy);
}

function who_of(conflict) {
	if (conflict.kind === "equipment") return (conflict.equipment && conflict.equipment.label) || conflict.resource_label || "";
	if (conflict.kind === "qualification") return ((conflict.items || [])[0] || {}).title || "";
	return conflict.resource_label || "";
}

function item_html(conflict, item, ci, ii, busy) {
	const link = item_link(item);
	const kind = item.projected ? t("Projected visit") : t(ITEM_KINDS[item.kind] || "Booking");
	const slot = Array.isArray(item.slot) && item.slot.length === 2 ? item.slot.join("–") : "";
	const meta = [kind, slot, Number(item.hours) > 0 ? `${hours(item.hours)}h` : ""].filter(Boolean).join(" · ");
	const name = escape(item.title || item.name || "");
	const title = item.own
		? `<span class="pk-link pk-cc-title" role="button" tabindex="0" data-pk-cc-item="${ci}.${ii}" title="${escape(
				t("Open it here")
		  )}">${name}</span>`
		: `<span class="pk-cc-title">${name}</span>`;
	const open =
		!item.own && link.action
			? `<button type="button" class="btn btn-default btn-xs pk-cc-open" data-pk-cc-item="${ci}.${ii}">${escape(link.label)}</button>`
			: "";
	const fixes = fixes_for_item(conflict, item)
		.map(
			({ fix, index }) =>
				`<button type="button" class="btn btn-default btn-xs pk-cc-fix" data-pk-cc-fix="${ci}.${index}"${
					busy ? " disabled" : ""
				}>${escape(fix.label || "")}</button>`
		)
		.join("");
	return (
		`<li class="pk-cc-record"><div class="pk-cc-record-main">${title}<span class="pk-cc-meta">${escape(meta)}</span>${open}</div>` +
		(fixes ? `<div class="pk-cc-fixes">${fixes}</div>` : "") +
		`</li>`
	);
}

function keep_html(conflict, ci, ctx, busy) {
	const fixes = conflict.fixes || [];
	const index = fixes.findIndex((fix) => fix && fix.type === "keep");
	if (index < 0 || conflict.acknowledged) return "";
	const allowed = !!(conflict.own || ctx.can_schedule);
	if (allowed && ctx.keep_key && ctx.keep_key === conflict.key) {
		const id = `pk-cc-reason-${ci}`;
		return (
			`<div class="pk-cc-keep"><label for="${id}">${escape(t("Why keep it? The reason goes on the timeline."))}</label>` +
			`<textarea id="${id}" class="form-control" rows="2" required aria-required="true" data-pk-cc-reason></textarea>` +
			`<div class="pk-cc-keep-error" data-pk-cc-keep-error hidden>${escape(t("Say why it is being kept."))}</div>` +
			(ctx.draft ? `<div class="pk-cc-hint">${escape(t("Keeping a conflict is saved straight away, even in Draft mode."))}</div>` : "") +
			`<div class="pk-cc-fixes"><button type="button" class="btn btn-primary btn-xs" data-pk-cc-keep-save="${ci}"${
				busy ? " disabled" : ""
			}>${escape(t("Keep it"))}</button><button type="button" class="btn btn-default btn-xs" data-pk-cc-keep-cancel>${escape(
				t("Cancel")
			)}</button></div></div>`
		);
	}
	const tip = allowed ? "" : t("Only a planner can keep a conflict that has nothing on this planner in it.");
	return (
		`<div class="pk-cc-fixes pk-cc-keep-row"><button type="button" class="btn btn-default btn-xs pk-cc-fix" data-pk-cc-fix="${ci}.${index}"${
			busy || !allowed ? " disabled" : ""
		}${tip ? ` title="${escape(tip)}"` : ""}>${escape(fixes[index].label || t("Keep it with a reason"))}</button></div>`
	);
}

function conflict_html(conflict, ci, ctx) {
	const info = KIND_INFO[conflict.kind] || { label: "Conflict", icon: "" };
	const kind = KIND_INFO[conflict.kind] ? conflict.kind : "other";
	const busy = !!(ctx.busy_key && ctx.busy_key === conflict.key);
	const classes = ["pk-cc-item", `pk-cc-k-${kind}`];
	if (conflict.acknowledged) classes.push("pk-cc-kept");
	if (busy) classes.push("pk-busy");
	// A block's note only as the page has it (its block_notes), never the copy on the conflict.
	const block = conflict.block && conflict.block.name ? conflict.block.name : "";
	const note = block && typeof ctx.block_note === "function" ? String(ctx.block_note(block) || "") : "";
	const items = (conflict.items || []).map((item, ii) => item_html(conflict, item, ci, ii, busy)).join("");
	const ack = conflict.acknowledged
		? `<div class="pk-cc-ack">${escape(
				t("Kept by {0} on {1}: {2}", [
					conflict.acknowledged.by_name || conflict.acknowledged.by || "",
					day_label(conflict.acknowledged.on),
					conflict.acknowledged.reason || "",
				])
		  )}</div>`
		: "";
	const refs = glow_keys((conflict.items || []).map((item) => item && item.name).filter(Boolean));
	return (
		`<article class="${classes.join(" ")}" data-pk-cc-key="${escape(conflict.key)}" data-pk-cc-refs="${escape(refs)}">` +
		`<header class="pk-cc-head"><span class="pk-cc-icon" title="${escape(t(info.label))}">${info.icon}</span>` +
		`<b class="pk-cc-who">${escape(who_of(conflict))}</b><span class="pk-cc-kind">${escape(t(info.label))}</span></header>` +
		`<div class="pk-cc-msg">${escape(conflict.sentence || conflict.message || "")}</div>` +
		(note ? `<div class="pk-cc-note">${escape(t("Note: {0}", [note]))}</div>` : "") +
		`<ul class="pk-cc-records">${items}</ul>${ack}${keep_html(conflict, ci, ctx, busy)}</article>`
	);
}

// The whole list. `ctx`: {loading, show_kept, keep_key, busy_key, draft, can_schedule, block_note,
// focus_date, range_label}.
export function conflicts_html(list, ctx) {
	ctx = ctx || {};
	if (ctx.loading) return `<p class="pk-empty">${escape(t("Loading…"))}</p>`;
	if (!Array.isArray(list)) return `<p class="pk-empty">${escape(t("The conflicts could not be loaded. Try again."))}</p>`;
	const parts = [];
	if (ctx.draft) {
		parts.push(
			`<div class="pk-note pk-note-warn">${escape(
				t("Draft mode is on: a fix goes into your drafts, and this list shows the published schedule until you publish.")
			)}</div>`
		);
	}
	const open = open_count(list);
	const kept = kept_count(list);
	const pressed = ctx.show_kept ? "true" : "false";
	const toggle = kept
		? `<button type="button" class="btn btn-default btn-xs" data-pk-cc-toggle-kept aria-pressed="${pressed}">${escape(
				ctx.show_kept ? t("Hide kept ({0})", [kept]) : t("Show kept ({0})", [kept])
		  )}</button>`
		: "";
	parts.push(
		`<div class="pk-cc-bar"><span class="pk-cc-count">${escape(
			open ? t("{0} to look at", [open]) : t("Nothing to look at")
		)}</span>${toggle}</div>`
	);
	const groups = group_by_day(list, ctx.show_kept);
	if (ctx.focus_date && !groups.some((group) => group.date === ctx.focus_date)) {
		parts.push(`<p class="pk-empty">${escape(t("Nothing is wrong on {0}.", [day_label(ctx.focus_date)]))}</p>`);
	}
	if (!groups.length) {
		parts.push(
			`<p class="pk-empty">${escape(
				ctx.range_label ? t("Nothing is wrong in {0}.", [ctx.range_label]) : t("Nothing is wrong in these dates.")
			)}</p>`
		);
	}
	groups.forEach((group) => {
		parts.push(
			`<section class="pk-cc-day" data-pk-cc-day="${escape(group.date)}"><h4 class="pk-cc-day-head">${escape(
				day_label(group.date)
			)}</h4>${group.conflicts.map(({ conflict, index }) => conflict_html(conflict, index, ctx)).join("")}</section>`
		);
	});
	parts.push(`<p class="pk-note">${escape(t("Drive times here are estimates (saved and straight-line times)."))}</p>`);
	return `<div class="pk-cc">${parts.join("")}</div>`;
}

// The picker for "Pick someone who's free": `people` from free_people(). Nothing is selected; a
// tap on a person is the only thing that runs the fix. `ctx`: {title, context, field ("resource" |
// "user"), exclude: {key: reason}, loading, note}.
export function picker_html(people, ctx) {
	ctx = ctx || {};
	const field = ctx.field === "user" ? "user" : "resource";
	const exclude = ctx.exclude || {};
	const head =
		`<div class="pk-cc-pick-head"><button type="button" class="btn btn-default btn-xs" data-pk-cc-back>${escape(
			t("‹ Back to the conflicts")
		)}</button></div>` +
		`<h4 class="pk-cc-pick-title">${escape(ctx.title || t("Pick someone who's free"))}</h4>` +
		(ctx.context ? `<p class="pk-cc-pick-context">${escape(ctx.context)}</p>` : "") +
		`<p class="pk-note">${escape(
			t("Nobody is picked for you. Tap the person who should take it; a conflict it causes still asks for a reason.")
		)}</p>` +
		(ctx.note ? `<div class="pk-note pk-note-warn">${escape(ctx.note)}</div>` : "");
	if (ctx.loading) return `<div class="pk-cc-pick">${head}<p class="pk-empty">${escape(t("Loading…"))}</p></div>`;
	if (!Array.isArray(people)) {
		return `<div class="pk-cc-pick">${head}<p class="pk-empty">${escape(t("Who is free could not be loaded. Try again."))}</p></div>`;
	}
	if (!people.length) return `<div class="pk-cc-pick">${head}<p class="pk-empty">${escape(t("Nobody to show."))}</p></div>`;
	const row = (person, index) => {
		const key = person[field];
		const why = !key ? (field === "user" ? t("No user account to hand it to") : t("Not on the planner")) : exclude[key] || "";
		const facts = person.free ? t("{0}h free", [hours(person.free_hours)]) : person.reason || t("Busy");
		const classes = ["pk-cc-person"];
		if (!person.free) classes.push("pk-cc-busy");
		const tip =
			why ||
			(person.free
				? t("Give it to {0}", [person.label])
				: t("{0} is busy ({1}). You will be asked for a reason.", [person.label, facts]));
		return (
			`<li><button type="button" class="${classes.join(" ")}" data-pk-cc-pick="${index}"${why ? " disabled" : ""} title="${escape(
				tip
			)}"><span class="pk-cc-person-name">${escape(person.label)}</span><span class="pk-cc-person-facts">${escape(
				why || facts
			)}</span></button></li>`
		);
	};
	const free = [];
	const busy = [];
	people.forEach((person, index) => (person.free ? free : busy).push(row(person, index)));
	return (
		`<div class="pk-cc-pick">${head}` +
		`<div class="pk-section-title">${escape(t("Free ({0})", [free.length]))}</div>` +
		(free.length ? `<ul class="pk-cc-people">${free.join("")}</ul>` : `<p class="pk-empty">${escape(t("Nobody has the hours free."))}</p>`) +
		(busy.length
			? `<div class="pk-section-title">${escape(t("Busy ({0})", [busy.length]))}</div><ul class="pk-cc-people">${busy.join("")}</ul>`
			: "") +
		`</div>`
	);
}

// The messages a refused call came back with (frappe's `_server_messages`), as plain strings.
export function server_messages(response) {
	const raw = response && response._server_messages;
	if (!raw) return [];
	let list = [];
	try {
		list = JSON.parse(raw);
	} catch (e) {
		return [];
	}
	return (Array.isArray(list) ? list : [])
		.map((entry) => {
			try {
				const message = typeof entry === "string" ? JSON.parse(entry) : entry;
				return message && typeof message === "object" ? String(message.message || "") : String(message || "");
			} catch (e) {
				return String(entry || "");
			}
		})
		.filter(Boolean);
}

// ---------------------------------------------------------------------- the drawer

export function create_conflicts(env) {
	const warn = (e) => env.warn && env.warn(e);

	function center(opts) {
		opts = opts || {};
		const state = {
			list: null,
			failed: false,
			range_key: "",
			pending: false,
			token: 0,
			timer: null,
			handle: null,
			view: "list",
			pick: null,
			people: undefined,
			pick_token: 0,
			show_kept: false,
			keep_key: "",
			keep_text: "",
			busy: "",
			focus: null,
		};
		const call = (method, args, extra) =>
			Promise.resolve(env.frappe.call(Object.assign({ method, args: args || {} }, extra || {}))).then(
				(r) => (r && r.message) || null
			);
		const range = () => {
			const value = typeof opts.range === "function" ? opts.range() : null;
			return value && value.start && value.end ? value : null;
		};
		const is_open = () => !!(state.handle && state.handle.is_open());
		const toast = (text, extra) => env.toast && env.toast(text, extra || {});

		function fresh() {
			const now = range();
			return !!(now && state.list && !state.failed && !state.pending && state.range_key === `${now.start}|${now.end}`);
		}

		function fetch() {
			state.pending = false;
			env.clear_timeout(state.timer);
			const now = range();
			if (!now) return Promise.resolve(null);
			const token = ++state.token;
			if (is_open() && state.list) state.handle.body.classList.add("pk-busy");
			// Silent: the count is read after every load, and a failure must not open a dialog each time.
			return call(METHODS.get, { start: now.start, end: now.end, planner: opts.planner }, { silent: true })
				.then((list) => {
					if (token !== state.token) return null;
					state.list = Array.isArray(list) ? list : [];
					state.failed = false;
					state.range_key = `${now.start}|${now.end}`;
					after();
					return state.list;
				})
				.catch(() => {
					if (token !== state.token) return null;
					state.failed = true;
					after();
					return null;
				});
		}

		function after() {
			if (typeof opts.on_list === "function") {
				try {
					opts.on_list(state.failed ? null : state.list);
				} catch (e) {
					warn(e);
				}
			}
			render();
		}

		function schedule(delay) {
			env.clear_timeout(state.timer);
			state.pending = true;
			state.timer = env.set_timeout(fetch, delay != null ? delay : is_open() ? OPEN_MS : COUNT_MS);
		}

		function find_conflict(key) {
			return (state.list || []).find((conflict) => conflict && conflict.key === key) || null;
		}

		// The same fix in the newest list (fresh `modified`), or null when it has gone.
		function current_fix(key, fix) {
			const conflict = find_conflict(key);
			const found =
				conflict &&
				(conflict.fixes || []).find(
					(entry) => entry && entry.type === fix.type && entry.doctype === fix.doctype && entry.name === fix.name
				);
			return found ? { conflict, fix: found } : null;
		}

		function picker_ctx() {
			const pick = state.pick || {};
			const fix = pick.fix || {};
			const args = (fix.who_is_free && fix.who_is_free.args) || {};
			const span = args.end && args.end !== args.start ? `${day_label(args.start)} – ${day_label(args.end)}` : day_label(args.start);
			let exclude = {};
			let context = "";
			try {
				exclude = (typeof opts.exclude === "function" && opts.exclude(pick.conflict, fix)) || {};
				context = (typeof opts.pick_context === "function" && opts.pick_context(pick.conflict, fix)) || "";
			} catch (e) {
				warn(e);
			}
			return {
				title: t("Who is free on {0} for {1}h", [span, hours(args.hours || fix.hours)]),
				context,
				field: (fix.pick && fix.pick.field) || "resource",
				exclude,
				loading: state.people === undefined,
				note: pick.note || "",
			};
		}

		function render() {
			const handle = state.handle;
			if (!is_open()) return;
			const body = handle.body;
			body.classList.remove("pk-busy");
			const now = range();
			handle.set_subtitle(now ? now.label || "" : "");
			const typed = body.querySelector("[data-pk-cc-reason]");
			if (typed) state.keep_text = typed.value;
			if (state.view === "pick" && state.pick) {
				handle.set_body(picker_html(state.people, picker_ctx()));
				return;
			}
			const scroll = body.scrollTop;
			const focus = state.focus;
			handle.set_body(
				conflicts_html(state.failed ? null : state.list, {
					loading: !state.list && !state.failed,
					show_kept: state.show_kept,
					keep_key: state.keep_key,
					busy_key: state.busy,
					draft: typeof opts.draft === "function" && !!opts.draft(),
					can_schedule: typeof opts.can_schedule === "function" && !!opts.can_schedule(),
					block_note: opts.block_note,
					focus_date: focus && focus.date && state.list ? focus.date : "",
					range_label: now ? now.label : "",
				})
			);
			const reason = body.querySelector("[data-pk-cc-reason]");
			if (reason) reason.value = state.keep_text || "";
			if (focus && state.list) {
				state.focus = null;
				let el = null;
				if (focus.key) {
					el = Array.from(body.querySelectorAll("[data-pk-cc-key]")).find((node) => node.getAttribute("data-pk-cc-key") === focus.key);
				}
				if (!el && focus.ref) {
					const ref = glow_key(focus.ref);
					el = Array.from(body.querySelectorAll("[data-pk-cc-refs]")).find((node) =>
						String(node.getAttribute("data-pk-cc-refs") || "").split(" ").includes(ref)
					);
				}
				if (!el && focus.date) {
					el = Array.from(body.querySelectorAll("[data-pk-cc-day]")).find((node) => node.getAttribute("data-pk-cc-day") === focus.date);
				}
				if (el) {
					body.scrollTop += el.getBoundingClientRect().top - body.getBoundingClientRect().top - 8;
					if (el.classList.contains("pk-cc-item")) el.classList.add("pk-cc-focus");
					return;
				}
			}
			body.scrollTop = scroll;
			if (reason && state.keep_key) {
				try {
					reason.focus({ preventScroll: true });
				} catch (e) {
					// Courtesy.
				}
			}
		}

		function run(key, fix, picked) {
			if (state.busy) return;
			const found = current_fix(key, fix);
			if (!found) {
				state.view = "list";
				state.pick = null;
				toast(t("That conflict changed; here is the current list."), { tone: "warning" });
				render();
				return;
			}
			const args = fix_args(found.fix, picked);
			if (!args) return;
			state.busy = key;
			state.view = "list";
			state.pick = null;
			state.people = undefined;
			render();
			// The conflict's buttons stay off until the list after the change has arrived, so a second tap
			// never sends the same fix again with what is by then an old `modified`.
			Promise.resolve()
				.then(() => opts.run_fix({ conflict: found.conflict, fix: found.fix, args, picked: picked || null }))
				.catch(warn)
				.then(() => fetch())
				.catch(warn)
				.then(() => {
					state.busy = "";
					render();
				});
		}

		function start_pick(conflict, fix) {
			const spec = fix.who_is_free || {};
			state.view = "pick";
			state.pick = { key: conflict.key, conflict, fix, note: "" };
			state.people = undefined;
			render();
			const token = ++state.pick_token;
			if (spec.method !== METHODS.who_is_free) {
				state.people = null;
				render();
				return;
			}
			call(METHODS.who_is_free, Object.assign({}, spec.args || {}))
				.then((answer) => {
					if (token !== state.pick_token || state.view !== "pick") return;
					state.people = free_people(answer);
					state.pick.note = (answer && answer.note) || "";
					render();
				})
				.catch(() => {
					if (token !== state.pick_token || state.view !== "pick") return;
					state.people = null;
					render();
				});
		}

		function pick(index) {
			const chosen = state.pick;
			const person = Array.isArray(state.people) ? state.people[index] : null;
			if (!chosen || !person) return;
			const field = (chosen.fix.pick && chosen.fix.pick.field) || "resource";
			const value = person[field];
			const exclude = (typeof opts.exclude === "function" && opts.exclude(chosen.conflict, chosen.fix)) || {};
			if (!value || exclude[value]) return;
			run(chosen.key, chosen.fix, person);
		}

		function keep(index) {
			const conflict = (state.list || [])[index];
			const fix = conflict && (conflict.fixes || []).find((entry) => entry && entry.type === "keep");
			const body = state.handle && state.handle.body;
			const field = body && body.querySelector("[data-pk-cc-reason]");
			const reason = String((field && field.value) || "").trim();
			if (!reason) {
				// A reason is required: nothing goes to the server without one.
				const error = body && body.querySelector("[data-pk-cc-keep-error]");
				if (error) error.hidden = false;
				if (field) field.focus();
				return;
			}
			if (!fix || fix.method !== METHODS.acknowledge || state.busy) return;
			const args = Object.assign({}, fix.args, { reason, items: JSON.stringify((fix.args && fix.args.items) || []) });
			state.keep_text = reason;
			state.busy = conflict.key;
			render();
			let messages = [];
			call(METHODS.acknowledge, args, {
				silent: true,
				error: (r) => {
					messages = server_messages(r);
				},
			}).then(
				() => {
					state.busy = "";
					state.keep_key = "";
					state.keep_text = "";
					toast(t("Kept: {0}", [conflict.message || ""]), { tone: "success" });
					return fetch();
				},
				() =>
					fetch().then(() => {
						state.busy = "";
						const now = find_conflict(conflict.key);
						if (!now || now.fingerprint !== conflict.fingerprint) {
							state.keep_key = "";
							state.keep_text = "";
							toast(t("That conflict changed; here is the current list."), { tone: "warning" });
						} else if (messages.length && env.frappe && env.frappe.msgprint) {
							env.frappe.msgprint(messages.join("<br>"));
						}
						render();
					})
			);
		}

		function click(e) {
			const target = e.target;
			if (!target || !target.closest || state.busy) return;
			if (target.closest("[data-pk-cc-toggle-kept]")) {
				state.show_kept = !state.show_kept;
				render();
				return;
			}
			if (target.closest("[data-pk-cc-back]")) {
				state.view = "list";
				state.pick = null;
				state.people = undefined;
				render();
				return;
			}
			const person = target.closest("[data-pk-cc-pick]");
			if (person) {
				pick(Number(person.getAttribute("data-pk-cc-pick")));
				return;
			}
			const cancel = target.closest("[data-pk-cc-keep-cancel]");
			if (cancel) {
				state.keep_key = "";
				state.keep_text = "";
				render();
				return;
			}
			const save = target.closest("[data-pk-cc-keep-save]");
			if (save) {
				keep(Number(save.getAttribute("data-pk-cc-keep-save")));
				return;
			}
			const fix_el = target.closest("[data-pk-cc-fix]");
			if (fix_el) {
				const [ci, fi] = String(fix_el.getAttribute("data-pk-cc-fix")).split(".").map(Number);
				const conflict = (state.list || [])[ci];
				const fix = conflict && (conflict.fixes || [])[fi];
				if (!fix) return;
				if (fix.type === "keep") {
					state.keep_key = conflict.key;
					state.keep_text = "";
					render();
				} else if (fix.type === "pick_free") {
					start_pick(conflict, fix);
				} else {
					run(conflict.key, fix, null);
				}
				return;
			}
			const item_el = target.closest("[data-pk-cc-item]");
			if (item_el) {
				const [ci, ii] = String(item_el.getAttribute("data-pk-cc-item")).split(".").map(Number);
				const conflict = (state.list || [])[ci];
				const item = conflict && (conflict.items || [])[ii];
				if (item && typeof opts.open_item === "function") opts.open_item(conflict, item);
			}
		}

		function open(focus) {
			state.focus = focus || null;
			state.view = "list";
			state.pick = null;
			state.people = undefined;
			if (is_open()) {
				render();
				if (!fresh()) fetch();
				return;
			}
			let handle = null;
			const now = range();
			handle = env.drawer.open({
				title: t("Conflicts"),
				subtitle: now ? now.label || "" : "",
				body: `<p class="pk-empty">${escape(t("Loading…"))}</p>`,
				width: opts.width || 540,
				key: "conflicts",
				owner: opts.owner || null,
				push: opts.push || null,
				reopen: () => open(null),
				on_click: (e) => click(e),
				on_close: () => {
					if (state.handle === handle) state.handle = null;
				},
			});
			state.handle = handle;
			if (fresh()) render();
			else fetch();
		}

		return {
			open,
			schedule,
			refresh: fetch,
			is_open,
			list: () => state.list,
			count: () => (state.list && !state.failed ? open_count(state.list) : null),
			index: () => index_by_ref(state.list),
			find: (doctype, name, key) => {
				const hits = index_by_ref(state.list);
				return (key && hits[`key|${key}`]) || hits[`${doctype}|${name}`] || null;
			},
		};
	}

	return { center };
}

export const CONFLICTS_CSS = `
.pk-cc-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 6px;font-size:12px;color:var(--text-muted);}
.pk-cc-count{font-weight:600;}
.pk-cc-day{margin:0 0 10px;}
.pk-cc-day-head{margin:8px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;}
.pk-cc-item{border:1px solid var(--border-color);border-left:3px solid #dc2626;border-radius:8px;padding:6px 8px;margin-bottom:6px;background:var(--card-bg);font-size:12px;}
.pk-cc-item.pk-cc-k-qualification{border-left-color:#d97706;}
.pk-cc-item.pk-cc-k-equipment{border-left-color:#7c3aed;}
.pk-cc-item.pk-cc-kept{border-left-color:#94a3b8;opacity:.8;}
.pk-cc-item.pk-cc-focus{box-shadow:0 0 0 2px var(--primary,#2490ef);}
.pk-cc-head{display:flex;align-items:center;gap:6px;min-width:0;}
.pk-cc-icon{display:inline-flex;color:#b91c1c;flex:0 0 auto;}
.pk-cc-k-qualification .pk-cc-icon{color:#b45309;}
.pk-cc-k-equipment .pk-cc-icon{color:#6d28d9;}
.pk-cc-kept .pk-cc-icon{color:var(--text-muted);}
.pk-cc-who{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;}
.pk-cc-kind{margin-left:auto;font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--text-muted);white-space:nowrap;}
.pk-cc-msg{margin:3px 0 4px;}
.pk-cc-note{margin:0 0 4px;color:var(--text-muted);font-style:italic;}
.pk-cc-records{list-style:none;margin:0;padding:0;}
.pk-cc-record{border-top:1px dashed var(--border-color);padding:4px 0;}
.pk-cc-record-main{display:flex;flex-wrap:wrap;align-items:center;gap:6px;min-width:0;}
.pk-cc-title{font-weight:600;min-width:0;}
.pk-cc-meta{font-size:11px;color:var(--text-muted);}
.pk-cc-fixes{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;}
.pk-cc-ack{margin-top:4px;font-size:11px;color:var(--text-muted);}
.pk-cc-keep{margin-top:6px;}
.pk-cc-keep label{font-size:11px;font-weight:normal;color:var(--text-muted);margin:0 0 2px;}
.pk-cc-keep-error{font-size:11px;color:#b91c1c;margin-top:2px;}
.pk-cc-keep-error[hidden]{display:none;}
.pk-cc-hint{font-size:11px;color:var(--text-muted);margin-top:2px;}
.pk-cc-pick-head{margin:0 0 6px;}
.pk-cc-pick-title{margin:0 0 2px;font-size:14px;font-weight:600;}
.pk-cc-pick-context{margin:0;font-size:12px;color:var(--text-muted);}
.pk-cc-people{list-style:none;margin:0;padding:0;}
.pk-cc-person{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;padding:8px 10px;margin:0 0 4px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);color:var(--text-color);font-size:13px;text-align:left;cursor:pointer;}
.pk-cc-person:hover,.pk-cc-person:focus{border-color:var(--primary,#2490ef);outline:none;}
.pk-cc-person.pk-cc-busy{opacity:.6;}
.pk-cc-person[disabled]{opacity:.45;cursor:default;}
.pk-cc-person-name{font-weight:600;min-width:0;}
.pk-cc-person-facts{font-size:11px;color:var(--text-muted);text-align:right;}
`;
