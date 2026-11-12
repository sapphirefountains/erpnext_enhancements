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
 * 2. A dead panel. If the frame never says "ready", or says it in a contract version this loader
 *    does not know, the classic widget has to take over, once, with the framed one fully gone
 *    (its listeners too: two widgets answering Alt+T open and close each other).
 * 3. Who is listened to. A Desk page gets `message` events from anything holding a reference to
 *    the window. Only Triton's origin, and only our own frame's window, may open a record or
 *    trigger a sign-in ticket.
 * 4. The classic widget, untouched. With no `triton_frame` in boot the gate must fetch nothing and
 *    wrap nothing.
 *
 * The contract's message NAMES are provisional until Triton's document lands (contract.js says
 * so). Every assertion below reads them from contract.js rather than spelling them, so stage 2
 * changes that file and this suite follows.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { LOADER_CSS, LOADER_JS, LOAD_TIMEOUT_MS, installGate } from "../erpnext_enhancements/public/js/global_enhancements/triton_frame_gate.js";
import { createBridge } from "../erpnext_enhancements/public/js/triton_frame/bridge.js";
import { CONTRACT_VERSION, ENVELOPE_KEY, FROM_FRAME, TO_FRAME, envelope, readMessage } from "../erpnext_enhancements/public/js/triton_frame/contract.js";
import {
	FRAME_ALLOW,
	READY_TIMEOUT_MS,
	TICKETS_PER_MINUTE,
	TICKET_METHOD,
	createLoader,
	validConfig,
} from "../erpnext_enhancements/public/js/triton_frame/loader.js";
import { SETTLE_MS, createPageSource, ownFilters } from "../erpnext_enhancements/public/js/triton_frame/page_source.js";
import { recordRoute } from "../erpnext_enhancements/public/js/triton_frame/record_link.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");
/** Executable code only: an absence check must not be tripped by the comment explaining it. */
const code = (rel) =>
	read(rel)
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.replace(/^\s*\/\/.*$/gm, "")
		.replace(/\s\/\/ .*$/gm, "");

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
		if (typeof child.onAttach === "function") child.onAttach();
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
	doc.body = new Node("body");
	doc.head = new Node("head");
	doc.activeElement = null;
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
	win.advance = (ms) => {
		clock += ms;
		timers
			.filter((timer) => timer.at <= clock)
			.sort((a, b) => a.at - b.at)
			.forEach((timer) => {
				const i = timers.indexOf(timer);
				if (i < 0) return; // cleared by an earlier one
				timers.splice(i, 1);
				timer.fn();
			});
	};
	win.pendingTimers = () => timers.length;

	win.fetchCalls = [];
	win.ticketReply = { ok: true, ticket: "one-time-ticket", expires_in: 60 };
	win.fetch = async (url, options) => {
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

	win.routes = [];
	const router = new Emitter();
	router.on = (type, fn) => router.addEventListener(type, fn);
	router.off = (type, fn) => router.removeEventListener(type, fn);
	win.frappe = {
		csrf_token: "csrf-abc",
		session: { user: "pat@example.com" },
		boot: { user: { can_read: ["Sales Order", "Project", "Customer"] } },
		router: router,
		set_route: (route) => win.routes.push(route),
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
	const openAndReady = async () => {
		loader.toggle(true);
		fromFrame(FROM_FRAME.READY);
		await settle();
	};
	return { win, loader, gaveUp, page, fab, panel, frame, fromFrame, openAndReady };
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
	assert.equal(FRAME_ALLOW, "clipboard-write; microphone");
	assert.equal(frame().parentNode, panel);
});

ok("the address is written ONCE across open, close, reopen, route changes, ready and sign-in", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.loader.toggle(false);
	t.loader.toggle(true);
	t.loader.toggle();
	t.loader.toggle();
	t.page.ref = { type: "document", doctype: "Project", name: "PROJ-0002" };
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	t.fromFrame(FROM_FRAME.NEED_TICKET);
	await settle();
	t.fromFrame(FROM_FRAME.READY); // the page in the frame reloaded itself
	await settle();
	t.loader.ask("Draft a course", { type: "document", doctype: "Customer", name: "C-1" });
	await settle();

	assert.equal(t.win.document.frames.length, 1, "a second frame was created");
	assert.equal(t.frame().srcWrites.length, 1, "the frame's address was written again: " + JSON.stringify(t.frame().srcWrites));
	assert.equal(t.frame().appended, 1, "the frame was moved to another parent, which reloads it");
});

ok("the loader's code has one `src` assignment and touches no history or frame location", () => {
	const all = ["loader.js", "bridge.js", "page_source.js", "record_link.js", "contract.js"]
		.map((f) => code("erpnext_enhancements/public/js/triton_frame/" + f))
		.join("\n");
	assert.equal((all.match(/\.src\s*=[^=]/g) || []).length, 1, "exactly one assignment to .src");
	for (const banned of ["pushState", "replaceState", "contentWindow.location", "location.replace", "location.assign", "location.href"]) {
		assert.ok(!all.includes(banned), banned + " appears in the framed loader");
	}
	assert.ok(!/postMessage\([^)]*["']\*["']/.test(all), 'a message is posted to "*"');
});

// ---------------------------------------------------------------------------
// 2. Fallback
// ---------------------------------------------------------------------------
ok("a frame that never says ready is given up on after READY_TIMEOUT_MS", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.win.advance(READY_TIMEOUT_MS - 1);
	assert.equal(t.gaveUp.length, 0, "gave up early");
	t.win.advance(1);
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.gaveUp[0].reason, "timeout");
	assert.equal(t.gaveUp[0].detail.open, true, "the classic panel must open in its place");
});

ok("giving up removes the button, the panel, the frame and every listener", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.win.advance(READY_TIMEOUT_MS);
	assert.equal(t.win.document.body.children.length, 0, "something of the framed widget is still on the page");
	assert.equal(t.win.count("message"), 0, "still listening for messages");
	assert.equal(t.win.document.count("keydown"), 0, "still answering Alt+T: two widgets would open and close each other");
	assert.equal(t.win.visualViewport.count("resize"), 0);
	assert.equal(t.win.frappe.router.count("change"), 0);
	assert.equal(t.win.pendingTimers(), 0);
});

ok("giving up happens once, and the dead loader does nothing afterwards", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	const frame = t.frame();
	t.win.advance(READY_TIMEOUT_MS);
	t.win.advance(READY_TIMEOUT_MS * 3);
	t.loader.toggle(true);
	assert.equal(t.loader.ask("hello"), false);
	t.win.emit("message", { origin: ORIGIN, source: frame.contentWindow, data: envelope(FROM_FRAME.READY) });
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.win.document.body.children.length, 0);
});

ok("a frame that says ready in time is kept, however long it then runs", async () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.win.advance(READY_TIMEOUT_MS - 1);
	t.fromFrame(FROM_FRAME.READY);
	await settle();
	t.win.advance(READY_TIMEOUT_MS * 10);
	assert.equal(t.gaveUp.length, 0);
	assert.equal(t.loader.isReady(), true);
});

ok("ready in a contract version this loader does not know hands back at once", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	// By hand: the fromFrame helper always writes this loader's own version.
	t.win.emit("message", {
		origin: ORIGIN,
		source: t.frame().contentWindow,
		data: { [ENVELOPE_KEY]: CONTRACT_VERSION + 1, type: FROM_FRAME.READY },
	});
	assert.equal(t.gaveUp.length, 1);
	assert.equal(t.gaveUp[0].reason, "contract");
	assert.equal(t.win.fetchCalls.length, 0, "a ticket was requested for a page we cannot drive");
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

ok("Triton having no ticket route hands back; any other ticket failure does not", async () => {
	const missing = makeLoader();
	missing.win.ticketReply = { ok: false, reason: "unsupported", message: "no" };
	await missing.openAndReady();
	assert.equal(missing.gaveUp.length, 1);
	assert.equal(missing.gaveUp[0].reason, "no-ticket-route");

	for (const reply of [{ ok: false, reason: "unreachable", message: "Triton could not be reached." }, "network", 403, 500]) {
		const t = makeLoader();
		t.win.ticketReply = reply;
		await t.openAndReady();
		assert.equal(t.gaveUp.length, 0, "gave up on " + JSON.stringify(reply));
		const words = t.panel.find("triton-frame-words");
		const retry = t.panel.find("triton-frame-retry");
		assert.equal(t.panel.find("triton-frame-shell").hidden, false, "the panel says nothing");
		assert.ok(words.textContent.length > 10, "no plain message");
		assert.equal(retry.hidden, false, "no way to try again");
		assert.equal(t.frame().got(TO_FRAME.TICKET).length, 0);

		// Try again, and this time Triton answers.
		t.win.ticketReply = { ok: true, ticket: "second-ticket", expires_in: 60 };
		retry.emit("click");
		await settle();
		assert.equal(t.frame().got(TO_FRAME.TICKET).length, 1);
		assert.equal(t.panel.find("triton-frame-shell").hidden, true);
	}
});

// ---------------------------------------------------------------------------
// 3. Who is listened to, and who is posted to
// ---------------------------------------------------------------------------
ok("a message from any other origin is dropped, even from our own frame's window", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	for (const origin of ["https://evil.example.com", "https://triton.example.com.evil.test", "http://triton.example.com", "null", "", ORIGIN + "/"]) {
		t.fromFrame(FROM_FRAME.READY, null, { origin: origin });
	}
	assert.equal(t.loader.isReady(), false);
	assert.equal(t.win.fetchCalls.length, 0);
	t.win.advance(READY_TIMEOUT_MS);
	assert.equal(t.gaveUp[0].reason, "timeout", "a foreign ready stopped the fallback");
});

ok("a message from Triton's origin but another window is dropped", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.fromFrame(FROM_FRAME.READY, null, { source: { postMessage() {} } });
	t.fromFrame(FROM_FRAME.READY, null, { source: null });
	t.fromFrame(FROM_FRAME.READY, null, { source: t.win });
	assert.equal(t.loader.isReady(), false);
});

ok("data that is not a bridge message is dropped", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	for (const data of [null, undefined, "ready", 1, [], {}, { type: FROM_FRAME.READY }, { [ENVELOPE_KEY]: "1", type: FROM_FRAME.READY }, { [ENVELOPE_KEY]: 1 }]) {
		t.win.emit("message", { origin: ORIGIN, source: t.frame().contentWindow, data: data });
	}
	assert.equal(t.loader.isReady(), false);
	assert.equal(readMessage({ [ENVELOPE_KEY]: 1, type: "x" }).type, "x");
});

ok("nothing but ready is acted on before ready", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { doctype: "Sales Order", name: "SO-0001" });
	t.fromFrame(FROM_FRAME.NEED_TICKET);
	t.fromFrame(FROM_FRAME.CLOSE);
	assert.equal(t.win.routes.length, 0);
	assert.equal(t.win.fetchCalls.length, 0);
	assert.equal(t.loader.isOpen(), true);
});

ok("a message in another version is dropped once the frame is ready", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.win.emit("message", {
		origin: ORIGIN,
		source: t.frame().contentWindow,
		data: { [ENVELOPE_KEY]: CONTRACT_VERSION + 1, type: FROM_FRAME.OPEN_RECORD, doctype: "Sales Order", name: "SO-0001" },
	});
	assert.equal(t.win.routes.length, 0);
});

ok("every message to the frame names Triton's origin as its target", async () => {
	const t = makeLoader();
	await t.openAndReady();
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
ok("ready is answered with a ticket, then the page, then opened", async () => {
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
	assert.deepEqual(types, [TO_FRAME.TICKET, TO_FRAME.PAGE, TO_FRAME.OPENED]);
	assert.equal(t.frame().got(TO_FRAME.TICKET)[0].data.ticket, "one-time-ticket");
	assert.deepEqual(t.frame().got(TO_FRAME.PAGE)[0].data.page, { type: "document", doctype: "Sales Order", name: "SO-0001" });
	assert.equal(t.panel.find("triton-frame-shell").hidden, true);
	assert.ok(t.panel.classList.contains("triton-frame-live"));
});

ok("the ticket is not kept, logged or written anywhere by the loader", async () => {
	const t = makeLoader();
	await t.openAndReady();
	assert.ok(!JSON.stringify(t.win.logs).includes("one-time-ticket"));
	const src = code("erpnext_enhancements/public/js/triton_frame/loader.js");
	assert.ok(!/console\./.test(src), "the loader writes to the console");
	assert.ok(!/localStorage|sessionStorage|document\.cookie/.test(src), "the loader touches browser storage");
});

ok("need-ticket gets a new ticket, and a frame stuck asking is cut off", async () => {
	const t = makeLoader();
	await t.openAndReady();
	for (let i = 0; i < TICKETS_PER_MINUTE + 5; i += 1) {
		t.fromFrame(FROM_FRAME.NEED_TICKET);
		await settle();
	}
	assert.equal(t.win.fetchCalls.length, TICKETS_PER_MINUTE, "the server was asked more often than the limit");
	assert.ok(t.frame().got(TO_FRAME.TICKET_ERROR).length >= 1, "the frame was not told why it got no ticket");
	assert.equal(t.panel.find("triton-frame-shell").hidden, true, "a mid-chat failure covered the chat");
});

ok("a new ticket for a closed panel does not take the keyboard away from the Desk", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.loader.toggle(false);
	const before = t.frame().focused;
	t.fromFrame(FROM_FRAME.NEED_TICKET);
	await settle();
	assert.equal(t.frame().got(TO_FRAME.TICKET).length, 2, "the closed panel was not signed in again");
	assert.equal(t.frame().focused, before, "a hidden frame was focused");

	// Nor is a frame focused while the panel is showing its own words over it.
	const failing = makeLoader();
	failing.win.ticketReply = { ok: false, reason: "unreachable", message: "Triton could not be reached." };
	await failing.openAndReady();
	assert.equal(failing.frame().focused, 0);
});

ok("two requests at once share one call", async () => {
	const t = makeLoader();
	t.loader.toggle(true);
	t.fromFrame(FROM_FRAME.READY);
	t.fromFrame(FROM_FRAME.READY);
	await settle();
	assert.equal(t.win.fetchCalls.length, 1);
});

ok("with page context switched off the frame is never told the page", async () => {
	const t = makeLoader({ config: Object.assign({}, CONFIG, { page_context: 0 }) });
	await t.openAndReady();
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	assert.equal(t.frame().got(TO_FRAME.PAGE).length, 0);
	assert.equal(t.win.frappe.router.count("change"), 0);
});

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------
ok("the button, Alt+T and the frame's own requests open and close the panel", async () => {
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

	t.fromFrame(FROM_FRAME.READY);
	await settle();
	t.fromFrame(FROM_FRAME.CLOSE);
	assert.equal(t.loader.isOpen(), false);
	t.fromFrame(FROM_FRAME.TOGGLE);
	assert.equal(t.loader.isOpen(), true);
	assert.equal(t.frame().got(TO_FRAME.CLOSED).length, 1);
	assert.equal(t.frame().got(TO_FRAME.OPENED).length, 2);
});

ok("the waiting panel has a Close that works before the frame is ready", () => {
	const t = makeLoader();
	t.loader.toggle(true);
	const close = t.panel.find("triton-close");
	assert.ok(close, "a phone would be stuck under a blank full-screen sheet");
	close.emit("click");
	assert.equal(t.loader.isOpen(), false);
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
	await t.openAndReady();
	assert.ok(t.frame().focused >= 1, "focus did not go to the chat on open");
	t.win.document.activeElement = t.frame();
	t.loader.toggle(false);
	assert.equal(t.fab.focused, 1);
});

// ---------------------------------------------------------------------------
// Records the frame asks for
// ---------------------------------------------------------------------------
ok("open-record moves the Desk with frappe.set_route, and closes the sheet on a phone", async () => {
	const t = makeLoader();
	await t.openAndReady();
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { doctype: "Sales Order", name: "SO-0001" });
	assert.deepEqual(t.win.routes, [["Form", "Sales Order", "SO-0001"]]);
	assert.equal(t.loader.isOpen(), true, "closed on a desktop, where the record shows beside the panel");

	t.win.phone = true;
	t.fromFrame(FROM_FRAME.OPEN_RECORD, { path: "/app/project/PROJ-0002" });
	assert.deepEqual(t.win.routes[1], ["Form", "Project", "PROJ-0002"]);
	assert.equal(t.loader.isOpen(), false);
});

ok("open-record refuses everything that is not a readable DocType and a name", async () => {
	const t = makeLoader();
	await t.openAndReady();
	const refused = [
		{ doctype: "User", name: "Administrator" }, // not in this person's can_read
		{ doctype: "Sales Order", name: 42 },
		{ doctype: "Sales Order", name: "x".repeat(141) },
		{ doctype: "Sales Order", name: "SO\u0000" },
		{ doctype: "../../etc", name: "x" },
		{ path: "https://evil.example.com/app/sales-order/SO-0001" },
		{ path: "//evil.example.com/app/sales-order/SO-0001" },
		{ path: "/app/sales-order/SO-0001?x=1" },
		{ path: "/app/triton-settings" }, // a slug that is not a readable DocType
		{ path: "/api/method/logout" },
		{ path: "javascript:alert(1)" },
		{ path: "/app/sales-order/%E0%A4%A" },
		{ url: "https://evil.example.com" },
		"/app/sales-order/SO-0001",
		null,
	];
	for (const target of refused) {
		t.fromFrame(FROM_FRAME.OPEN_RECORD, target && typeof target === "object" ? target : { path: undefined, value: target });
	}
	assert.deepEqual(t.win.routes, []);
});

ok("recordRoute: both spellings, lists, encoded names, and no list of DocTypes means no", () => {
	const readable = ["Sales Order", "Project"];
	assert.deepEqual(recordRoute({ doctype: "Sales Order", name: "SO-0001" }, readable), ["Form", "Sales Order", "SO-0001"]);
	assert.deepEqual(recordRoute({ doctype: "Sales Order" }, readable), ["List", "Sales Order"]);
	assert.deepEqual(recordRoute({ path: "/app/sales-order" }, readable), ["List", "Sales Order"]);
	assert.deepEqual(recordRoute({ path: "/desk/sales-order/SO%2F0001%20A" }, readable), ["Form", "Sales Order", "SO/0001 A"]);
	assert.equal(recordRoute({ doctype: "Sales Order", name: "SO-0001" }, undefined), null);
	assert.equal(recordRoute({ doctype: "Customer", name: "C-1" }, readable), null);
	assert.equal(recordRoute({ doctype: "sales order", name: "SO-0001" }, readable), null);
});

// ---------------------------------------------------------------------------
// SapphireTriton.ask() and the page source
// ---------------------------------------------------------------------------
ok("ask() opens the panel, waits for ready, and is delivered once", async () => {
	const t = makeLoader();
	const context = { type: "document", doctype: "Customer", name: "C-1", fn() {} };
	assert.equal(t.loader.ask("Draft a course", context), true);
	assert.equal(t.loader.isOpen(), true);
	assert.equal(t.frame().got(TO_FRAME.ASK).length, 0);
	t.fromFrame(FROM_FRAME.READY);
	await settle();
	t.fromFrame(FROM_FRAME.READY);
	await settle();
	const asks = t.frame().got(TO_FRAME.ASK);
	assert.equal(asks.length, 1);
	assert.equal(asks[0].data.prompt, "Draft a course");
	assert.deepEqual(asks[0].data.context, { type: "document", doctype: "Customer", name: "C-1" });
});

ok("a route change reports the new page a moment later, and never the same page twice", async () => {
	const t = makeLoader();
	await t.openAndReady();
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
	assert.equal(pages().length, 2, "the same page was reported twice");

	t.page.ref = null;
	t.win.frappe.router.emit("change");
	t.win.advance(SETTLE_MS);
	assert.equal(pages()[2], null);

	// Reopening reports again: the record may have been edited meanwhile.
	t.loader.toggle(false);
	t.loader.toggle(true);
	assert.equal(pages().length, 4);
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
// 4. The gate, and the classic widget left alone
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
