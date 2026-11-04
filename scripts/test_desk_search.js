#!/usr/bin/env node
/**
 * The Desk search box (desk_search.js, v1.795.0), executed against a stub of frappe 16.53's
 * AwesomeBar and Awesomplete and a small fake DOM rather than grepped.
 *
 * Nik, 2026-11-04: typing a customer's first name must show their records before screen names, grouped,
 * with a preview, call and email buttons, related links, and recent records on an empty box. Each
 * rule below fails silently in production (a row lands in the wrong place, a button selects its
 * row instead, a stale answer replaces a fresh one), so each is executed here:
 *
 *   1. mark() escapes the text and wraps every typed word, whatever its case.
 *   2. Phones: US numbers read 801-555-0142 however stored; tel: keeps a leading +; mailto: only
 *      for something shaped like an address.
 *   3. compose(): records first in the server's order with a heading on each group's first row,
 *      then knowledge base hits, frappe's tools, "Search everything", and the best three screens
 *      under "Screens"; indexes strictly descending; frappe's own objects never mutated.
 *   4. Text that names a screen ("project", "sales invoice") keeps the screens on top.
 *   5. No records yet: frappe's list with "Search for ..." first, nothing of ours.
 *   6. An empty box lists recent records first, then frappe's rows under "Pages".
 *   7. compose() of its own output gives the same list (every writer goes through the setter).
 *   8. A frappe row for a record we already list is dropped.
 *   9. push_recent keeps one entry per record, newest first, at most twelve.
 *  10. results_for keeps the last answer while the text grows or shrinks, and drops it otherwise.
 *  11. decorate(): the list setter composes, the renderer draws our rows (title marked, chips with
 *      tel:/mailto:, related links) and puts headings on frappe's rows; a click on a related link
 *      routes with its filters and closes the box, and never reaches Awesomplete; a click on the
 *      phone chip is left to the browser; a click on the row itself is Awesomplete's.
 *  12. A fetch answer for older text never replaces the newer one.
 *  13. install() is idempotent and leaves a box without a list accessor alone.
 *  14. remember() records a saved form under the person's key, never a new or child document.
 *  15. Review of PR #1425: an answer for older text never leads the list (Enter would open it); an
 *      arrowed pick survives a redraw; the box forgets its answer when it closes or opens; titles
 *      are escaped for frappe's renderer; the request is a POST with the CSRF token and no text in
 *      the URL; related and see-all hrefs carry their filters, and a Ctrl/Cmd/Shift click is left
 *      to the browser; a capped group's see-all says no number.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(ROOT, "erpnext_enhancements", "public", "js", "global_enhancements", "desk_search.js");

if (!fs.existsSync(FILE)) {
	console.error("MARKERS NOT FOUND: " + path.relative(process.cwd(), FILE) + " does not exist.");
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, "utf8");

let failures = 0;
function check(condition, message) {
	if (condition) console.log("  ok    " + message);
	else {
		failures += 1;
		console.error("  FAIL  " + message);
	}
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ------------------------------------------------------------------ a small fake DOM

class FakeElement {
	constructor(tag) {
		this.tagName = tag.toUpperCase();
		this.children = [];
		this.parentNode = null;
		this.className = "";
		this._html = "";
		this.listeners = [];
		this.href = "";
		this.title = "";
		this.value = "";
	}
	get classList() {
		const el = this;
		return {
			add: (c) => {
				if (!el.className.split(/\s+/).includes(c)) el.className = (el.className + " " + c).trim();
			},
			contains: (c) => el.className.split(/\s+/).includes(c),
		};
	}
	set innerHTML(v) {
		this._html = String(v);
		this.children = [];
	}
	get innerHTML() {
		return this._html + this.children.map((c) => c.outerHTML).join("");
	}
	get outerHTML() {
		return "<" + this.tagName.toLowerCase() + ' class="' + this.className + '">' + this.innerHTML + "</" + this.tagName.toLowerCase() + ">";
	}
	get firstChild() {
		return this.children[0] || null;
	}
	appendChild(child) {
		child.parentNode = this;
		this.children.push(child);
		return child;
	}
	insertBefore(child, ref) {
		child.parentNode = this;
		const i = ref ? this.children.indexOf(ref) : -1;
		if (i === -1) this.children.push(child);
		else this.children.splice(i, 0, child);
		return child;
	}
	matches(selector) {
		return selector.split(",").some((s) => this.classList.contains(s.trim().replace(/^\./, "")));
	}
	closest(selector) {
		let node = this;
		while (node) {
			if (node.matches && node.matches(selector)) return node;
			node = node.parentNode;
		}
		return null;
	}
	contains(node) {
		while (node) {
			if (node === this) return true;
			node = node.parentNode;
		}
		return false;
	}
	addEventListener(type, fn, capture) {
		this.listeners.push({ type, fn, capture: !!capture });
	}
	find(cls) {
		if (this.classList.contains(cls)) return this;
		for (const c of this.children) {
			const hit = c.find(cls);
			if (hit) return hit;
		}
		return null;
	}
	findAll(cls, out = []) {
		if (this.classList.contains(cls)) out.push(this);
		this.children.forEach((c) => c.findAll(cls, out));
		return out;
	}
}

/** Capture listeners on `on`, then (unless stopped) its bubble listeners: the order a browser uses. */
function dispatch(on, type, target, extra) {
	const event = Object.assign(
		{
			type,
			target,
			stopped: false,
			prevented: false,
			button: 0,
			stopPropagation() {
				this.stopped = true;
			},
			preventDefault() {
				this.prevented = true;
			},
		},
		extra
	);
	for (const l of on.listeners.filter((l) => l.type === type && l.capture)) l.fn(event);
	if (!event.stopped) for (const l of on.listeners.filter((l) => l.type === type && !l.capture)) l.fn(event);
	return event;
}

function makeStorage(initial) {
	const data = new Map(Object.entries(initial || {}));
	return {
		data,
		getItem: (key) => (data.has(key) ? data.get(key) : null),
		setItem: (key, value) => data.set(key, String(value)),
	};
}

// ------------------------------------------------------------------ the world

function makeWorld(opts) {
	opts = opts || {};
	const handlers = [];
	const routes = [];
	const fetches = [];
	const document = {
		createElement: (tag) => new FakeElement(tag),
		activeElement: null,
	};
	const jq = (target) => ({
		on(event, fn) {
			handlers.push([event, fn, target]);
			return this;
		},
		data() {
			return this;
		},
	});
	const frappe = {
		session: { user: opts.user || "pat@x" },
		csrf_token: "csrf-123",
		route_options: null,
		open_in_new_tab: false,
		datetime: { now_datetime: () => "2026-11-04 10:00:00", prettyDate: () => "2 hours ago" },
		router: {
			make_url: (r) => "/desk/" + r.join("/").toLowerCase().replace(/ /g, "-"),
			convert_from_standard_route: (r) => r,
			get_route_from_arguments: (r) => r,
		},
		set_route: (r) => routes.push({ route: r, options: frappe.route_options }),
		searchdialog: { search: { init_search: (txt, kind) => routes.push({ full_search: txt, kind }) } },
		search: {},
	};
	const ctx = {
		console,
		JSON,
		Object,
		Array,
		String,
		Set,
		Map,
		Math,
		Date,
		setTimeout: (fn) => {
			ctx._timers.push(fn);
			return ctx._timers.length;
		},
		clearTimeout: () => {},
		_timers: [],
		encodeURIComponent,
		URLSearchParams,
		document,
		jQuery: jq,
		frappe,
		__: (s, args) => (args ? s.replace(/\{(\d)\}/g, (_m, i) => args[i]) : s),
		localStorage: opts.storage || makeStorage(),
		fetch: (url, init) =>
			new Promise((resolve) => {
				fetches.push({ url, init, resolve });
			}),
	};
	ctx.window = ctx;
	vm.createContext(ctx);
	vm.runInContext(SOURCE, ctx, { filename: FILE });
	return { ctx, frappe, handlers, routes, fetches, document, api: ctx.erpnext_enhancements.desk_search };
}

// A stub of frappe 16.53's Awesomplete: a `list` accessor on the prototype that evaluates when the
// input has focus, get_item by value, and its own click handler on the list (bubble phase).
function makeAwesomplete(world) {
	class Base {
		set list(v) {
			this._list = v;
			this.evaluated = (this.evaluated || 0) + 1;
			const sorted = v.slice().sort((a, b) => b.index - a.index);
			this.suggestions = sorted.map((o) => ({ label: o.index, value: o.value }));
			this.index = this.suggestions.length ? 0 : -1;
			this.rendered = sorted.map((o) => this.item({ label: o.index, value: o.value }, this.input.value, 0));
			this.ul.children = [];
			this.rendered.forEach((li) => this.ul.appendChild(li));
		}
		get list() {
			return this._list;
		}
		goto(i) {
			this.index = i;
		}
		get_item(value) {
			return this._list.find((o) => o.value === value);
		}
	}
	const aw = new Base();
	aw.input = new FakeElement("input");
	aw.ul = new FakeElement("ul");
	aw.selected = [];
	aw.ul.addEventListener("click", (e) => aw.selected.push(e.target), false);
	aw.item = function (suggestion) {
		const d = this.get_item(suggestion.value);
		const li = new FakeElement("li");
		li.innerHTML = "<a>" + (d.label || d.value) + "</a>";
		li.frappe_row = d;
		return li;
	};
	return aw;
}

// What frappe's input handler builds for "Dana" (shapes as in awesome_bar.js and search_utils.js).
function frappeOptions() {
	return [
		{ label: "Search for <b>Dana</b> <kbd>↵</kbd>", value: "Search for Dana", match: "Dana", index: 100000, default: "Search", onclick: () => "full" },
		{ label: "Drive Link Candidate List", value: "Drive Link Candidate List", match: "Drive Link Candidate", index: 200.05, route: ["List", "Drive Link Candidate"] },
		{ label: "Calendar Cutover Item List", value: "Calendar Cutover Item List", match: "Calendar Cutover Item", index: 179.05, route: ["List", "Calendar Cutover Item"] },
		{ label: "Report Cheques and Deposits", value: "Report Cheques and Deposits", index: 195, route: ["query-report", "Cheques and Deposits"] },
		{ label: "Calendar View List", value: "Calendar View List", match: "Calendar View", index: 157.05, route: ["List", "Calendar View"] },
		{ label: "Project <b>PRJ-00433</b>", value: "Project PRJ-00433", index: 80, route: ["Form", "Project", "PRJ-00433"] },
		{ label: "SOP-06 · Fountain <b>dana</b>", value: "SOP-06", index: 120, route: ["Form", "Knowledge Article", "SOP-06"] },
		{ label: "Find Dana in Project", value: "Find Dana in Project", index: 90, default: "Current", match: "Dana" },
	];
}

function results() {
	return {
		txt: "Dana",
		groups: [
			{
				key: "people",
				label: "People",
				total: 3,
				more: false,
				see_all: null,
				rows: [
					{ doctype: "Contact", name: "Dana Example", title: "Dana Example", type: "Contact", subtitle: "Example Residence", phone: "+18015550142", email: "dana@example.com", route: ["Form", "Contact", "Dana Example"], related: [{ label: "Example Residence", kind: "Account", route: ["Form", "Customer", "Example Residence"] }, { label: "5 projects", route: ["List", "Project"], route_options: { customer: "Example Residence" } }] },
					{ doctype: "Contact", name: "Danae Corp", title: "Danae Corp", type: "Contact", subtitle: "", phone: "", email: "", route: ["Form", "Contact", "Danae Corp"] },
				],
			},
			{
				key: "projects",
				label: "Projects",
				total: 5,
				more: false,
				see_all: { route: ["List", "Project"], route_options: { name: ["in", ["PRJ-00433", "PRJ-00440"]] } },
				rows: [
					{ doctype: "Project", name: "PRJ-00433", title: "Dana Example - Residence", type: "Project", subtitle: "PRJ-00433 · Active", phone: "", email: "", route: ["Form", "Project", "PRJ-00433"] },
				],
			},
		],
	};
}

const values = (list) => list.map((o) => o.value);

// ------------------------------------------------------------------ 1-2: helpers

console.log("1-2. mark, phones, mail");
{
	const { api } = makeWorld();
	check(api.mark("Dana Whitfield", "dana") === "<mark>Dana</mark> Whitfield", "a word is marked whatever its case");
	check(api.mark("Dana Whitfield", "whit dana") === "<mark>Dana</mark> <mark>Whit</mark>field", "every word is marked");
	check(api.mark('<img src=x onerror="a()">', "img") === "&lt;<mark>img</mark> src=x onerror=&quot;a()&quot;&gt;", "the text is escaped around the marks");
	check(api.mark("A & B", "") === "A &amp; B", "no words, just escaped");
	check(api.format_phone("+18015550142") === "801-555-0142", "+1 country code dropped");
	check(api.format_phone("(801) 555 0142") === "801-555-0142", "punctuation normalized");
	check(api.format_phone("+44 20 7946 0958") === "+44 20 7946 0958", "a foreign number stays as stored");
	check(api.tel_href("+1 801-555-0142") === "tel:+18015550142", "tel: keeps the leading +");
	check(api.tel_href("801") === null, "too short to dial");
	check(api.mail_href("dana@example.com") === "mailto:dana@example.com", "an address gets mailto:");
	check(api.mail_href("javascript:alert(1)") === null && api.mail_href("a@b") === null, "anything else gets nothing");
}

// ------------------------------------------------------------------ 3, 7, 8: compose with records

console.log("3, 7, 8. compose with records");
{
	const { api } = makeWorld();
	const list = frappeOptions();
	const before = JSON.stringify(list.map((o) => o.index));
	const out = api.compose(list, { txt: "Dana", results: results(), recents: [] });
	check(
		same(values(out), [
			"Contact: Dana Example · Dana Example",
			"Contact: Danae Corp · Danae Corp",
			"Project: Dana Example - Residence · PRJ-00433",
			"SOP-06",
			"Find Dana in Project",
			"Search for Dana",
			"Drive Link Candidate List",
			"Report Cheques and Deposits",
			"Calendar Cutover Item List",
		]),
		"records, knowledge base, tools, search-all, then the best three screens: " + values(out).join(" | ")
	);
	check(out[0]._ee.heading.label === "People" && out[1]._ee.heading === null && out[2]._ee.heading.label === "Projects", "a heading on each group's first row");
	check(out[2]._ee.heading.see_all.route[1] === "Project" && out[2]._ee.heading.total === 5, "the group's see-all travels with its heading");
	check(out[3]._ee_heading.label === "Knowledge base" && out[6]._ee_heading.label === "Screens", "frappe's rows get their headings on copies");
	check(out.every((o, i) => i === 0 || o.index < out[i - 1].index), "indexes strictly descending");
	check(JSON.stringify(list.map((o) => o.index)) === before && !list.some((o) => o._ee_heading), "frappe's own objects are untouched");
	check(!values(out).includes("Project PRJ-00433"), "frappe's recent row for a listed record is dropped");
	check(!values(out).includes("Calendar View List"), "only three screens");
	const search_all = out.find((o) => o.value === "Search for Dana");
	check(!/kbd/.test(search_all.label) && /Search everything for <b>Dana<\/b>/.test(search_all.label), "search-all loses its Enter hint");
	check(search_all.onclick() === "full", "and keeps frappe's onclick");
	const again = api.compose(out, { txt: "Dana", results: results(), recents: [] });
	check(same(values(again), values(out)) && same(again.map((o) => o.index), out.map((o) => o.index)), "composing its own output changes nothing");
	check(same(values(api.compose(again.concat([frappeOptions()[4]]), { txt: "Dana", results: results() })), values(out)), "and a frappe row added later is ranked in, not appended");
}

// ------------------------------------------------------------------ 4: a screen name

console.log("4. text that names a screen");
{
	const { api } = makeWorld();
	const list = [
		{ value: "Search for project", default: "Search", index: 100000 },
		{ value: "Project List", match: "Project", index: 200.05, route: ["List", "Project"] },
		{ value: "Project Template List", match: "Project Template", index: 150, route: ["List", "Project Template"] },
	];
	const res = { txt: "project", groups: [{ key: "projects", label: "Projects", total: 1, rows: [{ doctype: "Project", name: "PRJ-1", title: "Big project", type: "Project", route: ["Form", "Project", "PRJ-1"] }] }] };
	const out = api.compose(list, { txt: "project", results: res });
	check(out[0].value === "Project List" && out[0]._ee_heading.label === "Screens", "\"project\" keeps Project List on top");
	check(api.names_a_screen([{ value: "Sales Invoice List", match: "Sales Invoice" }], "sales inv"), "two words that start a screen name");
	check(!api.names_a_screen([{ value: "Drive Link Candidate List", match: "Drive Link Candidate" }], "cand"), "one fuzzy word is not a screen name");
}

// ------------------------------------------------------------------ 5: no records yet

console.log("5. no records yet");
{
	const { api } = makeWorld();
	const out = api.compose(frappeOptions(), { txt: "Dana", results: null });
	check(out[0].value === "Search for Dana", "\"Search for ...\" first, so a fast Enter opens the full results");
	check(out.length === frappeOptions().length && !out.some((o) => o._ee), "frappe's rows, nothing of ours");
	const empty = api.compose(frappeOptions(), { txt: "Dana", results: { txt: "Dana", groups: [] } });
	check(empty[0].value === "Search for Dana" && !empty.some((o) => o._ee), "an empty answer is the same");
}

// ------------------------------------------------------------------ 6: recents

console.log("6. recent records on an empty box");
{
	const { api } = makeWorld();
	const recents = [
		{ d: "Project", n: "PRJ-00707", t: "Dana Example - Fountain", at: "2026-11-04 08:00:00" },
		{ d: "Customer", n: "Example Residence", t: "Example Residence" },
	];
	const frappe_rows = [{ value: "AI Pending Action List", index: 40, route: ["List", "AI Pending Action"] }];
	const out = api.compose(frappe_rows, { txt: "", recents });
	check(same(values(out).slice(0, 2), ["Recent: Dana Example - Fountain · Project PRJ-00707", "Recent: Example Residence · Customer Example Residence"]), "recents first, newest first");
	check(out[0]._ee.heading.label === "Recent" && out[1]._ee.row.type === "Account", "under \"Recent\"; a Customer reads Account");
	check(out[2]._ee_heading.label === "Pages", "frappe's rows under \"Pages\"");
	const twice = api.compose([{ value: "Project PRJ-00707", index: 80, route: ["Form", "Project", "PRJ-00707"] }].concat(frappe_rows), { txt: "", recents });
	check(!values(twice).includes("Project PRJ-00707") && twice[2].value === "AI Pending Action List", "frappe's own row for a recent record is dropped, and Pages starts after it");
	check(same(api.compose(frappe_rows, { txt: "", recents: [] }), frappe_rows), "no recents, frappe's list as it was");
	check(!api.compose(frappe_rows, { txt: "C", recents }).some((o) => o._ee), "one character: frappe's own filtered recents");
}

// ------------------------------------------------------------------ 9-10: recents and stale answers

console.log("9-10. push_recent, results_for");
{
	const { api } = makeWorld();
	let list = [];
	for (let i = 0; i < 15; i++) list = api.push_recent(list, { d: "Project", n: "P" + i });
	check(list.length === 12 && list[0].n === "P14", "newest first, at most twelve");
	list = api.push_recent(list, { d: "Project", n: "P5" });
	check(list[0].n === "P5" && list.filter((e) => e.n === "P5").length === 1, "one entry per record");
	const state = { results: { txt: "Dan", groups: [{ rows: [] }] } };
	check(api.results_for(state, "Dana") === state.results, "kept while the text grows");
	check(api.results_for(state, "Da") === state.results, "kept while it shrinks");
	check(api.results_for(state, "Wads") === null, "dropped for other text");
	check(api.results_for({ results: { txt: "Dan", groups: [] } }, "Dan") === null, "an empty answer is no answer");
}

// ------------------------------------------------------------------ 11-12: decorate

console.log("11-12. decorate: the setter, the renderer, the clicks, a stale fetch");
{
	const world = makeWorld();
	const { api, frappe, routes, fetches, ctx } = world;
	const aw = makeAwesomplete(world);
	let hidden = 0;
	const bar = {
		awesomplete: aw,
		options: frappeOptions(),
		deduplicate: (o) => o.slice(),
		search_modal: {
			modal: (what) => (what === "hide" ? (hidden += 1) : null),
			handlers: [],
			on(events, fn) {
				this.handlers.push([events, fn]);
				return this;
			},
		},
	};
	api.decorate(bar);
	check(aw._ee_desk_search === true && aw.ul.classList.contains("ee-ds-list"), "decorated once, list marked for the stylesheet");
	api.decorate(bar);
	check(aw.ul.listeners.filter((l) => l.capture).length === 2, "a second decorate adds nothing");

	aw.input.value = "Dana";
	const input_listeners = aw.input.listeners.filter((l) => l.type === "input");
	input_listeners.forEach((l) => l.fn({}));
	check(ctx._timers.length === 1, "typing schedules one request");
	aw.list = bar.options;
	check(aw._list[0].value === "Search for Dana", "before the answer: frappe's list, Search first");

	ctx._timers.shift()();
	check(fetches.length === 1 && /desk_search\.search$/.test(fetches[0].url) && fetches[0].init.method === "POST", "a POST, nothing typed in the URL");
	check(fetches[0].init.body.get("txt") === "Dana" && fetches[0].init.headers["X-Frappe-CSRF-Token"] === "csrf-123", "the text in the body, with the CSRF token");
	// The person types on before the first answer arrives.
	aw.input.value = "Dana W";
	input_listeners.forEach((l) => l.fn({}));
	ctx._timers.shift()();
	check(fetches.length === 2, "a second request for the newer text");
	const stale = { ok: true, json: () => ({ message: results() }) };
	fetches[0].resolve(stale);
	setImmediate(() => {
		check(!aw._list.some((o) => o._ee), "the older answer is dropped");
		const fresh = results();
		fresh.txt = "Dana W";
		fetches[1].resolve({ ok: true, json: () => ({ message: fresh }) });
		setImmediate(() => {
			check(aw._list[0]._ee && aw._list[0]._ee.kind === "record", "the newer answer is drawn, records first");
			const li = aw.rendered[0];
			check(li.classList.contains("ee-ds-li") && li.classList.contains("ee-ds-headed"), "our row, with its heading");
			check(li.find("ee-ds-head").innerHTML.includes("People"), "the heading reads People");
			check(li.find("ee-ds-title").innerHTML === "<mark>Dana</mark> Example", "the title is marked");
			check(li.find("ee-ds-call").href === "tel:+18015550142" && li.find("ee-ds-call").innerHTML === "801-555-0142", "the phone chip dials");
			check(li.find("ee-ds-mail").href === "mailto:dana@example.com", "the email chip mails");
			const rels = li.findAll("ee-ds-rel");
			check(rels.length === 2 && rels[0].innerHTML.includes("Account"), "related links drawn");
			const kb = aw.rendered.find((r) => r.frappe_row && r.frappe_row.value === "SOP-06");
			check(kb && kb.classList.contains("ee-ds-headed") && kb.firstChild.classList.contains("ee-ds-head"), "frappe's row gets its heading on top");
			const project = aw.rendered.find((r) => r.find("ee-ds-head") && r.find("ee-ds-head").innerHTML.includes("Projects"));
			check(project.find("ee-ds-seeall").innerHTML === "See all 5", "see-all counts the group");

			// an arrowed pick survives a redraw for the same text (the hook's reply, say)
			aw.goto(2);
			const picked = aw.suggestions[2].value;
			aw.list = bar.options;
			check(aw.index === 2 && aw.suggestions[aw.index].value === picked, "an arrowed pick stays picked when the list is redrawn");
			aw.goto(0);
			aw.list = bar.options;
			check(aw.index === 0, "row 0 stays row 0");

			// filters ride in the href, so a new tab keeps them; a Ctrl/Cmd/Shift click is the browser's
			const rel_project = li.findAll("ee-ds-rel")[1];
			check(rel_project.href === "/desk/list/project?customer=Example%20Residence", "a related link's href carries its filter: " + rel_project.href);
			check(project.find("ee-ds-seeall").href === "/desk/list/project?name=" + encodeURIComponent(JSON.stringify(["in", ["PRJ-00433", "PRJ-00440"]])), "and so does see-all's");
			const n_before = routes.length;
			const ctrl = dispatch(aw.ul, "click", rel_project, { ctrlKey: true });
			check(ctrl.stopped && !ctrl.prevented && routes.length === n_before && frappe.route_options === null, "a Ctrl-click opens the href in a new tab and leaves this tab's route_options alone");
			const shift = dispatch(aw.ul, "click", rel_project, { shiftKey: true });
			check(!shift.prevented && routes.length === n_before, "so does a Shift-click");

			// a related link: routes with its filters, closes the box, never selects the row
			const before = aw.selected.length;
			dispatch(aw.ul, "mousedown", rels[1]);
			const click = dispatch(aw.ul, "click", rels[1], { ctrlKey: false });
			check(click.stopped && click.prevented, "the link's click is ours");
			check(aw.selected.length === before, "Awesomplete never sees it");
			const last = routes[routes.length - 1];
			check(same(last.route, ["List", "Project"]) && same(last.options, { customer: "Example Residence" }), "the list opens filtered to the account");
			check(hidden === 1 && aw.input.value === "", "and the box closes");

			// the phone chip: left to the browser
			const n_routes = routes.length;
			const call = dispatch(aw.ul, "click", li.find("ee-ds-call"));
			check(call.stopped && !call.prevented && routes.length === n_routes, "the phone chip opens the dialer, not the record");
			check(aw.selected.length === before, "and selects nothing");

			// see-all
			dispatch(aw.ul, "click", project.find("ee-ds-seeall"));
			const seeall = routes[routes.length - 1];
			check(same(seeall.options, { name: ["in", ["PRJ-00433", "PRJ-00440"]] }), "see-all opens the group's matches");

			// the row itself: Awesomplete's
			const main = li.find("ee-ds-main");
			const row_click = dispatch(aw.ul, "click", main);
			check(!row_click.stopped && aw.selected[aw.selected.length - 1] === main, "a click on the row is Awesomplete's to select");

			// the box forgets its answer when it closes (frappe clears the input without an input event)
			const forget = bar.search_modal.handlers.find((h) => /hide\.bs\.modal/.test(h[0]) && /show\.bs\.modal/.test(h[0]));
			check(!!forget, "the box listens for its own close and open");
			forget[1]();
			check(bar._ee_desk_search.results === null, "and drops the answer");
			aw.input.value = "Da";
			aw.list = frappeOptions();
			check(!aw._list.some((o) => o._ee), "so two letters typed into the reopened box never show the old answer");

			// a mixed group's see-all opens the full results
			const mixed = { awesomplete: makeAwesomplete(world), options: [], deduplicate: (o) => o, search_modal: { modal() {} } };
			api.decorate(mixed);
			mixed.awesomplete.input.value = "Dana";
			const r = results();
			r.groups[1].see_all = null;
			mixed._ee_desk_search.results = r;
			mixed.awesomplete.list = [];
			const proj = mixed.awesomplete.rendered.find((x) => x.find("ee-ds-seeall"));
			dispatch(mixed.awesomplete.ul, "click", proj.find("ee-ds-seeall"));
			check(same(routes[routes.length - 1], { full_search: "Dana", kind: "global_search" }), "a mixed group's see-all opens the full results");

			finish(world);
		});
	});
}

function review_checks() {
	console.log("15. review of PR #1425");
	const { api } = makeWorld();
	const stale = api.compose(frappeOptions(), { txt: "Dana W", results: results() });
	check(stale[0].value === "Search for Dana" && /kbd/.test(stale[0].label), "an answer for older text: frappe's own Search row leads, Enter hint and all");
	check(stale[1]._ee && stale[1]._ee.kind === "record", "the older records stay in view below it");
	check(stale.filter((o) => o.value === "Search for Dana").length === 1, "and no second search row");
	check(api.compose(frappeOptions(), { txt: "dana", results: results() })[0]._ee.kind === "record", "the same text in another case is the same answer");
	const r = results();
	r.groups[0].rows[0].title = "<img src=x onerror=a()>";
	check(api.record_options(r)[0].label === "&lt;img src=x onerror=a()&gt;", "a record's label is escaped for frappe's renderer");
	check(api.recent_options([{ d: "Project", n: "P-1", t: "<b>x</b>" }])[0].label === "&lt;b&gt;x&lt;/b&gt;", "and a recent one's");
	check(api.link_href({ route: ["List", "Project"] }) === "/desk/list/project", "no filters, no query string");
	check(api.link_href({ route: ["List", "Project"], route_options: { project_name: ["like", "%dana%"] } }) === "/desk/list/project?project_name=" + encodeURIComponent('["like","%dana%"]'), "a JSON filter is written the way frappe's router writes it");
	const world = makeWorld();
	const aw = makeAwesomplete(world);
	const bar = { awesomplete: aw, options: [], deduplicate: (o) => o, search_modal: { modal() {} } };
	world.api.decorate(bar);
	aw.input.value = "Dana";
	const capped = results();
	capped.groups[1].more = true;
	capped.groups[1].see_all = { route: ["List", "Project"], route_options: { project_name: ["like", "%Dana%"] } };
	bar._ee_desk_search.results = capped;
	aw.list = [];
	const head = aw.rendered.find((x) => x.find("ee-ds-head") && x.find("ee-ds-head").innerHTML.includes("Projects"));
	check(head.find("ee-ds-seeall").innerHTML === "See all", "a capped group's see-all says no number");
}

function finish() {
	review_checks();

	// ------------------------------------------------------------------ 13: install

	console.log("13. install");
	{
		const world = makeWorld();
		const calls = [];
		const AwesomeBar = function () {};
		AwesomeBar.prototype.setup_event_listeners = function () {
			calls.push("frappe");
			this.awesomplete = makeAwesomplete(world);
		};
		world.frappe.search.AwesomeBar = AwesomeBar;
		check(world.api.install() === true && world.api.install() === false, "installed once");
		const bar = new AwesomeBar();
		bar.setup_event_listeners();
		check(calls.length === 1 && bar.awesomplete._ee_desk_search === true, "frappe's setup runs, then ours");
		const early = { awesomplete: makeAwesomplete(world), options: [], deduplicate: (o) => o, search_modal: { modal() {} } };
		const world2 = makeWorld();
		const AB2 = function () {};
		AB2.prototype.setup_event_listeners = function () {};
		world2.frappe.search.AwesomeBar = AB2;
		world2.frappe.app = { awesome_bar: early };
		world2.api.install();
		check(early.awesomplete._ee_desk_search === true, "a box opened before install is decorated at install (frappe.app.awesome_bar)");
		const odd = { awesomplete: { input: new FakeElement("input"), ul: new FakeElement("ul") } };
		world.api.decorate(odd);
		check(!odd.awesomplete._ee_desk_search, "no list accessor: frappe's box is left alone");
		const none = makeWorld();
		check(none.api.install() === false, "no AwesomeBar: nothing to do");
	}

	// ------------------------------------------------------------------ 14: remember

	console.log("14. remember");
	{
		const storage = makeStorage();
		const world = makeWorld({ storage });
		const frm = (over) =>
			Object.assign(
				{ doctype: "Opportunity", docname: "CRM-OPP-2026-00058", doc: { custom_opportunity_name: "Dana Example - Fountain", title: "Example Residence" }, meta: { title_field: "title" }, is_new: () => false },
				over
			);
		world.api.remember(frm());
		const saved = JSON.parse(storage.getItem("ee-desk-search-recent:pat@x"));
		check(saved.length === 1 && saved[0].t === "Dana Example - Fountain" && saved[0].at === "2026-11-04 10:00:00", "an Opportunity is remembered by the name people see");
		world.api.remember(frm({ is_new: () => true, docname: "new-opportunity-1" }));
		world.api.remember(frm({ meta: { istable: 1 }, docname: "row-1" }));
		check(JSON.parse(storage.getItem("ee-desk-search-recent:pat@x")).length === 1, "never a new or child document");
		world.api.remember(frm({ doctype: "Project", docname: "PRJ-00707", doc: { project_name: "Fountain 4-Tier" }, meta: { title_field: "project_name" } }));
		check(JSON.parse(storage.getItem("ee-desk-search-recent:pat@x"))[0].n === "PRJ-00707", "newest first");
		const guest = makeWorld({ storage: makeStorage(), user: "Guest" });
		guest.api.remember(frm());
		check(guest.ctx.localStorage.data.size === 0, "nothing for Guest");

		// an empty box draws the recents with "Opened 2 hours ago"
		const aw = makeAwesomplete(world);
		const bar = { awesomplete: aw, options: [], deduplicate: (o) => o, search_modal: { modal() {} } };
		world.api.decorate(bar);
		aw.input.value = "";
		aw.list = [{ value: "AI Pending Action List", index: 40, route: ["List", "AI Pending Action"] }];
		const first = aw.rendered[0];
		check(first.find("ee-ds-head").innerHTML.includes("Recent") && first.find("ee-ds-sub").innerHTML === "Opened 2 hours ago", "an empty box lists recents, each with when it was opened");
		check(first.find("ee-ds-type").innerHTML === "Project", "with its type");
	}

	if (failures) {
		console.error("\n" + failures + " check(s) failed");
		process.exit(1);
	}
	console.log("\nall checks passed");
}
