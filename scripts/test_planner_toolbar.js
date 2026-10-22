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
 *      Toolbar v2 (the planner UX redesign, P1): the kit's tabs control (a tablist: roles, roving
 *      tabindex, arrow keys that move focus only) and the section flattening that turns Display,
 *      Views, Tools and Print into More's one list.
 *   2. Both planner pages' toolbar blocks (PPTB, MPTB) on an instance of each page's own class, with
 *      a recording stand-in for jQuery over the same fake DOM: every control the old toolbar had is
 *      still reachable under + Add or More (an explicit list, each item run and the method it calls
 *      checked, in the menu's list and in the phone's sheet), Split and Move by keep a home, toggles
 *      say their state, the alert chips are there only above zero and open what their buttons did,
 *      the hold's strip line shows only while "Hold all my changes" is on and its Publish and Discard call the existing
 *      publish and discard, the phone's sheet takes the filters in and gives them back, and the
 *      Project Planner's Capacity screen (the Heatmap and Utilization merged, P7) is a route.
 *   3. The Crew board in days (P7): bars of a task's days along a person's row, Show hours, the Board |
 *      Capacity header as routes, Capacity's two sections (the heatmap, and the Utilization table behind
 *      the report's own gate), and the saved views and old addresses that say heatmap or utilization.
 *
 *   4. The Maintenance Planner simplify (P10): project work as one busy strip a technician-day (and as
 *      cards, or not at all, on request) in the month, the week and the crew view, with its tooltip, its
 *      click and the print; Resources available as one line until it is opened; the key of the visit
 *      kinds in the "?" help only, as one provider function that later phases extend; My day to the
 *      Project Planner's Today; and the two new switches in the saved views.
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

// What a page's toolbar block asks of the kit besides menus and the sheet, from the real module.
function kitHelpers() {
	const kit = loadKit();
	return { sections_to_items: kit.sections_to_items, tidy: kit.tidy };
}

function loadKit() {
	const ctx = { console, Date, Math, Array, Object, String, Number, JSON };
	vm.createContext(ctx);
	vm.runInContext(`${kitSource()}\nthis.kit = { create_guard, create_toolbar, item_role, tidy, sections_to_items };`, ctx);
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

test("sections_to_items makes one menu list out of named sections", () => {
	const { kit } = toolbarEnv();
	const out = kit.sections_to_items([
		{ title: "Display", items: [{ heading: "Color by" }, { label: "Color: Job type", radio: true, checked: true }, { divider: true }] },
		{ title: "Views", items: [] },
		null,
		{ title: "Tools", items: [{ divider: true }, { label: "Undo" }] },
		{ title: "Print", items: [{ heading: "Nothing under me" }] },
	]);
	check(
		"a heading over each section with something in it, a divider between, nothing for an empty one",
		out.map((entry) => (entry.divider ? "—" : entry.heading || entry.label)),
		["Display", "Color by", "Color: Job type", "—", "Tools", "Undo"]
	);
	check("a section heading is marked, so the menu can set it apart from a sub-heading", [out[0].section, out[1].section], [true, undefined]);
	check("no sections, no list", kit.sections_to_items([]), []);
});

test("The view tabs are a tablist: roles, a roving tabindex, arrow keys that move focus only", () => {
	const { doc, b, toolbar, bar } = toolbarEnv();
	const group = doc.createElement("span");
	bar.appendChild(group);
	const make = (view) => {
		const button = doc.createElement("button");
		button.setAttribute("data-tb-tab", view);
		group.appendChild(button);
		return button;
	};
	const [month, week, crew, heatmap] = ["month", "week", "crew", "heatmap"].map(make);
	const tabs = toolbar.tabs(group, { label: "Planner views", selected: "week" });
	check("the group is a tablist with a name", [group.getAttribute("role"), group.getAttribute("aria-label")], ["tablist", "Planner views"]);
	check("each button is a tab", [month, week, crew, heatmap].map((button) => button.getAttribute("role")), ["tab", "tab", "tab", "tab"]);
	check("the selected tab says so; only it is in the tab order", [month, week, crew, heatmap].map((button) => [button.getAttribute("aria-selected"), button.getAttribute("tabindex")]), [["false", "-1"], ["true", "0"], ["false", "-1"], ["false", "-1"]]);
	week.focus();
	const right = key(week, "ArrowRight");
	check("ArrowRight moves focus to the next tab, and does not open it", [right.defaultPrevented, doc.activeElement === crew, week.getAttribute("aria-selected")], [true, true, "true"]);
	key(crew, "End");
	check("End", doc.activeElement === heatmap, true);
	key(heatmap, "ArrowRight");
	check("ArrowRight from the last wraps to the first", doc.activeElement === month, true);
	key(month, "ArrowLeft");
	check("ArrowLeft from the first wraps to the last", doc.activeElement === heatmap, true);
	heatmap.style.display = "none";
	month.focus();
	key(month, "ArrowLeft");
	check("a tab the page has hidden (Utilization without the report) is skipped", doc.activeElement === crew, true);
	tabs.sync("crew");
	check("sync moves the selection and the tab stop", [week.getAttribute("aria-selected"), crew.getAttribute("aria-selected"), crew.getAttribute("tabindex"), week.getAttribute("tabindex")], ["false", "true", "0", "-1"]);
	tabs.sync("my-week");
	check("on a screen that is no tab, none is selected and the first one stays reachable", [[month, week, crew].map((button) => button.getAttribute("aria-selected")), month.getAttribute("tabindex")], [["false", "false", "false"], "0"]);
	check("tabs push no history entry", pushes(b), 0);
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
	const route = { now: [], set: [] };
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
		set_route: (...args) => route.set.push(args),
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
		by_project: { "PRJ-1": { name: "PRJ-1", title: "Plaza" } },
		t1: {},
		data: {
			can_edit: true,
			can_schedule: true,
			projects: [{ name: "PRJ-1", title: "Plaza" }],
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
	pp.tb.kit = { toolbar: kitHelpers() };
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
	mp.tb.kit = { toolbar: kitHelpers() };
	return Object.assign(env, { mp });
}

// One of More's four sections, or + Add's list, as the page builds them.
function section(page, where) {
	if (where === "add") return page.tb_add_items();
	const found = page.tb_more_sections().find((entry) => entry.key === where);
	return found ? found.items : [];
}

// The Project Planner's old toolbar, control by control, and where each is now. "bar" controls are
// pinned in the page source; the rest are items of + Add or of one of More's sections (run, and the
// method they reach checked) or alert chips.
const PP_OLD_TOOLBAR = [
	["‹ Earlier", "bar", "this.shift(-1)"],
	["Today", "bar", "this.go(this.view, frappe.datetime.get_today())"],
	["Later ›", "bar", "this.shift(1)"],
	["Month / Week / Crew", "bar", '["crew", __("Crew")],'],
	["Heatmap (now Capacity, in the Crew tab's header)", "bar", '[PP.capacity_view, __("Capacity"), __('],
	["Utilization (now Capacity's table under the heatmap)", "bar", "this.load_utilization(token)"],
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
	["Highlight a project", "display", "Highlight a project…", "tb_highlight_prompt"],
	["Maintenance, rentals, travel", "display", "Maintenance, rentals, travel", "set_show_foreign"],
	["Draft mode (Hold all my changes)", "tools", "Hold all my changes", "set_draft_mode"],
	["Tap to move", "tools", "Tap to move", "p6c_set_tap"],
	["Block time", "tools", "Block time…", "p6d_block_form"],
	["Copy week…", "tools", "Copy week…", "open_copy_week"],
	["Conflicts (also a chip)", "tools", "Conflicts…", "p6d_open_conflicts"],
	["Undo", "tools", "Undo", "undo"],
	["Print: this view", "print", "Print this view", "p6c_print"],
	["Print: selection only", "print", "Print selection only (2)", "p6c_print"],
	["Print crew sheet", "print", "Print crew sheet", "print_crew_sheet"],
	["Quick add (the double-click's dialog)", "add", "Quick add task…", "p6b_quick_add"],
	["Build out tasks", "add", "Build out tasks…", "tb_build_out"],
	["Day note", "add", "Day note…", "p6d_note_form"],
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
	"tb_highlight_prompt",
	"set_show_foreign",
	"set_draft_mode",
	"p6c_set_tap",
	"p6d_block_form",
	"open_copy_week",
	"p6d_open_conflicts",
	"undo",
	"p6c_print",
	"print_crew_sheet",
	"p6b_quick_add",
	"tb_build_out",
	"p6d_note_form",
	"p4_toggle_over",
	"tb_open_overdue",
];

test("Project Planner: nothing in the old toolbar is lost, and + Add and More are all that is left of the menus", () => {
	const { pp } = projectPlanner();
	const source = fs.readFileSync(PP_JS, "utf8");
	pp.undo_stack = [{ subject: "Dig" }];
	const calls = record(pp, PP_ACTIONS);
	pp.p6d_default_date = () => "2026-10-14";
	const alerts = pp.tb_alerts();
	const more = pp.tb_items("more");
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
			const item = find(section(pp, where), label);
			check(`${what}: in ${where}`, !!item, true);
			// Reached by the menu a person opens too: the flat list, with the same item in it.
			check(`${what}: in the menu's list`, !!find(where === "add" ? pp.tb_items("add") : more, label), true);
			if (item) item.on_click();
		}
		check(`${what}: reaches ${method}`, calls.length ? calls[0][0] : null, method);
	});
	check("two menus", pp.tb_menu_list().map((m) => m[1]), ["Add", "More"]);
	check("their buttons read + Add and More", pp.tb_menu_list().map((m) => m[3]), ["+ Add", "More"]);
	check("More's sections, in order", more.filter((item) => item.section).map((item) => item.heading), ["Display", "Views", "Tools", "Print"]);
	check(
		"tips are the help's first section",
		source.includes("this.p6a_tip_section(),\n\t\t\t\t...this.p6c_legend_sections(),") || source.includes("this.p6a_tip_section(),\r\n\t\t\t\t...this.p6c_legend_sections(),"),
		true
	);
});

test("Project Planner: + Add starts the double-click's dialog, the build-out grid and the day note", () => {
	const { pp, frappe } = projectPlanner();
	const calls = record(pp, ["p6b_quick_add", "t1_open_build_out", "p6d_note_form"]);
	pp.p6d_default_date = () => "2026-10-14";
	pp.by_project = { "PRJ-1": { name: "PRJ-1", title: "Plaza" } };
	pp.data.projects = [{ name: "PRJ-1", title: "Plaza" }];
	check("labels", pp.tb_add_items().map((item) => item.label), ["Quick add task…", "Build out tasks…", "Day note…"]);
	find(pp.tb_add_items(), "Quick add task…").on_click();
	check("Quick add opens on the first day on screen, on nobody's row", calls[0], ["p6b_quick_add", { kind: "cell", resource: "", ymd: "2026-10-14" }]);
	find(pp.tb_add_items(), "Day note…").on_click();
	check("the day note opens on that day", calls[1], ["p6d_note_form", { date: "2026-10-14" }]);
	// Build out: the project in the filter, else the person is asked.
	pp.project = "PRJ-1";
	find(pp.tb_add_items(), "Build out tasks…").on_click();
	check("with a project filtered, the grid opens for it", calls[2], ["t1_open_build_out", "PRJ-1", "Plaza"]);
	pp.project = "";
	let prompted = null;
	frappe.prompt = (fields, done, title) => (prompted = { fields, done, title });
	find(pp.tb_add_items(), "Build out tasks…").on_click();
	check("with none, a Project field is asked for first", [prompted.fields.map((f) => f.fieldname), prompted.fields[0].reqd, prompted.title], [["project"], 1, "Build out tasks for"]);
	prompted.done({ project: "PRJ-1" });
	check("and the grid opens for the answer", calls[3], ["t1_open_build_out", "PRJ-1", "Plaza"]);
	// Who sees what.
	pp.data.can_edit = false;
	pp.data.can_schedule = false;
	check("someone who cannot edit or schedule is offered none of it", pp.tb_add_items(), []);
	pp.data.can_edit = true;
	pp.view = "capacity";
	check("off the calendar views only the build-out grid is offered (Capacity, the heatmap and Utilization merged)", pp.tb_add_items().map((item) => item.label), ["Build out tasks…"]);
});

test("Project Planner: switches say their state; items follow the screen", () => {
	const { pp } = projectPlanner();
	pp.draft_on = true;
	pp.p6c.tap = false;
	pp.show_foreign = false;
	pp.p6c.color_by = "job_type";
	const tools = section(pp, "tools");
	check("Hold all my changes ticked, Tap to move not", [find(tools, "Hold all my changes").checked, find(tools, "Tap to move").checked], [true, false]);
	check("Maintenance, rentals, travel unticked", find(section(pp, "display"), "Maintenance, rentals, travel").checked, false);
	const colors = section(pp, "display").filter((item) => item.radio);
	check("Color by is one radio group with the mode on screen ticked", colors.filter((item) => item.checked).map((item) => item.label), ["Color: Job type"]);
	check("the saved view on screen is ticked", find(section(pp, "views"), "Field crew").checked, true);
	check("Undo is there but disabled with nothing to undo", find(tools, "Undo").disabled, true);
	pp.p6c_legend_entries = () => [
		{ key: "PRJ-1", label: "Plaza", color: "#123456", hi: "PRJ-1" },
		{ key: "x", label: "Other", color: "#abcdef" },
	];
	pp.p6c.highlight = { key: "PRJ-1", label: "Plaza" };
	const display = section(pp, "display");
	check("the color key is in Display", [!!find(display, "Plaza"), !!find(display, "Other")], [true, true]);
	check("a project in the key is a switch for its highlight, ticked while it is on", find(display, "Plaza").checked, true);
	check("anything else in the key is read-only", find(display, "Other").info, true);
	const stop = find(display, "Highlighting Plaza");
	check("a highlight that is on can be switched off from Display, and says it is on", [!!stop, stop && stop.checked, find(display, "Highlight a project…")], [true, true, null]);
	const cleared = record(pp, ["p6c_clear_highlight"]);
	stop.on_click();
	check("which is the pill's ×", cleared[0][0], "p6c_clear_highlight");
	pp.view = "month";
	check("no Copy week on the month view", find(section(pp, "tools"), "Copy week…"), null);
	pp.view = "capacity";
	check(
		"Capacity: no calendar switches, no Undo; the saved views and Print stay",
		[!!find(section(pp, "tools"), "Tap to move"), !!find(section(pp, "tools"), "Undo"), section(pp, "display").length, !!find(section(pp, "views"), "Field crew"), !!find(section(pp, "print"), "Print this view")],
		[false, false, 0, true, true]
	);
	check("and the Display section, empty there, is left out with its heading", pp.tb_items("more").filter((item) => item.section).map((item) => item.heading), ["Views", "Tools", "Print"]);
	check(
		"Capacity: My week with the saved views, Print this view and the crew sheet, only Draft mode in Tools",
		[
			section(pp, "views").map((i) => i.label).filter((label) => label === "My week"),
			section(pp, "print").map((i) => i.label).filter((label) => label === "Print crew sheet"),
			section(pp, "tools").map((i) => i.label),
		],
		[["My week"], ["Print crew sheet"], ["Hold all my changes"]]
	);
	check("no chips off the calendar", pp.tb_alerts(), []);
});

test("Project Planner: More > Display has Show all tasks, which says how many it leaves out and asks again", () => {
	const { pp } = projectPlanner();
	pp.data.hidden_tasks = 38;
	let item = find(section(pp, "display"), "Show all tasks");
	check("it is there on the calendar, off, with the count", [!!item, item.checked, item.hint], [true, false, "38 not shown"]);
	check("and explains the rule", /phase/i.test(item.title) && /crew/i.test(item.title), true);
	const calls = record(pp, ["pick_filter"]);
	item.on_click();
	check("turning it on is a filter pick (remembered, in the address, a new load)", calls, [["pick_filter", "all_tasks", "1"]]);
	pp.all_tasks = "1";
	item = find(section(pp, "display"), "Show all tasks");
	check("on: ticked, and nothing to count", [item.checked, item.hint], [true, ""]);
	calls.length = 0;
	item.on_click();
	check("and it turns off the same way", calls, [["pick_filter", "all_tasks", ""]]);
	pp.all_tasks = "";
	pp.data.hidden_tasks = 0;
	check("with nothing left out there is no count", find(section(pp, "display"), "Show all tasks").hint, "");
	pp.view = "capacity";
	check("not on Capacity: it only changes the calendar", find(section(pp, "display"), "Show all tasks"), null);
});

test("Project Planner: Show all tasks is part of the view, and the print says what was left out", () => {
	const { pp } = projectPlanner();
	pp.all_tasks = "";
	pp.p6c_pick = (state) => Object.assign({}, state);
	check("a view carries it", [pp.p6c_state(true).all_tasks, (pp.all_tasks = "1", pp.p6c_state(true).all_tasks)], [false, true]);
	delete pp.p6c_view_summary;
	check("the summary names it", pp.p6c_view_summary({ all_tasks: true }), "All tasks");
	pp.all_tasks = "";
	pp.data.hidden_tasks = 38;
	pp.show_foreign = true;
	pp.p6c.highlight = null;
	check("the print says tasks were not shown", pp.p6c_print_lines(false, 0).filter((line) => /not shown/.test(line)), ["Phase and crew work only (38 tasks not shown)"]);
	pp.all_tasks = "1";
	check("and says nothing when it is all", pp.p6c_print_lines(false, 0).filter((line) => /not shown/.test(line)), []);
	// Applying a view that shows another set of tasks asks the server; one that does not, only draws.
	pp.p6c_sync_controls = () => null;
	pp.write_filter_url = () => null;
	pp.show_all_unscheduled = false;
	const calls = record(pp, ["load", "render"]);
	pp.all_tasks = "";
	pp.p6c_apply_state({ view: "week", all_tasks: true }, {});
	check("a view with Show all tasks loads again", [pp.all_tasks, calls.map((c) => c[0])], ["1", ["load"]]);
	calls.length = 0;
	pp.p6c_apply_state({ view: "week", all_tasks: true }, {});
	check("the same set is only drawn", calls.map((c) => c[0]), ["render"]);
	calls.length = 0;
	pp.p6c_apply_state({ view: "week" }, {});
	check("a view saved before this one is phase and crew work only", [pp.all_tasks, calls.map((c) => c[0])], ["", ["load"]]);
});

test("Project Planner: 'last used' keeps Show all tasks a flag even when the address named it, and a toggle saves it", () => {
	const { pp, frappe } = projectPlanner();
	const sent = [];
	frappe.call = (opts) => (sent.push(opts.args && opts.args.data ? JSON.parse(opts.args.data) : null), Promise.resolve({}));
	pp.p6c_pick = (state) => Object.assign({}, state);
	pp.p6c.saved_text = null;
	pp.all_tasks = "1";
	pp.own_filters = { project: "", pm: "", group: "", all_tasks: "" };
	pp.url_filters = { all_tasks: "1" };
	pp.p6c_save_last();
	check("a link's all_tasks is that visit's: the person's own value, as a flag", [typeof sent[0].all_tasks, sent[0].all_tasks], ["boolean", false]);
	pp.url_filters = null;
	pp.p6c.saved_text = null;
	sent.length = 0;
	pp.p6c_save_last();
	check("their own pick is saved as true", sent[0].all_tasks, true);
	// The kit's menu sits outside the page body, so a pick from it asks for the save itself.
	const soon = record(pp, ["load", "p6c_state_soon", "write_filter_url"]);
	pp.set_all_tasks(false);
	check("a pick from the menu loads the week and schedules the save", soon.map((c) => c[0]).sort(), ["load", "p6c_state_soon", "write_filter_url"]);
});

test("Project Planner: Job type is the colors until someone chooses, and the view carries the choice", () => {
	const { pp, ctx } = projectPlanner();
	check("the default color", vm.runInContext("PP6C.default_color", ctx), "job_type");
	const source = fs.readFileSync(PP_JS, "utf8");
	check("a fresh planner starts on it, unchosen", /color_by: PP6C\.default_color,\s*\n\s*color_chosen: false,/.test(source), true);
	// "Task color" saved in "last used" before the flag existed is the old default, not a wish.
	let applied = null;
	pp.p6c_apply_state = (state) => {
		applied = state;
	};
	delete pp.p6c_view_summary;
	delete pp.p6c_matching_view;
	pp.p6c.loaded = true;
	pp.p6c.restored = false;
	pp.p6c.last = { view: "week", color_by: "default", project: "PRJ-1" };
	pp.p6c_pick = (state) => Object.assign({}, state);
	vm.runInContext("frappe", ctx).get_route = () => ["project-planner", "week", "2026-10-14"];
	pp.p6c_before_route();
	check("an old saved Task color, with no flag, is dropped on the first open", applied, { view: "week", project: "PRJ-1" });
	pp.p6c.restored = false;
	pp.p6c.last = { view: "week", color_by: "default", color_chosen: false };
	pp.p6c_before_route();
	check("so is one the view says was not chosen", applied, { view: "week", color_chosen: false });
	// The person picks a color (Task color included): the view says so, on the server, for any device.
	pp.render = () => null;
	pp.p6c_set_color("default");
	check("picking Task color sets the flag", [pp.p6c.color_by, pp.p6c.color_chosen], ["default", true]);
	check("and the saved state carries it, \"last used\" included", [pp.p6c_state(true).color_by, pp.p6c_state(true).color_chosen, pp.p6c_state(false).color_chosen], ["default", true, true]);
	pp.p6c.restored = false;
	pp.p6c.last = { view: "week", color_by: "default", color_chosen: true };
	pp.p6c_before_route();
	check("and then a saved Task color is restored, on any device", applied, { view: "week", color_by: "default", color_chosen: true });
	pp.p6c_set_color("nonsense");
	check("an unknown mode falls back to the default", pp.p6c.color_by, "job_type");
	check("a saved view's summary words a non-default color, Task color included", [pp.p6c_view_summary({ color_by: "job_type" }), pp.p6c_view_summary({ color_by: "default" }), pp.p6c_view_summary({ color_by: "person" })], ["", "Task colors", "Person colors"]);
});

test("Project Planner: applying a view sets whether its color was chosen, and a view still matches without the flag", () => {
	const { pp } = projectPlanner();
	pp.p6c_pick = (state) => Object.assign({}, state);
	pp.p6c_sync_controls = () => null;
	pp.filters = () => ({});
	pp.write_filter_url = () => null;
	pp.render = () => null;
	pp.p6c_apply_state({ view: "week", color_by: "job_type" }, { render: false });
	check("a view saved before the flag with another color: not a choice", [pp.p6c.color_by, pp.p6c.color_chosen], ["job_type", false]);
	pp.p6c_apply_state({ view: "week", color_by: "default" }, { render: false });
	check("a view saved before the flag that holds Task color is a snapshot someone took: a choice", [pp.p6c.color_by, pp.p6c.color_chosen], ["default", true]);
	pp.p6c_apply_state({ view: "week", color_by: "default", color_chosen: false }, { render: false });
	check("a view that says it was not chosen is not", pp.p6c.color_chosen, false);
	pp.p6c_apply_state({ view: "week", color_by: "pm", color_chosen: true }, { render: false });
	check("and one that says it was is", [pp.p6c.color_by, pp.p6c.color_chosen], ["pm", true]);
	// The Views menu ticks the saved view on screen, flag or no flag.
	delete pp.p6c_matching_view;
	pp.p6c.views = [{ name: "Old", data: { view: "week", project: "", pm: "", group: "", foreign: true, over_only: false, panel: true, color_by: "pm" } }];
	pp.view = "week";
	pp.project = "";
	pp.pm = "";
	pp.group = "";
	pp.show_foreign = true;
	pp.over_only = false;
	pp.panel_open = true;
	check("a view saved without color_chosen still matches the page showing it", (pp.p6c_matching_view() || {}).name, "Old");
});

test("Both planners: Split stays in the right-click menu, and Move by is a field of the Move… panel", () => {
	const source = fs.readFileSync(PP_JS, "utf8");
	check("Split is in the right-click menu", source.includes('multi ? __("Split across days…") : __("Split in two…")'), true);
	[[PP_JS, "pp"], [MP_JS, "mp"]].forEach(([file, name]) => {
		const code = fs.readFileSync(file, "utf8");
		check(`${name}: the selection bar no longer draws the Move by switch`, code.includes("p6e_bar_switch"), false);
	});
	const prefs = {};
	const pages = [
		[projectPlanner().pp, "Move by (working days)", "p6e_move_label"],
		[maintenancePlanner().mp, "Move by (working days)", "p6e_move_label"],
	];
	pages.forEach(([page, label], index) => {
		const name = index ? "mp" : "pp";
		page.load_pref = (key, fallback) => (key in prefs ? prefs[key] : fallback);
		page.save_pref = (key, value) => {
			prefs[key] = value;
		};
		page.p6b_render_bar = () => null;
		page.p6b_selected_cards = () => [{ name: "T1", key: "T1" }];
		let opts = null;
		const sets = [];
		const days = { df: {}, set_label: (l) => (days.label = l), set_description: (d) => (days.description = d) };
		const values = { unit: "Calendar days" };
		page.p6b_panel = (o) => {
			opts = o;
			return {
				set_primary_action: () => null,
				show: () => null,
				get_value: (field) => values[field],
				set_value: (field, value) => sets.push([field, value]),
				fields_dict: { days },
			};
		};
		page.p6b_move_panel();
		const unit = opts.fields.find((field) => field.fieldname === "unit");
		check(`${name}: a Move by field, calendar days to begin with`, [unit.fieldtype, unit.label, unit.options, unit.default], ["Select", "Move by", "Calendar days\nWorking days", "Calendar days"]);
		check(`${name}: the number field follows it`, opts.fields.map((field) => field.fieldname), ["unit", "days"]);
		values.unit = "Working days";
		unit.change();
		check(`${name}: choosing working days is remembered on the device and re-words the number`, [page.p6e_unit(), days.label, sets], ["working", label, [["days", 5]]]);
		unit.change();
		check(`${name}: choosing the same again changes nothing`, sets.length, 1);
		values.unit = "Calendar days";
		page.p6e_unit_value = "calendar";
	});
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

test("Project Planner: the hold's strip line shows only while Hold all my changes is on; Publish and Discard are the existing ones", () => {
	const { pp } = projectPlanner();
	const calls = record(pp, ["publish_drafts", "discard_drafts"]);
	pp.data.drafts = { count: 3, stale: [] };
	pp.render_draft_bar();
	// P4 draws a line per project with drafts too (scripts/test_planner_autoshift.js); none here.
	check("the hold off: hidden and empty", [pp.$draft_bar.nodes[0].style.display, pp.$draft_bar.text()], ["none", ""]);
	pp.draft_on = true;
	pp.render_draft_bar();
	const bar = pp.$draft_bar.nodes[0];
	check("the hold on: shown under the toolbar", [bar.style.display, bar.previousSibling === undefined, bar.parentNode.childNodes.indexOf(bar) === bar.parentNode.childNodes.indexOf(pp.$toolbar.nodes[0]) + 1], ["", true, true]);
	check("it says so, with the count", bar.querySelector(".pp-draft-text").textContent, "Holding your changes: 3 not published yet");
	const buttons = bar.querySelectorAll("button");
	check("Publish and Discard", buttons.map((b) => b.textContent), ["Publish", "Discard"]);
	buttons[0].click();
	buttons[1].click();
	check("each calls the existing function", calls.map((c) => c[0]), ["publish_drafts", "discard_drafts"]);
	pp.data.drafts = { count: 0, stale: [] };
	pp.render_draft_bar();
	check("nothing drafted yet: it says so, no buttons", [bar.querySelector(".pp-draft-text").textContent, bar.querySelectorAll("button").length], ["Holding your changes: nothing held yet. They stay yours until you publish them.", 0]);
});

test("Project Planner: Capacity is the Crew address with ?capacity=, and the old addresses are redirects", () => {
	const { pp, route, frappe } = projectPlanner();
	route.now = ["project-planner", "crew", "2026-10-01"];
	frappe.route_options = { capacity: "1" };
	check("its route", pp.route_target(), { view: "capacity", anchor: "2026-10-01", capacity: "1" });
	frappe.route_options = null;
	check("without the flag it is the Board", pp.route_target(), { view: "crew", anchor: "2026-10-01" });
	route.now = ["project-planner", "utilization", "2026-10-01"];
	check("the old Utilization address redirects, asking for the table", pp.route_target(), { view: "capacity", anchor: "2026-10-01", capacity_redirect: true, capacity: "utilization" });
	route.now = ["project-planner", "heatmap", "2026-10-12"];
	check("the old Heatmap address redirects", pp.route_target(), { view: "capacity", anchor: "2026-10-12", capacity_redirect: true, capacity: "1" });
	route.now = ["project-planner", "crew", "2026-10-01"];
	frappe.route_options = { capacity: "1" };
	const steps = [];
	pp.p6c_before_route = () => false;
	pp.take_route_filters = () => steps.push("filters");
	pp.set_mode = (mode) => steps.push(["mode", mode]);
	pp.load_capacity = () => steps.push("load");
	pp.handle_route();
	check("landing: the filters, the mode, the load", steps, ["filters", ["mode", "capacity"], "load"]);
	check("the view", [pp.view, pp.anchor], ["capacity", "2026-10-01"]);
	check("the flag is taken off route_options (it is the address's, like the filters)", Object.prototype.hasOwnProperty.call(frappe.route_options || {}, "capacity"), false);
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

// A Capacity page with the pieces render_utilization draws into, and a kit whose quiet call is
// recorded and answers as told.
function utilizationPage(answer, access) {
	const env = projectPlanner();
	const { pp, $, frappe, ctx } = env;
	access = access || {};
	frappe.boot.allowed_reports = access.reports === undefined ? { "Crew Utilization": {} } : access.reports;
	frappe.boot.user = { can_get_report: access.task === false ? [] : ["Task"] };
	frappe.model = { can_get_report: (dt) => frappe.boot.user.can_get_report.indexOf(dt) !== -1 };
	const asked = [];
	const quiet_args = [];
	const dialogs = [];
	frappe.call = (opts) => (asked.push(["frappe.call", opts.method]), Promise.resolve({}));
	frappe.msgprint = (m) => dialogs.push(m);
	ctx.window.planner_kit = {
		quiet_call: (method, args) => (asked.push(["quiet", method, args.report_name]), quiet_args.push(args), answer()),
		state_html: (kind, opts) => `<div class="sf-state sf-state--${kind}"><p class="sf-state-title">${opts.title}</p></div>`,
	};
	pp.view = "capacity";
	pp.anchor = "2026-10-01";
	pp.request = 0;
	pp.p6a_require = (fn) => Promise.resolve().then(fn);
	pp.range = () => ({ start: { format: () => "2026-10-01" }, end: { format: () => "2026-10-31" } });
	pp.title = () => "October 2026";
	pp.take_title_focus = () => null;
	pp.$title = $("<span></span>");
	pp.$view_buttons = { week: $("<button></button>"), crew: $("<button></button>") };
	pp.$body = $("<div></div>");
	pp.$grid_wrap = $("<div></div>");
	pp.p6a_route = (fn) => fn();
	return Object.assign(env, { asked, dialogs, quiet_args });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("Project Planner: Capacity's Utilization table answers to the report's access", async () => {
	const ok = utilizationPage(() => Promise.resolve({ result: [] }));
	check("both gates pass: offered", ok.pp.util_allowed(), true);
	const no_task = utilizationPage(() => Promise.resolve({}), { task: false });
	check("no report permission on Task: not offered", no_task.pp.util_allowed(), false);
	const no_report = utilizationPage(() => Promise.resolve({}), { reports: { "Other Report": {} } });
	check("the report not among the user's: not offered", no_report.pp.util_allowed(), false);
	// Someone the report is not for sees the heatmap alone: no section, and nothing asked of the server.
	await no_report.pp.load_utilization(0);
	await settle();
	check("no access: the table's section is hidden, no panel", [no_report.pp.$cap_util.nodes[0].style.display, no_report.pp.$cap_util.nodes[0].querySelector(".pp-util-denied")], ["none", null]);
	check("and nothing asked of the server, no dialog", [no_report.asked, no_report.dialogs], [[], []]);
	// Arrived by the old Utilization address anyway: the no-access panel says why it is not there.
	no_report.pp.capacity_asked = "utilization";
	await no_report.pp.load_utilization(0);
	await settle();
	const text = no_report.pp.$cap_util.nodes[0].querySelector(".pp-util-denied");
	check("arrived by the old address: the no-access panel", !!text && text.innerHTML.includes("You don't have access to the Crew Utilization report."), true);
	check("the kit's no-access markup", !!text && text.innerHTML.includes("sf-state--no-access"), true);
	check("still nothing asked of the server, no dialog", [no_report.asked, no_report.dialogs], [[], []]);
	await ok.pp.load_utilization(0);
	await settle();
	check("someone the report lets in: asked through the quiet call, the section shown", [ok.asked, ok.pp.$cap_util.nodes[0].style.display], [[["quiet", "frappe.desk.query_report.run", "Crew Utilization"]], ""]);
});

test("Project Planner: a refusal from the server is a panel, not frappe's red dialog", async () => {
	const page = utilizationPage(() => Promise.reject({ status: 403, exc_type: "PermissionError", message: "No" }));
	await page.pp.load_utilization(0);
	await settle();
	check("asked through the kit's quiet call only", page.asked, [["quiet", "frappe.desk.query_report.run", "Crew Utilization"]]);
	check("no dialog", page.dialogs, []);
	check("the no-access panel", !!page.pp.$cap_util.nodes[0].querySelector(".pp-util-denied"), true);
	const broken = utilizationPage(() => Promise.reject({ status: 0, exc_type: "", message: "offline" }));
	await broken.pp.load_utilization(0);
	await settle();
	check(
		"any other failure says it could not be loaded",
		broken.pp.$cap_util.nodes[0].textContent.includes("Utilization could not be loaded"),
		true
	);
});

test("Project Planner: Utilization's table keeps every column and flags Planned % only", async () => {
	const rows = [
		{ resource: "R1", label: "Austin", group: "Field", period: "Week of Sep 28", capacity: 24, booked: 24.1, actual: 30, utilization: 100.4, actual_pct: 125 },
		{ resource: "R1", label: "Austin", group: "Field", period: "Week of Oct 5", capacity: 40, booked: 10, actual: 8, utilization: 25, actual_pct: 20 },
	];
	const page = utilizationPage(() => Promise.resolve({ result: rows }));
	await page.pp.load_utilization(0);
	await settle();
	const html = page.pp.$cap_util.nodes[0].querySelector(".pp-util-scroll").innerHTML;
	check("Planned % past 100 (raw 100.4) is Over", html.includes('<span class="pp-chip pp-red">100% · Over</span>'), true);
	check("Actual % at 125 is not flagged", [html.includes("125%"), html.includes("125% · Over")], [true, false]);
	check("every column the view had", ["Person", "Week", "Capacity", "Booked", "Actual (kiosk)", "Planned %", "Actual %"].every((name) => html.includes(`>${name}</th>`)), true);
	check("and every figure of a row: capacity, booked and clocked hours", ["24h", "24.1h", "30h"].every((text) => html.includes(text)), true);
	const note = page.pp.$cap_util.nodes[0].querySelector(".pp-util-note");
	check("a note explains Planned %, Over and Near full", !!note && note.textContent.includes("Near full 75% or more"), true);
	check("the heading and the way on to the full report", page.pp.$cap_util.nodes[0].querySelector(".pp-util-head").textContent.includes("Open the full report"), true);
});

test("Project Planner: on Capacity, Ctrl+Z is not the planner's and + Add has nothing to offer", () => {
	const { pp } = projectPlanner();
	const undone = [];
	pp.undo = () => undone.push(1);
	pp.view = "capacity";
	check("Ctrl+Z on Capacity: not taken, nothing undone", [pp.p6b_run_key("undo"), undone.length], [false, 0]);
	pp.view = "week";
	check("Ctrl+Z on the week still undoes", [pp.p6b_run_key("undo"), undone.length], [true, 1]);
	pp.view = "capacity";
	pp.data = null;
	pp.tb_render();
	const off = (name) => pp.tb.$menus[name].hasClass("pp-tb-off");
	check("+ Add has nothing there and is hidden; More stays (My week, the crew sheet)", [off("add"), off("more")], [true, false]);
	pp.view = "week";
	pp.data = { can_edit: true, can_schedule: true, tasks: [], unscheduled: [], projects: [] };
	pp.tb_render();
	check("back on the week, both", [off("add"), off("more")], [false, false]);
});

test("Project Planner: the view buttons are tabs, and the tab on screen is the selected one", () => {
	const { pp } = projectPlanner();
	const tabs = [];
	pp.$seg = pp.$toolbar;
	pp.tb.kit = null;
	pp.tb.tabs = { sync: (view) => tabs.push(view) };
	pp.view = "crew";
	pp.tb_render();
	pp.view = "capacity";
	pp.tb_render();
	check("every redraw hands the screen's tab to the tablist (Capacity sits under Crew)", tabs, ["crew", "crew"]);
	const source = fs.readFileSync(PP_JS, "utf8");
	check("each button names its view for it", source.includes('.attr("data-tb-tab", view)'), true);
	// P5: the tab of the view on screen (week and month are the Calendar tab).
	check("the kit's tablist is wired when it loads", source.includes('kit.toolbar.tabs(this.$seg[0], { label: __("Planner views"), selected: this.ppl_tab() })'), true);
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
	check("its sections: the same ones the menus have", sections.map((s) => s.title), ["Alerts", "Add", "Display", "Views", "Tools", "Print", "This page"]);
	check("the chips are in it", sections[0].items.map((i) => [i.label, i.tone]), [["3 Conflicts", "danger"], ["1 Running over", "warn"], ["2 Overdue", "danger"]]);
	check("+ Add is in it", sections[1].items.map((i) => i.label), ["Quick add task…", "Build out tasks…", "Day note…"]);
	check("a switch keeps the sheet open; an action does not", [find(sections[4].items, "Hold all my changes").keep_open, find(sections[4].items, "Copy week…").keep_open], [true, false]);
	check("the help and refresh, which the phone's bar has no room for", sections[6].items.map((i) => i.label), ["How to read the planner", "Refresh"]);
	// Everything More offers, the sheet offers: nothing is reachable only on a big screen.
	const menu = pp.tb_items("more").filter((item) => item.label && !item.info).map((item) => item.label).sort();
	const sheet = sections.slice(2, 6).flatMap((s) => s.items).filter((item) => item.label && !item.info).map((item) => item.label).sort();
	check("every More item is in the sheet", sheet, menu);
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
	["The visit-kind legend row (P10: in \"?\" only)", "help", "Visit cards", null],
	["Draft mode", "tools", "Draft mode", "set_draft_mode"],
	["Tap to move", "tools", "Tap to move", "p6c_set_tap"],
	["Add visit", "add", "Add visit…", "p6e_add_visit"],
	["Day note", "add", "Day note…", "p6d_note_form"],
	["Block time", "tools", "Block time…", "p6d_block_form"],
	["Conflicts", "tools", "Conflicts…", "p6d_open_conflicts"],
	["Undo", "tools", "Undo", "undo"],
	["Print", "print", "Print this view", "p6c_print"],
	["Day Board (the page head keeps its button)", "print", "Day Board", null],
	["Overdue (new chip)", "chip", "overdue", "tb_open_overdue"],
];

test("Maintenance Planner: nothing in the old toolbar is lost, and + Add and More are all that is left of the menus", () => {
	const env = maintenancePlanner();
	const { mp } = env;
	const source = fs.readFileSync(MP_JS, "utf8");
	const calls = record(mp, [
		"p6c_save_as",
		"p6c_set_color",
		"set_show_projected",
		"set_show_project_work",
		"set_draft_mode",
		"p6c_set_tap",
		"p6e_add_visit",
		"p6d_note_form",
		"p6d_block_form",
		"p6d_open_conflicts",
		"undo",
		"p6c_print",
		"tb_open_overdue",
	]);
	mp.p6d_default_date = () => "2026-10-14";
	const more = mp.tb_items("more");
	MP_OLD_TOOLBAR.forEach(([what, where, label, method]) => {
		if (where === "bar") {
			check(`${what}: still in the bar`, source.includes(label), true);
			return;
		}
		if (where === "help") {
			// The "?" help holds it, and More ▸ Display does not (P10, section 4.9).
			mp.p6c_legend_sections = () => [];
			mp.p6d_legend_sections = () => [];
			mp.p6a_tip_section = () => ({ title: "Tips", items: [] });
			const help = mp.legend_sections().find((entry) => entry.title === label);
			check(`${what}: a section of the help`, !!help, true);
			check(`${what}: with every kind of visit`, help ? help.items.length : 0, 8);
			check(`${what}: and none in Display`, section(mp, "display").some((item) => item.info), false);
			return;
		}
		calls.length = 0;
		if (where === "chip") {
			const chip = mp.tb_alerts().find((alert) => alert.key === label);
			check(`${what}: a chip`, !!chip, true);
			if (chip) chip.open();
		} else {
			const item = find(section(mp, where), label);
			check(`${what}: in ${where}`, !!item, true);
			check(`${what}: in the menu's list`, !!find(where === "add" ? mp.tb_items("add") : more, label), true);
			if (item && item.on_click) item.on_click();
		}
		check(`${what}: reaches ${method}`, calls.length ? calls[0][0] : null, method);
	});
	check("the Day Board item goes to the Day Board", env.route.set, [["maintenance-day-board"]]);
	check("two menus", mp.tb_menu_list().map((m) => m[1]), ["Add", "More"]);
	check("their buttons read + Add and More", mp.tb_menu_list().map((m) => m[3]), ["+ Add", "More"]);
	check("More's sections, in order", more.filter((item) => item.section).map((item) => item.heading), ["Display", "Views", "Tools", "Print"]);
	check("tips are the help's first section", source.includes("this.p6a_tip_section(),\n\t\t\t...this.p6c_legend_sections(),"), true);
	check("the legend row is gone from under the toolbar", source.includes('class="mp-legend"'), false);
});

test("Maintenance Planner: switches, kinds, chips and the draft strip", () => {
	const { mp } = maintenancePlanner();
	const display = section(mp, "display");
	check("Projected visits ticked, Project work not", [find(display, "Projected visits").checked, find(display, "Project work").checked], [true, false]);
	check("Project work as cards is a switch of its own, off until ticked", find(display, "Project work as cards").checked, false);
	check("Display holds no key of the visit kinds any more (they are in the help)", display.filter((i) => i.info).map((i) => i.label), []);
	const edit = section(mp, "tools");
	check("Tap to move ticked, Draft mode not", [find(edit, "Tap to move").checked, find(edit, "Draft mode").checked], [true, false]);
	check("Undo can run", find(edit, "Undo").disabled, false);
	mp.data.can_create_visits = false;
	mp.data.can_schedule = false;
	check("Add visit and Block time only for those who may", [find(mp.tb_add_items(), "Add visit…"), find(section(mp, "tools"), "Block time…")], [null, null]);
	check("and a day note only for schedulers", find(mp.tb_add_items(), "Day note…"), null);
	check("with nobody to offer + Add to, it has nothing in it", mp.tb_add_items(), []);

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
	mp.tb.kit = { toolbar: Object.assign(kitHelpers(), { sheet: (o) => ((opts = o), { is_open: () => true, refresh: () => null }) }) };
	mp.tb_open_sheet();
	const slot = mp.$body.nodes[0].ownerDocument.createElement("div");
	opts.top(slot);
	check("the filter moved in", slot.contains(mp.$filters.nodes[0]), true);
	const sections = opts.sections();
	check("its sections: the same ones the menus have", sections.map((s) => s.title), ["Alerts", "Add", "Display", "Views", "Tools", "Print", "This page"]);
	check("+ Add is in it", sections[1].items.map((i) => i.label), ["Add visit…", "Day note…"]);
	check("the Day Board is in Print, where the menu has it", sections[5].items.map((i) => i.label), ["Print this view", "Day Board"]);
	check("the help and refresh", sections[6].items.map((i) => i.label), ["How to read the planner", "Refresh"]);
	const menu = mp.tb_items("more").filter((item) => item.label && !item.info).map((item) => item.label).sort();
	const sheet = sections.slice(2, 6).flatMap((s) => s.items).filter((item) => item.label && !item.info).map((item) => item.label).sort();
	check("every More item is in the sheet", sheet, menu);
	opts.on_close("user");
	check("closed: the filter is back before the chips", mp.$filters.nodes[0].parentNode.childNodes.indexOf(mp.$filters.nodes[0]) + 1, mp.$alerts.nodes[0].parentNode.childNodes.indexOf(mp.$alerts.nodes[0]));
});

test("Maintenance Planner: the view buttons are tabs", () => {
	const { mp } = maintenancePlanner();
	const tabs = [];
	mp.tb.tabs = { sync: (view) => tabs.push(view) };
	mp.view = "crew";
	mp.tb_render();
	check("a redraw hands the screen's view to the tablist", tabs, ["crew"]);
	const source = fs.readFileSync(MP_JS, "utf8");
	check("each button names its view", source.includes('.attr("data-tb-tab", view)'), true);
	check("the kit's tablist is wired when it loads", source.includes('kit.toolbar.tabs(this.$seg[0], { label: __("Planner views"), selected: this.view })'), true);
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
			pp.tb_items("more");
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
			mp.tb_items("more");
		} catch (e) {
			error = e && e.message;
		}
		check(`Color by ${mode}: nothing thrown`, error, null);
	});
});

// ------------------------------------------------------------------ Crew board in days + Capacity (P7)

// A Project Planner with the crew board's data: one task, T1, Mon to Wed, booked 8h a day on R1.
function crewPage(extra) {
	const env = projectPlanner();
	const { pp } = env;
	const week = ["2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"];
	const booking = (ref) => ({ ref, kind: "task", label: ref, hours: 8, slot: null });
	const T1 = { name: "T1", subject: "Install pumps", project: "PRJ-1", start: "2026-10-12", end: "2026-10-14", movable: true, crew: [] };
	pp.by_task = { T1 };
	pp.data = Object.assign(
		{
			can_edit: true,
			tasks: [T1],
			unscheduled: [],
			days: { R1: { "2026-10-12": { bookings: [booking("T1")] }, "2026-10-13": { bookings: [booking("T1")] }, "2026-10-14": { bookings: [booking("T1")] } } },
		},
		extra || {}
	);
	pp.task_visible = () => true;
	["dependency_lines", "gap_lines", "p4_card_chips", "p4_tip_lines"].forEach((name) => (pp[name] = () => []));
	pp.crew_hours = false;
	return Object.assign(env, { week, T1, booking });
}

test("Project Planner: the crew board counts a task in days, and joins a task's days into one bar", () => {
	const { pp, week } = crewPage();
	const booked_on = pp.crew_booked_days([{ name: "R1" }], week);
	check("each person's booked days of each task", Array.from(booked_on.R1.T1), ["2026-10-12", "2026-10-13", "2026-10-14"]);
	const ctx = (ymd) => pp.crew_span_ctx(booked_on, "R1", week, ymd, pp.data.days.R1[ymd].bookings[0]);
	check("the first day: joined only to the next", [ctx("2026-10-12").prev, ctx("2026-10-12").next], [false, true]);
	check("the middle day: joined both ways", [ctx("2026-10-13").prev, ctx("2026-10-13").next], [true, true]);
	check("the last day: joined only to the one before", [ctx("2026-10-14").prev, ctx("2026-10-14").next], [true, false]);
	check("each chip says which day of how many", ["2026-10-12", "2026-10-13", "2026-10-14"].map((ymd) => pp.crew_days_text(ctx(ymd).count)), ["Day 1 of 3", "Day 2 of 3", "Day 3 of 3"]);
	const html = (ymd) => pp.booking_html("R1", ymd, pp.data.days.R1[ymd].bookings[0], ctx(ymd));
	const sub_text = (ymd) => (/<div class="pp-card-sub">([^<]*)<\/div>/.exec(html(ymd)) || [])[1];
	check("a chip is labeled in days, with no hours", sub_text("2026-10-13"), "Day 2 of 3");
	check("and carries its place in the bar", [html("2026-10-12").includes("pp-span-next"), html("2026-10-12").includes("pp-span-prev"), html("2026-10-14").includes("pp-span-prev")], [true, false, true]);
	check("it is still the chip that is dragged: task, day and person stay on it", ['data-task="T1"', 'data-date="2026-10-13"', 'data-resource="R1"'].every((attr) => html("2026-10-13").includes(attr)), true);
	pp.crew_hours = true;
	check("Show hours adds the hours beside the days", sub_text("2026-10-13"), "Day 2 of 3 · 8h");
	pp.crew_hours = false;
	check("a task of one day reads 1 day", pp.crew_days_text({ n: 1, total: 1 }), "1 day");
	// A block or someone else's visit is no part of a bar.
	check("a personal block is no task: no bar", pp.crew_span_ctx(booked_on, "R1", week, "2026-10-12", { ref: "B1", kind: "block" }), null);
	check("nor is a drive", pp.crew_span_ctx(booked_on, "R1", week, "2026-10-12", { ref: "T1", kind: "drive" }), null);
	// The tooltip keeps the hours, whatever the label says.
	pp.crew_hours = false;
	check("the hours stay in the tooltip", /title="[^"]*8h[^"]*"/.test(html("2026-10-13")), true);
});

test("Project Planner: Day n of N is the task's working days, the same count as the calendar card's days", () => {
	const { pp, T1 } = crewPage();
	// Friday of the week before to Wednesday: four working days, and Monday is the second.
	T1.start = "2026-10-09";
	T1.end = "2026-10-14";
	check("Monday: day 2 of 4 (Friday, Monday, Tuesday, Wednesday)", pp.crew_day_count(T1, "2026-10-12"), { n: 2, total: 4 });
	check("Tuesday: day 3 of 4", pp.crew_day_count(T1, "2026-10-13"), { n: 3, total: 4 });
	check("the card calls the same task 4 days", pp.duration_text(T1), "4 days");
	// A person who works Monday to Wednesday is booked on three chips of a five-day task: the label is the task's.
	T1.start = "2026-10-12";
	T1.end = "2026-10-16";
	check("a chip of a five-day task says of 5, whatever days this person works", ["2026-10-12", "2026-10-14"].map((ymd) => pp.crew_day_count(T1, ymd)), [{ n: 1, total: 5 }, { n: 3, total: 5 }]);
	check("and the card says 5 days", pp.duration_text(T1), "5 days");
	// A task with no end is one day.
	T1.end = null;
	check("no end: a day", pp.crew_day_count(T1, "2026-10-12"), { n: 1, total: 1 });
	// A booking on a weekend counts as the Friday before it, never past the end.
	T1.start = "2026-10-12";
	T1.end = "2026-10-16";
	check("never past the task's length", pp.crew_day_count(T1, "2026-10-17"), { n: 5, total: 5 });
});

test("Project Planner: Capacity's hint is set on every load, and the selection bar leaves with the screen", async () => {
	const env = capacityPage();
	env.ctx.moment = richMoment;
	const { pp, route, $ } = env;
	pp.view = "capacity";
	pp.range = () => ({ start: { format: () => "2026-10-11" }, end: { format: () => "2026-12-05" } });
	pp.$hint = $("<div></div>");
	pp.$hint.text("Drag a task card to another day to move it.");
	const loading = pp.load_capacity();
	check("the Board's or Gantt's drag help is gone the moment Capacity starts loading", pp.$hint.nodes[0].textContent.includes("Each square is one person's week"), true);
	await loading;
	// The bar stays on a calendar screen and goes on Capacity, by the page's own view (handle_route has run).
	const hidden = [];
	pp.$p6b_bar = { hide: () => hidden.push(1) };
	pp.p6b = { resize: null };
	pp.p6b_close_results = () => null;
	route.now = ["project-planner", "crew", "2026-10-12"];
	pp.view = "crew";
	pp.p6b_route_changed();
	check("on the Board the bar is left alone", hidden.length, 0);
	pp.view = "capacity";
	pp.p6b_route_changed();
	check("on Capacity (?capacity= already taken off route_options) it is hidden", hidden.length, 1);
});

test("Project Planner: the whole crew board draws a bar of days along the person's row", () => {
	const { pp, week, ctx, $ } = crewPage();
	ctx.moment = richMoment;
	pp.range_days = () => week;
	pp.today = () => "2026-10-14";
	pp.resources = () => [{ name: "R1", label: "Austin", group: "Field" }];
	pp.resource_color = () => "#123456";
	pp.avail_state = () => ({ cls: "pp-green", text: "Full", ratio: 1, soft: 0, soft_ratio: 0 });
	pp.day_tip = () => "";
	pp.drive_html = () => "";
	pp.p6d_crew_notes_row = () => "";
	pp.p4_equipment_rows = () => "";
	pp.bind_week_links = () => null;
	pp.$grid_wrap = $("<div></div>");
	pp.render_crew();
	const html = pp.$grid_wrap.nodes[0].querySelector(".pp-crew").innerHTML;
	const chips = html.match(/<div class="pp-card(?: [^"]*)?"/g) || [];
	check("one chip a booked day, three in all", chips.length, 3);
	check("joined: first to the next, middle both, last to the one before", chips.map((c) => [c.includes("pp-span-prev"), c.includes("pp-span-next")]), [[false, true], [true, true], [true, false]]);
	check("every one says its day", ["Day 1 of 3", "Day 2 of 3", "Day 3 of 3"].every((text) => html.includes(`>${text}</div>`)), true);
	check("the cells are still the drop targets, a day and a person", html.includes('data-date="2026-10-13" data-resource="R1"'), true);
	check("and the day cell still says Full", html.includes("Full"), true);
});

test("Project Planner: Show hours is the person's, kept in this browser, and redraws the board", () => {
	const { pp, ctx } = crewPage();
	const saved = [];
	pp.save_pref = (key, value) => saved.push([key, value]);
	pp.render_crew_bar = () => saved.push("bar");
	let drawn = 0;
	pp.render = () => (drawn += 1);
	pp.view = "crew";
	pp.set_crew_hours(true);
	check("on: saved and the board redrawn, the header left alone (it keeps the keyboard's focus)", [pp.crew_hours, saved, drawn], [true, [["ee_project_planner_crew_hours", "1"]], 1]);
	pp.set_crew_hours(false);
	check("and off", [pp.crew_hours, saved[1]], [false, ["ee_project_planner_crew_hours", "0"]]);
	check("it starts off, in days", vm.runInContext("PP.prefs_crew_hours", ctx), "ee_project_planner_crew_hours");
});

test("Project Planner: Capacity's header switches Board and Capacity as routes of the Crew tab", () => {
	const { pp, route, $ } = crewPage();
	const here = [];
	pp.$crewbar = $("<div></div>");
	pp.view = "crew";
	pp.go = (view, anchor) => here.push([view, anchor]);
	pp.render_crew_bar();
	const buttons = () => pp.$crewbar.nodes[0].querySelectorAll("button");
	check("on the Board: two screens, the Board pressed", [Array.from(buttons()).map((b) => [b.textContent, b.getAttribute("aria-pressed")])], [[["Board", "true"], ["Capacity", "false"]]]);
	check("and Show hours is on the Board only", pp.$crewbar.nodes[0].querySelectorAll("input").length, 1);
	buttons()[1].click();
	check("Capacity is a route (a history entry), not a toggle in place", here, [["capacity", "2026-10-14"]]);
	pp.view = "capacity";
	pp.render_crew_bar();
	check("on Capacity: the Capacity button pressed, no Show hours", [Array.from(buttons()).map((b) => b.getAttribute("aria-pressed")), pp.$crewbar.nodes[0].querySelectorAll("input").length], [["false", "true"], 0]);
	buttons()[0].click();
	check("the Board is a route too", here[1], ["crew", "2026-10-14"]);
	pp.view = "week";
	pp.render_crew_bar();
	check("the header is only on the Crew tab", pp.$crewbar.nodes[0].style.display, "none");
	check("Capacity sits under the Crew tab", [(pp.view = "capacity", pp.ppl_tab()), (pp.view = "week", pp.ppl_tab())], ["crew", "calendar"]);
});

// A moment with the little the heatmap draws with: parse, clone, add days, format.
function richMoment(value) {
	const date = typeof value === "string" ? new Date(`${value}T00:00:00Z`) : new Date(value);
	const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
	const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	const two = (n) => String(n).padStart(2, "0");
	const m = {
		isValid: () => !Number.isNaN(date.getTime()),
		clone: () => richMoment(date.getTime()),
		add: (n) => (date.setUTCDate(date.getUTCDate() + n), m),
		format: (fmt) =>
			fmt
				.replace("YYYY", date.getUTCFullYear())
				.replace("MM", two(date.getUTCMonth() + 1))
				.replace("DD", two(date.getUTCDate()))
				.replace("MMM", months[date.getUTCMonth()])
				.replace("ddd", names[date.getUTCDay()])
				.replace(/\bD\b/, date.getUTCDate()),
	};
	return m;
}

// The heatmap's answer for one person over two weeks, and the page around it.
function capacityPage(report) {
	const env = utilizationPage(report || (() => Promise.resolve({ result: [] })));
	const { pp, frappe } = env;
	pp.$crewbar = env.$("<div></div>");
	pp.$body = env.$("<div></div>");
	pp.group = "";
	pp.today = () => "2026-10-14";
	pp.resource_color = () => "#123456";
	pp.p6d_state = () => null;
	pp.p4_set_mode = () => null;
	const week = (start) => ({ start });
	frappe.call = (opts) => {
		env.asked.push(["frappe.call", opts.method, opts.args]);
		if (String(opts.method).endsWith("get_heatmap")) {
			return Promise.resolve({
				message: {
					weeks: [week("2026-10-11"), week("2026-10-18")],
					resources: [{ name: "R1", label: "Austin", group: "Field" }],
					cells: {
						R1: {
							"2026-10-11": { capacity: 40, booked: 44, soft_booked: 0, over_days: 1, off_days: 0, level: "red" },
							"2026-10-18": { capacity: 40, booked: 10, soft_booked: 4, over_days: 0, off_days: 0 },
						},
					},
				},
			});
		}
		return Promise.resolve({});
	};
	return env;
}

test("Project Planner: Capacity loads the heatmap and the Utilization table together, each into its own box", async () => {
	const rows = [{ resource: "R1", label: "Austin", group: "Field", period: "Week of Oct 11", capacity: 40, booked: 44, actual: 30, utilization: 110, actual_pct: 75 }];
	const env = capacityPage(() => Promise.resolve({ result: rows }));
	env.ctx.moment = richMoment;
	const { pp, asked } = env;
	pp.view = "capacity";
	pp.range = () => ({ start: { format: () => "2026-10-11" }, end: { format: () => "2026-12-05" } });
	// pp_ymd is the page's own: the moment stand-in's format is all it needs.
	await pp.load_capacity();
	await settle();
	const heat = pp.$cap_heat.nodes[0];
	const util = pp.$cap_util.nodes[0];
	check("the heatmap asked for eight weeks from the first of them", asked.find((a) => a[1] && a[1].endsWith("get_heatmap"))[2], { start: "2026-10-11", weeks: 8 });
	check(
		"the report asked for the same eight weeks, week by week",
		env.quiet_args.map((args) => JSON.parse(args.filters)),
		[{ from_date: "2026-10-11", to_date: "2026-12-05", granularity: "Week" }]
	);
	const grid = heat.querySelector(".pp-hm").innerHTML;
	check("both sections drew: the heatmap's grid and the table", [grid.includes("pp-hm-cell"), util.querySelector(".pp-util-scroll").innerHTML.includes('class="pp-util"')], [true, true]);
	check("every heatmap figure survives: the percentage and the hours of each week", [grid.includes("110%"), grid.includes("44h of 40h"), grid.includes("1 day over")], [true, true, true]);
	check("pencilled hours too", grid.includes("+4h pencil"), true);
	check("the heatmap's cell still opens its week", grid.includes('data-hm-week="2026-10-11"'), true);
	check("the table's Planned % is flagged Over", util.querySelector(".pp-util-scroll").innerHTML.includes("110% · Over"), true);
	check("both are in the one grid, the heatmap above the table", [pp.$grid_wrap.nodes[0].childNodes.length, pp.$grid_wrap.nodes[0].childNodes[0] === heat], [2, true]);
});

test("Project Planner: without the report's access Capacity is the heatmap alone, and never asks for it", async () => {
	const env = capacityPage(() => Promise.resolve({ result: [] }));
	env.ctx.moment = richMoment;
	env.frappe.boot.allowed_reports = { "Other Report": {} };
	const { pp, asked } = env;
	pp.view = "capacity";
	pp.range = () => ({ start: { format: () => "2026-10-11" }, end: { format: () => "2026-12-05" } });
	await pp.load_capacity();
	await settle();
	check("the heatmap is there", !!pp.$cap_heat.nodes[0].querySelector(".pp-hm"), true);
	check("the table's box is hidden and empty", [pp.$cap_util.nodes[0].style.display, pp.$cap_util.nodes[0].childNodes.length], ["none", 0]);
	check("the report was never asked", asked.some((a) => a[0] === "quiet"), false);
});

test("Project Planner: leaving Capacity drops its boxes, and Capacity is not a calendar view", () => {
	const { pp, $ } = capacityPage();
	pp.$cap_heat = $("<div></div>");
	pp.$cap_util = $("<div></div>");
	pp.$main = $("<div></div>");
	pp.$p6c_pill = null;
	pp.p6c_set_mode = () => null;
	pp.$toolbar = $("<div></div>");
	pp.$grid_wrap = $("<div></div>");
	pp.$route = $("<div></div>");
	pp.$panel = $("<div></div>");
	pp.view = "week";
	pp.set_mode("");
	check("a calendar screen takes the grid: the boxes are gone", [pp.$cap_heat, pp.$cap_util], [null, null]);
	check("Capacity is not one of the calendar views", [pp.tb_calendar(), (pp.view = "capacity", pp.tb_calendar())], [true, false]);
});

// A saved or last-used view that says heatmap (before P7) is Capacity.
test("Project Planner: the remembered tab and saved views that say heatmap open Capacity", () => {
	const env = projectPlanner();
	const { pp, route, frappe } = env;
	delete pp.p6c_matching_view;
	pp.p6c_pick = (state) => Object.assign({}, state);
	pp.p6c.restored = true;
	pp.p6c.loaded = true;
	const set = route.set;
	// The tab a bare address opens is this device's (P8): a device that remembered Heatmap or Capacity lands on
	// Capacity, which is the Crew path with the flag, replaced and never pushed.
	route.now = ["project-planner"];
	pp.p8_is_phone = () => false;
	pp.p8_tab = () => "heatmap";
	check("a bare address on a device that remembered the Heatmap: Capacity, replaced, never pushed", [pp.p8_landing(), frappe.route_flags && frappe.route_flags.replace_route], [true, true]);
	check("on the Crew path with the flag", set[0], ["project-planner", "crew", "2026-10-14", { capacity: "1" }]);
	pp.p8_tab = () => "capacity";
	frappe.route_flags = {};
	pp.p8_landing();
	check("a device that remembered Capacity does the same", set[1], ["project-planner", "crew", "2026-10-14", { capacity: "1" }]);
	pp.p8_tab = () => "crew";
	pp.p8_landing();
	check("the Board is a plain Crew route", set[2], ["project-planner", "crew", "2026-10-14"]);
	check("both screens are tabs a device remembers", vm.runInContext("PP8.tabs", env.ctx).filter((tab) => tab === "capacity" || tab === "heatmap"), ["heatmap", "capacity"]);
	// Picking a saved view that says heatmap.
	const went = [];
	pp.go = (view, anchor) => went.push([view, anchor]);
	pp.view = "week";
	pp.p6c_pick = (state) => Object.assign({}, state);
	pp.write_filter_url = () => null;
	pp.render = () => null;
	pp.p6c_apply_state({ view: "heatmap" }, { route: true });
	check("picking it from the week goes to Capacity", went, [["heatmap", "2026-10-14"]]);
	pp.view = "capacity";
	pp.p6c_apply_state({ view: "heatmap" }, { route: true });
	check("and while on Capacity it does not move", went.length, 1);
	// Capacity is saved as itself, and the old name still matches the saved view that holds it.
	pp.p6c_state = () => ({ view: "capacity", group: "" });
	pp.p6c.views = [{ name: "Old heat", data: { view: "heatmap", group: "" } }];
	check("a view saved as Heatmap is the one on screen when Capacity is", (pp.p6c_matching_view() || {}).name, "Old heat");
	check("saved views list it as Capacity", vm.runInContext("PP6C.view_labels", env.ctx), { week: "Week", month: "Month", crew: "Crew", heatmap: "Capacity", capacity: "Capacity", lookahead: "Lookahead" });
});

test("Project Planner: calendar cards say their length in days and people, with hours only when set", () => {
	const { pp } = projectPlanner();
	const label = (card) => pp.duration_text(card);
	const three = { start: "2026-10-14", end: "2026-10-16" };
	check("three days, two people", label({ ...three, crew: [{}, {}] }), "3 days · 2 people");
	check("one day, one person", label({ start: "2026-10-14", end: "2026-10-14", crew: [{}] }), "1 day · 1 person");
	check("no end date is one day", label({ start: "2026-10-14", end: "", crew: [] }), "1 day");
	check("Friday to Monday is two working days", label({ start: "2026-10-16", end: "2026-10-19", crew: [] }), "2 days");
	check("Thursday to Monday is three working days", label({ start: "2026-10-15", end: "2026-10-19", crew: [] }), "3 days");
	check("a weekend-only task still reads one day", label({ start: "2026-10-17", end: "2026-10-18", crew: [] }), "1 day");
	check("hours show after the people when set", label({ ...three, crew: [{}, {}], expected_time: 20 }), "3 days · 2 people · 20h");
	const slot = ["09:00", "11:00"];
	check("a time slot shows as a range", label({ start: "2026-10-14", end: "2026-10-14", crew: [], slot }), "1 day · 09:00–11:00");
	check("no dates, no label", label({ start: "", end: "", crew: [] }), "");
	const source = fs.readFileSync(PP_JS, "utf8");
	check("the calendar card and its quick look use the day label", (source.match(/this\.duration_text\(card\)/g) || []).length, 2);
});

// ------------------------------------------------------------------ Maintenance Planner simplify (P10)

// A moment with what the Maintenance Planner's calendar, crew view and Resources panel ask of it, on UTC
// dates: parse, clone, add and subtract days, day(), month(), date(), isAfter and format.
function miniMoment(value) {
	const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
	const longs = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
	const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
	const longMonths = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
	const two = (n) => String(n).padStart(2, "0");
	let date;
	if (value === undefined) date = new Date(Date.UTC(2026, 9, 21));
	else if (typeof value === "number") date = new Date(value);
	else date = new Date(`${value}T00:00:00Z`);
	const m = {
		_time: () => date.getTime(),
		isValid: () => !Number.isNaN(date.getTime()),
		clone: () => miniMoment(date.getTime()),
		add: (n) => (date.setUTCDate(date.getUTCDate() + n), m),
		subtract: (n) => m.add(-n),
		day: (n) => {
			if (n === undefined) return date.getUTCDay();
			date.setUTCDate(date.getUTCDate() + n - date.getUTCDay());
			return m;
		},
		month: () => date.getUTCMonth(),
		date: () => date.getUTCDate(),
		year: () => date.getUTCFullYear(),
		isAfter: (other) => date.getTime() > other._time(),
		format: (fmt) =>
			fmt.replace(/YYYY|MMMM|MMM|MM|dddd|ddd|DD|D/g, (token) => {
				if (token === "YYYY") return date.getUTCFullYear();
				if (token === "MMMM") return longMonths[date.getUTCMonth()];
				if (token === "MMM") return months[date.getUTCMonth()];
				if (token === "MM") return two(date.getUTCMonth() + 1);
				if (token === "dddd") return longs[date.getUTCDay()];
				if (token === "ddd") return names[date.getUTCDay()];
				if (token === "DD") return two(date.getUTCDate());
				return date.getUTCDate();
			}),
	};
	return m;
}

// The week of Sun Oct 18 to Sat Oct 24 2026 on a Maintenance Planner with two technicians and their project
// work: Austin is booked 8h Monday (a task and travel), has 3h free Tuesday and is off Thursday and Friday;
// Korben has a rental crew task all of Monday.
function simplifyPage(extra) {
	const env = maintenancePlanner();
	const { mp, $ } = env;
	env.ctx.moment = miniMoment;
	delete mp.booking_cards;
	const day = (capacity, booked, items) => ({ capacity, booked, free: Math.max(0, capacity - booked), items: items || [], blocks: [] });
	const bookings = {
		"austin@x.com": {
			"2026-10-18": day(0, 0),
			"2026-10-19": day(8, 8, [
				{ kind: "task", ref: "TASK-1", label: "Underground rough-in", project: "Riverwalk Plaza", hours: 6 },
				{ kind: "travel", ref: "TRIP-1", label: "Travel: Kapture Events", hours: 2 },
			]),
			"2026-10-20": day(8, 5, [{ kind: "task", ref: "TASK-2", label: "<img src=x onerror=1> Excavate", project: "Plaza", hours: 5 }]),
			"2026-10-21": day(8, 8),
			"2026-10-22": day(0, 0),
			"2026-10-23": day(0, 0),
			"2026-10-24": day(0, 0),
		},
		"korben@x.com": {
			"2026-10-19": day(8, 8, [{ kind: "rental", ref: "TASK-3", label: "Rental crew", hours: 8 }]),
			"2026-10-20": day(8, 0),
		},
	};
	const technicians = [
		{ user: "austin@x.com", name: "Austin Healey", enabled: true, resource: "R-A", group: "Field" },
		{ user: "korben@x.com", name: "Korben Dallas", enabled: true, resource: "R-K", group: "Field" },
	];
	Object.assign(mp, {
		view: "week",
		anchor: "2026-10-20",
		technician: "",
		show_project_work: true,
		show_project_cards: false,
		panel_open: false,
		tech_names: { "austin@x.com": "Austin Healey", "korben@x.com": "Korben Dallas" },
		tech_by_user: { "austin@x.com": technicians[0], "korben@x.com": technicians[1] },
		by_key: {},
		day_cards: {},
		$panel: $("<div></div>").appendTo(env.doc.body),
		$tray: $("<div></div>"),
		$grid_wrap: $("<div></div>"),
	});
	mp.data = Object.assign(mp.data, { today: "2026-10-19", technicians, bookings, visits: [], unscheduled: [], projected: [], moves: [], unscheduled_moves: [] });
	delete mp.tech_name;
	mp.save_pref = (k, v) => ((mp.prefs = mp.prefs || {})[k] = v);
	mp.render = () => ((mp.renders = (mp.renders || 0) + 1), null);
	Object.assign(mp, extra || {});
	return env;
}

const strips = (html) => (html.match(/class="mp-busy[ "]/g) || []).length;

test("Maintenance Planner: project work is a busy strip by default, cards when asked, and nothing when off", () => {
	const { mp } = simplifyPage();
	const booking = { kind: "booking", technician: "austin@x.com" };
	check("the default is the strip", [mp.project_work_mode(), mp.show_project_cards], ["strip", false]);
	check("a strip day: no booking is a card", mp.visible(booking), false);
	mp.show_project_cards = true;
	check("cards: the booking is a card again", [mp.project_work_mode(), mp.visible(booking)], ["cards", true]);
	mp.technician = "korben@x.com";
	check("and a filter on another technician hides it", mp.visible(booking), false);
	mp.technician = "__none__";
	check("Unassigned has no project work", mp.visible(booking), false);
	mp.technician = "";
	mp.show_project_work = false;
	check("Project work off: neither", [mp.project_work_mode(), mp.visible(booking), mp.p10_day_strips_html("2026-10-19")], ["off", false, ""]);
});

test("Maintenance Planner: a day's strips, one a technician, in the technicians' order", () => {
	const { mp } = simplifyPage();
	const html = mp.p10_day_strips_html("2026-10-19");
	check("two strips, Austin's first", [strips(html), html.indexOf("Austin") < html.indexOf("Korben")], [2, true]);
	check("the words: hours, and the kind when there is only one", [html.includes("Project: 8h"), html.includes("Rental: 8h")], [true, true]);
	check("with All technicians each strip names the person", [html.includes('class="mp-busy-name">Austin<'), html.includes('class="mp-busy-name">Korben<')], [true, true]);
	check("a strip is a button to the person's week: role, focus, the person and the day", [html.includes('role="button" tabindex="0"'), html.includes('data-busy-user="austin@x.com"'), html.includes('data-busy-date="2026-10-19"')], [true, true, true]);
	check("it lights up with the person's name", html.includes('data-pk-persons="austin%40x.com"'), true);
	check("hover lists the tasks", [html.includes("• Underground rough-in (Project, 6h, Riverwalk Plaza)"), html.includes("• Travel: Kapture Events (Travel, 2h)")], [true, true]);
	mp.technician = "austin@x.com";
	const one = mp.p10_day_strips_html("2026-10-19");
	check("a picked technician: their strip only, and no name (the column says who)", [strips(one), one.includes("mp-busy-name"), one.includes("Korben")], [1, false, false]);
	check("a day without project work has none", mp.p10_day_strips_html("2026-10-21"), "");
	mp.technician = "__none__";
	check("Unassigned has none", mp.p10_day_strips_html("2026-10-19"), "");
	mp.technician = "";
	mp.show_project_cards = true;
	check("in cards mode the day has no strips (the cards are drawn)", mp.p10_day_strips_html("2026-10-19"), "");
});

test("Maintenance Planner: a strip escapes what people typed, and pencilled hours are said and drawn apart", () => {
	const { mp, ctx } = simplifyPage();
	const html = mp.p10_day_strips_html("2026-10-20");
	check("a task name cannot break out of the tooltip", [html.includes("<img"), html.includes("&lt;img src=x onerror=1&gt; Excavate")], [false, true]);
	const pure = vm.runInContext("MP10_PURE", ctx);
	const text = (items) => mp.p10_busy_text(pure.busy(items));
	check("firm and pencilled hours", text([{ kind: "task", hours: 6 }, { kind: "task", hours: 2, tentative: true }]), "Project: 6h + 2h pencil");
	check("only pencil", text([{ kind: "task", hours: 2, tentative: true }]), "Project: 2h pencil");
	check("half hours keep their fraction", text([{ kind: "travel", hours: 2.5 }]), "Travel: 2.5h");
	check("a project task among them makes it Project", [text([{ kind: "task", hours: 4 }, { kind: "travel", hours: 4 }]), text([{ kind: "rental", hours: 3 }, { kind: "task", hours: 5 }])], ["Project: 8h", "Project: 8h"]);
	check("rental and travel with no project task are labeled by what they are, whatever order the engine listed them", [text([{ kind: "rental", hours: 4 }, { kind: "travel", hours: 4 }]), text([{ kind: "travel", hours: 4 }, { kind: "rental", hours: 4 }])], ["Rental + Travel: 8h", "Rental + Travel: 8h"]);
	check("a weekend travel day books no hours and is just Travel", [text([{ kind: "travel", hours: 0 }]), text([{ kind: "travel", hours: 0 }, { kind: "travel", hours: 0 }])], ["Travel", "Travel: 2 items"]);
	check("no hours at all counts the items", text([{ kind: "task" }, { kind: "task" }]), "Project: 2 items");
	check("and says what they are when mixed", text([{ kind: "task" }, { kind: "travel" }]), "Project + Travel: 2 items");
	const pencil = simplifyPage({ show_project_cards: false });
	pencil.mp.data.bookings["austin@x.com"]["2026-10-20"].items = [{ kind: "task", ref: "T", label: "Soft", hours: 4, tentative: true }];
	const soft = pencil.mp.p10_day_strips_html("2026-10-20");
	check("an all-pencil strip is hatched", soft.includes('class="mp-busy mp-busy-pencil"'), true);
	check("and the tooltip says pencil", soft.includes("pencil)"), true);
});

test("Maintenance Planner: a click on a strip opens the person's week, the day selected; keys do too", () => {
	const { mp, doc } = simplifyPage();
	const opened = [];
	mp.p6a = { kit: {} };
	mp.p6a_open_person = (user, day) => opened.push([user, day]);
	mp.click_blocked_until = 0;
	mp.$body = { 0: doc.createElement("div") };
	mp.init_p10();
	const strip = doc.createElement("div");
	strip.className = "mp-busy";
	strip.setAttribute("data-busy-user", "austin@x.com");
	strip.setAttribute("data-busy-date", "2026-10-19");
	mp.$body[0].appendChild(strip);
	strip.click();
	check("a click", opened, [["austin@x.com", "2026-10-19"]]);
	const enter = key(strip, "Enter");
	check("Enter does the same and is not left to the browser", [opened.length, enter.defaultPrevented], [2, true]);
	key(strip, "a");
	check("any other key does nothing", opened.length, 2);
	mp.click_blocked_until = Date.now() + 1000;
	strip.click();
	check("a click that ends a drag is not a click", opened.length, 2);
	// Before the kit has loaded, a day with one task opens the task; a longer list waits for the kit.
	mp.click_blocked_until = 0;
	mp.p6a = { kit: null };
	const routes = [];
	mp.open_card = (card) => routes.push([card.kind, card.booking_kind, card.ref]);
	mp.p10_open_busy(strip);
	check("two tasks wait for the kit", routes, []);
	strip.setAttribute("data-busy-date", "2026-10-20");
	mp.p10_open_busy(strip);
	check("one task opens itself", routes, [["booking", "task", "TASK-2"]]);
});

test("Maintenance Planner: the calendar draws strips in the days, cards in cards mode", () => {
	const env = simplifyPage();
	const { mp, $ } = env;
	mp.range = () => ({ start: miniMoment("2026-10-18"), end: miniMoment("2026-10-24") });
	mp.p6d_day_head = () => null;
	mp.card_html = (card) => `<card ${card.key}>`;
	mp.render_tray = () => null;
	const days = () => mp.$grid_wrap.nodes[0].querySelectorAll(".mp-day").map((node) => [node.getAttribute("data-date"), node.textContent]);
	const cards = mp.all_cards();
	check("the week's bookings are cards in the data either way", cards.filter((card) => card.kind === "booking").length, 4);
	mp.render_calendar(cards);
	const monday = days().find(([ymd]) => ymd === "2026-10-19")[1];
	check("strip mode: Monday has Austin's and Korben's strips and no booking cards", [strips(monday), monday.includes("<card bk|")], [2, false]);
	check("a day without project work has no strip", strips(days().find(([ymd]) => ymd === "2026-10-21")[1]), 0);
	mp.show_project_cards = true;
	mp.render_calendar(mp.all_cards());
	const cardsMonday = days().find(([ymd]) => ymd === "2026-10-19")[1];
	check("cards mode: the booking cards, no strips", [strips(cardsMonday), (cardsMonday.match(/<card bk\|/g) || []).length], [0, 3]);
	mp.show_project_work = false;
	mp.render_calendar(mp.all_cards());
	check("off: neither", days().map(([, text]) => strips(text) + (text.match(/<card bk\|/g) || []).length).reduce((a, b) => a + b, 0), 0);
	// Fountain Move cards are not bookings: a strip mode day still draws them.
	mp.show_project_work = true;
	mp.show_project_cards = false;
	mp.data.moves = [{ key: "move|TASK-9", kind: "move", name: "TASK-9", date: "2026-10-19", technician: "austin@x.com", crew: [], site: "Move", movable: true, hours: 8 }];
	mp.render_calendar(mp.all_cards());
	const withMove = days().find(([ymd]) => ymd === "2026-10-19")[1];
	check("a Fountain Move card is still a card beside the strips", [withMove.includes("<card move|TASK-9>"), strips(withMove)], [true, 2]);
});

test("Maintenance Planner: the crew view puts the strip in the person's cell", () => {
	const { mp } = simplifyPage();
	mp.view = "crew";
	mp.range_days = () => ["2026-10-19", "2026-10-20"];
	mp.p6d_crew_notes_row = () => "";
	mp.p6d_cell_blocks = () => "";
	mp.card_row_html = (card) => `<card ${card.key}>`;
	mp.render_tray = () => null;
	const html = () => mp.$grid_wrap.nodes[0].querySelector(".mp-crew").innerHTML;
	mp.render_crew(mp.all_cards());
	check("strips in the cells with project work, none in the others", strips(html()), 3);
	check("none of them names the person (the row does)", html().includes("mp-busy-name"), false);
	check("and no booking card", html().includes("<card bk|"), false);
	mp.technician = "korben@x.com";
	mp.render_crew(mp.all_cards());
	check("the crew view ignores the technician filter, as it always did", strips(html()), 3);
	mp.technician = "";
	mp.show_project_cards = true;
	mp.render_crew(mp.all_cards());
	check("cards mode: the cards are in the cells, no strip", [strips(html()), (html().match(/<card bk\|/g) || []).length], [0, 4]);
	mp.show_project_work = false;
	mp.render_crew(mp.all_cards());
	check("off: neither", [strips(html()), html().includes("<card bk|")], [0, false]);
});

test("Maintenance Planner: More > Display has Project work and Project work as cards; the cards switch asks for the work itself", () => {
	const { mp, ctx } = simplifyPage();
	const key = (name) => vm.runInContext(`MP.${name}`, ctx);
	const display = () => section(mp, "display");
	check("the two switches, and the strip is what they say first", [find(display(), "Project work").checked, find(display(), "Project work as cards").checked], [true, false]);
	find(display(), "Project work as cards").on_click();
	check("ticked: cards, remembered, the page redrawn", [mp.show_project_cards, mp.prefs[key("cards_key")], mp.renders], [true, "1", 1]);
	check("and it reads as ticked", find(display(), "Project work as cards").checked, true);
	find(display(), "Project work as cards").on_click();
	check("unticked: the strip again", [mp.project_work_mode(), mp.show_project_cards], ["strip", false]);
	mp.show_project_work = false;
	check("Project work off: the cards switch reads unticked", find(display(), "Project work as cards").checked, false);
	find(display(), "Project work as cards").on_click();
	check("asking for cards turns the work on", [mp.show_project_work, mp.project_work_mode()], [true, "cards"]);
	check("and remembers both", [mp.prefs[key("project_key")], mp.prefs[key("cards_key")]], ["1", "1"]);
	mp.set_show_project_cards(false);
	mp.set_show_project_work(false);
	mp.set_show_project_cards(false);
	check("the strip's own switch leaves Project work as it is", [mp.show_project_work, mp.project_work_mode()], [false, "off"]);
	check("Display is Color by, then the three switches; it holds no key", display().filter((item) => item.label).map((item) => item.label).slice(-3), ["Projected visits", "Project work", "Project work as cards"]);
});

test("Maintenance Planner: Resources available is one line for the week until it is opened", () => {
	const env = simplifyPage();
	const { mp } = env;
	mp.render_panel();
	const panel = mp.$panel.nodes[0];
	const line = panel.querySelector(".mp-res-sum");
	check("closed, the heading holds the summary", !!line, true);
	check("it says which week, and keeps today's onward", line.innerHTML.includes("This week"), true);
	check("Austin: 3h free Tuesday, off Thursday and Friday (the weekend is left out)", line.innerHTML.includes("3h free Tue · Off Thu–Fri"), true);
	check("Korben: free 8h on Tuesday only, Monday fully booked", line.innerHTML.includes("8h free Tue"), true);
	check("the names are the table's own chips, draggable and clickable", [line.innerHTML.includes('class="mp-person"'), line.innerHTML.includes('data-user="austin@x.com"')], [true, true]);
	check("the title carries all of it", line.getAttribute("title").includes("Austin Healey: 3h free Tue · Off Thu–Fri"), true);
	check("closed, no table", panel.querySelector(".mp-res-grid"), null);
	const toggle = panel.querySelector(".btn");
	check("Show says it is closed", [toggle.textContent, toggle.getAttribute("aria-expanded")], ["Show", "false"]);
	let decorated = 0;
	mp.p6a = {};
	mp.p6a_decorate = () => (decorated += 1);
	toggle.click();
	check("clicking opens it, remembered under the new key", [mp.panel_open, mp.prefs[vm.runInContext("MP.resources_key", env.ctx)]], [true, "1"]);
	check("the redrawn names get 6A's markup (a click for their week, the hover glow) without a full render", decorated, 1);
	const open = mp.$panel.nodes[0];
	check("open: the table, and no summary line", [!!open.querySelector(".mp-res-grid"), open.querySelector(".mp-res-sum")], [true, null]);
	check("Hide says it is open", [open.querySelector(".btn").textContent, open.querySelector(".btn").getAttribute("aria-expanded")], ["Hide", "true"]);
	// A picked technician: only theirs.
	mp.panel_open = false;
	mp.technician = "korben@x.com";
	mp.render_panel();
	const korben = mp.$panel.nodes[0].querySelector(".mp-res-sum").innerHTML;
	check("a picked technician is the only one in the line", [korben.includes("Korben"), korben.includes("Austin")], [true, false]);
	// The crew view has no panel.
	mp.view = "crew";
	mp.render_panel();
	check("the crew view has none", mp.$panel.nodes[0].style.display, "none");
});

test("Maintenance Planner: the summary says free days, a full week, overtime, work on a day off, and keeps to a line", () => {
	const { ctx } = simplifyPage();
	const pure = vm.runInContext("MP10_PURE", ctx);
	const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
	const week = (rows) => rows.map((row, i) => (row ? { name: names[i], weekend: i === 0 || i === 6, capacity: row[0], booked: row[1], free: row[2] } : null));
	const said = (rows) => pure.summary(week(rows)).text;
	check("the design's own example", said([[0, 0], [8, 8], [8, 5, 3], [8, 8], [0, 0], [0, 0], [0, 0]]), "3h free Tue · Off Thu–Fri");
	check("days with the same hours free are one stretch", said([[0, 0], [8, 0, 8], [8, 0, 8], [8, 0, 8], [8, 0, 8], [8, 0, 8], [0, 0]]), "8h free Mon–Fri");
	check("different hours are named apart, in day order", said([[0, 0], [8, 4, 4], [8, 0, 8], [8, 4, 4], [8, 8, 0], [8, 8, 0], [0, 0]]), "4h free Mon, Wed · 8h free Tue");
	check("a week with nothing free is said so", said([[0, 0], [8, 8, 0], [8, 8, 0], [8, 8, 0], [0, 0], [0, 0], [0, 0]]), "Fully booked · Off Thu–Fri");
	check("over their hours is said, in words, and is a danger", [said([[0, 0], [8, 10, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]), pure.summary(week([[0, 0], [8, 10, 0], null, null, null, null, null])).danger], ["Over 2h Mon · Off Tue–Fri", true]);
	check("work on a day off is a danger too, even on a weekend", [said([[0, 3], [8, 0, 8], null, null, null, null, null]), pure.summary(week([[0, 3]])).danger], ["8h free Mon · Booked on a day off Sun", true]);
	check("more than three stretches are cut to three and a count", said([[0, 0], [8, 1, 7], [8, 2, 6], [8, 3, 5], [8, 4, 4], [8, 5, 3], [0, 0]]), "7h free Mon · 6h free Tue · 5h free Wed · +2 more");
	check("a day the engine sent nothing for is skipped, and the days stay in place", said([[0, 0], null, [8, 0, 8], null, null, null, [0, 0]]), "8h free Tue");
	check("nothing to say is nothing", said([null, null, null, null, null, null, null]), "");
	check("Saturday and Sunday are never 'off'", said([[0, 0], [8, 0, 8], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]]), "8h free Mon · Off Tue–Fri");
	check("a Saturday shift counts as a working day", said([[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [8, 8, 0]]), "Fully booked · Off Mon–Fri");
	check("runs of consecutive days", [pure.runs([0, 1, 3, 5, 6]), pure.range_text([1, 2, 3], names), pure.range_text([1, 3], names)], [[[0, 1], [3], [5, 6]], "Mon–Wed", "Mon, Wed"]);
});

test("Maintenance Planner: in the month the summary speaks of the week that holds today; a past week is shown whole", () => {
	const { mp } = simplifyPage();
	mp.view = "month";
	mp.p6a_week_start = () => "2026-10-18";
	const now = mp.p10_summary_window();
	check("this week, from today (Monday) on", [now.label, now.days[0], now.days.length], ["This week", "2026-10-19", 6]);
	mp.p6a_week_start = () => "2026-10-25";
	const later = mp.p10_summary_window();
	check("a later week is named by its first day and whole", [later.label, later.days.length], ["Week of Oct 25", 7]);
	mp.p6a_week_start = () => "2026-10-04";
	const earlier = mp.p10_summary_window();
	check("a week that has gone is shown whole", [earlier.label, earlier.days.length], ["Week of Oct 4", 7]);
});

test("Maintenance Planner: My day is the technician's way to Today on the Project Planner, one history entry", () => {
	const { mp, route, frappe, ctx } = simplifyPage();
	const routed = [];
	mp.p6a_route = (fn) => routed.push(fn) && fn();
	frappe.session = { user: "austin@x.com" };
	check("Austin is a technician with a Planner Resource: he has a day", mp.p10_has_day(), true);
	check("it is in More > Tools", find(section(mp, "tools"), "My day") !== null, true);
	mp.data.can_schedule = false;
	find(section(mp, "tools"), "My day").on_click();
	check("it goes to /project-planner/today/<the planner's today>, by set_route", route.set, [["project-planner", "today", "2026-10-19"]]);
	check("through the drawer-aware route, leaving the page (not keeping a drawer)", routed.length, 1);
	// The bar's own link: shown to someone who cannot schedule.
	mp.$myday = mp.$filters;
	const bar = mp.$myday.nodes[0];
	mp.p10_sync_myday();
	check("a technician sees the link in the bar", bar.style.display, "");
	mp.data.can_schedule = true;
	mp.p10_sync_myday();
	check("a scheduler does not (it is still in Tools)", bar.style.display, "none");
	check("and still has it in Tools", find(section(mp, "tools"), "My day") !== null, true);
	// Someone with no day of their own is offered nothing.
	frappe.session = { user: "lisa@x.com" };
	check("Lisa is not on the technician list: no day", [mp.p10_has_day(), find(section(mp, "tools"), "My day")], [false, null]);
	mp.data.can_schedule = false;
	mp.p10_sync_myday();
	check("and no bar link either", bar.style.display, "none");
	route.set.length = 0;
	mp.p10_open_today();
	check("and the click does nothing", route.set, []);
	frappe.session = { user: "austin@x.com" };
	mp.tech_by_user["austin@x.com"].resource = "";
	check("a technician with no Planner Resource has no Today either", mp.p10_has_day(), false);
	check("the link goes only to Today: never the Project Planner's landing", vm.runInContext("MP10.today_view", ctx), "today");
});

test("Maintenance Planner: the help is one provider function, with the kinds, the strip and Resources, and takes more", () => {
	const { mp, frappe } = simplifyPage();
	mp.p6c_legend_sections = () => [];
	mp.p6d_legend_sections = () => [];
	mp.p6a_tip_section = () => ({ title: "Tips", items: [] });
	frappe.session = { user: "lisa@x.com" };
	const titles = () => mp.legend_sections().map((section) => section.title);
	check("the sections, kinds before the strip, Resources after it", titles().slice(0, 5), ["Tips", "Quick looks", "Visit cards", "Project work", "Resources available"]);
	check("no 'Your day' for someone with no day", titles().includes("Your day"), false);
	frappe.session = { user: "austin@x.com" };
	check("a technician's help says what My day is", titles().includes("Your day"), true);
	const strip = mp.legend_sections().find((s) => s.title === "Project work").items[0];
	check("the strip's sample is the real strip class", strip.sample_html.includes('class="mp-busy mp-p10-sample"'), true);
	// P12 and later phases register providers; a provider that throws is skipped.
	mp.p6_legend_providers = [() => [{ title: "A later phase", items: [{ sample_html: "", text: "x" }] }], () => { throw new Error("boom"); }];
	check("a registered provider's sections come after Keyboard and Faster scheduling", titles().slice(-3), ["Keyboard", "Faster scheduling", "A later phase"]);
	// "?" opens whatever legend_sections returns.
	let opened = null;
	mp.p6a = { kit: { legend: (sections, opts) => (opened = [sections.length, opts.title]) } };
	mp.p6a_legend();
	check("the button opens them in the kit's legend", opened, [titles().length, "How to read the Maintenance Planner"]);
});

test("Maintenance Planner: saved views carry the two new switches, and an old view still matches", () => {
	const env = simplifyPage();
	const { mp, ctx } = env;
	const proto = vm.runInContext("MaintenancePlanner", ctx).prototype;
	mp.p6c_matching_view = proto.p6c_matching_view;
	const state = mp.p6c_state();
	check("the state says whether project work is cards and whether Resources is open", [state.project_cards, state.resources], [false, false]);
	check("and has no 'panel' (the old default put 'open' in every user's last-used row)", "panel" in state, false);
	check("the page's schema knows both, and not 'panel'", Object.keys(vm.runInContext("MP6C.schema", ctx)).sort(), ["color_by", "project_cards", "project_work", "projected", "resources", "technician", "view"]);
	mp.p6c_apply_state({ project_cards: true, resources: true, panel: false }, { render: false });
	check("applying a view sets them; a stored 'panel' is not read", [mp.show_project_cards, mp.panel_open], [true, true]);
	mp.p6c_apply_state({ project_cards: false, resources: false }, { render: false });
	check("and back", [mp.show_project_cards, mp.panel_open], [false, false]);
	mp.p6c_apply_state({ technician: "austin@x.com" }, { render: false });
	check("a view that says nothing about them leaves them as they are", [mp.show_project_cards, mp.panel_open], [false, false]);
	// A view saved before P10 has no say about the two newer switches, so it still matches the screen.
	mp.technician = "";
	mp.p6c.views = [
		{ name: "Old", data: { view: "week", technician: "", projected: true, project_work: true, color_by: "default" } },
		{ name: "Other", data: { view: "month", technician: "", projected: true, project_work: true, color_by: "default" } },
	];
	mp.view = "week";
	check("the old view that matches the screen is the one on screen", (mp.p6c_matching_view() || {}).name, "Old");
	mp.show_project_cards = true;
	check("it says nothing about cards, so it still matches while they are on", (mp.p6c_matching_view() || {}).name, "Old");
	const now = { view: "week", technician: "", projected: true, project_work: true, project_cards: true, resources: false, color_by: "default" };
	mp.p6c.views = [{ name: "Strip", data: Object.assign({}, now, { project_cards: false }) }, { name: "Cards", data: now }];
	check("a view that does say it matches only the screen it describes", (mp.p6c_matching_view() || {}).name, "Cards");
	mp.show_project_cards = false;
	check("and the other when the screen is the strip", (mp.p6c_matching_view() || {}).name, "Strip");
	check("a view's summary says cards", mp.p6c_view_summary({ view: "week", project_work: true, project_cards: true }), "Week · Project work as cards");
	check("and 'no project work' wins over cards", mp.p6c_view_summary({ view: "week", project_work: false, project_cards: true }), "Week · No project work");
});

test("Maintenance Planner: the printed page shows the strips beside the visits, and the hint says how project work is drawn", () => {
	const { mp } = simplifyPage();
	mp.p6c.highlight = null;
	const day = mp.p10_print_items("2026-10-19", null);
	check("a day: a line a technician, with the person in the second line", day.map((item) => [item.title, item.sub]), [["Project: 8h", "Austin Healey"], ["Rental: 8h", "Korben Dallas"]]);
	check("plain: not dashed, no marks, not faded, not lit", [day[0].dashed, day[0].marks, day[0].faded, day[0].highlight], [false, [], false, false]);
	mp.technician = "austin@x.com";
	check("a picked technician: theirs, without the name", mp.p10_print_items("2026-10-19", null).map((item) => [item.title, item.sub]), [["Project: 8h", ""]]);
	mp.technician = "";
	check("the crew sheet's cell is that person's", mp.p10_print_items("2026-10-19", "korben@x.com").map((item) => item.title), ["Rental: 8h"]);
	mp.p6c.highlight = { key: "PRJ-1" };
	check("with a site highlighted the strip is not faded: on screen the highlight fades cards, never the strip", mp.p10_print_items("2026-10-19", null).map((item) => item.faded), [false, false]);
	check("the hint names the strip", mp.p10_hint_note().includes("thin grey strip"), true);
	mp.show_project_cards = true;
	check("cards mode prints cards (p6c_print_item), not strips", mp.p10_print_items("2026-10-19", null), []);
	check("and the hint names the cards", mp.p10_hint_note().includes("Cards marked Project"), true);
	mp.show_project_work = false;
	check("with project work off the hint says nothing of it", mp.p10_hint_note(), "");
});

test("Maintenance Planner: a click on a strip in the month opens the week of the day clicked, not today's", () => {
	const { mp, doc } = simplifyPage();
	const peeks = [];
	mp.p6a = { kit: { peeks: { person: (opts) => (peeks.push(opts), { is_open: () => true }) }, drawer: {} } };
	mp.p6d_person_actions = () => [];
	mp.range = () => (mp.view === "month" ? { start: miniMoment("2026-09-27"), end: miniMoment("2026-11-07") } : { start: miniMoment("2026-10-18"), end: miniMoment("2026-10-24") });
	mp.data.bookings["austin@x.com"]["2026-10-29"] = { capacity: 8, booked: 4, free: 4, items: [{ kind: "task", ref: "TASK-7", label: "Late October", hours: 4 }], blocks: [] };
	mp.view = "month";
	mp.anchor = "2026-10-01";
	const strip = (ymd) => {
		const el = doc.createElement("div");
		el.setAttribute("data-busy-user", "austin@x.com");
		el.setAttribute("data-busy-date", ymd);
		return el;
	};
	mp.p10_open_busy(strip("2026-10-29"));
	check("a strip on Thursday the 29th: the peek starts on that week's Sunday and selects the 29th", [peeks[0].start, peeks[0].selected, peeks[0].user], ["2026-10-25", "2026-10-29", "austin@x.com"]);
	mp.p10_open_busy(strip("2026-10-20"));
	check("a strip in today's week: today's week", [peeks[1].start, peeks[1].selected], ["2026-10-18", "2026-10-20"]);
	mp.p10_open_busy(strip("2026-09-28"));
	check("a strip in the month's leading days: its own week, in the month before", [peeks[2].start, peeks[2].selected], ["2026-09-27", "2026-09-28"]);
	check("clicking a technician's name (no day) still opens the week on screen: in the month, the one holding today", (mp.p6a_open_person("austin@x.com"), [peeks[3].start, peeks[3].selected]), ["2026-10-18", null]);
	check("the Resources summary still speaks of today's week in the month", mp.p6a_week_start(), "2026-10-18");
	mp.view = "week";
	mp.anchor = "2026-10-20";
	mp.p10_open_busy(strip("2026-10-22"));
	check("in the week view, a day of the week on screen opens that week", peeks[4].start, "2026-10-18");
	check("a day that is not a date asks for the week on screen", mp.p6a_week_start("not a day"), "2026-10-18");
});

test("Maintenance Planner: a disabled technician keeps their crew row while they have project work, in the crew view and the print", () => {
	const { mp } = simplifyPage();
	mp.data.technicians.push({ user: "jesse@x.com", name: "Jesse Pinkman", enabled: false, resource: "R-J", group: "Field" });
	mp.tech_names["jesse@x.com"] = "Jesse Pinkman";
	mp.data.bookings["jesse@x.com"] = { "2026-10-19": { capacity: 8, booked: 8, free: 0, items: [{ kind: "task", ref: "TASK-8", label: "Last week of work", hours: 8 }], blocks: [] } };
	mp.view = "crew";
	mp.range_days = () => ["2026-10-19", "2026-10-20"];
	mp.p6d_crew_notes_row = () => "";
	mp.p6d_cell_blocks = () => "";
	mp.card_row_html = (card) => `<card ${card.key}>`;
	mp.render_tray = () => null;
	const crew = () => mp.$grid_wrap.nodes[0].querySelector(".mp-crew").innerHTML;
	const printed = () => mp.p6c_print_crew().rows.map((row) => row.label);
	mp.render_crew(mp.all_cards());
	check("strip mode: the row is there, with its strip", [crew().includes('data-user="jesse@x.com"'), strips(crew())], [true, 4]);
	check("and so is the printed row", printed().includes("Jesse Pinkman"), true);
	mp.show_project_cards = true;
	mp.render_crew(mp.all_cards());
	check("cards mode: the row is there, with its card", [crew().includes('data-user="jesse@x.com"'), crew().includes("<card bk|jesse@x.com")], [true, true]);
	check("and in the print", printed().includes("Jesse Pinkman"), true);
	mp.show_project_work = false;
	mp.render_crew(mp.all_cards());
	check("project work off: nothing keeps a disabled person's row", [crew().includes('data-user="jesse@x.com"'), printed().includes("Jesse Pinkman")], [false, false]);
	mp.show_project_work = true;
	mp.show_project_cards = false;
	mp.data.bookings["jesse@x.com"]["2026-10-19"].items = [];
	mp.render_crew(mp.all_cards());
	check("with nothing on the week there is no row either", [crew().includes('data-user="jesse@x.com"'), printed().includes("Jesse Pinkman")], [false, false]);
	mp.data.technicians.find((person) => person.user === "jesse@x.com").enabled = true;
	mp.render_crew(mp.all_cards());
	check("an enabled technician always has one", crew().includes('data-user="jesse@x.com"'), true);
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
