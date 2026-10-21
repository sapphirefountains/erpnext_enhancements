#!/usr/bin/env node
/**
 * The planner toolbar redesign (v1.687.0), run under node against the REAL code:
 *
 *   1. planner_kit/toolbar.js on a small fake DOM: the menu button (open, arrow keys, Home/End, Esc
 *      gives focus back, Tab and a click elsewhere close it, an item closes the menu before it runs,
 *      toggles say their state with aria-checked and a check mark, and no history entry is pushed),
 *      and the phone's sheet over planner_kit/history.js and a fake browser (one history entry, Back
 *      closes it without the router routing, Esc closes it and steps off its entry, an item closes
 *      it first, a switch keeps it open and redraws it).
 *   2. Both planner pages' toolbar blocks (PPTB, MPTB) on an instance of each page's own class, with
 *      a recording stand-in for jQuery over the same fake DOM: every control the old toolbar had is
 *      still reachable (an explicit list, each item run and the method it calls checked), toggles
 *      say their state, the alert chips are there only above zero and open what their buttons did,
 *      the draft strip shows only in Draft mode and its Publish and Discard call the existing
 *      publish and discard, the phone's sheet takes the filters in and gives them back, and the
 *      Project Planner's Utilization view is a route.
 *
 * Run: node scripts/test_planner_toolbar.js
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
	get children() {
		return this.childNodes;
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
	insertBefore(node, ref) {
		if (node.parentNode) node.parentNode.removeChild(node);
		node.parentNode = this;
		const i = this.childNodes.indexOf(ref);
		if (i < 0) this.childNodes.push(node);
		else this.childNodes.splice(i, 0, node);
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
	get isConnected() {
		return this.ownerDocument.body.contains(this);
	}
	closest(selector) {
		for (let node = this; node && node.nodeType === 1; node = node.parentNode) if (matches(node, selector)) return node;
		return null;
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
	setAttribute(name, value) {
		this.attrs[name] = String(value);
	}
	getAttribute(name) {
		return name in this.attrs ? this.attrs[name] : null;
	}
	removeAttribute(name) {
		delete this.attrs[name];
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
	getBoundingClientRect() {
		return { left: 100, top: 10, bottom: 40, width: 120, height: 30 };
	}
	// The class string and the attributes a selector here may name.
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
			const attr = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(one);
			if (attr) return attr[2] === undefined ? attr[1] in node.attrs : node.attrs[attr[1]] === attr[2];
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
	doc.getElementById = () => null;
	doc.activeElement = doc.body;
	return doc;
}

// An event at `target`: the document's capture listeners, then the target and its ancestors (bubble),
// then the document's bubble listeners. Returns the event (defaultPrevented, stopped).
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
	for (let node = target; node && node.nodeType === 1 && !ev.stopped; node = node.parentNode) {
		node.listeners.filter((l) => l.type === type).forEach((l) => l.fn(ev));
	}
	if (!ev.stopped) doc.listeners.filter((l) => l.type === type && !l.capture).forEach((l) => l.fn(ev));
	return ev;
}

const key = (target, k, extra) => fire(target, "keydown", Object.assign({ key: k }, extra || {}));

// ------------------------------------------------------------------ the kit's modules as one script

function kitSource() {
	return ["history.js", "toolbar.js"]
		.map((name) =>
			fs
				.readFileSync(path.join(KIT, name), "utf8")
				.replace(/^import .*?;\s*$/gm, "")
				.replace(/^export (const|function|class) /gm, "$1 ")
		)
		.join("\n");
}

function loadKit() {
	const ctx = { console, Date, Math, Array, Object, String, Number, JSON };
	vm.createContext(ctx);
	vm.runInContext(`${kitSource()}\nthis.kit = { create_guard, create_toolbar, item_role, tidy };`, ctx);
	return ctx.kit;
}

// A browser's history and window, as test_planner_phase6a's model has it: frappe's popstate listener
// was added first, and calls the router, which the kit's gate may stop.
function browser() {
	const listeners = [];
	const timers = [];
	const tasks = [];
	const entries = [{ state: null, url: "/desk/project-planner/week/2026-10-12" }];
	let index = 0;
	const log = [];
	const win = {
		innerWidth: 375,
		innerHeight: 800,
		event: undefined,
		Event: function (type) {
			this.type = type;
		},
		addEventListener(type, fn, capture) {
			listeners.push({ type, fn, capture: !!capture });
		},
		removeEventListener(type, fn, capture) {
			const i = listeners.findIndex((l) => l.type === type && l.fn === fn && l.capture === !!capture);
			if (i >= 0) listeners.splice(i, 1);
		},
		dispatchEvent(event) {
			fireWin(event);
		},
		history: {
			get state() {
				return entries[index].state;
			},
			get length() {
				return entries.length;
			},
			pushState(state, title, url) {
				log.push(["push", state]);
				entries.splice(index + 1);
				entries.push({ state, url: url || entries[index].url });
				index += 1;
			},
			replaceState(state, title, url) {
				log.push(["replace", state]);
				entries[index] = { state, url: url || entries[index].url };
			},
			back() {
				log.push(["back"]);
				tasks.push(() => traverse(-1));
			},
		},
	};
	function fireWin(event) {
		for (const l of listeners.filter((x) => x.type === event.type)) {
			win.event = event;
			try {
				l.fn(event);
			} finally {
				win.event = undefined;
			}
		}
	}
	function traverse(step) {
		const next = index + step;
		if (next < 0 || next >= entries.length) return;
		index = next;
		fireWin({ type: "popstate", state: entries[index].state });
	}
	const router = { route: "project-planner/week/2026-10-12", routed: 0, replace: false, change: [] };
	let route_impl = function () {
		router.routed += 1;
	};
	win.addEventListener("popstate", () => route_impl());
	const env = {
		win,
		set_timeout: (fn, ms) => {
			timers.push({ fn, ms: Number(ms) || 0 });
			return timers.length;
		},
		clear_timeout: (id) => {
			if (timers[id - 1]) timers[id - 1].dead = true;
		},
		route: () => router.route,
		page: () => router.route.split("/")[0],
		set_replace: (on) => {
			router.replace = !!on;
		},
		on_route_change: (fn) => router.change.push(fn),
		hide_open_dialog: () => {},
		gate_router: (decide) => {
			const original = route_impl;
			route_impl = function () {
				const ev = win.event;
				if (ev && ev.type === "popstate" && !ev.__planner_kit_seen && decide(ev) === true) return;
				return original.apply(this, arguments);
			};
			return true;
		},
	};
	const run = () => {
		for (let i = 0; i < 20; i++) {
			const t = timers.filter((x) => x && !x.dead && !x.done && x.ms < 1000);
			const q = tasks.splice(0);
			if (!t.length && !q.length) break;
			t.forEach((x) => {
				x.done = true;
				x.fn();
			});
			q.forEach((fn) => fn());
		}
	};
	return { win, env, router, entries, log, run, at: () => index, back: () => traverse(-1), fire: fireWin };
}

function toolbarEnv() {
	const kit = loadKit();
	const doc = makeDoc();
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const toolbar = kit.create_toolbar({ doc, win: b.win, guard, t: (s) => s, warn: (e) => console.log("warn", e) });
	const bar = doc.createElement("div");
	doc.body.appendChild(bar);
	return { kit, doc, b, guard, toolbar, bar };
}

const pushes = (b) => b.log.filter((entry) => entry[0] === "push").length;

// ------------------------------------------------------------------ 1. the kit's menu button

test("A menu opens from its button, as a menu, pushing no history entry", () => {
	const { doc, b, toolbar, bar } = toolbarEnv();
	const button = doc.createElement("button");
	bar.appendChild(button);
	const ran = [];
	const handle = toolbar.menu(button, {
		label: "Edit",
		items: () => [
			{ heading: "Switches" },
			{ label: "Draft mode", checked: true, on_click: () => ran.push("draft") },
			{ label: "Tap to move", checked: false, on_click: () => ran.push("tap") },
			{ divider: true },
			{ label: "Color: Person", radio: true, checked: true, on_click: () => ran.push("radio") },
			{ label: "Copy week…", on_click: () => ran.push(["copy", handle.is_open()]) },
			{ label: "Undo", disabled: true, on_click: () => ran.push("undo") },
			{ label: "Plaza", info: true, sample_html: "<i></i>" },
		],
	});
	check("the button says it has a menu, closed", [button.getAttribute("aria-haspopup"), button.getAttribute("aria-expanded")], ["menu", "false"]);
	button.click();
	const menu = handle.element();
	check("open: aria-expanded", button.getAttribute("aria-expanded"), "true");
	check("the list is a menu named for its button", [menu.getAttribute("role"), menu.getAttribute("aria-label")], ["menu", "Edit"]);
	check("the button controls it", button.getAttribute("aria-controls"), menu.id);
	const items = handle.items();
	check(
		"each item's role and state",
		items.map((item) => [item.getAttribute("role"), item.getAttribute("aria-checked"), item.getAttribute("aria-disabled")]),
		[
			["menuitemcheckbox", "true", null],
			["menuitemcheckbox", "false", null],
			["menuitemradio", "true", null],
			["menuitem", null, null],
			["menuitem", null, "true"],
			["menuitem", null, "true"],
		]
	);
	check("a ticked switch shows a check mark, an unticked one none", [items[0].querySelector(".pk-tb-check").textContent, items[1].querySelector(".pk-tb-check").textContent], ["✓", ""]);
	check("the first item has focus", doc.activeElement === items[0], true);
	check("no history entry for a menu", [pushes(b), b.win.history.length], [0, 1]);

	key(items[0], "ArrowDown");
	check("ArrowDown moves on", doc.activeElement === items[1], true);
	items[0].focus();
	key(items[0], "ArrowUp");
	check("ArrowUp from the first wraps to the last", doc.activeElement === items[items.length - 1], true);
	key(doc.activeElement, "ArrowDown");
	check("ArrowDown from the last wraps to the first", doc.activeElement === items[0], true);
	key(doc.activeElement, "End");
	check("End", doc.activeElement === items[items.length - 1], true);
	key(doc.activeElement, "Home");
	check("Home", doc.activeElement === items[0], true);

	const esc = key(doc.activeElement, "Escape");
	check("Esc closes it", [handle.is_open(), button.getAttribute("aria-expanded"), !!menu.parentNode], [false, "false", false]);
	check("Esc is the menu's own: nothing behind it takes it", [esc.defaultPrevented, esc.stopped], [true, true]);
	check("and focus goes back to the button", doc.activeElement === button, true);

	button.click();
	handle.items()[3].click();
	check("an item closes the menu first, then runs", ran, [["copy", false]]);
	check("focus back on the button after an item", doc.activeElement === button, true);

	button.click();
	handle.items()[4].click();
	handle.items()[5].click();
	check("a disabled item and a key entry do nothing, and the menu stays", [ran.length, handle.is_open()], [1, true]);
	handle.items()[1].click();
	check("a switch runs", ran[1], "tap");
	check("still no history entry", pushes(b), 0);
});

test("A menu closes on a click elsewhere, on Tab, and when another opens", () => {
	const { doc, toolbar, bar } = toolbarEnv();
	const one = doc.createElement("button");
	const two = doc.createElement("button");
	const elsewhere = doc.createElement("div");
	[one, two, elsewhere].forEach((el) => bar.appendChild(el));
	const items = () => [{ label: "A", on_click: () => null }, { label: "B", on_click: () => null }];
	const first = toolbar.menu(one, { label: "One", items });
	const second = toolbar.menu(two, { label: "Two", items });
	one.click();
	fire(first.items()[1], "pointerdown");
	check("a press inside the menu keeps it open", first.is_open(), true);
	fire(elsewhere, "pointerdown");
	check("a press elsewhere closes it", first.is_open(), false);
	check("without taking focus anywhere", doc.activeElement === one, false);
	one.click();
	key(first.items()[0], "Tab");
	check("Tab closes it", first.is_open(), false);
	one.click();
	two.click();
	check("opening another closes the first", [first.is_open(), second.is_open()], [false, true]);
	two.click();
	check("its button closes it again", second.is_open(), false);
	key(two, "ArrowUp");
	check("ArrowUp on the button opens it at the last item", [second.is_open(), doc.activeElement === second.items()[1]], [true, true]);
	key(doc.activeElement, "Escape");
	key(two, "ArrowDown");
	check("ArrowDown on the button opens it at the first", doc.activeElement === second.items()[0], true);
});

test("A route change closes an open menu and takes its listeners with it; Tab leaves from the button", () => {
	const kit = loadKit();
	const doc = makeDoc();
	const b = browser();
	const changes = [];
	const toolbar = kit.create_toolbar({ doc, win: b.win, t: (s) => s, on_route_change: (fn) => changes.push(fn) });
	const bar = doc.createElement("div");
	doc.body.appendChild(bar);
	const button = doc.createElement("button");
	const after = doc.createElement("button");
	bar.appendChild(button);
	bar.appendChild(after);
	const handle = toolbar.menu(button, { label: "Edit", items: () => [{ label: "A", on_click: () => null }] });
	const pointer = () => doc.listeners.filter((l) => l.type === "pointerdown").length;
	button.click();
	check("open: it listens for a click elsewhere", pointer(), 1);
	changes.forEach((fn) => fn());
	check("a route change closes it", [handle.is_open(), !!handle.element(), button.getAttribute("aria-expanded")], [false, false, "false"]);
	check("and removes its document listener", pointer(), 0);
	button.click();
	toolbar.close_all("route");
	check("a page hiding (close_all) closes it too", [handle.is_open(), pointer()], [false, 0]);
	button.click();
	const tab = key(handle.items()[0], "Tab");
	check("Tab closes it with focus back on its button", [handle.is_open(), doc.activeElement === button], [false, true]);
	check("and lets the browser's Tab go on from there (to the control after it)", tab.defaultPrevented, false);
});

test("The sheet's focus trap skips what is hidden or disabled", () => {
	const { doc, toolbar } = toolbarEnv();
	const shown = doc.createElement("select");
	const hidden = doc.createElement("select");
	hidden.offsetParent = null; // display: none (the Project filter on the Utilization view)
	const off = doc.createElement("input");
	off.disabled = true;
	const sheet = toolbar.sheet({
		title: "x",
		top: (slot) => [shown, hidden, off].forEach((el) => slot.appendChild(el)),
		sections: () => [{ title: "Edit", items: [{ label: "Draft mode", checked: false, on_click: () => null }] }],
	});
	const last = sheet.body.querySelectorAll(".pk-sheet-item")[0];
	const close = sheet.el.querySelector(".pk-sheet-close");
	last.focus();
	const forward = key(last, "Tab");
	check("Tab on the last usable control wraps to the first", [forward.defaultPrevented, doc.activeElement === close], [true, true]);
	const back = key(close, "Tab", { shiftKey: true });
	check("Shift+Tab on the first wraps to the last usable one, not the hidden field", [back.defaultPrevented, doc.activeElement === last], [true, true]);
	shown.focus();
	const middle = key(shown, "Tab");
	check("in between, the browser moves focus itself", middle.defaultPrevented, false);
});

test("tidy drops headings and dividers that say nothing", () => {
	const { kit } = toolbarEnv();
	const out = kit.tidy([
		{ divider: true },
		{ heading: "Empty" },
		{ divider: true },
		{ label: "A" },
		{ divider: true },
		{ divider: true },
		{ heading: "Tail" },
	]);
	check("tidied", out, [{ label: "A" }]);
});

// ------------------------------------------------------------------ 1b. the phone's sheet

test("The sheet takes one history entry, and Back closes it without the router routing", () => {
	const { doc, b, guard, toolbar, bar } = toolbarEnv();
	const opener = doc.createElement("button");
	bar.appendChild(opener);
	opener.focus();
	const closed = [];
	const filters = doc.createElement("span");
	bar.appendChild(filters);
	const sheet = toolbar.sheet({
		title: "Filters and tools",
		max_width: 767,
		return_focus: opener,
		top: (slot) => slot.appendChild(filters),
		sections: () => [{ title: "Edit", items: [{ label: "Draft mode", checked: false, on_click: () => null }] }],
		on_close: (how) => {
			closed.push(how);
			bar.appendChild(filters);
		},
	});
	check("one history entry pushed", pushes(b), 1);
	check("a modal dialog with its title", [sheet.el.getAttribute("role"), sheet.el.getAttribute("aria-modal")], ["dialog", "true"]);
	check("the page's filters moved in", sheet.top.contains(filters), true);
	check("the guard holds it", guard.stack().length, 1);
	const routed = b.router.routed;
	b.back();
	check("Back closed it", [sheet.is_open(), closed], [false, ["back"]]);
	check("the router did not route (the planner is not reloaded)", b.router.routed, routed);
	check("its fields went home", bar.contains(filters), true);
	check("the sheet left the page", [!!sheet.el.parentNode, guard.stack().length], [false, 0]);
	check("focus back on its button", doc.activeElement === opener, true);
});

test("Esc closes the sheet and steps back off its entry; a switch keeps it open", () => {
	const { doc, b, guard, toolbar, bar } = toolbarEnv();
	const opener = doc.createElement("button");
	bar.appendChild(opener);
	let draft = false;
	const ran = [];
	const sheet = toolbar.sheet({
		title: "Filters and tools",
		return_focus: opener,
		sections: () => [
			{ title: "Edit", items: [{ label: "Draft mode", checked: draft, keep_open: true, on_click: () => (draft = !draft) }] },
			{ title: "Views", items: [{ label: "My week", on_click: () => ran.push(["my week", sheet.is_open()]) }] },
			{ title: "Empty", items: [] },
		],
	});
	const buttons = () => sheet.body.querySelectorAll(".pk-sheet-item");
	check("the empty section is left out", sheet.body.querySelectorAll(".pk-sheet-section").length, 2);
	check("a switch says its state", buttons()[0].getAttribute("aria-pressed"), "false");
	buttons()[0].click();
	check("a switch keeps the sheet open and redraws it", [sheet.is_open(), buttons()[0].getAttribute("aria-pressed")], [true, "true"]);
	guard.top().on_escape();
	check("Esc (through the kit's handler) closes it", sheet.is_open(), false);
	b.run();
	check("and steps back off its entry, once", [b.log.filter((e) => e[0] === "back").length, b.at()], [1, 0]);

	const again = toolbar.sheet({
		title: "Filters and tools",
		sections: () => [{ title: "Views", items: [{ label: "My week", on_click: () => ran.push(["my week", again.is_open()]) }] }],
	});
	again.body.querySelectorAll(".pk-sheet-item")[0].click();
	check("an item closes the sheet first, then runs", ran, [["my week", false]]);
	const third = toolbar.sheet({ title: "x", max_width: 767, sections: () => [] });
	b.win.innerWidth = 1024;
	b.fire({ type: "resize" });
	check("turned past the phone breakpoint, it closes", third.is_open(), false);
});

// ------------------------------------------------------------------ 2. the pages, on a fake jQuery

function makeJQuery(doc) {
	class J {
		constructor(nodes) {
			this.nodes = nodes.filter(Boolean);
			this.length = this.nodes.length;
			this.nodes.forEach((node, i) => (this[i] = node));
		}
		each(fn) {
			this.nodes.forEach((node, i) => fn(i, node));
			return this;
		}
		text(value) {
			if (value === undefined) return this.nodes.map((n) => n.textContent).join("");
			this.nodes.forEach((n) => (n.textContent = String(value)));
			return this;
		}
		html(value) {
			this.nodes.forEach((n) => (n.innerHTML = value));
			return this;
		}
		attr(name, value) {
			if (value === undefined) return this.nodes[0] ? this.nodes[0].getAttribute(name) : undefined;
			this.nodes.forEach((n) => n.setAttribute(name, value));
			return this;
		}
		addClass(name) {
			String(name || "")
				.split(/\s+/)
				.filter(Boolean)
				.forEach((c) => this.nodes.forEach((n) => n.classList.add(c)));
			return this;
		}
		removeClass(name) {
			this.nodes.forEach((n) => n.classList.remove(name));
			return this;
		}
		toggleClass(name, on) {
			this.nodes.forEach((n) => n.classList.toggle(name, on));
			return this;
		}
		hasClass(name) {
			return this.nodes.some((n) => n.classList.contains(name));
		}
		on(type, fn) {
			this.nodes.forEach((n) => n.addEventListener(type, fn));
			return this;
		}
		trigger(type) {
			this.nodes.forEach((n) => fire(n, type));
			return this;
		}
		appendTo(target) {
			const parent = $(target).nodes[0];
			this.nodes.forEach((n) => parent.appendChild(n));
			return this;
		}
		prependTo(target) {
			const parent = $(target).nodes[0];
			this.nodes.forEach((n) => parent.insertBefore(n, parent.firstChild));
			return this;
		}
		append(child) {
			const parent = this.nodes[0];
			if (typeof child === "string") {
				const text = doc.createElement("#text");
				text.ownText = child;
				parent.appendChild(text);
			} else $(child).nodes.forEach((n) => parent.appendChild(n));
			return this;
		}
		insertBefore(target) {
			const ref = $(target).nodes[0];
			this.nodes.forEach((n) => ref.parentNode.insertBefore(n, ref));
			return this;
		}
		insertAfter(target) {
			const ref = $(target).nodes[0];
			const parent = ref.parentNode;
			const next = parent.childNodes[parent.childNodes.indexOf(ref) + 1] || null;
			this.nodes.forEach((n) => (next ? parent.insertBefore(n, next) : parent.appendChild(n)));
			return this;
		}
		empty() {
			this.nodes.forEach((n) => (n.textContent = ""));
			return this;
		}
		show() {
			this.nodes.forEach((n) => (n.style.display = ""));
			return this;
		}
		hide() {
			this.nodes.forEach((n) => (n.style.display = "none"));
			return this;
		}
		toggle(on) {
			return on ? this.show() : this.hide();
		}
		find(selector) {
			return new J(this.nodes.flatMap((n) => n.querySelectorAll(selector)));
		}
		first() {
			return new J(this.nodes.slice(0, 1));
		}
		val(value) {
			if (value === undefined) return this.nodes[0] ? this.nodes[0].value || "" : "";
			this.nodes.forEach((n) => (n.value = value));
			return this;
		}
		prop() {
			return this;
		}
		css() {
			return this;
		}
	}
	function parse(html) {
		const m = /^<([a-z0-9]+)([^>]*)>([\s\S]*?)(?:<\/\1>)?$/i.exec(html.trim());
		const el = doc.createElement(m[1]);
		for (const [, name, value] of m[2].matchAll(/([\w-]+)="([^"]*)"/g)) {
			if (name === "class") el.className = value;
			else el.setAttribute(name, value);
		}
		if (m[3]) el.ownText = m[3];
		return el;
	}
	function $(arg) {
		if (arg instanceof J) return arg;
		if (typeof arg === "string") return new J([parse(arg)]);
		if (arg && arg.nodeType) return new J([arg]);
		return new J([]);
	}
	return $;
}

const translate = (text, args) => String(text).replace(/\{(\d+)\}/g, (all, i) => (args && args[i] !== undefined ? args[i] : all));

function loadPage(file) {
	const doc = makeDoc();
	const $ = makeJQuery(doc);
	const route = { now: [] };
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
		datetime: { get_today: () => "2026-10-14" },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		get_route: () => route.now,
		show_alert: () => null,
		route_options: null,
	};
	const ctx = {
		frappe,
		$,
		__: translate,
		window: { localStorage: null },
		document: doc,
		console,
		moment: (value) => ({ isValid: () => true, format: () => String(value) }),
		setTimeout: () => 0,
		clearTimeout: () => null,
	};
	vm.createContext(ctx);
	vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
	return { ctx, doc, $, frappe, route };
}

// Every method an item may call, replaced by a recorder, so a menu item is seen to reach it.
function record(page, names) {
	const calls = [];
	names.forEach((name) => {
		page[name] = (...args) => {
			calls.push([name].concat(args.map((a) => (a && typeof a === "object" && "name" in a ? a.name : a))));
		};
	});
	return calls;
}

function find(items, label) {
	return items.find((item) => item && item.label === label) || null;
}

function projectPlanner() {
	const env = loadPage(PP_JS);
	const ProjectPlanner = vm.runInContext("ProjectPlanner", env.ctx);
	const pp = Object.create(ProjectPlanner.prototype);
	const $ = env.$;
	const bar = $('<div class="pp-toolbar"></div>').appendTo(env.doc.body);
	Object.assign(pp, {
		view: "week",
		anchor: "2026-10-14",
		project: "",
		pm: "",
		group: "",
		show_foreign: true,
		draft_on: false,
		over_only: false,
		undo_stack: [],
		by_project: {},
		data: {
			can_edit: true,
			can_schedule: true,
			tasks: [
				{ name: "T1", over_plan: true, project: "" },
				{ name: "T2", over_plan: false, project: "" },
			],
			unscheduled: [],
		},
		overdue: { groups: [{ project: "", rows: [{ name: "T9" }, { name: "T8" }], count: 2 }], total: 2 },
		p6c: { color_by: "default", tap: false, loaded: true, highlight: null, views: [{ name: "Field crew", data: { view: "crew" } }] },
		p6d: { center: { count: () => 3 } },
		p6_selection: new Set(["T1", "T2"]),
		$toolbar: bar,
		$filters: $('<span class="pp-tb-filters"></span>').appendTo(bar),
		$alerts: $('<span class="pp-tb-alerts"></span>').appendTo(bar),
		$tools: $('<span class="pp-tb-tools"></span>').appendTo(bar),
		$undo: $('<button class="pp-undo"></button>').appendTo(bar),
		$trays: $("<div></div>"),
		$hint: $("<div></div>"),
	});
	pp.$draft_bar = $('<div class="pp-draft-bar"></div>').insertAfter(bar);
	pp.p6c_matching_view = () => pp.p6c.views[0];
	pp.p6c_view_summary = () => "Crew";
	pp.p6c_swatch_html = () => "<i></i>";
	pp.p6c_legend_entries = () => [];
	pp.init_toolbar();
	return Object.assign(env, { pp });
}

function maintenancePlanner() {
	const env = loadPage(MP_JS);
	const MaintenancePlanner = vm.runInContext("MaintenancePlanner", env.ctx);
	const mp = Object.create(MaintenancePlanner.prototype);
	const $ = env.$;
	const bar = $('<div class="mp-toolbar"></div>').appendTo(env.doc.body);
	Object.assign(mp, {
		view: "month",
		anchor: "2026-10-14",
		technician: "",
		show_projected: true,
		show_project_work: false,
		draft_on: false,
		undo_stack: [{ site: "Hotel" }],
		data: {
			can_create_visits: true,
			can_schedule: true,
			visits: [
				{ key: "v1", kind: "visit", overdue: true, date: "2026-10-01", site: "Hotel", technician: "a@x.com" },
				{ key: "v2", kind: "visit", overdue: false, date: "2026-10-20", site: "Plaza" },
			],
			unscheduled: [],
			projected: [],
		},
		p6c: { color_by: "default", tap: true, loaded: true, highlight: null, views: [] },
		p6d: { center: { count: () => 0 } },
		p6_selection: new Set(),
		by_key: {},
		$body: $('<div class="mp-wrap"></div>').appendTo(env.doc.body),
		$filters: $('<span class="mp-tb-filters"></span>').appendTo(bar),
		$alerts: $('<span class="mp-tb-alerts"></span>').appendTo(bar),
		$tools: $('<span class="mp-tb-tools"></span>').appendTo(bar),
	});
	mp.$body.nodes[0].appendChild(bar.nodes[0]);
	mp.$draft_bar = $('<div class="mp-draft-bar"></div>').insertAfter(bar);
	mp.booking_cards = () => [];
	mp.p6c_matching_view = () => null;
	mp.p6c_legend_entries = () => [];
	mp.tech_name = (user) => user;
	mp.init_toolbar();
	return Object.assign(env, { mp });
}

// The Project Planner's old toolbar, control by control, and where each is now. "bar" controls are
// pinned in the page source; the rest are menu items (run, and the method they reach checked) or
// alert chips.
const PP_OLD_TOOLBAR = [
	["‹ Earlier", "bar", "this.shift(-1)"],
	["Today", "bar", "this.go(this.view, frappe.datetime.get_today())"],
	["Later ›", "bar", "this.shift(1)"],
	["Month / Week / Crew / Heatmap", "bar", '[PP.heatmap_view, __("Heatmap")],'],
	["Utilization (was a button to the report)", "bar", '[PP.util_view, __("Utilization")],'],
	["Search", "bar", "$wrap.prependTo(this.$filters)"],
	["Project filter", "bar", 'this.make_select(this.$filters, __("Project")'],
	["Project manager filter", "bar", 'this.make_select(this.$filters, __("Project manager")'],
	["Group filter", "bar", 'this.make_select(this.$filters, __("Group")'],
	["At a glance", "bar", ".insertAfter(this.$project);"],
	["?", "bar", 'class="btn btn-default btn-sm pp-p6a-help">?</button>'],
	["↻", "bar", 'class="btn btn-default btn-sm pp-refresh">↻</button>'],
	["Undo (icon)", "bar", 'class="btn btn-default btn-sm pp-undo pp-empty">↶</button>'],
	["My week", "views", "My week", "go_my_week"],
	["Views: a saved view", "views", "Field crew", "p6c_use_view"],
	["Views: Save view as…", "views", "Save current view as…", "p6c_save_as"],
	["Views: Manage views…", "views", "Manage views…", "p6c_manage_views"],
	["Color by", "display", "Color: Person", "p6c_set_color"],
	["Maintenance, rentals, travel", "display", "Maintenance, rentals, travel", "set_show_foreign"],
	["Draft mode", "edit", "Draft mode", "set_draft_mode"],
	["Tap to move", "edit", "Tap to move", "p6c_set_tap"],
	["Block time", "edit", "Block time…", "p6d_block_form"],
	["Copy week…", "edit", "Copy week…", "open_copy_week"],
	["Conflicts (also a chip)", "edit", "Conflicts…", "p6d_open_conflicts"],
	["Undo", "edit", "Undo", "undo"],
	["Print: this view", "print", "Print this view", "p6c_print"],
	["Print: selection only", "print", "Print selection only (2)", "p6c_print"],
	["Print crew sheet", "print", "Print crew sheet", "print_crew_sheet"],
	["Conflicts (N)", "chip", "conflicts", "p6d_open_conflicts"],
	["Running over (N)", "chip", "over", "p4_toggle_over"],
	["Overdue (the tray's heading)", "chip", "overdue", "tb_open_overdue"],
];

const PP_ACTIONS = [
	"go_my_week",
	"p6c_use_view",
	"p6c_save_as",
	"p6c_manage_views",
	"p6c_set_color",
	"set_show_foreign",
	"set_draft_mode",
	"p6c_set_tap",
	"p6d_block_form",
	"open_copy_week",
	"p6d_open_conflicts",
	"undo",
	"p6c_print",
	"print_crew_sheet",
	"p4_toggle_over",
	"tb_open_overdue",
];

test("Project Planner: nothing in the old toolbar is lost", () => {
	const { pp } = projectPlanner();
	const source = fs.readFileSync(PP_JS, "utf8");
	pp.undo_stack = [{ subject: "Dig" }];
	const calls = record(pp, PP_ACTIONS);
	pp.p6d_default_date = () => "2026-10-14";
	const alerts = pp.tb_alerts();
	PP_OLD_TOOLBAR.forEach(([what, where, label, method]) => {
		if (where === "bar") {
			check(`${what}: still in the bar`, source.includes(label), true);
			return;
		}
		calls.length = 0;
		if (where === "chip") {
			const chip = alerts.find((alert) => alert.key === label);
			check(`${what}: a chip`, !!chip, true);
			if (chip) chip.open();
		} else {
			const item = find(pp.tb_items(where), label);
			check(`${what}: in ${where}`, !!item, true);
			if (item) item.on_click();
		}
		check(`${what}: reaches ${method}`, calls.length ? calls[0][0] : null, method);
	});
	check("the four menus, in the bar's order", pp.tb_menu_list().map((m) => m[1]), ["Views", "Display", "Edit", "Print"]);
	check(
		"tips are the help's first section",
		source.includes("this.p6a_tip_section(),\n\t\t\t\t...this.p6c_legend_sections(),"),
		true
	);
});

test("Project Planner: switches say their state; items follow the screen", () => {
	const { pp } = projectPlanner();
	pp.draft_on = true;
	pp.p6c.tap = false;
	pp.show_foreign = false;
	pp.p6c.color_by = "job_type";
	const edit = pp.tb_items("edit");
	check("Draft mode ticked, Tap to move not", [find(edit, "Draft mode").checked, find(edit, "Tap to move").checked], [true, false]);
	check("Maintenance, rentals, travel unticked", find(pp.tb_items("display"), "Maintenance, rentals, travel").checked, false);
	const colors = pp.tb_items("display").filter((item) => item.radio);
	check("Color by is one radio group with the mode on screen ticked", colors.filter((item) => item.checked).map((item) => item.label), ["Color: Job type"]);
	check("the saved view on screen is ticked", find(pp.tb_items("views"), "Field crew").checked, true);
	check("Undo is there but disabled with nothing to undo", find(edit, "Undo").disabled, true);
	pp.p6c_legend_entries = () => [
		{ key: "PRJ-1", label: "Plaza", color: "#123456", hi: "PRJ-1" },
		{ key: "x", label: "Other", color: "#abcdef" },
	];
	pp.p6c.highlight = { key: "PRJ-1" };
	const display = pp.tb_items("display");
	check("the color key is in Display", [!!find(display, "Plaza"), !!find(display, "Other")], [true, true]);
	check("a project in the key is a switch for its highlight, ticked while it is on", find(display, "Plaza").checked, true);
	check("anything else in the key is read-only", find(display, "Other").info, true);
	pp.view = "month";
	check("no Copy week on the month view", find(pp.tb_items("edit"), "Copy week…"), null);
	pp.view = "heatmap";
	check(
		"the heatmap: no calendar switches, no Undo; the saved views and Print stay",
		[!!find(pp.tb_items("edit"), "Tap to move"), !!find(pp.tb_items("edit"), "Undo"), pp.tb_items("display").length, !!find(pp.tb_items("views"), "Field crew"), !!find(pp.tb_items("print"), "Print this view")],
		[false, false, 0, true, true]
	);
	pp.view = "utilization";
	check(
		"Utilization: My week and the crew sheet, nothing that needs cards",
		[pp.tb_items("views").map((i) => i.label), pp.tb_items("print").map((i) => i.label)],
		[["My week"], ["Print crew sheet"]]
	);
	check("no chips off the calendar", pp.tb_alerts(), []);
});

test("Project Planner: the alert chips are there only above zero, with their counts", () => {
	const { pp, doc } = projectPlanner();
	const chips = () =>
		pp.$alerts.nodes[0].childNodes.map((el) => [el.getAttribute("data-tb-alert"), el.className, el.textContent, el.getAttribute("aria-pressed")]);
	pp.render = () => pp.tb_render();
	pp.tb_render();
	check(
		"Conflicts and Overdue in danger, Running over in warn, each a count and its word",
		chips(),
		[
			["conflicts", "pp-tb-alert pp-tb-danger", "3Conflicts", null],
			["over", "pp-tb-alert pp-tb-warn", "1Running over", "false"],
			["overdue", "pp-tb-alert pp-tb-danger", "2Overdue", null],
		]
	);
	check("the row is shown", pp.$alerts.hasClass("pp-tb-empty"), false);
	check("the phone's button carries the total", [pp.$tb_more_count.text(), pp.$tb_more.attr("aria-label")], ["6", "Filters and tools, 6 alerts"]);

	// Running over filters the board and stays up to be turned off, even with nothing past plan.
	const over = pp.$alerts.nodes[0].childNodes[1];
	over.focus();
	over.click();
	check("Running over toggles the filter", pp.over_only, true);
	check("the chip says it is pressed, and keeps focus once redrawn", [pp.$alerts.find('[data-tb-alert="over"]').attr("aria-pressed"), doc.activeElement.getAttribute("data-tb-alert")], ["true", "over"]);
	pp.data.tasks[0].over_plan = false;
	pp.tb_render();
	check("at zero while it filters, it stays (to turn it off)", pp.$alerts.find('[data-tb-alert="over"]').length, 1);

	pp.over_only = false;
	pp.p6d.center.count = () => 0;
	pp.overdue = { groups: [], total: 0 };
	pp.tb_render();
	check("nothing above zero: no chips, and the row is hidden by its class", [chips(), pp.$alerts.hasClass("pp-tb-empty")], [[], true]);
	check("the phone's button has no count", [pp.$tb_more_count.text(), pp.$tb_more.attr("aria-label")], ["", "Filters and tools"]);
	pp.p6d = { center: null };
	check("before the first conflict count: no chip", pp.tb_alerts().map((a) => a.key), []);

	// The Overdue chip opens the tray.
	pp.overdue = { groups: [{ project: "", rows: [{ name: "T9" }], count: 1 }], total: 1 };
	const trays = [];
	pp.render_trays = () => trays.push(pp.overdue_open);
	pp.$trays = { 0: { querySelector: () => null } };
	pp.tb_alerts().find((a) => a.key === "overdue").open();
	check("Overdue opens the tray", [pp.overdue_open, trays], [true, [true]]);
});

test("Project Planner: the draft strip shows only in Draft mode; Publish and Discard are the existing ones", () => {
	const { pp } = projectPlanner();
	const calls = record(pp, ["publish_drafts", "discard_drafts"]);
	pp.data.drafts = { count: 3, stale: [] };
	pp.render_draft_bar();
	check("Draft mode off: hidden and empty", [pp.$draft_bar.nodes[0].style.display, pp.$draft_bar.text()], ["none", ""]);
	pp.draft_on = true;
	pp.render_draft_bar();
	const bar = pp.$draft_bar.nodes[0];
	check("Draft mode on: shown under the toolbar", [bar.style.display, bar.previousSibling === undefined, bar.parentNode.childNodes.indexOf(bar) === bar.parentNode.childNodes.indexOf(pp.$toolbar.nodes[0]) + 1], ["", true, true]);
	check("it says so, with the count", bar.querySelector(".pp-draft-text").textContent, "Draft mode on: 3 change(s) not published yet");
	const buttons = bar.querySelectorAll("button");
	check("Publish and Discard", buttons.map((b) => b.textContent), ["Publish", "Discard"]);
	buttons[0].click();
	buttons[1].click();
	check("each calls the existing function", calls.map((c) => c[0]), ["publish_drafts", "discard_drafts"]);
	pp.data.drafts = { count: 0, stale: [] };
	pp.render_draft_bar();
	check("nothing drafted yet: it says so, no buttons", [bar.querySelector(".pp-draft-text").textContent, bar.querySelectorAll("button").length], ["Draft mode on: nothing drafted yet. Your changes stay yours until you publish them.", 0]);
});

test("Project Planner: Utilization is a view with a route", () => {
	const { pp, route } = projectPlanner();
	route.now = ["project-planner", "utilization", "2026-10-01"];
	check("its route", pp.route_target(), { view: "utilization", anchor: "2026-10-01" });
	const steps = [];
	pp.p6c_before_route = () => false;
	pp.take_route_filters = () => steps.push("filters");
	pp.set_mode = (mode) => steps.push(["mode", mode]);
	pp.load_utilization = () => steps.push("load");
	pp.handle_route();
	check("landing: the filters, the mode, the load", steps, ["filters", ["mode", "utilization"], "load"]);
	check("the view", [pp.view, pp.anchor], ["utilization", "2026-10-01"]);
	check(
		"a week over capacity carries its word, not only a color (Planned %)",
		[pp.util_pct_html(112, true), pp.util_pct_html(80, true), pp.util_pct_html(40, true), pp.util_pct_html(null, true)],
		[
			'<span class="pp-chip pp-red">112% · Over</span>',
			'<span class="pp-chip pp-amber">80% · Near full</span>',
			"40%",
			'<span class="pp-util-none">No hours</span>',
		]
	);
	check(
		"the thresholds are the report's, on the raw value: 100.4% reads 100% and is over",
		[pp.util_pct_html(100.4, true), pp.util_pct_html(100, true), pp.util_pct_html(74.6, true)],
		['<span class="pp-chip pp-red">100% · Over</span>', '<span class="pp-chip pp-amber">100% · Near full</span>', "75%"]
	);
	check("Actual % is never flagged", [pp.util_pct_html(130, false), pp.util_pct_html(90, false)], ["130%", "90%"]);
});

// A Utilization page with the pieces render_utilization draws into, and a kit whose quiet call is
// recorded and answers as told.
function utilizationPage(answer, access) {
	const env = projectPlanner();
	const { pp, $, frappe, ctx } = env;
	access = access || {};
	frappe.boot.allowed_reports = access.reports === undefined ? { "Crew Utilization": {} } : access.reports;
	frappe.boot.user = { can_get_report: access.task === false ? [] : ["Task"] };
	frappe.model = { can_get_report: (dt) => frappe.boot.user.can_get_report.indexOf(dt) !== -1 };
	const asked = [];
	const dialogs = [];
	frappe.call = (opts) => (asked.push(["frappe.call", opts.method]), Promise.resolve({}));
	frappe.msgprint = (m) => dialogs.push(m);
	ctx.window.planner_kit = {
		quiet_call: (method, args) => (asked.push(["quiet", method, args.report_name]), answer()),
		state_html: (kind, opts) => `<div class="sf-state sf-state--${kind}"><p class="sf-state-title">${opts.title}</p></div>`,
	};
	pp.view = "utilization";
	pp.anchor = "2026-10-01";
	pp.request = 0;
	pp.p6a_require = (fn) => Promise.resolve().then(fn);
	pp.range = () => ({ start: { format: () => "2026-10-01" }, end: { format: () => "2026-10-31" } });
	pp.title = () => "October 2026";
	pp.take_title_focus = () => null;
	pp.$title = $("<span></span>");
	pp.$view_buttons = { week: $("<button></button>"), utilization: $("<button></button>") };
	pp.$body = $("<div></div>");
	pp.$grid_wrap = $("<div></div>");
	pp.p6a_route = (fn) => fn();
	return Object.assign(env, { asked, dialogs });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("Project Planner: Utilization's button and panel answer to the report's access", async () => {
	const ok = utilizationPage(() => Promise.resolve({ result: [] }));
	check("both gates pass: offered", ok.pp.util_allowed(), true);
	const no_task = utilizationPage(() => Promise.resolve({}), { task: false });
	check("no report permission on Task: not offered", no_task.pp.util_allowed(), false);
	const no_report = utilizationPage(() => Promise.resolve({}), { reports: { "Other Report": {} } });
	check("the report not among the user's: not offered", no_report.pp.util_allowed(), false);
	no_report.pp.init_toolbar();
	check("the view switcher hides its button", no_report.pp.$view_buttons.utilization.nodes[0].style.display, "none");
	ok.pp.init_toolbar();
	check("and shows it to someone it lets in", ok.pp.$view_buttons.utilization.nodes[0].style.display, "");

	await no_report.pp.load_utilization();
	await settle();
	const text = no_report.pp.$grid_wrap.nodes[0].querySelector(".pp-util-denied");
	check("arrived by the address anyway: the no-access panel", !!text && text.innerHTML.includes("You don't have access to the Crew Utilization report."), true);
	check("the kit's no-access markup", !!text && text.innerHTML.includes("sf-state--no-access"), true);
	check("and nothing asked of the server, no dialog", [no_report.asked, no_report.dialogs], [[], []]);
});

test("Project Planner: a refusal from the server is a panel, not frappe's red dialog", async () => {
	const page = utilizationPage(() => Promise.reject({ status: 403, exc_type: "PermissionError", message: "No" }));
	await page.pp.load_utilization();
	await settle();
	check("asked through the kit's quiet call only", page.asked, [["quiet", "frappe.desk.query_report.run", "Crew Utilization"]]);
	check("no dialog", page.dialogs, []);
	check("the no-access panel", !!page.pp.$grid_wrap.nodes[0].querySelector(".pp-util-denied"), true);
	const broken = utilizationPage(() => Promise.reject({ status: 0, exc_type: "", message: "offline" }));
	await broken.pp.load_utilization();
	await settle();
	check(
		"any other failure says it could not be loaded",
		broken.pp.$grid_wrap.nodes[0].textContent.includes("Utilization could not be loaded"),
		true
	);
});

test("Project Planner: Utilization's table flags Planned % only, and explains its partial weeks", async () => {
	const rows = [
		{ resource: "R1", label: "Austin", group: "Field", period: "Week of Sep 28", capacity: 24, booked: 24.1, actual: 30, utilization: 100.4, actual_pct: 125 },
		{ resource: "R1", label: "Austin", group: "Field", period: "Week of Oct 5", capacity: 40, booked: 10, actual: 8, utilization: 25, actual_pct: 20 },
	];
	const page = utilizationPage(() => Promise.resolve({ result: rows }));
	await page.pp.load_utilization();
	await settle();
	const html = page.pp.$grid_wrap.nodes[0].querySelector(".pp-util-scroll").innerHTML;
	check("Planned % past 100 (raw 100.4) is Over", html.includes('<span class="pp-chip pp-red">100% · Over</span>'), true);
	check("Actual % at 125 is not flagged", [html.includes("125%"), html.includes("125% · Over")], [true, false]);
	check("the column says Planned %", html.includes("Planned %"), true);
	const note = page.pp.$grid_wrap.nodes[0].querySelector(".pp-util-note");
	check("a note explains the partial first and last weeks", !!note && note.textContent.includes("first and last weeks"), true);
});

test("Project Planner: on Utilization, Ctrl+Z is not the planner's and empty menus are not offered", () => {
	const { pp } = projectPlanner();
	const undone = [];
	pp.undo = () => undone.push(1);
	pp.view = "utilization";
	check("Ctrl+Z on Utilization: not taken, nothing undone", [pp.p6b_run_key("undo"), undone.length], [false, 0]);
	pp.view = "week";
	check("Ctrl+Z on the week still undoes", [pp.p6b_run_key("undo"), undone.length], [true, 1]);
	pp.view = "utilization";
	pp.tb_render();
	const off = (name) => pp.tb.$menus[name].hasClass("pp-tb-off");
	check("Display and Edit have nothing there and are hidden; Views and Print stay", [off("views"), off("display"), off("edit"), off("print")], [false, true, true, false]);
	pp.view = "week";
	pp.tb_render();
	check("back on the week, all four", [off("views"), off("display"), off("edit"), off("print")], [false, false, false, false]);
});

test("Project Planner: the phone's sheet takes the filters in and gives them back", () => {
	const { pp } = projectPlanner();
	let opts = null;
	const closes = [];
	pp.tb.kit = {
		toolbar: {
			sheet: (o) => {
				opts = o;
				return { is_open: () => true, refresh: () => null, close: (how) => closes.push(how) };
			},
		},
	};
	pp.tb_open_sheet();
	const slot = pp.$toolbar.nodes[0].ownerDocument.createElement("div");
	opts.top(slot);
	check("the filters moved into the sheet, marked", [slot.contains(pp.$filters.nodes[0]), pp.$filters.hasClass("pp-tb-in-sheet")], [true, true]);
	const sections = opts.sections();
	check("its sections", sections.map((s) => s.title), ["Alerts", "Views", "Display", "Edit", "Print", "This page"]);
	check("the chips are in it", sections[0].items.map((i) => [i.label, i.tone]), [["3 Conflicts", "danger"], ["1 Running over", "warn"], ["2 Overdue", "danger"]]);
	check("a switch keeps the sheet open; an action does not", [find(sections[3].items, "Draft mode").keep_open, find(sections[3].items, "Copy week…").keep_open], [true, false]);
	check("the help and refresh, which the phone's bar has no room for", sections[5].items.map((i) => i.label), ["How to read the planner", "Refresh"]);
	opts.on_close("back");
	const bar = pp.$toolbar.nodes[0];
	check("closed: the filters are back in the bar, before the chips", [bar.contains(pp.$filters.nodes[0]), bar.childNodes.indexOf(pp.$filters.nodes[0]) + 1 === bar.childNodes.indexOf(pp.$alerts.nodes[0]), pp.$filters.hasClass("pp-tb-in-sheet")], [true, true, false]);
	pp.tb.sheet = { close: (how) => closes.push(how) };
	pp.tb_close_sheet();
	check("a search pick closes it first, as the person's own close", closes, ["user"]);
});

// The Maintenance Planner's old toolbar.
const MP_OLD_TOOLBAR = [
	["‹ Earlier", "bar", "this.shift(-1)"],
	["Today", "bar", "this.go(this.view, frappe.datetime.get_today())"],
	["Later ›", "bar", "this.shift(1)"],
	["Month / Week / Crew", "bar", '["crew", __("Crew")],'],
	["Search", "bar", "$wrap.prependTo(this.$filters)"],
	["Technician filter", "bar", ".appendTo(this.$filters);"],
	["?", "bar", 'class="btn btn-default btn-sm mp-p6a-help">?</button>'],
	["↻", "bar", 'class="btn btn-default btn-sm mp-refresh">↻</button>'],
	["Undo (icon)", "bar", 'class="btn btn-default btn-sm mp-undo mp-empty">↶</button>'],
	["Views: Save view as…", "views", "Save current view as…", "p6c_save_as"],
	["Color by", "display", "Color: Technician", "p6c_set_color"],
	["Projected visits", "display", "Projected visits", "set_show_projected"],
	["Project work", "display", "Project work", "set_show_project_work"],
	["The visit-kind legend row", "display", "Pending review", null],
	["Draft mode", "edit", "Draft mode", "set_draft_mode"],
	["Tap to move", "edit", "Tap to move", "p6c_set_tap"],
	["Add visit", "edit", "Add visit…", "p6e_add_visit"],
	["Block time", "edit", "Block time…", "p6d_block_form"],
	["Conflicts", "edit", "Conflicts…", "p6d_open_conflicts"],
	["Undo", "edit", "Undo", "undo"],
	["Print", "print", "Print this view", "p6c_print"],
	["Overdue (new chip)", "chip", "overdue", "tb_open_overdue"],
];

test("Maintenance Planner: nothing in the old toolbar is lost", () => {
	const { mp } = maintenancePlanner();
	const source = fs.readFileSync(MP_JS, "utf8");
	const calls = record(mp, [
		"p6c_save_as",
		"p6c_set_color",
		"set_show_projected",
		"set_show_project_work",
		"set_draft_mode",
		"p6c_set_tap",
		"p6e_add_visit",
		"p6d_block_form",
		"p6d_open_conflicts",
		"undo",
		"p6c_print",
		"tb_open_overdue",
	]);
	mp.p6d_default_date = () => "2026-10-14";
	MP_OLD_TOOLBAR.forEach(([what, where, label, method]) => {
		if (where === "bar") {
			check(`${what}: still in the bar`, source.includes(label), true);
			return;
		}
		calls.length = 0;
		if (where === "chip") {
			const chip = mp.tb_alerts().find((alert) => alert.key === label);
			check(`${what}: a chip`, !!chip, true);
			if (chip) chip.open();
		} else {
			const item = find(mp.tb_items(where), label);
			check(`${what}: in ${where}`, !!item, true);
			if (item && item.on_click) item.on_click();
		}
		check(`${what}: reaches ${method}`, calls.length ? calls[0][0] : null, method);
	});
	check("the four menus", mp.tb_menu_list().map((m) => m[1]), ["Views", "Display", "Edit", "Print"]);
	check("tips are the help's first section", source.includes("this.p6a_tip_section(),\n\t\t\t\t...this.p6c_legend_sections(),"), true);
	check("the legend row is gone from under the toolbar", source.includes('class="mp-legend"'), false);
});

test("Maintenance Planner: switches, kinds, chips and the draft strip", () => {
	const { mp } = maintenancePlanner();
	const display = mp.tb_items("display");
	check("Projected visits ticked, Project work not", [find(display, "Projected visits").checked, find(display, "Project work").checked], [true, false]);
	check(
		"the visit kinds are Display's key, read-only, each drawn with its card's class",
		display.filter((i) => i.info).map((i) => [i.label, i.sample_html]).slice(0, 2),
		[
			["Scheduled visit", '<i class="mp-swatch"></i>'],
			["Seasonal", '<i class="mp-swatch mp-seasonal"></i>'],
		]
	);
	const edit = mp.tb_items("edit");
	check("Tap to move ticked, Draft mode not", [find(edit, "Tap to move").checked, find(edit, "Draft mode").checked], [true, false]);
	check("Undo can run", find(edit, "Undo").disabled, false);
	mp.data.can_create_visits = false;
	mp.data.can_schedule = false;
	check("Add visit and Block time only for those who may", [find(mp.tb_items("edit"), "Add visit…"), find(mp.tb_items("edit"), "Block time…")], [null, null]);

	mp.tb_render();
	check("Overdue only (no conflicts): one chip", mp.$alerts.nodes[0].childNodes.map((el) => [el.className, el.textContent]), [["mp-tb-alert mp-tb-danger", "1Overdue"]]);
	mp.technician = "b@x.com";
	mp.tb_render();
	check("a filter that hides the overdue visit hides its chip", [mp.$alerts.nodes[0].childNodes.length, mp.$alerts.hasClass("mp-tb-empty")], [0, true]);
	mp.technician = "";
	mp.p6d.center.count = () => 2;
	check("conflicts above zero: the chip", mp.tb_alerts().map((a) => [a.key, a.count]), [["conflicts", 2], ["overdue", 1]]);

	// The Overdue chip lists the overdue visits in a side panel; one opens its visit.
	let drawer = null;
	const opened = [];
	const bodies = [];
	const panel = { open: true, is_open: () => panel.open, set_body: (html) => bodies.push(html) };
	mp.p6a = { kit: { drawer: { open: (o) => ((drawer = o), panel) } } };
	mp.open_card = (card) => opened.push(card.key);
	mp.by_key = { v1: mp.data.visits[0] };
	mp.tb_open_overdue();
	check("a side panel of overdue visits", [drawer.title, drawer.body.includes('data-tb-overdue="v1"'), drawer.body.includes("Hotel"), drawer.body.includes("Plaza")], ["Overdue visits", true, true, false]);
	const row = { closest: () => ({ getAttribute: () => "v1" }) };
	drawer.on_click({ target: row });
	check("a row opens its visit", opened, ["v1"]);
	// Moved on the calendar behind it: the planner reloads, and the open list follows.
	mp.data.visits[0].overdue = false;
	mp.tb_render();
	check("a reload while it is open redraws the list", [bodies.length, bodies[0].includes("No overdue visits on screen.")], [1, true]);
	panel.open = false;
	mp.tb_render();
	check("closed, it is left alone", bodies.length, 1);

	const calls = record(mp, ["c1_publish", "c1_discard"]);
	mp.data.drafts = { count: 2, stale: [] };
	mp.c1_render_bar();
	check("Draft mode off: no strip", mp.$draft_bar.nodes[0].style.display, "none");
	mp.draft_on = true;
	mp.c1_render_bar();
	const bar = mp.$draft_bar.nodes[0];
	check("Draft mode on: the strip says so", [bar.style.display, bar.querySelector(".mp-draft-text").textContent], ["", "Draft mode on: 2 change(s) not published yet"]);
	bar.querySelectorAll("button").forEach((b) => b.click());
	check("Publish and Discard are C1's own", calls.map((c) => c[0]), ["c1_publish", "c1_discard"]);
});

test("Maintenance Planner: the phone's sheet", () => {
	const { mp } = maintenancePlanner();
	let opts = null;
	mp.tb.kit = { toolbar: { sheet: (o) => ((opts = o), { is_open: () => true, refresh: () => null }) } };
	mp.tb_open_sheet();
	const slot = mp.$body.nodes[0].ownerDocument.createElement("div");
	opts.top(slot);
	check("the filter moved in", slot.contains(mp.$filters.nodes[0]), true);
	check("its sections", opts.sections().map((s) => s.title), ["Alerts", "Views", "Display", "Edit", "Print", "This page"]);
	check("the page head's Day Board is in it too", opts.sections()[5].items.map((i) => i.label), ["How to read the planner", "Day Board", "Refresh"]);
	opts.on_close("user");
	check("closed: the filter is back before the chips", mp.$filters.nodes[0].parentNode.childNodes.indexOf(mp.$filters.nodes[0]) + 1, mp.$alerts.nodes[0].parentNode.childNodes.indexOf(mp.$alerts.nodes[0]));
});

// The planner kit can already be loaded when the page is first routed, so tb_render runs from set_mode
// before load() has answered: this.data is still null. A saved Color by of "person" (or any mode that
// reads the data) must not throw there, or handle_route aborts and load() never runs.
test("Project Planner: tb_render before the first load, with data null, throws in no Color by mode", () => {
	["person", "project", "pm", "job_type", "status"].forEach((mode) => {
		const { pp, ctx } = projectPlanner();
		const proto = vm.runInContext("ProjectPlanner", ctx).prototype;
		pp.p6c_legend_entries = proto.p6c_legend_entries;
		pp.p6a = { kit: { big_picture: { palette_legend: () => [], hash_color: () => "#123456" } } };
		pp.by_resource = {};
		pp.data = null;
		pp.p6c.color_by = mode;
		let error = null;
		try {
			pp.tb_render();
			pp.tb_items("display");
		} catch (e) {
			error = e && e.message;
		}
		check(`Color by ${mode}: nothing thrown`, error, null);
		check(`Color by ${mode}: no people to list`, pp.resources(), []);
	});
});

test("Maintenance Planner: tb_render before the first load, with data null, throws in no Color by mode", () => {
	["technician", "site", "contract", "status"].forEach((mode) => {
		const { mp, ctx } = maintenancePlanner();
		const proto = vm.runInContext("MaintenancePlanner", ctx).prototype;
		mp.p6c_legend_entries = proto.p6c_legend_entries;
		mp.p6a = { kit: { big_picture: { palette_legend: () => [], hash_color: () => "#123456" } } };
		mp.data = null;
		mp.p6c.color_by = mode;
		let error = null;
		try {
			mp.tb_render();
			mp.tb_items("display");
		} catch (e) {
			error = e && e.message;
		}
		check(`Color by ${mode}: nothing thrown`, error, null);
	});
});

// ------------------------------------------------------------------ run

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (error) {
			failures += 1;
			console.log(`  ERROR ${error && error.stack}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
