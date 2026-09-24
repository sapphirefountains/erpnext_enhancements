#!/usr/bin/env node
/**
 * Back and Forward on /feedback, driven through the real app on a fake DOM and a fake history,
 * and with the real "Report a problem" panel open over it.
 *
 * The rules pinned here are the product owner's, and each one is a way this page broke:
 *
 *   - **Back returns to the previous screen and Forward restores it.** Every tap that changes the
 *     screen adds one entry; the page's own start re-stamps the entry it opened on (the landing
 *     redirect included), so Back from the first screen leaves the page. A tab that is already
 *     lit adds nothing, or the next Back would appear to do nothing.
 *   - **A half-written request is never silently lost.** The New form keeps what was typed across
 *     Back, Forward and the tabs, and a second tap on its tab leaves it alone. It outlives leaving
 *     the page too: it is mirrored to this tab's sessionStorage under the user (fields and file
 *     names only), so Back off a first-entry /feedback/new and Forward bring it back.
 *   - **A late reply never draws over the screen the person went to**: a request that finishes
 *     loading after Back, or a submit that finishes after they left the form. And an "Expand
 *     with AI" that lands after the form was drawn again never replaces what was typed since.
 *   - **The report panel owns Back while it is open.** The page does nothing on a popstate while
 *     `ee_capture.isOpen()` is true, so answering "Discard this report?" does not clear the
 *     request under it; an entry the panel left behind keeps the screen and is re-stamped. Nor
 *     does it push over the panel's entry: a submit that lands under the panel says so in place.
 *     `scripts/test_capture_panel.js` pins the panel's half of the same contract.
 *
 * The history behaves as a browser's does where it matters: `back()`/`forward()` are queued and
 * land on a later task, the person's own Back and Forward land at once, pushState drops the
 * forward entries, and every popstate listener gets the same event. Replies come from a stubbed
 * fetch, and any of them can be held open to land late.
 *
 * Run: node scripts/test_feedback_history.js
 */

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const JS = path.join(__dirname, "..", "erpnext_enhancements", "public", "js");
const APP = path.join(JS, "feedback", "app.js");
const CAPTURE = path.join(JS, "capture");

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

/** Enough DOM for the real app and the real panel. Nothing is laid out or drawn. */
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
	append(...nodes) {
		for (const node of nodes) {
			if (typeof node === "string") {
				const text = new FakeElement(this.ownerDocument, "#text");
				text.text = node;
				this.appendChild(text);
			} else {
				this.appendChild(node);
			}
		}
	}
	appendChild(node) {
		if (node.parentNode) node.parentNode.removeChild(node);
		node.parentNode = this;
		this.childNodes.push(node);
		return node;
	}
	insertBefore(node, ref) {
		if (node.parentNode) node.parentNode.removeChild(node);
		node.parentNode = this;
		const i = this.childNodes.indexOf(ref);
		if (i === -1) this.childNodes.push(node);
		else this.childNodes.splice(i, 0, node);
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
	contains(other) {
		for (let node = other; node; node = node.parentNode) if (node === this) return true;
		return false;
	}
	closest() {
		return null;
	}
	querySelectorAll() {
		return [];
	}
	getClientRects() {
		return [{}];
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
	click() {}
	*walk() {
		for (const node of this.childNodes) {
			yield node;
			yield* node.walk();
		}
	}
}

function fakeDocument() {
	const doc = { title: "Feedback", referrer: "", listeners: {} };
	doc.createElement = (tag) => new FakeElement(doc, tag);
	doc.createTextNode = (text) => {
		const node = new FakeElement(doc, "#text");
		node.text = String(text);
		return node;
	};
	doc.documentElement = new FakeElement(doc, "html");
	doc.head = new FakeElement(doc, "head");
	doc.body = new FakeElement(doc, "body");
	doc.documentElement.appendChild(doc.head);
	doc.documentElement.appendChild(doc.body);
	doc.activeElement = doc.body;
	doc.getElementById = (id) => {
		for (const node of doc.documentElement.walk()) if (node.id === id) return node;
		return null;
	};
	doc.addEventListener = FakeElement.prototype.addEventListener;
	doc.removeEventListener = FakeElement.prototype.removeEventListener;
	doc.dispatch = (type, extra) => {
		const ev = { type, target: doc.body, preventDefault() {}, stopPropagation() {}, ...(extra || {}) };
		for (const fn of (doc.listeners[type] || []).slice()) fn(ev);
	};
	return doc;
}

// ------------------------------------------------------------------ a fake browser history

/**
 * pushState drops the forward entries and moves the address; back/forward are queued and land on
 * a later task; the person's own Back and Forward land at once; every popstate listener gets the
 * SAME event, and one removed mid-dispatch is not called. `calls` records each call and its
 * argument count, so a URL argument shows.
 */
function fakeBrowser(options) {
	const opts = options || {};
	const listeners = {};
	const entries = (opts.entries || [{ state: null, url: opts.url || "/feedback/new" }]).map((e) => ({ ...e }));
	let index = entries.length - 1;
	const calls = [];
	const clone = (value) => (value === undefined || value === null ? null : structuredClone(value));
	const traverse = (delta) => {
		const target = index + delta;
		if (target < 0 || target >= entries.length) return false;
		index = target;
		const ev = { type: "popstate", state: clone(entries[index].state) };
		for (const fn of (listeners.popstate || []).slice()) {
			if ((listeners.popstate || []).includes(fn)) fn(ev);
		}
		return true;
	};
	const history = {
		get state() {
			return entries[index].state;
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
		back() {
			calls.push({ call: "back" });
			setImmediate(() => traverse(-1));
		},
		forward() {
			calls.push({ call: "forward" });
			setImmediate(() => traverse(1));
		},
	};
	const location = {
		origin: "https://erp.example.com",
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
		innerHeight: 800,
		performance: { now: () => 0 },
		console: { error() {} },
		confirm: () => true,
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

const API = "erpnext_enhancements.api.feedback.";

function bootstrap(reviewer) {
	return {
		is_reviewer: !!reviewer,
		paused: false,
		ai_drafting: false,
		request_types: ["Feature", "Bug"],
		impacts: ["Blocking my work", "Painful but I can work around it", "Nice to have"],
		my_requests: [
			{ name: "ER-2026-00001", title: "Stock page is slow", status: "Submitted", request_type: "Bug", impact: "Nice to have", creation: "2026-09-20 10:00:00" },
			{ name: "ER-2026-00002", title: "Export the queue", status: "Submitted", request_type: "Feature", impact: "Nice to have", creation: "2026-09-21 10:00:00" },
		],
		review_queue: [],
		full_name: "Sam Staff",
	};
}

function detail(name) {
	return {
		name,
		title: `Detail of ${name}`,
		status: "Submitted",
		request_type: "Bug",
		impact: "Nice to have",
		requester_name: "Sam Staff",
		creation: "2026-09-20 10:00:00",
		description: "",
		context: {},
		attachments: [],
		duplicate_candidates: [],
		created_tasks: [],
	};
}

/**
 * Answers by method name. `hold(method)` keeps the next reply back until `release()`. `sent`
 * records each call's method and arguments.
 */
function fakeServer(options) {
	const opts = options || {};
	const held = {};
	const sent = [];
	const answer = (method, args) => {
		if (method === API + "get_bootstrap") return bootstrap(opts.reviewer);
		if (method === API + "get_request") return detail(args.name);
		if (method === API + "submit_request") return { name: "ER-2026-00099", rejected: [] };
		if (method === API + "draft_description") return { description: "Drafted by the AI." };
		throw new Error(`no fake reply for ${method}`);
	};
	return {
		sent,
		fetch: async (url, init) => {
			const method = String(url).replace("/api/method/", "");
			const args = JSON.parse((init && init.body) || "{}");
			sent.push({ method, args });
			if (held[method]) {
				const gate = held[method];
				delete held[method];
				await gate;
			}
			return { ok: true, status: 200, json: async () => ({ message: answer(method, args) }) };
		},
		hold(method) {
			let release;
			held[method] = new Promise((resolve) => (release = resolve));
			return { release };
		},
	};
}

/**
 * A tab's sessionStorage. Pass the same one to a second `boot` for the page loaded again in that
 * tab (Back off the page, then Forward); `"blocked"` is a private window whose storage throws.
 */
function fakeStorage() {
	const data = new Map();
	return {
		data,
		getItem: (key) => (data.has(key) ? data.get(key) : null),
		setItem: (key, value) => data.set(key, String(value)),
		removeItem: (key) => data.delete(key),
	};
}

// ------------------------------------------------------------------ driving the page

async function boot(F, options) {
	const opts = options || {};
	const b = fakeBrowser(opts);
	const doc = fakeDocument();
	const server = fakeServer(opts);
	globalThis.window = b.win;
	globalThis.document = doc;
	globalThis.fetch = server.fetch;
	if (opts.storage === "blocked") {
		Object.defineProperty(b.win, "sessionStorage", {
			get() {
				throw new Error("SecurityError: storage is disabled");
			},
		});
	} else {
		b.win.sessionStorage = opts.storage || fakeStorage();
	}
	b.win.EE_CAPTURE = { surface: "web" };
	b.win.EE_FEEDBACK_BOOT = { user: opts.user || "sam@example.com" };
	if (opts.capture) opts.capture(b.win);
	const root = doc.createElement("div");
	doc.body.appendChild(root);
	const app = new F.FeedbackApp(root, b.win.EE_FEEDBACK_BOOT);
	await app.mount();
	await flush();
	return { b, doc, app, server };
}

function find(scope, tag, text) {
	for (const node of scope.walk()) if (node.tagName === tag && node.textContent === text) return node;
	return null;
}

function findInput(scope, placeholder) {
	for (const node of scope.walk()) if (node.placeholder === placeholder) return node;
	return null;
}

function tap(node) {
	if (!node) throw new Error("nothing to tap");
	node.dispatch("click", { button: 0 });
}

const TITLE = "One line: what is wrong, or what you want";
const DESC = "What happened, and what you expected instead.";
const DRAFT_KEY = "ee_fb_new_draft:sam@example.com";
/** Type into a box the way a person does: the value changes, then `input` fires. */
function typeInto(node, text) {
	node.value = text;
	node.dispatch("input");
}
const tab = (pg, label) => {
	for (const node of pg.app.nav.walk()) if (node.tagName === "A" && node.textContent.startsWith(label)) return node;
	return null;
};
/** What the pane shows: the form, a list, a request's title, or a notice. */
function screen(pg) {
	const pane = pg.app.pane;
	for (const node of pane.walk()) {
		if (node.classList.contains("ee-fb-detail-title")) return `request: ${node.textContent}`;
		if (node.classList.contains("ee-fb-list")) return "list";
		if (node.classList.contains("ee-fb-form")) return "form";
		if (node.classList.contains("ee-fb-loading")) return "loading";
	}
	return pane.textContent;
}

(async () => {
	let F;
	try {
		F = await import(pathToFileURL(APP).href);
	} catch (err) {
		console.error("COULD NOT LOAD feedback/app.js — its modules must stay importable without a DOM.");
		console.error(err && err.message);
		process.exit(2);
	}
	if (typeof F.FeedbackApp !== "function") {
		console.error("MARKER NOT FOUND: feedback/app.js no longer exports FeedbackApp");
		process.exit(2);
	}

	console.log("feedback — Back and Forward\n");

	console.log("the page's own start adds no entry");
	{
		const pg = await boot(F);
		check("the entry it opened on is re-stamped with two arguments", pg.b.calls.map((c) => [c.call, c.args, c.state]), [["replace", 2, { ee_fb: 1 }]]);
		check("the form is on screen", screen(pg), "form");
	}
	{
		const pg = await boot(F, { url: "/feedback", reviewer: true });
		check("the bare landing is corrected in place, never pushed", pg.b.calls.map((c) => [c.call, c.url]), [["replace", "/feedback/review"]]);
		check("one entry", pg.b.entries.length, 1);
	}

	console.log("\nBack and Forward walk the screens; the lit tab adds nothing");
	{
		const pg = await boot(F);
		tap(tab(pg, "My requests"));
		tap(find(pg.app.pane, "A", "Stock page is slow"));
		await flush();
		check("two taps, two pushes with their addresses", pg.b.calls.filter((c) => c.call === "push").map((c) => c.url), ["/feedback/mine", "/feedback/request/ER-2026-00001"]);
		check("the request", screen(pg), "request: Detail of ER-2026-00001");
		const before = pg.b.calls.length;
		pg.b.userBack();
		check("Back: the list", screen(pg), "list");
		pg.b.userBack();
		check("Back: the form", screen(pg), "form");
		pg.b.userForward();
		pg.b.userForward();
		await flush();
		check("Forward twice: the request again", screen(pg), "request: Detail of ER-2026-00001");
		check("walking writes nothing", pg.b.calls.slice(before), []);
		pg.b.userBack();
		tap(tab(pg, "My requests"));
		check("the lit tab: no push", [pg.b.count("push"), pg.b.entries.length], [2, 3]);
	}

	console.log("\na half-written request survives Back, Forward and the tabs");
	{
		const pg = await boot(F);
		const box = findInput(pg.app.pane, TITLE);
		box.value = "The printer icon does nothing";
		box.dispatch("input");
		tap(tab(pg, "New request"));
		check("a second tap on the lit New tab leaves the same form alone", [findInput(pg.app.pane, TITLE) === box, pg.b.count("push")], [true, 0]);
		tap(tab(pg, "My requests"));
		check("on the list", screen(pg), "list");
		pg.b.userBack();
		check("Back: the form, with what was typed", findInput(pg.app.pane, TITLE).value, "The printer icon does nothing");
		pg.b.userForward();
		pg.b.userBack();
		check("Forward and Back again: still there", findInput(pg.app.pane, TITLE).value, "The printer icon does nothing");
	}

	console.log("\na reply that lands after Back draws nothing");
	{
		const pg = await boot(F);
		tap(tab(pg, "My requests"));
		const gate = pg.server.hold(API + "get_request");
		tap(find(pg.app.pane, "A", "Stock page is slow"));
		check("the request is loading", screen(pg), "loading");
		pg.b.userBack();
		check("Back drew the list", screen(pg), "list");
		gate.release();
		await flush();
		check("the request, landing late, did not paint over it", screen(pg), "list");
	}
	{
		const pg = await boot(F);
		tap(tab(pg, "My requests"));
		const gate = pg.server.hold(API + "get_request");
		tap(find(pg.app.pane, "A", "Stock page is slow"));
		pg.b.userBack();
		tap(find(pg.app.pane, "A", "Export the queue"));
		await flush();
		gate.release();
		await flush();
		check("A, then Back, then B: B stays, A's late reply is dropped", screen(pg), "request: Detail of ER-2026-00002");
	}

	console.log("\na submit that lands after the person left the form");
	{
		const pg = await boot(F);
		findInput(pg.app.pane, TITLE).value = "Filed on the way out";
		const gate = pg.server.hold(API + "submit_request");
		tap(find(pg.app.pane, "BUTTON", "Submit"));
		tap(tab(pg, "My requests"));
		const pushes = pg.b.count("push");
		gate.release();
		await flush();
		check("no push to the request: the list stays", [pg.b.count("push") - pushes, screen(pg)], [0, "list"]);
		check("it says it was filed", pg.app.banner.textContent, "Filed as ER-2026-00099.");
		check("and the next form starts empty", pg.app.state.newDraft, null);
	}

	console.log("\nan \"Expand with AI\" that lands late never replaces newer typing");
	{
		// Asked, then away and back: the form is drawn again, and typed into, before the reply.
		const pg = await boot(F);
		typeInto(findInput(pg.app.pane, TITLE), "The printer icon does nothing");
		const gate = pg.server.hold(API + "draft_description");
		tap(find(pg.app.pane, "BUTTON", "Expand with AI"));
		await flush();
		tap(tab(pg, "My requests"));
		tap(tab(pg, "New request"));
		const desc = findInput(pg.app.pane, DESC);
		typeInto(desc, "Typed by hand after coming back");
		gate.release();
		await flush();
		check("the reply lands: the box and the draft keep what was typed", [desc.value, pg.app.state.newDraft.description], ["Typed by hand after coming back", "Typed by hand after coming back"]);
		tap(tab(pg, "My requests"));
		pg.b.userBack();
		check("away and Back: the form is drawn with the typing, not the AI's text", findInput(pg.app.pane, DESC).value, "Typed by hand after coming back");
		check("and the mirror agrees", JSON.parse(pg.b.win.sessionStorage.getItem(DRAFT_KEY)).description, "Typed by hand after coming back");
	}
	{
		// Asked, then away: nothing typed since, so the reply is kept for the form's return.
		const pg = await boot(F);
		typeInto(findInput(pg.app.pane, TITLE), "The printer icon does nothing");
		const gate = pg.server.hold(API + "draft_description");
		tap(find(pg.app.pane, "BUTTON", "Expand with AI"));
		await flush();
		tap(tab(pg, "My requests"));
		gate.release();
		await flush();
		check("landed on the list: drew nothing, said nothing", [screen(pg), pg.app.banner.hidden], ["list", true]);
		pg.b.userBack();
		check("Back: the form shows the drafted description", findInput(pg.app.pane, DESC).value, "Drafted by the AI.");
	}
	{
		// Asked, away and back, nothing typed: the form on screen shows it, not only the draft.
		const pg = await boot(F);
		typeInto(findInput(pg.app.pane, TITLE), "The printer icon does nothing");
		const gate = pg.server.hold(API + "draft_description");
		tap(find(pg.app.pane, "BUTTON", "Expand with AI"));
		await flush();
		tap(tab(pg, "My requests"));
		pg.b.userBack();
		const desc = findInput(pg.app.pane, DESC);
		gate.release();
		await flush();
		check("the form drawn again shows it, and so does the draft", [desc.value, pg.app.state.newDraft.description], ["Drafted by the AI.", "Drafted by the AI."]);
		check("and says where it came from", pg.app.banner.textContent.startsWith("Drafted from your title."), true);
	}
	{
		const pg = await boot(F);
		typeInto(findInput(pg.app.pane, TITLE), "The printer icon does nothing");
		const desc = findInput(pg.app.pane, DESC);
		typeInto(desc, "It is greyed out");
		tap(find(pg.app.pane, "BUTTON", "Expand with AI"));
		await flush();
		const asked = pg.server.sent.filter((c) => c.method === API + "draft_description").map((c) => c.args.description);
		check("on the form it was asked from: it fills the box, from what was there", [desc.value, pg.app.state.newDraft.description, asked], ["Drafted by the AI.", "Drafted by the AI.", ["It is greyed out"]]);
	}

	console.log("\na half-written request outlives leaving the page, in this tab only");
	{
		// /feedback/new is the tab's first entry: Back leaves the page, and Forward loads it again.
		const storage = fakeStorage();
		const first = await boot(F, { storage });
		typeInto(findInput(first.app.pane, TITLE), "The printer icon does nothing");
		typeInto(findInput(first.app.pane, DESC), "Nothing happens on click");
		check("Back from the first entry leaves the page", first.b.userBack(), false);
		const saved = JSON.parse(storage.getItem(DRAFT_KEY));
		check(
			"mirrored under this user, fields only",
			[Object.keys(saved), saved.title, saved.description],
			[["request_type", "title", "impact", "description", "steps", "attachments", "labels"], "The printer icon does nothing", "Nothing happens on click"]
		);
		const other = await boot(F, { storage, user: "lee@example.com" });
		check("somebody else signed in to the same tab gets a blank form", findInput(other.app.pane, TITLE).value, "");
		const again = await boot(F, { storage });
		check(
			"Forward loads the page again: the form has what was typed",
			[findInput(again.app.pane, TITLE).value, findInput(again.app.pane, DESC).value],
			["The printer icon does nothing", "Nothing happens on click"]
		);
		tap(find(again.app.pane, "BUTTON", "Submit"));
		await flush();
		check("filed: the mirror is dropped", storage.getItem(DRAFT_KEY), null);
		const after = await boot(F, { storage });
		check("so the next load starts empty", [findInput(after.app.pane, TITLE).value, findInput(after.app.pane, DESC).value], ["", ""]);
	}
	{
		// An upload keeps its name and label in the mirror, never its bytes.
		const storage = fakeStorage();
		const pg = await boot(F, { storage });
		const realXHR = globalThis.XMLHttpRequest;
		globalThis.XMLHttpRequest = class {
			constructor() {
				this.upload = {};
			}
			open() {}
			setRequestHeader() {}
			abort() {}
			send() {
				setImmediate(() => {
					this.status = 200;
					this.responseText = JSON.stringify({ message: { name: "FILE-0001", file_url: "/private/files/printer.png" } });
					this.onload();
				});
			}
		};
		try {
			let picker = null;
			for (const node of pg.app.pane.walk()) if (node.type === "file") picker = node;
			picker.files = [new File(["PNG-BYTES-NOT-FOR-STORAGE"], "printer.png", { type: "image/png" })];
			picker.dispatch("change");
			await flush();
		} finally {
			globalThis.XMLHttpRequest = realXHR;
		}
		const raw = storage.getItem(DRAFT_KEY);
		const saved = JSON.parse(raw);
		check("the upload's name and label are mirrored", [saved.attachments, saved.labels], [["FILE-0001"], { "FILE-0001": "printer.png" }]);
		check("and none of the file's contents", raw.includes("PNG-BYTES"), false);
		const again = await boot(F, { storage });
		const rows = [];
		for (const node of again.app.pane.walk()) if (node.classList.contains("ee-fb-attachment-ok")) rows.push(node.textContent);
		check("loaded again: the file is listed as ready", rows, ["printer.png — ready"]);
		typeInto(findInput(again.app.pane, TITLE), "The printer icon does nothing");
		tap(find(again.app.pane, "BUTTON", "Submit"));
		await flush();
		const submitted = again.server.sent.filter((c) => c.method === API + "submit_request").map((c) => c.args.attachments);
		check("and submit sends it", submitted, [["FILE-0001"]]);
	}
	{
		const storage = fakeStorage();
		storage.setItem(DRAFT_KEY, "{not json");
		const broken = await boot(F, { storage });
		check("a mirror that is not JSON: a blank form", [screen(broken), findInput(broken.app.pane, TITLE).value], ["form", ""]);
		storage.setItem(DRAFT_KEY, JSON.stringify({ request_type: "Rant", impact: "Whatever", title: 7, attachments: ["F-1", 3], labels: { "F-1": "a.png", "F-2": "b.png" } }));
		const odd = await boot(F, { storage });
		check(
			"one that is malformed: only what is well formed, and only choices still offered",
			[odd.app.state.newDraft.request_type, odd.app.state.newDraft.impact, odd.app.state.newDraft.title, odd.app.state.newDraft.attachments, odd.app.state.newDraft.labels],
			["Bug", "Painful but I can work around it", "", ["F-1"], { "F-1": "a.png" }]
		);
	}
	{
		const pg = await boot(F, { storage: "blocked" });
		check("storage that throws: the form still draws", screen(pg), "form");
		typeInto(findInput(pg.app.pane, TITLE), "Private window");
		tap(tab(pg, "My requests"));
		pg.b.userBack();
		check("and keeps what was typed in memory across Back", findInput(pg.app.pane, TITLE).value, "Private window");
	}

	console.log("\nwhile the report panel is open, Back is the panel's");
	{
		let open = true;
		const pg = await boot(F, { capture: (win) => (win.ee_capture = { isOpen: () => open }) });
		check("a page opened under the panel leaves its entry alone", pg.b.calls, []);
		open = false;
		const box = findInput(pg.app.pane, TITLE);
		box.value = "Kept under the panel";
		box.dispatch("input");
		tap(tab(pg, "My requests"));
		pg.b.userBack();
		const form = findInput(pg.app.pane, TITLE);
		open = true;
		pg.b.userForward();
		check("isOpen(): Forward redraws nothing", [findInput(pg.app.pane, TITLE) === form, screen(pg)], [true, "form"]);
	}
	{
		// Anything that navigates while the panel is open (a late reply, say) would push over its entry.
		let open = false;
		const pg = await boot(F, { capture: (win) => (win.ee_capture = { isOpen: () => open }) });
		open = true;
		pg.app.navigate("/feedback/mine");
		check("isOpen(): navigate pushes nothing and draws nothing", [pg.b.count("push"), screen(pg), pg.b.win.location.pathname], [0, "form", "/feedback/new"]);
		open = false;
		pg.app.navigate("/feedback/mine");
		check("closed: it moves", [pg.b.count("push"), screen(pg)], [1, "list"]);
	}

	console.log("\nwith the real report panel over the form");
	{
		const P = await import(pathToFileURL(path.join(CAPTURE, "panel.js")).href);
		const R = await import(pathToFileURL(path.join(CAPTURE, "recorder.js")).href);
		// A confirm() a person answered takes time; one the browser never showed returns at once.
		const realNow = Date.now;
		let skew = 0;
		Date.now = () => realNow() + skew;
		const asked = [];
		const answered = (answer) => (message) => {
			asked.push(message);
			skew += 1000;
			return answer;
		};
		try {
			const pg = await boot(F, {
				capture: (win) => {
					R.install(win);
					win.ee_capture_panel = { open: P.openPanel, isOpen: P.isPanelOpen };
				},
			});
			const box = findInput(pg.app.pane, TITLE);
			box.value = "Half a request";
			box.dispatch("input");
			await P.openPanel({ schema: 1, surface: "web", page: { path: "/feedback/new", title: "Feedback" } }, { surface: "web" });
			const push = pg.b.calls.filter((c) => c.call === "push");
			check("the panel takes its own entry on /feedback, with no URL", [push.length, push[0] && push[0].args, push[0] && Object.keys(push[0].state)], [1, 2, ["ee_capture"]]);
			findInput(pg.doc.body, "One line: what went wrong?").value = "Half a report";
			pg.b.win.confirm = answered(true);
			pg.b.userBack();
			check("Back asks the panel's question", asked, ["Discard this report?"]);
			check("the request under it is the same form, still holding its text", [findInput(pg.app.pane, TITLE) === box, box.isConnected, box.value], [true, true, "Half a request"]);
			check("the page is on its own entry", [pg.b.index(), pg.b.win.location.pathname, pg.b.win.history.state], [0, "/feedback/new", { ee_fb: 1 }]);
			await flush();
			check("the panel is closed and settled", P.isPanelOpen(), false);
			const before = pg.b.calls.length;
			pg.b.userForward();
			check("Forward onto the panel's dead entry: the same form", [findInput(pg.app.pane, TITLE) === box, box.value], [true, "Half a request"]);
			check("and the entry is re-stamped as this page's, with two arguments", pg.b.calls.slice(before).map((c) => [c.call, c.args, c.state]), [["replace", 2, { ee_fb: 1 }]]);
			pg.b.userBack();
			check("Back off it: the form, drawn again from what was typed", [screen(pg), findInput(pg.app.pane, TITLE).value], ["form", "Half a request"]);
			await flush();
		} finally {
			Date.now = realNow;
		}
	}

	console.log("\na submit that lands while the report panel is open");
	{
		const P = await import(pathToFileURL(path.join(CAPTURE, "panel.js")).href);
		const R = await import(pathToFileURL(path.join(CAPTURE, "recorder.js")).href);
		const realNow = Date.now;
		let skew = 0;
		Date.now = () => realNow() + skew;
		try {
			const pg = await boot(F, {
				capture: (win) => {
					R.install(win);
					win.ee_capture_panel = { open: P.openPanel, isOpen: P.isPanelOpen };
				},
			});
			typeInto(findInput(pg.app.pane, TITLE), "Filed while the panel was open");
			const gate = pg.server.hold(API + "submit_request");
			tap(find(pg.app.pane, "BUTTON", "Submit"));
			await flush();
			await P.openPanel({ schema: 1, surface: "web", page: { path: "/feedback/new", title: "Feedback" } }, { surface: "web" });
			check("the panel is open, on its own entry", [P.isPanelOpen(), pg.b.index(), Object.keys(pg.b.win.history.state)], [true, 1, ["ee_capture"]]);
			const focused = pg.doc.activeElement;
			const before = pg.b.calls.length;
			gate.release();
			await flush();
			check("the submit lands: no push over the panel's entry", pg.b.calls.slice(before).filter((c) => c.call === "push"), []);
			check("the panel's entry is still the one on top", [pg.b.entries.length, pg.b.index(), Object.keys(pg.b.win.history.state)], [2, 1, ["ee_capture"]]);
			check("it says it was filed, on the form under the panel", [pg.app.banner.textContent, screen(pg)], ["Filed as ER-2026-00099.", "form"]);
			check("the form is drawn again empty, so the same request cannot go twice", findInput(pg.app.pane, TITLE).value, "");
			check("without taking focus from the panel", pg.doc.activeElement === focused, true);
			check("and the mirror is dropped", pg.b.win.sessionStorage.getItem(DRAFT_KEY), null);
			pg.b.win.confirm = () => {
				skew += 1000;
				return true;
			};
			pg.b.userBack();
			await flush();
			check(
				"Back closes the panel: its own address and entry, the empty form",
				[P.isPanelOpen(), pg.b.win.location.pathname, pg.b.index(), screen(pg), findInput(pg.app.pane, TITLE).value],
				[false, "/feedback/new", 0, "form", ""]
			);
			check("and the next Back leaves the page", pg.b.userBack(), false);
		} finally {
			Date.now = realNow;
		}
	}

	console.log("\nsource rules");
	{
		const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
		const dir = path.join(JS, "feedback");
		const writers = fs.readdirSync(dir).filter((f) => f.endsWith(".js") && /\b(pushState|replaceState)\s*\(/.test(strip(fs.readFileSync(path.join(dir, f), "utf8"))));
		check("only app.js writes the history", writers, ["app.js"]);
		const app = strip(fs.readFileSync(APP, "utf8"));
		check("no beforeunload prompt", /beforeunload/.test(app), false);
		check("the panel's key is the one panel.js exports", /CAPTURE_KEY = "ee_capture"/.test(app), true);
		const P = await import(pathToFileURL(path.join(CAPTURE, "panel.js")).href);
		check("panel.js HISTORY_KEY", P.HISTORY_KEY, "ee_capture");
	}

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("feedback history: all assertions passed");
})();
