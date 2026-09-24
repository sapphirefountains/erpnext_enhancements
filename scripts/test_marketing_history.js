#!/usr/bin/env node
/**
 * Back and Forward on /marketing, driven through the real app on a fake DOM and a fake history.
 *
 * The rules pinned here are the product owner's, and each one is a way this page broke:
 *
 *   - **Back returns to the previous screen and Forward restores it.** Every tap that changes the
 *     screen adds one entry; the page's own start re-stamps the entry it opened on, so Back from
 *     the first screen leaves the page. A link to the screen already showing adds nothing, or the
 *     next Back would appear to do nothing, and that holds under another address for the same
 *     screen (/marketing/calendar/<this month> is /marketing; a week from any of its days).
 *   - **Unsaved work is never silently lost.** Back or Forward from a composer with changes asks
 *     the same "Leave without saving?" a click asks. The step is put back while it asks, so Stay
 *     keeps the screen, its address and every character typed; Discard takes the step again.
 *   - **A late reply never draws over the screen the person went to**, never arms the leave
 *     guard for a post nobody is looking at, and never moves the page back to the post. The
 *     queue's Send back redraws the queue only while the queue is the screen.
 *   - **A dialog belongs to its screen**: Back closes "Delete this post?" rather than leaving it
 *     standing over another screen, still bound to the old post.
 *   - **Delete replaces**, so Back never lands on the address of a post that is gone; and when the
 *     post was opened from the calendar it lands on, the page steps back onto that calendar's own
 *     entry, so no second copy of it sits underneath for the next Back to land on.
 *   - **A copy comes back on Forward**, from its own entry, not as an empty form.
 *
 * The history behaves as a browser's does where it matters: `go()`/`back()` are queued and land
 * on a later task, the person's own Back and Forward land at once, and pushState drops the
 * forward entries. Replies come from a stubbed fetch, and any of them can be held open to land
 * late.
 *
 * Run: node scripts/test_marketing_history.js
 */

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const DIR = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "marketing");

let failures = 0;
function check(label, actual, expected) {
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

// ------------------------------------------------------------------ a fake DOM

/** Just enough DOM to mount the real app under node. Nothing is laid out or drawn. */
class FakeElement {
	constructor(doc, tag) {
		this.ownerDocument = doc;
		this.tagName = String(tag).toUpperCase();
		this.childNodes = [];
		this.parentNode = null;
		this.attrs = {};
		this.dataset = {};
		this.style = {};
		this.listeners = {};
		this.className = "";
		this.hidden = false;
		this.value = "";
		this.text = "";
		this.open = false;
		const classes = () => this.className.split(/\s+/).filter(Boolean);
		this.classList = {
			add: (name) => {
				if (!classes().includes(name)) this.className = `${this.className} ${name}`.trim();
			},
			remove: (name) => {
				this.className = classes()
					.filter((c) => c !== name)
					.join(" ");
			},
			toggle: (name, on) => {
				const want = on === undefined ? !classes().includes(name) : !!on;
				if (want) this.classList.add(name);
				else this.classList.remove(name);
			},
			contains: (name) => classes().includes(name),
		};
	}
	get firstChild() {
		return this.childNodes[0] || null;
	}
	get textContent() {
		return this.text + this.childNodes.map((node) => node.textContent).join("");
	}
	set textContent(value) {
		for (const node of this.childNodes) node.parentNode = null;
		this.childNodes = [];
		this.text = String(value);
	}
	appendChild(node) {
		if (node.parentNode) node.parentNode.removeChild(node);
		node.parentNode = this;
		this.childNodes.push(node);
		return node;
	}
	removeChild(node) {
		const i = this.childNodes.indexOf(node);
		if (i !== -1) this.childNodes.splice(i, 1);
		node.parentNode = null;
		return node;
	}
	remove() {
		if (this.parentNode) this.parentNode.removeChild(this);
	}
	get isConnected() {
		let node = this;
		while (node.parentNode) node = node.parentNode;
		return node === this.ownerDocument.documentElement;
	}
	setAttribute(name, value) {
		this.attrs[name] = String(value);
	}
	getAttribute(name) {
		return name in this.attrs ? this.attrs[name] : null;
	}
	removeAttribute(name) {
		delete this.attrs[name];
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	removeEventListener(type, fn) {
		const list = this.listeners[type] || [];
		const i = list.indexOf(fn);
		if (i !== -1) list.splice(i, 1);
	}
	dispatch(type, extra) {
		const ev = { type, target: this, button: 0, preventDefault() {}, stopPropagation() {}, ...(extra || {}) };
		for (const fn of (this.listeners[type] || []).slice()) fn(ev);
	}
	focus() {
		this.ownerDocument.activeElement = this;
	}
	// <dialog>: the close event is queued, as a browser queues it.
	showModal() {
		this.open = true;
	}
	close() {
		if (!this.open) return;
		this.open = false;
		setImmediate(() => this.dispatch("close"));
	}
	*walk() {
		for (const node of this.childNodes) {
			yield node;
			yield* node.walk();
		}
	}
	querySelectorAll(selector) {
		const groups = selector.split(",").map((s) => s.trim().split(/\s+/));
		const out = [];
		for (const node of this.walk()) {
			if (groups.some((chain) => matchesChain(node, chain, this))) out.push(node);
		}
		return out;
	}
}

/** `tag`, `.class`, `[attr='v']` compounds, joined by the descendant combinator only. */
function matchesCompound(node, compound) {
	const m = /^([a-z]*)((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i.exec(compound);
	if (!m) throw new Error(`the fake DOM cannot match ${compound}`);
	if (m[1] && node.tagName !== m[1].toUpperCase()) return false;
	for (const cls of (m[2] || "").split(".").filter(Boolean)) if (!node.classList.contains(cls)) return false;
	for (const attr of (m[3] || "").match(/\[[^\]]+\]/g) || []) {
		const [, name, value] = /^\[([\w-]+)(?:=['"]?([^'"\]]*)['"]?)?\]$/.exec(attr);
		if (value === undefined ? node.getAttribute(name) === null : node.getAttribute(name) !== value) return false;
	}
	return true;
}

function matchesChain(node, chain, scope) {
	if (!matchesCompound(node, chain[chain.length - 1])) return false;
	let rest = chain.length - 2;
	for (let up = node.parentNode; rest >= 0 && up && up !== scope.parentNode; up = up.parentNode) {
		if (matchesCompound(up, chain[rest])) rest -= 1;
	}
	return rest < 0;
}

function fakeDocument() {
	const doc = { listeners: {} };
	doc.createElement = (tag) => new FakeElement(doc, tag);
	doc.documentElement = new FakeElement(doc, "html");
	doc.body = new FakeElement(doc, "body");
	doc.documentElement.appendChild(doc.body);
	doc.activeElement = doc.body;
	doc.querySelectorAll = (selector) => doc.documentElement.querySelectorAll(selector);
	doc.addEventListener = FakeElement.prototype.addEventListener;
	doc.removeEventListener = FakeElement.prototype.removeEventListener;
	return doc;
}

// ------------------------------------------------------------------ a fake browser history

/**
 * A history that behaves as a browser's does where it matters here: pushState drops the forward
 * entries and moves the address; go/back/forward are queued and land on a later task; the
 * person's own Back and Forward land at once; a traversal past either end does nothing (in a
 * browser it would leave the document). `calls` records each call and its argument count.
 */
function fakeBrowser(options) {
	const opts = options || {};
	const listeners = {};
	const entries = (opts.entries || [{ state: null, url: opts.url || "/marketing" }]).map((e) => ({ ...e }));
	let index = opts.index === undefined ? entries.length - 1 : opts.index;
	const calls = [];
	const clone = (value) => (value === undefined || value === null ? null : structuredClone(value));
	const traverse = (delta) => {
		const target = index + delta;
		if (target < 0 || target >= entries.length || !delta) return false;
		index = target;
		const ev = { type: "popstate", state: clone(entries[index].state) };
		for (const fn of (listeners.popstate || []).slice()) fn(ev);
		return true;
	};
	const history = {
		get state() {
			return clone(entries[index].state);
		},
		get length() {
			return entries.length;
		},
		pushState(state, title, url) {
			calls.push({ call: "push", args: arguments.length, state: clone(state), url });
			entries.splice(index + 1);
			entries.push({ state: clone(state), url: url === undefined ? entries[index].url : url });
			index = entries.length - 1;
		},
		replaceState(state, title, url) {
			calls.push({ call: "replace", args: arguments.length, state: clone(state), url });
			entries[index] = { state: clone(state), url: url === undefined ? entries[index].url : url };
		},
		go(delta) {
			calls.push({ call: "go", delta });
			setImmediate(() => traverse(delta));
		},
		back() {
			this.go(-1);
		},
		forward() {
			this.go(1);
		},
	};
	const location = {
		get pathname() {
			return entries[index].url.split("?")[0];
		},
		get search() {
			const q = entries[index].url.indexOf("?");
			return q === -1 ? "" : entries[index].url.slice(q);
		},
	};
	const win = {
		history,
		location,
		addEventListener(type, fn) {
			(listeners[type] = listeners[type] || []).push(fn);
		},
		removeEventListener(type, fn) {
			const list = listeners[type] || [];
			const i = list.indexOf(fn);
			if (i !== -1) list.splice(i, 1);
		},
	};
	return {
		win,
		entries,
		calls,
		index: () => index,
		count: (name) => calls.filter((c) => c.call === name).length,
		userBack: () => traverse(-1),
		userForward: () => traverse(1),
	};
}

const flush = async (rounds) => {
	for (let i = 0; i < (rounds || 6); i++) await new Promise((resolve) => setImmediate(resolve));
};

// ------------------------------------------------------------------ the server

const SPA = "erpnext_enhancements.marketing.publish.spa.";
const ACTIONS = "erpnext_enhancements.marketing.publish.approval.";

const BOOTSTRAP = {
	full_name: "Mia Marketer",
	timezone: "America/Denver",
	week_start: "Sunday",
	today: "2026-09-24",
	publishing: { enabled: true, networks: { Facebook: true } },
	networks: ["Facebook"],
	accounts: [{ name: "ACC-FB", network: "Facebook", label: "Sapphire Fountains", enabled: true }],
	limits: {},
	quota: [],
	pending_approval: 0,
	asset_types: [],
};

function post(name, extra) {
	return {
		name,
		title: `Post ${name}`,
		status: "Draft",
		body: "Opening day at the plaza.",
		link: "",
		targets: [],
		media: [],
		scheduled_at: null,
		modified: "2026-09-24 09:00:00",
		author: "mia@example.com",
		network_check: [],
		actions: { can_edit: true, can_submit: true, can_approve: false, approve_problems: [], locked: false, can_delete: true },
		...(extra || {}),
	};
}

const LOCKED = post("SPOST-LOCKED", {
	title: "The approved one",
	status: "Approved",
	actions: { can_edit: false, can_submit: false, can_approve: false, approve_problems: [], locked: true, can_cancel: false, can_delete: false },
});

/** A row of the approval queue (`approval_queue`), for `boot(M, {queue: [...]})`. */
const QUEUED = { name: "SPOST-Q", title: "Waiting for a manager", author: "mia@example.com", scheduled_at: null, problems: 0, networks: [], approve_problems: [] };

/**
 * A fetch that answers by method name. `hold(method)` keeps that method's next reply back until
 * `release` is called on what it returns, so it can land after the person has moved on.
 */
function fakeServer(options) {
	const opts = options || {};
	const held = {};
	const seen = [];
	const answer = (method, args) => {
		if (method === SPA + "get_bootstrap") return BOOTSTRAP;
		if (method === SPA + "get_calendar") return { posts: [], unscheduled: [] };
		if (method === SPA + "approval_queue") return { posts: opts.queue || [] };
		if (method === ACTIONS + "send_back") return {};
		if (method === SPA + "check_post") return { general: [], by_network: {} };
		if (method === SPA + "get_post") return args.name === LOCKED.name ? LOCKED : post(args.name);
		if (method === SPA + "get_results") return { name: args.name, title: `Post ${args.name}`, status: "Published", jobs: [] };
		if (method === SPA + "save_post") return { name: "SPOST-NEW" };
		if (method === SPA + "delete_post") return {};
		if (method === ACTIONS + "submit_for_approval") return {};
		throw new Error(`no fake reply for ${method}`);
	};
	const fetch = async (url, init) => {
		const method = String(url).replace("/api/method/", "");
		const args = JSON.parse((init && init.body) || "{}");
		seen.push(method);
		if (held[method]) {
			const gate = held[method];
			delete held[method];
			await gate.promise;
		}
		const message = answer(method, args);
		return { ok: true, status: 200, json: async () => ({ message }) };
	};
	return {
		fetch,
		seen,
		hold(method) {
			let release;
			const promise = new Promise((resolve) => (release = resolve));
			held[method] = { promise };
			return { release };
		},
	};
}

// ------------------------------------------------------------------ driving the page

async function boot(M, options) {
	const b = fakeBrowser(options);
	const doc = fakeDocument();
	const server = fakeServer(options);
	globalThis.window = b.win;
	globalThis.document = doc;
	globalThis.fetch = server.fetch;
	const root = doc.createElement("div");
	doc.body.appendChild(root);
	const app = new M.MarketingApp(root, {});
	await app.mount();
	await flush();
	return { b, doc, app, server };
}

function find(scope, tag, text) {
	for (const node of scope.walk()) if (node.tagName === tag && node.textContent === text) return node;
	return null;
}

/** Click a link or button the way a person does: a plain left click. */
function tap(node) {
	if (!node) throw new Error("nothing to tap");
	node.dispatch("click", { button: 0 });
}

/** What the pane shows: its toolbar title, or a placeholder's. */
function screen(pg) {
	for (const node of pg.app.pane.walk()) {
		if (node.classList.contains("ee-mk-toolbar-title") || node.classList.contains("ee-mk-placeholder-title")) {
			return node.textContent;
		}
	}
	return "";
}

function titleBox(pg) {
	for (const node of pg.app.pane.walk()) {
		if (node.tagName === "INPUT" && node.placeholder === "Only for finding it here; no network shows it") return node;
	}
	return null;
}

function openDialogs(pg) {
	return pg.doc.querySelectorAll("dialog.ee-mk-dialog").filter((d) => d.open);
}

function dialogTitle(dialog) {
	for (const node of dialog.walk()) if (node.classList.contains("ee-mk-dialog-title")) return node.textContent;
	return "";
}

function dialogButton(pg, label) {
	const [dialog] = openDialogs(pg);
	return dialog ? find(dialog, "BUTTON", label) : null;
}

const tab = (pg, label) => find(pg.app.nav, "A", label);

(async () => {
	let M;
	try {
		M = await import(pathToFileURL(path.join(DIR, "app.js")).href);
	} catch (err) {
		console.error("COULD NOT LOAD marketing/app.js — its modules must stay importable without a DOM.");
		console.error(err && err.message);
		process.exit(2);
	}
	if (typeof M.MarketingApp !== "function") {
		console.error("MARKER NOT FOUND: marketing/app.js no longer exports MarketingApp");
		process.exit(2);
	}

	console.log("marketing — Back and Forward\n");

	console.log("the page's own start adds no entry");
	{
		const pg = await boot(M);
		check("one call: the entry it opened on is re-stamped, with two arguments", pg.b.calls.map((c) => [c.call, c.args]), [["replace", 2]]);
		check("and it carries this page's place", pg.b.win.history.state, { ee_mk: 0 });
		check("the calendar is on screen", screen(pg), "September 2026");
	}
	{
		const pg = await boot(M, { entries: [{ state: { ee_mk: 0 }, url: "/marketing" }, { state: { ee_mk: 1 }, url: "/marketing/queue" }] });
		check("a reload onto an entry of its own writes nothing and keeps its place", [pg.b.calls.length, pg.app.idx, screen(pg)], [0, 1, "Approval queue"]);
	}

	console.log("\nBack and Forward walk the screens");
	{
		const pg = await boot(M);
		tap(tab(pg, "Approval queue"));
		await flush();
		tap(tab(pg, "New post"));
		await flush();
		const pushes = pg.b.calls.filter((c) => c.call === "push");
		check("each tap pushes one entry, with its address", pushes.map((c) => [c.url, c.state.ee_mk]), [["/marketing/queue", 1], ["/marketing/new", 2]]);
		check("the composer is on screen", screen(pg), "New post");
		const before = pg.b.calls.length;
		pg.b.userBack();
		await flush();
		check("Back: the queue", [pg.b.win.location.pathname, screen(pg)], ["/marketing/queue", "Approval queue"]);
		pg.b.userBack();
		await flush();
		check("Back: the calendar", [pg.b.win.location.pathname, screen(pg)], ["/marketing", "September 2026"]);
		pg.b.userForward();
		await flush();
		check("Forward: the queue again", screen(pg), "Approval queue");
		pg.b.userForward();
		await flush();
		check("Forward: the composer again", screen(pg), "New post");
		check("walking writes nothing to the history", pg.b.calls.slice(before), []);
	}

	console.log("\na link to where the page already is adds no entry");
	{
		const pg = await boot(M);
		tap(tab(pg, "Calendar"));
		await flush();
		tap(find(pg.app.pane, "A", "Today"));
		await flush();
		check("no push; the entry is re-stamped in place", pg.b.calls.slice(1).map((c) => [c.call, c.url]), [["replace", "/marketing"], ["replace", "/marketing"]]);
		check("still one entry", pg.b.entries.length, 1);
	}
	{
		// The same screen under another address: /marketing/calendar/<this month> is /marketing.
		const pg = await boot(M);
		tap(find(pg.app.pane, "A", "›"));
		await flush();
		tap(find(pg.app.pane, "A", "‹"));
		await flush();
		check("paged a month and back: September, at its own address", [screen(pg), pg.b.win.location.pathname], ["September 2026", "/marketing/calendar/2026-09"]);
		tap(find(pg.app.pane, "A", "Today"));
		await flush();
		const last = pg.b.calls[pg.b.calls.length - 1];
		check("Today on this month re-stamps, whatever the address says", [last.call, last.url], ["replace", "/marketing"]);
		check("no second September behind it", pg.b.entries.map((e) => e.url), ["/marketing", "/marketing/calendar/2026-10", "/marketing"]);
		pg.b.userBack();
		await flush();
		check("so the next Back shows another screen: October", screen(pg), "October 2026");
	}
	{
		const pg = await boot(M);
		tap(find(pg.app.pane, "A", "Month"));
		await flush();
		check("the lit Month switch on /marketing re-stamps too", [pg.b.count("push"), pg.b.entries.length], [0, 1]);
	}
	{
		// A week is the same week from any of its days, and /marketing/week is this one.
		const pg = await boot(M);
		pg.app.navigate("/marketing/week");
		await flush();
		const week = screen(pg);
		check("this week, at /marketing/week", [week, pg.b.count("push")], ["Week of Sun, Sep 20", 1]);
		tap(find(pg.app.pane, "A", "Today"));
		await flush();
		tap(find(pg.app.pane, "A", "Week"));
		await flush();
		check("Today and the lit Week switch re-stamp, whatever day the address names", [pg.b.count("push"), pg.b.entries.length, screen(pg)], [1, 2, week]);
		pg.b.userBack();
		await flush();
		check("one Back: the month", screen(pg), "September 2026");
		tap(find(pg.app.pane, "A", "›"));
		await flush();
		check("a different month still pushes", [pg.b.count("push"), screen(pg)], [2, "October 2026"]);
	}

	console.log("\nBack from a composer with changes asks, and Stay keeps everything");
	{
		const pg = await boot(M);
		tap(tab(pg, "Approval queue"));
		await flush();
		tap(tab(pg, "New post"));
		await flush();
		const box = titleBox(pg);
		box.value = "Half a caption";
		box.dispatch("input");
		pg.b.userBack();
		check("asked once", openDialogs(pg).map(dialogTitle), ["Leave without saving?"]);
		check("the step is put back: one go(+1)", pg.b.calls.filter((c) => c.call === "go").map((c) => c.delta), [1]);
		await flush();
		check("back on the composer's entry and address", [pg.b.index(), pg.b.win.location.pathname], [2, "/marketing/new"]);
		check("the same form, still holding what was typed", [titleBox(pg) === box, box.value, box.isConnected], [true, "Half a caption", true]);
		tap(dialogButton(pg, "Stay"));
		await flush();
		check("Stay: no dialog, the composer and its text", [openDialogs(pg).length, screen(pg), box.value], [0, "New post", "Half a caption"]);
		pg.b.userBack();
		await flush();
		check("the next Back asks again, one dialog at a time", openDialogs(pg).length, 1);
		tap(dialogButton(pg, "Discard changes"));
		await flush();
		check("Discard: the step is taken again, to the queue", [pg.b.index(), pg.b.win.location.pathname, screen(pg)], [1, "/marketing/queue", "Approval queue"]);
		check("no dialog, no guard left behind", [openDialogs(pg).length, pg.app.leaveGuard], [0, null]);
		check("and nothing was pushed along the way", pg.b.count("push"), 2);
	}

	console.log("\nForward from a composer with changes asks too");
	{
		const pg = await boot(M);
		tap(tab(pg, "New post"));
		await flush();
		tap(tab(pg, "Approval queue"));
		await flush();
		pg.b.userBack();
		await flush();
		const box = titleBox(pg);
		box.value = "Typed after coming back";
		box.dispatch("input");
		pg.b.userForward();
		check("asked", openDialogs(pg).length, 1);
		await flush();
		check("put back with go(-1): the composer, its text", [pg.b.calls.filter((c) => c.call === "go").map((c) => c.delta), pg.b.index(), box.value], [[-1], 1, "Typed after coming back"]);
		tap(dialogButton(pg, "Discard changes"));
		await flush();
		check("Discard: Forward is taken, to the queue", [pg.b.index(), screen(pg)], [2, "Approval queue"]);
	}

	console.log("\na composer with nothing typed leaves without asking");
	{
		const pg = await boot(M);
		tap(tab(pg, "New post"));
		await flush();
		pg.b.userBack();
		await flush();
		check("no dialog, no go(); the calendar", [openDialogs(pg).length, pg.b.count("go"), screen(pg)], [0, 0, "September 2026"]);
	}

	console.log("\na reply that lands after Back draws nothing");
	{
		const pg = await boot(M);
		const gate = pg.server.hold(SPA + "get_post");
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		check("the post is loading", screen(pg), "Loading the post…");
		pg.b.userBack();
		await flush();
		check("Back drew the calendar", screen(pg), "September 2026");
		gate.release();
		await flush();
		check("the post, landing late, did not paint over it", screen(pg), "September 2026");
		check("and armed no leave guard for it", pg.app.leaveGuard, null);
	}
	{
		const pg = await boot(M);
		const gate = pg.server.hold(SPA + "get_results");
		pg.app.navigate("/marketing/post/SPOST-1/results");
		await flush();
		pg.b.userBack();
		await flush();
		gate.release();
		await flush();
		check("results landing late do not paint over the calendar", screen(pg), "September 2026");
	}

	console.log("\na Send back that lands after Back does not paint the queue over the next screen");
	{
		const loads = (pg) => pg.server.seen.filter((m) => m === SPA + "approval_queue").length;
		const sendBack = async (pg) => {
			tap(tab(pg, "Approval queue"));
			await flush();
			tap(find(pg.app.pane, "BUTTON", "Send back"));
			if (openDialogs(pg).map(dialogTitle)[0] !== "Send “Waiting for a manager” back to Draft?") throw new Error("Send back did not ask");
			const gate = pg.server.hold(ACTIONS + "send_back");
			tap(dialogButton(pg, "Send back"));
			await flush();
			return gate;
		};
		{
			const pg = await boot(M, { queue: [QUEUED] });
			const gate = await sendBack(pg);
			pg.b.userBack();
			await flush();
			check("Back while it was sent: the dialog is gone, the calendar shows", [openDialogs(pg).length, screen(pg), pg.b.win.location.pathname], [0, "September 2026", "/marketing"]);
			const before = loads(pg);
			gate.release();
			await flush();
			check("the reply lands: still the calendar, at its own address", [screen(pg), pg.b.win.location.pathname], ["September 2026", "/marketing"]);
			check("and the queue was not even fetched to be drawn over it", loads(pg) - before, 0);
			check("it says what it did", pg.app.notice.textContent.startsWith("Sent back to Draft."), true);
		}
		{
			const pg = await boot(M, { queue: [QUEUED] });
			const gate = await sendBack(pg);
			pg.b.userBack();
			await flush();
			pg.b.userForward();
			await flush();
			check("Back, then Forward onto the queue before the reply", screen(pg), "Approval queue");
			const before = loads(pg);
			gate.release();
			await flush();
			check("the reply lands: the queue is drawn again, after the send back", [screen(pg), loads(pg) - before], ["Approval queue", 1]);
		}
		{
			const pg = await boot(M, { queue: [QUEUED] });
			const gate = await sendBack(pg);
			const before = loads(pg);
			gate.release();
			await flush();
			check("never moved: the queue is drawn again", [screen(pg), loads(pg) - before, pg.b.win.location.pathname], ["Approval queue", 1, "/marketing/queue"]);
		}
	}

	console.log("\nBack closes a dialog the screen opened");
	{
		const pg = await boot(M);
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		tap(find(pg.app.pane, "BUTTON", "Delete"));
		check("Delete asks", openDialogs(pg).map(dialogTitle), ["Delete this post?"]);
		pg.b.userBack();
		await flush();
		check("Back: the dialog is closed and gone, the calendar shows", [openDialogs(pg).length, pg.doc.querySelectorAll("dialog").length, screen(pg)], [0, 0, "September 2026"]);
	}

	console.log("\nDelete returns to the calendar the post was opened from");
	{
		const pg = await boot(M);
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		check("the post's entry records the address underneath", pg.b.win.history.state, { ee_mk: 1, from: "/marketing" });
		tap(find(pg.app.pane, "BUTTON", "Delete"));
		const drawn = pg.server.seen.length;
		tap(dialogButton(pg, "Delete"));
		await flush();
		check(
			"replaced (never pushed) with the calendar, then one step back onto the calendar's own entry",
			pg.b.calls.slice(-2).map((c) => [c.call, c.url || c.delta]),
			[["replace", "/marketing"], ["go", -1]]
		);
		check("on the entry the page opened on, showing the calendar", [pg.b.index(), pg.app.idx, screen(pg)], [0, 0, "September 2026"]);
		check("the dead post's address is nowhere in the history", pg.b.entries.map((e) => e.url), ["/marketing", "/marketing"]);
		check(
			"the calendar is drawn once: landing on its own entry redraws nothing",
			pg.server.seen.slice(drawn).filter((m) => m === SPA + "get_calendar").length,
			1
		);
		check("and it still says what it did", pg.app.notice.textContent.startsWith("Deleted."), true);
		check("so the next Back leaves the page, not a second press on the same screen", pg.b.userBack(), false);
		pg.b.userForward();
		await flush();
		check("Forward onto the replaced entry is that same calendar, never the post", [pg.b.index(), screen(pg), pg.b.win.location.pathname], [1, "September 2026", "/marketing"]);
		tap(tab(pg, "Approval queue"));
		await flush();
		pg.b.userBack();
		await flush();
		check("and it holds its place in the count: Back from the next screen", [pg.b.index(), screen(pg)], [1, "September 2026"]);
	}
	{
		// Opened from this month at its long address: the step back lands on that address.
		const pg = await boot(M);
		tap(find(pg.app.pane, "A", "›"));
		await flush();
		tap(find(pg.app.pane, "A", "‹"));
		await flush();
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		tap(find(pg.app.pane, "BUTTON", "Delete"));
		tap(dialogButton(pg, "Delete"));
		await flush();
		check(
			"back on September's own entry, at its own address",
			[pg.b.index(), pg.b.win.location.pathname, screen(pg)],
			[2, "/marketing/calendar/2026-09", "September 2026"]
		);
		pg.b.userBack();
		await flush();
		check("the next Back: October", screen(pg), "October 2026");
	}
	{
		// Opened from another screen: replaced with this month, and Back returns to that screen.
		const pg = await boot(M);
		tap(tab(pg, "Approval queue"));
		await flush();
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		tap(find(pg.app.pane, "BUTTON", "Delete"));
		tap(dialogButton(pg, "Delete"));
		await flush();
		check("opened from the queue: replaced, no step back", [pg.b.count("go"), pg.b.entries.map((e) => e.url)], [0, ["/marketing", "/marketing/queue", "/marketing"]]);
		check("the calendar shows", screen(pg), "September 2026");
		pg.b.userBack();
		await flush();
		check("Back: the queue", screen(pg), "Approval queue");
	}
	{
		const pg = await boot(M);
		tap(find(pg.app.pane, "A", "›"));
		await flush();
		pg.app.navigate("/marketing/post/SPOST-1");
		await flush();
		tap(find(pg.app.pane, "BUTTON", "Delete"));
		tap(dialogButton(pg, "Delete"));
		await flush();
		check("opened from October: replaced with this month, no step back", [pg.b.count("go"), screen(pg)], [0, "September 2026"]);
		pg.b.userBack();
		await flush();
		check("Back: October", screen(pg), "October 2026");
	}

	console.log("\na save that lands after Discard does not pull the page back");
	{
		const pg = await boot(M);
		tap(tab(pg, "Approval queue"));
		await flush();
		tap(tab(pg, "New post"));
		await flush();
		const box = titleBox(pg);
		box.value = "Saved in the background";
		box.dispatch("input");
		const gate = pg.server.hold(SPA + "save_post");
		tap(find(pg.app.pane, "BUTTON", "Save draft"));
		await flush();
		pg.b.userBack();
		await flush();
		tap(dialogButton(pg, "Discard changes"));
		await flush();
		check("on the queue", screen(pg), "Approval queue");
		const before = pg.b.calls.length;
		gate.release();
		await flush();
		check("the save lands: no history call, still the queue", [pg.b.calls.slice(before), screen(pg), pg.b.win.location.pathname], [[], "Approval queue", "/marketing/queue"]);
		check("and it says so", pg.app.notice.textContent.startsWith("Saved."), true);
	}

	console.log("\na copy comes back on Forward, from its own entry");
	{
		const pg = await boot(M);
		pg.app.navigate("/marketing/post/SPOST-LOCKED");
		await flush();
		tap(find(pg.app.pane, "BUTTON", "Copy into a new draft"));
		await flush();
		const push = pg.b.calls[pg.b.calls.length - 1];
		check("pushed with the copy in its state", [push.call, push.url, push.state.seed && push.state.seed.title], ["push", "/marketing/new", "The approved one"]);
		check("the composer starts from the copy", titleBox(pg).value, "The approved one");
		pg.b.userBack();
		await flush();
		tap(dialogButton(pg, "Discard changes"));
		await flush();
		check("Back, discarded: the approved post", screen(pg), "The approved one");
		pg.b.userForward();
		await flush();
		check("Forward: the copy again, not an empty form", [screen(pg), titleBox(pg) && titleBox(pg).value], ["New post", "The approved one"]);
		check("the entry still holds it for the next time", pg.b.win.history.state.seed.title, "The approved one");
	}

	console.log("\nsource rules");
	{
		const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
		const writers = [];
		for (const file of fs.readdirSync(DIR).filter((f) => f.endsWith(".js"))) {
			const text = strip(fs.readFileSync(path.join(DIR, file), "utf8"));
			if (/\b(pushState|replaceState)\s*\(|history\.go\s*\(/.test(text)) writers.push(file);
		}
		check("only app.js writes the history", writers, ["app.js"]);
		const app = strip(fs.readFileSync(path.join(DIR, "app.js"), "utf8"));
		check("the beforeunload guard is still there (leaving the document)", /addEventListener\("beforeunload"/.test(app), true);
		check("and no new one was added", (app.match(/beforeunload/g) || []).length, 1);
	}

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("marketing history: all assertions passed");
})();
