/**
 * Prune stale desk sidebar preferences.
 *
 * Targets: localStorage "sidebar_item_map" (every desk page).
 * Loaded via: erpnext_enhancements.bundle.js (global).
 *
 * The v16 desk remembers which sidebar to show per route in localStorage
 * ("sidebar_item_map") and trusts it blindly: resolve_sidebar() returns the
 * remembered name without checking it still exists, and entries are only ever
 * appended, never replaced. When a "Workspace Sidebar" doc disappears (a user
 * deletes one of their hand-built sidebars, an app stops shipping one), every
 * browser that remembered it keeps resolving routes to the dead name and the
 * sidebar silently stops switching. Drop every remembered name that isn't in
 * the boot payload so resolution falls through to the live sidebars.
 */
(function () {
	function prune() {
		try {
			const known = window.frappe && frappe.boot && frappe.boot.workspace_sidebar_item;
			if (!known) return;
			const raw = localStorage.getItem("sidebar_item_map");
			if (!raw) return;
			const map = JSON.parse(raw);
			let changed = false;
			for (const entity of Object.keys(map)) {
				const remembered = Array.isArray(map[entity]) ? map[entity] : [];
				const kept = remembered.filter((name) => known[String(name).toLowerCase()]);
				if (kept.length !== remembered.length) {
					changed = true;
					if (kept.length) {
						map[entity] = kept;
					} else {
						delete map[entity];
					}
				}
			}
			if (changed) {
				localStorage.setItem("sidebar_item_map", JSON.stringify(map));
			}
		} catch (e) {
			// Never let preference cleanup interfere with desk boot.
			console.warn("sidebar_pref_heal:", e);
		}
	}

	// Boot JSON is inlined before app_include_js bundles, so an eager pass
	// usually lands before the first sidebar resolution; the app_ready pass
	// is the safety net when this evaluates too early.
	prune();
	$(document).on("app_ready", prune);
})();
