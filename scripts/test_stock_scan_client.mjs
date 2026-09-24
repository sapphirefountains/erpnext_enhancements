#!/usr/bin/env node
/**
 * The Stock Scan page's pure client logic, executed — not grepped.
 *
 * `/stock-scan` is a phone page that posts submitted stock vouchers, one tap per save. The
 * decisions it makes before it asks the server anything live in plain ES modules with no DOM,
 * `public/js/stock_scan/logic.js`, `transport.js` and `nav.js`, and this runs them directly.
 * Plain node, no runner and no npm install, the shape of the repo's other JS guards
 * (`scripts/test_marketing_client.js`).
 *
 * The assertions worth the most:
 *
 *   - **The page's twin of the label parser stays identical to the server's.** The server's
 *     `stock_scan_rules.parse_scan` decides every scan; the page's `parseScan` only names the
 *     label in the error it shows when the server could not answer (`scanFailure`). It runs
 *     over `erpnext_enhancements/tests/data/stock_scan_parse_vectors.json`, the same file
 *     `tests/test_stock_scan_rules.py` runs `parse_scan` over, so that name is always the one
 *     the server would have looked up.
 *   - **A signed-out phone is told to reload.** Frappe v16 runs an expired session as Guest
 *     and answers a 403 "… is not whitelisted" (`session_expired: 1` on the first response
 *     only). `isSignedOut` / `call()` turn that into a 401 `signedOut` error, so the page
 *     offers Reload instead of a Try again that can never work.
 *   - **A kept reference expires.** `keptRef` reuses a save's `client_ref` only inside
 *     `RETRY_WINDOW_MS`; the server keeps every reference forever, so an identical save an
 *     hour later must not be answered "already saved" and posted nowhere.
 *   - **A remembered job is the picker's own.** `rememberedJob` hands the phone's job back only
 *     to the user who picked it.
 *   - **A retried save carries a reference the server accepts.** `mintRef()` is checked against
 *     the server's own `_CLIENT_REF` pattern, read out of `api/stock_scan.py` here so the two
 *     cannot drift. A ref the server refuses is a save that fails every time, on every phone.
 *   - **An error is a sentence.** `errorMessage` picks the LAST message Frappe raised (a
 *     unique-key collision queues Frappe's own message before ours), strips ERPNext's HTML
 *     ("Insufficient Stock" carries links), reads list-valued messages, and turns a CSRF
 *     failure — whose server text is just "Invalid Request" — into "reload the page".
 *   - **A dropped connection is retryable, a refusal is not.** `call()` over a stubbed `fetch`:
 *     a network error or a timeout is `StockScanCallError` with status 0 and `retryable`, a 417
 *     is not, and the CSRF token is read from the boot when the call is made, not at import.
 *   - **Back and Forward never change the URL.** `nav.js` runs against a fake session history
 *     that REFUSES any `pushState`/`replaceState` without exactly `(state, "")` — a URL change is
 *     a camera prompt per shelf on an iPhone. Over it: the first screen is a replace (Back from
 *     it leaves the page), a sheet is one marker that Back closes and × steps back off once, a
 *     screen reached from a sheet takes the marker's entry, an entry the page did not write
 *     (the report form's, an earlier load's) is re-stamped rather than restored, the report
 *     form opens only once nothing of ours is in flight, and `back()` is never called onto an
 *     entry that is not ours.
 *   - **"Report a problem" shows only where it can work.** `reportAvailable` wants the recorder
 *     and the `system_user=yes` cookie, the same test as the floating launcher's.
 *   - **Nothing opens or moves under the report form.** The real `app.js`, mounted on a small
 *     fake DOM with a fake recorder whose form arrives only when told (a slow first download):
 *     tapped, every door — Scan, Search, the job chip, a Recent row, Undo, a scanner gun, How
 *     many?, Save, Move, the back link — does nothing until the form has closed; a form that
 *     lands over a sheet anyway, or after Back un-wanted it, is closed again, not kept; a second
 *     tap while the first is loading asks once; and keys aimed at the form never reach the
 *     camera's code box or a sheet's Escape. Loaded last, after every pure module has proved it
 *     imports without a window.
 *
 * Loads the modules by file URL. If one grows a DOM or `window` access at import time this
 * fails loudly (exit 2) rather than asserting nothing.
 *
 * Run: node scripts/test_stock_scan_client.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const CLIENT = path.join(APP, "public", "js", "stock_scan");
const VECTORS = path.join(APP, "tests", "data", "stock_scan_parse_vectors.json");
const API = path.join(APP, "api", "stock_scan.py");

let failures = 0;
let passes = 0;

function same(a, b) {
	// -0 and 0 are the same quantity; JSON.stringify already says so, and so does this.
	return JSON.stringify(a) === JSON.stringify(b);
}

function check(label, actual, expected) {
	if (same(actual, expected)) {
		passes += 1;
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
	}
}

function truthy(label, value, detail) {
	if (value) {
		passes += 1;
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}${detail ? `: ${detail}` : ""}`);
	}
}

function near(label, actual, expected) {
	truthy(label, typeof actual === "number" && Math.abs(actual - expected) < 1e-9, `got ${actual}, want ${expected}`);
}

async function load(name) {
	const file = path.join(CLIENT, name);
	if (!fs.existsSync(file)) {
		console.error(`MISSING ${path.relative(path.join(APP, ".."), file)}: the front end has not written it yet.`);
		process.exit(2);
	}
	try {
		return await import(pathToFileURL(file).href);
	} catch (err) {
		console.error(`COULD NOT LOAD ${name}: it must import without a DOM, window or fetch.`);
		console.error(err && err.stack ? err.stack : err);
		process.exit(2);
	}
}

function exported(mod, file, names) {
	const missing = names.filter((name) => !(name in mod));
	if (missing.length) {
		console.error(`${file} does not export ${missing.join(", ")} (the build spec names them).`);
		process.exit(2);
	}
}

/** Frappe's `_server_messages`: a JSON string of a list of JSON strings. */
function serverMessages(...messages) {
	return JSON.stringify(messages.map((m) => JSON.stringify(m)));
}

function withTimeout(promise, ms, label) {
	let timer;
	const limit = new Promise((resolve) => {
		timer = setTimeout(() => resolve({ timedOut: true, label }), ms);
	});
	return Promise.race([promise, limit]).finally(() => clearTimeout(timer));
}

// ------------------------------------------------------------------ app.js on a fake DOM

/**
 * Just enough DOM to mount the real `StockScanApp` under node: elements nest, carry classes,
 * attributes and listeners, take focus, and find their `.class` ancestor. Nothing is laid out.
 * The one selector anything here asks `closest` for is a class.
 */
class FakeNode {
	constructor(doc, tag, ns) {
		this.ownerDocument = doc;
		this.tagName = String(tag).toUpperCase();
		this.namespaceURI = ns || null;
		this.childNodes = [];
		this.parentNode = null;
		this.attrs = {};
		this.style = {};
		this.dataset = {};
		this.listeners = {};
		this.className = "";
		this.id = "";
		this.hidden = false;
		this.disabled = false;
		this.value = "";
		this.data = "";
		const names = () => String(this.className).split(/\s+/).filter(Boolean);
		this.classList = {
			add: (...list) => {
				for (const name of list) if (!names().includes(name)) this.className = `${this.className} ${name}`.trim();
			},
			remove: (...list) => {
				this.className = names()
					.filter((c) => !list.includes(c))
					.join(" ");
			},
			toggle: (name, on) => {
				const want = on === undefined ? !names().includes(name) : !!on;
				if (want) this.classList.add(name);
				else this.classList.remove(name);
				return want;
			},
			contains: (name) => names().includes(name),
		};
	}
	get firstChild() {
		return this.childNodes[0] || null;
	}
	get children() {
		return this.childNodes.filter((n) => n.tagName !== "#TEXT");
	}
	get textContent() {
		return this.data + this.childNodes.map((n) => n.textContent).join("");
	}
	set textContent(value) {
		for (const n of this.childNodes) n.parentNode = null;
		this.childNodes = [];
		this.data = String(value);
	}
	get isConnected() {
		let n = this;
		while (n.parentNode) n = n.parentNode;
		return n === this.ownerDocument.documentElement;
	}
	get offsetParent() {
		return this.isConnected ? this.ownerDocument.body : null;
	}
	get offsetHeight() {
		return 0;
	}
	get offsetWidth() {
		return 0;
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
	contains(other) {
		for (let n = other; n; n = n.parentNode) if (n === this) return true;
		return false;
	}
	closest(selector) {
		const cls = String(selector).startsWith(".") ? selector.slice(1) : null;
		for (let n = this; n && n.classList; n = n.parentNode) if (cls && n.classList.contains(cls)) return n;
		return null;
	}
	querySelector() {
		return null;
	}
	querySelectorAll() {
		return [];
	}
	setAttribute(name, value) {
		this.attrs[name] = String(value);
		if (name === "class") this.className = String(value);
	}
	getAttribute(name) {
		return name in this.attrs ? this.attrs[name] : null;
	}
	hasAttribute(name) {
		return name in this.attrs;
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
	focus() {
		this.ownerDocument.activeElement = this;
	}
	blur() {}
	/** A tap. A disabled button, like a browser's, does nothing. */
	click() {
		if (this.disabled) return;
		const ev = { type: "click", target: this, preventDefault() {}, stopPropagation() {} };
		for (const fn of (this.listeners.click || []).slice()) fn(ev);
	}
	*walk() {
		for (const n of this.childNodes) {
			yield n;
			yield* n.walk();
		}
	}
}

function fakePage() {
	const doc = { cookie: "sid=abc; system_user=yes", hidden: false, readyState: "complete", keyListeners: [] };
	doc.createElement = (tag) => new FakeNode(doc, tag);
	doc.createElementNS = (ns, tag) => new FakeNode(doc, tag, ns);
	doc.createTextNode = (text) => {
		const n = new FakeNode(doc, "#text");
		n.data = String(text);
		return n;
	};
	doc.documentElement = new FakeNode(doc, "html");
	doc.head = new FakeNode(doc, "head");
	doc.body = new FakeNode(doc, "body");
	doc.documentElement.appendChild(doc.head);
	doc.documentElement.appendChild(doc.body);
	doc.activeElement = doc.body;
	doc.getElementById = (id) => {
		for (const n of doc.documentElement.walk()) if (n.id === id) return n;
		return null;
	};
	doc.querySelector = () => null;
	const capturing = (opt) => opt === true || !!(opt && opt.capture);
	doc.addEventListener = (type, fn, opt) => doc.keyListeners.push({ type, fn, capture: capturing(opt) });
	doc.removeEventListener = (type, fn, opt) => {
		const i = doc.keyListeners.findIndex((l) => l.type === type && l.fn === fn && l.capture === capturing(opt));
		if (i !== -1) doc.keyListeners.splice(i, 1);
	};
	/** A key pressed with focus on `target`: document capture listeners, then bubble ones. */
	doc.key = (key, target) => {
		const ev = {
			type: "keydown",
			key,
			target: target || doc.activeElement,
			defaultPrevented: false,
			ctrlKey: false,
			metaKey: false,
			altKey: false,
			shiftKey: false,
			preventDefault() {
				this.defaultPrevented = true;
			},
			stopPropagation() {},
		};
		for (const phase of [true, false]) {
			for (const l of doc.keyListeners.slice()) {
				if (l.type === "keydown" && l.capture === phase && doc.keyListeners.includes(l)) l.fn(ev);
			}
		}
		return ev;
	};

	// Session history as a browser keeps it: pushState drops the forward entries, a script's
	// back() lands on a later task, the person's Back lands at once, and every write with a URL
	// argument is refused.
	const listeners = {};
	const entries = [{ state: null }];
	let index = 0;
	const calls = [];
	const clone = (v) => (v === undefined ? null : structuredClone(v));
	const dispatch = (type, ev) => {
		for (const fn of (listeners[type] || []).slice()) if ((listeners[type] || []).includes(fn)) fn(ev);
	};
	const traverse = (delta) => {
		const target = index + delta;
		if (target < 0 || target >= entries.length) return false;
		index = target;
		dispatch("popstate", { type: "popstate", state: clone(entries[index].state) });
		return true;
	};
	const history = {
		scrollRestoration: "auto",
		get state() {
			return entries[index].state;
		},
		get length() {
			return entries.length;
		},
		pushState(state, title) {
			calls.push("push");
			if (arguments.length !== 2 || title !== "") throw new Error(`pushState with ${arguments.length} arguments`);
			entries.splice(index + 1);
			entries.push({ state: clone(state) });
			index = entries.length - 1;
		},
		replaceState(state, title) {
			calls.push("replace");
			if (arguments.length !== 2 || title !== "") throw new Error(`replaceState with ${arguments.length} arguments`);
			entries[index] = { state: clone(state) };
		},
		back() {
			calls.push("back");
			setImmediate(() => traverse(-1));
		},
		forward() {
			calls.push("forward");
			setImmediate(() => traverse(1));
		},
	};
	const store = new Map();
	const win = {
		history,
		location: { href: "https://erp.example.com/stock-scan", pathname: "/stock-scan", reload() {} },
		localStorage: {
			getItem: (k) => (store.has(k) ? store.get(k) : null),
			setItem: (k, v) => store.set(k, String(v)),
			removeItem: (k) => store.delete(k),
		},
		// Reduced motion: a closing sheet leaves the DOM at once instead of after its animation.
		matchMedia: (query) => ({ matches: /reduce/.test(query), addEventListener() {} }),
		scrollTo() {},
		addEventListener(type, fn) {
			(listeners[type] = listeners[type] || []).push(fn);
		},
		removeEventListener(type, fn) {
			const list = listeners[type] || [];
			const i = list.indexOf(fn);
			if (i !== -1) list.splice(i, 1);
		},
	};
	const kinds = () =>
		entries.map((e) => (!e.state ? "-" : e.state.ee_capture ? "P" : e.state.marker ? "M" : e.state.ee_ss ? "S" : "?"));
	return { doc, win, entries, calls, index: () => index, here: () => kinds()[index], kinds, userBack: () => traverse(-1) };
}

/**
 * The capture recorder's `window.ee_capture`, as far as this page uses it. `open()` answers
 * only when the test says the panel bundle has arrived (`arrive`), like a first download on a
 * slow connection. The panel it "mounts" keeps a history entry the way capture/panel.js does:
 * pushed on open; Back while open closes it; closed any other way while its entry is current,
 * it steps back off it and `isOpen()` stays true until that popstate has been delivered.
 */
function fakeRecorder(page) {
	let seq = 0;
	const rec = {
		opens: 0,
		waiting: [],
		panel: null,
		registerCaptureState() {},
		open(opts) {
			rec.opens += 1;
			rec.lastOpts = opts;
			return new Promise((resolve, reject) => rec.waiting.push({ resolve, reject }));
		},
		isOpen() {
			return !!(rec.panel && (!rec.panel.closed || rec.panel.pendingBack));
		},
		arrive() {
			const waiting = rec.waiting.splice(0);
			if (!rec.panel || rec.panel.closed) rec.panel = mount();
			for (const w of waiting) w.resolve(rec.panel.handle);
		},
		fail() {
			for (const w of rec.waiting.splice(0)) w.reject(new Error("The report form could not be loaded."));
		},
	};
	function mount() {
		const p = { id: `cap-${++seq}`, closed: false, pendingBack: false, closeCalls: 0 };
		let resolveClosed = null;
		const closed = new Promise((resolve) => (resolveClosed = resolve));
		page.win.history.pushState({ ee_capture: p.id }, "");
		const finish = (removeEntry) => {
			p.closed = true;
			page.win.removeEventListener("popstate", onPop);
			const state = page.win.history.state;
			if (removeEntry && state && state.ee_capture === p.id) {
				p.pendingBack = true;
				const landed = () => {
					page.win.removeEventListener("popstate", landed);
					p.pendingBack = false;
					resolveClosed({ status: "canceled" });
				};
				page.win.addEventListener("popstate", landed);
				page.win.history.back();
			} else resolveClosed({ status: "canceled" });
		};
		const onPop = (ev) => {
			if (!p.closed && !(ev.state && ev.state.ee_capture === p.id)) finish(false);
		};
		page.win.addEventListener("popstate", onPop);
		p.handle = {
			surface: "web",
			closed,
			close() {
				p.closeCalls += 1;
				if (!p.closed) finish(true);
			},
		};
		return p;
	}
	return rec;
}

/** Let timers, script traversals and promise callbacks run. */
async function settle() {
	for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 2));
}

/**
 * The review's sequence, run on the real app.js: on a slow connection "Report a problem" is
 * tapped, and Scan is tapped before the form appears. The camera sheet opened under the form,
 * kept decoding, navigated the page under it — writing its screen over the form's own history
 * entry — and took every letter typed into the form. From the tap until the form closes the
 * page now opens nothing and goes nowhere; a form that arrives over a sheet anyway is closed
 * again rather than kept; and keys aimed at the form never reach a sheet or the camera.
 */
async function appUnderTheReportForm() {
	const page = fakePage();
	const rec = fakeRecorder(page);
	const log = {
		name: "SSL-0001",
		action: "Take",
		status: "Posted",
		item_code: "PDT-0008",
		item_name: "Widget",
		warehouse: "Bin A1 - SF",
		warehouse_name: "Bin A1",
		qty: 1,
		stock_uom: "Each",
		posted_at: "2026-09-24 09:00:00",
		can_undo: 1,
	};
	const boot = { user: "tina@example.com", today: "2026-09-24", recent: [log], settings: {}, initial: null, decoder_url: "/jsqr.js" };
	const realFetch = globalThis.fetch;
	const fetched = [];
	globalThis.window = page.win;
	globalThis.document = page.doc;
	page.win.ee_capture = rec;
	page.win.EE_STOCK_SCAN_BOOT = Object.assign({ csrf_token: "tok" }, boot);
	globalThis.fetch = async (url) => {
		fetched.push(String(url));
		return new Response(JSON.stringify({ message: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
	};
	try {
		const A = await load("app.js");
		const U = await load("ui.js");
		const root = page.doc.createElement("div");
		root.id = "ee-stock-scan-root";
		root.className = "ee-ss-root";
		page.doc.body.appendChild(root);
		const app = new A.StockScanApp(root, boot);
		app.mount();
		const find = (cls) => {
			for (const n of page.doc.body.walk()) if (n.classList && n.classList.contains(cls)) return n;
			return null;
		};
		const pushes = () => page.calls.filter((c) => c === "push").length;

		console.log("\nstock scan app under the report form\n");
		check("app: the page boots onto the entry it opened (one replace, no push)", [page.kinds(), pushes()], [["S"], 0]);
		check("app: the header door is drawn for a System User with the recorder on the page", app.reportBtn.hidden, false);

		// 1. Tapped, still downloading: every door on the page is shut.
		app.reportBtn.click();
		check("app: Report asks the recorder once, as surface web", [rec.opens, rec.lastOpts], [1, { surface: "web" }]);
		check("app: ... and the button says it is busy", app.reportBtn.getAttribute("aria-busy"), "true");
		app.scanBtn.click();
		find("ee-ss-hero").click();
		app.searchBtn.click();
		find("ee-ss-searchbox").click();
		app.jobChip.click();
		find("ee-ss-recent-main").click();
		find("ee-ss-undo-btn").click();
		for (const k of ["P", "D", "T", "Enter"]) page.doc.key(k, page.doc.body); // a scanner gun
		await settle();
		check("app: while the form loads, Scan, Search, the job chip, a Recent row, Undo and a scanner gun open nothing", [U.sheetDepth(), app.scanner], [0, null]);
		check("app: ... look nothing up", fetched, []);
		check("app: ... and add no history entry", [page.kinds(), pushes()], [["S"], 0]);

		// 2. It arrives on an untouched page and is kept; the page stays shut while it is open.
		rec.arrive();
		await settle();
		truthy("app: the form that arrives is kept", app.report === rec.panel.handle && !rec.panel.closed);
		check("app: ... its own entry is on top of the screen, nothing of ours over it", page.kinds(), ["S", "P"]);
		check("app: ... and the button is no longer busy", app.reportBtn.getAttribute("aria-busy"), null);
		app.scanBtn.click();
		app.searchBtn.click();
		check("app: with the form open, Scan and Search still open nothing", [U.sheetDepth(), app.scanner, page.kinds()], [0, null, ["S", "P"]]);

		// 3. Back closes the form (the panel answers it); the page does not move.
		page.userBack();
		await settle();
		check("app: Back with the form open is the form's: it closes, the page stays on its screen", [rec.panel.closed, app.report, app.view.name, page.here()], [true, null, "start", "S"]);
		app.scanBtn.click();
		check("app: once it has closed, Scan opens the camera again (one marker)", [U.sheetDepth(), app.scanner !== null, page.kinds()], [1, true, ["S", "M"]]);

		// 4. Keys aimed at something over the page (the report form's textarea) are not the
		// camera's or the sheet's; keys aimed at the page still are.
		const formField = page.doc.createElement("textarea"); // mounted on <body>, like the panel
		page.doc.body.appendChild(formField);
		formField.focus();
		page.doc.key("a", formField);
		check("app: a letter typed into the form stays there (the camera does not take focus)", page.doc.activeElement === formField, true);
		page.doc.key("Escape", formField);
		check("app: ... and Escape in the form does not close the sheet under it", U.sheetDepth(), 1);
		page.doc.body.focus();
		page.doc.key("b", page.doc.body);
		const box = page.doc.activeElement;
		check("app: a scanner gun on the page still lands in the camera's code box", [box.tagName, box.placeholder], ["INPUT", "Type a code"]);
		page.doc.key("Escape", box);
		await settle();
		check("app: ... and Escape on the page still closes the camera, stepping back off its marker", [U.sheetDepth(), app.scanner, page.here()], [0, null, "S"]);
		formField.remove();

		// 5. A door that forgot to check: a sheet opens while the form loads, and the form lands
		// over it. It must not be kept (the sheet would keep its keys, its marker would sit under
		// the form's entry): it is closed again, which leaves the sheet and its marker as they were.
		const before = rec.opens;
		app.reportBtn.click();
		check("app: Report tapped again", rec.opens, before + 1);
		const forgot = U.sheet({ title: "A door that forgot" });
		check("app: ... a sheet opened anyway pushes its marker", page.kinds(), ["S", "M"]);
		rec.arrive();
		await settle();
		check("app: a form that lands over a sheet is closed again, not kept", [rec.panel.closeCalls, rec.panel.closed, app.report], [1, true, null]);
		check("app: ... its entry is gone and the browser is back on the sheet's marker", [page.here(), U.sheetDepth()], ["M", 1]);
		forgot.close();
		await settle();
		check("app: ... and closing the sheet then steps back onto the screen: nothing orphaned", [page.here(), page.index()], ["S", 0]);

		// 6. Back while it loads un-wants it: it is closed when it lands, on the screen Back chose.
		app.showLocation({ warehouse: "Bin A1 - SF", warehouse_name: "Bin A1", items: [] }, {});
		check("app: a location is a new entry", [page.kinds(), app.view.name], [["S", "S"], "location"]);
		app.reportBtn.click();
		page.userBack();
		await settle();
		check("app: Back while the form loads goes back", app.view.name, "start");
		rec.arrive();
		await settle();
		check("app: ... and the form that lands afterwards is closed again", [rec.panel.closed, app.report, page.here(), page.index()], [true, null, "S", 0]);

		// 7. Back un-wants it, then Report is tapped again before it lands: the one open() in flight
		// is wanted again — no second open(), whose unwanted answer would close the same panel.
		app.showLocation({ warehouse: "Bin A1 - SF", warehouse_name: "Bin A1", items: [] }, {});
		const opensBefore = rec.opens;
		app.reportBtn.click();
		page.userBack();
		await settle();
		app.reportBtn.click();
		check("app: Report, Back, Report again while loading asks the recorder once", rec.opens, opensBefore + 1);
		rec.arrive();
		await settle();
		truthy("app: ... and the form that lands is kept", app.report === rec.panel.handle && !rec.panel.closed);
		app.report.close();
		await settle();
		check("app: closed from its own ×, it takes its entry with it", [app.report, page.here()], [null, "S"]);

		// 8. An item view's doors: the stepper's quantity sheet, Save and Move.
		app.showItem(
			{
				item_code: "PDT-0008",
				item_name: "Widget",
				warehouse: "Bin A1 - SF",
				warehouse_name: "Bin A1",
				stock_uom: "Each",
				on_hand: 5,
				available: 5,
				open_orders: [],
				elsewhere: [{ warehouse: "Stores - SF", warehouse_name: "Stores", on_hand: 3 }],
			},
			{}
		);
		check("app: an item view", app.view.name, "item");
		const entriesBefore = page.entries.length;
		app.reportBtn.click();
		app.nudge(1);
		// Called, not only tapped: a tap on a disabled button would pass for the wrong reason.
		find("ee-ss-step-value").click();
		app.askQuantity();
		app.save();
		app.openMove();
		app.goBack();
		await settle();
		check("app: while the form loads, How many?, Save, Move and the back link do nothing", [U.sheetDepth(), app.view.name, page.entries.length, fetched], [0, "item", entriesBefore, []]);
		rec.fail();
		await settle();
		check("app: a form that fails to load leaves the page usable", [app.reportWanted, app.reportBusy()], [false, false]);
		find("ee-ss-step-value").click();
		check("app: ... How many? opens again", U.sheetDepth(), 1);
		U.closeAllSheets();
		await settle();

		// The Inventory Scanner Settings off switch reaches the page as settings.browser_history = 0.
		const off = new A.StockScanApp(page.doc.createElement("div"), Object.assign({}, boot, { settings: { browser_history: 0 } }));
		const on = new A.StockScanApp(page.doc.createElement("div"), Object.assign({}, boot, { settings: {} }));
		check("app: settings.browser_history 0 (the off switch) gives the page no history; absent keeps it", [off.nav.enabled, on.nav.enabled], [false, true]);
	} finally {
		globalThis.fetch = realFetch;
		delete globalThis.window;
		delete globalThis.document;
	}
}

(async () => {
	if (typeof globalThis.window !== "undefined") {
		console.error("a window global exists before the modules load; this test must load them without one");
		process.exit(2);
	}
	const L = await load("logic.js");
	const T = await load("transport.js");
	const N = await load("nav.js");
	exported(L, "logic.js", [
		"parseScan", "plain", "clampChange", "describeChange", "mintRef", "CLIENT_REF_RE", "isSafeImage", "jobExpired",
		// Not in the spec's list, but app.js's retry rule is these; they are tested below.
		"mayHaveSaved", "saveKey", "keptRef", "RETRY_WINDOW_MS",
		// The review fixes: the offline scan error, the per-user job, the moved-on save message.
		"scanFailure", "rememberedJob", "saveLabel",
		// "Report a problem" in the header.
		"reportAvailable",
	]);
	exported(T, "transport.js", ["M", "call", "StockScanCallError", "errorMessage", "isSignedOut", "SIGNED_OUT"]);
	exported(N, "nav.js", ["NavHistory", "MAX_ENTRIES", "TRAVERSE_TIMEOUT_MS"]);

	console.log("stock scan client\n");

	// ------------------------------------------------------------------ parseScan
	const vectors = JSON.parse(fs.readFileSync(VECTORS, "utf8"));
	truthy(`the shared vectors are substantial (${vectors.length})`, vectors.length >= 15);
	let parseOk = 0;
	for (const v of vectors) {
		const got = L.parseScan(v.raw);
		const want = { kind: v.kind, value: v.value };
		const pair = got && { kind: got.kind, value: got.value };
		if (same(pair, want)) parseOk += 1;
		else check(`parseScan(${JSON.stringify(v.raw)}) — ${v.why || ""}`, pair, want);
	}
	check(`parseScan agrees with stock_scan_rules.parse_scan on every vector`, parseOk, vectors.length);

	// ------------------------------------------------------------------ scanFailure
	// The one use the page makes of parseScan: naming the label when the server never answered.
	const why = "Could not reach the server. Check your signal and try again.";
	check(
		"scanFailure: a location label is named by its location",
		L.scanFailure("https://erp.sapphirefountains.com/stock-scan?w=Bin%20B1-2-10%20-%20SF", why),
		`Couldn't open Bin B1-2-10 - SF: ${why}`,
	);
	check(
		"scanFailure: + in a label decodes to a space, as on the server",
		L.scanFailure("https://erp.example.com/stock-scan/?w=Stores+-+SF", why),
		`Couldn't open Stores - SF: ${why}`,
	);
	check("scanFailure: an item label names the item", L.scanFailure("/stock-scan?item=PDT-0008", why), `Couldn't open PDT-0008: ${why}`);
	check("scanFailure: a typed code is quoted", L.scanFailure("  pump seal ", why), `Couldn't look up "pump seal": ${why}`);
	check(
		"scanFailure: another site's URL is text, never a location",
		L.scanFailure("https://evil.example.com/stock-scan-not?w=X", why),
		`Couldn't look up "https://evil.example.com/stock-scan-not?w=X": ${why}`,
	);
	check("scanFailure: a blank scan is just the reason", L.scanFailure("   ", why), why);
	const long = L.scanFailure("x".repeat(200), why);
	truthy("scanFailure: a very long code is cut to 60 characters", long.includes(`"${"x".repeat(59)}…"`) && !long.includes("x".repeat(60)), long);
	truthy("scanFailure: no reason is still a sentence", /\w/.test(L.scanFailure("Bin A1", "")) && !/undefined|null/.test(L.scanFailure("Bin A1", undefined)));

	// ------------------------------------------------------------------ plain
	// Expected values are Python's stock_scan_rules.plain on the same inputs.
	const plainCases = [
		[3, "3"],
		[3.0, "3"],
		[2.5, "2.5"],
		[0.1 + 0.2, "0.3"],
		[1 / 3, "0.333333"],
		[2.0000001, "2"],
		[12.125, "12.125"],
		[-3, "-3"],
		[-2.5, "-2.5"],
		[0, "0"],
		[100000, "100000"],
		[1234567.5, "1234567.5"],
	];
	for (const [value, want] of plainCases) check(`plain(${value})`, L.plain(value), want);

	// ------------------------------------------------------------------ clampChange
	check("clampChange: a take stops at what is available", L.clampChange(-5, 3), -3);
	check("clampChange: a take within stock is kept", L.clampChange(-3, 3), -3);
	check("clampChange: fractional stock bounds a take", L.clampChange(-10, 2.5), -2.5);
	check("clampChange: nothing available means nothing to take", L.clampChange(-1, 0), 0);
	check("clampChange: negative stock is not a license to take", L.clampChange(-4, -2), 0);
	check("clampChange: an add is not bounded by stock", L.clampChange(7, 5), 7);
	check("clampChange: an add stops at the per-save maximum", L.clampChange(200000, 5), 100000);
	check("clampChange: the maximum is 100000 by default", L.clampChange(100000, 0), 100000);
	check("clampChange: a custom maximum", L.clampChange(9, 5, 4), 4);
	check("clampChange: zero stays zero", L.clampChange(0, 12), 0);

	// ------------------------------------------------------------------ describeChange
	const take = L.describeChange(-3, "Unit", 12);
	check("describeChange: a take", [take.verb, take.label], ["take", "Take 3"]);
	near("describeChange: a take leaves on hand minus the change", take.after, 9);
	const add = L.describeChange(5, "Unit", 12);
	check("describeChange: an add", [add.verb, add.label], ["add", "Add 5"]);
	near("describeChange: an add raises on hand", add.after, 17);
	const none = L.describeChange(0, "Unit", 12);
	check("describeChange: no change is a disabled Save", [none.verb, none.label], ["none", "Save"]);
	const half = L.describeChange(-2.5, "Unit", 10);
	check("describeChange: decimals go through plain()", half.label, "Take 2.5");
	near("describeChange: and so does the arithmetic", half.after, 7.5);
	check("describeChange: float noise is not shown", L.describeChange(0.1 + 0.2, "Unit", 1).label, "Add 0.3");
	check("describeChange: no location means no 'after'", L.describeChange(4, "Unit", null).after, null);

	// ------------------------------------------------------------------ mintRef
	const source = fs.readFileSync(API, "utf8");
	const serverPattern = (source.match(/_CLIENT_REF\s*=\s*re\.compile\(r"([^"]+)"\)/) || [])[1];
	truthy("the server's _CLIENT_REF pattern was found in api/stock_scan.py", Boolean(serverPattern));
	const SERVER = new RegExp(serverPattern || "^(?!)$");
	truthy("CLIENT_REF_RE is a RegExp", L.CLIENT_REF_RE instanceof RegExp);
	const probes = [
		"ss-abc", "ss-12345", "a".repeat(7), "a".repeat(8), "a".repeat(80), "a".repeat(81),
		"ss-abc def", "ss-abc/def", "ss-abc_def", "ss.abc:de-f", "", "ss-é1234567", "SS-ABC.123:x-y",
		"ss-kf3z9a1-8qz0x4m2", "ss-<script>", "ss-abc;drop",
	];
	const disagree = probes.filter((p) => L.CLIENT_REF_RE.test(p) !== SERVER.test(p));
	check("CLIENT_REF_RE accepts exactly what the server's pattern accepts", disagree, []);
	const refs = new Set();
	let refsOk = true;
	for (let i = 0; i < 2000; i += 1) {
		const ref = L.mintRef();
		if (typeof ref !== "string" || !SERVER.test(ref) || !L.CLIENT_REF_RE.test(ref) || !ref.startsWith("ss-")) {
			refsOk = false;
			check("a minted ref the server accepts", ref, "ss-<time>-<random> matching " + serverPattern);
			break;
		}
		refs.add(ref);
	}
	truthy("2000 minted refs all match the server pattern and start ss-", refsOk);
	check("and no two are the same", refs.size, 2000);

	// ------------------------------------------------------------------ isSafeImage
	const images = [
		["/files/pump.jpg", true],
		["/private/files/pump.jpg", true],
		["/assets/erpnext/images/placeholder.png", true],
		["https://cdn.example.com/pump.jpg", true],
		["http://cdn.example.com/pump.jpg", false],
		["//evil.example.com/pixel.gif", false],
		["/\\evil.example.com/pixel.gif", false],
		["javascript:alert(1)", false],
		["data:image/png;base64,iVBORw0KGgo=", false],
		["files/pump.jpg", false],
		["", false],
		[null, false],
		[undefined, false],
	];
	for (const [src, want] of images) check(`isSafeImage(${JSON.stringify(src)})`, Boolean(L.isSafeImage(src)), want);

	// ------------------------------------------------------------------ jobExpired
	const HOUR = 60 * 60 * 1000;
	const now = Date.UTC(2026, 8, 23, 15, 0, 0);
	check("jobExpired: picked this morning", Boolean(L.jobExpired(now - 2 * HOUR, now)), false);
	check("jobExpired: picked a minute under 12 h ago", Boolean(L.jobExpired(now - 12 * HOUR + 60000, now)), false);
	check("jobExpired: picked yesterday", Boolean(L.jobExpired(now - 12 * HOUR - 60000, now)), true);
	check("jobExpired: picked last week", Boolean(L.jobExpired(now - 7 * 24 * HOUR, now)), true);
	check("jobExpired: no saved time is expired, not forever", Boolean(L.jobExpired(undefined, now)), true);

	// ------------------------------------------------------------------ rememberedJob
	// The phone remembers the job for the person who picked it, not for whoever holds it next.
	const tina = "tina@sapphirefountains.com";
	const job = { project: "PRJ-00598", project_name: "City Hall Plaza Fountain", customer: "City of Riverton", saved_at: now - HOUR, user: tina };
	check("rememberedJob: the picker gets their job back", L.rememberedJob(job, tina, now), job);
	check("rememberedJob: the next person on the phone does not", L.rememberedJob(job, "sam@sapphirefountains.com", now), null);
	check("rememberedJob: a job stored before jobs had an owner is nobody's", L.rememberedJob({ ...job, user: undefined }, tina, now), null);
	check("rememberedJob: no signed-in user, no job", L.rememberedJob(job, "", now), null);
	check("rememberedJob: a lapsed job is gone even for its picker", L.rememberedJob({ ...job, saved_at: now - 13 * HOUR }, tina, now), null);
	check("rememberedJob: garbage is no job", [L.rememberedJob(null, tina, now), L.rememberedJob("PRJ-1", tina, now), L.rememberedJob({ user: tina, saved_at: now }, tina, now)], [null, null, null]);

	// ------------------------------------------------------------------ the client_ref reuse rule
	// app.js keeps a save's client_ref for the next tap when mayHaveSaved(status) — the server
	// then hands back the first save instead of posting a second — and mints a fresh one when
	// saveKey() changes, i.e. when the person changed what the save would post.
	for (const status of [0, 409, 502, 503, 504]) {
		check(`mayHaveSaved(${status}): the save may have posted, so the retry keeps its ref`, Boolean(L.mayHaveSaved(status)), true);
	}
	for (const status of [400, 401, 403, 404, 417, 429, 500]) {
		check(`mayHaveSaved(${status}): refused and rolled back, so the next tap is a new save`, Boolean(L.mayHaveSaved(status)), false);
	}
	const base = { action: "take", item_code: "PDT-0008", warehouse: "Bin A1-3-1 - SF", qty: 3, project: "PRJ-00577" };
	check("saveKey: the same save is the same key", L.saveKey({ ...base }), L.saveKey({ ...base }));
	check("saveKey: float noise in the quantity is the same save", L.saveKey({ ...base, qty: 3.0000000001 }), L.saveKey(base));
	for (const [label, change] of [
		["the quantity", { qty: 4 }],
		["the item", { item_code: "PDT-0009" }],
		["the location", { warehouse: "Bin A1-3-2 - SF" }],
		["the source of a move", { from_warehouse: "Stores - SF" }],
		["the order line", { purchase_order_item: "abc123" }],
		["without a purchase order", { without_po: 1 }],
		["the job", { project: "PRJ-00578" }],
		["the direction", { action: "add" }],
	]) {
		truthy(`saveKey: changing ${label} is a different save`, L.saveKey({ ...base, ...change }) !== L.saveKey(base));
	}
	// A return from a job and found stock are two different adds (the job is on one only).
	const addBase = { action: "add", item_code: "PDT-0008", warehouse: "Bin A1-3-1 - SF", qty: 2, without_po: 1 };
	truthy("saveKey: 'Returned from <job>' and 'Not on a purchase order' are different saves", L.saveKey({ ...addBase, project: "PRJ-00598" }) !== L.saveKey(addBase));

	// ------------------------------------------------------------------ keptRef: the retry window
	// A kept ref is reused only inside RETRY_WINDOW_MS. The server's _already_saved has no age
	// limit, so a ref kept forever turns a new identical save into "already saved", posted nowhere.
	const MIN = 60 * 1000;
	check("RETRY_WINDOW_MS is ten minutes", L.RETRY_WINDOW_MS, 10 * MIN);
	const kept = { ref: "ss-kf3z9a1-8qz0x4m2ab", at: now };
	check("keptRef: a retry right away reuses the ref", L.keptRef(kept, now + 5000), kept.ref);
	check("keptRef: a retry at the edge of the window reuses it", L.keptRef(kept, now + 10 * MIN), kept.ref);
	check("keptRef: past the window it is a new save", L.keptRef(kept, now + 10 * MIN + 1), null);
	check("keptRef: an hour later it is certainly a new save", L.keptRef(kept, now + 60 * MIN), null);
	check("keptRef: a stamp well in the future (clock changed) is not trusted", L.keptRef({ ...kept, at: now + 30 * MIN }, now), null);
	check("keptRef: a bare string from the old shape is not trusted", L.keptRef(kept.ref, now), null);
	check("keptRef: nothing kept, nothing reused", [L.keptRef(undefined, now), L.keptRef(null, now), L.keptRef({ ref: kept.ref }, now)], [null, null, null]);
	check("keptRef: a ref the server would refuse is not reused", L.keptRef({ ref: "bad ref", at: now }, now), null);

	// ------------------------------------------------------------------ words for a moved-on save
	check("saveLabel: a take", L.saveLabel("take", 3, "Each", "Pump Seal Kit 3/4 in"), "Take 3 Each of Pump Seal Kit 3/4 in");
	check("saveLabel: a move keeps decimals plain", L.saveLabel("move", 7.5, "Foot", "PVC Pipe"), "Move 7.5 Foot of PVC Pipe");
	check("saveLabel: an add", L.saveLabel("add", 5, "", "Widget"), "Add 5 of Widget");
	const returned = { action: "Add Without PO", qty: 2, stock_uom: "Each", item_name: "Widget", warehouse_name: "Bin A1-3-1", project: "PRJ-00598" };
	check("logHeadline: an add with a job reads as a return", L.logHeadline(returned), "Returned 2 Each");
	check("logHeadline: an add without one reads as an add", L.logHeadline({ ...returned, project: null }), "Added 2 Each");
	check("undoQuestion: a return names the job", L.undoQuestion(returned), "Undo: returned 2 Each of Widget to Bin A1-3-1 from PRJ-00598?");
	check("undoQuestion: found stock does not", L.undoQuestion({ ...returned, project: null }), "Undo: added 2 Each of Widget to Bin A1-3-1?");

	// ------------------------------------------------------------------ reportAvailable
	// The header's "Report a problem": the recorder on the page AND the System User cookie
	// (capture/launcher.js's own test, which this bundle may not import).
	const recorder = { open: () => Promise.resolve() };
	check("reportAvailable: no recorder on the page", L.reportAvailable(undefined, "system_user=yes"), false);
	check("reportAvailable: a recorder without open()", L.reportAvailable({ open: 1 }, "system_user=yes"), false);
	check("reportAvailable: a System User", L.reportAvailable(recorder, "system_user=yes"), true);
	check("reportAvailable: among other cookies", L.reportAvailable(recorder, "sid=abc; system_user=yes; full_name=Tina"), true);
	check("reportAvailable: a website user", L.reportAvailable(recorder, "sid=abc; system_user=no"), false);
	check("reportAvailable: a look-alike cookie name", L.reportAvailable(recorder, "xsystem_user=yes"), false);
	check("reportAvailable: no cookie at all", [L.reportAvailable(recorder, null), L.reportAvailable(recorder, "")], [false, false]);

	// ------------------------------------------------------------------ nav.js: Back and Forward
	// NavHistory against a fake session history that behaves like a browser's: pushState drops
	// the forward entries, back() is asynchronous (land() delivers its popstate), and every
	// write is REFUSED unless it has exactly two arguments, the second "" — a third argument is
	// a URL, and on an iPhone a URL change is a camera prompt per shelf.
	function fakeHistory() {
		const h = {
			entries: [{ state: null }],
			index: 0,
			pending: [],
			pushes: 0,
			replaces: 0,
			backs: 0,
			refuse: false,
			scrollRestoration: "auto",
			get state() {
				return h.entries[h.index].state;
			},
			pushState(state, title) {
				if (arguments.length !== 2 || title !== "") throw new Error(`pushState with ${arguments.length} arguments`);
				if (h.refuse) throw Object.assign(new Error("too many calls"), { name: "SecurityError" });
				h.pushes += 1;
				h.entries.splice(h.index + 1);
				h.entries.push({ state: structuredClone(state) });
				h.index = h.entries.length - 1;
			},
			replaceState(state, title) {
				if (arguments.length !== 2 || title !== "") throw new Error(`replaceState with ${arguments.length} arguments`);
				if (h.refuse) throw Object.assign(new Error("too many calls"), { name: "SecurityError" });
				h.replaces += 1;
				h.entries[h.index] = { state: structuredClone(state) };
			},
			back() {
				h.backs += 1;
				h.pending.push(-1);
			},
			/** Deliver the next script traversal: the state its popstate carries, or undefined. */
			land() {
				const delta = h.pending.shift();
				if (delta === undefined || h.index + delta < 0 || h.index + delta >= h.entries.length) return undefined;
				h.index += delta;
				return h.entries[h.index].state;
			},
			/** The phone's own Back / Forward: synchronous here, never counted as the page's back(). */
			userBack() {
				h.index -= 1;
				return h.entries[h.index].state;
			},
			userForward() {
				h.index += 1;
				return h.entries[h.index].state;
			},
			/** The capture panel pushing its own entry over whatever is current (capture/panel.js). */
			foreignPush(state) {
				h.entries.splice(h.index + 1);
				h.entries.push({ state });
				h.index = h.entries.length - 1;
			},
		};
		return h;
	}

	function manualClock() {
		let now = 0;
		let nextId = 1;
		const timers = new Map();
		return {
			later(fn, ms) {
				const id = nextId++;
				timers.set(id, { at: now + (ms || 0), fn });
				return id;
			},
			cancel(id) {
				timers.delete(id);
			},
			tick(ms) {
				now += ms || 0;
				for (;;) {
					const due = [...timers].filter(([, t]) => t.at <= now).sort((a, b) => a[1].at - b[1].at || a[0] - b[0]);
					if (!due.length) return;
					timers.delete(due[0][0]);
					due[0][1].fn();
				}
			},
		};
	}

	function rig(opts) {
		const o = opts || {};
		const h = fakeHistory();
		const clock = manualClock();
		const load = { busy: false };
		const nav = new N.NavHistory(h, o.key || "doc-1", { busy: () => load.busy, later: clock.later, cancel: clock.cancel });
		return { h, clock, load, nav };
	}

	const START_V = { name: "start" };
	const BIN = { name: "location", location: { warehouse: "Bin A1 - SF" } };
	const ITEM = { name: "item", item: { item_code: "PDT-0008", warehouse: "Bin A1 - SF" } };
	const snapOf = (view, back) => ({ view, back: back.slice() });
	const states = (h) => h.entries.map((e) => (e.state ? (e.state.marker ? "M" : e.state.ee_capture ? "P" : "S") : "-"));

	{
		// 1. The first screen replaces the entry the phone opened: Back from it leaves the page.
		const { h, nav } = rig();
		nav.screen(snapOf(START_V, []));
		check("nav: the first screen is stamped with replaceState, never pushed", [h.entries.length, h.pushes, h.replaces], [1, 0, 1]);
		check("nav: ... carrying this document's key and no URL", [h.state.ee_ss, typeof h.state.id], ["doc-1", "number"]);
		check("nav: ... and render() owns scrolling", h.scrollRestoration, "manual");
		check("nav: Back from the first screen is not the page's to answer (no in-page parent behind it)", nav.behindIs(START_V, []), false);
		nav.screen(snapOf(BIN, [START_V]), "replace");
		check("nav: a 'replace' on the first entry stays one entry", [h.entries.length, h.pushes], [1, 0]);
	}

	{
		// 2. Back and Forward restore the screens, from memory.
		const { h, nav } = rig();
		const s0 = snapOf(START_V, []);
		const s1 = snapOf(BIN, [START_V]);
		nav.screen(s0);
		nav.screen(s1);
		check("nav: a tapped screen is a new entry", [h.entries.length, h.pushes], [2, 1]);
		check("nav: item names never go into history.state", Object.keys(h.state).sort(), ["ee_ss", "id"]);
		check("nav: Back restores the screen before", nav.popped(h.userBack()).snap, s0);
		check("nav: Forward restores the one after", nav.popped(h.userForward()).snap, s1);
		truthy("nav: ... the very snapshot, not a copy", nav.snaps.get(h.state.id) === s1);
	}

	{
		// 3. A sheet: one marker, pushed in the tap; closed from the UI, one back() off it.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		nav.overlayOpened();
		check("nav: opening a sheet pushes one marker", states(h), ["S", "S", "M"]);
		nav.overlayOpened();
		check("nav: a second sheet on top shares it", h.pushes, 2);
		nav.overlayClosed();
		nav.overlayClosed();
		clock.tick(0);
		check("nav: closing the last sheet from the UI steps back off the marker once", h.backs, 1);
		const out = nav.popped(h.land());
		check("nav: ... and that popstate changes nothing on screen", out, { close: false, cancel: false });
		check("nav: ... leaving the browser on the screen", [h.index, states(h)[h.index]], [1, "S"]);
	}

	{
		// 4. The phone's Back while a sheet is open closes it, and nothing steps back again.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		nav.overlayOpened();
		const out = nav.popped(h.userBack());
		check("nav: Back over a sheet says close (and cancel whatever it started)", [out.close, out.cancel, "snap" in out], [true, true, false]);
		nav.overlayClosed(); // closeAllSheets() in app.js
		clock.tick(0);
		check("nav: ... and the close that follows issues no back()", h.backs, 0);
	}

	{
		// 5. A sheet that closes INTO a navigation (a camera read, a search pick) keeps its marker
		// until the screen lands, which then takes the marker's entry: nothing added without a tap.
		const { h, clock, load, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		nav.overlayClosed();
		load.busy = true;
		clock.tick(0);
		check("nav: no back() while the lookup the sheet started is loading", h.backs, 0);
		nav.screen(snapOf(BIN, [START_V]));
		check("nav: the screen the lookup opened replaces the marker", [states(h), h.pushes], [["S", "S"], 1]);
		load.busy = false;
		nav.settled();
		clock.tick(0);
		check("nav: ... so settling issues no back()", h.backs, 0);
	}

	{
		// 6. Closed and reopened in one tick (job picker -> "Where did these come from?").
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		nav.overlayClosed();
		nav.overlayOpened();
		clock.tick(0);
		check("nav: close + reopen reuses the marker: one push, no back()", [h.pushes, h.backs, states(h)], [1, 0, ["S", "M"]]);
	}

	{
		// 7. A screen that lands while our own back() is in flight waits for it: [S, N], never [S, M, N].
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		nav.overlayClosed();
		clock.tick(0);
		nav.screen(snapOf(BIN, [START_V]));
		check("nav: a write during our traversal is queued", states(h), ["S", "M"]);
		nav.popped(h.land());
		check("nav: ... and made once it lands", [states(h), h.index], [["S", "S"], 1]);
	}

	{
		// 8. Two quick Backs: each popstate is read for where the browser IS, not counted.
		const { h, nav } = rig();
		const s0 = snapOf(START_V, []);
		nav.screen(s0);
		nav.screen(snapOf(BIN, [START_V]));
		nav.screen(snapOf(ITEM, [START_V, BIN]));
		const a = h.userBack();
		const b = h.userBack();
		nav.popped(a);
		check("nav: Back, Back lands on the grandparent", nav.popped(b).snap, s0);
		check("nav: ... with no back() of the page's own", h.backs, 0);
	}

	{
		// 9. Entries this document never wrote: the report form's, an earlier load's, none at all.
		for (const [label, foreign] of [
			["null state", null],
			["another document's key", { ee_ss: "doc-0", id: 1 }],
			["an unknown id", { ee_ss: "doc-1", id: 999 }],
			["the report form's entry", { ee_capture: 3 }],
			["the report form's key beside ours", { ee_ss: "doc-1", id: 2, ee_capture: 3 }],
		]) {
			const { h, nav } = rig();
			const s1 = snapOf(BIN, [START_V]);
			nav.screen(snapOf(START_V, []));
			nav.screen(s1);
			h.foreignPush(foreign);
			let out = null;
			let threw = null;
			try {
				out = nav.popped(foreign);
			} catch (e) {
				threw = e;
			}
			truthy(`nav: ${label}: does not throw`, !threw, threw && threw.message);
			check(`nav: ${label}: stays on the current screen`, out && "snap" in out, false);
			check(`nav: ${label}: the entry is re-stamped as this document's current screen`, [h.state.ee_ss, !!h.state.ee_capture, nav.snaps.get(h.state.id)], ["doc-1", false, s1]);
		}
	}

	{
		// 10. Forward onto a marker whose sheet is gone: it becomes the screen under it.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		const s1 = snapOf(BIN, [START_V]);
		nav.screen(s1);
		nav.overlayOpened();
		nav.popped(h.userBack());
		nav.overlayClosed();
		clock.tick(0);
		const out = nav.popped(h.userForward());
		check("nav: Forward onto a dead marker re-stamps it as its screen", [states(h), "snap" in out, nav.snaps.get(h.state.id)], [["S", "S", "S"], false, s1]);
	}

	{
		// 11. The in-page back link is the browser's Back only when the entry behind is its parent.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		check("nav: behindIs: the parent over the same stack", nav.behindIs(START_V, []), true);
		check("nav: behindIs: some other view is not it", nav.behindIs(BIN, []), false);
		nav.screen(snapOf(ITEM, [START_V])); // a "root" scan from elsewhere: Start is the parent now
		check("nav: behindIs: after a root scan, the entry behind is not the parent", nav.behindIs(START_V, []), false);
		nav.back();
		check("nav: behindIs: never while a traversal is in flight", nav.behindIs(BIN, [START_V]), false);
		nav.popped(h.land());
		clock.tick(0);
		const r = rig();
		r.nav.screen(snapOf(START_V, []));
		r.nav.screen(snapOf(BIN, [START_V]));
		r.h.foreignPush({ ee_capture: 1 });
		check("nav: behindIs: never while something else's entry is on top", r.nav.behindIs(START_V, []), false);
	}

	{
		// 12. Bounded like a browser's own session history.
		const { nav } = rig();
		nav.screen(snapOf(START_V, []));
		for (let i = 0; i < 200; i += 1) nav.screen(snapOf({ name: "item", n: i }, [START_V]));
		check("nav: at most MAX_ENTRIES remembered", [nav.trail.length <= N.MAX_ENTRIES, nav.snaps.size <= N.MAX_ENTRIES, N.MAX_ENTRIES], [true, true, 50]);
		// Foreign entries reset the trail but keep the snapshots Back may still need; still bounded.
		const r = rig();
		r.nav.screen(snapOf(START_V, []));
		for (let i = 0; i < 300; i += 1) {
			r.nav.screen(snapOf({ name: "item", n: i }, [START_V]));
			if (i % 3 === 0) r.nav.popped({ ee_capture: i });
		}
		truthy("nav: snapshots stay bounded across foreign entries", r.nav.snaps.size <= 2 * N.MAX_ENTRIES, String(r.nav.snaps.size));
	}

	{
		// 13. A traversal whose popstate never comes: the queued writes go out after the timeout.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		nav.overlayClosed();
		clock.tick(0);
		h.pending.length = 0; // the popstate is lost
		nav.screen(snapOf(BIN, [START_V]));
		const queued = h.pushes + h.replaces;
		clock.tick(N.TRAVERSE_TIMEOUT_MS - 1);
		check("nav: still waiting just before TRAVERSE_TIMEOUT_MS", h.pushes + h.replaces, queued);
		clock.tick(1);
		check("nav: ... and written once it passes", h.pushes + h.replaces, queued + 1);
		check("nav: TRAVERSE_TIMEOUT_MS is 1.5 s", N.TRAVERSE_TIMEOUT_MS, 1500);
	}

	{
		// 14. The kill switch: no history object, nothing happens, and the page's own stack rules.
		const off = new N.NavHistory(null, "doc-1", {});
		let ran = 0;
		let threw = null;
		try {
			off.screen(snapOf(START_V, []));
			off.screen(snapOf(BIN, [START_V]));
			off.overlayOpened();
			off.overlayClosed();
			off.settled();
			off.whenQuiet(() => (ran += 1));
		} catch (e) {
			threw = e;
		}
		truthy("nav: switched off (BROWSER_HISTORY = false), every call is a no-op", !threw && !off.enabled, threw && threw.message);
		check("nav: ... popped() says nothing", off.popped({ ee_ss: "doc-1", id: 1 }), {});
		check("nav: ... behindIs() is always false, so the back link walks the page's own stack", off.behindIs(START_V, []), false);
		check("nav: ... whenQuiet() runs at once", ran, 1);
	}

	{
		// 15. Safari throttles bursts of history calls with a SecurityError: the entry is lost, not the screen.
		const { h, nav } = rig();
		nav.screen(snapOf(START_V, []));
		h.refuse = true;
		let threw = null;
		try {
			nav.screen(snapOf(BIN, [START_V]));
			nav.overlayOpened();
		} catch (e) {
			threw = e;
		}
		truthy("nav: a refused pushState does not throw", !threw, threw && threw.message);
		check("nav: ... and the model did not move", [nav.trail.length, nav.marker], [1, null]);
	}

	{
		// 16. The report form owns its entry: it pushes over our screen and backs off it itself.
		// app.js ignores popstate while the form is open; the one after it closes changes nothing.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		h.foreignPush({ ee_capture: 1 });
		h.back(); // the panel's own history.back() on closing from its × or Send
		const backsBefore = h.backs;
		const out = nav.popped(h.land());
		clock.tick(0);
		check("nav: the form backing off its own entry lands on our screen with nothing to do", [out, states(h)[h.index]], [{}, "S"]);
		check("nav: ... and the page issues no back() of its own", h.backs, backsBefore);
		// Forward onto the dead form entry: stamped as our screen, then Back is still a plain Back.
		const fwd = nav.popped(h.userForward());
		check("nav: Forward onto the closed form's entry stays put and re-stamps it", ["snap" in fwd, states(h)], [false, ["S", "S", "S"]]);
		const back = nav.popped(h.userBack());
		check("nav: ... Back from there is the same screen, drawn once", "snap" in back, false);
	}

	{
		// 17. whenQuiet: the report form opens only on a screen of ours, never over our marker.
		const { h, clock, load, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		let opened = 0;
		nav.whenQuiet(() => (opened += 1));
		check("nav: whenQuiet with nothing in flight runs at once, with no back()", [opened, h.backs], [1, 0]);
		// The camera read a code and closed; its lookup is loading, so the marker is held.
		nav.overlayOpened();
		nav.overlayClosed();
		load.busy = true;
		clock.tick(0);
		nav.whenQuiet(() => {
			opened += 1;
			h.foreignPush({ ee_capture: 2 });
		});
		check("nav: whenQuiet steps back off a held marker first (the caller dropped the lookup)", [h.backs, opened], [1, 1]);
		nav.popped(h.land());
		clock.tick(0);
		check("nav: ... and runs once that lands, so the form's entry sits on our screen", [opened, states(h)], [2, ["S", "S", "P"]]);
		load.busy = false;
		nav.settled();
		clock.tick(0);
		check("nav: ... and nothing steps back again afterwards", h.backs, 1);
	}

	{
		// 18. Never back() off an entry that is not ours, even when the model thinks it is.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		h.foreignPush({ ee_capture: 1 });
		nav.overlayClosed();
		clock.tick(0);
		check("nav: something pushed over our marker: the release does not step back off it", h.backs, 0);
	}

	{
		// 19. A bfcache restore (pageshow) on the entry we are on: nothing moves.
		const { h, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.screen(snapOf(BIN, [START_V]));
		check("nav: a popstate for the current entry is nothing", nav.popped(h.state), {});
		check("nav: ... not even a write", [h.pushes, h.replaces], [1, 1]);
	}

	{
		// 20. The review's sequence, at nav's level: a sheet's marker, the report form pushed over
		// it, then a screen lands (a camera read under the form). Taking over "the marker" by the
		// model alone wrote the screen over the FORM's entry, [S, M, S] with P gone, and left M
		// orphaned under it. The form's entry is never ours to write over.
		const { h, clock, nav } = rig();
		nav.screen(snapOf(START_V, []));
		nav.overlayOpened();
		h.foreignPush({ ee_capture: 1 });
		nav.screen(snapOf(BIN, [START_V]));
		check("nav: a screen landing with the form's entry over our marker goes on top, never over it", states(h), ["S", "M", "P", "S"]);
		check("nav: ... and the marker under it was not taken over", nav.marker !== null, true);
		nav.overlayClosed();
		clock.tick(0);
		check("nav: ... so closing the sheet then steps back off nothing", [h.backs, nav.marker], [0, null]);
		const r = rig();
		r.nav.screen(snapOf(START_V, []));
		r.h.foreignPush({ ee_capture: 2 });
		r.nav.screen(snapOf(ITEM, [START_V]), "replace");
		check("nav: a 'replace' (a bin that opened its one item) with the form's entry on top is a push too", states(r.h), ["S", "P", "S"]);
		r.nav.screen(snapOf(BIN, [START_V]), "replace");
		check("nav: ... and once the browser is on ours again, a replace is a replace", [states(r.h), r.h.index], [["S", "P", "S"], 2]);
	}

	// ------------------------------------------------------------------ M
	check("M carries exactly the eleven endpoints", Object.keys(T.M).sort(), [
		"ADD", "ITEM", "LOCATION", "MOVE", "RECENT", "RESOLVE", "SEARCH_ITEMS", "SEARCH_LOCATIONS",
		"SEARCH_PROJECTS", "TAKE", "UNDO",
	]);
	truthy(
		"every M value is a method of api/stock_scan.py",
		Object.values(T.M).every((m) => typeof m === "string" && m.startsWith("erpnext_enhancements.api.stock_scan.")),
	);

	// ------------------------------------------------------------------ errorMessage
	const ours = { message: "Only 3 on hand at Bin A1-3-1, so 4 cannot be taken.", title: "Message", indicator: "red", raise_exception: 1 };
	const theirs = { message: "Duplicate entry for Client Reference", title: "Message", indicator: "red", raise_exception: 1 };
	const info = { message: "Stock Entry STE-0001 created", title: "Message", indicator: "green" };
	check(
		"errorMessage: the LAST raised message wins (Frappe's own collision message comes first)",
		T.errorMessage({ exc_type: "ValidationError", _server_messages: serverMessages(theirs, ours) }, 417),
		ours.message,
	);
	check(
		"errorMessage: a raised message beats a later informational one",
		T.errorMessage({ exc_type: "ValidationError", _server_messages: serverMessages(ours, info) }, 417),
		ours.message,
	);
	check(
		"errorMessage: with nothing raised, the last message",
		T.errorMessage({ _server_messages: serverMessages({ message: "first" }, { message: "second" }) }, 417),
		"second",
	);
	const insufficient = T.errorMessage(
		{
			exc_type: "NegativeStockError",
			_server_messages: serverMessages({
				message: '1 units of <a href="/app/item/PDT-0008">Widget</a> needed in <strong>Bin A1-3-1 - SF</strong> to complete this transaction.',
				title: "Insufficient Stock",
				raise_exception: 1,
			}),
		},
		417,
	);
	truthy("errorMessage: ERPNext's HTML is stripped", !/[<>]/.test(insufficient), insufficient);
	truthy(
		"errorMessage: and the words survive",
		/Widget/.test(insufficient) && /Bin A1-3-1 - SF/.test(insufficient) && /needed/.test(insufficient),
		insufficient,
	);
	const listed = T.errorMessage(
		{ _server_messages: serverMessages({ message: ["Row 1: qty is required", "Row 2: warehouse is required"], raise_exception: 1, as_list: 1 }) },
		417,
	);
	truthy("errorMessage: a list-valued message is read", /Row 1: qty is required/.test(listed) && /Row 2: warehouse is required/.test(listed), listed);
	const denied = T.errorMessage(
		{ exc_type: "PermissionError", _server_messages: serverMessages({ message: "You are not permitted to use Stock Scan.", raise_exception: 1 }) },
		403,
	);
	check("errorMessage: a 403 with the server's sentence shows that sentence", denied, "You are not permitted to use Stock Scan.");

	const csrfBare = T.errorMessage({ exc_type: "CSRFTokenError" }, 400);
	truthy("errorMessage: a CSRF failure says to reload", /reload/i.test(csrfBare), csrfBare);
	// Frappe v16's validate_csrf_token raises frappe.throw(_("Invalid Request"), CSRFTokenError),
	// so the real payload carries that message; "Invalid Request" tells a technician nothing.
	const csrfReal = T.errorMessage(
		{ exc_type: "CSRFTokenError", _server_messages: serverMessages({ message: "Invalid Request", title: "Message", raise_exception: 1 }) },
		400,
	);
	truthy("errorMessage: a CSRF failure says to reload even when the server says 'Invalid Request'", /reload/i.test(csrfReal), csrfReal);

	// A signed-out session on Frappe v16, exactly as the server builds it: sessions.py runs the
	// request as Guest (session_expired: 1 on the first response only), validate_csrf_token
	// skips Guest, and is_whitelisted throws this PermissionError (HTTP 403) for every method here.
	const notWhitelisted = {
		message:
			"<details><summary>You are not permitted to access this resource. Login to access</summary>Function <strong>erpnext_enhancements.api.stock_scan.get_item</strong> is not whitelisted.</details>",
		title: "Method Not Allowed",
		indicator: "red",
		raise_exception: 1,
	};
	const guestLater = { exc_type: "PermissionError", _server_messages: serverMessages(notWhitelisted) };
	const guestFirst = { ...guestLater, session_expired: 1 };
	check("isSignedOut: the first response after expiry (session_expired: 1)", T.isSignedOut(guestFirst, 403), true);
	check("isSignedOut: every later one (403 'not whitelisted', cookie already gone)", T.isSignedOut(guestLater, 403), true);
	check("isSignedOut: a 401", T.isSignedOut({}, 401), true);
	check("isSignedOut: a real permission refusal is not a sign-out", T.isSignedOut({ exc_type: "PermissionError", _server_messages: serverMessages({ message: "You are not permitted to use Stock Scan.", raise_exception: 1 }) }, 403), false);
	check("isSignedOut: 'not whitelisted' only counts on a 403 PermissionError", T.isSignedOut({ exc_type: "ValidationError", _server_messages: serverMessages(notWhitelisted) }, 417), false);
	check("isSignedOut: a bare 403 is not a sign-out", T.isSignedOut({}, 403), false);
	check("isSignedOut: no payload at all", [T.isSignedOut(null, 0), T.isSignedOut(null, 500)], [false, false]);
	check("errorMessage: the Guest 403 says to sign in again, not 'not whitelisted'", T.errorMessage(guestLater, 403), T.SIGNED_OUT);
	check("errorMessage: so does the first response, flagged session_expired", T.errorMessage(guestFirst, 403), T.SIGNED_OUT);
	truthy("SIGNED_OUT says to reload", /reload/i.test(T.SIGNED_OUT) && /sign/i.test(T.SIGNED_OUT), T.SIGNED_OUT);

	const generic = T.errorMessage({}, 500);
	const statusCases = [
		[401, /sign|log|session|access|permi|allow/i],
		[403, /sign|log|session|access|permi|allow/i],
		[429, /too many|wait|moment|again|slow/i],
	];
	for (const [status, pattern] of statusCases) {
		const text = T.errorMessage({}, status);
		truthy(
			`errorMessage: status ${status} with no message is a sentence of its own`,
			typeof text === "string" && text.trim() && pattern.test(text) && !/undefined|\[object/.test(text) && text !== generic,
			JSON.stringify(text),
		);
	}
	// Status 0 never reaches the server, so call() words it itself (checked below); here it
	// only has to be a sentence, with no payload at all.
	const noAnswer = T.errorMessage(null, 0);
	truthy(
		"errorMessage: status 0 with no payload is a sentence",
		typeof noAnswer === "string" && noAnswer.trim().length > 0 && !/undefined|\[object|null/.test(noAnswer),
		JSON.stringify(noAnswer),
	);
	const garbage = T.errorMessage({ _server_messages: "not json" }, 417);
	truthy("errorMessage: a malformed _server_messages does not throw", typeof garbage === "string" && garbage.trim().length > 0, JSON.stringify(garbage));
	truthy("errorMessage: a bare 500 is still a sentence", typeof generic === "string" && generic.trim().length > 0 && !/undefined|\[object/.test(generic), JSON.stringify(generic));

	// ------------------------------------------------------------------ StockScanCallError
	const offline = new T.StockScanCallError("Could not reach the server.", 0);
	truthy("StockScanCallError is an Error", offline instanceof Error);
	check("status 0 is retryable", [offline.status, offline.retryable], [0, true]);
	const refused = new T.StockScanCallError("Only 3 on hand.", 417, { exc_type: "NegativeStockError" });
	check("a refusal is not retryable", [refused.status, refused.retryable], [417, false]);
	check("the exception type rides along", refused.excType, "NegativeStockError");

	// ------------------------------------------------------------------ call() over a stubbed fetch
	const realFetch = globalThis.fetch;
	const seen = [];
	globalThis.window = { EE_STOCK_SCAN_BOOT: { csrf_token: "tok-123" } };
	try {
		globalThis.fetch = async (url, init) => {
			seen.push({ url, init });
			return new Response(JSON.stringify({ message: { kind: "location" } }), {
				status: 200,
				headers: { "Content-Type": "application/json" },
			});
		};
		const args = { code: "Stores - SF", warehouse: null };
		const ok = await T.call(T.M.RESOLVE, args, { timeout: 2000 });
		check("call(): unwraps .message", ok, { kind: "location" });
		const sent = seen[0] || { init: {} };
		check("call(): POSTs to /api/method/<method>", [sent.url, sent.init.method], [`/api/method/${T.M.RESOLVE}`, "POST"]);
		const headers = new Headers(sent.init.headers || {});
		check("call(): sends the CSRF token read from the boot at call time", headers.get("X-Frappe-CSRF-Token"), "tok-123");
		check("call(): sends JSON", (headers.get("Content-Type") || "").split(";")[0], "application/json");
		check("call(): with the session cookie", sent.init.credentials, "same-origin");
		let body = null;
		try {
			body = JSON.parse(sent.init.body);
		} catch (e) {
			body = sent.init.body;
		}
		check("call(): the args are the JSON body", body, args);

		globalThis.fetch = async () => {
			throw new TypeError("Failed to fetch");
		};
		const dropped = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		truthy("call(): a network failure throws StockScanCallError", dropped instanceof T.StockScanCallError, String(dropped));
		check("call(): ... with status 0, retryable", dropped && [dropped.status, dropped.retryable], [0, true]);
		truthy(
			"call(): ... saying the connection is the problem",
			dropped && /connect|reach|network|signal|offline|internet/i.test(dropped.message),
			dropped && dropped.message,
		);

		globalThis.fetch = async () =>
			new Response(
				JSON.stringify({ exc_type: "NegativeStockError", _server_messages: serverMessages(ours) }),
				{ status: 417, headers: { "Content-Type": "application/json" } },
			);
		const failed = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		truthy("call(): a refusal throws StockScanCallError", failed instanceof T.StockScanCallError, String(failed));
		check(
			"call(): ... carrying the server's sentence, its status and type, not retryable",
			failed && [failed.message, failed.status, failed.excType, failed.retryable],
			[ours.message, 417, "NegativeStockError", false],
		);
		check("call(): ... and a refusal is not a sign-out", failed && [failed.signedOut, failed.needsReload], [false, false]);

		for (const [label, body] of [
			["the first response after the session expired", guestFirst],
			["a later one, the cookie already cleared", guestLater],
		]) {
			globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 403, headers: { "Content-Type": "application/json" } });
			const out = await T.call(T.M.ITEM,{ item_code: "X" }, { timeout: 2000 }).then(
				() => null,
				(e) => e,
			);
			check(
				`call(): ${label} (403 'not whitelisted') is a 401 signedOut error saying to reload`,
				out && [out instanceof T.StockScanCallError, out.status, out.signedOut, out.needsReload, out.retryable, out.message],
				[true, 401, true, true, false, T.SIGNED_OUT],
			);
			check(`call(): ... and a save refused that way is not kept for a retry`, Boolean(out && L.mayHaveSaved(out.status)), false);
		}

		globalThis.fetch = async () =>
			new Response(JSON.stringify({ exc_type: "CSRFTokenError", _server_messages: serverMessages({ message: "Invalid Request", raise_exception: 1 }) }), {
				status: 400,
				headers: { "Content-Type": "application/json" },
			});
		const stale = await T.call(T.M.TAKE, { item_code: "X" }, { timeout: 2000 }).then(
			() => null,
			(e) => e,
		);
		check("call(): a stale CSRF token needs a reload but is not a sign-out", stale && [stale.status, stale.needsReload, stale.signedOut], [400, true, false]);

		globalThis.fetch = (url, init) =>
			new Promise((resolve, reject) => {
				const signal = init && init.signal;
				if (signal) signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
			});
		const slow = await withTimeout(
			T.call(T.M.TAKE, { item_code: "X" }, { timeout: 30 }).then(
				() => null,
				(e) => e,
			),
			3000,
			"call() never gave up",
		);
		truthy("call(): honors its timeout (aborts the fetch)", slow && !slow.timedOut, slow && slow.label);
		check(
			"call(): a timeout is StockScanCallError status 0, retryable with the same client_ref",
			slow && !slow.timedOut ? [slow instanceof T.StockScanCallError, slow.status, slow.retryable] : null,
			[true, 0, true],
		);
		truthy(
			"call(): ... and says so in words",
			slow && !slow.timedOut && typeof slow.message === "string" && slow.message.trim().length > 0 && !/abort/i.test(slow.message),
			slow && slow.message,
		);
	} finally {
		globalThis.fetch = realFetch;
		delete globalThis.window;
	}

	await appUnderTheReportForm();

	console.log(`\n${passes} passed, ${failures} failed`);
	process.exit(failures ? 1 : 0);
})().catch((err) => {
	console.error(err && err.stack ? err.stack : err);
	process.exit(2);
});
