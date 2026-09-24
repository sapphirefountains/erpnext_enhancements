#!/usr/bin/env node
/**
 * The AI Pending Action list's batch helpers and wiring, executed rather than grepped (v1.528.0).
 *
 * The list view lets someone confirm several AI proposals in one go, and most of its rules are
 * about safety rather than convenience:
 *
 * - a row with a `review_reason` (high risk, hidden values, a submit or cancel, a write to the
 *   gate's own records), or High risk or hidden values whatever the server said, never starts
 *   ticked, and never more than 50 start ticked or are ticked by "Select all";
 * - more than 50 ticked is refused with the review dialog left open;
 * - the second confirmation says how many will run and how many are high-risk, and it is a
 *   Dialog with an HTML field, because frappe.confirm wraps its message in a <p>;
 * - the names go to the server at most 10 per request, one request after another; the results
 *   merge into one table, and a failed request stops the rest and says what may have run;
 * - "Review My Pending (N)" follows realtime list updates, reads "100+" at the server's limit,
 *   and the page-menu entry v16 makes for it follows its count and visibility;
 * - the selected-rows confirmation counts only what my_pending_actions says will run;
 * - every string from the server is escaped. A summary is text the assistant wrote from
 *   arguments it chose, so it is exactly where an injected `<img onerror>` would arrive.
 *
 * Frappe runs a doctype's list script inside `new Function(source)()`. This does the same, with
 * a stub `frappe`, `__`, `$` and `document`, and appends one `return` naming the helpers so they
 * can be called directly. The file itself exports nothing.
 *
 * Run: node scripts/test_ai_pending_batch.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIST_JS = path.join(
	HERE,
	"..",
	"erpnext_enhancements",
	"ai_governance",
	"doctype",
	"ai_pending_action",
	"ai_pending_action_list.js"
);

const tests = [];
function test(name, fn) {
	tests.push([name, fn]);
}

// ------------------------------------------------------------------ a small fake DOM + jQuery

class Node {
	constructor(tag, classes = [], text = "", attrs = {}) {
		this.tag = tag;
		this.classes = new Set(classes);
		this.own_text = text;
		this.attrs = attrs;
		this.children = [];
		this.parent = null;
		this.detached = false;
	}
	append(child) {
		child.parent = this;
		this.children.push(child);
		return child;
	}
	matches(selector) {
		const [tag, ...classes] = selector.split(".");
		return (!tag || tag === this.tag) && classes.every((c) => this.classes.has(c));
	}
	descendants() {
		return this.children.flatMap((child) => [child, ...child.descendants()]);
	}
	text() {
		return this.own_text + this.children.map((child) => child.text()).join("");
	}
	attached() {
		let node = this;
		while (node) {
			if (node.detached) return false;
			node = node.parent;
		}
		return true;
	}
}

class Q {
	constructor(nodes) {
		this.nodes = nodes.filter(Boolean);
		this.length = this.nodes.length;
		this.nodes.forEach((node, i) => {
			this[i] = node;
		});
	}
	find(selector) {
		return new Q(this.nodes.flatMap((n) => n.descendants()).filter((n) => n.matches(selector)));
	}
	filter(fn) {
		return new Q(this.nodes.filter((n, i) => fn.call(n, i, n)));
	}
	last() {
		return new Q(this.nodes.slice(-1));
	}
	parent() {
		return new Q([...new Set(this.nodes.map((n) => n.parent))]);
	}
	text(value) {
		if (value === undefined) return this.nodes.map((n) => n.text()).join("");
		this.nodes.forEach((n) => {
			n.own_text = String(value);
			n.children = [];
		});
		return this;
	}
	addClass(c) {
		this.nodes.forEach((n) => n.classes.add(c));
		return this;
	}
	removeClass(c) {
		this.nodes.forEach((n) => n.classes.delete(c));
		return this;
	}
	hasClass(c) {
		return this.nodes.some((n) => n.classes.has(c));
	}
	remove() {
		this.nodes.forEach((n) => {
			if (n.parent) n.parent.children = n.parent.children.filter((c) => c !== n);
			n.detached = true;
		});
		return this;
	}
	attr(key) {
		return this.nodes[0] && this.nodes[0].attrs[key];
	}
	prop(key, value) {
		this.nodes.forEach((n) => {
			n[key] = value;
		});
		return this;
	}
	each(fn) {
		this.nodes.forEach((n, i) => fn.call(n, i, n));
		return this;
	}
}

const $ = (x) => (x instanceof Q ? x : new Q(Array.isArray(x) ? x : [x]));
const document = { body: { contains: (node) => Boolean(node) && node.attached() } };

// A dialog's HTML field: keeps the HTML, the delegated handlers and a few texts it was given.
class FakeWrapper {
	constructor() {
		this.handlers = {};
		this.texts = {};
		this.content = "";
	}
	html(value) {
		this.content = value;
		return this;
	}
	on(event, selector, handler) {
		this.handlers[`${event} ${selector}`] = handler;
		return this;
	}
	find(selector) {
		const wrapper = this;
		return {
			text(value) {
				wrapper.texts[selector] = value;
				return this;
			},
			each() {
				return this;
			},
		};
	}
}

// ------------------------------------------------------------------ the stub frappe

// frappe v16's escape_html (frappe/public/js/frappe/utils/utils.js), so the assertions below
// hold against the mapping production uses.
const ESCAPES = {
	"&": "&amp;",
	"<": "&lt;",
	">": "&gt;",
	'"': "&quot;",
	"'": "&#39;",
	"`": "&#x60;",
	"=": "&#x3D;",
};

const state = { calls: [], dialogs: [], messages: [], alerts: [], progress: [], hidden_progress: 0, debouncers: [] };

class FakeDialog {
	constructor(opts) {
		this.opts = opts;
		this.shown = false;
		this.hidden = false;
		this.fields_dict = {};
		(opts.fields || []).forEach((field) => {
			this.fields_dict[field.fieldname] = { $wrapper: new FakeWrapper() };
		});
		this.primary_text = opts.primary_action_label;
		this.secondary_text = opts.secondary_action_label;
		state.dialogs.push(this);
	}
	get_primary_btn() {
		return { text: (value) => (this.primary_text = value) };
	}
	get_secondary_btn() {
		return { text: (value) => (this.secondary_text = value) };
	}
	show() {
		this.shown = true;
	}
	hide() {
		this.hidden = true;
	}
}

const frappe = {
	listview_settings: {},
	session: { user: "nik@x" },
	utils: {
		escape_html: (txt) => (txt == null ? "" : String(txt).replace(/[&<>"'`=]/g, (ch) => ESCAPES[ch] || ch)),
		get_form_link: (doctype, name) =>
			`/desk/${encodeURIComponent(doctype.toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(name)}`,
		// A debounce the test fires by hand.
		debounce: (fn, wait) => {
			const debounced = () => {
				debounced.pending = true;
			};
			debounced.pending = false;
			debounced.fire = () => {
				if (!debounced.pending) return;
				debounced.pending = false;
				fn();
			};
			state.debouncers.push({ debounced, wait });
			return debounced;
		},
	},
	ui: { Dialog: FakeDialog },
	call: (opts) => {
		state.calls.push(opts);
	},
	msgprint: (message) => state.messages.push(message),
	show_alert: (alert) => state.alerts.push(alert),
	show_progress: (title, count, total, description) => state.progress.push([count, total, description]),
	hide_progress: () => {
		state.hidden_progress += 1;
	},
	confirm: () => {
		throw new Error("frappe.confirm must not be used: it wraps block HTML in a <p>");
	},
};
const __ = (text, replace) =>
	String(text).replace(/\{(\d+)\}/g, (match, i) => (replace && i in replace ? String(replace[i]) : match));

function reset() {
	state.calls = [];
	state.dialogs = [];
	state.messages = [];
	state.alerts = [];
	state.progress = [];
	state.hidden_progress = 0;
	state.debouncers = [];
}

// The server's answers, in v16's order: on success the callback, then `always`; on a failed
// request `always` first and then `error` (request.js registers .always before .fail).
function answer_ok(opts, message) {
	opts.callback && opts.callback({ message });
	opts.always && opts.always({ message });
}
function answer_failed(opts) {
	opts.always && opts.always(null);
	opts.error && opts.error();
}
function answer_neither(opts) {
	opts.always && opts.always(null);
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 5));

function batch_answer(opts) {
	const names = JSON.parse(JSON.stringify(opts.args.names));
	return {
		results: names.map((name) => ({ name, tool_name: "create_document", summary: `S ${name}`, status: "Executed", message: "" })),
	};
}

// ------------------------------------------------------------------ load the real script

const HELPERS = [
	"starts_ticked",
	"initial_selection",
	"select_all",
	"count_label",
	"batch_limit_problem",
	"selection_count_text",
	"review_notes",
	"untick_reason",
	"short_duration",
	"selection_summary",
	"execute_question",
	"cancel_question",
	"runnable_rows",
	"chunk_names",
	"run_in_chunks",
	"oldest_first",
	"batch_payload",
	"tally",
	"results_headline",
	"render_review_table",
	"render_results",
	"render_selected_confirmation",
	"send_chunk",
	"update_review_button",
	"refresh_review_button",
	"watch_realtime_updates",
	"ask",
	"run_batch",
	"show_review_dialog",
	"decide_selected",
	"CHUNK_SIZE",
	"MAX_BATCH",
];

const source = fs.readFileSync(LIST_JS, "utf8");
const load = new Function("frappe", "__", "$", "document", `${source}\n;return { ${HELPERS.join(", ")} };`);
const h = load(frappe, __, $, document);

const EVIL = `<img src=x onerror="alert('pwned')">`;

const ROWS = [
	{ name: "AI-PA-1", tool_name: "create_document", summary: "Create ToDo", risk: "Low", batch_default: true, review_reason: "", has_hidden: false, age_seconds: 300, expires_in_seconds: 3300 },
	{ name: "AI-PA-2", tool_name: "delete_document", summary: "Delete Customer ACME", risk: "High", batch_default: false, review_reason: "high risk", has_hidden: false, age_seconds: 120, expires_in_seconds: 60 },
	{ name: "AI-PA-3", tool_name: "author_training_course", summary: "Author a training course", risk: "Medium", batch_default: false, review_reason: "has hidden values", has_hidden: true, age_seconds: 30, expires_in_seconds: null },
	{ name: "AI-PA-4", tool_name: "update_document", summary: "Update Task T-1", risk: "Medium", batch_default: true, review_reason: "", has_hidden: false, age_seconds: 7200, expires_in_seconds: 90000 },
	{ name: "AI-PA-5", tool_name: "update_document", summary: "Update Sales Invoice ACC-SINV-1", risk: "Medium", batch_default: false, review_reason: "submits or cancels a document", has_hidden: false, age_seconds: 60, expires_in_seconds: 600 },
];

function many(count, extra = {}) {
	return Array.from({ length: count }, (_, i) =>
		Object.assign(
			{ name: `AI-PA-${i + 1}`, tool_name: "create_document", summary: `Create ToDo ${i + 1}`, risk: "Low", batch_default: true, has_hidden: false },
			extra
		)
	);
}

function fake_listview() {
	const menu_root = new Node("ul", ["dropdown-menu"]);
	const toolbar = new Node("div", ["custom-actions"]);
	const page = {
		menu: new Q([menu_root]),
		toolbar,
		inner_calls: 0,
		actions_menu: [],
		// Like v16 page.js: a same-labelled page-menu entry first, then the button.
		add_inner_button(label, action, group, type) {
			this.inner_calls += 1;
			const li = menu_root.append(new Node("li", ["user-action", "hidden-xl"]));
			const a = li.append(new Node("a", ["grey-link", "dropdown-item"]));
			a.append(new Node("span", ["menu-item-label"], label));
			return new Q([toolbar.append(new Node("button", ["btn", `btn-${type}`], label))]);
		},
		add_actions_menu_item(label) {
			this.actions_menu.push(label);
		},
	};
	const menu_entries = () => new Q([menu_root]).find("a.grey-link");
	return {
		page,
		menu_entries,
		refreshed: 0,
		refresh() {
			this.refreshed += 1;
		},
		clear_checked_items() {},
	};
}

// ------------------------------------------------------------------ tests

console.log("AI Pending Action batch helpers");

test("the list script registers onload and refresh, and loads the fields its confirmation reads", () => {
	const settings = frappe.listview_settings["AI Pending Action"];
	assert.equal(typeof settings.onload, "function");
	assert.equal(typeof settings.refresh, "function");
	assert.ok(settings.add_fields.includes("requested_by"));
	assert.ok(settings.add_fields.includes("status"));
});

test("only what the server marked batch_default starts ticked", () => {
	assert.deepEqual([...h.initial_selection(ROWS)], ["AI-PA-1", "AI-PA-4"]);
});

test("High risk or hidden values never start ticked, whatever batch_default says", () => {
	const lying = ROWS.map((row) => Object.assign({}, row, { batch_default: true }));
	assert.deepEqual([...h.initial_selection(lying)], ["AI-PA-1", "AI-PA-4", "AI-PA-5"]);
	assert.equal(h.starts_ticked({ risk: "High", batch_default: true }), false);
	assert.equal(h.starts_ticked({ risk: "Low", batch_default: true, has_hidden: true }), false);
	assert.equal(h.starts_ticked(null), false);
});

test("an unticked row says why, in the server's words when it gave them", () => {
	assert.equal(h.untick_reason(ROWS[4]), "Review individually recommended: submits or cancels a document.");
	assert.equal(h.untick_reason(ROWS[1]), "Review individually recommended: high risk.");
	// An older server that sent no review_reason still gets a reason for High and hidden.
	assert.match(h.untick_reason({ risk: "High", has_hidden: true }), /high risk, hidden values/);
	assert.equal(h.untick_reason(ROWS[0]), "");
});

test("no more than 50 start ticked, and Select all ticks the oldest 50", () => {
	assert.equal(h.MAX_BATCH, 50);
	const rows = many(60);
	const ticked = [...h.initial_selection(rows)];
	assert.equal(ticked.length, 50);
	assert.deepEqual(ticked, rows.slice(0, 50).map((row) => row.name));
	const all = [...h.select_all(many(60, { risk: "High", batch_default: false }))];
	assert.equal(all.length, 50);
	assert.equal(all[49], "AI-PA-50");
	assert.equal(h.select_all(ROWS).size, ROWS.length);
});

test("more than 50 is a problem, and the count line says so", () => {
	assert.equal(h.batch_limit_problem(50), "");
	assert.match(h.batch_limit_problem(51), /^At most 50 actions can be decided at once, and 51 are ticked\./);
	assert.equal(h.selection_count_text(3, 5), "3 of 5 selected");
	assert.equal(h.selection_count_text(51, 60), "51 of 60 selected (at most 50 at once)");
	assert.equal(h.selection_count_text(50, 100), "50 of 100+ selected");
});

test("the server's limit reads as 100+", () => {
	assert.equal(h.count_label(99), "99");
	assert.equal(h.count_label(100), "100+");
	assert.deepEqual(h.review_notes(many(10)), []);
	assert.equal(h.review_notes(many(60)).length, 1);
	assert.match(h.review_notes(many(60))[0], /Select all ticks the oldest 50/);
	assert.equal(h.review_notes(many(100)).length, 2);
	assert.match(h.review_notes(many(100))[0], /oldest 100 pending actions, and there are more/);
	assert.ok(h.render_review_table(many(60), new Set()).includes("Select all ticks the oldest 50"));
});

test("the second confirmation counts what runs and names the high-risk ones", () => {
	assert.equal(h.execute_question({ count: 3, high: 0, hidden: 0 }), "Run 3 actions as you? This executes them now.");
	assert.equal(
		h.execute_question({ count: 3, high: 1, hidden: 0 }),
		"Run 3 actions as you, including 1 high-risk? This executes them now."
	);
	assert.match(h.execute_question({ count: 1, high: 0, hidden: 0 }), /^Run 1 action as you\?/);
	assert.match(h.execute_question({ count: 2, high: 0, hidden: 1 }), /1 of them use hidden values/);
	assert.match(h.cancel_question({ count: 2 }), /^Cancel 2 actions\? Nothing runs\./);
});

test("the summary of a selection counts ticked rows only", () => {
	const selected = new Set(["AI-PA-2", "AI-PA-3"]);
	assert.deepEqual(h.selection_summary(ROWS, selected), { count: 2, high: 1, hidden: 1 });
	assert.deepEqual(h.selection_summary(ROWS, new Set()), { count: 0, high: 0, hidden: 0 });
});

test("durations are short and never negative", () => {
	assert.equal(h.short_duration(30), "30s");
	assert.equal(h.short_duration(125), "2m");
	assert.equal(h.short_duration(7200), "2h");
	assert.equal(h.short_duration(3 * 86400), "3d");
	assert.equal(h.short_duration(-5), "0s");
	assert.equal(h.short_duration(null), "");
	assert.equal(h.short_duration("not a number"), "");
});

test("results are tallied by status and headlined for the kind of batch", () => {
	const counts = h.tally([{ status: "Executed" }, { status: "Skipped" }, { status: "Skipped" }, { status: "Failed" }]);
	assert.deepEqual(counts, { Executed: 1, Cancelled: 0, Failed: 1, Skipped: 2, Unknown: 0, "Not sent": 0 });
	assert.equal(h.results_headline("confirm", counts), "Executed 1, failed 1, skipped 2.");
	assert.equal(h.results_headline("cancel", h.tally([{ status: "Cancelled" }])), "Cancelled 1, failed 0, skipped 0.");
	assert.equal(
		h.results_headline("confirm", h.tally([{ status: "Unknown" }, { status: "Not sent" }, { status: "Not sent" }])),
		"Executed 0, failed 0, skipped 0, unknown 1, not sent 2."
	);
});

test("the review table ticks the default rows and notes the others", () => {
	const html = h.render_review_table(ROWS, h.initial_selection(ROWS));
	const checked = [...html.matchAll(/data-name="([^"]+)" checked/g)].map((m) => m[1]);
	assert.deepEqual(checked, ["AI-PA-1", "AI-PA-4"]);
	assert.equal((html.match(/Review individually recommended/g) || []).length, 3);
	assert.ok(html.includes("Review individually recommended: submits or cancels a document."));
	assert.ok(html.includes('target="_blank"'), "each row links to its form in a new tab");
	assert.ok(html.includes("/desk/ai-pending-action/AI-PA-3"));
	assert.ok(html.includes('data-ee-select="all"') && html.includes('data-ee-select="none"'));
	assert.ok(html.includes(">never<"), "no expiry reads as never");
});

test("the review table escapes everything the server sent", () => {
	const evil = [{ name: `AI-PA-"><b>`, tool_name: EVIL, summary: EVIL, risk: EVIL, batch_default: false, review_reason: EVIL, has_hidden: false }];
	const html = h.render_review_table(evil, h.initial_selection(evil));
	assert.ok(!html.includes("<img"), "a summary or reason was put into the page as HTML");
	assert.ok(!html.includes('"><b>'), "a name broke out of its attribute");
	assert.ok(html.includes("&lt;img"));
});

test("the results table and the selected-rows confirmation escape too", () => {
	const results = h.render_results("confirm", {
		results: [{ name: "AI-PA-1", tool_name: EVIL, summary: EVIL, status: "Failed", message: EVIL }],
	});
	assert.ok(!results.includes("<img"));
	assert.ok(results.includes("Executed 0, failed 1, skipped 0."));
	const confirmation = h.render_selected_confirmation("confirm", [{ name: "AI-PA-1", summary: EVIL, risk: "High" }], 2);
	assert.ok(!confirmation.includes("<img"));
	assert.ok(confirmation.includes("including 1 high-risk"));
	assert.ok(confirmation.includes("2 of the selected are not yours, not Pending or expired"));
});

test("skipped results carry the note that says why nothing happened to them", () => {
	const html = h.render_results("cancel", { results: [{ name: "AI-PA-9", status: "Skipped", message: "Skipped: it is already Executed." }] });
	assert.ok(html.includes("Skipped actions were left exactly as they were"));
	assert.ok(!h.render_results("cancel", { results: [] }).includes("Skipped actions were left"));
});

test("selected rows count as runnable only when my_pending_actions says so", () => {
	const rows = [
		{ name: "A", requested_by: "nik@x", status: "Pending", summary: "mine" },
		{ name: "B", requested_by: "nik@x", status: "Pending", summary: "mine, but expired" },
		{ name: "C", requested_by: "jordan@x", status: "Pending", summary: "theirs" },
		{ name: "D", requested_by: "nik@x", status: "Executed", summary: "decided" },
	];
	const pending = [{ name: "A", has_hidden: true, risk: "High" }];
	const runnable = h.runnable_rows(rows, pending, "nik@x");
	assert.deepEqual(runnable.map((row) => row.name), ["A"]);
	assert.equal(runnable[0].has_hidden, true, "the server's flags are merged in");
	// my_pending_actions did not answer: the list columns decide.
	assert.deepEqual(h.runnable_rows(rows, null, "nik@x").map((row) => row.name), ["A", "B"]);
	// It answered with its limit, so a row newer than those it returned falls back to the columns.
	const full = many(100).map((row) => ({ name: row.name }));
	const newer = { name: "AI-PA-999", requested_by: "nik@x", status: "Pending" };
	assert.deepEqual(h.runnable_rows([newer, rows[2]], full, "nik@x").map((row) => row.name), ["AI-PA-999"]);
});

test("the list sends one action per request, and chunk_names still splits any size", () => {
	// One per request: each is then the request the form's own Confirm button makes, with the
	// whole server timeout to itself, and the time budget can never skip older actions while a
	// later request runs newer ones.
	assert.equal(h.CHUNK_SIZE, 1);
	const names = many(25).map((row) => row.name);
	assert.deepEqual(h.chunk_names(names, 10).map((chunk) => chunk.length), [10, 10, 5]);
	assert.deepEqual(h.chunk_names([], 10), []);
});

test("chunks go one after another, and the results merge into one table", async () => {
	const names = many(25).map((row) => row.name);
	const sent = [];
	let in_flight = 0;
	const progress = [];
	const outcome = await h.run_in_chunks(
		names,
		10,
		(chunk) => {
			in_flight += 1;
			assert.equal(in_flight, 1, "a second request was sent before the first answered");
			sent.push(chunk);
			return new Promise((resolve) =>
				setTimeout(() => {
					in_flight -= 1;
					resolve({ ok: true, payload: { results: chunk.map((name) => ({ name, status: "Executed" })) } });
				}, 1)
			);
		},
		(done, total) => progress.push(`${done}/${total}`)
	);
	assert.deepEqual(sent.map((chunk) => chunk.length), [10, 10, 5]);
	assert.deepEqual(outcome.results.map((item) => item.name), names);
	assert.deepEqual([outcome.failed, outcome.not_sent], [[], []]);
	assert.deepEqual(progress, ["0/25", "10/25", "20/25", "25/25"]);
});

test("a request that ran out of time stops the batch, so newer actions never run ahead of it", async () => {
	const names = many(5).map((row) => row.name);
	let requests = 0;
	const outcome = await h.run_in_chunks(names, 1, (chunk) => {
		requests += 1;
		const status = requests === 2 ? "Skipped" : "Executed";
		return { ok: true, payload: { results: chunk.map((name) => ({ name, status })), budget_reached: requests === 2 } };
	});
	assert.equal(requests, 2, "a chunk was sent after the server reported its time budget");
	assert.deepEqual(outcome.results.map((item) => item.status), ["Executed", "Skipped"]);
	assert.deepEqual(outcome.failed, []);
	assert.deepEqual(outcome.not_sent, names.slice(2));
});

test("selected rows go oldest first, whatever order the list shows them in", () => {
	const newest_first = [
		{ name: "AI-PA-12", creation: "2026-09-24 10:00:12" },
		{ name: "AI-PA-3", creation: "2026-09-24 10:00:03" },
		{ name: "AI-PA-10", creation: "2026-09-24 10:00:10" },
		{ name: "AI-PA-2b", creation: "2026-09-24 10:00:02" },
		{ name: "AI-PA-2a", creation: "2026-09-24 10:00:02" },
	];
	assert.deepEqual(h.oldest_first(newest_first).map((row) => row.name), ["AI-PA-2a", "AI-PA-2b", "AI-PA-3", "AI-PA-10", "AI-PA-12"]);
	assert.equal(newest_first[0].name, "AI-PA-12", "oldest_first sorted the caller's array in place");
	assert.deepEqual(h.oldest_first(null), []);
	assert.ok(source.includes("oldest_first(rows).map((row) => row.name)"), "decide_selected no longer sends oldest first");
});

test("a failed request stops the batch and says what may have run and what was not sent", async () => {
	const names = many(25).map((row) => row.name);
	let requests = 0;
	const outcome = await h.run_in_chunks(names, 10, (chunk) => {
		requests += 1;
		return requests === 2 ? { ok: false } : { ok: true, payload: { results: chunk.map((name) => ({ name, status: "Executed" })) } };
	});
	assert.equal(requests, 2, "a chunk was sent after a failed request");
	assert.equal(outcome.results.length, 10);
	assert.deepEqual(outcome.failed, names.slice(10, 20));
	assert.deepEqual(outcome.not_sent, names.slice(20));
	const labels = new Map([["AI-PA-11", { tool_name: "create_document", summary: EVIL }]]);
	const payload = h.batch_payload(outcome, labels);
	assert.equal(payload.stopped, true);
	assert.deepEqual(h.tally(payload.results), { Executed: 10, Cancelled: 0, Failed: 0, Skipped: 0, Unknown: 10, "Not sent": 5 });
	const html = h.render_results("confirm", payload);
	assert.ok(html.includes("Executed 10, failed 0, skipped 0, unknown 10, not sent 5."));
	assert.ok(html.includes("A request failed, so the batch stopped there."));
	assert.ok(!html.includes("<img"), "a label was put into the page as HTML");
	// A send that throws is a failed request too.
	return h.run_in_chunks(["X"], 10, () => {
		throw new Error("boom");
	}).then((thrown) => assert.deepEqual(thrown.failed, ["X"]));
});

test("one batch request settles once, in v16's callback order", async () => {
	reset();
	const ok = h.send_chunk("confirm_actions", ["A"]);
	answer_ok(state.calls[0], { results: [{ name: "A", status: "Executed" }] });
	assert.deepEqual(await ok, { ok: true, payload: { results: [{ name: "A", status: "Executed" }] } });
	assert.equal(state.calls[0].type, "POST");
	assert.equal(state.calls[0].method, "erpnext_enhancements.assistant_tools.gating_api.confirm_actions");
	const failed = h.send_chunk("confirm_actions", ["B"]);
	answer_failed(state.calls[1]);
	assert.deepEqual(await failed, { ok: false });
	const neither = h.send_chunk("cancel_actions", ["C"]);
	answer_neither(state.calls[2]);
	assert.deepEqual(await neither, { ok: false });
});

test("run_batch sends one action at a time, in order, shows k of n, and ends in one results dialog", async () => {
	reset();
	const listview = fake_listview();
	const names = many(25).map((row) => row.name);
	const done = h.run_batch(listview, "confirm", names, new Map());
	for (const expected of names) {
		await flush();
		const pending = state.calls.filter((c) => c.method.endsWith(".confirm_actions") && !c.answered);
		assert.equal(pending.length, 1, "exactly one request in flight");
		assert.deepEqual(pending[0].args.names, [expected]);
		pending[0].answered = true;
		answer_ok(pending[0], batch_answer(pending[0]));
	}
	await done;
	const progress = state.progress.map(([count, total, text]) => [count, total, text]);
	assert.equal(progress.length, 26);
	assert.deepEqual(progress[0], [0, 25, "0 of 25 done"]);
	assert.deepEqual(progress[1], [1, 25, "1 of 25 done"]);
	assert.deepEqual(progress.at(-1), [25, 25, "25 of 25 done"]);
	assert.equal(state.hidden_progress, 1);
	const results = state.dialogs.at(-1);
	assert.ok(results.fields_dict.results.$wrapper.content.includes("Executed 25, failed 0, skipped 0."));
	assert.equal(listview.refreshed, 1);
});

test("run_batch stops sending after a failed request", async () => {
	reset();
	const listview = fake_listview();
	const names = many(25).map((row) => row.name);
	const done = h.run_batch(listview, "cancel", names, new Map());
	await flush();
	answer_ok(state.calls[0], batch_answer(state.calls[0]));
	await flush();
	answer_failed(state.calls[1]);
	await done;
	assert.equal(state.calls.filter((c) => c.method.endsWith(".cancel_actions")).length, 2);
	const html = state.dialogs.at(-1).fields_dict.results.$wrapper.content;
	assert.ok(html.includes("unknown 1, not sent 23."));
});

test("the confirmation is a Dialog with an HTML field, never frappe.confirm", () => {
	reset();
	assert.ok(!/frappe\.confirm\s*\(/.test(source), "frappe.confirm wraps block HTML in a <p>");
	let answered = 0;
	const html = "<p>Run 2 actions as you?</p><ul><li>x</li></ul>";
	const dialog = h.ask("Confirm", html, () => (answered += 1));
	assert.deepEqual(dialog.opts.fields, [{ fieldtype: "HTML", fieldname: "message" }]);
	assert.equal(dialog.fields_dict.message.$wrapper.content, html);
	assert.equal(dialog.shown, true);
	dialog.opts.primary_action();
	assert.equal(dialog.hidden, true);
	assert.equal(answered, 1);
});

test("the review dialog refuses more than 50 and stays open", async () => {
	reset();
	const listview = fake_listview();
	const rows = many(60);
	const dialog = h.show_review_dialog(listview, rows);
	const wrapper = dialog.fields_dict.review.$wrapper;
	assert.equal(dialog.primary_text, "Confirm & Execute (50)");
	assert.equal(wrapper.texts[".ee-apa-count"], "50 of 60 selected");
	// Tick a 51st by hand.
	const box = new Node("input", [], "", { "data-name": "AI-PA-51" });
	box.checked = true;
	wrapper.handlers["change .ee-apa-check"].call(box);
	assert.equal(dialog.primary_text, "Confirm & Execute (51)");
	assert.equal(wrapper.texts[".ee-apa-count"], "51 of 60 selected (at most 50 at once)");
	dialog.opts.primary_action();
	assert.match(state.messages.at(-1), /At most 50 actions can be decided at once, and 51 are ticked/);
	assert.equal(dialog.hidden, false, "the review dialog closed and lost the selection");
	assert.equal(state.dialogs.length, 1, "a confirmation opened anyway");
	assert.equal(state.calls.length, 0);
	// Select all brings it back to the oldest 50, and then it asks, and then it sends the oldest.
	wrapper.handlers["click [data-ee-select]"].call(new Node("button", [], "", { "data-ee-select": "all" }));
	assert.equal(dialog.primary_text, "Confirm & Execute (50)");
	dialog.opts.primary_action();
	const confirmation = state.dialogs.at(-1);
	assert.notEqual(confirmation, dialog);
	assert.match(confirmation.fields_dict.message.$wrapper.content, /Run 50 actions as you\?/);
	confirmation.opts.primary_action();
	assert.equal(dialog.hidden, true);
	await flush();
	assert.equal(state.calls.length, 1);
	assert.deepEqual(state.calls[0].args.names, [rows[0].name]);
});

test("Review My Pending and its page-menu entry keep the same count and visibility", () => {
	const listview = fake_listview();
	const entry_li = () => listview.menu_entries().parent();
	h.update_review_button(listview, 3);
	assert.equal(listview.page.inner_calls, 1);
	assert.equal(listview.ee_apa_review_button.text(), "Review My Pending (3)");
	assert.equal(listview.menu_entries().length, 1);
	assert.equal(listview.menu_entries().text(), "Review My Pending (3)");
	h.update_review_button(listview, 0);
	assert.ok(listview.ee_apa_review_button.hasClass("hide"));
	assert.ok(entry_li().hasClass("hide"), "the phone menu still offers an empty review");
	h.update_review_button(listview, 100);
	assert.ok(!listview.ee_apa_review_button.hasClass("hide"));
	assert.ok(!entry_li().hasClass("hide"));
	assert.equal(listview.ee_apa_review_button.text(), "Review My Pending (100+)");
	assert.equal(listview.menu_entries().text(), "Review My Pending (100+)");
	assert.equal(listview.page.inner_calls, 1, "the button was added twice");
	// The toolbar was rebuilt: a new button, and the old menu entry goes with the old button.
	listview.ee_apa_review_button.remove();
	h.update_review_button(listview, 2);
	assert.equal(listview.page.inner_calls, 2);
	assert.equal(listview.menu_entries().length, 1, "a stale page-menu entry was left behind");
	assert.equal(listview.menu_entries().text(), "Review My Pending (2)");
});

test("a count asked for mid-flight runs once the first lands", () => {
	reset();
	const listview = fake_listview();
	h.refresh_review_button(listview);
	h.refresh_review_button(listview);
	assert.equal(state.calls.length, 1);
	answer_ok(state.calls[0], many(4));
	assert.equal(state.calls.length, 2, "the second request for a count was dropped");
	answer_ok(state.calls[1], many(5));
	assert.equal(state.calls.length, 2);
	assert.equal(listview.ee_apa_review_button.text(), "Review My Pending (5)");
});

test("realtime list updates recount the button, through the list's own debounced_refresh", () => {
	reset();
	const seen = [];
	const listview = {
		debounced_refresh(...args) {
			seen.push([this, args]);
			return "refreshed";
		},
	};
	const updates = [];
	assert.equal(h.watch_realtime_updates(listview, (lv) => updates.push(lv), 2000), true);
	assert.equal(listview.debounced_refresh(), "refreshed");
	listview.debounced_refresh("x");
	listview.debounced_refresh();
	assert.equal(seen.length, 3, "the list's own realtime refresh must still run");
	assert.equal(seen[1][0], listview);
	assert.deepEqual(seen[1][1], ["x"]);
	assert.equal(updates.length, 0, "the recount is debounced");
	const { debounced, wait } = state.debouncers.at(-1);
	assert.equal(wait, 2000);
	debounced.fire();
	assert.deepEqual(updates, [listview]);
	// Wrapping twice does not stack, and a list without the hook is left alone.
	const wrapped = listview.debounced_refresh;
	assert.equal(h.watch_realtime_updates(listview, () => {}, 2000), true);
	assert.equal(listview.debounced_refresh, wrapped);
	assert.equal(h.watch_realtime_updates({}, () => {}, 2000), false);
});

test("onload wires the realtime recount and the actions menu", () => {
	reset();
	const listview = fake_listview();
	listview.debounced_refresh = () => {};
	frappe.listview_settings["AI Pending Action"].onload(listview);
	assert.equal(listview.debounced_refresh.ee_apa_watching, true);
	assert.deepEqual(listview.page.actions_menu, ["Confirm & Execute Selected", "Cancel Selected"]);
	assert.ok(state.calls[0].method.endsWith(".my_pending_actions"));
});

test("the selected-rows path asks through a Dialog and counts only what will run", () => {
	reset();
	const listview = fake_listview();
	listview.get_checked_items = () => [
		{ name: "A", requested_by: "nik@x", status: "Pending", summary: "mine" },
		{ name: "B", requested_by: "nik@x", status: "Pending", summary: "expired" },
	];
	h.decide_selected(listview, "confirm");
	answer_ok(state.calls[0], [{ name: "A", risk: "Low", has_hidden: false }]);
	const confirmation = state.dialogs.at(-1);
	const html = confirmation.fields_dict.message.$wrapper.content;
	assert.match(html, /Run 1 action as you\?/);
	assert.match(html, /1 of the selected are not yours, not Pending or expired/);
});

// ------------------------------------------------------------------ run

let failures = 0;
for (const [name, fn] of tests) {
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.stack ? err.stack.split("\n").slice(0, 3).join("\n       ") : String(err)));
	}
}
console.log(`\n${tests.length - failures}/${tests.length} passed`);
if (failures) process.exit(1);
