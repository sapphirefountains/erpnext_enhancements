#!/usr/bin/env node
/**
 * Sidebar memory (sidebar_memory.js, v1.717.0), executed against a stubbed DOM, jQuery, storage
 * and frappe rather than grepped.
 *
 * Nik, 2026-10-22: a sidebar a person hides must stay hidden on every page until they show it
 * again. Every rule below fails silently in production -- nothing throws, a sidebar is simply open
 * again, or a button is missing, or a phone is stranded:
 *
 *   1. The saved choice is on <html> at evaluation, before the desk draws a page, so a hidden
 *      sidebar is never painted and then removed.
 *   2. One click toggles, saves, and re-labels EVERY button already on screen.
 *   3. The button goes on a page only if it has a sidebar this governs (a form's, the Training
 *      rail), and is kept out of sight on a new, unsaved record (its sidebar is hidden).
 *   4. form-refresh / page-change / ee-page-sidebar-ready all add it, and adding it twice
 *      (the form toolbar clears the icon group on refresh) never makes two.
 *   5. Another tab's choice arrives by the storage event: the record-sidebar class follows it, and
 *      the left Desk sidebar is folded or opened to match -- on a desktop only, never a drawer.
 *   6. Storage that throws (private window) never escapes, and the toggle still works in memory.
 *   7. The left sidebar is NOT driven on a route change: frappe restores it from its own key on
 *      every redraw, and a second writer would fight it.
 *   8. ...except in the one case frappe gets wrong: restore_expanded_state() forces the panel open
 *      when the sidebar it drew has no items, over a saved collapse. The wrapper lets the collapse win
 *      on a desktop, never on a phone drawer, and never twice.
 *
 * If a marker no longer resolves this exits 2 rather than passing vacuously.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const FILE = path.join(ROOT, 'erpnext_enhancements', 'public', 'js', 'global_enhancements', 'sidebar_memory.js');

if (!fs.existsSync(FILE)) {
	console.error(
		'MARKERS NOT FOUND: ' +
			path.relative(process.cwd(), FILE) +
			' does not exist. The sidebar memory was moved or removed — re-derive these checks ' +
			'deliberately rather than deleting them.'
	);
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, 'utf8');

const NAV = 'desk-sidebar-collapsed';
const PAGE = 'ee-page-sidebar-hidden';
const CLS = 'ee-page-sidebar-hidden';
const BTN = 'ee-page-sidebar-toggle';

let failures = 0;
function check(condition, message) {
	if (condition) console.log('  ok    ' + message);
	else {
		failures += 1;
		console.error('  FAIL  ' + message);
	}
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
	};
	return storage;
}

// A button is the only element the module creates. It needs jQuery's attr/toggleClass/on/appendTo.
function makeButton(world) {
	const classes = new Set();
	const button = {
		attrs: {},
		handlers: {},
		parent: null,
		classes,
		length: 1,
		attr(map) {
			Object.assign(button.attrs, map);
			return button;
		},
		toggleClass(name, on) {
			(on === undefined ? !classes.has(name) : on) ? classes.add(name) : classes.delete(name);
			return button;
		},
		hasClass: (name) => classes.has(name),
		on(event, fn) {
			button.handlers[event] = fn;
			return button;
		},
		appendTo(group) {
			group.buttons.push(button);
			button.parent = group;
			world.buttons.push(button);
			return button;
		},
		click: () => button.handlers.click && button.handlers.click(),
		each(fn) {
			fn.call(button, 0, button);
		},
	};
	return button;
}

// frappe's Page, as far as the module sees it: an icon group, and a sidebar with some content.
function makePage({ governed = true, hideSidebar = false } = {}) {
	const iconClasses = new Set(['hide']);
	const group = {
		buttons: [],
		find: (sel) => {
			const matches = group.buttons.filter((b) => sel === '.' + BTN);
			const found = matches.slice();
			found.length = matches.length;
			found.toggleClass = (...a) => matches.forEach((b) => b.toggleClass(...a));
			found.attr = (...a) => matches.forEach((b) => b.attr(...a));
			return found;
		},
		removeClass: (name) => {
			iconClasses.delete(name);
			return group;
		},
		hidden: () => iconClasses.has('hide'),
	};
	const sidebarClasses = new Set(hideSidebar ? ['hide-sidebar'] : []);
	const sidebar = {
		length: 1,
		find: (sel) => {
			const out = governed && /form-sidebar|tn-rail/.test(sel) ? [{}] : [];
			return out;
		},
		hasClass: (name) => sidebarClasses.has(name),
		classes: sidebarClasses,
	};
	return { icon_group: group, sidebar };
}

// Evaluates the module once, as one page load would.
function load({ storage = makeStorage(), sidebar, mobile = false, noStorageEvents = false, SidebarClass } = {}) {
	const world = { buttons: [] };
	const events = {};
	const winHandlers = {};
	const warnings = [];
	const htmlClasses = new Set();
	const frappe = {
		app: sidebar ? { sidebar } : undefined,
		container: { page: { page: null } },
		is_mobile: () => mobile,
		ui: SidebarClass ? { Sidebar: SidebarClass } : undefined,
	};
	function $(arg) {
		if (typeof arg === 'string') {
			if (arg.startsWith('<')) return makeButton(world);
			if (arg === '.' + BTN) {
				const all = world.buttons.slice();
				all.length = all.length;
				all.each = (fn) => all.forEach((b, i) => fn.call(b, i, b));
				return all;
			}
			throw new Error('unexpected selector ' + arg);
		}
		if (arg && typeof arg.attr === 'function') return arg; // $(this) inside .each
		return {
			on(event, fn) {
				(events[event] = events[event] || []).push(fn);
			},
		};
	}
	const context = {
		$,
		frappe,
		localStorage: storage,
		document: {
			documentElement: {
				classList: {
					toggle: (name, on) => (on ? htmlClasses.add(name) : htmlClasses.delete(name)),
					contains: (name) => htmlClasses.has(name),
				},
			},
		},
		console: { warn: (...a) => warnings.push(a), log() {}, error() {} },
		__: (s) => s,
	};
	context.window = context;
	if (!noStorageEvents) {
		context.addEventListener = (name, fn) => (winHandlers[name] = fn);
	}
	vm.createContext(context);
	let threw = null;
	try {
		vm.runInContext(SOURCE, context, { filename: 'sidebar_memory.js' });
	} catch (e) {
		threw = e;
	}
	return {
		threw,
		warnings,
		world,
		storage,
		frappe,
		html: () => htmlClasses.has(CLS),
		fire(event, ...args) {
			for (const fn of events[event] || []) fn({}, ...args);
		},
		storageEvent(key) {
			winHandlers.storage && winHandlers.storage({ key });
		},
		handlers: events,
		api: () => context.erpnext_enhancements && context.erpnext_enhancements.sidebar_memory,
	};
}

// A frappe v16.50.0 Sidebar: close()/open() save the key; a drawer neither reads nor writes it.
function makeSidebar(storage, { expanded = true, drawer = false } = {}) {
	const sb = {
		wrapper: {},
		sidebar_expanded: expanded,
		closed: 0,
		opened: 0,
		panel_can_close: () => drawer,
		close() {
			sb.closed += 1;
			sb.sidebar_expanded = false;
			if (!drawer) storage.setItem(NAV, '1');
		},
		open() {
			sb.opened += 1;
			sb.sidebar_expanded = true;
			if (!drawer) storage.setItem(NAV, '0');
		},
	};
	return sb;
}

console.log('sidebar memory — a hidden sidebar stays hidden everywhere until shown again\n');

// --- 1. Before the first paint -------------------------------------------------------------
{
	const hidden = load({ storage: makeStorage({ [PAGE]: '1' }) });
	check(!hidden.threw, 'evaluates without throwing before the desk exists');
	check(hidden.html(), 'a saved "hidden" puts the class on <html> at evaluation, before any page is drawn');
	const shown = load({ storage: makeStorage({ [PAGE]: '0' }) });
	check(!shown.html(), 'a saved "shown" ("0") leaves the class off');
	check(!load().html(), 'a browser that never chose starts with the sidebar shown');
}

// --- 2. One click toggles, saves and re-labels every button ---------------------------------
{
	const page = load();
	const a = makePage();
	const b = makePage();
	page.fire('page-change'); // nothing current yet; must not throw
	page.fire('form-refresh', { page: a });
	page.fire('form-refresh', { page: b });
	check(page.world.buttons.length === 2, 'a form page gets a button');
	check(page.world.buttons[0].attrs['aria-expanded'] === 'true', 'it starts expanded when the sidebar is shown');
	page.world.buttons[0].click();
	check(page.storage.getItem(PAGE) === '1', 'one click saves "hidden"');
	check(page.html(), 'and puts the class on <html> at once');
	check(
		page.world.buttons.every(
			(x) => x.attrs['aria-expanded'] === 'false' && x.attrs.title === 'Show sidebar' && x.attrs['aria-label'] === 'Sidebar'
		),
		'every button on screen is re-labelled, not just the one clicked, with a constant accessible name'
	);
	page.world.buttons[1].click();
	check(page.storage.getItem(PAGE) === '0' && !page.html(), 'a second click, from another page, shows it again and saves that');
}

// --- 3. Which pages get the button ---------------------------------------------------------
{
	const page = load();
	const bare = makePage({ governed: false });
	page.fire('form-refresh', { page: bare });
	check(page.world.buttons.length === 0, 'a page whose sidebar holds nothing this governs gets no button');

	const fresh = makePage({ hideSidebar: true });
	page.fire('form-refresh', { page: fresh });
	check(page.world.buttons.length === 1, 'a new, unsaved record still gets the button...');
	check(page.world.buttons[0].classes.has('hide'), '...kept out of sight while its sidebar is hidden');
	fresh.sidebar.classes.delete('hide-sidebar'); // saved: frappe un-hides the sidebar
	page.fire('form-refresh', { page: fresh });
	check(!page.world.buttons[0].classes.has('hide'), 'and shown once the record is saved');

	page.fire('form-refresh', {});
	check(page.warnings.length === 0 && page.world.buttons.length === 1, 'a form event with no page is ignored');
}

// --- 4. All three events add it, and never twice -------------------------------------------
{
	const page = load();
	const training = makePage();
	page.fire('ee-page-sidebar-ready', training);
	check(page.world.buttons.length === 1, 'the Training rail mounting adds the button');
	page.fire('ee-page-sidebar-ready', training);
	page.fire('form-refresh', { page: training });
	check(page.world.buttons.length === 1, 'the form toolbar refreshing again never makes a second one');
	check(!training.icon_group.hidden(), 'and the icon group is un-hidden so the button can be seen');

	page.frappe.container.page.page = makePage();
	page.fire('page-change');
	check(page.world.buttons.length === 2, 'page-change adds it to the page now on screen');
}

// --- 5. Another tab's choice -----------------------------------------------------------------
{
	const storage = makeStorage();
	const sidebar = makeSidebar(storage);
	const page = load({ storage, sidebar });
	page.fire('form-refresh', { page: makePage() });
	storage.setItem(PAGE, '1');
	page.storageEvent(PAGE);
	check(page.html(), 'the other tab hid the record sidebar: this tab hides it');
	check(page.api().is_hidden(), 'and its in-memory state follows');
	check(page.world.buttons[0].attrs['aria-expanded'] === 'false', 'and the button here is re-labelled');

	storage.setItem(NAV, '1');
	page.storageEvent(NAV);
	check(sidebar.closed === 1 && !sidebar.sidebar_expanded, 'the other tab folded the left sidebar: this tab folds it');
	page.storageEvent(NAV);
	check(sidebar.closed === 1, 'a repeat of the same value does nothing');
	storage.setItem(NAV, '0');
	page.storageEvent(NAV);
	check(sidebar.opened === 1 && sidebar.sidebar_expanded, 'and opens it when the other tab opened it');

	page.storageEvent('some_other_key');
	check(sidebar.closed === 1 && sidebar.opened === 1, 'an unrelated key changes nothing');
	storage.setItem(PAGE, '0');
	storage.setItem(NAV, '1');
	page.storageEvent(null);
	check(!page.html() && sidebar.closed === 2, 'a cleared store (key null) re-reads both');
}

// --- 6. A phone drawer is never driven ---------------------------------------------------------
{
	const storage = makeStorage({ [NAV]: '1' });
	const sidebar = makeSidebar(storage, { drawer: true });
	const page = load({ storage, sidebar });
	page.storageEvent(NAV);
	check(sidebar.closed === 0 && sidebar.opened === 0, 'a drawer is neither folded nor opened by another tab');
	check(sidebar.sidebar_expanded, 'and stays as it was');
}

// --- 7. No sidebar object yet: the desk is still booting -------------------------------------
{
	const storage = makeStorage({ [NAV]: '1' });
	const page = load({ storage });
	page.storageEvent(NAV);
	check(!page.threw && page.warnings.length === 0, 'a storage event before frappe.app exists is a no-op');
	page.frappe.app = { sidebar: { wrapper: null } };
	page.storageEvent(NAV);
	check(page.warnings.length === 0, 'and so is one before the sidebar has drawn');
}

// --- 8. Storage that throws -------------------------------------------------------------------
{
	const storage = makeStorage({ [PAGE]: '1' });
	storage.throws = true;
	const page = load({ storage });
	check(!page.threw, 'refusing storage throws nothing at evaluation');
	check(!page.html(), 'and the sidebar simply starts shown');
	const p = makePage();
	page.fire('form-refresh', { page: p });
	let threw = null;
	try {
		page.world.buttons[0].click();
	} catch (e) {
		threw = e;
	}
	check(!threw, 'a click with storage refused throws nothing');
	check(page.html() && page.api().is_hidden(), 'and the toggle still works for the life of the page');
	page.storageEvent(PAGE);
	check(!page.threw, 'a storage event with storage refused throws nothing');
}

// --- 9. A broken sidebar is a warning, not a boot failure --------------------------------------
{
	const storage = makeStorage({ [NAV]: '1' });
	const sidebar = makeSidebar(storage);
	sidebar.close = () => {
		throw new Error('wrapper detached');
	};
	const page = load({ storage, sidebar });
	page.storageEvent(NAV);
	check(!page.threw && page.warnings.length === 1, 'a close() that throws is caught and logged');
}

// --- 10. A route change must not drive the left sidebar -----------------------------------------
{
	const storage = makeStorage({ [NAV]: '1' });
	const sidebar = makeSidebar(storage, { expanded: true }); // e.g. frappe forced it open for an empty workspace
	const page = load({ storage, sidebar });
	page.frappe.container.page.page = makePage();
	page.fire('page-change');
	page.fire('form-refresh', { page: makePage() });
	check(sidebar.closed === 0 && sidebar.opened === 0, 'page-change and form-refresh never fold or open the left sidebar');
}

// --- 11. An empty sidebar must not override a saved collapse -------------------------------------
// frappe's restore_expanded_state() forces the panel open when the sidebar it drew has no items,
// without saving. Rebuilt here the way sidebar.js v16.50.0 has it.
function makeSidebarClass(storage, { drawer = false } = {}) {
	class Sidebar {
		constructor(items) {
			this.sidebar_items = items;
			this.sidebar_expanded = true;
			this.applied = 0;
		}
		panel_can_close() {
			return drawer;
		}
		load_expanded_state() {
			this.sidebar_expanded = !drawer && storage.getItem(NAV) !== '1';
		}
		apply_expanded_state() {
			this.applied += 1;
		}
		restore_expanded_state() {
			this.load_expanded_state();
			if (this.sidebar_items.length === 0) this.sidebar_expanded = true;
			this.apply_expanded_state();
		}
	}
	return Sidebar;
}
{
	const storage = makeStorage({ [NAV]: '1' });
	const Sidebar = makeSidebarClass(storage);
	const page = load({ storage, SidebarClass: Sidebar });
	check(Sidebar.prototype._ee_restore_patched === true, 'the restore wrapper is installed at evaluation when the desk class exists');
	const empty = new Sidebar([]);
	empty.restore_expanded_state();
	check(!empty.sidebar_expanded, 'an empty sidebar no longer pops open over a saved collapse');
	check(empty.applied === 2, 'and the collapse is drawn (frappe drew once, we draw the fold)');
	const full = new Sidebar([{}]);
	full.restore_expanded_state();
	check(!full.sidebar_expanded && full.applied === 1, 'a sidebar with items is left exactly as frappe restored it, not drawn twice');
	storage.setItem(NAV, '0');
	const open = new Sidebar([]);
	open.restore_expanded_state();
	check(open.sidebar_expanded, 'with no collapse saved an empty sidebar is still forced open, as before');
	check(storage.getItem(NAV) === '0', 'and nothing is written to the key by the wrapper');
	load({ storage, SidebarClass: Sidebar });
	const again = new Sidebar([{}]);
	again.restore_expanded_state();
	check(again.applied === 1, 'evaluating the module twice does not wrap the method twice');
	void page;
}
{
	const storage = makeStorage({ [NAV]: '1' });
	const Sidebar = makeSidebarClass(storage, { drawer: true });
	load({ storage, SidebarClass: Sidebar });
	const s = new Sidebar([]);
	s.restore_expanded_state();
	check(s.sidebar_expanded && s.applied === 1, 'a phone drawer is left as frappe has it');
}
{
	// The class is not defined when the bundle evaluates: patched on app_ready instead.
	const storage = makeStorage({ [NAV]: '1' });
	const page = load({ storage });
	check(!page.threw && (page.handlers.app_ready || []).length === 1, 'no desk class yet: one app_ready retry is registered');
	const Sidebar = makeSidebarClass(storage);
	page.frappe.ui = { Sidebar };
	page.fire('app_ready');
	const s = new Sidebar([]);
	s.restore_expanded_state();
	check(!s.sidebar_expanded, 'and the retry installs the wrapper');
	page.fire('app_ready');
	check(Sidebar.prototype._ee_restore_patched === true, 'a second app_ready is harmless');
}
{
	const storage = makeStorage({ [NAV]: '1' });
	const Sidebar = makeSidebarClass(storage);
	Sidebar.prototype.apply_expanded_state = function () {
		this.applied += 1;
		if (this.applied > 1) throw new Error('detached');
	};
	const page = load({ storage, SidebarClass: Sidebar });
	const s = new Sidebar([]);
	let threw = null;
	try {
		s.restore_expanded_state();
	} catch (e) {
		threw = e;
	}
	check(!threw && page.warnings.length === 1, 'a failure while folding is a warning, never a broken route');
}

console.log('');
if (failures) {
	console.error(failures + ' check(s) failed');
	process.exit(1);
}
console.log('all checks passed');
