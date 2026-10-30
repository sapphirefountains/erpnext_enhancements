#!/usr/bin/env node
/**
 * Custom pages S4c (v1.680.0): page_search.js executed against a stubbed frappe, rather than
 * grepped. It loads the real file in a vm context with frappe.boot.page_info, frappe.ui.keys,
 * frappe.set_route, a fake AwesomeBar and a fake SearchDialog, fires app_ready, and checks:
 *
 *   1. Matching: titles, aliases, multi-word aliases, and the short acronyms ("qbo", "qb")
 *      that MariaDB full-text search can never find (ft_min_word_len = 4).
 *   2. Role filtering: a Desk page appears only when frappe.boot.page_info has it, the www
 *      Stock Scan only for its roles, and a shortcut is registered only for what the user can
 *      open.
 *   3. Routing: a Desk page through frappe.set_route(<page name>), a www page through a real
 *      navigation; the awesomebar rows carry the route Frappe's own select handler follows.
 *   4. The awesomebar wrap: Frappe's own row for the same page is folded into ours (one row
 *      per page), Frappe's list comes back untouched when nothing matches or anything throws,
 *      and a missing hook point means nothing is patched.
 *   5. Keys: every chord is spelled as frappe.ui.keys.get_key spells it, and none is bound by
 *      Frappe v16.50, ERPNext v16.50 or this app (the list below).
 *   6. Never while typing: input, textarea, select, contenteditable (any spelling, and a child
 *      of one), a held-down repeat and an open dialog all leave the keystroke alone.
 *
 * If the file is gone this exits 2 rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const FILE = path.join(ROOT, 'erpnext_enhancements', 'public', 'js', 'global_enhancements', 'page_search.js');

if (!fs.existsSync(FILE)) {
	console.error(
		'MARKERS NOT FOUND: ' +
			path.relative(process.cwd(), FILE) +
			' does not exist. page_search.js was moved or removed; re-derive these checks ' +
			'deliberately rather than deleting them.'
	);
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, 'utf8');

// Every chord already bound, in frappe.ui.keys.get_key's spelling (lower case; modifiers
// prefixed ctrl, then shift, then alt). Read from the v16.50.0 tags of frappe and erpnext.
const FRAPPE_V16_50_KEYS = [
	// frappe/public/js/frappe/ui/keyboard.js
	'ctrl+s', 'ctrl+k', 'ctrl+g', 'shift+/', 'escape', 'esc', 'enter', 'ctrl+down', 'ctrl+up',
	'shift+ctrl+r', 'shift+t',
	// desk.js (theme), ui/sidebar/sidebar.js, ui/sidebar/dock.js
	'shift+ctrl+g', 'ctrl+/', 'shift+ctrl+/',
	// form/form.js (record nav, redo, print, grid rows)
	'shift+ctrl+>', 'shift+ctrl+<', 'shift+ctrl+z', 'ctrl+p', 'up arrow', 'down arrow',
	'ctrl+shift+up', 'ctrl+shift+down', 'shift+alt+down',
	// form/toolbar.js page-menu items, list_view.js, report_view.js
	'ctrl+e', 'ctrl+j', 'shift+ctrl+d', 'shift+r', 'ctrl+z', 'ctrl+y', 'ctrl+b',
	'down', 'up', 'shift+down', 'shift+up', 'space',
	// views/kanban_v2, views/treeview, print_format_builder, form/layout.js
	'alt+hover', 'ctrl+i', 'ctrl+u', 'tab', 'shift+tab',
	// views/workspace/workspace.js: every bare letter and shift+letter on a workspace
	...'abcdefghijklmnopqrstuvwxyz'.split('').flatMap((l) => [l, 'shift+' + l]),
	// ui/alt_keyboard_shortcuts.js: alt+<letter> menu accelerators (stand aside when Shift is held)
	...'abcdefghijklmnopqrstuvwxyz'.split('').map((l) => 'alt+' + l),
];
const ERPNEXT_V16_50_KEYS = [
	// selling/page/point_of_sale
	'ctrl+enter', 'ctrl+d', 'ctrl+e', 'ctrl+i', 'ctrl+g', 'ctrl+p', 'ctrl+backspace',
	'shift+ctrl+backspace', 'ctrl+>', 'escape', 'tab',
	...'abcdefghijklmnopqrstuvwxyz0123456789'.split('').map((l) => 'ctrl+' + l),
];
const APP_KEYS = [
	// public/js/global_enhancements/triton_widget.js: Alt+T, and it does not exclude Shift
	'alt+t', 'alt+shift+t',
	// the planners' own shortcut() (project_planner.js, maintenance_planner.js): bare keys only
	't', '/', '?', '1', '2', '3', 'left', 'right', 'ctrl+z',
];
// Browser chords a page cannot reliably take: Chrome's Shift+Alt+T (toolbar), Shift+Alt+I
// (feedback) and Shift+Alt+A (pane focus).
const BROWSER_KEYS = ['alt+shift+t', 'alt+shift+i', 'alt+shift+a'];
const TAKEN = new Set([...FRAPPE_V16_50_KEYS, ...ERPNEXT_V16_50_KEYS, ...APP_KEYS, ...BROWSER_KEYS]);

const EXPECTED_SHORTCUTS = {
	'project-planner': 'alt+shift+p',
	'maintenance-planner': 'alt+shift+m',
	'maintenance-day-board': 'alt+shift+d',
	'rental-planner': 'alt+shift+r',
	'plan-a-trip': 'alt+shift+j',
	'/stock-scan': 'alt+shift+s',
};

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

const ALL_PAGES = {
	'project-planner': { title: 'Project Planner' },
	'maintenance-planner': { title: 'Maintenance Planner' },
	'maintenance-day-board': { title: 'Maintenance Day Board' },
	'rental-planner': { title: 'Rental Planner' },
	'plan-a-trip': { title: 'Plan a Trip' },
	'inventory-scanner-audit': { title: 'Inventory Scanner Audit' },
	'time-kiosk': { title: 'Time Kiosk' },
	'integrations-health': { title: 'Integrations Health' },
	'device-fleet-dashboard': { title: 'Device Fleet Dashboard' },
	'quickbooks-online-dashboard': { title: 'QuickBooks Online Dashboard' },
	'quickbooks-record-matching': { title: 'QuickBooks Record Matching' },
	'stripe-payments-dashboard': { title: 'Stripe Payments' },
	'opportunity-board': { title: 'Opportunity Board' },
	'sales-pipeline': { title: 'Sales Pipeline' },
	'guided-entry': { title: 'Guided Entry' },
	'visit-wizard': { title: 'Visit Wizard' },
	'visit-review': { title: 'Visit Review' },
	'contract-builder': { title: 'Contract Builder' },
	'inspection-wizard': { title: 'Inspection Wizard' },
	'location-timeline': { title: 'Location Timeline' },
	learn: { title: 'Training' },
	'water-engineering-wizard': { title: 'Water Engineering Wizard' },
	'marketing-performance': { title: 'Marketing Performance' },
	'device-console': { title: 'Device Console' },
	'fleet-maintenance-schedule': { title: 'Fleet Maintenance Schedule' },
	// A Frappe page, never offered by this section.
	'user-profile': { title: 'User Profile' },
};

// ---- a tiny DOM: elements with a tag, attributes, a parent, closest() and is() ----
function el(tag, attrs, parent) {
	const node = {
		nodeType: 1,
		tagName: tag.toUpperCase(),
		attrs: Object.assign({}, attrs || {}),
		parentNode: parent || null,
	};
	// The browser's own rule: contenteditable "", "true" or "plaintext-only", here or inherited.
	Object.defineProperty(node, 'isContentEditable', {
		get() {
			let n = node;
			while (n) {
				const v = n.attrs && n.attrs.contenteditable;
				if (v !== undefined) return v !== 'false';
				n = n.parentNode;
			}
			return false;
		},
	});
	node.closest = function (selector) {
		// page_search.js asks exactly this selector; anything else is a harness bug.
		if (selector.replace(/\s+/g, ' ') !==
			"input, textarea, select, [contenteditable]:not([contenteditable='false'])") {
			throw new Error('unexpected selector ' + selector);
		}
		let n = node;
		while (n) {
			const t = (n.tagName || '').toLowerCase();
			if (t === 'input' || t === 'textarea' || t === 'select') return n;
			if (n.attrs && n.attrs.contenteditable !== undefined && n.attrs.contenteditable !== 'false') return n;
			n = n.parentNode;
		}
		return null;
	};
	return node;
}
// Frappe's add_shortcut test: $(document.activeElement).is("input, select, textarea, [contenteditable=true]")
function frappe_is_input_focused(node) {
	if (!node) return false;
	const t = (node.tagName || '').toLowerCase();
	if (t === 'input' || t === 'select' || t === 'textarea') return true;
	return !!(node.attrs && node.attrs.contenteditable === 'true');
}

// Frappe v16.50 frappe.ui.keys.get_key, verbatim in effect.
const KEY_MAP = { 13: 'enter', 27: 'escape', 191: '/' };
'abcdefghijklmnopqrstuvwxyz'.split('').forEach((l, i) => (KEY_MAP[65 + i] = l));
function get_key(e) {
	let key = KEY_MAP[e.keyCode] || String.fromCharCode(e.keyCode);
	if (e.ctrlKey || e.metaKey) key = 'ctrl+' + key;
	if (e.shiftKey) key = 'shift+' + key;
	if (e.altKey) key = 'alt+' + key;
	return key.toLowerCase();
}

function makeWorld(options) {
	options = options || {};
	const calls = { set_route: [], assign: [], searchable: [], hidden: 0, shortcuts: [] };
	const handlers = {};
	const body = el('body');
	const document = { activeElement: body };
	let appReady = null;

	const $ = function (target) {
		return {
			on(event, fn) {
				if (target === document && event === 'app_ready') appReady = fn;
			},
		};
	};

	class AwesomeBar {
		build_options(txt) {
			if (options.frappe_build_throws) throw new Error('frappe build failed');
			// Stand-ins for Frappe's own rows: a DocType and its own Page row for Project Planner.
			const out = [
				{ type: 'DocType', label: 'Project', value: 'Project', index: 265, route: ['List', 'Project'] },
			];
			if (/plan/i.test(txt)) {
				out.push({
					type: 'Page',
					label: 'Open Project Planner',
					value: 'Open Project Planner',
					index: 257,
					route: ['project-planner'],
				});
			}
			return out;
		}
	}

	class SearchDialog {
		constructor() {
			this.searches = { global_search: { id: 'g' }, tags: { id: 't' } };
			this.search = this.searches.global_search;
			this.global_doctype_filter = '';
			this.rendered = null;
		}
		parse_results(result_sets, keyword) {
			this.rendered = result_sets;
		}
	}

	const frappe = {
		boot: {
			page_info: options.page_info || ALL_PAGES,
			user: { roles: options.roles || ['System Manager'] },
		},
		user_roles: options.roles || ['System Manager'],
		set_route: (...args) => calls.set_route.push(args),
		search: {
			utils: options.no_utils
				? undefined
				: { make_function_searchable: (fn, label) => calls.searchable.push({ fn, label }) },
		},
		ui: {
			keys: options.no_keys
				? undefined
				: {
						add_shortcut(opts) {
							calls.shortcuts.push(opts);
							const { shortcut, action, condition, ignore_inputs } = opts;
							// Frappe v16.50's handler, in effect.
							const handler = (e) => {
								if (frappe_is_input_focused(document.activeElement) && !ignore_inputs) return;
								if (condition && !condition()) return;
								const out = action(e);
								if (out || out === undefined) e.preventDefault();
							};
							handlers[shortcut] = [handler];
						},
						get_shortcut_label: (s) =>
							s.split('+').map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join('+'),
					},
		},
	};
	if (!options.no_awesomebar) frappe.search.AwesomeBar = AwesomeBar;
	if (!options.no_searchdialog) frappe.search.SearchDialog = SearchDialog;
	frappe.searchdialog = {
		search: { search_dialog: { hide: () => (calls.hidden += 1) } },
	};

	const context = {
		frappe,
		jQuery: $,
		$,
		document,
		location: { assign: (url) => calls.assign.push(url) },
		cur_dialog: null,
		console,
	};
	context.window = context;
	context.globalThis = context;
	vm.createContext(context);
	vm.runInContext(SOURCE, context, { filename: FILE });

	// The event frappe.ui.keys hands a handler is jQuery's: jQuery 3.7.1 copies keyCode, the
	// modifier flags and target onto it, but NOT `repeat`, which stays on originalEvent only.
	function press(keyCode, mods, target) {
		mods = Object.assign({}, mods || {});
		const repeat = !!mods.repeat;
		delete mods.repeat;
		const native = Object.assign(
			{ keyCode, altKey: false, shiftKey: false, ctrlKey: false, metaKey: false, repeat },
			mods
		);
		const e = {
			keyCode: native.keyCode,
			altKey: native.altKey,
			shiftKey: native.shiftKey,
			ctrlKey: native.ctrlKey,
			metaKey: native.metaKey,
			originalEvent: native,
		};
		e.target = native.target = target || document.activeElement;
		e.defaultPrevented = false;
		e.preventDefault = () => (e.defaultPrevented = true);
		const list = handlers[get_key(e)] || [];
		list.forEach((h) => h(e));
		return e;
	}

	return {
		context,
		frappe,
		calls,
		handlers,
		document,
		body,
		AwesomeBar,
		SearchDialog,
		api: context.erpnext_enhancements && context.erpnext_enhancements.page_search,
		ready() {
			if (appReady) appReady();
			return appReady !== null;
		},
		press,
	};
}

function titles(hits) {
	return hits.map((h) => h.entry.title);
}

// ------------------------------------------------------------------ 1. matching
console.log('matching');
{
	const w = makeWorld();
	check(w.api && typeof w.api.match === 'function', 'the module exposes erpnext_enhancements.page_search');
	const entries = w.api.resolve_entries(w.frappe);
	const find = (q) => titles(w.api.match(q, entries));

	const planner = find('planner');
	check(
		['Project Planner', 'Maintenance Planner', 'Rental Planner'].every((t) => planner.includes(t)),
		'"planner" finds Project, Maintenance and Rental Planner (' + planner.join(', ') + ')'
	);
	check(find('kiosk')[0] === 'Time Kiosk', '"kiosk" finds Time Kiosk (the /kiosk redirect page)');
	const scan = find('scan');
	check(
		scan.includes('Stock Scan') && scan.includes('Inventory Scanner Audit'),
		'"scan" finds Stock Scan and Inventory Scanner Audit'
	);
	check(find('trip')[0] === 'Plan a Trip', '"trip" finds Plan a Trip first');
	check(find('day board')[0] === 'Maintenance Day Board', '"day board" finds Maintenance Day Board first');
	check(find('Day-Board')[0] === 'Maintenance Day Board', 'punctuation and case do not matter ("Day-Board")');
	check(find('rental')[0] === 'Rental Planner', '"rental" finds Rental Planner first');
	const health = find('health');
	check(
		health[0] === 'Integrations Health' &&
			['Device Fleet Dashboard', 'QuickBooks Online Dashboard', 'Stripe Payments'].every((t) => health.includes(t)),
		'"health" finds Integrations Health first, then the other health dashboards'
	);
	for (const q of ['qbo', 'QBO', 'qb']) {
		const r = find(q);
		check(
			r.length === 2 && r.includes('QuickBooks Online Dashboard') && r.includes('QuickBooks Record Matching'),
			'"' + q + '" (an acronym full-text search cannot see) finds both QuickBooks pages'
		);
	}
	check(find('project')[0] === 'Project Planner', 'a title word finds its page ("project")');
	check(find('maint').includes('Maintenance Planner'), 'a title prefix finds its page ("maint")');
	check(!find('an').includes('Plan a Trip'), 'two letters never match mid-word ("an" is not "Plan a Trip")');
	check(find('x').length === 0 && find('').length === 0, 'one letter or nothing finds nothing');
	check(find('zzzz').length === 0, 'an unknown word finds nothing');
	check(!find('user').includes('User Profile'), "Frappe's own pages are not part of this section");

	// Exact alias outranks a title prefix: "board" is an alias of both boards.
	const board = w.api.match('board', entries);
	check(
		board.length >= 2 && board[0].score === board[1].score,
		'an exact alias scores alike across pages ("board": ' + titles(board).slice(0, 2).join(', ') + ')'
	);
	const qbo = w.api.match('qbo', entries)[0];
	check(qbo.score > 156, 'an exact acronym outranks a fuzzy DocType hit for it (' + qbo.score + ' > 156)');
	const proj = w.api.match('project', entries)[0];
	check(proj.score < 265, 'a title word does not outrank the exact "Project" DocType (' + proj.score + ' < 265)');
}

// ------------------------------------------------------------------ 2. role filtering
console.log('role filtering');
{
	const limited = {
		'project-planner': ALL_PAGES['project-planner'],
		'plan-a-trip': ALL_PAGES['plan-a-trip'],
		'time-kiosk': ALL_PAGES['time-kiosk'],
	};
	const w = makeWorld({ page_info: limited, roles: ['Employee', 'Projects User'] });
	const entries = w.api.resolve_entries(w.frappe);
	check(
		JSON.stringify(entries.map((e) => e.key).sort()) ===
			JSON.stringify(['plan-a-trip', 'project-planner', 'time-kiosk']),
		'only pages in frappe.boot.page_info, and no Stock Scan without a scan role'
	);
	check(
		JSON.stringify(titles(w.api.match('planner', entries))) === JSON.stringify(['Project Planner']),
		'"planner" finds only the planner this user may open'
	);
	check(w.api.match('scan', entries).length === 0, '"scan" finds nothing for a user who cannot scan');
	check(w.ready(), 'installs on app_ready');
	check(
		JSON.stringify(w.calls.shortcuts.map((s) => s.shortcut).sort()) ===
			JSON.stringify(['alt+shift+j', 'alt+shift+p']),
		'shortcuts only for pages the user can open (' + w.calls.shortcuts.map((s) => s.shortcut).join(', ') + ')'
	);
	check(w.calls.searchable.length === 2, 'and "Go to" commands only for those');

	const clerk = makeWorld({ page_info: {}, roles: ['Inventory Clerk'] });
	check(
		JSON.stringify(clerk.api.resolve_entries(clerk.frappe).map((e) => e.key)) === JSON.stringify(['/stock-scan']),
		'an Inventory Clerk gets Stock Scan from its roles, with no Desk page'
	);
	const title = makeWorld({ page_info: { 'project-planner': { title: 'Crew Planner' } } });
	check(
		title.api.resolve_entries(title.frappe)[0].title === 'Crew Planner',
		'titles come from the Page record (page_info), not from this file'
	);
}

// ------------------------------------------------------------------ 3. routing
console.log('routing');
{
	const w = makeWorld();
	w.ready();
	const entries = w.api.resolve_entries(w.frappe);
	const pp = entries.find((e) => e.key === 'project-planner');
	const ss = entries.find((e) => e.key === '/stock-scan');
	w.api.open_entry(pp, w.frappe);
	check(
		JSON.stringify(w.calls.set_route) === JSON.stringify([['project-planner']]),
		'a Desk page opens through frappe.set_route(<page name>)'
	);
	w.api.open_entry(ss, w.frappe);
	check(
		JSON.stringify(w.calls.assign) === JSON.stringify(['/stock-scan']) && w.calls.set_route.length === 1,
		'a www page opens with a real navigation (location.assign), not set_route'
	);

	const rows = w.api.awesomebar_options('scan', w.frappe);
	const stock = rows.find((r) => r.match === 'Stock Scan');
	const audit = rows.find((r) => r.match === 'Inventory Scanner Audit');
	check(
		stock && JSON.stringify(stock.route) === JSON.stringify(['/stock-scan']),
		"the awesomebar row for Stock Scan is an in-app path, which Frappe's select handler navigates to"
	);
	check(
		audit && JSON.stringify(audit.route) === JSON.stringify(['inventory-scanner-audit']) && audit.type === 'Page',
		'a Desk page row routes to its page name and is typed "Page"'
	);
	check(stock.description.indexOf('Alt+Shift+S') !== -1, 'a row with a shortcut shows it (' + stock.description + ')');
	const kiosk = w.api.awesomebar_options('kiosk', w.frappe)[0];
	check(/<b>Time Kiosk<\/b>/.test(kiosk.label), 'the row label bolds the title');

	const set = w.api.nav_set('qbo', w.frappe);
	check(set && set.title === 'Pages' && set.fetch_type === 'Nav' && set.results.length === 2, 'the search dialog gets a "Pages" Nav section for "qbo"');
	set.results.find((r) => r.match === 'QuickBooks Record Matching').onclick();
	check(w.calls.hidden === 1, 'a "Pages" result closes the search dialog');
	check(
		JSON.stringify(w.calls.set_route[w.calls.set_route.length - 1]) === JSON.stringify(['quickbooks-record-matching']),
		'and routes with frappe.set_route'
	);
	check(w.api.nav_set('zzzz', w.frappe) === null, 'no section when nothing matches');

	// Rows carry a real href where the dialog can draw one: SearchDialog.get_link makes it from
	// `route` with frappe.router.make_url, i.e. /desk/<page>.
	const qrows = w.api.nav_set('qbo', w.frappe).results;
	check(
		qrows.every((r) => Array.isArray(r.route) && r.route.length === 1 && /^quickbooks-/.test(r.route[0])),
		'a Desk page row carries its route, so the dialog gives it a /desk/<page> href (new tab works)'
	);
	const srow = w.api.nav_set('scan', w.frappe).results.find((r) => r.match === 'Stock Scan');
	check(srow && srow.route === undefined, 'a www row carries no route (make_url would spell it as a Desk path)');

	// A Ctrl/Cmd/Shift-click belongs to the browser: no routing, no closing.
	const opened = [];
	w.context.open = (url, target) => opened.push([url, target]);
	const before = { routes: w.calls.set_route.length, hidden: w.calls.hidden, assign: w.calls.assign.length };
	w.context.event = { ctrlKey: true, metaKey: false, shiftKey: false };
	qrows[0].onclick();
	check(
		w.calls.set_route.length === before.routes && w.calls.hidden === before.hidden,
		'Ctrl-click on a Desk page row leaves the current tab alone (its href opens the new tab)'
	);
	w.context.event = { ctrlKey: false, metaKey: true, shiftKey: false };
	srow.onclick();
	check(
		JSON.stringify(opened) === JSON.stringify([['/stock-scan', '_blank']]) && w.calls.assign.length === before.assign,
		'Cmd-click on the www row opens it in a new tab itself'
	);
	w.context.event = { ctrlKey: false, metaKey: false, shiftKey: false };
	qrows[0].onclick();
	check(w.calls.set_route.length === before.routes + 1 && w.calls.hidden === before.hidden + 1, 'a plain click still closes the dialog and routes with set_route');
	w.context.event = undefined;
}

// ------------------------------------------------------------------ 4. the awesomebar wrap
console.log('awesomebar and search dialog hooks');
{
	const w = makeWorld();
	w.ready();
	const bar = new w.AwesomeBar();
	const out = bar.build_options('planner');
	const pp = out.filter((o) => JSON.stringify(o.route) === JSON.stringify(['project-planner']));
	check(pp.length === 1 && pp[0].ee_page_search, "Frappe's own Project Planner row is folded into ours: one row");
	check(pp[0].index >= 257, "and keeps Frappe's higher score (" + pp[0].index + ')');
	check(out.some((o) => o.value === 'Project'), "Frappe's other rows are kept");
	for (let i = 1; i < out.length; i++) {
		if ((out[i - 1].index || 0) < (out[i].index || 0)) {
			fail('rows are sorted by score, high first');
			break;
		}
		if (i === out.length - 1) pass('rows are sorted by score, high first');
	}
	const none = bar.build_options('zzzz');
	check(none.length === 1 && none[0].value === 'Project', "Frappe's list comes back as it was when nothing matches");
	const tag = bar.build_options('#planner');
	check(!tag.some((o) => o.ee_page_search), 'nothing is added in "#tag" mode');

	// Install twice: still one wrap.
	w.api.install();
	w.context.frappe._ee_page_search_installed = false;
	w.api.install();
	check(bar.build_options('planner').filter((o) => o.ee_page_search).length === 3, 'a second install does not wrap twice');

	// Anything of ours that throws leaves Frappe's list alone.
	const t = makeWorld();
	t.ready();
	Object.defineProperty(t.frappe.boot, 'page_info', {
		get() {
			throw new Error('boom');
		},
	});
	const kept = new t.AwesomeBar().build_options('planner');
	check(kept.length === 2 && !kept.some((o) => o.ee_page_search), 'an error inside the wrap returns Frappe\'s options unchanged');

	// Missing hook points: nothing is patched, nothing throws.
	const bare = makeWorld({ no_awesomebar: true, no_searchdialog: true, no_keys: true, no_utils: true });
	let threw = false;
	try {
		bare.ready();
	} catch (e) {
		threw = true;
	}
	check(!threw, 'with no AwesomeBar, SearchDialog or frappe.ui.keys, install does nothing and does not throw');
	const nobuild = makeWorld();
	delete nobuild.AwesomeBar.prototype.build_options;
	nobuild.ready();
	check(!nobuild.AwesomeBar.prototype._ee_page_search_patched, 'a renamed build_options is not patched');

	// Search dialog.
	const dlg = new w.SearchDialog();
	dlg.parse_results([{ title: 'Project', results: [{}] }], 'qbo');
	check(dlg.rendered[0].title === 'Pages' && dlg.rendered.length === 2, 'parse_results gets the "Pages" section first');
	dlg.global_doctype_filter = 'Project';
	dlg.parse_results([], 'qbo');
	check(dlg.rendered.length === 0, 'not when the dialog is drilled into one DocType');
	dlg.global_doctype_filter = '';
	dlg.search = dlg.searches.tags;
	dlg.parse_results([], '#qbo');
	check(dlg.rendered.length === 0, 'not in "#tag" mode');
}

// ------------------------------------------------------------------ 5. keys
console.log('shortcuts');
{
	const w = makeWorld();
	w.ready();
	const bound = w.calls.shortcuts;
	const byShortcut = {};
	bound.forEach((s) => (byShortcut[s.shortcut] = s));
	const entries = w.api.resolve_entries(w.frappe);
	for (const [key, chord] of Object.entries(EXPECTED_SHORTCUTS)) {
		const entry = entries.find((e) => e.key === key);
		check(entry && entry.shortcut === chord, key + ' is on ' + chord);
	}
	check(bound.length === 6, 'six shortcuts registered, through frappe.ui.keys.add_shortcut');
	check(new Set(bound.map((s) => s.shortcut)).size === bound.length, 'no two share a chord');
	bound.forEach((s) => {
		check(/^alt\+shift\+[a-z]$/.test(s.shortcut), s.shortcut + " is spelled as frappe's get_key spells it");
		check(!TAKEN.has(s.shortcut), s.shortcut + ' is not bound by Frappe v16.50, ERPNext v16.50, this app or the browser');
		check(typeof s.description === 'string' && /^Go to /.test(s.description), s.shortcut + ' has a help-dialog description: ' + s.description);
		check(!s.ignore_inputs, s.shortcut + ' keeps add_shortcut\'s own input guard (no ignore_inputs)');
		const letter = s.shortcut.slice(-1);
		const code = letter.toUpperCase().charCodeAt(0);
		check(
			get_key({ keyCode: code, altKey: true, shiftKey: true }) === s.shortcut,
			'Alt+Shift+' + letter.toUpperCase() + ' reaches the ' + s.shortcut + ' handler'
		);
	});
	check(
		w.calls.searchable.map((c) => c.label).sort().join('|') ===
			['Go to Maintenance Day Board', 'Go to Maintenance Planner', 'Go to Plan a Trip', 'Go to Project Planner', 'Go to Rental Planner', 'Go to Stock Scan'].join('|'),
		'six "Go to ..." commands in the awesomebar'
	);
	w.calls.searchable.find((c) => c.label === 'Go to Rental Planner').fn();
	check(
		JSON.stringify(w.calls.set_route[w.calls.set_route.length - 1]) === JSON.stringify(['rental-planner']),
		'a "Go to" command routes with frappe.set_route'
	);

	// Firing.
	const P = 'P'.charCodeAt(0);
	const S = 'S'.charCodeAt(0);
	w.calls.set_route.length = 0;
	let e = w.press(P, { altKey: true, shiftKey: true });
	check(
		JSON.stringify(w.calls.set_route) === JSON.stringify([['project-planner']]) && e.defaultPrevented,
		'Alt+Shift+P on the page body opens the Project Planner and takes the key'
	);
	e = w.press(S, { altKey: true, shiftKey: true });
	check(JSON.stringify(w.calls.assign) === JSON.stringify(['/stock-scan']), 'Alt+Shift+S opens Stock Scan');
	w.calls.set_route.length = 0;
	e = w.press(P, { altKey: true });
	check(w.calls.set_route.length === 0, 'Alt+P alone does nothing of ours');
	e = w.press(P, { altKey: true, shiftKey: true, ctrlKey: true });
	check(w.calls.set_route.length === 0, 'AltGr+Shift+P (ctrl+alt) does nothing of ours');

	// ------------------------------------------------------------------ 6. never while typing
	console.log('never while typing');
	const cases = [
		['an input', el('input')],
		['a textarea', el('textarea')],
		['a select', el('select')],
		['contenteditable="true"', el('div', { contenteditable: 'true' })],
		['contenteditable="" (Frappe\'s selector misses it)', el('div', { contenteditable: '' })],
		['contenteditable="plaintext-only"', el('div', { contenteditable: 'plaintext-only' })],
		['a paragraph inside an editor', el('p', {}, el('div', { contenteditable: 'true' }))],
	];
	for (const [label, node] of cases) {
		w.calls.set_route.length = 0;
		w.document.activeElement = node;
		e = w.press(P, { altKey: true, shiftKey: true }, node);
		check(w.calls.set_route.length === 0 && !e.defaultPrevented, 'focus in ' + label + ': nothing fires, key untouched');
	}
	w.document.activeElement = w.body;
	w.calls.set_route.length = 0;
	const editorChild = el('span', {}, el('div', { contenteditable: '' }));
	e = w.press(P, { altKey: true, shiftKey: true }, editorChild);
	check(w.calls.set_route.length === 0, 'a keystroke whose target is inside an editor does not fire');
	e = w.press(P, { altKey: true, shiftKey: true, repeat: true });
	check(w.calls.set_route.length === 0 && !e.defaultPrevented, 'a held-down repeat does not fire');
	w.context.cur_dialog = { display: true };
	e = w.press(P, { altKey: true, shiftKey: true });
	check(w.calls.set_route.length === 0 && !e.defaultPrevented, 'an open dialog keeps the key');
	w.context.cur_dialog = { display: false };
	e = w.press(P, { altKey: true, shiftKey: true });
	check(w.calls.set_route.length === 1, 'a closed dialog does not block it');
}

if (failures) {
	console.error('\n' + failures + ' check(s) failed');
	process.exit(1);
}
console.log('\nall page_search checks passed');
