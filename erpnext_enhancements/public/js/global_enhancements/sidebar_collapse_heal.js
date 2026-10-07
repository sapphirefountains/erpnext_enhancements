/**
 * One-time heal for the desk sidebar our old auto-collapse script hid (v1.574.2).
 *
 * Targets: localStorage "desk-sidebar-collapsed" (frappe v16.50.0 ui/sidebar/sidebar.js).
 * Loaded via: erpnext_enhancements.bundle.js (global, every desk page).
 *
 * Until v1.574.1 this app shipped auto_collapse_sidebar.js, which clicked the page-head
 * `.sidebar-toggle-btn` on every form opened in a window under 1400px wide (most laptops,
 * or any zoomed browser), meaning to fold the FORM's own sidebar. In frappe v16.50.0 that
 * button is the only one of its kind and it toggles the DESK's module sidebar
 * (page.js -> frappe.app.sidebar.toggle_width()), which saves the choice in this browser as
 * "desk-sidebar-collapsed" = "1". Beside a pinned Dock -- every user here -- a collapsed
 * sidebar is hidden outright, and the Help menu (Report a Problem, Company Knowledge Base,
 * Design Reviews) goes with it. The form's own sidebar was never folded, so the next form
 * clicked the toggle again, flipping the panel back, and so on.
 *
 * The script is deleted; this clears what it left behind, ONCE per browser. It cannot tell a
 * collapse our script made from one the person chose, so it never runs a second time: the
 * first pass sets DONE_KEY whether or not there was anything to clear, and from then on a
 * collapse (Ctrl+/, or the panel icon by the page title) sticks as frappe intends.
 *
 * Two passes, the same shape as sidebar_pref_heal.js:
 *   - Eager, at evaluation. app_include_js runs before frappe.start_app() (desk.js, on
 *     document ready), so clearing the key here means Sidebar.make_dom() finds no saved
 *     state and draws the panel open; there is nothing to reopen and nothing flickers.
 *   - app_ready, the safety net for a bundle that evaluates after the desk started. Note
 *     that app_ready fires INSIDE the Application constructor, before
 *     `frappe.app = new frappe.Application()` has assigned, so frappe.app.sidebar does not
 *     exist yet at that moment; the reopen waits one tick.
 *
 * Below md the sidebar is a drawer (panel_can_close()) that neither reads nor writes the key
 * and always starts shut. Opening it would cover the page, so the heal never calls open()
 * there.
 */
(function () {
	const COLLAPSED_KEY = "desk-sidebar-collapsed";
	const DONE_KEY = "ee_sidebar_heal_16_50";

	// True when this call cleared a saved collapse. Storage can be missing or refuse (a
	// private window, blocked site data); then nothing was persisted and there is nothing
	// to heal.
	function clear_once() {
		try {
			if (localStorage.getItem(DONE_KEY) === "1") return false;
			const saved = localStorage.getItem(COLLAPSED_KEY);
			if (saved !== null) localStorage.removeItem(COLLAPSED_KEY);
			localStorage.setItem(DONE_KEY, "1");
			return saved === "1";
		} catch (e) {
			return false;
		}
	}

	// Opens the live sidebar if the desk already drew it collapsed. Desktop only.
	function reopen() {
		try {
			const sidebar = window.frappe && frappe.app && frappe.app.sidebar;
			if (!sidebar || !sidebar.wrapper || typeof sidebar.open !== "function") return;
			if (sidebar.sidebar_expanded) return;
			const is_drawer =
				typeof sidebar.panel_can_close === "function"
					? sidebar.panel_can_close()
					: !!(frappe.is_mobile && frappe.is_mobile());
			if (is_drawer) return;
			sidebar.open();
		} catch (e) {
			// Never let a cleanup interfere with desk boot.
			console.warn("sidebar_collapse_heal:", e);
		}
	}

	if (clear_once()) reopen();
	$(document).on("app_ready", function () {
		if (clear_once()) setTimeout(reopen, 0);
	});
})();
