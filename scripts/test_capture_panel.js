#!/usr/bin/env node
/**
 * Guards what the capture panel sends (WI-079 slice 2) — the parts that are pure and can be
 * checked without a browser.
 *
 * The rules pinned here are the ones a quiet edit could break without anything looking wrong:
 *
 *   - **the query string never leaves a web or kiosk page.** Token pages carry secrets there
 *     (ADR 0016 §4). On the Desk it is kept, from the snapshot the person saw;
 *   - **the payload holds only fields the server accepts** (`SUBMIT_ALLOWED_FIELDS` in
 *     `api/feedback.py`) — anything else comes back in `rejected` and is a bug here — and
 *     never a key named `sid`, which makes Frappe fake a session-expired error;
 *   - **the snapshot is fitted under the server's 200 KB limit before it is shown**, so what
 *     the person reviews is what is sent;
 *   - **the error wording** the WI specifies for 429 and 403, and that a network failure is
 *     classified as "offline" (which saves a draft) rather than as an error (which loses the
 *     report);
 *   - **Back closes the panel on the web and the kiosk, and never costs the page a step.** The
 *     panel pushes one entry with no URL, and a Back asks "Discard this report?". Every other
 *     way of closing removes the entry once, and only while it is still on top. Pages with
 *     history of their own see `ee_capture.isOpen()` true for every popstate the panel causes.
 *     The Desk and /feedback get no entry.
 *
 * Imports `panel.js` for real, which also imports `transport.js`, `annotate.js` and
 * `drafts.js`: all four must stay importable without a DOM. If one grows a top-level browser
 * dependency this fails at import rather than silently asserting nothing. The Back rules are
 * checked by mounting the real panel on a small fake DOM and a fake history that behaves as a
 * browser's does: `back()` is queued and lands later, and every listener gets the same event.
 *
 * Run: node scripts/test_capture_panel.js
 */

const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

const CAPTURE = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "capture");
const TARGET = path.join(CAPTURE, "panel.js");
const FEEDBACK_API = path.join(__dirname, "..", "erpnext_enhancements", "api", "feedback.py");

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

function truthy(label, value) {
	check(label, !!value, true);
}

/** The server's allowlist, read from the source so the two cannot drift apart unnoticed. */
function serverAllowedFields() {
	const text = fs.readFileSync(FEEDBACK_API, "utf8");
	const match = text.match(/SUBMIT_ALLOWED_FIELDS\s*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
	if (!match) return null;
	return new Set(Array.from(match[1].matchAll(/"([a-z_]+)"/g), (m) => m[1]));
}

function keysDeep(value, out) {
	const keys = out || [];
	if (Array.isArray(value)) value.forEach((item) => keysDeep(item, keys));
	else if (value && typeof value === "object") {
		for (const key of Object.keys(value)) {
			keys.push(key);
			keysDeep(value[key], keys);
		}
	}
	return keys;
}

// ------------------------------------------------------------------ a fake DOM and history

/**
 * Just enough DOM to mount the real panel under node. Elements nest, carry attributes and
 * listeners, and know whether they are in the document. Nothing is laid out or drawn.
 */
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
		const ev = { type, target: this, preventDefault() {}, stopPropagation() {}, ...(extra || {}) };
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
	const doc = { title: "Itinerary", listeners: {} };
	doc.createElement = (tag) => new FakeElement(doc, tag);
	doc.documentElement = new FakeElement(doc, "html");
	doc.head = new FakeElement(doc, "head");
	doc.body = new FakeElement(doc, "body");
	doc.documentElement.append(doc.head, doc.body);
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

/**
 * A window whose history behaves as a browser's does where it matters here:
 *   - `pushState` drops the forward entries;
 *   - `back()`/`forward()` are queued and land on a later task, so code that runs between the
 *     call and the landing (a `.then`, a timer) can push first;
 *   - the person's own Back and Forward land at once;
 *   - every popstate listener gets the SAME event object, and a listener removed mid-dispatch
 *     is not called;
 *   - a traversal past either end does nothing.
 * `calls` records each history call with its argument count, so a URL argument shows.
 */
function fakeBrowser(options) {
	const opts = options || {};
	const listeners = {};
	const entries = (opts.entries || [null]).map((state) => ({ state }));
	let index = entries.length - 1;
	const calls = [];
	const clone = (value) => (value === undefined ? null : structuredClone(value));
	const dispatch = (type, ev) => {
		for (const fn of (listeners[type] || []).slice()) {
			if ((listeners[type] || []).includes(fn)) fn(ev);
		}
	};
	const traverse = (delta) => {
		const target = index + delta;
		if (target < 0 || target >= entries.length) return false;
		index = target;
		dispatch("popstate", { type: "popstate", state: clone(entries[index].state) });
		return true;
	};
	const history = {
		get state() {
			return entries[index].state;
		},
		get length() {
			return entries.length;
		},
		pushState(state) {
			calls.push({ call: "push", args: arguments.length, state });
			if (opts.refusePush) throw new Error("SecurityError: pushState refused");
			entries.splice(index + 1);
			entries.push({ state: clone(state) });
			index = entries.length - 1;
		},
		replaceState(state) {
			calls.push({ call: "replace", args: arguments.length, state });
			entries[index] = { state: clone(state) };
		},
		back() {
			calls.push({ call: "back" });
			if (!opts.backNeverLands) setImmediate(() => traverse(-1));
		},
		forward() {
			calls.push({ call: "forward" });
			setImmediate(() => traverse(1));
		},
	};
	const win = {
		history,
		location: { origin: "https://erp.example.com", pathname: opts.path || "/itinerary", search: "" },
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
		listenerCount: (type) => (listeners[type] || []).length,
		userBack: () => traverse(-1),
		userForward: () => traverse(1),
	};
}

const flush = async (rounds) => {
	for (let i = 0; i < (rounds || 4); i++) await new Promise((resolve) => setImmediate(resolve));
};

function findByText(doc, tag, text) {
	for (const node of doc.body.walk()) if (node.tagName === tag && node.textContent === text) return node;
	return null;
}

function findInput(doc, placeholder) {
	for (const node of doc.body.walk()) if (node.placeholder === placeholder) return node;
	return null;
}

/**
 * The panel's history entry, driven through the real panel on the fake DOM. Each scenario
 * leaves no panel open and no cleanup pending, because both are module state in panel.js.
 */
async function backClosesThePanel(P, stripComments) {
	for (const name of ["isPanelOpen", "wantsHistoryEntry"]) {
		if (typeof P[name] !== "function") {
			console.error(`MARKER NOT FOUND: panel.js no longer exports ${name}()`);
			process.exit(2);
		}
	}
	const R = await import(pathToFileURL(path.join(CAPTURE, "recorder.js")).href);
	const KEY = P.HISTORY_KEY;
	const snap = (surface) => ({ schema: 1, surface, page: { path: "/itinerary", title: "Itinerary" } });

	// A confirm() a person answered takes time; one the browser refused to show returns at once.
	const realNow = Date.now;
	let skew = 0;
	Date.now = () => realNow() + skew;
	const answered = (answer, log) => (message) => {
		if (log) log.push(message);
		skew += 1000;
		return answer;
	};

	/**
	 * A page as the kiosk or Stock Scan builds it: the recorder installed, its own popstate
	 * listener registered at boot (so before the panel's), and the panel bundle's global.
	 */
	function page(options) {
		const opts = options || {};
		const b = fakeBrowser(opts);
		const doc = fakeDocument();
		b.win.document = doc;
		b.win.EE_CAPTURE = { surface: opts.surface === "kiosk" ? "kiosk" : "web" };
		if (opts.feedback) b.win.EE_FEEDBACK_BOOT = { user: "a@x.com" };
		globalThis.window = b.win;
		globalThis.document = doc;
		const api = R.install(b.win);
		const saw = [];
		b.win.addEventListener("popstate", (ev) => saw.push({ open: api.isOpen(), state: ev.state }));
		b.win.ee_capture_panel = { open: P.openPanel, isOpen: P.isPanelOpen };
		return { b, doc, api, saw };
	}

	async function open(pg, surface) {
		const handle = await P.openPanel(snap(surface || "web"), { surface: surface || "web" });
		let result = null;
		handle.closed.then((r) => {
			result = r;
		});
		return { handle, result: () => result };
	}

	const panelRoot = (doc) => {
		for (const node of doc.body.walk()) if (node.classList.contains("ee-cap-root")) return node;
		return null;
	};
	/** The panel id in the current entry, or null. Null-safe, so a regression fails a check rather than crashing the run. */
	const topId = (pg) => (pg.b.win.history.state || {})[KEY] || null;

	console.log("\nBack: the panel's own history entry, on the web and the kiosk");
	{
		const pg = page();
		check("ee_capture.isOpen() is false before the panel exists", [pg.api.isOpen(), P.isPanelOpen()], [false, false]);
		const { handle } = await open(pg);
		const pushes = pg.b.calls.filter((c) => c.call === "push");
		check("opening pushes exactly one entry", pushes.length, 1);
		check("with two arguments, never a URL", pushes[0].args, 2);
		check("its state is {ee_capture: <id>} and nothing else", Object.keys(pushes[0].state), [KEY]);
		truthy("the id is a string, unique to this panel", typeof pushes[0].state[KEY] === "string" && pushes[0].state[KEY].length >= 8);
		check("it is on top", [pg.b.index(), topId(pg)], [1, pushes[0].state[KEY]]);
		check("isOpen() through both globals", [pg.api.isOpen(), pg.b.win.ee_capture_panel.isOpen()], [true, true]);
		check("the recorder's route ring did not grow (same path)", pg.api.snapshot().routes.length, 1);
		const again = await P.openPanel(snap("web"), { surface: "web" });
		check("a second open brings the same panel forward, with no second entry", [again === handle, pg.b.count("push")], [true, 1]);
		handle.close();
		await flush();
	}

	console.log("\nclosing by the panel's own controls removes the entry, once");
	for (const [label, close] of [
		["Cancel", (pg) => findByText(pg.doc, "BUTTON", "Cancel").dispatch("click")],
		["×", (pg) => findByText(pg.doc, "BUTTON", "×").dispatch("click")],
		["Escape in the panel", (pg) => panelRoot(pg.doc).dispatch("keydown", { key: "Escape" })],
		["Escape with focus outside it", (pg) => pg.doc.dispatch("keydown", { key: "Escape" })],
		["handle.close()", (pg, handle) => handle.close()],
	]) {
		const pg = page();
		const { handle, result } = await open(pg);
		close(pg, handle);
		check(`${label}: one history.back()`, pg.b.count("back"), 1);
		check(`${label}: still reported open until that Back lands`, [P.isPanelOpen(), result()], [true, null]);
		await flush();
		check(`${label}: back on the page's entry`, pg.b.index(), 0);
		check(`${label}: the page saw that popstate with isOpen() true`, pg.saw.map((s) => s.open), [true]);
		check(`${label}: closed resolves after it`, [P.isPanelOpen(), result() && result().status], [false, "canceled"]);
		check(`${label}: no listener left behind`, pg.b.listenerCount("popstate"), 2);
	}

	console.log("\nForward onto the dead entry does not reopen the panel");
	{
		const pg = page();
		const { handle } = await open(pg);
		handle.close();
		await flush();
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		pg.b.userForward();
		await flush();
		check("no panel, no question, no step back", [!!panelRoot(pg.doc), asked.length, pg.b.count("back"), P.isPanelOpen()], [false, 0, 1, false]);
		check("the page sees an ee_capture state with isOpen() false, and re-stamps it", [pg.saw[1].open, KEY in pg.saw[1].state], [false, true]);
	}

	// A page with no history of its own (/itinerary, /travel_guidelines) keeps the entry a Back
	// left behind, and nobody re-stamps it. replaceState could not remove it anyway. Pinned
	// here: stepping onto it and off again is inert, and the panel makes no history call.
	console.log("\nthe entry a Back left behind: Forward onto it and Back off it are inert");
	{
		const pg = page();
		const { result } = await open(pg);
		const id = topId(pg);
		pg.b.userBack(); // nothing typed: closes at once
		await flush();
		check("closed by Back", [!!panelRoot(pg.doc), P.isPanelOpen(), result() && result().status], [false, false, "canceled"]);
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		const before = pg.b.calls.length;
		pg.b.userForward();
		await flush();
		check("Forward lands on the dead entry: no panel, nothing asked", [pg.b.index(), topId(pg), !!panelRoot(pg.doc), asked.length, P.isPanelOpen()], [1, id, false, 0, false]);
		check("a page with history of its own would see it with isOpen() false, to re-stamp it", [pg.saw[1] && pg.saw[1].open, pg.saw[1] && pg.saw[1].state && pg.saw[1].state[KEY]], [false, id]);
		pg.b.userBack();
		await flush();
		check("Back off it: on the page's entry, still no panel", [pg.b.index(), !!panelRoot(pg.doc), P.isPanelOpen(), pg.saw.length], [0, false, false, 3]);
		check("the panel made no history call through either step", pg.b.calls.slice(before), []);
		check("and left no listener behind", pg.b.listenerCount("popstate"), 2);
	}

	console.log("\na reload with the panel open: the page starts on the old entry, never taken for the new panel's");
	{
		const stale = { [KEY]: "entry-from-before-the-reload" };
		const pg = page({ entries: [null, stale] });
		const first = await open(pg);
		const id = topId(pg);
		truthy("the new panel's id is its own", !!id && id !== stale[KEY]);
		findByText(pg.doc, "BUTTON", "Cancel").dispatch("click");
		await flush(8);
		check("Cancel: one back() onto the stale entry, no forward() past it", [pg.b.count("back"), pg.b.count("forward"), pg.b.index(), topId(pg)], [1, 0, 1, stale[KEY]]);
		check("closed and settled", [P.isPanelOpen(), first.result() && first.result().status], [false, "canceled"]);
		const second = await open(pg);
		check("reopened on top of the stale entry", [pg.b.count("push"), pg.b.index()], [2, 2]);
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		pg.b.userBack(); // off the new entry, onto the stale one
		check("Back onto the stale entry closes the new panel, with no back() of its own", [!!panelRoot(pg.doc), asked.length, pg.b.count("back"), pg.b.index()], [false, 0, 1, 1]);
		await flush();
		check("and settles", [P.isPanelOpen(), second.result() && second.result().status], [false, "canceled"]);
	}

	console.log("\nSend, then Done, removes the entry too");
	{
		const pg = page();
		const realFetch = globalThis.fetch;
		globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ message: { name: "ER-00042" } }) });
		try {
			const { result } = await open(pg);
			findInput(pg.doc, "One line: what went wrong?").value = "Save does nothing";
			findInput(pg.doc, "What you did, what you expected, and what happened instead.").value =
				"The save button spins and nothing is saved.";
			findByText(pg.doc, "BUTTON", "Send report").dispatch("click");
			await flush();
			const done = findByText(pg.doc, "BUTTON", "Done");
			truthy("the report was sent", done);
			done.dispatch("click");
			check("Done: one history.back()", pg.b.count("back"), 1);
			await flush();
			check("Done: back on the page's entry, closed as sent", [pg.b.index(), result().status, result().name], [0, "sent", "ER-00042"]);
		} finally {
			globalThis.fetch = realFetch;
		}
	}

	console.log("\nBack with nothing typed closes at once, without a Back of its own");
	{
		const pg = page();
		const { result } = await open(pg);
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		pg.b.userBack();
		check("closed, nothing asked, no history.back()", [!!panelRoot(pg.doc), asked.length, pg.b.count("back")], [false, 0, 0]);
		check("the page saw that Back with isOpen() true", pg.saw.map((s) => s.open), [true]);
		await flush();
		check("isOpen() false and closed resolved", [P.isPanelOpen(), result().status], [false, "canceled"]);
	}

	console.log("\nBack with a typed report asks; staying re-arms Back, discarding closes");
	{
		const pg = page();
		const { result } = await open(pg);
		const id = topId(pg);
		findInput(pg.doc, "One line: what went wrong?").value = "Half a report";
		const asked = [];
		pg.b.win.confirm = answered(false, asked);
		pg.b.userBack();
		check("asked once, as the Close button asks", asked, ["Discard this report?"]);
		truthy("still open", panelRoot(pg.doc) && P.isPanelOpen());
		check("the entry is back, same id, on top", [pg.b.count("push"), pg.b.index(), topId(pg)], [2, 1, id]);
		check("two arguments on the re-push too", pg.b.calls.filter((c) => c.call === "push").map((c) => c.args), [2, 2]);
		pg.b.win.confirm = answered(true, asked);
		pg.b.userBack();
		check("the next Back asks again", asked.length, 2);
		check("discarded: closed, no history.back() of its own", [!!panelRoot(pg.doc), pg.b.count("back"), pg.b.index()], [false, 0, 0]);
		check("the page saw both Backs with isOpen() true", pg.saw.map((s) => s.open), [true, true]);
		await flush();
		check("closed resolved as canceled", [P.isPanelOpen(), result().status], [false, "canceled"]);
	}

	console.log("\na question the browser never showed does not make Back a trap");
	{
		const pg = page({ entries: [{ screen: "list" }, { screen: "item" }] });
		await open(pg);
		findInput(pg.doc, "One line: what went wrong?").value = "Half a report";
		const asked = [];
		pg.b.win.confirm = (message) => {
			asked.push(message);
			return false; // at once: dialogs are blocked for this page
		};
		pg.b.userBack();
		check("still open, the report kept, the entry NOT pushed again", [!!panelRoot(pg.doc), pg.b.count("push")], [true, 1]);
		pg.b.userBack();
		check("the next Back is not stopped: the browser moves on, nothing asked", [pg.b.index(), asked.length], [0, 1]);
		pg.b.win.confirm = answered(true);
		findByText(pg.doc, "BUTTON", "Cancel").dispatch("click");
		check("closing then steps back over nothing", [P.isPanelOpen(), pg.b.count("back")], [false, 0]);
	}

	console.log("\nthe page moved on while the panel was open: close leaves the page's entry alone");
	{
		const pg = page();
		await open(pg);
		pg.b.win.history.pushState({ screen: "home" }, "");
		findByText(pg.doc, "BUTTON", "Cancel").dispatch("click");
		check("no history.back(), still on the page's entry", [pg.b.count("back"), pg.b.index(), pg.b.win.history.state], [0, 2, { screen: "home" }]);
		check("nothing pending", P.isPanelOpen(), false);
	}
	{
		const pg = page();
		await open(pg);
		const id = topId(pg);
		pg.b.win.history.pushState({ screen: "home" }, "");
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		pg.b.userBack(); // off the page's entry, onto the panel's own
		check("Back onto the panel's own entry: still open, nothing asked", [!!panelRoot(pg.doc), asked.length, topId(pg)], [true, 0, id]);
		check("the page saw it with isOpen() true, so it leaves the entry alone", pg.saw.map((s) => s.open), [true]);
		findByText(pg.doc, "BUTTON", "Cancel").dispatch("click");
		await flush();
		check("its entry is on top again, so closing removes it", [pg.b.count("back"), pg.b.index(), P.isPanelOpen()], [1, 0, false]);
	}

	console.log("\na page push between the cleanup back() and its landing: step forward again");
	{
		const pg = page();
		const { result } = await open(pg);
		findByText(pg.doc, "BUTTON", "Cancel").dispatch("click");
		pg.b.win.history.pushState({ screen: "home" }, ""); // a timer of the page's, in the gap
		await flush(8);
		check("one back, one forward", [pg.b.count("back"), pg.b.count("forward")], [1, 1]);
		check("ends on the page's new entry", [pg.b.index(), pg.b.win.history.state], [2, { screen: "home" }]);
		check("the page saw both steps with isOpen() true", pg.saw.map((s) => s.open), [true, true]);
		check("then settled", [P.isPanelOpen(), result().status], [false, "canceled"]);
	}

	console.log("\na page that pushes when `closed` resolves pushes after the cleanup, not under it");
	{
		const pg = page();
		const { handle } = await open(pg);
		handle.closed.then(() => pg.b.win.history.pushState({ screen: "home" }, ""));
		handle.close();
		await flush();
		check("no dead entry between the page's two", pg.b.entries.map((e) => e.state), [null, { screen: "home" }]);
		check("on the new one", pg.b.index(), 1);
	}

	console.log("\nreopened before the cleanup landed: the new entry waits for it");
	{
		const pg = page();
		const first = await open(pg);
		const firstId = topId(pg);
		first.handle.close();
		const second = await open(pg); // before the queued Back has landed
		check("no second push yet", pg.b.count("push"), 1);
		await flush();
		truthy("the new panel is open: the cleanup was not read as a Back", panelRoot(pg.doc) && P.isPanelOpen());
		const state = pg.b.win.history.state;
		check("its own entry on top of the page's", [pg.b.entries.length, pg.b.index(), !!state && state[KEY] !== firstId], [2, 1, true]);
		check("the first panel's closed resolved", first.result().status, "canceled");
		second.handle.close();
		await flush();
		check("and it cleans up after itself", [pg.b.index(), P.isPanelOpen()], [0, false]);
	}

	console.log("\nthe cleanup Back never lands: the panel stops waiting after BACK_SETTLE_MS");
	{
		const pg = page({ backNeverLands: true });
		const { handle, result } = await open(pg);
		handle.close();
		await new Promise((resolve) => setTimeout(resolve, P.BACK_SETTLE_MS / 2));
		check("still waiting at half the time", [P.isPanelOpen(), result()], [true, null]);
		await new Promise((resolve) => setTimeout(resolve, P.BACK_SETTLE_MS / 2 + 100));
		check("then closed, and isOpen() false", [P.isPanelOpen(), result() && result().status], [false, "canceled"]);
	}

	console.log("\nthe kiosk takes an entry; the Desk and /feedback do not");
	{
		const pg = page({ surface: "kiosk" });
		const { handle } = await open(pg, "kiosk");
		check("kiosk: one push", pg.b.count("push"), 1);
		handle.close();
		await flush();
		check("kiosk: removed on close", pg.b.index(), 0);
	}
	{
		const pg = page({ entries: [{ route: "list" }, { route: "form" }] });
		const { handle } = await open(pg, "desk");
		check("Desk: no push", pg.b.count("push"), 0);
		pg.b.userBack();
		check("Desk: Back is frappe's router's; the panel stays open", [!!panelRoot(pg.doc), P.isPanelOpen()], [true, true]);
		handle.close();
		check("Desk: no history.back() on close", [pg.b.count("back"), P.isPanelOpen()], [0, false]);
	}
	{
		const pg = page({ feedback: true });
		const { handle } = await open(pg);
		check("/feedback: no push (its router re-renders on every popstate)", pg.b.count("push"), 0);
		handle.close();
		check("/feedback: no history.back()", [pg.b.count("back"), P.isPanelOpen()], [0, false]);
	}
	{
		// The known gap while /feedback is excluded: Back is its router's, as before this change.
		const pg = page({ feedback: true, entries: [{ view: "mine" }, { view: "request" }] });
		const { handle } = await open(pg);
		findInput(pg.doc, "One line: what went wrong?").value = "Half a report";
		const asked = [];
		pg.b.win.confirm = answered(true, asked);
		pg.b.userBack();
		check("/feedback: Back moves the page underneath; the panel stays, nothing asked", [pg.b.index(), !!panelRoot(pg.doc), asked.length], [0, true, 0]);
		check("/feedback: the page's router saw that popstate with isOpen() true", pg.saw.map((s) => s.open), [true]);
		pg.b.win.confirm = answered(true);
		handle.close();
		check("/feedback: closing makes no history call", [pg.b.count("push"), pg.b.count("back"), P.isPanelOpen()], [0, 0, false]);
	}
	check("wantsHistoryEntry: web yes, kiosk yes, desk no, /feedback no", [
		P.wantsHistoryEntry("web", { history: { pushState() {} } }),
		P.wantsHistoryEntry("kiosk", { history: { pushState() {} } }),
		P.wantsHistoryEntry("desk", { history: { pushState() {} } }),
		P.wantsHistoryEntry("web", { history: { pushState() {} }, EE_FEEDBACK_BOOT: {} }),
	], [true, true, false, false]);
	check("wantsHistoryEntry: no history API, or a throwing window, is no", [
		P.wantsHistoryEntry("web", {}),
		P.wantsHistoryEntry("web", Object.defineProperty({}, "history", { get() { throw new Error("boom"); } })),
	], [false, false]);
	check("wantsHistoryEntry: EE_CAPTURE.history false (the settings off switch) is no; true or absent is yes", [
		P.wantsHistoryEntry("kiosk", { history: { pushState() {} }, EE_CAPTURE: { history: false } }),
		P.wantsHistoryEntry("web", { history: { pushState() {} }, EE_CAPTURE: { history: false } }),
		P.wantsHistoryEntry("web", { history: { pushState() {} }, EE_CAPTURE: { history: true } }),
		P.wantsHistoryEntry("web", { history: { pushState() {} }, EE_CAPTURE: {} }),
	], [false, false, true, true]);

	console.log("\na history that refuses pushState: the panel still opens and closes");
	{
		const pg = page({ refusePush: true });
		const realWarn = console.warn;
		console.warn = () => {};
		try {
			const { handle } = await open(pg);
			truthy("open", panelRoot(pg.doc) && P.isPanelOpen());
			handle.close();
			check("closed, with no history.back()", [P.isPanelOpen(), pg.b.count("back")], [false, 0]);
		} finally {
			console.warn = realWarn;
		}
	}

	console.log("\nthe bundle's global carries isOpen, and the recorder asks it safely");
	{
		const pg = page();
		delete pg.b.win.ee_capture_panel;
		await import(pathToFileURL(path.join(CAPTURE, "..", "capture_panel.bundle.js")).href);
		check("capture_panel.bundle.js installs isOpen: isPanelOpen", pg.b.win.ee_capture_panel.isOpen === P.isPanelOpen, true);
		pg.b.win.ee_capture_panel = { open() {}, isOpen() { throw new Error("boom"); } };
		check("a throwing isOpen reads as closed", pg.api.isOpen(), false);
		pg.b.win.ee_capture_panel = { open() {} };
		check("a panel without isOpen reads as closed", pg.api.isOpen(), false);
	}

	console.log("\nsource rules for the history calls");
	{
		const calls = [];
		for (const file of fs.readdirSync(CAPTURE).filter((f) => f.endsWith(".js"))) {
			for (const c of historyCallArgs(stripComments(fs.readFileSync(path.join(CAPTURE, file), "utf8")))) calls.push({ file, ...c });
		}
		truthy("the scan found the panel's pushState (not vacuous)", calls.some((c) => c.file === "panel.js" && c.name === "pushState"));
		check("every pushState/replaceState in capture/ has two arguments, never a URL", calls.filter((c) => c.args !== 2).map((c) => c.text), []);
		const all = fs.readdirSync(CAPTURE).map((f) => stripComments(fs.readFileSync(path.join(CAPTURE, f), "utf8"))).join("\n");
		check("no beforeunload prompt anywhere in capture/", /beforeunload/.test(all), false);
		check("no history.go() either", /history\.go\(/.test(all), false);
	}

	Date.now = realNow;
	// The last fake window stays global: the bundle's load-time draft offer reads `window` after
	// a delay, and finds nobody signed in there.
}

/** The argument lists of every `.pushState(` / `.replaceState(` call in a source text. */
function historyCallArgs(source) {
	const out = [];
	const re = /\.(pushState|replaceState)\(/g;
	let m;
	while ((m = re.exec(source))) {
		let depth = 0;
		let args = 1;
		let quote = "";
		let i = m.index + m[0].length;
		for (; i < source.length; i++) {
			const c = source[i];
			if (quote) {
				if (c === "\\") i++;
				else if (c === quote) quote = "";
				continue;
			}
			if (c === '"' || c === "'" || c === "`") quote = c;
			else if ("([{".includes(c)) depth++;
			else if (")]}".includes(c)) {
				if (depth === 0) break;
				depth--;
			} else if (c === "," && depth === 0) args++;
		}
		out.push({ name: m[1], args, text: source.slice(m.index, i + 1) });
	}
	return out;
}

(async () => {
	let P;
	try {
		P = await import(pathToFileURL(TARGET).href);
	} catch (err) {
		console.error("COULD NOT LOAD panel.js — it and its imports must stay importable without a DOM.");
		console.error(err && err.message);
		process.exit(2);
	}

	const needed = [
		"openPanel",
		"validateFields",
		"descriptionCounter",
		"contextFields",
		"buildPayload",
		"resolveAppVersion",
		"resolveUser",
		"fitSnapshot",
		"minimalSnapshot",
		"summarizeSnapshot",
		"classifyError",
		"describeDraftOutcome",
		"sendDraftsLabel",
		"normalizeSurface",
		"sendSavedDrafts",
		"clearSavedDrafts",
		"offerSavedDraftsOnLoad",
		"wellFormed",
		"cut",
		"newClientId",
		"contextToSend",
	];
	for (const name of needed) {
		if (typeof P[name] !== "function") {
			console.error(`MARKER NOT FOUND: panel.js no longer exports ${name}()`);
			process.exit(2);
		}
	}

	console.log("\nvalidation mirrors the server: title required, <= 200; description >= 20");
	const good = {
		request_type: "Bug",
		impact: "Blocking my work",
		title: "Save does nothing",
		description: "x".repeat(20),
	};
	check("a good report passes", P.validateFields(good), { ok: true, errors: {} });
	check("empty title", Object.keys(P.validateFields({ ...good, title: "   " }).errors), ["title"]);
	check("201-char title", Object.keys(P.validateFields({ ...good, title: "t".repeat(201) }).errors), ["title"]);
	check("200-char title is fine", P.validateFields({ ...good, title: "t".repeat(200) }).ok, true);
	check("19 characters is short", Object.keys(P.validateFields({ ...good, description: "d".repeat(19) }).errors), [
		"description",
	]);
	check(
		"padding does not count toward 20",
		P.validateFields({ ...good, description: `   ${"d".repeat(19)}   ` }).ok,
		false
	);
	check("unknown type", Object.keys(P.validateFields({ ...good, request_type: "Idea" }).errors), ["request_type"]);
	check("Feature is a type", P.validateFields({ ...good, request_type: "Feature" }).ok, true);
	check("impact is required, as the server requires it", Object.keys(P.validateFields({ ...good, impact: "" }).errors), ["impact"]);
	check("an impact the Select does not know is refused", Object.keys(P.validateFields({ ...good, impact: "Urgent" }).errors), ["impact"]);
	check("the impacts are the Select's own", P.IMPACTS, ["Blocking my work", "Painful but I can work around it", "Nice to have"]);

	console.log("\nthe description counter");
	check("empty", P.descriptionCounter(""), { ok: false, text: "20 more characters needed" });
	check("one short", P.descriptionCounter("d".repeat(19)), { ok: false, text: "1 more character needed" });
	check("enough", P.descriptionCounter("d".repeat(25)), { ok: true, text: "25 characters" });

	console.log("\ncontext_url: path always; the query string on the Desk only");
	const deskSnap = {
		surface: "desk",
		page: { path: "/desk/todo/abc", query: "?view=1", form: { doctype: "ToDo", name: "abc", is_new: false } },
	};
	const env = (surface, extra) => ({
		surface,
		location: { pathname: "/from-location", search: "?token=SECRET" },
		userAgent: "UA",
		appVersion: "1.2.3",
		...(extra || {}),
	});
	const desk = P.contextFields(deskSnap, env("desk"));
	check("desk keeps the snapshot's query", desk.context_url, "/desk/todo/abc?view=1");
	check("desk form doctype", desk.context_doctype, "ToDo");
	check("desk form name", desk.context_docname, "abc");
	const web = P.contextFields({ surface: "web", page: { path: "/itinerary", query: "?token=SECRET" } }, env("web"));
	check("web drops a query even when the snapshot carries one", web.context_url, "/itinerary");
	const kiosk = P.contextFields(null, env("kiosk"));
	check("kiosk with no snapshot uses the path, never location.search", kiosk.context_url, "/from-location");
	const deskNoSnap = P.contextFields(null, env("desk"));
	check("desk with no snapshot falls back to location", deskNoSnap.context_url, "/from-location?token=SECRET");
	check(
		"desk query without its ? gets one",
		P.contextFields({ page: { path: "/desk", query: "a=1" } }, env("desk")).context_url,
		"/desk?a=1"
	);
	check(
		"desk snapshot with a null query adds nothing from location",
		P.contextFields({ page: { path: "/desk/todo", query: null } }, env("desk")).context_url,
		"/desk/todo"
	);
	check(
		"a new document's throwaway name is not sent",
		P.contextFields({ page: { path: "/desk/todo/new", form: { doctype: "ToDo", name: "new-todo-1", is_new: true } } }, env("desk"))
			.context_docname,
		""
	);
	const listCtx = P.contextFields({ page: { path: "/desk/todo", list: { doctype: "ToDo", view: "List" } } }, env("desk"));
	check("a list gives the doctype and no name", [listCtx.context_doctype, listCtx.context_docname], ["ToDo", ""]);
	check(
		"user agent capped at 300",
		P.contextFields(null, env("web", { userAgent: "u".repeat(400) })).context_user_agent.length,
		300
	);
	check("url capped at 500", P.contextFields({ page: { path: `/${"p".repeat(600)}` } }, env("web")).context_url.length, 500);
	check("app version passed through", desk.context_app_version, "1.2.3");

	console.log("\nthe payload holds only what the server accepts");
	const payload = P.buildPayload(
		{ request_type: "Bug", title: "  Save does nothing  ", description: "  It spins forever.  ", steps_to_reproduce: " 1. Click " },
		desk
	);
	check("title trimmed", payload.title, "Save does nothing");
	check("description trimmed", payload.description, "It spins forever.");
	check("steps kept for a bug", payload.steps_to_reproduce, "1. Click");
	check(
		"Feature drops steps",
		"steps_to_reproduce" in P.buildPayload({ request_type: "Feature", title: "t", description: "d", steps_to_reproduce: "s" }, desk),
		false
	);
	check("empty steps are omitted, not sent blank", "steps_to_reproduce" in P.buildPayload({ ...good, steps_to_reproduce: "  " }, desk), false);
	check("an unknown type becomes Bug", P.buildPayload({ request_type: "Idea" }, {}).request_type, "Bug");
	check("the chosen impact is sent", P.buildPayload({ ...good, impact: "Nice to have" }, desk).impact, "Nice to have");
	check("a missing impact sends the shown default", P.buildPayload({ request_type: "Bug" }, {}).impact, P.DEFAULT_IMPACT);
	const allowed = serverAllowedFields();
	if (!allowed || !allowed.size) {
		failures += 1;
		console.error("  FAIL could not read SUBMIT_ALLOWED_FIELDS from api/feedback.py");
	} else {
		const extra = Object.keys(payload).filter((key) => !allowed.has(key));
		check("every payload key is in SUBMIT_ALLOWED_FIELDS", extra, []);
	}
	check("no key named sid anywhere in the payload", keysDeep(payload).includes("sid"), false);

	console.log("\nthe app version: Desk boot, else the web template's build, else blank");
	check(
		"Desk boot wins",
		P.resolveAppVersion({
			frappe: { boot: { versions: { erpnext_enhancements: "1.530.0" } } },
			EE_CAPTURE: { build: "web" },
		}),
		"1.530.0"
	);
	check("web build", P.resolveAppVersion({ EE_CAPTURE: { build: "1.529.0" } }), "1.529.0");
	check("nothing", P.resolveAppVersion({}), "");
	check("no window", P.resolveAppVersion(undefined), "");

	console.log("\nwho the drafts belong to (a hint only; the server is asked before sending)");
	check("opts first", P.resolveUser({ user: "a@x.com" }, { EE_CAPTURE: { user: "b@x.com" } }), "a@x.com");
	check("then the recorder's config", P.resolveUser({}, { ee_capture: { config: { user: "c@x.com" } }, EE_CAPTURE: { user: "b@x.com" } }), "c@x.com");
	check("then the web template", P.resolveUser({}, { EE_CAPTURE: { user: "b@x.com" } }), "b@x.com");
	check("then the Desk session", P.resolveUser({}, { frappe: { session: { user: "d@x.com" } } }), "d@x.com");
	check("Guest is nobody", P.resolveUser({ user: "Guest" }, {}), "");
	const hostile = {};
	Object.defineProperty(hostile, "EE_CAPTURE", {
		get() {
			throw new Error("boom");
		},
	});
	hostile.frappe = { session: { user: "e@x.com" } };
	check("a throwing source is skipped", P.resolveUser({}, hostile), "e@x.com");

	console.log("\nthe snapshot is fitted under the limit before it is shown");
	const small = { schema: 1, page: { path: "/desk" }, console: [], requests: [], routes: [], app: { a: 1 } };
	const fittedSmall = P.fitSnapshot(small);
	check("a small snapshot is unchanged", fittedSmall, { snapshot: small, trimmed: false });
	fittedSmall.snapshot.app.a = 2;
	check("and it is a copy, not the recorder's object", small.app.a, 1);
	const huge = {
		schema: 1,
		captured_at: "2026-09-23T00:00:00Z",
		surface: "desk",
		page: { path: "/desk/todo/abc", title: "ToDo" },
		console: Array.from({ length: 20 }, (_, i) => ({ level: "error", message: "e".repeat(5000) + i, count: 1 })),
		requests: Array.from({ length: 20 }, () => ({ method: "POST", path: "/api/method/x", status: 500 })),
		routes: Array.from({ length: 10 }, (_, i) => ({ path: `/desk/${i}` })),
		app: { blob: "z".repeat(400 * 1024) },
	};
	const fittedHuge = P.fitSnapshot(huge);
	const size = Buffer.byteLength(JSON.stringify(fittedHuge.snapshot), "utf8");
	truthy(`an oversized snapshot fits (${size} bytes <= ${P.SNAPSHOT_MAX_BYTES})`, size <= P.SNAPSHOT_MAX_BYTES);
	check("it says it was shortened", [fittedHuge.trimmed, fittedHuge.snapshot.truncated], [true, true]);
	check("the page survives", fittedHuge.snapshot.page.path, "/desk/todo/abc");
	check("the recorder's object is untouched", huge.app.blob.length, 400 * 1024);
	check("a tighter limit still fits", Buffer.byteLength(JSON.stringify(P.fitSnapshot(huge, 2000).snapshot)) <= 2000, true);
	check("not an object -> null", P.fitSnapshot("nope"), { snapshot: null, trimmed: false });
	const loop = { a: 1 };
	loop.self = loop;
	check("circular -> null, not a throw", P.fitSnapshot(loop), { snapshot: null, trimmed: false });
	check("the limit leaves room under the server's 200 KB", P.SNAPSHOT_MAX_BYTES < 200 * 1024, true);

	console.log("\nthe fallback snapshot never carries a query string");
	const minimal = P.minimalSnapshot("desk", { pathname: "/desk/x", title: "X", userAgent: "UA" });
	check("schema 1", minimal.schema, 1);
	check("query is null even on the Desk", minimal.page.query, null);
	truthy("it says why it is thin", minimal.note);
	check("an unknown surface becomes web", P.minimalSnapshot("mars", {}).surface, "web");

	console.log("\nsummary line");
	check(
		"counts and plurals",
		P.summarizeSnapshot({ console: [1], requests: [1, 2], routes: [] }),
		"1 console error · 2 failed or slow requests · 0 recent pages"
	);
	check("missing arrays are zero", P.summarizeSnapshot(null), "0 console errors · 0 failed or slow requests · 0 recent pages");

	console.log("\nerrors: the WI's wording, and offline is a draft, not a failure");
	check("network failure is offline", P.classifyError({ status: 0 }).kind, "offline");
	check("429 wording", P.classifyError({ status: 429, message: "server text" }).message, "You've sent several reports just now. Try again in a minute.");
	check("403 wording", P.classifyError({ status: 403 }).message, "Only staff accounts can send reports.");
	check("417 shows the server's reason", P.classifyError({ status: 417, message: "New requests are paused right now." }), {
		kind: "refused",
		message: "New requests are paused right now.",
	});
	check("401 is an ended session", P.classifyError({ status: 401 }).kind, "session");
	check("SessionExpired is an ended session", P.classifyError({ status: 403, payload: { exc_type: "SessionExpired" } }).kind, "session");
	check("5xx is the server's problem", P.classifyError({ status: 502, message: "<html>Bad gateway</html>" }).kind, "server");
	check("413 names the screenshot", P.classifyError({ status: 413 }).kind, "refused");
	check("a plain Error keeps its message", P.classifyError(new Error("odd")), { kind: "error", message: "odd" });
	check("nothing at all", P.classifyError(null).kind, "error");

	console.log("\ntemporary refusals keep a saved draft");
	check(
		"a pause is its own kind, not a refusal",
		P.classifyError({ status: 417, message: "New requests are paused right now.", payload: { exc_type: "FeedbackPausedError" } }).kind,
		"paused"
	);
	check(
		"a stale CSRF token is its own kind, not a refusal",
		P.classifyError({ status: 400, payload: { exc_type: "CSRFTokenError" } }).kind,
		"stale"
	);
	{
		const src = fs.readFileSync(TARGET, "utf8");
		const run = src.slice(src.indexOf("async function runDrafts"), src.indexOf("export function clearSavedDrafts"));
		check(
			"runDrafts deletes a draft only on refused or forbidden",
			run.includes('outcome.kind === "refused" || outcome.kind === "forbidden"') && !/kind === "(paused|stale|session|offline|throttled)"/.test(run),
			true
		);
		const api = fs.readFileSync(FEEDBACK_API, "utf8");
		truthy("the server raises FeedbackPausedError from submit_capture", /def submit_capture[\s\S]*?FeedbackPausedError/.test(api));
	}

	console.log("\nno half an emoji reaches the server");
	check("a lone high surrogate is replaced", P.wellFormed("ok \ud83d"), "ok \ufffd");
	check("a lone low surrogate is replaced", P.wellFormed("\ude00x"), "\ufffdx");
	check("a whole pair is kept", P.wellFormed("a \ud83d\ude00 b"), "a \ud83d\ude00 b");
	check("cut never splits a pair", P.cut("ab\ud83d\ude00", 3), "ab");
	check("cut keeps a whole pair", P.cut("ab\ud83d\ude00c", 4), "ab\ud83d\ude00");
	{
		const fitted = P.fitSnapshot({ console: [{ message: "boom \ud83d" }] }).snapshot;
		check("the fitted snapshot is well formed", JSON.stringify(fitted).includes("\\ud83d"), false);
		const big = { console: [{ message: "x".repeat(2999) + "\ud83d\ude00" + "y".repeat(5000) }], app: { pad: "z".repeat(400000) } };
		const clipped = JSON.stringify(P.fitSnapshot(big).snapshot);
		check("clipping leaves no lone surrogate", /\\ud83d(?!\\ude00)/.test(clipped), false);
	}

	console.log("\none id per report, and sent_at for the skew correction");
	{
		const a = P.newClientId();
		truthy("the id has the shape submit_capture accepts", /^[A-Za-z0-9-]{8,64}$/.test(a));
		check("two reports, two ids", a === P.newClientId(), false);
		const snap = { schema: 1, captured_at: "2026-09-23T10:00:00.000Z" };
		const sent = P.contextToSend(snap, new Date("2026-09-23T10:05:00.000Z"));
		check("sent_at is stamped", sent.sent_at, "2026-09-23T10:05:00.000Z");
		check("captured_at is kept", sent.captured_at, "2026-09-23T10:00:00.000Z");
		check("the reviewed snapshot is not changed", "sent_at" in snap, false);
		check("no snapshot still sends an object", Object.keys(P.contextToSend(null, new Date(0))), ["sent_at"]);
	}

	console.log("\nthe screenshot prefix is the one the retention job deletes");
	{
		const jobs = fs.readFileSync(path.join(__dirname, "..", "erpnext_enhancements", "product_feedback", "capture_jobs.py"), "utf8");
		const m = jobs.match(/CAPTURE_SHOT_PREFIX = "([^"]+)"/);
		check("capture_jobs.CAPTURE_SHOT_PREFIX == panel SHOT_PREFIX", m && m[1], P.SHOT_PREFIX);
		const src = fs.readFileSync(TARGET, "utf8");
		truthy("the upload is named with it", src.includes("asFile(image, `${SHOT_PREFIX}${fileStamp()}.png`)"));
	}

	console.log("\nsaved drafts: labels and outcomes");
	check("one", P.sendDraftsLabel(1), "Send 1 saved report");
	check("several", P.sendDraftsLabel(3), "Send 3 saved reports");
	check("all sent", P.describeDraftOutcome(["ER-1", "ER-2"], [], 0, null), {
		message: "Sent 2 saved reports (ER-1, ER-2).",
		tone: "ok",
	});
	check("throttled part-way", P.describeDraftOutcome(["ER-1"], [], 2, { kind: "throttled" }), {
		message: "Sent 1 saved report (ER-1). You've sent several reports just now. Try again in a minute. 2 reports still saved on this device.",
		tone: "warn",
	});
	check("refused and removed", P.describeDraftOutcome([], ["Title is required."], 0, null).tone, "bad");
	check("nothing to send", P.describeDraftOutcome([], [], 0, null).message, "There are no saved reports to send.");

	console.log("\nsurfaces");
	check("kiosk", P.normalizeSurface("KIOSK"), "kiosk");
	check("unknown falls back", P.normalizeSurface("tv", "desk"), "desk");
	check("unknown with no fallback is web", P.normalizeSurface(undefined), "web");

	console.log("\nthe stylesheet: prefixed, themed, above the Desk's modals, touch-sized");
	const css = P.PANEL_CSS || "";
	const classes = (css.match(/\.[a-zA-Z][\w-]*/g) || []).filter((c) => !/^\.\d/.test(c));
	check("every class selector is ee-cap-*", classes.filter((c) => !c.startsWith(".ee-cap-")), []);
	truthy("dark via the OS, unless the page chose light", css.includes('@media (prefers-color-scheme: dark)') && css.includes(':root:not([data-theme="light"])'));
	truthy("dark via the Desk/kiosk data-theme", css.includes(':root[data-theme="dark"]'));
	const zs = Array.from(css.matchAll(/z-index:\s*(\d+)/g), (m) => Number(m[1]));
	truthy("z-index above the Desk modal (1040) and the app's highest overlay (2050)", zs.length && Math.min(...zs) > 2050);
	truthy("44px controls by default", /--ee-cap-tap:\s*44px/.test(css));
	truthy("larger on the kiosk", /\.ee-cap-kiosk\s*\{\s*--ee-cap-tap:\s*56px/.test(css));

	console.log("\nsource rules for every panel-side file");
	// Comments stripped first: the comment explaining why a token is absent names the token.
	const stripComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
	for (const name of ["panel.js", "annotate.js", "drafts.js"]) {
		const text = stripComments(fs.readFileSync(path.join(CAPTURE, name), "utf8"));
		check(`${name}: no innerHTML (the snapshot is page text)`, /\binnerHTML\b|outerHTML|insertAdjacentHTML/.test(text), false);
		check(`${name}: never the guest error logger`, text.includes("log_client_error"), false);
		check(`${name}: no sid key`, /["'\s{,]sid["']?\s*:/.test(text), false);
		check(`${name}: tabs, not spaces`, /^ {2,}\S/m.test(text.replace(/`[\s\S]*?`/g, "")), false);
	}

	await backClosesThePanel(P, stripComments);

	console.log("");
	if (failures) {
		console.error(failures + " assertion(s) failed");
		process.exit(1);
	}
	console.log("capture panel: all assertions passed");
})();
