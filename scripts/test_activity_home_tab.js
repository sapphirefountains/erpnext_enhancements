#!/usr/bin/env node
/**
 * Where the activity feed (the form timeline) shows, run against the REAL
 * public/js/global_enhancements/activity_first_tab_only.js in a vm with a fake DOM.
 *
 * The fake DOM has the shape Frappe v16 really builds (form.js, page.js, layout.js, tab.js), not the
 * shape the script was first written against:
 *
 *   .layout-main-section-wrapper                       <- the root; the script puts its class here
 *     .layout-main-section > .form-layout > .form-page > .form-tabs > li > button.nav-link
 *     div > .form-footer                               <- a SIBLING of .layout-main-section,
 *                                                         built on page.main.parent() (form.js);
 *                                                         it is NOT inside .form-page
 *
 * The tab links carry the ids tab.js gives them (`<scrubbed doctype>-<fieldname>-tab`). A tab switch
 * is what Frappe's set_active() does and nothing more: it moves the `active` class between the links.
 * `shown.bs.tab` is never fired, because Frappe's click handler marks the link active before
 * Bootstrap's `.tab("show")` runs and Bootstrap returns early on an active link. The script has to
 * notice the switch from the class change alone.
 *
 * Project's home for the feed is its Comments tab (Nik, 2026-10-22). Every other doctype is left as
 * it is (the feed on every tab).
 *
 * Run: node scripts/test_activity_home_tab.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(
	__dirname, "..", "erpnext_enhancements", "public", "js", "global_enhancements", "activity_first_tab_only.js"
);
const HIDDEN = "ee-activity-off-tab";

let checks = 0;
let failures = 0;

function check(label, actual, expected) {
	checks += 1;
	if (actual !== expected) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${expected}\n    actual   ${actual}`);
	}
}

// --- a small page: elements with a class list, a parent chain, and the few selectors the script uses
class El {
	constructor(cls = [], id = "") {
		this.id = id;
		this.cls = new Set(cls);
		this.children = [];
		this.parent = null;
		this.nodeType = 1;
		this.classList = {
			add: (name) => this._set(name, true),
			remove: (name) => this._set(name, false),
			toggle: (name, on) => this._set(name, on === undefined ? !this.cls.has(name) : on),
			contains: (name) => this.cls.has(name),
		};
	}
	_set(name, on) {
		const had = this.cls.has(name);
		if (on) this.cls.add(name);
		else this.cls.delete(name);
		if (had !== on) this.world && this.world.classChanged(this);
		return on;
	}
	append(child) {
		child.parent = this;
		child.world = this.world;
		this.children.push(child);
		return child;
	}
	all() {
		return this.children.flatMap((c) => [c, ...c.all()]);
	}
	matches(compound) {
		return compound.split(".").filter(Boolean).every((c) => this.cls.has(c));
	}
	closest(selector) {
		for (let el = this; el; el = el.parent) if (el.matches(selector)) return el;
		return null;
	}
	// ".a .b" (descendant) or ".a.b" / ".a" only; enough for the script's selectors.
	querySelectorAll(selector) {
		const parts = selector.split(/\s+/);
		return this.all().filter((el) => {
			if (!el.matches(parts[parts.length - 1])) return false;
			let at = el.parent;
			for (let i = parts.length - 2; i >= 0; i--) {
				while (at && !at.matches(parts[i])) at = at.parent;
				if (!at) return false;
				at = at.parent;
			}
			return true;
		});
	}
	querySelector(selector) {
		return this.querySelectorAll(selector)[0] || null;
	}
}

// The world: the document, timers, observers, and the jQuery-ish handlers the script registers.
function load() {
	const world = {
		roots: [],
		timers: [],
		observers: [],
		handlers: {},
		classChanged(el) {
			for (const o of this.observers) {
				if (o.options && o.options.attributes && o.target.all().includes(el)) o.callback([{ target: el }]);
			}
		},
	};
	const jq = (target) => ({
		on(event, fn) {
			(world.handlers[event] = world.handlers[event] || []).push(fn);
			return this;
		},
		ready(fn) {
			world.ready = fn;
			return this;
		},
	});
	const sandbox = {
		$: jq,
		document: { querySelectorAll: () => world.roots, body: new El() },
		MutationObserver: class {
			constructor(callback) {
				this.callback = callback;
			}
			observe(target, options) {
				this.target = target;
				this.options = options;
				world.observers.push(this);
			}
		},
		setTimeout: (fn) => (world.timers.push(fn), world.timers.length),
		clearTimeout: (id) => {
			world.timers[id - 1] = null;
		},
		console,
	};
	sandbox.frappe = {
		provide(name) {
			let target = sandbox;
			for (const part of name.split(".")) target = target[part] = target[part] || {};
		},
		scrub: (text, sep) => text.replace(/ /g, sep || "_").toLowerCase(),
	};
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), sandbox, { filename: SCRIPT });
	world.apply = sandbox.erpnext_enhancements.activity_tabs.apply;
	world.settle = () => {
		// run the debounced work; a run may queue another
		for (let i = 0; i < 5 && world.timers.some(Boolean); i++) {
			const due = world.timers.splice(0);
			due.forEach((fn) => fn && fn());
		}
	};
	return world;
}

/**
 * A form page as Frappe v16 lays it out. `ids` are the tab links' ids, `active` the open one.
 * `footerIn` puts the footer in a tab pane instead (ERPNext's CRM doctypes move it there).
 */
function form(world, ids, active, { footerIn = null, noFooter = false } = {}) {
	const root = new El(["layout-main-section-wrapper"]);
	root.world = world;
	const main = root.append(new El(["layout-main-section"]));
	const page = main.append(new El(["form-layout"])).append(new El(["form-page"]));
	const tabs = page.append(new El(["form-tabs"]));
	const links = ids.map((id, i) => tabs.append(new El(["nav-link", ...(i === active ? ["active"] : [])], id)));
	const panes = page.append(new El(["form-tab-content"]));
	const pane = panes.append(new El(["tab-pane"]));
	const footerHost = footerIn === "pane" ? pane : root.append(new El());
	const footer = noFooter ? null : footerHost.append(new El(["form-footer"]));
	world.roots.push(root);
	return {
		root,
		footer,
		links,
		hidden: () => root.cls.has(HIDDEN),
		// Frappe's tab.js set_active(): clear the others, mark this one. No shown.bs.tab.
		switchTo(i) {
			links.forEach((l, j) => l.classList.toggle("active", j === i));
			world.settle();
		},
	};
}

const PROJECT = [
	"project-custom_details-tab",
	"project-custom_scope-tab",
	"project-custom_schedule-tab",
	"project-custom_comments-tab",
	"project-connections_tab-tab",
];
const TASK = ["task-details-tab", "task-dependencies_tab-tab", "task-custom_comments-tab"];

function paint(world) {
	world.apply();
	world.settle();
}

// --- Project: first paint on each tab (a form can open on a #hash tab)
PROJECT.forEach((id, i) => {
	const world = load();
	const f = form(world, PROJECT, i);
	paint(world);
	check(`Project opens on ${id}: feed ${i === 3 ? "shown" : "hidden"}`, f.hidden(), i !== 3);
});

// --- Project: switching tabs, the way Frappe does it
{
	const world = load();
	const f = form(world, PROJECT, 0);
	paint(world);
	check("Project, Details: hidden", f.hidden(), true);
	f.switchTo(3);
	check("then Comments (class change only): shown", f.hidden(), false);
	f.switchTo(1);
	check("then Scope: hidden again", f.hidden(), true);
	f.switchTo(3);
	f.switchTo(4);
	check("Comments then the last tab: hidden", f.hidden(), true);
	f.switchTo(3);
	check("and back to Comments: shown", f.hidden(), false);
	check("the tab list is observed once, however often it is applied", (paint(world), paint(world), world.observers.filter((o) => o.options && o.options.attributes).length), 1);
}

// --- the form-refresh handler paints too, after a timeout
{
	const world = load();
	const f = form(world, PROJECT, 0);
	world.handlers["form-refresh"].forEach((fn) => fn());
	world.settle();
	check("form-refresh paints the feed away on Details", f.hidden(), true);
}

// --- every other doctype is left as it is: the feed on every tab
{
	const world = load();
	const f = form(world, TASK, 0);
	paint(world);
	check("Task, first tab: feed shown", f.hidden(), false);
	f.switchTo(1);
	check("Task, second tab: feed still shown", f.hidden(), false);
	f.switchTo(2);
	check("Task, a tab named custom_comments: feed still shown (not Project's)", f.hidden(), false);
}

// --- fallbacks
{
	const world = load();
	const f = form(world, PROJECT.filter((id) => !id.includes("comments")), 1);
	paint(world);
	check("Project without a Comments tab: left alone", f.hidden(), false);
}
{
	const world = load();
	const f = form(world, PROJECT, 0);
	f.links.forEach((l) => l.classList.remove("active"));
	paint(world);
	check("Project mid-render (no active tab yet): shown, no flash", f.hidden(), false);
}
{
	const world = load();
	const f = form(world, ["project-custom_details-tab"], 0);
	paint(world);
	check("a form with one tab is untouched", f.hidden(), false);
}

// --- a footer already inside a tab pane is left to Bootstrap (CRM doctypes)
{
	const world = load();
	const f = form(world, PROJECT, 0, { footerIn: "pane" });
	f.root.classList.add(HIDDEN);
	paint(world);
	check("a footer inside a tab pane: class cleared", f.hidden(), false);
}

// --- no footer (a print view), nothing to toggle, nothing thrown
{
	const world = load();
	const f = form(world, PROJECT, 0, { noFooter: true });
	paint(world);
	check("no footer in the page: no class", f.hidden(), false);
}

// --- two forms open in the DOM at once (Frappe keeps recent pages): each decides for itself
{
	const world = load();
	const a = form(world, PROJECT, 0);
	const b = form(world, PROJECT, 3);
	paint(world);
	check("two Projects: the one on Details hides its feed", a.hidden(), true);
	check("two Projects: the one on Comments shows its feed", b.hidden(), false);
}

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
