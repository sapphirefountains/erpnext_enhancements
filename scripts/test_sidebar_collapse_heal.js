#!/usr/bin/env node
/**
 * The one-time desk sidebar heal (sidebar_collapse_heal.js, v1.574.2), executed against a
 * stubbed localStorage, jQuery and frappe rather than grepped.
 *
 * Until v1.574.1 auto_collapse_sidebar.js clicked the page-head toggle on every form in a
 * window under 1400px. In frappe v16.50.0 that toggle folds the DESK's module sidebar and
 * saves "desk-sidebar-collapsed" = "1" in the browser; beside a pinned Dock (every user
 * here) that hides the panel and the Help menu outright. The heal clears it once. Every
 * rule below fails silently in production:
 *
 *   1. It runs ONCE per browser, and the first run counts even when there was nothing to
 *      clear. It cannot tell our collapse from a person's, so a heal that ran again — or
 *      that only marked itself done when it found the key — would undo a deliberate
 *      Ctrl+/ on some later reload, forever.
 *   2. It clears before the desk draws the sidebar (app_include_js evaluates before
 *      frappe.start_app), so the panel simply comes up open.
 *   3. If the desk is already up it reopens the live sidebar — on a desktop only. On a
 *      phone the sidebar is a drawer, and opening it covers the page.
 *   4. On app_ready it defers the reopen a tick: app_ready fires inside the Application
 *      constructor, before frappe.app is assigned, so frappe.app.sidebar is not there yet.
 *   5. Storage that throws (private windows, blocked site data) never escapes into boot.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const FILE = path.join(ROOT, 'erpnext_enhancements', 'public', 'js', 'global_enhancements', 'sidebar_collapse_heal.js');

if (!fs.existsSync(FILE)) {
	console.error(
		'MARKERS NOT FOUND: ' +
			path.relative(process.cwd(), FILE) +
			' does not exist. The heal was moved or removed — re-derive these checks ' +
			'deliberately rather than deleting them.'
	);
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, 'utf8');

const COLLAPSED = 'desk-sidebar-collapsed';
const DONE = 'ee_sidebar_heal_16_50';

let failures = 0;

function fail(message) {
	failures += 1;
	console.error('  FAIL  ' + message);
}

function pass(message) {
	console.log('  ok    ' + message);
}

function check(condition, message) {
	if (condition) pass(message);
	else fail(message);
}

function makeStorage(initial) {
	const data = new Map(Object.entries(initial || {}));
	const storage = {
		throws: false,
		data,
		getItem(key) {
			if (storage.throws) throw new Error('SecurityError: storage is disabled');
			return data.has(key) ? data.get(key) : null;
		},
		setItem(key, value) {
			if (storage.throws) throw new Error('SecurityError: storage is disabled');
			data.set(key, String(value));
		},
		removeItem(key) {
			if (storage.throws) throw new Error('SecurityError: storage is disabled');
			data.delete(key);
		},
	};
	return storage;
}

// frappe v16.50.0 Sidebar, as far as the heal can see it: open() saves "0" (sidebar.js
// save_collapsed_state, skipped for a drawer) and marks the panel expanded.
function makeSidebar(storage, { expanded = false, drawer = false } = {}) {
	const sidebar = {
		wrapper: {},
		sidebar_expanded: expanded,
		opened: 0,
		panel_can_close() {
			return drawer;
		},
		open() {
			sidebar.opened += 1;
			sidebar.sidebar_expanded = true;
			if (!drawer) storage.setItem(COLLAPSED, '0');
		},
	};
	return sidebar;
}

// Evaluates the heal once, as one page load would. `app` is frappe.app at evaluation time.
function load(storage, { app } = {}) {
	const handlers = {};
	const timers = [];
	const warnings = [];
	const frappe = { app, is_mobile: () => false };
	const context = {
		frappe,
		localStorage: storage,
		document: {},
		$: () => ({
			on(event, fn) {
				(handlers[event] = handlers[event] || []).push(fn);
			},
		}),
		setTimeout: (fn) => timers.push(fn),
		console: { warn: (...args) => warnings.push(args), log() {}, error() {} },
	};
	context.window = context;
	vm.createContext(context);
	let threw = null;
	try {
		vm.runInContext(SOURCE, context, { filename: 'sidebar_collapse_heal.js' });
	} catch (e) {
		threw = e;
	}
	return {
		frappe,
		threw,
		warnings,
		timers,
		appReady() {
			for (const fn of handlers.app_ready || []) fn();
		},
		flush() {
			while (timers.length) timers.shift()();
		},
		handlers,
	};
}

console.log('desk sidebar heal — once per browser, before the desk draws, never on a phone\n');

// --- 1. The usual case: our old script left the sidebar collapsed -------------------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	const page = load(storage);
	check(!page.threw, 'evaluates without throwing before the desk exists');
	check(storage.getItem(COLLAPSED) === null, 'a saved collapse is cleared at evaluation, before Sidebar.make_dom() reads it');
	check(storage.getItem(DONE) === '1', 'the heal marks itself done');
	check((page.handlers.app_ready || []).length === 1, 'registers exactly one app_ready safety net');
	storage.setItem(COLLAPSED, '1'); // anything written after the eager pass is not ours to clear
	page.appReady();
	page.flush();
	check(storage.getItem(COLLAPSED) === '1', 'the app_ready pass does not run the heal a second time');
}

// --- 2. Once only: a collapse the person makes afterwards sticks --------------------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	load(storage).appReady();
	storage.setItem(COLLAPSED, '1'); // Ctrl+/ on a later visit
	const later = load(storage, { app: { sidebar: makeSidebar(storage) } });
	later.appReady();
	later.flush();
	check(storage.getItem(COLLAPSED) === '1', 'a deliberate collapse on a later load is left alone');
	check(later.frappe.app.sidebar.opened === 0, 'and the sidebar is not reopened over it');
}

// --- 3. Nothing to clear still counts as the one run -------------------------------------
{
	const storage = makeStorage({});
	load(storage);
	check(storage.getItem(DONE) === '1', 'a browser with no saved state is marked done on its first load');
	storage.setItem(COLLAPSED, '1');
	load(storage).appReady();
	check(storage.getItem(COLLAPSED) === '1', 'so a collapse made after that first load is never cleared');
}

// --- 4. The desk is already up (bundle evaluated late): reopen the live sidebar -----------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	const sidebar = makeSidebar(storage);
	load(storage, { app: { sidebar } });
	check(sidebar.opened === 1, 'a desktop sidebar already drawn collapsed is reopened, once');
	check(storage.getItem(DONE) === '1', 'and the heal is marked done');
}
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	const sidebar = makeSidebar(storage, { expanded: true });
	load(storage, { app: { sidebar } });
	check(sidebar.opened === 0, 'an already-open sidebar is not toggled');
}
{
	const storage = makeStorage({ [COLLAPSED]: '0' });
	const sidebar = makeSidebar(storage);
	load(storage, { app: { sidebar } });
	check(sidebar.opened === 0, 'a saved "open" ("0") is not treated as something to reopen');
}

// --- 5. A phone: the sidebar is a drawer and must stay shut -------------------------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	const sidebar = makeSidebar(storage, { drawer: true });
	const page = load(storage, { app: { sidebar } });
	page.appReady();
	page.flush();
	check(sidebar.opened === 0, 'the mobile drawer is never opened over the page');
	check(storage.getItem(COLLAPSED) === null, 'the stale key is still cleared there');
}

// --- 6. app_ready: frappe.app.sidebar does not exist yet inside the constructor ------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	storage.throws = true; // the eager pass gets nothing
	const page = load(storage);
	storage.throws = false;
	page.frappe.app = {}; // frappe.provide("frappe.app"); the instance is not assigned yet
	page.appReady();
	check(storage.getItem(COLLAPSED) === null, 'the app_ready pass clears the key');
	check(page.timers.length === 1, 'and defers the reopen instead of looking for the sidebar now');
	const sidebar = makeSidebar(storage);
	page.frappe.app = { sidebar }; // the constructor returned
	page.flush();
	check(sidebar.opened === 1, 'the deferred reopen finds the live sidebar and opens it');
}

// --- 7. Storage that throws never reaches desk boot --------------------------------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	storage.throws = true;
	const sidebar = makeSidebar(storage);
	const page = load(storage, { app: { sidebar } });
	let threw = page.threw;
	try {
		page.appReady();
		page.flush();
	} catch (e) {
		threw = e;
	}
	check(!threw, 'refusing storage throws nothing, at evaluation or on app_ready');
	check(sidebar.opened === 0, 'and opens nothing, since nothing was cleared');
}

// --- 8. A broken sidebar object is a warning, not a boot failure -------------------------
{
	const storage = makeStorage({ [COLLAPSED]: '1' });
	const sidebar = makeSidebar(storage);
	sidebar.open = () => {
		throw new Error('wrapper detached');
	};
	const page = load(storage, { app: { sidebar } });
	check(!page.threw && page.warnings.length === 1, 'an open() that throws is caught and logged as a warning');
}

console.log('');
if (failures) {
	console.error(failures + ' check(s) failed');
	process.exit(1);
}
console.log('all checks passed');
