#!/usr/bin/env node
/**
 * P12 of the planner UX redesign: the first-visit tour and "?" for the new tabs
 * (docs/planner-ux-redesign.md section 7), run under node against the REAL code.
 *
 *   1. planner_kit/tour.js on a small fake DOM: a modal dialog named by its heading and described by its
 *      sentence, the count in a polite live region, focus on Next and trapped (Tab and Shift+Tab wrap, a
 *      focus that strayed is pulled back), Esc / Skip / a press outside each skip, the arrows step, the last
 *      step says Done, focus goes back, a key pressed inside never reaches the page behind, every word is
 *      set as text, and steps are cleaned (junk dropped, at most eight). It takes no part in the browser's
 *      history: the fake window records any history call and there must be none.
 *   2. The two pages' step lists (PPTOUR_PURE, MPTOUR_PURE): four to six steps for the people the tour is
 *      for, plain words, one idea each; the Overview step only for the Executive Team; the editing steps only
 *      for people who can edit; a phone's tour is Today's, a desktop's is the tabs; the technician's My day.
 *   3. The Project Planner and the Maintenance Planner, the real page scripts on an instance with a fake kit
 *      and a fake server: the first visit shows the tour (after the landing has settled) and marks it on the
 *      server; the second visit does not; Skip, Esc and a press outside leave it seen; Take the tour from
 *      "?" shows it again and marks nothing; a phone gets the phone tour for its own flag, a desktop the
 *      desktop's; nothing opens before the saved views have answered, the data is drawn and the toolbar is
 *      built, and none of that throws when a piece is missing; it waits while a drawer, a menu, a dialog or a
 *      drag is on screen and gives up after a minute; a route change closes it; its keys are its own (the
 *      page's shortcuts stand down); a failed request tells this browser instead; and from the first landing
 *      to the last close there is no history entry, no route and no guard overlay.
 *
 * Run: node scripts/test_planner_tour.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const KIT = path.join(APP, "public", "js", "planner_kit");
const PP_JS = path.join(APP, "project_enhancements", "page", "project_planner", "project_planner.js");
const MP_JS = path.join(APP, "sapphire_maintenance", "page", "maintenance_planner", "maintenance_planner.js");

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => {
	for (let i = 0; i < 6; i++) await tick();
};
const translate = (text, args) => String(text).replace(/\{(\d+)\}/g, (all, i) => (args && args[i] !== undefined ? args[i] : all));

// ------------------------------------------------------------------ a fake DOM

class El {
	constructor(doc, tag) {
		this.ownerDocument = doc;
		this.nodeType = 1;
		this.tagName = String(tag).toUpperCase();
		this.childNodes = [];
		this.parentNode = null;
		this.attrs = {};
		this.listeners = [];
		this.style = {};
		this.className = "";
		this.ownText = "";
		this.html = null;
		this.disabled = false;
		this.hidden = false;
		this.type = "";
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
	get id() {
		return this.attrs.id || "";
	}
	set id(value) {
		this.attrs.id = String(value);
	}
	get firstChild() {
		return this.childNodes[0] || null;
	}
	get textContent() {
		return this.ownText + this.childNodes.map((node) => node.textContent).join("");
	}
	set textContent(value) {
		this.childNodes.forEach((node) => (node.parentNode = null));
		this.childNodes = [];
		this.ownText = String(value);
	}
	set innerHTML(value) {
		this.textContent = "";
		this.html = String(value);
	}
	get innerHTML() {
		return this.html || "";
	}
	appendChild(node) {
		if (node.parentNode) node.parentNode.removeChild(node);
		node.parentNode = this;
		this.childNodes.push(node);
		return node;
	}
	removeChild(node) {
		const i = this.childNodes.indexOf(node);
		if (i >= 0) this.childNodes.splice(i, 1);
		node.parentNode = null;
		return node;
	}
	contains(other) {
		for (let node = other; node; node = node.parentNode) if (node === this) return true;
		return false;
	}
	descendants() {
		return this.childNodes.flatMap((node) => [node].concat(node.descendants()));
	}
	querySelectorAll(selector) {
		return this.descendants().filter((node) => matches(node, selector));
	}
	querySelector(selector) {
		return this.querySelectorAll(selector)[0] || null;
	}
	closest(selector) {
		for (let node = this; node && node.nodeType === 1; node = node.parentNode) if (matches(node, selector)) return node;
		return null;
	}
	setAttribute(name, value) {
		this.attrs[name] = String(value);
	}
	getAttribute(name) {
		return name in this.attrs ? this.attrs[name] : null;
	}
	addEventListener(type, fn, capture) {
		this.listeners.push({ type, fn, capture: !!capture });
	}
	removeEventListener(type, fn, capture) {
		const i = this.listeners.findIndex((l) => l.type === type && l.fn === fn && l.capture === !!capture);
		if (i >= 0) this.listeners.splice(i, 1);
	}
	focus() {
		this.ownerDocument.activeElement = this;
	}
	click() {
		return fire(this, "click");
	}
}

function matches(node, selector) {
	return String(selector)
		.split(",")
		.map((s) => s.trim())
		.some((one) => {
			if (!one) return false;
			if (one.startsWith(".")) return one.slice(1).split(".").every((c) => node.classList.contains(c));
			const tag = /^([a-z0-9]+)$/i.exec(one);
			if (tag) return node.tagName === tag[1].toUpperCase();
			return false;
		});
}

function makeDoc() {
	const doc = { listeners: [], activeElement: null };
	doc.createElement = (tag) => new El(doc, tag);
	doc.documentElement = new El(doc, "html");
	doc.body = new El(doc, "body");
	doc.documentElement.appendChild(doc.body);
	doc.addEventListener = (type, fn, capture) => doc.listeners.push({ type, fn, capture: !!capture });
	doc.removeEventListener = (type, fn, capture) => {
		const i = doc.listeners.findIndex((l) => l.type === type && l.fn === fn && l.capture === !!capture);
		if (i >= 0) doc.listeners.splice(i, 1);
	};
	doc.querySelector = (selector) => doc.body.querySelector(selector);
	doc.getElementById = () => null;
	doc.activeElement = doc.body;
	return doc;
}

// An event at `target`: the document's capture listeners, then the target and its ancestors (bubble),
// then the document's bubble listeners.
function fire(target, type, extra) {
	const doc = target.ownerDocument;
	const ev = Object.assign(
		{
			type,
			target,
			defaultPrevented: false,
			stopped: false,
			preventDefault() {
				this.defaultPrevented = true;
			},
			stopPropagation() {
				this.stopped = true;
			},
		},
		extra || {}
	);
	doc.listeners.filter((l) => l.type === type && l.capture).forEach((l) => l.fn(ev));
	if (ev.stopped) return ev;
	for (let node = target; node && node.nodeType === 1 && !ev.stopped; node = node.parentNode) {
		node.listeners.filter((l) => l.type === type).forEach((l) => l.fn(ev));
	}
	if (!ev.stopped) doc.listeners.filter((l) => l.type === type && !l.capture).forEach((l) => l.fn(ev));
	return ev;
}

const key = (target, k, extra) => fire(target, "keydown", Object.assign({ key: k }, extra || {}));

// ------------------------------------------------------------------ the kit's tour as a script

function loadTour() {
	const source = fs
		.readFileSync(path.join(KIT, "tour.js"), "utf8")
		.replace(/^import .*?;\s*$/gm, "")
		.replace(/^export (const|function|class) /gm, "$1 ");
	const ctx = { console, Array, Object, String, Number, JSON, Math };
	vm.createContext(ctx);
	vm.runInContext(`${source}\nthis.mod = { create_tour, clean_steps, count_text, MAX_STEPS };`, ctx);
	return ctx.mod;
}

// A window whose history is recorded: the tour must never touch it.
function historyLog() {
	const log = [];
	const history = {
		pushState: (...a) => log.push(["push", a]),
		replaceState: (...a) => log.push(["replace", a]),
		back: () => log.push(["back"]),
		forward: () => log.push(["forward"]),
		go: (...a) => log.push(["go", a]),
	};
	return { log, history };
}

function tourEnv() {
	const mod = loadTour();
	const doc = makeDoc();
	const warned = [];
	const tour = mod.create_tour({ doc, t: translate, warn: (e) => warned.push(String(e)) });
	return { mod, doc, tour, warned };
}

const STEPS = [
	{ title: "One", text: "First thing." },
	{ title: "Two", text: "Second thing.", keys: "Esc skips." },
	{ title: "Three", text: "Third thing." },
];

// ------------------------------------------------------------------ 1. the kit's tour

test("The kit: steps are cleaned, and the count reads in words", () => {
	const { mod } = tourEnv();
	const cleaned = mod.clean_steps([
		{ title: "  A   title ", text: " some \n text ", keys: " k " },
		{ title: "", text: "no heading" },
		{ title: "no sentence", text: "" },
		{ title: 3, text: "number" },
		null,
		"x",
		{ title: "x".repeat(200), text: "y".repeat(900) },
	]);
	check("junk is dropped, spaces collapsed", cleaned.map((s) => [s.title, s.text, s.keys]).slice(0, 1), [["A title", "some text", "k"]]);
	check("long words are capped", [cleaned[1].title.length, cleaned[1].text.length], [80, 320]);
	check("not a list: nothing", [mod.clean_steps(null), mod.clean_steps("x"), mod.clean_steps({})], [[], [], []]);
	const many = Array.from({ length: 20 }, (_v, i) => ({ title: `T${i}`, text: "t" }));
	check("at most eight steps", mod.clean_steps(many).length, mod.MAX_STEPS);
	check("MAX_STEPS", mod.MAX_STEPS, 8);
	check("the count", [mod.count_text(0, 5, translate), mod.count_text(4, 5, translate)], ["Step 1 of 5", "Step 5 of 5"]);
	const { tour } = tourEnv();
	check("a tour with no usable step opens nothing", [tour.open({ steps: [{ title: "", text: "" }] }), tour.is_open()], [null, false]);
});

test("The kit: a modal dialog named by its heading, the count in a live region, focus on Next", () => {
	const { doc, tour } = tourEnv();
	const opener = doc.createElement("button");
	doc.body.appendChild(opener);
	opener.focus();
	const handle = tour.open({ title: "Project Planner tour", steps: STEPS, owner: "pp" });
	const overlay = handle.element();
	const card = overlay.querySelector(".pk-tour-card");
	check("it is on the page", [overlay.parentNode === doc.body, tour.is_open(), tour.current() === handle], [true, true, true]);
	check("the owner is recorded", overlay.getAttribute("data-owner"), "pp");
	check("a modal dialog", [card.getAttribute("role"), card.getAttribute("aria-modal")], ["dialog", "true"]);
	const title = overlay.querySelector(".pk-tour-title");
	const text = overlay.querySelector(".pk-tour-text");
	check("labelled by its heading, described by its sentence", [card.getAttribute("aria-labelledby"), card.getAttribute("aria-describedby")], [title.id, text.id]);
	check("the heading is a heading", title.tagName, "H2");
	check("named for a reader that ignores labelledby", card.getAttribute("aria-label"), "Project Planner tour");
	const live = overlay.querySelector(".pk-tour-step");
	check("one polite live region holds the count, heading and sentence", [live.getAttribute("aria-live"), live.contains(title), live.contains(text), live.contains(overlay.querySelector(".pk-tour-count"))], ["polite", true, true, true]);
	check("step 1", [overlay.querySelector(".pk-tour-count").textContent, title.textContent, text.textContent], ["Step 1 of 3", "One", "First thing."]);
	check("no keys line on a step without one", overlay.querySelector(".pk-tour-keys").hidden, true);
	const next = overlay.querySelector(".pk-tour-next");
	const back = overlay.querySelector(".pk-tour-back");
	const skip = overlay.querySelector(".pk-tour-skip");
	check("focus is on Next", doc.activeElement === next, true);
	check("Back is off on the first step", back.disabled, true);
	check("the buttons are buttons with names", [next.type, skip.type, skip.getAttribute("aria-label"), next.getAttribute("aria-label")], ["button", "button", "Skip the tour", "Next step"]);
	check("the dots are decoration, with the current one marked", [overlay.querySelector(".pk-tour-dots").getAttribute("aria-hidden"), overlay.querySelectorAll(".pk-tour-dot").length, overlay.querySelectorAll(".pk-tour-dot-on").length], ["true", 3, 1]);

	next.click();
	check("Next goes on", [overlay.querySelector(".pk-tour-count").textContent, title.textContent, handle.index()], ["Step 2 of 3", "Two", 1]);
	check("its keys line shows", [overlay.querySelector(".pk-tour-keys").hidden, overlay.querySelector(".pk-tour-keys").textContent], [false, "Esc skips."]);
	check("Back is on, focus stays where it was", [back.disabled, doc.activeElement === next], [false, true]);
	back.click();
	check("Back goes back", [title.textContent, handle.index(), back.disabled], ["One", 0, true]);
	next.click();
	next.click();
	check("the last step says Done", [next.textContent, next.getAttribute("aria-label"), overlay.querySelector(".pk-tour-count").textContent], ["Done", "Done, close the tour", "Step 3 of 3"]);
	const closed = [];
	handle.opts.on_close = (how, index) => closed.push([how, index]);
	next.click();
	check("Done closes it as done, at the last step", closed, [["done", 2]]);
	check("it is gone from the page, and focus is back", [!!overlay.parentNode, tour.is_open(), doc.activeElement === opener], [false, false, true]);
	check("its key listener went with it", doc.listeners.filter((l) => l.type === "keydown").length, 0);
});

test("The kit: skippable by Esc, the Skip button and a press outside; a press inside is not outside", () => {
	const reasons = [];
	const run = (how) => {
		const { doc, tour } = tourEnv();
		const opener = doc.createElement("button");
		doc.body.appendChild(opener);
		opener.focus();
		const handle = tour.open({ steps: STEPS, on_close: (h, i) => reasons.push([h, i]) });
		const overlay = handle.element();
		handle.next();
		if (how === "esc") {
			const ev = key(doc.activeElement, "Escape");
			check("Esc is the tour's: it is not passed on", [ev.defaultPrevented, ev.stopped], [true, true]);
		}
		if (how === "skip") overlay.querySelector(".pk-tour-skip").click();
		if (how === "outside") {
			fire(overlay.querySelector(".pk-tour-card"), "pointerdown");
			fire(overlay.querySelector(".pk-tour-card"), "click");
			check("a press on the card keeps it open", tour.is_open(), true);
			fire(overlay.querySelector(".pk-tour-title"), "pointerdown");
			fire(overlay.querySelector(".pk-tour-title"), "click");
			check("nor a press on its words", tour.is_open(), true);
			// Selecting a word: the press begins in the card and the release is outside it, so the click lands on
			// the overlay. That is not a tap outside.
			fire(overlay.querySelector(".pk-tour-text"), "pointerdown");
			fire(overlay, "click");
			check("a drag that begins in the card and ends outside keeps it open", tour.is_open(), true);
			fire(overlay, "click");
			check("and a click with no press behind it (a script's) is not a tap", tour.is_open(), true);
			fire(overlay, "pointerdown");
			check("the press alone closes nothing: its click would fall through to the page", tour.is_open(), true);
			fire(overlay, "click");
		}
		check(`${how}: closed, gone, focus back`, [tour.is_open(), !!overlay.parentNode, doc.activeElement === opener], [false, false, true]);
	};
	["esc", "skip", "outside"].forEach(run);
	check("the three reasons, each at the step it was on", reasons, [["esc", 1], ["skip", 1], ["outside", 1]]);
	const { tour } = tourEnv();
	const h = tour.open({ steps: STEPS, on_close: (how) => reasons.push(["x", how]) });
	tour.close("route");
	tour.close("again");
	check("the page can close it with a reason, once", [reasons.slice(3), h.is_open()], [[["x", "route"]], false]);
});

test("The kit: Tab stays inside, a stray focus is pulled back, the arrows step, and keys stay in the card", () => {
	const { doc, tour } = tourEnv();
	const outside = doc.createElement("button");
	doc.body.appendChild(outside);
	const page_keys = [];
	doc.addEventListener("keydown", (e) => page_keys.push(e.key), false);
	const handle = tour.open({ steps: STEPS });
	const overlay = handle.element();
	const skip = overlay.querySelector(".pk-tour-skip");
	const back = overlay.querySelector(".pk-tour-back");
	const next = overlay.querySelector(".pk-tour-next");
	// On the first step Back is disabled, so the order is Skip, Next.
	next.focus();
	let ev = key(next, "Tab");
	check("Tab from the last button wraps to the first", [ev.defaultPrevented, doc.activeElement === skip], [true, true]);
	ev = key(skip, "Tab", { shiftKey: true });
	check("Shift+Tab from the first wraps to the last", [ev.defaultPrevented, doc.activeElement === next], [true, true]);
	skip.focus();
	ev = key(skip, "Tab");
	check("Tab in the middle is left to the browser", ev.defaultPrevented, false);
	outside.focus();
	ev = key(outside, "Tab");
	check("a focus that strayed is pulled back in", [ev.defaultPrevented, doc.activeElement === skip], [true, true]);
	outside.focus();
	key(outside, "Tab", { shiftKey: true });
	check("Shift+Tab from outside lands on the last", doc.activeElement === next, true);

	ev = key(next, "ArrowRight");
	check("ArrowRight steps on", [handle.index(), ev.defaultPrevented], [1, true]);
	check("and the buttons are Skip, Back, Next now", [back.disabled, skip.disabled], [false, false]);
	key(next, "ArrowLeft");
	check("ArrowLeft steps back", handle.index(), 0);
	key(next, "ArrowLeft");
	check("and stops at the first", handle.index(), 0);
	key(next, "ArrowRight");
	key(next, "ArrowRight");
	key(next, "ArrowRight");
	check("and at the last", handle.index(), 2);
	key(next, "ArrowRight", { ctrlKey: true });
	check("a modified arrow is the browser's", handle.index(), 2);

	page_keys.length = 0;
	key(next, "t");
	key(next, "?");
	key(next, "1");
	check("every other key stays in the card: the page behind never sees it", page_keys, []);
	tour.close();
	key(outside, "t");
	check("once it is closed the page hears keys again", page_keys, ["t"]);
});

test("The kit: a dialog that opens over the tour owns the keyboard (its Esc, its Tab)", () => {
	const { doc, tour } = tourEnv();
	const handle = tour.open({ steps: STEPS });
	const modal = doc.createElement("div");
	modal.className = "modal show";
	const field = doc.createElement("button");
	modal.appendChild(field);
	doc.body.appendChild(modal);
	field.focus();
	let ev = key(field, "Tab");
	check("Tab in the dialog is not pulled back into the tour", [ev.defaultPrevented, doc.activeElement === field], [false, true]);
	ev = key(field, "Escape");
	check("Esc in the dialog does not skip the tour behind it", [ev.defaultPrevented, tour.is_open()], [false, true]);
	ev = key(field, "ArrowRight");
	check("nor do the arrows step it", handle.index(), 0);
	doc.body.removeChild(modal);
	key(handle.element().querySelector(".pk-tour-next"), "Escape");
	check("once the dialog is gone the tour has its keys again", tour.is_open(), false);
});

test("The kit: every word is text, and a second tour replaces the first", () => {
	const { doc, tour } = tourEnv();
	const hostile = '<img src=x onerror="boom()"> & "quoted"';
	const handle = tour.open({ title: hostile, steps: [{ title: hostile, text: hostile, keys: hostile }] });
	const overlay = handle.element();
	const found = overlay.descendants().filter((n) => n.html !== null);
	check("nothing is written as markup", found.length, 0);
	check("the heading shows the words as typed", overlay.querySelector(".pk-tour-title").textContent, hostile);
	check("so does the sentence", overlay.querySelector(".pk-tour-text").textContent, hostile);
	const closed = [];
	handle.opts.on_close = (how) => closed.push(how);
	const second = tour.open({ steps: STEPS });
	check("a second tour closes the first as replaced, and is the only one", [closed, tour.current() === second, doc.body.descendants().filter((n) => n.classList.contains("pk-tour")).length], [["replaced"], true, 1]);
	check("one key listener, not two", doc.listeners.filter((l) => l.type === "keydown").length, 1);
	const one = tour.open({ steps: [{ title: "Only", text: "One step." }] });
	check("a one-step tour says Done at once", one.element().querySelector(".pk-tour-next").textContent, "Done");
	check("start can open it on a later step", [tour.open({ steps: STEPS, start: 2 }).index(), tour.open({ steps: STEPS, start: 99 }).index(), tour.open({ steps: STEPS, start: -3 }).index()], [2, 2, 0]);
});

test("The kit: the tour never touches the browser's history", () => {
	const src = fs.readFileSync(path.join(KIT, "tour.js"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
	const code = src
		.split("\n")
		.filter((line) => !line.trim().startsWith("//"))
		.join("\n");
	["pushState", "replaceState", "history", "guard", "popstate", "set_route", "location"].forEach((word) => {
		check(`tour.js never says ${word}`, code.includes(word), false);
	});
	// It is given no window at all: the only things it can reach are the document and the words.
	const { tour, doc } = tourEnv();
	const handle = tour.open({ steps: STEPS });
	handle.next();
	handle.back();
	key(doc.activeElement, "Escape");
	check("a whole open, step and Esc needs no window and leaves nothing open", tour.is_open(), false);
});

// ------------------------------------------------------------------ 2. the page scripts

function loadPage(file, env) {
	const doc = makeDoc();
	const route = { now: [] };
	const store = new Map();
	const timers = { list: [], ran: 0 };
	const hist = historyLog();
	const sets = [];
	const user_roles = env.roles || [];
	const frappe = {
		pages: new Proxy({}, { get: (target, k) => (target[k] = target[k] || {}) }),
		utils: {
			escape_html: (value) =>
				String(value == null ? "" : value)
					.replace(/&/g, "&amp;")
					.replace(/</g, "&lt;")
					.replace(/>/g, "&gt;")
					.replace(/"/g, "&quot;"),
		},
		datetime: { get_today: () => "2026-10-22" },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		session: { user: env.user || "nik@example.com" },
		user: { has_role: (role) => user_roles.includes(role) },
		get_route: () => route.now,
		set_route: (...args) => sets.push(args),
		show_alert: () => null,
		call: () => Promise.resolve({ message: {} }),
		route_options: null,
	};
	const window = {
		localStorage: env.storage_blocked
			? {
					getItem() {
						throw new Error("blocked");
					},
					setItem() {
						throw new Error("blocked");
					},
			  }
			: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) },
		matchMedia: (query) => ({ matches: /max-width:\s*(\d+)px/.test(query) ? !!env.phone && Number(/max-width:\s*(\d+)px/.exec(query)[1]) >= 767 : false }),
		history: hist.history,
		cur_dialog: null,
	};
	const ctx = {
		frappe,
		window,
		$: null,
		__: translate,
		document: doc,
		console,
		moment: (value) => ({ isValid: () => true, format: () => String(value) }),
		clearTimeout: (id) => {
			const t = timers.list.find((x) => x.id === id);
			if (t) t.dead = true;
		},
		Promise,
	};
	if (!env.no_timers) {
		ctx.setTimeout = (fn, ms) => {
			const t = { fn, ms, id: timers.list.length + 1, dead: false };
			timers.list.push(t);
			return t.id;
		};
	}
	vm.createContext(ctx);
	vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
	// Run what is pending, and what that schedules in turn, a bounded number of rounds.
	const flush = () => {
		for (let round = 0; round < 400; round++) {
			const live = timers.list.filter((t) => !t.dead && !t.done);
			if (!live.length) return;
			live.forEach((t) => {
				t.done = true;
				timers.ran += 1;
				t.fn();
			});
		}
	};
	return { ctx, doc, frappe, window, route, store, timers, flush, hist, sets };
}

// The real tour over the fake DOM, in the shape of the kit the pages hold; a fake server behind its calls.
function makeKit(doc, server) {
	const mod = loadTour();
	const kit = {
		tour: mod.create_tour({ doc, t: translate, warn: () => null }),
		drawer: {
			_current: null,
			current: () => kit.drawer._current,
			navigate: (fn) => {
				kit.log.push("drawer.navigate");
				kit.drawer._current = null;
				return fn();
			},
		},
		guard: {
			_top: null,
			top: () => kit.guard._top,
			open: () => kit.log.push("guard.open"),
			close: () => kit.log.push("guard.close"),
		},
		log: [],
		quiet_calls: [],
		fail: false,
		quiet_call: (method, args) => {
			kit.quiet_calls.push([method.split(".").pop(), args]);
			if (kit.fail) return Promise.reject({ status: 0 });
			if (method.endsWith("mark_tour_seen")) {
				server.rows[args.planner][args.kind] = true;
				server.marks.push(args);
				return Promise.resolve({ planner: args.planner, tour: server.rows[args.planner] });
			}
			return Promise.resolve({});
		},
		legend: (sections, opts) => {
			kit.legended = { sections, opts };
			kit.drawer._current = { legend: true };
		},
	};
	return kit;
}

const newServer = () => ({
	rows: { project: { desktop: false, phone: false }, maintenance: { desktop: false, phone: false } },
	marks: [],
});

function ppPage(opts) {
	const o = Object.assign({ view: "lookahead", phone: false, roles: [], server: newServer() }, opts || {});
	const env = loadPage(PP_JS, o);
	const ProjectPlanner = vm.runInContext("ProjectPlanner", env.ctx);
	const pp = Object.create(ProjectPlanner.prototype);
	const kit = makeKit(env.doc, o.server);
	const $body = { hasClass: () => false };
	Object.assign(pp, {
		view: o.view,
		data: o.no_data ? null : { can_edit: o.can_edit !== false, projects: [] },
		today_data: o.today === undefined ? null : o.today,
		drag: null,
		$body,
		$p6a_help: { 0: env.doc.createElement("button") },
		page: { wrapper: null },
		p6a: o.no_kit ? { kit: null } : { kit },
		tb: o.no_toolbar ? { kit: null } : { kit },
	});
	pp.init_tour();
	env.route.now = o.route || ["project-planner", o.view, "2026-10-22"];
	return Object.assign(env, { pp, kit, server: o.server, opts: o });
}

function mpPage(opts) {
	const o = Object.assign({ view: "month", phone: false, roles: [], server: newServer() }, opts || {});
	const env = loadPage(MP_JS, o);
	const MaintenancePlanner = vm.runInContext("MaintenancePlanner", env.ctx);
	const mp = Object.create(MaintenancePlanner.prototype);
	const kit = makeKit(env.doc, o.server);
	Object.assign(mp, {
		view: o.view,
		data: o.no_data ? null : { can_schedule: o.can_schedule !== false, can_create_visits: true },
		drag: null,
		$body: { hasClass: () => false },
		$p6a_help: { 0: env.doc.createElement("button") },
		page: { wrapper: null },
		tech_by_user: o.has_day ? { [env.frappe.session.user]: { resource: "RES-1", enabled: true } } : {},
		p6a: o.no_kit ? { kit: null } : { kit },
		tb: o.no_toolbar ? { kit: null } : { kit },
	});
	mp.init_tour();
	env.route.now = o.route || ["maintenance-planner", o.view, "2026-10-22"];
	return Object.assign(env, { mp, kit, server: o.server, opts: o });
}

const titles = (steps) => steps.map((s) => s.title);
const shown = (kit) => (kit.tour.current() ? kit.tour.current().steps.map((s) => s.title) : null);

test("Project Planner steps: four to six for the people it is for, plain words, one idea each", () => {
	const { ctx } = ppPage();
	const pure = vm.runInContext("PPTOUR_PURE", ctx);
	const editor = pure.steps("desktop", { executive: false, can_edit: true });
	check("an editor's desktop tour", titles(editor), ["Tabs along the top", "Drag to move", "Draft until you publish", "Notify crew?", "Help is one click away"]);
	const exec = pure.steps("desktop", { executive: true, can_edit: true });
	check("the Executive Team's has the Overview step, second", titles(exec), ["Tabs along the top", "Overview is for managers", "Drag to move", "Draft until you publish", "Notify crew?", "Help is one click away"]);
	check("and nobody else's does", [editor, pure.steps("desktop", { executive: false, can_edit: false })].some((steps) => titles(steps).includes("Overview is for managers")), false);
	const reader = pure.steps("desktop", { executive: false, can_edit: false });
	check("someone who cannot edit is told nothing about dragging, drafts or notices, and how to look", titles(reader), ["Tabs along the top", "Click for the details", "Help is one click away"]);
	check("an Executive Team reader gets the Overview too", titles(pure.steps("desktop", { executive: true, can_edit: false })), ["Tabs along the top", "Overview is for managers", "Click for the details", "Help is one click away"]);
	const phone = pure.steps("phone", { executive: true, can_edit: true });
	check("a phone's tour is Today's, and the same whoever it is", titles(phone), ["This is your day", "Open the route", "Block my time", "The rest of the planner", "Help is one tap away"]);
	check("no Overview on a phone", titles(phone).includes("Overview is for managers"), false);
	[editor, exec, phone].forEach((steps) => check("four to six steps", steps.length >= 4 && steps.length <= 6, true));
	[editor, exec, reader, phone].forEach((steps) =>
		steps.forEach((step) => {
			check(`"${step.title}" has words`, !!step.title && !!step.text, true);
			check(`"${step.title}" is short: a heading and a sentence or two`, step.title.length <= 40 && step.text.length <= 260, true);
			check(`"${step.title}" is one idea, in sentences`, step.text.split(/[.?!]\s/).length <= 4, true);
		})
	);
	check("the Draft step names Publish… and the baseline", /Publish…/.test(editor[2].text) && /baseline/.test(editor[2].text), true);
	check("the Notify step names both buttons and the two weeks", /Notify tells/.test(editor[3].text) && /Don't notify/.test(editor[3].text) && /two weeks/.test(editor[3].text), true);
	check("the drag step says the tasks that follow move, and Ctrl/⌘ moves one", /wait on it move with it/.test(editor[1].text) && /Ctrl \(⌘/.test(editor[1].keys), true);
	check("the kinds the server keeps are the page's", vm.runInContext("PPTOUR.kinds", ctx), ["desktop", "phone"]);
});

test("Maintenance Planner steps: a scheduler's, a technician's, a phone's", () => {
	const { ctx } = mpPage();
	const pure = vm.runInContext("MPTOUR_PURE", ctx);
	const scheduler = pure.steps("desktop", { can_schedule: true, has_day: false });
	check("a scheduler's desktop tour", titles(scheduler), ["Month, Week and Crew", "Drag to reschedule", "Project work is a thin strip", "Resources available", "Who hears about a change", "Help is one click away"]);
	const tech = pure.steps("desktop", { can_schedule: false, has_day: true });
	check("a technician's: no dragging or notices, but My day", titles(tech), ["Month, Week and Crew", "Project work is a thin strip", "Resources available", "My day", "Help is one click away"]);
	const viewer = pure.steps("desktop", { can_schedule: false, has_day: false });
	check("someone with neither", titles(viewer), ["Month, Week and Crew", "Project work is a thin strip", "Resources available", "Help is one click away"]);
	const phone = pure.steps("phone", { can_schedule: true, has_day: true });
	check("a phone's, with a day", titles(phone), ["Your visits", "Project work is a strip", "My day", "Filters and tools", "Help is one tap away"]);
	check("a phone's, without one", titles(pure.steps("phone", { has_day: false })), ["Your visits", "Project work is a strip", "Filters and tools", "Help is one tap away"]);
	[scheduler, tech, viewer, phone].forEach((steps) => check("four to six steps", steps.length >= 4 && steps.length <= 6, true));
	check("the scheduler's Resources step offers the drag, the others' does not", [/drag it onto a visit/.test(scheduler[3].text), /drag/.test(tech[2].text)], [true, false]);
	check("the notice step names Notify crew? and Draft mode", /Notify crew\?/.test(scheduler[4].text) && /Draft mode/.test(scheduler[4].text), true);
	[scheduler, tech, viewer, phone].forEach((steps) =>
		steps.forEach((step) => check(`"${step.title}" is short`, step.title.length <= 40 && step.text.length <= 260, true))
	);
	check("the screens a view of this planner opens on", vm.runInContext("MPTOUR.screens", ctx), ["month", "week", "crew"]);
});

// ------------------------------------------------------------------ 3. the Project Planner

test("PP: the first visit shows the tour once the landing has settled, and marks it on the server", async () => {
	const env = ppPage();
	const { pp, kit, server } = env;
	env.flush();
	check("nothing before the saved views have answered", [kit.tour.is_open(), server.marks.length], [false, 0]);
	pp.tour_take({ desktop: false, phone: false });
	check("answered: a check is scheduled, not run at once", [kit.tour.is_open(), env.timers.list.filter((t) => !t.dead && !t.done).length], [false, 1]);
	check("0.7 s after the landing, so a redirect settles first", env.timers.list.filter((t) => !t.dead && !t.done)[0].ms, 700);
	env.flush();
	check("then the desktop tour is on screen", [kit.tour.is_open(), shown(kit)], [true, ["Tabs along the top", "Drag to move", "Draft until you publish", "Notify crew?", "Help is one click away"]]);
	await settle();
	check("and the server was told, once, for this planner and this device", [kit.quiet_calls, server.marks], [[["mark_tour_seen", { planner: "project", kind: "desktop" }]], [{ planner: "project", kind: "desktop" }]]);
	check("the page remembers it for the session", pp.tour_state().seen, { desktop: true, phone: false });
	check("the browser was not asked to remember it (the server is the record)", [...env.store.keys()], []);
	check("the planner's shortcuts treat it as a dialog", pp.tour_open(), true);
});

test("PP: the second visit does not show it; Skip, Esc and a press outside each leave it seen", async () => {
	const server = newServer();
	const first = ppPage({ server });
	first.pp.tour_take(server.rows.project);
	first.flush();
	check("first visit: shown", first.kit.tour.is_open(), true);
	first.doc.querySelector(".pk-tour-skip").click();
	await settle();
	check("Skip closes it", [first.kit.tour.is_open(), first.pp.tour_state().handle], [false, null]);
	check("and the server has it as seen", server.rows.project, { desktop: true, phone: false });
	first.pp.tour_route();
	first.flush();
	check("a new landing in the same visit: not again", [first.kit.tour.is_open(), server.marks.length], [false, 1]);

	// A second visit, a new page object reading the server's flags.
	const second = ppPage({ server });
	second.pp.tour_take(server.rows.project);
	second.flush();
	await settle();
	check("second visit: nothing on screen, nothing marked again", [second.kit.tour.is_open(), server.marks.length], [false, 1]);

	// Esc and a press outside leave it seen too (it is marked the moment it shows).
	["esc", "outside"].forEach((how) => {
		const s = newServer();
		const p = ppPage({ server: s });
		p.pp.tour_take(s.rows.project);
		p.flush();
		if (how === "esc") key(p.doc.activeElement, "Escape");
		else {
			fire(p.doc.querySelector(".pk-tour"), "pointerdown");
			fire(p.doc.querySelector(".pk-tour"), "click");
		}
		check(`${how}: closed`, p.kit.tour.is_open(), false);
		p.pp.tour_route();
		p.flush();
		check(`${how}: seen, so it does not come back`, [p.kit.tour.is_open(), s.rows.project.desktop], [false, true]);
	});
});

test("PP: Take the tour from ? opens it again and marks nothing", async () => {
	const server = newServer();
	server.rows.project.desktop = true;
	const env = ppPage({ server });
	const { pp, kit } = env;
	pp.tour_take(server.rows.project);
	env.flush();
	check("already seen: not on screen", kit.tour.is_open(), false);
	const actions = pp.tour_legend_actions();
	check("the help's footer has the button", actions.map((a) => a.label), ["Take the tour"]);
	check("it has a title for a hover", !!actions[0].title, true);
	pp.p6c_legend_sections = () => [];
	pp.p6d_legend_sections = () => [];
	pp.p6b_legend_sections = () => [];
	pp.p6a_tip_section = () => ({ title: "Tips", items: [] });
	pp.p6a_legend();
	check("the page's help is opened with that action", [kit.legended.opts.actions.map((a) => a.label), kit.drawer.current() !== null], [["Take the tour"], true]);
	kit.legended.opts.actions[0].on_click();
	check("the help drawer closed first, then the tour opened", [kit.log, kit.drawer.current(), kit.tour.is_open()], [["drawer.navigate"], null, true]);
	check("it is the same tour", shown(kit), ["Tabs along the top", "Drag to move", "Draft until you publish", "Notify crew?", "Help is one click away"]);
	await settle();
	check("a replay marks nothing", [server.marks, kit.quiet_calls], [[], []]);
	key(env.doc.activeElement, "Escape");
	check("and focus goes back to the ? button", env.doc.activeElement === pp.$p6a_help[0], true);
	check("replayed twice is fine", (pp.tour_replay(), pp.tour_replay(), kit.tour.is_open()), true);
});

test("PP: the Overview step is for the Executive Team only", () => {
	const plain = ppPage({ roles: ["Projects Manager", "Projects User"] });
	plain.pp.tour_take({ desktop: false, phone: false });
	plain.flush();
	check("a project manager's tour has no Overview step", shown(plain.kit).includes("Overview is for managers"), false);
	const exec = ppPage({ roles: ["Executive Team"] });
	exec.pp.tour_take({ desktop: false, phone: false });
	exec.flush();
	check("the Executive Team's has it, second", shown(exec.kit).slice(0, 3), ["Tabs along the top", "Overview is for managers", "Drag to move"]);
	const broken = ppPage({ roles: ["Executive Team"] });
	broken.frappe.user.has_role = () => {
		throw new Error("no roles yet");
	};
	broken.pp.tour_take({ desktop: false, phone: false });
	broken.flush();
	check("a boot with no roles is no Overview step, and no failure", [broken.kit.tour.is_open(), shown(broken.kit).includes("Overview is for managers")], [true, false]);
	const reader = ppPage({ roles: [], can_edit: false });
	reader.pp.tour_take({ desktop: false, phone: false });
	reader.flush();
	check("someone who cannot edit gets the short tour", shown(reader.kit), ["Tabs along the top", "Click for the details", "Help is one click away"]);
});

test("PP: a phone's tour is Today's, with its own flag; a desktop's is the tabs", async () => {
	const server = newServer();
	server.rows.project.desktop = true; // seen on a desktop
	const phone = ppPage({ server, phone: true, view: "today", today: { resource: "RES-1", days: [] }, route: ["project-planner", "today", "2026-10-22"] });
	phone.pp.tour_take(server.rows.project);
	phone.flush();
	check("on a phone, Today shows the phone tour although the desktop's was seen", shown(phone.kit), ["This is your day", "Open the route", "Block my time", "The rest of the planner", "Help is one tap away"]);
	await settle();
	check("and marks the phone flag only", [server.marks, server.rows.project], [[{ planner: "project", kind: "phone" }], { desktop: true, phone: true }]);

	const lookahead = ppPage({ phone: true, view: "lookahead" });
	lookahead.pp.tour_take({ desktop: false, phone: false });
	lookahead.flush();
	check("a phone on the Lookahead list is not given Today's tour unasked", lookahead.kit.tour.is_open(), false);
	const unlisted = ppPage({ phone: true, view: "today", today: { resource: "" }, route: ["project-planner", "today", "2026-10-22"] });
	unlisted.pp.tour_take({ desktop: false, phone: false });
	unlisted.flush();
	check("a person with no day of their own has nothing on Today to explain", unlisted.kit.tour.is_open(), false);
	const waiting = ppPage({ phone: true, view: "today", today: null, route: ["project-planner", "today", "2026-10-22"] });
	waiting.pp.tour_take({ desktop: false, phone: false });
	waiting.timers.list.forEach((t) => !t.dead && !t.done && ((t.done = true), t.fn()));
	check("Today's answer not in yet: it waits", [waiting.kit.tour.is_open(), waiting.timers.list.filter((t) => !t.dead && !t.done).length], [false, 1]);
	waiting.pp.today_data = { resource: "RES-1" };
	waiting.flush();
	check("and shows once it is", waiting.kit.tour.is_open(), true);

	const desktopToday = ppPage({ phone: false, view: "today", today: { resource: "RES-1" }, route: ["project-planner", "today", "2026-10-22"] });
	desktopToday.pp.tour_take({ desktop: false, phone: false });
	desktopToday.flush();
	check("a desktop that opens Today is not given the tabs tour there", desktopToday.kit.tour.is_open(), false);

	["route", "my-week"].forEach((view) => {
		const p = ppPage({ view });
		p.pp.tour_take({ desktop: false, phone: false });
		p.flush();
		check(`the ${view} screen has no toolbar, so no tour opens on it`, p.kit.tour.is_open(), false);
	});
	["lookahead", "week", "month", "crew", "capacity", "gantt", "overview"].forEach((view) => {
		const p = ppPage({ view });
		p.pp.tour_take({ desktop: false, phone: false });
		p.flush();
		check(`${view}: a desktop's tour opens`, p.kit.tour.is_open(), true);
	});
});

test("PP: nothing opens before the page can show it, and nothing throws while it waits", () => {
	const flags = { desktop: false, phone: false };
	// No answer from the saved views.
	let env = ppPage();
	env.pp.tour_route();
	env.flush();
	check("no flags yet: waiting, and then it gives up", [env.kit.tour.is_open(), env.timers.ran >= 60], [false, true]);
	env.pp.tour_take(undefined);
	env.pp.tour_take(null);
	env.pp.tour_take("junk");
	env.pp.tour_take(7);
	env.flush();
	check("a reply with no flags in it is not a yes", env.kit.tour.is_open(), false);

	// No kit / no toolbar / no data, each in turn, then everything arrives.
	env = ppPage({ no_toolbar: true });
	env.pp.tour_take(flags);
	env.timers.list.forEach((t) => !t.dead && !t.done && ((t.done = true), t.fn()));
	check("the toolbar is not built: it waits", [env.kit.tour.is_open(), env.pp.tour_verdict()], [false, "wait"]);
	env.pp.tb.kit = env.kit;
	check("built: it can go", env.pp.tour_verdict(), "ok");

	env = ppPage({ no_data: true });
	env.pp.tour_take(flags);
	check("no data: waiting", env.pp.tour_verdict(), "wait");
	env.pp.data = { can_edit: true };
	check("data: it can go", env.pp.tour_verdict(), "ok");
	env.pp.$body = { hasClass: (c) => c === "pp-loading" };
	check("a load in progress: waiting", env.pp.tour_verdict(), "wait");

	// Capacity draws its own heatmap and has no planner answer on a first landing: that is its data.
	env = ppPage({ no_data: true, view: "capacity" });
	env.pp.tour_take(flags);
	check("Capacity with neither the heatmap nor the planner's answer: waiting", env.pp.tour_verdict(), "wait");
	env.pp.heatmap = { weeks: [], rows: [] };
	check("its heatmap is its data", env.pp.tour_verdict(), "ok");
	env.pp.$body = { hasClass: (c) => c === "pp-loading" };
	check("still loading: waiting", env.pp.tour_verdict(), "wait");

	env = ppPage({ no_kit: true });
	env.pp.tour_take(flags);
	check("the kit has not loaded: waiting", env.pp.tour_verdict(), "wait");

	// An older kit on the Desk has no tour: nothing to wait for, nothing to throw about.
	env = ppPage();
	delete env.kit.tour;
	env.pp.tour_take(flags);
	env.flush();
	check("an older kit: no tour, no wait, no throw", [env.pp.tour_verdict(), env.pp.tour_legend_actions(), env.pp.tour_start(false), env.pp.tour_open()], ["", [], false, false]);
	env.pp.tour_replay();
	env.pp.tour_close("route");

	// A bare instance, as a half-built page, calls everything without a throw.
	const bare = Object.create(vm.runInContext("ProjectPlanner", env.ctx).prototype);
	const calls = [
		() => bare.tour_take(flags),
		() => bare.tour_route(),
		() => bare.tour_nudge(),
		() => bare.tour_check(0),
		() => bare.tour_close("x"),
		() => bare.tour_replay(),
		() => bare.tour_start(false),
		() => bare.tour_legend_actions(),
		() => bare.tour_open(),
		() => bare.tour_who(),
	];
	let threw = null;
	calls.forEach((fn, i) => {
		try {
			fn();
		} catch (e) {
			threw = `call ${i}: ${e.message}`;
		}
	});
	check("a bare page: nothing throws", threw, null);

	// No timers at all (a page that cannot schedule): no throw, no tour.
	env = ppPage({ no_timers: true });
	let t2 = null;
	try {
		env.pp.tour_take(flags);
		env.pp.tour_route();
	} catch (e) {
		t2 = e.message;
	}
	check("no setTimeout: no throw, no tour", [t2, env.kit.tour.is_open()], [null, false]);

	// A verdict that throws (a page half torn down) is a skipped check, not an error.
	env = ppPage();
	env.pp.tour_take(flags);
	env.pp.p6b_page_live = () => {
		throw new Error("torn down");
	};
	let t3 = null;
	try {
		env.flush();
	} catch (e) {
		t3 = e.message;
	}
	check("a failing check is swallowed", [t3, env.kit.tour.is_open()], [null, false]);

	// Another page, or a hidden one, is not the planner's to show on.
	env = ppPage({ route: ["Form", "Project", "PRJ-1"] });
	env.pp.tour_take(flags);
	env.flush();
	check("the route is another page's: no tour", env.kit.tour.is_open(), false);
	env = ppPage();
	env.pp.page.wrapper = { is: () => false, 0: {} };
	env.pp.tour_take(flags);
	env.flush();
	check("the page is hidden: no tour", env.kit.tour.is_open(), false);
});

test("PP: it waits while anything else needs the person, and gives up after a minute", () => {
	const flags = { desktop: false, phone: false };
	const blockers = {
		"a drawer": (e) => (e.kit.drawer._current = { open: true }),
		"a menu or the sheet": (e) => (e.kit.guard._top = { kind: "menu" }),
		"a Frappe dialog": (e) => (e.window.cur_dialog = { display: true }),
		"a Bootstrap modal": (e) => e.doc.body.appendChild(Object.assign(e.doc.createElement("div"), { className: "modal show" })),
		"a drag": (e) => (e.pp.drag = { active: true }),
	};
	Object.entries(blockers).forEach(([name, block]) => {
		const env = ppPage();
		block(env);
		env.pp.tour_take(flags);
		env.timers.list.forEach((t) => !t.dead && !t.done && ((t.done = true), t.fn()));
		check(`${name}: it waits`, [env.kit.tour.is_open(), env.pp.tour_verdict()], [false, "wait"]);
		check(`${name}: and asks again a second later`, env.timers.list.filter((t) => !t.dead && !t.done).map((t) => t.ms), [1000]);
	});
	// The way clears: it opens.
	const env = ppPage();
	env.kit.drawer._current = { open: true };
	env.pp.tour_take(flags);
	env.timers.list.forEach((t) => !t.dead && !t.done && ((t.done = true), t.fn()));
	env.kit.drawer._current = null;
	env.flush();
	check("a drawer closes and the tour follows", env.kit.tour.is_open(), true);
	// Never clears: 1 + 60 checks, then silence.
	const stuck = ppPage();
	stuck.kit.drawer._current = { open: true };
	stuck.pp.tour_take(flags);
	stuck.flush();
	check("a minute of asking, then it stops", [stuck.timers.ran, stuck.kit.tour.is_open()], [61, false]);
});

test("PP: a route change or the page hiding closes it; the planner's shortcuts stand down while it is open", () => {
	const env = ppPage();
	const { pp, kit } = env;
	pp.tour_take({ desktop: false, phone: false });
	env.flush();
	check("open", kit.tour.is_open(), true);
	const closed = [];
	kit.tour.current().opts.on_close = (how) => closed.push(how);
	const go = [];
	pp.go = (...a) => go.push(a);
	pp.p6b_run_key = (action) => (go.push(["key", action]), true);
	const press = (k) =>
		pp.p6b_key({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, defaultPrevented: false, target: { closest: () => null }, preventDefault() {}, stopPropagation() {} });
	press("t");
	press("1");
	press("?");
	press("ArrowRight");
	check("T, 1, ? and the arrows do nothing to the page behind it", go, []);
	pp.tour_route();
	check("a route of the planner's own closes it, as a route (focus is not pulled back)", [closed, kit.tour.is_open()], [["route"], false]);
	press("t");
	check("closed, the shortcuts work again", go, [["key", "today"]]);

	// The page hiding.
	const hidden = ppPage();
	let on_hide = null;
	const wrapper = { 0: {}, on: (event, fn) => (event === "hide" ? (on_hide = fn) : null), is: () => true };
	hidden.pp.page.wrapper = wrapper;
	hidden.pp.init_tour();
	hidden.pp.tour_take({ desktop: false, phone: false });
	hidden.flush();
	check("open again", hidden.kit.tour.is_open(), true);
	on_hide({ target: {} });
	check("another element hiding inside the page does not close it", hidden.kit.tour.is_open(), true);
	on_hide({ target: wrapper[0] });
	check("the page hiding does", hidden.kit.tour.is_open(), false);
});

test("PP: no history entry, no route, no overlay in the guard, from first landing to last close", async () => {
	const server = newServer();
	const env = ppPage({ server });
	const { pp, kit } = env;
	pp.tour_take(server.rows.project);
	env.flush();
	const handle = kit.tour.current();
	handle.next();
	handle.next();
	handle.back();
	key(env.doc.activeElement, "ArrowRight");
	pp.tour_legend_actions()[0].on_click();
	handle.close("skip");
	pp.tour_replay();
	key(env.doc.activeElement, "Escape");
	await settle();
	check("the browser's history was not touched", env.hist.log, []);
	check("the router was not asked for anything", env.sets, []);
	check("the kit's history guard was never joined", kit.log.filter((entry) => entry.startsWith("guard")), []);
	check("the tour is the only thing the kit's overlay stack never held", kit.guard.top(), null);
});

test("PP: a request that fails tells this browser instead, so the tour does not return at every visit", async () => {
	const server = newServer();
	const env = ppPage({ server });
	env.kit.fail = true;
	env.pp.tour_take(server.rows.project);
	env.flush();
	await settle();
	check("shown, the request failed", [env.kit.tour.is_open(), server.marks], [true, []]);
	check("this browser is told, per person and device kind", [...env.store.entries()], [["ee_project_planner_tour:nik@example.com:desktop", "1"]]);
	// Next visit: the server still says unseen (the request failed), the browser says seen.
	const again = ppPage({ server });
	again.store.set("ee_project_planner_tour:nik@example.com:desktop", "1");
	again.pp.tour_take({ desktop: false, phone: false });
	again.flush();
	check("the next visit does not show it", again.kit.tour.is_open(), false);
	// Another person on the same browser is not that person.
	const other = ppPage({ server, user: "clegg@example.com" });
	other.store.set("ee_project_planner_tour:nik@example.com:desktop", "1");
	other.pp.tour_take({ desktop: false, phone: false });
	other.flush();
	check("another person's visit still shows it", other.kit.tour.is_open(), true);
	// Storage that throws is the same as storage that is empty.
	const blocked = ppPage({ server: newServer(), storage_blocked: true });
	blocked.kit.fail = true;
	blocked.pp.tour_take({ desktop: false, phone: false });
	blocked.flush();
	await settle();
	check("blocked storage: it shows, and failing to remember is no failure", blocked.kit.tour.is_open(), true);
	// No quiet call on the kit: frappe's own, silent.
	const plain = ppPage();
	delete plain.kit.quiet_call;
	const frappeCalls = [];
	plain.frappe.call = (opts) => (frappeCalls.push(opts), Promise.resolve({}));
	plain.pp.tour_take({ desktop: false, phone: false });
	plain.flush();
	await settle();
	check("without the kit's quiet call it falls back to frappe.call, silent", frappeCalls.map((c) => [c.method.split(".").pop(), c.args, c.silent]), [["mark_tour_seen", { planner: "project", kind: "desktop" }, true]]);
	check("the method is the prefs module's, and a POST-only one", frappeCalls[0].method, "erpnext_enhancements.api.planner_views_prefs.mark_tour_seen");
});

test("PP: ? for the new tabs, in the help; Today gets a ? of its own", () => {
	const env = ppPage();
	const sections = env.pp.p12_legend_sections();
	check("the sections, in order", sections.map((s) => s.title), ["The tabs", "Where the planner opens", "Moving a task", "Draft and Published", "Baseline"]);
	const tabs = sections[0].items.map((item) => item.chip.text);
	check("a line for every tab, and Today", tabs, ["Lookahead", "Gantt", "Crew", "Calendar", "Overview", "Today"]);
	sections.forEach((section) =>
		section.items.forEach((item) => {
			check(`"${section.title}": an item has words`, !!item.text, true);
			check(`"${section.title}": and a sample that is a chip or nothing (never markup of a record's)`, !item.sample_html || item.sample_html === "", true);
		})
	);
	const all = JSON.stringify(sections);
	["Publish…", "Notify crew?", "Don't notify", "Reset baseline", "Hold all my changes", "Executive Team", "working days", "Ctrl (⌘ on a Mac)"].forEach((word) => check(`the help says "${word}"`, all.includes(word), true));
	check("the landing rule: the last tab per device; a phone Today; a desktop the Lookahead; the Executive Team the Overview", /last on this device/.test(all) && /phone opens Today/.test(all) && /Executive Team's opens the Overview/.test(all), true);
	// The kit's renderer turns them to markup with every word escaped.
	const legend_src = fs
		.readFileSync(path.join(KIT, "legend.js"), "utf8")
		.replace(/^import .*?;\s*$/gm, "")
		.replace(/^export (const|function|class) /gm, "$1 ");
	const lctx = { escape: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"), safe_color: (v, f) => f, console };
	vm.createContext(lctx);
	vm.runInContext(`${legend_src}\nthis.legend_html = legend_html;`, lctx);
	const html = lctx.legend_html(sections);
	check("the legend draws them", [html.includes("pk-chip pk-chip-plain\">Lookahead"), html.includes("pk-chip pk-chip-amber")], [true, true]);

	// Today's own ?.
	const head = { children: [] };
	const made = [];
	env.ctx.$ = (markup) => {
		const el = { markup, handlers: {}, attrs: {}, text(t) { this.t = t; return this; }, attr(k, v) { this.attrs[k] = v; return this; }, on(e, fn) { this.handlers[e] = fn; return this; }, appendTo(parent) { parent.children.push(this); made.push(this); return this; } };
		return el;
	};
	let opened = 0;
	env.pp.p6a_legend = () => (opened += 1);
	env.pp.tour_today_help(head);
	check("a ? button beside Planner, named", [made.length, made[0].t, made[0].attrs["aria-label"]], [1, "?", "How to read the planner"]);
	made[0].handlers.click();
	check("it opens the help", opened, 1);
});

// ------------------------------------------------------------------ 4. the Maintenance Planner

test("MP: the first visit shows the tour, marks it, and the second does not", async () => {
	const server = newServer();
	const env = mpPage({ server });
	const { mp, kit } = env;
	mp.tour_take(server.rows.maintenance);
	env.flush();
	check("a scheduler's desktop tour", shown(kit), ["Month, Week and Crew", "Drag to reschedule", "Project work is a thin strip", "Resources available", "Who hears about a change", "Help is one click away"]);
	await settle();
	check("marked for this planner", server.marks, [{ planner: "maintenance", kind: "desktop" }]);
	check("the browser holds nothing", [...env.store.keys()], []);
	env.doc.querySelector(".pk-tour-skip").click();
	check("Skip closes it", kit.tour.is_open(), false);
	const again = mpPage({ server });
	again.mp.tour_take(server.rows.maintenance);
	again.flush();
	await settle();
	check("the second visit: not shown, not marked", [again.kit.tour.is_open(), server.marks.length], [false, 1]);
	// The Project Planner's flag is its own.
	check("and the other planner's flags were not touched", server.rows.project, { desktop: false, phone: false });
	// Take the tour.
	again.mp.p6c_legend_sections = () => [];
	again.mp.p6d_legend_sections = () => [];
	again.mp.p6b_legend_sections = () => [];
	again.mp.p6a_tip_section = () => ({ title: "Tips", items: [] });
	again.mp.p6a_legend();
	check("the help carries Take the tour", again.kit.legended.opts.actions.map((a) => a.label), ["Take the tour"]);
	check("and the sections are P12's, after the tips", again.kit.legended.sections.map((s) => s.title).slice(0, 4), ["Tips", "The views", "Moving a visit", "Draft mode and Notify crew?"]);
	again.kit.legended.opts.actions[0].on_click();
	check("it shows the tour again", [again.kit.tour.is_open(), again.server.marks.length], [true, 1]);
});

test("MP: a phone's tour and a technician's; the strays", async () => {
	const server = newServer();
	const phone = mpPage({ server, phone: true, has_day: true, can_schedule: false });
	phone.mp.tour_take(server.rows.maintenance);
	phone.flush();
	check("a phone gets the phone tour, with My day for a technician who has one", shown(phone.kit), ["Your visits", "Project work is a strip", "My day", "Filters and tools", "Help is one tap away"]);
	await settle();
	check("its own flag", server.rows.maintenance, { desktop: false, phone: true });

	const tech = mpPage({ has_day: true, can_schedule: false });
	tech.mp.tour_take({ desktop: false, phone: false });
	tech.flush();
	check("a technician at a desktop", shown(tech.kit), ["Month, Week and Crew", "Project work is a thin strip", "Resources available", "My day", "Help is one click away"]);

	["month", "week", "crew"].forEach((view) => {
		const p = mpPage({ view });
		p.mp.tour_take({ desktop: false, phone: false });
		p.flush();
		check(`${view}: the tour opens`, p.kit.tour.is_open(), true);
	});
	const none = mpPage({ view: "route" });
	none.mp.tour_take({ desktop: false, phone: false });
	none.flush();
	check("a screen that is not one of the three views: not unasked", none.kit.tour.is_open(), false);
});

test("MP: not before the page can show it; its shortcuts stand down; a route change closes it; no history", async () => {
	const flags = { desktop: false, phone: false };
	let env = mpPage({ no_data: true });
	env.mp.tour_take(flags);
	check("no data: waiting", env.mp.tour_verdict(), "wait");
	env.mp.data = { can_schedule: true };
	check("data: ready", env.mp.tour_verdict(), "ok");
	env = mpPage({ no_toolbar: true });
	env.mp.tour_take(flags);
	check("no toolbar: waiting", env.mp.tour_verdict(), "wait");
	env = mpPage({ no_kit: true });
	env.mp.tour_take(flags);
	check("no kit: waiting", env.mp.tour_verdict(), "wait");
	env = mpPage();
	delete env.kit.tour;
	env.mp.tour_take(flags);
	env.flush();
	check("an older kit: nothing to show, nothing thrown", [env.mp.tour_verdict(), env.mp.tour_legend_actions(), env.mp.tour_start(false)], ["", [], false]);
	const bare = Object.create(vm.runInContext("MaintenancePlanner", env.ctx).prototype);
	let threw = null;
	[() => bare.tour_take(flags), () => bare.tour_route(), () => bare.tour_check(0), () => bare.tour_close(), () => bare.tour_replay(), () => bare.tour_start(false), () => bare.tour_open(), () => bare.tour_legend_actions()].forEach((fn, i) => {
		try {
			fn();
		} catch (e) {
			threw = `call ${i}: ${e.message}`;
		}
	});
	check("a bare page: nothing throws", threw, null);

	// Busy.
	env = mpPage();
	env.kit.drawer._current = { open: true };
	env.mp.tour_take(flags);
	env.flush();
	check("a drawer open: a minute of waiting, then nothing", [env.timers.ran, env.kit.tour.is_open()], [61, false]);

	// Open: shortcuts, route, history.
	const open = mpPage();
	open.mp.tour_take(flags);
	open.flush();
	check("open", open.kit.tour.is_open(), true);
	const went = [];
	open.mp.p6b_run_key = (action) => (went.push(action), true);
	const press = (k) =>
		open.mp.p6b_key({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, defaultPrevented: false, target: { closest: () => null }, preventDefault() {}, stopPropagation() {} });
	press("t");
	press("1");
	check("the planner's keys do nothing behind the tour", went, []);
	const handle = open.kit.tour.current();
	handle.next();
	handle.back();
	open.mp.tour_route();
	check("a route change closes it", open.kit.tour.is_open(), false);
	press("t");
	check("then the keys work again", went, ["today"]);
	open.mp.tour_replay();
	key(open.doc.activeElement, "Escape");
	await settle();
	check("no history entry, no route, no guard overlay", [open.hist.log, open.sets, open.kit.log.filter((e) => e.startsWith("guard"))], [[], [], []]);
});

test("MP: a failing request tells this browser; the help's sections", async () => {
	const server = newServer();
	const env = mpPage({ server });
	env.kit.fail = true;
	env.mp.tour_take(server.rows.maintenance);
	env.flush();
	await settle();
	check("shown; the request failed; the browser is told", [env.kit.tour.is_open(), [...env.store.entries()]], [true, [["ee_maintenance_planner_tour:nik@example.com:desktop", "1"]]]);
	const again = mpPage({ server });
	again.store.set("ee_maintenance_planner_tour:nik@example.com:desktop", "1");
	again.mp.tour_take({ desktop: false, phone: false });
	again.flush();
	check("next visit: not shown", again.kit.tour.is_open(), false);

	const sections = again.mp.p12_legend_sections();
	check("the views, moving a visit, Draft mode and Notify crew?", sections.map((s) => s.title), ["The views", "Moving a visit", "Draft mode and Notify crew?"]);
	check("a line for each view", sections[0].items.map((item) => item.chip.text), ["Month", "Week", "Crew"]);
	const all = JSON.stringify(sections);
	["Notify crew?", "Don't notify", "Draft mode", "Publish", "14 days", "Esc puts the change back"].forEach((word) => check(`the help says "${word}"`, all.includes(word), true));
	check("the kinds the server keeps are the page's", vm.runInContext("MPTOUR.kinds", again.ctx), ["desktop", "phone"]);
	check("MP10's legend sections are still there to be extended", typeof again.mp.p10_legend_sections, "function");
});

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (e) {
			failures += 1;
			console.log(`  FAIL ${name}: threw ${e && e.stack ? e.stack : e}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	console.log(`\n${failures} of ${checks} checks failed`);
	process.exit(failures ? 1 : 0);
})();
