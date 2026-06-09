// Auto-collapse sidebar.
//
// Targets: every Desk form (any "Form" route).
// Loaded via: hooks.py `app_include_js` (global, all desk pages).
//
// Collapses the form's right sidebar by default on small/zoomed-in screens,
// where it otherwise squeezes the main form content. Acts only on initial
// load of each form route so the user's manual toggle afterwards is respected.

(function () {
	// window.innerWidth shrinks as the user zooms in, so this single check
	// also covers the "browser zoom is high" case without a separate test.
	const COLLAPSE_BREAKPOINT = 1400;

	let last_checked_route = null;

	const try_collapse_sidebar = (attempts_left) => {
		const $sidebar = $(".layout-side-section");
		const $toggle_btn = $(".sidebar-toggle-btn, .sidebar-toggle").first();

		if (!$sidebar.length || !$toggle_btn.length) {
			if (attempts_left > 0) {
				setTimeout(() => try_collapse_sidebar(attempts_left - 1), 150);
			}
			return;
		}

		const is_collapsed =
			$sidebar.hasClass("collapsed") || $sidebar.hasClass("hide") || !$sidebar.is(":visible");

		if (!is_collapsed) {
			$toggle_btn.trigger("click");
		}
	};

	const maybe_collapse_sidebar_on_load = () => {
		const route = frappe.get_route();
		if (!route || route[0] !== "Form") {
			return;
		}
		if (window.innerWidth >= COLLAPSE_BREAKPOINT) {
			return;
		}

		const route_key = route.join("/");
		if (last_checked_route === route_key) {
			return;
		}
		last_checked_route = route_key;

		try_collapse_sidebar(20);
	};

	frappe.router.on("change", () => {
		setTimeout(maybe_collapse_sidebar_on_load, 300);
	});
})();
