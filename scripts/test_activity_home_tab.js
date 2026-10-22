#!/usr/bin/env node
/**
 * Where the activity feed (the form timeline) shows, run against the REAL
 * public/js/global_enhancements/activity_first_tab_only.js in a vm with a fake DOM.
 *
 * Most forms show the feed on their first tab only. Project's home for it is its Comments tab
 * (Nik, 2026-10-22): shown there, hidden on every other tab, Details included. The fake tab links
 * carry the ids Frappe v16 gives them (`<scrubbed doctype>-<fieldname>-tab`, form/tab.js).
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

function load() {
	const wrappers = [];
	const jq = () => ({ on() { return this; }, ready() { return this; } });
	const sandbox = {
		$: jq,
		document: { querySelectorAll: () => wrappers },
		MutationObserver: class { observe() {} },
		setTimeout: () => 0,
		clearTimeout: () => {},
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
	return { wrappers, apply: sandbox.erpnext_enhancements.activity_tabs.apply };
}

// A `.form-page`: tab links with Frappe's ids, one active, and a footer.
function form(ids, active, { footerInPane = false } = {}) {
	const links = ids.map((id) => ({ id }));
	const classes = new Set();
	return {
		classes,
		classList: {
			toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
			remove: (name) => classes.delete(name),
			contains: (name) => classes.has(name),
		},
		querySelectorAll: (selector) => (selector === ".form-tabs .nav-link" ? links : []),
		querySelector(selector) {
			if (selector === ".form-tabs .nav-link.active") return active === null ? null : links[active];
			if (selector === ".form-footer") return { closest: () => (footerInPane ? {} : null) };
			return null;
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
const TASK = ["task-details-tab", "task-dependencies_tab-tab"];

function hidden(wrapper) {
	return wrapper.classes.has(HIDDEN);
}

function run(wrapper) {
	const { wrappers, apply } = load();
	wrappers.push(wrapper);
	apply();
	return hidden(wrapper);
}

// --- Project: the Comments tab is home
check("Project, Details open: feed hidden", run(form(PROJECT, 0)), true);
check("Project, Scope open: feed hidden", run(form(PROJECT, 1)), true);
check("Project, Schedule open: feed hidden", run(form(PROJECT, 2)), true);
check("Project, Comments open: feed shown", run(form(PROJECT, 3)), false);
check("Project, last tab open: feed hidden", run(form(PROJECT, 4)), true);

// --- every other doctype: the first tab, as before
check("Task, first tab open: feed shown", run(form(TASK, 0)), false);
check("Task, second tab open: feed hidden", run(form(TASK, 1)), true);

// --- fallbacks
check(
	"Project without a Comments tab falls back to the first tab (shown)",
	run(form(PROJECT.filter((id) => !id.includes("comments")), 0)),
	false
);
check(
	"Project without a Comments tab, second tab open: hidden",
	run(form(PROJECT.filter((id) => !id.includes("comments")), 1)),
	true
);
check("a tab named like Project's, on another doctype, is not its home", run(form(["task-details-tab", "task-custom_comments-tab"], 1)), true);
check("Project mid-render (no active tab yet): shown, no flash", run(form(PROJECT, null)), false);
check("a form with one tab is untouched", run(form(["project-custom_details-tab"], 0)), false);

// --- a footer already inside a tab pane is left to Bootstrap (CRM doctypes)
{
	const { wrappers, apply } = load();
	const w = form(PROJECT, 0, { footerInPane: true });
	w.classes.add(HIDDEN);
	wrappers.push(w);
	apply();
	check("a footer inside a tab pane: class cleared", hidden(w), false);
}

// --- switching tabs re-evaluates
{
	const { wrappers, apply } = load();
	const w = form(PROJECT, 0);
	wrappers.push(w);
	apply();
	check("Details: hidden", hidden(w), true);
	const w2 = form(PROJECT, 3);
	w2.classes.add(HIDDEN);
	wrappers[0] = w2;
	apply();
	check("then Comments: shown again", hidden(w2), false);
}

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
