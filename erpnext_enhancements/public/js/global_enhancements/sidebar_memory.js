/**
 * Sidebar memory: a hidden sidebar stays hidden, on every page, until the person shows it again.
 *
 * Targets: the whole desk -- the left Desk sidebar ("menu picker"), the record sidebar on the right
 * of every form, and the Training rail (the left column of the Training pages).
 * Loaded via: erpnext_enhancements.bundle.js (global); the CSS half is desk_enhancements.bundle.css.
 *
 * Nik, 2026-10-22: "if they toggle the sidebar once and then hide the menu picker on the left side,
 * it should globally do that until they change it back." Decided with him the same day: two
 * settings, kept in THIS BROWSER only (localStorage), and a phone ignores both.
 *
 * 1. LEFT DESK SIDEBAR. frappe v16.50.0 already remembers it: Sidebar.close()/open() write
 *    localStorage "desk-sidebar-collapsed", and every redraw of the sidebar (a new workspace, a new
 *    route) reads it back through restore_expanded_state() -- so there is nothing to re-apply on a
 *    route change. Two gaps, both closed here:
 *      a. restore_expanded_state() FORCES the panel open whenever the sidebar it just drew has no
 *         items (a route whose module has no Workspace Sidebar), without saving. So on such a route
 *         the panel pops open over a saved collapse and folds again on the next route with items:
 *         "it resets when I navigate". The wrapper below lets a saved collapse win on a desktop.
 *      b. The desk does not notice that ANOTHER TAB changed the key. This file listens for the
 *         storage event and folds or opens the live sidebar to match.
 *    (Until v1.717.0 an "enforce_sidebar_expanded" in erpnext_enhancements.js also stripped
 *    body.sidebar-collapsed on every class change; it is gone.)
 *
 * 2. RECORD SIDEBAR + TRAINING RAIL. frappe v16.50.0 has no in-page toggle for the form's right-hand
 *    sidebar at all -- only the Desk Settings checkbox, which is server-side and all-or-nothing. This
 *    adds one icon button to the page head of every page that has such a sidebar, and one class on
 *    <html> (`ee-page-sidebar-hidden`) that the stylesheet turns into "hidden". The class is set at
 *    evaluation, before the desk draws its first page, so a hidden sidebar is never painted and then
 *    removed. Because the class is on <html> and not on a page, every form and every Training page
 *    follows it, and so does one opened later.
 *
 * Phones: below 992px the record sidebar is frappe's own responsive layout and the Training rail is a
 * stacked menu, so the stylesheet applies nothing and the button is hidden there. The left sidebar is
 * a drawer below 768px (Sidebar.panel_can_close()), which starts shut and neither reads nor writes
 * the key, so cross-tab sync never touches it.
 *
 * The restore wrapper is installed at evaluation (or on app_ready if the desk class is not defined yet).
 * The button is added on `form-refresh` (the form toolbar clears the page's icon group on refresh, so
 * it is re-added every time, idempotently), on `page-change` (any page that already has a sidebar),
 * and on `ee-page-sidebar-ready`, which the Training rail triggers when it mounts.
 */
(function () {
	const NAV_KEY = "desk-sidebar-collapsed"; // frappe's own; see the note above
	const PAGE_KEY = "ee-page-sidebar-hidden";
	const HTML_CLASS = "ee-page-sidebar-hidden";
	const BUTTON_CLASS = "ee-page-sidebar-toggle";
	// What marks a page's sidebar as one this toggle governs: the form's, or the Training rail.
	const GOVERNED = ".form-sidebar, .tn-rail";

	// Inline so no icon sprite is needed (a name the sprite lacks draws nothing and raises nothing).
	// lucide "panel-right".
	const ICON =
		'<svg class="icon icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
		'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
		'<rect width="18" height="18" x="3" y="3" rx="2"></rect><path d="M15 3v18"></path></svg>';

	// The in-memory copy is what the buttons follow, so a browser that refuses storage (a private
	// window, blocked site data) still toggles for the life of the page.
	const state = { hidden: false };

	function read(key) {
		try {
			return localStorage.getItem(key);
		} catch (e) {
			return null;
		}
	}

	function write(key, value) {
		try {
			localStorage.setItem(key, value);
		} catch (e) {
			// nothing to keep it in; it lasts until the next reload
		}
	}

	function apply_class() {
		const root = document.documentElement;
		if (root && root.classList) root.classList.toggle(HTML_CLASS, state.hidden);
	}

	function label() {
		const text = window.__ ? window.__ : (s) => s;
		return state.hidden ? text("Show sidebar") : text("Hide sidebar");
	}

	// One name, one state: a constant accessible name with aria-expanded says "Sidebar, expanded" or
	// "collapsed". A changing label AND aria-pressed would be read twice ("Show sidebar, pressed").
	function paint(button) {
		const text = window.__ ? window.__ : (s) => s;
		button.attr({
			"aria-expanded": state.hidden ? "false" : "true",
			"aria-label": text("Sidebar"),
			title: label(),
		});
		button.toggleClass("active", state.hidden);
	}

	function paint_all() {
		$("." + BUTTON_CLASS).each(function () {
			paint($(this));
		});
	}

	function set_hidden(hidden) {
		state.hidden = !!hidden;
		write(PAGE_KEY, state.hidden ? "1" : "0");
		apply_class();
		paint_all();
	}

	function has_governed_sidebar(page) {
		return !!(page && page.sidebar && page.sidebar.length && page.sidebar.find(GOVERNED).length);
	}

	// Adds the page-head button if this page has a sidebar the setting governs; keeps it out of sight
	// while the sidebar is (a new, unsaved record has none). Safe to call as often as you like.
	function ensure_toggle(page) {
		try {
			if (!page || !page.icon_group || !has_governed_sidebar(page)) return;
			let button = page.icon_group.find("." + BUTTON_CLASS);
			if (!button.length) {
				button = $(
					'<button type="button" class="text-muted btn btn-default icon-btn ' +
						BUTTON_CLASS +
						'">' +
						ICON +
						"</button>"
				);
				button.on("click", function () {
					set_hidden(!state.hidden);
				});
				button.appendTo(page.icon_group);
			}
			page.icon_group.removeClass("hide");
			button.toggleClass("hide", page.sidebar.hasClass("hide-sidebar"));
			paint(button);
		} catch (e) {
			// Never let a convenience button interfere with a page.
			console.warn("sidebar_memory:", e);
		}
	}

	function current_page() {
		return window.frappe && frappe.container && frappe.container.page && frappe.container.page.page;
	}

	// Another tab folded or opened the left sidebar: do the same here. Desktop only -- below md the
	// sidebar is a drawer that the key does not govern.
	function sync_nav() {
		try {
			const sidebar = window.frappe && frappe.app && frappe.app.sidebar;
			if (!sidebar || !sidebar.wrapper) return;
			if (typeof sidebar.panel_can_close === "function" ? sidebar.panel_can_close() : false) return;
			const collapsed = read(NAV_KEY) === "1";
			if (collapsed === !sidebar.sidebar_expanded) return;
			if (collapsed) sidebar.close();
			else sidebar.open();
		} catch (e) {
			console.warn("sidebar_memory:", e);
		}
	}

	// Wraps Sidebar.restore_expanded_state so an empty sidebar no longer overrides a saved collapse.
	// The original runs first and does everything it did before; only when it ended expanded, on a
	// desktop, with a collapse saved, do we fold it again (drawn, not saved: the key already says so).
	function patch_restore() {
		try {
			const proto = window.frappe && frappe.ui && frappe.ui.Sidebar && frappe.ui.Sidebar.prototype;
			if (!proto || proto._ee_restore_patched || typeof proto.restore_expanded_state !== "function") {
				return !!(proto && proto._ee_restore_patched);
			}
			const original = proto.restore_expanded_state;
			proto.restore_expanded_state = function () {
				const result = original.apply(this, arguments);
				try {
					const drawer = typeof this.panel_can_close === "function" && this.panel_can_close();
					if (
						this.sidebar_expanded &&
						!drawer &&
						read(NAV_KEY) === "1" &&
						typeof this.apply_expanded_state === "function"
					) {
						this.sidebar_expanded = false;
						this.apply_expanded_state();
					}
				} catch (e) {
					console.warn("sidebar_memory:", e);
				}
				return result;
			};
			proto._ee_restore_patched = true;
			return true;
		} catch (e) {
			console.warn("sidebar_memory:", e);
			return false;
		}
	}

	function on_storage(event) {
		// key === null means the whole store was cleared.
		if (event.key === null || event.key === PAGE_KEY) {
			state.hidden = read(PAGE_KEY) === "1";
			apply_class();
			paint_all();
		}
		if (event.key === null || event.key === NAV_KEY) sync_nav();
	}

	state.hidden = read(PAGE_KEY) === "1";
	apply_class();

	// The desk bundle defines frappe.ui.Sidebar before app bundles run; app_ready is the net for an
	// order where it does not. Either way it is patched before any route's sidebar is drawn.
	if (!patch_restore()) $(document).on("app_ready", patch_restore);

	if (typeof window.addEventListener === "function") window.addEventListener("storage", on_storage);
	$(document).on("form-refresh", function (event, frm) {
		ensure_toggle(frm && frm.page);
	});
	$(document).on("page-change", function () {
		ensure_toggle(current_page());
	});
	$(document).on("ee-page-sidebar-ready", function (event, page) {
		ensure_toggle(page);
	});

	window.erpnext_enhancements = window.erpnext_enhancements || {};
	window.erpnext_enhancements.sidebar_memory = { set_hidden, is_hidden: () => state.hidden };
})();
