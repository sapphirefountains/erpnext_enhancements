/*
 * The framed Triton widget's loader, gate and bridge, executably.
 *
 * Run: node scripts/test_triton_frame.mjs
 *
 * The real modules (`public/js/triton_frame/*.js` and `global_enhancements/triton_frame_gate.js`)
 * are imported and driven against a stand-in window: a small DOM, a clock that moves only when the
 * test says so, a recorded `fetch`, and a frame whose `src` counts how often it is written. Nothing
 * here is a copy of the code under test.
 *
 * WHAT IT GUARDS, in the order a person would be hurt by it:
 *
 * 1. Back and Forward. The frame's address is written once. A second write, or moving the frame
 *    to another parent, puts chat navigation into the tab's history list and Back stops moving the
 *    Desk. Nothing throws when that happens and no page looks wrong, so only a count catches it.
 * 2. A dead panel. If the frame never says `ready`, says it in a contract version this loader
 *    does not know, or cannot sign the person in, the classic widget has to take over, once, with
 *    the framed one fully gone (its listeners too: two widgets answering Alt+T open and close each
 *    other). And once the frame HAS signed in, nothing may take the chat away.
 * 3. Who is listened to. A Desk page gets `message` events from anything holding a reference to
 *    the window. Only Triton's origin, and only our own frame's window, may move the Desk, draw a
 *    clock-in card or trigger a sign-in ticket.
 * 4. What the frame can make the Desk do. A link is followed only to a page of this Desk, never
 *    to a URL; a clock-in request draws a card and clocks nobody in.
 * 5. The classic widget, untouched. With no `triton_frame` in boot the gate must fetch nothing and
 *    wrap nothing.
 * 6. The contract. The version and every message name in `contract.js` must be the ones written
 *    in `docs/triton-framed-widget.md`, and in Triton's own `docs/embed-bridge.md` where a Triton
 *    checkout (or TRITON_EMBED_BRIDGE_DOC) is at hand.
 *
 * Every assertion reads message names from contract.js rather than spelling them, so a contract
 * change is made in that one file and this suite follows.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { LOADER_CSS, LOADER_JS, LOAD_TIMEOUT_MS, installGate } from "../erpnext_enhancements/public/js/global_enhancements/triton_frame_gate.js";
import { createBridge } from "../erpnext_enhancements/public/js/triton_frame/bridge.js";
import { createClockIn, readParams } from "../erpnext_enhancements/public/js/triton_frame/clock_in.js";
import { CONTRACT_VERSION, ENVELOPE_KEY, FROM_FRAME, TO_FRAME, envelope, readMessage } from "../erpnext_enhancements/public/js/triton_frame/contract.js";
import {
	FRAME_ALLOW,
	MAX_MESSAGE,
	MAX_PROMPT,
	OWNER_CLASS,
	PAGE_POLL_MS,
	READY_TIMEOUT_MS,
	SIGN_IN_TIMEOUT_MS,
	SILENT_AFTER_LOAD_MS,
	TICKETS_PER_MINUTE,
	TICKET_METHOD,
	createLoader,
	validConfig,
} from "../erpnext_enhancements/public/js/triton_frame/loader.js";
import { SETTLE_MS, createPageSource, ownFilters } from "../erpnext_enhancements/public/js/triton_frame/page_source.js";
import { deskRoute } from "../erpnext_enhancements/public/js/triton_frame/record_link.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");
/** Executable code only: an absence check must not be tripped by the comment explaining it. */
const code = (rel) =>
	read(rel)
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.replace(/^\s*\/\/.*$/gm, "")
		.replace(/\s\/\/ .*$/gm, "");
const FRAME_FILES = ["loader.js", "bridge.js", "page_source.js", "record_link.js", "contract.js", "clock_in.js"];

const ORIGIN = "https://triton.example.com";
const CONFIG = Object.freeze({ origin: ORIGIN, url: ORIGIN + "/embed.html", page_context: 1 });

const tests = [];
const ok = (name, fn) => tests.push([name, fn]);
/** Let every already-resolved promise run. */
const settle = async () => {
	for (let i = 0; i < 12; i += 1) await Promise.resolve();
};

// ---------------------------------------------------------------------------
// The stand-in window
// ---------------------------------------------------------------------------
class Emitter {
	constructor() {
		this.handlers = {};
	}
	addEventListener(type, fn) {
		(this.handlers[type] = this.handlers[type] || []).push(fn);
	}
	removeEventListener(type, fn) {
		this.handlers[type] = (this.handlers[type] || []).filter((f) => f !== fn);
	}
	count(type) {
		return (this.handlers[type] || []).length;
	}
	emit(type, event) {
		(this.handlers[type] || []).slice().forEach((fn) => fn(event || {}));
	}
}

class Node extends Emitter {
	constructor(tag) {
		super();
		this.tagName = String(tag).toUpperCase();
		this.children = [];
		this.parentNode = null;
		this.attrs = {};
		this.className = "";
		this.textContent = "";
		this.hidden = false;
		this.appended = 0; // how many times this node was put into a parent
		this.focused = 0;
		this.props = {};
		this.style = { setProperty: (key, value) => (this.props[key] = value) };
		const self = this;
		this.classList = {
			list: () => self.className.split(/\s+/).filter(Boolean),
			contains: (c) => self.classList.list().includes(c),
			add: (c) => {
				if (!self.classList.contains(c)) self.className = self.classList.list().concat(c).join(" ");
			},
			remove: (c) => {
				self.className = self.classList.list().filter((x) => x !== c).join(" ");
			},
			toggle: (c, on) => {
				const want = on === undefined ? !self.classList.contains(c) : !!on;
				if (want) self.classList.add(c);
				else self.classList.remove(c);
			},
		};
	}
	get firstChild() {
		return this.children[0] || null;
	}
	setAttribute(key, value) {
		this.attrs[key] = String(value);
	}
	getAttribute(key) {
		return key in this.attrs ? this.attrs[key] : null;
	}
	appendChild(child) {
		if (child.parentNode) child.parentNode.removeChild(child);
		child.parentNode = this;
		child.appended += 1;
		this.children.push(child);
		return child;
	}
	removeChild(child) {
		this.children = this.children.filter((c) => c !== child);
		child.parentNode = null;
		return child;
	}
	focus() {
		this.focused += 1;
	}
	find(className) {
		for (const child of this.children) {
			if (child.classList.contains(className)) return child;
			const deep = child.find(className);
			if (deep) return deep;
		}
		return null;
	}
	/** Everything a person would read in this node. */
	words() {
		return [this.textContent].concat(this.children.map((c) => c.words())).filter(Boolean).join(" ");
	}
}

class Frame extends Node {
	constructor() {
		super("iframe");
		this.srcWrites = [];
		this.srcWrittenWhileAttached = false;
		this.posted = [];
		const posted = this.posted;
		this.contentWindow = { postMessage: (data, targetOrigin) => posted.push({ data, targetOrigin }) };
	}
	set src(value) {
		this.srcWrites.push(value);
		if (this.parentNode) this.srcWrittenWhileAttached = true;
	}
	get src() {
		return this.srcWrites[this.srcWrites.length - 1];
	}
	/** Messages the Desk posted, by type. */
	got(type) {
		return this.posted.filter((p) => p.data && p.data.type === type);
	}
}

function makeWindow(extra) {
	const win = new Emitter();
	const timers = [];
	let clock = 0;
	let nextId = 1;

	const doc = new Emitter();
	doc.documentElement = new Node("html");
	doc.body = new Node("body");
	doc.head = new Node("head");
	doc.activeElement = null;
	doc.hidden = false;
	doc.frames = [];
	doc.createElement = (tag) => {
		if (tag === "iframe") {
			const frame = new Frame();
			doc.frames.push(frame);
			return frame;
		}
		return new Node(tag);
	};

	win.document = doc;
	win.setTimeout = (fn, ms) => {
		const id = nextId++;
		timers.push({ id, at: clock + (ms || 0), fn });
		return id;
	};
	win.clearTimeout = (id) => {
		const i = timers.findIndex((timer) => timer.id === id);
		if (i >= 0) timers.splice(i, 1);
	};
	/** Move the clock, running every timer that comes due on the way (including ones they set). */
	win.advance = (ms) => {
		const until = clock + ms;
		for (;;) {
			const due = timers.filter((timer) => timer.at <= until).sort((a, b) => a.at - b.at)[0];
			if (!due) break;
			timers.splice(timers.indexOf(due), 1);
			clock = Math.max(clock, due.at);
			due.fn();
		}
		clock = until;
	};
	win.pendingTimers = () => timers.length;

	win.fetchCalls = [];
	win.ticketReply = { ok: true, ticket: "one-time-ticket", expires_in: 60 };
	// The Desk's own answer to "what is this project called": a test decides it.
	win.lookups = [];
	win.projectReply = { project_name: "Plaza fountain" };
	win.fetch = async (url, options) => {
		if (String(url).includes("frappe.client.get_value")) {
			win.lookups.push({ url, options });
			if (win.projectReply === "network") throw new Error("offline");
			if (typeof win.projectReply === "number") return { ok: false, status: win.projectReply, json: async () => ({}) };
			return { ok: true, status: 200, json: async () => ({ message: win.projectReply }) };
		}
		win.fetchCalls.push({ url, options });
		if (win.ticketReply === "network") throw new Error("offline");
		if (typeof win.ticketReply === "number") return { ok: false, status: win.ticketReply, json: async () => ({}) };
		return { ok: true, status: 200, json: async () => ({ message: win.ticketReply }) };
	};

	win.visualViewport = new Emitter();
	win.visualViewport.height = 700;
	win.visualViewport.offsetTop = 0;
	win.phone = false;
	win.matchMedia = () => ({ matches: win.phone });
	win.opened = [];
	win.open = (url, target, features) => win.opened.push([url, target, features]);

	// The device's location service: a test decides what it answers, and when.
	win.geo = { asked: [], answer: { coords: { latitude: 40.5, longitude: -111.9, accuracy: 9 } } };
	win.navigator = {
		geolocation: {
			getCurrentPosition: (success, failure, options) => {
				win.geo.asked.push(options);
				if (win.geo.answer) success(win.geo.answer);
				else failure({ code: 1 });
			},
		},
	};

	win.routes = [];
	win.alerts = [];
	win.calls = [];
	win.callReply = { status: "success", message: "Started", offsite: { flagged: 0 } };
	const router = new Emitter();
	router.on = (type, fn) => router.addEventListener(type, fn);
	router.off = (type, fn) => router.removeEventListener(type, fn);
	win.frappe = {
		csrf_token: "csrf-abc",
		session: { user: "pat@example.com" },
		boot: { user: { can_read: ["Sales Order", "Project", "Customer"] } },
		router: router,
		set_route: (route) => win.routes.push(route),
		show_alert: (alert) => win.alerts.push(alert),
		xcall: async (method, args) => {
			win.calls.push({ method, args });
			if (win.callReply instanceof Error) throw win.callReply;
			return win.callReply;
		},
		assets: { bundled_asset: (name) => "/assets/erpnext_enhancements/dist/" + name.replace(".bundle.", ".bundle.HASH.") },
	};
	win.logs = [];
	win.console = { info: (line) => win.logs.push(String(line)) };
	return Object.assign(win, extra || {});
}

/** A loader on a fresh window, with what it gave up with (if it did). */
function makeLoader(options) {
	const opts = options || {};
	const win = opts.win || makeWindow();
	const gaveUp = [];
	const page = { ref: opts.page === undefined ? { type: "document", doctype: "Sales Order", name: "SO-0001" } : opts.page };
	const loader = createLoader({
		win: win,
		config: opts.config || CONFIG,
		giveUp: (reason, detail) => gaveUp.push({ reason, detail }),
		detect: () => page.ref,
	});
	const fab = win.document.body.children.find((n) => n.classList.contains("triton-fab"));
	const panel = win.document.body.children.find((n) => n.classList.contains("triton-panel"));
	const frame = () => win.document.frames[0] || null;
	/** A message as the browser would deliver it from our frame. */
	const fromFrame = (type, fields, overrides) => {
		const data = Object.assign({}, fields || {}, { [ENVELOPE_KEY]: CONTRACT_VERSION, type: type });
		win.emit("message", Object.assign({ origin: ORIGIN, source: frame().contentWindow, data: data }, overrides || {}));
	};
	const ready = () => fromFrame(FROM_FRAME.READY, { version: CONTRACT_VERSION });
	/** Open the panel and let the frame say `ready`; the ticket, page and visibility go out. */
	const openAndReady = async () => {
		loader.toggle(true);
		ready();
		await settle();
	};
	/** The same, and the frame then signs in, as it does when the ticket is good. */
	const openAndSignIn = async () => {
		await openAndReady();
		fromFrame(FROM_FRAME.SIGNED_IN);
	};
	const card = () => win.document.body.children.find((n) => n.classList.contains("triton-frame-clockin")) || null;
	return { win, loader, gaveUp, page, fab, panel, frame, fromFrame, ready, openAndReady, openAndSignIn, card };
}

// ---------------------------------------------------------------------------
// 1. The frame's address is assigned once
// ---------------------------------------------------------------------------
ok("no frame exists until the panel is first opened", () => {
	const { win, fab, panel } = makeLoader();
	assert.ok(fab, "the button is built");
	assert.ok(panel, "the panel is built");
	assert.equal(win.document.frames.length, 0, "a person who never opens Triton never loads its page");
	assert.equal(win.fetchCalls.length, 0, "and never asks for a ticket");
});

ok("the button and the panel carry the classic widget's classes", () => {
	const { fab, panel } = makeLoader();
	assert.ok(fab.classList.contains("triton-fab"));
	assert.ok(panel.classList.contains("triton-panel"));
	assert.equal(fab.getAttribute("aria-expanded"), "false");
});

ok("the frame gets its address, and the permissions it needs, before it joins the page", () => {
	const { loader, frame, panel } = makeLoader();
	loader.toggle(true);
	assert.deepEqual(frame().srcWrites, [CONFIG.url]);
	assert.equal(frame().srcWrittenWhileAttached, false, "src was written to an attached frame");
	assert.equal(frame().getAttribute("allow"), FRAME_ALLOW);
	assert.equal(FRAME_ALLOW, "clipboard-write; microphone", "camera and geolocation are not the frame's to have");
	assert.equal(frame().getAttribute("referrerpolicy"), "origin");
	assert.equal(frame().getAttribute("sandbox"), null);
	assert.equal(frame().parentNode, panel);
});

ok("the address is written ONCE across open, close, reopen, route changes, ready, sign-in and every message", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.loader.toggle(false);
	t.loader.toggle(true);
	t.loader.toggle();
	t.loader.toggle();
	t.page.ref = { type: "document", doctype: "Project", name: "PROJ-0002" };
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "expired" });
	await settle();
	t.ready(); // the page in the frame reloaded itself after a Triton deploy
	await settle();
	t.loader.ask("Draft a course", { type: "document", doctype: "Customer", name: "C-1" });
	t.fromFrame(FROM_FRAME.RESIZE, { mode: "wide" });
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/desk/project/PROJ-0002" });
	t.fromFrame(FROM_FRAME.KEY, { key: "alt+t" });
	t.fromFrame(FROM_FRAME.KEY, { key: "alt+t" });
	t.fromFrame(FROM_FRAME.CLOSE);
	t.frame().emit("load");
	await settle();

	assert.equal(t.win.document.frames.length, 1, "a second frame was created");
	assert.equal(t.frame().srcWrites.length, 1, "the frame's address was written again: " + JSON.stringify(t.frame().srcWrites));
	assert.equal(t.frame().appended, 1, "the frame was moved to another parent, which reloads it");
	assert.equal(t.gaveUp.length, 0);
});

ok("the loader's code has one `src` assignment and touches no history or frame location", () => {
	const all = FRAME_FILES.map((f) => code("erpnext_enhancements/public/js/triton_frame/" + f)).join("\n");
	assert.equal((all.match(/\.src\s*=[^=]/g) || []).length, 1, "exactly one assignment to .src");
	for (const banned of ["pushState", "replaceState", "contentWindow.location", "location.replace", "location.assign", "location.href", "location.reload"]) {
		assert.ok(!all.includes(banned), banned + " appears in the framed loader");
	}
	assert.ok(!/postMessage\([^)]*["']\*["']/.test(all), 'a message is posted to "*"');
});

// ---------------------------------------------------------------------------
// 2. Fallback
// ---------------------------------------------------------------------------
ok("a frame that never says ready is given up on after READY_TIMEOUT_MS, and not sooner", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	assert.equal(READY_TIMEOUT_MS, 20000, "the contract asks for about 20 seconds: a cold load on a phone is slow");
	t.win.advance(READY_TIMEOUT_MS - 1);
	assert.equal(t.gaveUp.length, 0, "gave up early");
	assert.ok(/Connecting to Triton/.test(t.panel.words()), "the wait is not explained");
	t.win.advance(1);
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.gaveUp[0].reason, "timeout");
	assert.equal(t.gaveUp[0].detail.open, true, "the classic panel must open in its place");
});

ok("a page that finished loading and stays silent is given up on sooner", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.win.advance(1000);
	t.frame().emit("load"); // a refused frame, an error page or a sign-in page still fires load
	t.frame().emit("load");
	t.win.advance(SILENT_AFTER_LOAD_MS - 1);
	assert.equal(t.gaveUp.length, 0);
	t.win.advance(1);
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.gaveUp[0].reason, "silent");
	assert.ok(SILENT_AFTER_LOAD_MS < READY_TIMEOUT_MS);
});

ok("ready after load cancels both waits", async () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.frame().emit("load");
	t.ready();
	await settle();
	t.fromFrame(FROM_FRAME.SIGNED_IN);
	t.frame().emit("load"); // a later load (the page reloading itself) starts nothing
	t.win.advance(READY_TIMEOUT_MS * 5);
	assert.equal(t.gaveUp.length, 0);
});

ok("giving up removes the button, the panel, the frame, the clock-in card and every listener", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0002" } });
	assert.ok(t.card());
	t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: "bad-ticket" });
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.win.document.body.children.length, 0, "something of the framed widget is still on the page");
	assert.equal(t.win.count("message"), 0, "still listening for messages");
	assert.equal(t.win.document.count("keydown"), 0, "still answering Alt+T: two widgets would open and close each other");
	assert.equal(t.win.visualViewport.count("resize"), 0);
	assert.equal(t.win.frappe.router.count("change"), 0);
	assert.equal(t.win.pendingTimers(), 0, "a timer of the dead widget is still running");
});

ok("the frame's rules about the classic widget's own classes stop applying after a hand-back", () => {
	const t = makeLoader();
	const html = t.win.document.documentElement;
	assert.ok(html.classList.contains(OWNER_CLASS), "the loader does not mark the page as its own");
	t.loader.toggle(true);
	t.win.advance(READY_TIMEOUT_MS);
	assert.equal(t.gaveUp.length, 1);
	assert.ok(!html.classList.contains(OWNER_CLASS), "the mark stayed after the classic widget took over");

	// The stylesheet stays loaded after a hand-back. Every rule in it that names one of the
	// classic widget's own button classes must sit under that mark, or it would restyle the
	// classic button (hide it, on a phone, while the classic panel is open).
	const css = read("erpnext_enhancements/public/css/triton_frame.bundle.css").replace(/\/\*[\s\S]*?\*\//g, "");
	const selectors = [...css.matchAll(/([^{}]+)\{/g)].map((m) => m[1].trim()).filter((s) => !s.startsWith("@"));
	const onClassic = selectors.filter((s) => /\.triton-fab\b/.test(s));
	assert.ok(onClassic.length >= 2, "the unread dot and the phone rule are gone");
	for (const selector of onClassic) {
		assert.ok(selector.startsWith("." + OWNER_CLASS + " "), "not scoped to the loader: " + selector);
	}
	// Everything else in the sheet is about elements only the framed widget creates.
	for (const selector of selectors.filter((s) => !/\.triton-fab\b/.test(s))) {
		assert.ok(/\.triton-frame/.test(selector), "a rule that can reach the classic widget: " + selector);
	}
});

ok("giving up happens once, and the dead loader does nothing afterwards", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	const frame = t.frame();
	t.win.advance(READY_TIMEOUT_MS);
	t.win.advance(READY_TIMEOUT_MS * 3);
	t.loader.toggle(true);
	assert.equal(t.loader.ask("hello"), false);
	t.win.emit("message", { origin: ORIGIN, source: frame.contentWindow, data: envelope(FROM_FRAME.READY, { version: CONTRACT_VERSION }) });
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.win.document.body.children.length, 0);
});

ok("ready in a contract version this loader does not know hands back at once", () => {
	// In the envelope (a `ready` is read whatever number it carries), and in its own field.
	for (const data of [
		{ [ENVELOPE_KEY]: CONTRACT_VERSION + 1, type: FROM_FRAME.READY, version: CONTRACT_VERSION + 1 },
		{ [ENVELOPE_KEY]: CONTRACT_VERSION, type: FROM_FRAME.READY, version: CONTRACT_VERSION + 1 },
		{ [ENVELOPE_KEY]: CONTRACT_VERSION + 1, type: FROM_FRAME.READY, version: CONTRACT_VERSION },
		{ [ENVELOPE_KEY]: CONTRACT_VERSION, type: FROM_FRAME.READY },
		{ [ENVELOPE_KEY]: CONTRACT_VERSION, type: FROM_FRAME.READY, version: "1" },
	]) {
		const t = makeLoader();
		t.loader.toggle(true);
		t.win.emit("message", { origin: ORIGIN, source: t.frame().contentWindow, data: data });
		assert.equal(t.gaveUp.length, 1, "not handed back for " + JSON.stringify(data));
		assert.equal(t.gaveUp[0].reason, "contract");
		assert.equal(t.win.fetchCalls.length, 0, "a ticket was requested for a page we cannot drive");
	}
});

ok("sign-in-failed before the frame has ever signed in hands back to the classic widget", async () => {
	for (const reason of ["no-answer", "refused", "bad-ticket", "no-config", "something new", undefined]) {
		const t = makeLoader();
		await t.openAndReady();
		t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: reason });
		assert.equal(t.gaveUp.length, 1, "stayed framed on " + reason);
		assert.ok(t.gaveUp[0].reason.startsWith("sign-in-failed"));
		assert.equal(t.gaveUp[0].detail.open, true);
	}
	// What the frame said is not echoed into the console line unless it is one of the known words.
	const t = makeLoader();
	await t.openAndReady();
	t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: "<img src=x onerror=alert(1)>" });
	assert.equal(t.gaveUp[0].reason, "sign-in-failed, unknown");
});

ok("no word about sign-in after ready is a dead frame, and hands back", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.win.advance(SIGN_IN_TIMEOUT_MS - 1);
	assert.equal(t.gaveUp.length, 0);
	t.win.advance(1);
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.gaveUp[0].reason, "no-sign-in");
});

ok("once the frame has signed in, nothing takes the chat away", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	assert.equal(t.loader.isSignedIn(), true);
	t.win.advance(SIGN_IN_TIMEOUT_MS * 10);
	// The token ran out and no new ticket could be had: the frame shows the reason and Try again.
	t.win.ticketReply = { ok: false, reason: "unreachable", message: "Triton could not be reached. Try again in a minute." };
	t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "expired" });
	await settle();
	t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: "refused" });
	// A Triton that lost its ticket route mid-session does not swap a running chat out either.
	t.win.ticketReply = { ok: false, reason: "unsupported", message: "This Triton does not offer the framed assistant yet." };
	t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "retry" });
	await settle();
	// The page in the frame reloads itself after a deploy and signs in again, slowly.
	t.ready();
	await settle();
	t.win.advance(SIGN_IN_TIMEOUT_MS * 2);
	assert.equal(t.gaveUp.length, 0, "a working chat was replaced by the classic widget");
	assert.equal(t.frame().got(TO_FRAME.TICKET_ERROR).length, 3, "the frame was not told why it got no ticket");
});

ok("a prompt that was waiting goes with the hand-back", () => {
	const t = makeLoader();
	t.loader.ask("Draft a course", { type: "document", doctype: "Customer", name: "C-1" });
	t.win.advance(READY_TIMEOUT_MS);
	assert.deepEqual(t.gaveUp[0].detail.ask, {
		prompt: "Draft a course",
		context: { type: "document", doctype: "Customer", name: "C-1" },
	});
});

ok("a prompt the frame was holding when sign-in failed goes with the hand-back; one it has shown does not", async () => {
	const t = makeLoader();
	t.loader.ask("Draft a course", { type: "document", doctype: "Customer", name: "C-1" });
	t.ready();
	await settle();
	assert.equal(t.frame().got(TO_FRAME.ASK).length, 1);
	t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: "bad-ticket" });
	assert.deepEqual(t.gaveUp[0].detail.ask, { prompt: "Draft a course", context: { type: "document", doctype: "Customer", name: "C-1" } });

	// Signed in, the prompt is in the frame's message box. A later ready that goes nowhere must
	// not drop the same prompt into the classic widget as well.
	const shown = makeLoader();
	shown.loader.ask("Draft a course");
	await shown.openAndSignIn();
	shown.win.ticketReply = { ok: false, reason: "unsupported", message: "" };
	shown.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "expired" });
	await settle();
	assert.equal(shown.gaveUp.length, 0);
	assert.equal(shown.frame().got(TO_FRAME.ASK).length, 1);
});

ok("Triton having no ticket route hands back; any other ticket failure is said to the frame", async () => {
	const missing = makeLoader();
	missing.win.ticketReply = { ok: false, reason: "unsupported", message: "no" };
	await missing.openAndReady();
	assert.equal(missing.gaveUp.length, 1);
	assert.equal(missing.gaveUp[0].reason, "no-ticket-route");

	const long = "Triton could not be reached. " + "Try again in a minute. ".repeat(40);
	for (const reply of [{ ok: false, reason: "unreachable", message: long }, { ok: false, reason: "setup", message: "" }, "network", 403, 500]) {
		const t = makeLoader();
		t.win.ticketReply = reply;
		await t.openAndReady();
		assert.equal(t.gaveUp.length, 0, "gave up on " + JSON.stringify(reply) + " before the frame said so");
		assert.equal(t.frame().got(TO_FRAME.TICKET).length, 0);
		const errors = t.frame().got(TO_FRAME.TICKET_ERROR);
		assert.equal(errors.length, 1, "the frame was left to wait out its 15 seconds");
		assert.deepEqual(Object.keys(errors[0].data).sort(), ["message", ENVELOPE_KEY, "type"].sort());
		assert.ok(errors[0].data.message.length > 10 && errors[0].data.message.length <= MAX_MESSAGE);
		assert.equal(MAX_MESSAGE, 300);
		// The Desk does not also cover the frame with words of its own.
		assert.equal(t.panel.find("triton-frame-shell").hidden, true);
		assert.ok(t.panel.classList.contains("triton-frame-live"));
	}
});

// ---------------------------------------------------------------------------
// 3. Who is listened to, and who is posted to
// ---------------------------------------------------------------------------
ok("a message from any other origin is dropped, even from our own frame's window", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	for (const origin of ["https://evil.example.com", "https://triton.example.com.evil.test", "http://triton.example.com", "null", "", ORIGIN + "/", undefined]) {
		t.fromFrame(FROM_FRAME.READY, { version: CONTRACT_VERSION }, { origin: origin });
	}
	assert.equal(t.loader.isReady(), false);
	assert.equal(t.win.fetchCalls.length, 0);
	t.win.advance(READY_TIMEOUT_MS);
	assert.equal(t.gaveUp[0].reason, "timeout", "a foreign ready stopped the fallback");
});

ok("a message from Triton's origin but another window is dropped", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	for (const source of [{ postMessage() {} }, null, undefined, t.win]) {
		t.fromFrame(FROM_FRAME.READY, { version: CONTRACT_VERSION }, { source: source });
	}
	assert.equal(t.loader.isReady(), false);
	assert.equal(t.win.fetchCalls.length, 0);
});

ok("after ready, the wrong origin or the wrong window still cannot move the Desk, clock in or close the panel", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	const tries = [
		[FROM_FRAME.OPEN_RECORD, { path: "/desk/project/PROJ-0002" }],
		[FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0002" } }],
		[FROM_FRAME.CLOSE, {}],
		[FROM_FRAME.KEY, { key: "escape" }],
		[FROM_FRAME.NEED_TICKET, { reason: "retry" }],
		[FROM_FRAME.RESIZE, { mode: "wide" }],
		[FROM_FRAME.REPLY_FINISHED, { chat_id: 1, title: "x" }],
	];
	const calls = t.win.fetchCalls.length;
	for (const [type, fields] of tries) {
		t.fromFrame(type, fields, { origin: "https://evil.example.com" });
		t.fromFrame(type, fields, { source: { postMessage() {} } });
	}
	await settle();
	assert.deepEqual(t.win.routes, []);
	assert.equal(t.card(), null);
	assert.equal(t.loader.isOpen(), true);
	assert.equal(t.win.fetchCalls.length, calls);
	assert.ok(!t.panel.classList.contains("triton-frame-wide"));
	assert.deepEqual(t.win.alerts, []);
});

ok("data that is not a bridge message is dropped", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	for (const data of [null, undefined, "ready", 1, [], {}, { type: FROM_FRAME.READY, version: 1 }, { [ENVELOPE_KEY]: "1", type: FROM_FRAME.READY, version: 1 }, { [ENVELOPE_KEY]: 1 }, { [ENVELOPE_KEY]: 1, type: 7 }]) {
		t.win.emit("message", { origin: ORIGIN, source: t.frame().contentWindow, data: data });
	}
	assert.equal(t.loader.isReady(), false);
	assert.equal(t.gaveUp.length, 0);
	assert.equal(readMessage({ [ENVELOPE_KEY]: 1, type: "x" }).type, "x");
});

ok("a message type or field the loader does not know is ignored without an error", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	const posted = t.frame().posted.length;
	t.fromFrame("a-type-from-a-later-contract", { anything: { at: "all" } });
	t.fromFrame(FROM_FRAME.RESIZE, { mode: "fullscreen" });
	t.fromFrame(FROM_FRAME.KEY, { key: "f1" });
	t.fromFrame(FROM_FRAME.KEY, {});
	t.fromFrame(FROM_FRAME.SIGNED_IN, { a_new_field: true });
	await settle();
	assert.equal(t.gaveUp.length, 0);
	assert.equal(t.loader.isOpen(), true);
	assert.equal(t.frame().posted.length, posted, "the Desk answered a message it does not understand");
	assert.ok(!t.panel.classList.contains("triton-frame-wide"));
});

ok("nothing but ready is acted on before ready, and nothing is said to the frame before it", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/desk/sales-order/SO-0001" });
	t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "retry" });
	t.fromFrame(FROM_FRAME.CLOSE);
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0002" } });
	t.fromFrame(FROM_FRAME.SIGN_IN_FAILED, { reason: "no-config" });
	t.loader.toggle(false);
	t.loader.toggle(true);
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	assert.equal(t.win.routes.length, 0);
	assert.equal(t.win.fetchCalls.length, 0);
	assert.equal(t.card(), null);
	assert.equal(t.gaveUp.length, 0);
	assert.equal(t.frame().posted.length, 0, "the frame was spoken to before it was listening");
});

ok("a message in another version is dropped once the frame is ready", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.win.emit("message", {
		origin: ORIGIN,
		source: t.frame().contentWindow,
		data: { [ENVELOPE_KEY]: CONTRACT_VERSION + 1, type: FROM_FRAME.OPEN_RECORD, path: "/desk/sales-order/SO-0001" },
	});
	assert.equal(t.win.routes.length, 0);
});

ok("every message to the frame names Triton's origin as its target", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.loader.ask("hello");
	t.loader.toggle(false);
	assert.ok(t.frame().posted.length >= 4);
	for (const post of t.frame().posted) {
		assert.equal(post.targetOrigin, ORIGIN);
		assert.equal(post.data[ENVELOPE_KEY], CONTRACT_VERSION);
	}
});

ok("the bridge counts what it drops and stops listening when told", () => {
	const win = new Emitter();
	const frameWindow = { postMessage() {} };
	const seen = [];
	const bridge = createBridge({ win, origin: ORIGIN, frameWindow: () => frameWindow, onMessage: (m) => seen.push(m) });
	bridge.start();
	bridge.start();
	assert.equal(win.count("message"), 1);
	win.emit("message", { origin: "https://evil.example.com", source: frameWindow, data: envelope("x") });
	win.emit("message", { origin: ORIGIN, source: frameWindow, data: envelope("x") });
	assert.deepEqual([seen.length, bridge.stats.dropped, bridge.stats.accepted], [1, 1, 1]);
	bridge.stop();
	assert.equal(win.count("message"), 0);
});

ok("envelope fields cannot overwrite the version or the type", () => {
	const message = envelope("page", { type: "ticket", [ENVELOPE_KEY]: 99, page: null });
	assert.equal(message.type, "page");
	assert.equal(message[ENVELOPE_KEY], CONTRACT_VERSION);
});

// ---------------------------------------------------------------------------
// Signing in
// ---------------------------------------------------------------------------
ok("ready is answered with the ticket, then the page, then visibility, and the panel becomes the frame's", async () => {
	const t = makeLoader();
	await t.openAndReady();
	assert.equal(t.win.fetchCalls.length, 1);
	const call = t.win.fetchCalls[0];
	assert.equal(call.url, "/api/method/" + TICKET_METHOD);
	assert.equal(call.options.method, "POST");
	assert.equal(call.options.headers["X-Frappe-CSRF-Token"], "csrf-abc");
	assert.equal(call.options.credentials, "same-origin");
	assert.equal(call.options.redirect, "error");

	const types = t.frame().posted.map((p) => p.data.type);
	assert.deepEqual(types, [TO_FRAME.TICKET, TO_FRAME.PAGE, TO_FRAME.VISIBILITY]);
	assert.equal(t.frame().got(TO_FRAME.TICKET)[0].data.ticket, "one-time-ticket");
	assert.deepEqual(t.frame().got(TO_FRAME.PAGE)[0].data.page, { type: "document", doctype: "Sales Order", name: "SO-0001" });
	assert.equal(t.frame().got(TO_FRAME.VISIBILITY)[0].data.open, true);
	// The Desk's own header is gone the moment the frame is ready: the frame has its own.
	assert.equal(t.panel.find("triton-frame-shell").hidden, true);
	assert.ok(t.panel.classList.contains("triton-frame-live"));
});

ok("the ticket is not kept, logged or written anywhere by the loader", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	assert.ok(!JSON.stringify(t.win.logs).includes("one-time-ticket"));
	assert.ok(!JSON.stringify(t.win.alerts).includes("one-time-ticket"));
	for (const file of FRAME_FILES) {
		const src = code("erpnext_enhancements/public/js/triton_frame/" + file);
		assert.ok(!/console\./.test(src), file + " writes to the console");
		assert.ok(!/localStorage|sessionStorage|document\.cookie|indexedDB/.test(src), file + " touches browser storage");
	}
});

ok("need-ticket gets a new ticket, and a frame stuck asking is cut off", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	for (let i = 0; i < TICKETS_PER_MINUTE + 5; i += 1) {
		t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "expired" });
		await settle();
	}
	assert.equal(t.win.fetchCalls.length, TICKETS_PER_MINUTE, "the server was asked more often than the limit");
	assert.ok(t.frame().got(TO_FRAME.TICKET_ERROR).length >= 1, "the frame was not told why it got no ticket");
	assert.equal(t.gaveUp.length, 0);
});

ok("a new ticket for a closed panel does not take the keyboard away from the Desk", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.loader.toggle(false);
	const before = t.frame().focused;
	t.fromFrame(FROM_FRAME.NEED_TICKET, { reason: "expired" });
	await settle();
	assert.equal(t.frame().got(TO_FRAME.TICKET).length, 2, "the closed panel was not signed in again");
	assert.equal(t.frame().focused, before, "a hidden frame was focused");
});

ok("two requests at once share one call", async () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.ready();
	t.ready();
	await settle();
	assert.equal(t.win.fetchCalls.length, 1);
});

ok("with page context switched off the frame is told there is no page, once, and nothing more", async () => {
	const t = makeLoader({ config: Object.assign({}, CONFIG, { page_context: 0 }) });
	await t.openAndSignIn();
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS + PAGE_POLL_MS * 3);
	t.loader.toggle(false);
	t.loader.toggle(true);
	const pages = t.frame().got(TO_FRAME.PAGE).map((p) => p.data.page);
	assert.deepEqual(pages, [null]);
	assert.equal(t.win.frappe.router.count("change"), 0);
});

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------
ok("the button, Alt+T and the frame's own close and key messages open and close the panel", async () => {
	const t = makeLoader();
	const key = (extra) => {
		const event = Object.assign({ key: "t", altKey: true, prevented: false, preventDefault() { this.prevented = true; } }, extra || {});
		t.win.document.emit("keydown", event);
		return event;
	};
	t.fab.emit("click");
	assert.equal(t.loader.isOpen(), true);
	assert.ok(t.panel.classList.contains("triton-visible"));
	assert.ok(t.fab.classList.contains("triton-fab-open"));
	assert.equal(t.fab.getAttribute("aria-expanded"), "true");

	assert.equal(key().prevented, true);
	assert.equal(t.loader.isOpen(), false);
	key({ key: "T" });
	assert.equal(t.loader.isOpen(), true);

	// AltGr is ctrlKey+altKey on Windows and many European layouts: it types a character.
	assert.equal(key({ ctrlKey: true }).prevented, false);
	key({ metaKey: true });
	key({ altKey: false });
	assert.equal(t.loader.isOpen(), true);

	t.ready();
	await settle();
	t.fromFrame(FROM_FRAME.SIGNED_IN);
	const visible = () => t.frame().got(TO_FRAME.VISIBILITY).map((p) => p.data.open);
	assert.deepEqual(visible(), [true]);

	t.fromFrame(FROM_FRAME.CLOSE);
	assert.equal(t.loader.isOpen(), false);
	t.fromFrame(FROM_FRAME.KEY, { key: "alt+t" });
	assert.equal(t.loader.isOpen(), true);
	t.fromFrame(FROM_FRAME.KEY, { key: "escape" });
	assert.equal(t.loader.isOpen(), false);
	t.fromFrame(FROM_FRAME.KEY, { key: "escape" }); // already closed: nothing to say
	assert.equal(t.loader.isOpen(), false);
	assert.deepEqual(visible(), [true, false, true, false]);
});

ok("the waiting panel has a Close that works before the frame is ready", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	const close = t.panel.find("triton-close");
	assert.ok(close, "a phone would be stuck under a blank full-screen sheet");
	close.emit("click");
	assert.equal(t.loader.isOpen(), false);
});

ok("resize makes the panel wide and narrow again, and only for the two modes the contract names", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.RESIZE, { mode: "wide" });
	assert.ok(t.panel.classList.contains("triton-frame-wide"));
	t.fromFrame(FROM_FRAME.RESIZE, { mode: "sideways" });
	assert.ok(t.panel.classList.contains("triton-frame-wide"), "an unknown mode changed the size");
	t.fromFrame(FROM_FRAME.RESIZE, { mode: "panel" });
	assert.ok(!t.panel.classList.contains("triton-frame-wide"));
	const css = read("erpnext_enhancements/public/css/triton_frame.bundle.css");
	assert.ok(/\.triton-frame-panel\.triton-frame-wide\s*\{[^}]*width:\s*min\(1120px/.test(css), "the stylesheet has no wide size");
});

ok("the phone sheet follows the visible screen while the panel is open", () => {
	const t = makeLoader();
	t.win.visualViewport.height = 420;
	t.win.visualViewport.offsetTop = 12;
	t.win.visualViewport.emit("resize");
	assert.deepEqual(t.panel.props, {}, "sized while closed");
	t.loader.toggle(true);
	assert.equal(t.panel.props["--triton-sheet-height"], "420px");
	assert.equal(t.panel.props["--triton-sheet-top"], "12px");
	// The keyboard comes up: 300px of screen goes away.
	t.win.visualViewport.height = 120;
	t.win.visualViewport.emit("resize");
	assert.equal(t.panel.props["--triton-sheet-height"], "120px");
	t.win.visualViewport.offsetTop = 40;
	t.win.visualViewport.emit("scroll");
	assert.equal(t.panel.props["--triton-sheet-top"], "40px");

	const css = read("erpnext_enhancements/public/css/triton_frame.bundle.css");
	assert.ok(css.includes("var(--triton-sheet-height"), "the stylesheet does not read the height the loader sets");
	assert.ok(css.includes("var(--triton-sheet-top"));
});

ok("closing a panel that had the keyboard focus gives focus back to the button", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	assert.ok(t.frame().focused >= 1, "focus did not go to the chat on open");
	t.win.document.activeElement = t.frame();
	t.loader.toggle(false);
	assert.equal(t.fab.focused, 1);
});

ok("a reply that finished out of sight is noted on the Desk; one the person is looking at is not", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.REPLY_FINISHED, { chat_id: 412, title: "Plaza fountain pump" });
	assert.deepEqual(t.win.alerts, [], "the person was told about a reply they were watching arrive");
	assert.ok(!t.fab.classList.contains("triton-fab-unread"));

	t.loader.toggle(false);
	t.fromFrame(FROM_FRAME.REPLY_FINISHED, { chat_id: 412, title: 'Pump <b>sizing</b> & "notes"' });
	assert.equal(t.win.alerts.length, 1);
	assert.ok(t.fab.classList.contains("triton-fab-unread"));
	// show_alert renders HTML: a title is text a person or a model wrote.
	assert.ok(!/[<>]/.test(t.win.alerts[0].message), "the chat's title reached show_alert unescaped: " + t.win.alerts[0].message);
	assert.ok(t.win.alerts[0].message.includes("Pump"));

	t.loader.toggle(true);
	assert.ok(!t.fab.classList.contains("triton-fab-unread"), "the dot stayed after the panel was opened");
	t.win.document.hidden = true; // the tab is in the background
	t.fromFrame(FROM_FRAME.REPLY_FINISHED, { chat_id: null, title: 7 });
	assert.equal(t.win.alerts.length, 2);
	assert.ok(!/undefined|null|7/.test(t.win.alerts[1].message));
});

// ---------------------------------------------------------------------------
// 4. What the frame can make the Desk do: links
// ---------------------------------------------------------------------------
ok("open-record goes through frappe.set_route by URL, and closes the sheet on a phone", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/desk/sales-order/SO-0001" });
	assert.deepEqual(t.win.routes, ["/desk/sales-order/SO-0001"]);
	assert.equal(t.loader.isOpen(), true, "closed on a desktop, where the record shows beside the panel");
	assert.deepEqual(t.win.opened, [], "a tab was opened for a page the Desk could route");

	// A workspace segment, a query and a fragment: the Desk's own router parses its own address.
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/desk/stock/item/ITEM-0001?tab=details#stock" });
	assert.equal(t.win.routes[1], "/desk/stock/item/ITEM-0001?tab=details");
	assert.equal(t.win.frappe.route_hash, "#stock", "the fragment was dropped");

	t.win.phone = true;
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/app/project/PROJ-0002" });
	assert.equal(t.win.routes[2], "/app/project/PROJ-0002");
	assert.equal(t.loader.isOpen(), false);
});

ok("a path the Desk cannot route opens in a new tab, never in the frame", async () => {
	const t = makeLoader();
	t.win.frappe.set_route = () => {
		throw new Error("router is not up");
	};
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/desk/project/PROJ-0002#notes" });
	assert.deepEqual(t.win.opened, [["/desk/project/PROJ-0002#notes", "_blank", "noopener"]]);
	assert.equal(t.frame().srcWrites.length, 1);

	const bare = makeLoader();
	delete bare.win.frappe.set_route;
	await bare.openAndSignIn();
	bare.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "https://evil.example.com/desk/x" });
	assert.deepEqual(bare.win.opened, [], "a refused path was opened in a tab");
});

const REFUSED_PATHS = [
	["a full URL", "https://evil.example.com/desk/sales-order/SO-0001"],
	["a full URL to this very Desk", "https://erp.example.com/desk/sales-order/SO-0001"],
	["a protocol-relative value", "//evil.example.com/desk/sales-order/SO-0001"],
	["a backslash a browser reads as a slash", "/\\evil.example.com/desk/x"],
	["a backslash inside the path", "/desk\\..\\api\\method\\logout"],
	["javascript:", "javascript:alert(1)"],
	["javascript: dressed as a path", "/desk/javascript:alert(1)//x"],
	["data:", "data:text/html,<script>alert(1)</script>"],
	["a path outside the Desk", "/api/method/logout"],
	["a file", "/files/payroll.xlsx"],
	["the site root", "/"],
	["a name that only starts like the Desk", "/desktop/sales-order/SO-0001"],
	["a name that only starts like /app", "/application/x"],
	["a climb out of the Desk", "/desk/../api/method/logout"],
	["an encoded climb out of the Desk", "/desk/sales-order/%2e%2e/%2e%2e/api/method/logout"],
	["a doubled slash", "/desk//evil.example.com/x"],
	["a relative path", "desk/sales-order/SO-0001"],
	["a path with a space in front", " /desk/sales-order/SO-0001"],
	["a control character", "/desk/sales-order/SO\u0000-1"],
	["a newline", "/desk/sales-order/SO-1\n/x"],
	["angle brackets", "/desk/<script>"],
	["an empty string", ""],
	["a number", 42],
	["null", null],
	["nothing at all", undefined],
	["an object", { path: "/desk/x" }],
	["an array", ["/desk/x"]],
	["a path far longer than any Desk address", "/desk/" + "a".repeat(2100)],
];

for (const [what, value] of REFUSED_PATHS) {
	ok("open-record refuses " + what, async () => {
		assert.equal(deskRoute(value), null);
		const t = makeLoader();
		await t.openAndSignIn();
		t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: value });
		assert.deepEqual(t.win.routes, [], "the Desk was moved");
		assert.deepEqual(t.win.opened, [], "a tab was opened");
		assert.equal(t.loader.isOpen(), true);
	});
}

ok("open-record ignores every field but path", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { url: "https://evil.example.com", href: "/desk/x", doctype: "User", name: "Administrator" });
	assert.deepEqual(t.win.routes, []);
	assert.deepEqual(t.win.opened, []);
});

ok("deskRoute: what is accepted, and what it hands the router", () => {
	assert.deepEqual(deskRoute("/desk/sales-order/SO-0001"), { route: "/desk/sales-order/SO-0001", hash: "" });
	assert.deepEqual(deskRoute("/app/sales-order/SO-0001"), { route: "/app/sales-order/SO-0001", hash: "" });
	assert.deepEqual(deskRoute("/desk"), { route: "/desk", hash: "" });
	assert.deepEqual(deskRoute("/desk/"), { route: "/desk/", hash: "" });
	assert.deepEqual(deskRoute("/desk/sales-order?status=Draft#x"), { route: "/desk/sales-order?status=Draft", hash: "#x" });
	assert.deepEqual(deskRoute("/desk/item/ITEM%2F0001%20A"), { route: "/desk/item/ITEM%2F0001%20A", hash: "" });
	assert.deepEqual(deskRoute("/desk/Sales Order/SO 1"), { route: "/desk/Sales%20Order/SO%201", hash: "" });
	// A query is the Desk's route options; whatever it says, the page opened is still this Desk's.
	assert.equal(deskRoute("/desk/x?next=//evil.example.com").route, "/desk/x?next=//evil.example.com");
});

// ---------------------------------------------------------------------------
// 4. What the frame can make the Desk do: clock-in
// ---------------------------------------------------------------------------
const CLOCK = { project: "PROJ-0002", project_title: "Plaza fountain", task: null, time_category: "Field", description: "Pump install" };

ok("a clock-in message draws the Desk's card and clocks nobody in", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	await settle();
	const card = t.card();
	assert.ok(card, "no card was drawn");
	assert.ok(card.words().includes("Clock in to PROJ-0002?"), "the card does not say which project by its id: " + card.words());
	assert.ok(card.words().includes("Plaza fountain"));
	assert.ok(card.find("triton-approve") && card.find("triton-decline"));
	assert.deepEqual(t.win.calls, [], "the time kiosk was called before anyone pressed Confirm");
	assert.deepEqual(t.win.geo.asked, [], "location was asked for before anyone pressed Confirm");
	assert.equal(card.parentNode, t.win.document.body, "the card must be the Desk page's, not the frame's");
	assert.equal(t.frame().got("clock-in").length, 0, "the frame expects nothing back");

	// Leaving it alone, however long, does nothing.
	t.win.advance(10 * 60 * 1000);
	assert.deepEqual(t.win.calls, []);
});

ok("Confirm takes a fresh, precise location inside the click and sends it with the clock-in", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	t.card().find("triton-approve").emit("click");
	await settle();
	assert.equal(t.win.geo.asked.length, 1);
	assert.equal(t.win.geo.asked[0].maximumAge, 0, "a cached fix is not evidence of where somebody is standing");
	assert.equal(t.win.geo.asked[0].enableHighAccuracy, true);
	assert.deepEqual(t.win.calls, [
		{
			method: "erpnext_enhancements.api.time_kiosk.log_time",
			args: { action: "Start", project: "PROJ-0002", task: null, time_category: "Field", description: "Pump install", lat: 40.5, lng: -111.9, accuracy: 9 },
		},
	]);
	assert.ok(t.card().words().includes("Clocked in to PROJ-0002 (Plaza fountain)."), t.card().words());
	// The card says it and leaves; no coordinate is on it.
	assert.ok(!/40\.5|111\.9/.test(t.card().words()));
	t.win.advance(60 * 1000);
	assert.equal(t.card(), null);
});

ok("no location means no clock-in, and the card says so", async () => {
	const refused = makeLoader();
	refused.win.geo.answer = null; // the person said no, or the fix timed out
	await refused.openAndSignIn();
	refused.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	refused.card().find("triton-approve").emit("click");
	await settle();
	assert.deepEqual(refused.win.calls, [], "clocked in with no fix");
	assert.ok(refused.card().words().includes("Clocking in needs location and nothing was shared."));

	const none = makeLoader();
	none.win.navigator = {}; // a browser with no geolocation at all
	await none.openAndSignIn();
	none.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	none.card().find("triton-approve").emit("click");
	await settle();
	assert.deepEqual(none.win.calls, []);
	assert.ok(none.card().words().includes("Clocking in needs location"));
	none.card().find("triton-frame-clockin-dismiss").emit("click");
	assert.equal(none.card(), null);
});

ok("the card names the project by its id, and takes its name from the Desk, not from the frame", async () => {
	// The frame says one thing; the Desk knows the project by another name.
	const t = makeLoader();
	t.win.projectReply = { project_name: "Riverside pump house" };
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: Object.assign({}, CLOCK, { project_title: "Plaza fountain" }) });
	const name = () => t.card().find("triton-frame-clockin-name");
	assert.ok(t.card().words().includes("Clock in to PROJ-0002?"));
	assert.equal(name().textContent, "Plaza fountain", "until the Desk answers, the frame's title is all there is");
	await settle();
	assert.equal(name().textContent, "Riverside pump house", "the frame's title was kept over the Desk's own name");
	assert.ok(t.card().words().includes("Clock in to PROJ-0002?"));

	// One read of one field of that one record, with the person's own session. Nothing written.
	assert.equal(t.win.lookups.length, 1);
	const call = t.win.lookups[0];
	assert.ok(call.url.startsWith("/api/method/frappe.client.get_value?doctype=Project&fieldname=project_name&filters="));
	assert.deepEqual(JSON.parse(decodeURIComponent(call.url.split("filters=")[1])), { name: "PROJ-0002" });
	assert.equal(call.options.method, "GET");
	assert.equal(call.options.credentials, "same-origin");

	t.card().find("triton-approve").emit("click");
	await settle();
	assert.ok(t.card().words().includes("Clocked in to PROJ-0002 (Riverside pump house)."), t.card().words());
	assert.equal(t.win.calls[0].args.project, "PROJ-0002");
});

ok("when the Desk cannot name the project, the id still leads and nothing pops up", async () => {
	for (const reply of [403, 404, 500, "network", null, {}, { project_name: "" }, { project_name: 7 }, "PROJ-0002"]) {
		const t = makeLoader();
		t.win.projectReply = reply;
		await t.openAndSignIn();
		t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
		await settle();
		assert.ok(t.card().words().includes("Clock in to PROJ-0002?"), "no id on the card for " + JSON.stringify(reply));
		assert.equal(t.card().find("triton-frame-clockin-name").textContent, "Plaza fountain");
		assert.deepEqual(t.win.alerts, []);
		assert.equal(t.gaveUp.length, 0);
	}

	// No title from the frame and none from the Desk: the id alone, and no empty line.
	const bare = makeLoader();
	bare.win.projectReply = 403;
	await bare.openAndSignIn();
	bare.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0002" } });
	await settle();
	assert.equal(bare.card().find("triton-frame-clockin-name").hidden, true);
	bare.card().find("triton-approve").emit("click");
	await settle();
	assert.ok(bare.card().words().includes("Clocked in to PROJ-0002."), bare.card().words());
});

ok("a name that arrives for a card that has been replaced is not written onto the new one", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	const answers = [];
	t.win.fetch = (url) => new Promise((resolve) => answers.push([url, resolve]));
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0002", project_title: "Plaza fountain" } });
	await settle();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0009", project_title: "North gate" } });
	await settle();
	assert.equal(answers.length, 2);
	// The first card's answer comes back late.
	answers[0][1]({ ok: true, json: async () => ({ message: { project_name: "Riverside pump house" } }) });
	await settle();
	assert.ok(t.card().words().includes("Clock in to PROJ-0009?"));
	assert.equal(t.card().find("triton-frame-clockin-name").textContent, "North gate", "another project's name was written onto this card");
});

ok("Cancel does nothing but remove the card", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	t.card().find("triton-decline").emit("click");
	await settle();
	assert.equal(t.card(), null);
	assert.deepEqual(t.win.calls, []);
	assert.deepEqual(t.win.geo.asked, []);
});

ok("a location in the message is never used, and only the five known keys are read", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, {
		params: Object.assign({}, CLOCK, { lat: 1, lng: 2, latitude: 1, longitude: 2, accuracy: 0, action: "Stop", skip_reason: "x", offsite_acknowledged: 1 }),
	});
	t.card().find("triton-approve").emit("click");
	await settle();
	const args = t.win.calls[0].args;
	assert.equal(args.lat, 40.5, "a coordinate the model typed was sent as where the person is");
	assert.equal(args.lng, -111.9);
	assert.equal(args.accuracy, 9);
	assert.equal(args.action, "Start");
	assert.deepEqual(Object.keys(args).sort(), ["accuracy", "action", "description", "lat", "lng", "project", "task", "time_category"]);

	assert.deepEqual(readParams({ project: " PROJ-1 ", project_title: "x".repeat(500), task: 7, time_category: ["Field"], description: "a\n\nb", extra: 1 }), {
		project: "PROJ-1",
		project_title: "x".repeat(140),
		task: null,
		time_category: null,
		description: "a b",
	});
	for (const junk of [null, undefined, "PROJ-1", 7, [], [CLOCK]]) {
		assert.deepEqual(readParams(junk), { project: null, project_title: null, task: null, time_category: null, description: null });
	}
});

ok("a clock-in request with no project draws nothing; a second request replaces the first", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	for (const params of [undefined, null, {}, { project_title: "Plaza fountain" }, { project: "" }, { project: 12 }, "PROJ-0002"]) {
		t.fromFrame(FROM_FRAME.CLOCK_IN, { params: params });
		assert.equal(t.card(), null, "a card was drawn for " + JSON.stringify(params));
	}
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: { project: "PROJ-0009" } });
	const cards = t.win.document.body.children.filter((n) => n.classList.contains("triton-frame-clockin"));
	assert.equal(cards.length, 1);
	assert.ok(cards[0].words().includes("PROJ-0009"));
});

ok("a clock-in the time kiosk refuses is said on the card, which stays", async () => {
	const t = makeLoader();
	t.win.callReply = new Error("You are already clocked in.");
	await t.openAndSignIn();
	t.fromFrame(FROM_FRAME.CLOCK_IN, { params: CLOCK });
	t.card().find("triton-approve").emit("click");
	await settle();
	assert.ok(t.card().words().includes("You were not clocked in"));
	assert.ok(t.card().words().includes("already clocked in"));
	t.win.advance(60 * 1000);
	assert.ok(t.card(), "a failure vanished before anyone could read it");
});

ok("the clock-in card's code asks for location in one place, inside the Confirm click", () => {
	const src = code("erpnext_enhancements/public/js/triton_frame/clock_in.js");
	assert.equal((src.match(/getCurrentPosition\(/g) || []).length, 1);
	assert.ok(!/watchPosition/.test(src));
	const confirm = src.slice(src.indexOf("function confirm("), src.indexOf("function show("));
	assert.ok(confirm.includes("getCurrentPosition("), "location is not asked for inside confirm()");
	const show = src.slice(src.indexOf("function show("));
	assert.ok(/yes\.addEventListener\("click", \(\) => confirm\(params, slot, shown\)\)/.test(show), "confirm() is not reached from the Confirm click alone");
	assert.equal((src.match(/(?<!function )confirm\(params, slot, shown\)/g) || []).length, 1, "confirm() is called from somewhere other than the click");
	// The name lookup reads; it must not be a second way to reach the time kiosk or the device.
	const lookUp = src.slice(src.indexOf("function lookUpName("), src.indexOf("function confirm("));
	assert.ok(lookUp.includes("frappe.client.get_value") && /method:\s*"GET"/.test(lookUp));
	assert.ok(!/xcall|geolocation|LOG_TIME/.test(lookUp));
	assert.ok(/maximumAge:\s*0/.test(src) && /enableHighAccuracy:\s*true/.test(src));
	// A stand-alone card can be made and thrown away without a loader.
	const win = makeWindow();
	const card = createClockIn({ win });
	assert.equal(card.show(CLOCK), true);
	card.destroy();
	assert.equal(win.document.body.children.length, 0);
});

// ---------------------------------------------------------------------------
// SapphireTriton.ask() and the page source
// ---------------------------------------------------------------------------
ok("ask() opens the panel, waits for ready, and is delivered once, as prompt and page", async () => {
	const t = makeLoader();
	const context = { type: "document", doctype: "Customer", name: "C-1", fn() {} };
	assert.equal(t.loader.ask("Draft a course", context), true);
	assert.equal(t.loader.isOpen(), true);
	assert.equal(t.frame().got(TO_FRAME.ASK).length, 0);
	t.ready(); // before sign-in: the frame holds it until the chat is on screen
	await settle();
	t.ready();
	await settle();
	const asks = t.frame().got(TO_FRAME.ASK);
	assert.equal(asks.length, 1);
	assert.equal(asks[0].data.prompt, "Draft a course");
	assert.deepEqual(asks[0].data.page, { type: "document", doctype: "Customer", name: "C-1" });
	assert.ok(!("send" in asks[0].data), "the Desk never sends for the person");
	assert.ok(!("context" in asks[0].data));
});

ok("ask() with no page leaves the pin alone, cuts a prompt to the contract's length, and an empty prompt only opens the panel", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	t.loader.ask("x".repeat(MAX_PROMPT + 500));
	t.loader.toggle(false);
	assert.equal(t.loader.ask(""), true);
	assert.equal(t.loader.ask(undefined, { type: "document", doctype: "Customer", name: "C-1" }), true);
	assert.equal(t.loader.isOpen(), true);
	const asks = t.frame().got(TO_FRAME.ASK);
	assert.equal(asks.length, 1, "an ask with no prompt was sent; the contract requires one");
	assert.equal(asks[0].data.prompt.length, MAX_PROMPT);
	assert.equal(MAX_PROMPT, 8000);
	assert.ok(!("page" in asks[0].data), "an absent page must be omitted: null would unpin");
});

ok("a route change reports the new page a moment later, and never the same page twice", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	const pages = () => t.frame().got(TO_FRAME.PAGE).map((p) => p.data.page);
	assert.equal(pages().length, 1);

	t.page.ref = { type: "document", doctype: "Project", name: "PROJ-0002" };
	t.win.frappe.router.emit("change");
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS - 1);
	assert.equal(pages().length, 1, "reported before the page had drawn");
	t.win.advance(1);
	assert.deepEqual(pages()[1], { type: "document", doctype: "Project", name: "PROJ-0002" });

	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	t.loader.toggle(false);
	t.loader.toggle(true);
	assert.equal(pages().length, 2, "the same page was reported twice");

	t.page.ref = null;
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	assert.equal(pages()[2], null);
});

ok("while the panel is open, unsaved edits and changed filters are noticed without a route change", async () => {
	const t = makeLoader();
	await t.openAndSignIn();
	const pages = () => t.frame().got(TO_FRAME.PAGE).map((p) => p.data.page);
	t.win.advance(PAGE_POLL_MS * 5);
	assert.equal(pages().length, 1, "the page was re-sent though nothing changed");

	t.page.ref = { type: "document", doctype: "Sales Order", name: "SO-0001", unsaved: true };
	t.win.advance(PAGE_POLL_MS);
	assert.equal(pages().length, 2);
	assert.equal(pages()[1].unsaved, true);

	// Closed: nothing is read and nothing is sent, however long.
	t.loader.toggle(false);
	t.page.ref = { type: "document", doctype: "Sales Order", name: "SO-0001" };
	t.win.advance(PAGE_POLL_MS * 20);
	assert.equal(pages().length, 2, "the Desk kept reporting on a closed panel");
	assert.equal(t.win.pendingTimers(), 0, "a timer is running for a closed panel");

	// Reopened: what changed meanwhile goes out once.
	t.loader.toggle(true);
	assert.equal(pages().length, 3);
	assert.ok(!("unsaved" in pages()[2]));
});

ok("the page reference never carries field values", async () => {
	const t = makeLoader();
	t.page.ref = { type: "document", doctype: "Sales Order", name: "SO-0001", title: "Sales Order: SO-0001", route: "#Form/Sales Order/SO-0001", unsaved: true };
	await t.openAndSignIn();
	const sent = t.frame().got(TO_FRAME.PAGE)[0].data.page;
	assert.deepEqual(sent, t.page.ref, "the reference from page_context.js must pass through unchanged");
	const src = code("erpnext_enhancements/public/js/triton_frame/page_source.js") + code("erpnext_enhancements/public/js/triton_frame/loader.js");
	assert.ok(!src.includes("dirty_fields"), "dirty_fields is a name Triton's server reads for itself");
});

ok("a list's filters are kept only when they are that list's", () => {
	const win = { cur_list: { doctype: "Sales Order" }, frappe: { query_report: { report_name: "Stock Balance" } } };
	const filters = [["Sales Order", "status", "=", "Draft"]];
	assert.deepEqual(ownFilters({ type: "list", doctype: "Sales Order", filters }, win).filters, filters);
	assert.equal(ownFilters({ type: "list", doctype: "Customer", filters }, win).filters, null, "one list's filters went out under another list's name");
	assert.deepEqual(ownFilters({ type: "report", report_name: "Stock Balance", filters: { a: 1 } }, win).filters, { a: 1 });
	assert.equal(ownFilters({ type: "report", report_name: "General Ledger", filters: { a: 1 } }, win).filters, null);
	assert.deepEqual(ownFilters({ type: "report", report_name: "Sales Order", filters }, win).filters, filters, "the Report view of a list");
	assert.equal(ownFilters({ type: "list", doctype: "Customer", filters }, {}).filters, null);
	const doc = { type: "document", doctype: "Sales Order", name: "SO-1" };
	assert.equal(ownFilters(doc, win), doc);
});

ok("a page source whose detector throws reports nothing instead of breaking the Desk", () => {
	const win = makeWindow();
	const seen = [];
	const source = createPageSource({
		win,
		detect: () => {
			throw new Error("cur_frm is mid-teardown");
		},
		onChange: (ref) => seen.push(ref),
	});
	source.report(true);
	assert.deepEqual(seen, [null]);
});

ok("the config is checked before a ticket could be posted to it", () => {
	assert.equal(validConfig(CONFIG), true);
	assert.equal(validConfig({ origin: "http://triton.sapphire.localhost:4317", url: "http://triton.sapphire.localhost:4317/embed.html" }), true);
	for (const bad of [
		null,
		{},
		{ origin: ORIGIN, url: "https://evil.example.com/embed.html" },
		{ origin: "http://triton.example.com", url: "http://triton.example.com/embed.html" },
		{ origin: ORIGIN, url: "https://user:pw@triton.example.com/embed.html" },
		{ origin: ORIGIN, url: "/embed.html" },
		{ origin: ORIGIN, url: "javascript:alert(1)" },
	]) {
		assert.equal(validConfig(bad), false, JSON.stringify(bad));
	}
});

// ---------------------------------------------------------------------------
// 5. The gate, and the classic widget left alone
// ---------------------------------------------------------------------------
/** A window as the main bundle sees it: the classic widget has already defined its opener. */
function gateWindow(framed) {
	const win = makeWindow();
	win.classic = { asks: [], started: 0 };
	win.SapphireTriton = {
		ask: (prompt, context) => {
			win.classic.asks.push([prompt, context]);
			return Promise.resolve(true);
		},
		startClassic: () => {
			win.classic.started += 1;
			return Promise.resolve();
		},
	};
	if (framed) win.frappe.boot.triton_frame = Object.assign({}, CONFIG);
	win.tags = () => win.document.head.children;
	win.loaders = [];
	/** What triton_frame.bundle.js defines once it has run. */
	win.defineLoader = () => {
		win.SapphireTritonFrame = {
			start: (opts) => {
				const loader = { opts, asks: [], destroyed: 0, ask: (p, c) => (loader.asks.push([p, c]), true), destroy: () => (loader.destroyed += 1) };
				win.loaders.push(loader);
				return loader;
			},
		};
	};
	return win;
}

ok("on the classic widget the gate fetches nothing, wraps nothing and starts nothing", async () => {
	const win = gateWindow(false);
	const classicAsk = win.SapphireTriton.ask;
	installGate(win);
	await settle();
	win.advance(LOAD_TIMEOUT_MS * 2);
	await settle();
	assert.equal(win.tags().length, 0, "a file was fetched for a person on the classic widget");
	assert.equal(win.SapphireTriton.ask, classicAsk, "SapphireTriton.ask was replaced");
	assert.equal(win.classic.started, 0);
	assert.equal(win.pendingTimers(), 0);
	assert.deepEqual(win.logs, []);
});

ok("a Guest never arms the gate, whatever boot says", async () => {
	const win = gateWindow(true);
	win.frappe.session.user = "Guest";
	installGate(win);
	await settle();
	assert.equal(win.tags().length, 0);
});

ok("on the framed widget the gate fetches the two hashed files and starts the loader with the boot config", async () => {
	const win = gateWindow(true);
	installGate(win);
	const [link, script] = win.tags();
	assert.equal(win.tags().length, 2);
	assert.equal(link.href, "/assets/erpnext_enhancements/dist/triton_frame.bundle.HASH.css");
	assert.equal(script.src, "/assets/erpnext_enhancements/dist/triton_frame.bundle.HASH.js");
	assert.equal(LOADER_JS, "triton_frame.bundle.js");
	assert.equal(LOADER_CSS, "triton_frame.bundle.css");

	win.defineLoader();
	link.onload();
	script.onload();
	await settle();
	assert.equal(win.loaders.length, 1);
	assert.deepEqual(win.loaders[0].opts.config, CONFIG);
	assert.equal(win.classic.started, 0);

	// Arming twice (app_ready after the immediate attempt) changes nothing.
	win.advance(LOAD_TIMEOUT_MS * 2);
	await settle();
	assert.equal(win.tags().length, 2);
	assert.equal(win.loaders.length, 1);
	assert.equal(win.classic.started, 0);
});

ok("ask() before the loader has arrived is held and then delivered to it", async () => {
	const win = gateWindow(true);
	installGate(win);
	const asked = win.SapphireTriton.ask("Draft a course", { type: "document" });
	win.defineLoader();
	win.tags().forEach((tag) => tag.onload());
	await asked;
	assert.deepEqual(win.loaders[0].asks, [["Draft a course", { type: "document" }]]);
	assert.deepEqual(win.classic.asks, []);
});

for (const [name, fail] of [
	["the script fails to load", (win) => (win.tags()[0].onload(), win.tags()[1].onerror())],
	["the stylesheet fails to load", (win) => (win.defineLoader(), win.tags()[0].onerror(), win.tags()[1].onload())],
	["the script loads but defines no loader", (win) => win.tags().forEach((tag) => tag.onload())],
	["neither file arrives in time", (win) => win.advance(LOAD_TIMEOUT_MS)],
]) {
	ok("when " + name + ", the classic widget is started in its place", async () => {
		const win = gateWindow(true);
		const classicAsk = win.SapphireTriton.ask;
		installGate(win);
		fail(win);
		await settle();
		assert.equal(win.classic.started, 1);
		assert.equal(win.SapphireTriton.ask, classicAsk, "the classic opener was not put back");
		assert.equal(win.logs.length, 1, "one quiet console line");
		assert.ok(/classic/i.test(win.logs[0]));
		assert.deepEqual(win.classic.asks, [], "the classic panel was opened though nothing was open");

		// The files turning up afterwards must not start a second widget.
		win.defineLoader();
		win.tags().forEach((tag) => tag.onload && tag.onload());
		win.advance(LOAD_TIMEOUT_MS);
		await settle();
		assert.equal(win.loaders.length, 0, "the framed widget started after the classic one took over");
		assert.equal(win.classic.started, 1);
	});
}

ok("a Desk built before the loader existed hands back without fetching", async () => {
	const win = gateWindow(true);
	win.frappe.assets.bundled_asset = (name) => name; // frappe returns the bare name for an unknown bundle
	installGate(win);
	await settle();
	assert.equal(win.tags().length, 0);
	assert.equal(win.classic.started, 1);
});

ok("the loader giving up hands back once: classic starts, opens if the panel was open, and keeps the prompt", async () => {
	const win = gateWindow(true);
	installGate(win);
	win.defineLoader();
	win.tags().forEach((tag) => tag.onload());
	await settle();
	const giveUp = win.loaders[0].opts.giveUp;

	giveUp("timeout", { open: true, ask: { prompt: "Draft a course", context: { type: "document" } } });
	giveUp("timeout", { open: true, ask: null });
	giveUp("contract", { open: false, ask: null });
	await settle();
	assert.equal(win.classic.started, 1, "handed back more than once");
	assert.deepEqual(win.classic.asks, [["Draft a course", { type: "document" }]]);
	assert.equal(win.logs.length, 1);

	// From here on a Desk button that opens Triton reaches the classic widget.
	await win.SapphireTriton.ask("Another");
	assert.equal(win.classic.asks.length, 2);
	assert.deepEqual(win.loaders[0].asks, []);
});

ok("handing back with the panel open and no prompt opens the classic panel empty", async () => {
	const win = gateWindow(true);
	installGate(win);
	win.defineLoader();
	win.tags().forEach((tag) => tag.onload());
	await settle();
	win.loaders[0].opts.giveUp("timeout", { open: true, ask: null });
	await settle();
	assert.deepEqual(win.classic.asks, [[undefined, undefined]]);
});

ok("the real loader and the real gate hand back together: sign-in fails, the classic widget opens with the prompt", async () => {
	const win = gateWindow(true);
	win.SapphireTritonFrame = {
		start: ({ config, giveUp }) => createLoader({ win, config, giveUp, detect: () => null }),
	};
	installGate(win);
	win.tags().forEach((tag) => tag.onload());
	await settle();
	await win.SapphireTriton.ask("Draft a course", { type: "document", doctype: "Project", name: "PROJ-0002" });
	const frame = win.document.frames[0];
	const send = (type, fields) =>
		win.emit("message", { origin: ORIGIN, source: frame.contentWindow, data: Object.assign({}, fields, { [ENVELOPE_KEY]: CONTRACT_VERSION, type }) });
	send(FROM_FRAME.READY, { version: CONTRACT_VERSION });
	await settle();
	send(FROM_FRAME.SIGN_IN_FAILED, { reason: "no-config" });
	await settle();
	assert.equal(win.classic.started, 1);
	assert.deepEqual(win.classic.asks, [["Draft a course", { type: "document", doctype: "Project", name: "PROJ-0002" }]]);
	assert.equal(win.document.body.children.length, 0, "the framed widget is still on the page");
	assert.ok(win.logs[0].includes("sign-in-failed, no-config"));
});

ok("the classic widget stays asleep while boot says framed, and has a way back", () => {
	const src = code("erpnext_enhancements/public/js/global_enhancements/triton_widget.js");
	const init = src.slice(src.indexOf("async function init()"), src.indexOf("window.SapphireTriton = window.SapphireTriton || {}"));
	const guard = init.indexOf("if (framedWidgetOwnsTheButton()) return;");
	assert.ok(guard > 0, "init() no longer checks who owns the button");
	assert.ok(guard < init.indexOf("_booted = true"), "the check must come before the widget latches itself as booted");
	assert.ok(guard < init.indexOf('xcall("get_config")'), "get_config is called for a person on the framed widget");
	assert.ok(/return !_handedBack && !!\(frappe\.boot && frappe\.boot\.triton_frame\);/.test(src));
	assert.ok(/window\.SapphireTriton\.startClassic = function \(\) \{\s*_handedBack = true;\s*return init\(\);\s*\};/.test(src));
});

ok("the loader is its own bundle: the main bundle carries the gate and nothing of the loader", () => {
	const main = code("erpnext_enhancements/public/js/erpnext_enhancements.bundle.js");
	const widget = main.indexOf('import "./global_enhancements/triton_widget.js"');
	const gate = main.indexOf('import "./global_enhancements/triton_frame_gate.js"');
	assert.ok(widget > 0 && gate > widget, "the gate must be imported after the widget whose opener it wraps");
	assert.ok(!main.includes("triton_frame/"), "the loader was pulled into the Desk's main bundle");
	assert.ok(!main.includes("triton_frame.bundle"), "the loader bundle is imported by the main bundle");

	const gateSrc = code("erpnext_enhancements/public/js/global_enhancements/triton_frame_gate.js");
	assert.ok(!/^\s*import\s/m.test(gateSrc), "the gate imports something, which would land in the main bundle");
	assert.ok(!gateSrc.includes("frappe.require"), "frappe.require freezes the Desk while it loads");

	const hooks = read("erpnext_enhancements/hooks.py");
	assert.ok(!hooks.includes("triton_frame.bundle"), "the loader bundle is in app_include_js or app_include_css");

	const entry = read("erpnext_enhancements/public/js/triton_frame.bundle.js");
	assert.ok(entry.includes("window.SapphireTritonFrame"));
	assert.ok(fs.existsSync(path.join(ROOT, "erpnext_enhancements/public/css/triton_frame.bundle.css")));
});

// ---------------------------------------------------------------------------
// 6. The contract: this loader's words are the document's
// ---------------------------------------------------------------------------
/** The contract version and the message names a contract document states, read from its text. */
function readContract(text) {
	const version = /\*\*Contract version:\*\*\s*(\d+)/.exec(text);
	const names = (heading) => {
		const start = text.search(new RegExp("^#{2,3} " + heading + "\\s*$", "m"));
		if (start < 0) return null;
		const rest = text.slice(start + 4);
		const end = rest.search(/^#{2,3} (?!#)/m);
		const section = end < 0 ? rest : rest.slice(0, end);
		// Triton's document gives each message a heading of its own (and tables of FIELDS under
		// it); this repository's lists the messages as the first column of one table.
		const headings = [...section.matchAll(/^#### `([a-z-]+)`\s*$/gm)].map((m) => m[1]);
		const rows = [...section.matchAll(/^\| `([a-z-]+)` \|/gm)].map((m) => m[1]);
		const found = new Set(headings.length ? headings : rows);
		return [...found].sort();
	};
	return { version: version ? Number(version[1]) : null, fromFrame: names("Frame to Desk"), toFrame: names("Desk to frame") };
}

const sorted = (object) => Object.values(object).slice().sort();

ok("the loader's contract version and message names are the ones in docs/triton-framed-widget.md", () => {
	const doc = readContract(read("docs/triton-framed-widget.md"));
	assert.equal(doc.version, CONTRACT_VERSION, "contract.js and the document disagree about the contract version");
	assert.deepEqual(doc.fromFrame, sorted(FROM_FRAME), "frame-to-Desk messages: contract.js and the document disagree");
	assert.deepEqual(doc.toFrame, sorted(TO_FRAME), "Desk-to-frame messages: contract.js and the document disagree");
	// Every message the frame can send is handled by name in the loader, not just listed.
	const loader = code("erpnext_enhancements/public/js/triton_frame/loader.js");
	for (const key of Object.keys(FROM_FRAME)) assert.ok(loader.includes("FROM_FRAME." + key), "the loader never reads FROM_FRAME." + key);
	for (const key of Object.keys(TO_FRAME)) assert.ok(loader.includes("TO_FRAME." + key), "the loader never sends TO_FRAME." + key);
});

ok("the document reader fails on a version or a name that differs (so the check above can fail)", () => {
	const text = read("docs/triton-framed-widget.md");
	assert.notEqual(readContract(text.replace(/(\*\*Contract version:\*\*\s*)\d+/, "$12")).version, CONTRACT_VERSION);
	assert.notDeepEqual(readContract(text.replace("| `visibility` |", "| `opened` |")).toFrame, sorted(TO_FRAME));
	assert.notDeepEqual(readContract(text.replace("| `clock-in` |", "| `clockin` |")).fromFrame, sorted(FROM_FRAME));
});

// Triton's own document is in another repository (a private one, so it is not copied here). Where
// it can be found, the loader is held to it directly: a path in TRITON_EMBED_BRIDGE_DOC, or a
// Triton checkout beside this repository. On CI there is neither, and the check above stands alone.
function tritonContract() {
	const candidates = [process.env.TRITON_EMBED_BRIDGE_DOC];
	let dir = ROOT;
	for (let i = 0; i < 6; i += 1) {
		dir = path.dirname(dir);
		candidates.push(path.join(dir, "triton", "docs", "embed-bridge.md"));
	}
	return candidates.find((file) => file && fs.existsSync(file)) || null;
}

ok("where Triton's own contract document is at hand, the loader agrees with it", () => {
	const file = tritonContract();
	if (!file) {
		console.log("        (skipped: no Triton checkout with docs/embed-bridge.md, and TRITON_EMBED_BRIDGE_DOC is not set)");
		return;
	}
	const doc = readContract(fs.readFileSync(file, "utf8"));
	assert.equal(doc.version, CONTRACT_VERSION, "Triton's contract version is not the loader's (" + file + ")");
	assert.deepEqual(doc.fromFrame, sorted(FROM_FRAME), "frame-to-Desk messages differ from Triton's document");
	assert.deepEqual(doc.toFrame, sorted(TO_FRAME), "Desk-to-frame messages differ from Triton's document");
});

// ---------------------------------------------------------------------------
console.log("Framed Triton widget: loader, gate and bridge\n");
let failed = 0;
for (const [name, fn] of tests) {
	try {
		await fn();
		console.log("  ok    " + name);
	} catch (err) {
		failed += 1;
		console.error("  FAIL  " + name + "\n        " + (err && err.message));
	}
}
if (!tests.length) {
	console.error("\nno tests ran");
	process.exit(2);
}
console.log("\n" + (tests.length - failed) + " of " + tests.length + " passed");
process.exit(failed ? 1 : 0);
