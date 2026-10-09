/*
 * Planner kit entry point (Phase 6A, TASK-2026-02467): the small UI layer both planners share,
 * as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by the two planner pages, with `frappe.require("planner_kit.bundle.js", ...)`; it is
 * deliberately NOT in `app_include_js`, because nothing else on the Desk needs it. A bundle rather
 * than a raw /assets path because raw paths are served immutable for a year with no hash (the
 * "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.planner_kit`:
 *
 *   escape(value)                       the one HTML-escaping helper the kit's renderers use
 *   drawer.open(opts) / current() / close() / element() / route(fn, keep) / navigate(fn)
 *   panel(opts)                         a frappe.ui.FieldGroup in the drawer, Dialog-shaped
 *   toast(message, {action_label, on_action, timeout})
 *   menu({anchor, items})               an anchored menu (6B's right-click menu)
 *   legend(sections, opts)              the help drawer, from a description of what is drawn
 *   hint(key, text, {container})        a first-time tip, dismissed once per user
 *   hover_glow(container, "person")     hovering a name lights up that person's bookings
 *   peeks.person(opts) / peeks.day(opts) the shared quick looks (api/planner_views.py)
 *   glow_key / glow_keys / format.{hours, drive, day_label, add_days}
 *   big_picture                         Phase 6C: card colors, the team capacity strip, saved views, print
 *
 * See "Planner kit" in project_enhancements/README.md for how to use each one, and
 * planner_kit/history.js for how Back closes a drawer without the router reloading the page.
 */

import { escape, safe_color, glow_key, glow_keys, t, hours, drive, day_label, add_days, contact_href } from "./planner_kit/escape.js";
import { create_guard } from "./planner_kit/history.js";
import { create_drawer } from "./planner_kit/drawer.js";
import { create_panel } from "./planner_kit/panel.js";
import { create_toast } from "./planner_kit/toast.js";
import { create_menu } from "./planner_kit/menu.js";
import { create_legend, legend_html } from "./planner_kit/legend.js";
import { create_hint } from "./planner_kit/hint.js";
import { create_glow, GLOW_CLASS } from "./planner_kit/glow.js";
import { create_peeks, person_week_html, day_overview_html, day_state } from "./planner_kit/peeks.js";
import { CSS, STYLE_ID } from "./planner_kit/styles.js";
import * as big_picture from "./planner_kit/big_picture.js";

const VERSION = 1;

function install() {
	if (typeof window === "undefined" || typeof document === "undefined") return;
	if (window.planner_kit && window.planner_kit.version >= VERSION) return;
	const frappe = window.frappe;
	const warn = (e) => {
		if (window.console && typeof window.console.warn === "function") window.console.warn("planner_kit", e);
	};

	if (!document.getElementById(STYLE_ID)) {
		const style = document.createElement("style");
		style.id = STYLE_ID;
		style.textContent = CSS;
		document.head.appendChild(style);
	}

	const env = {
		win: window,
		doc: document,
		$: window.jQuery || window.$,
		frappe,
		t,
		warn,
		set_timeout: (fn, ms) => window.setTimeout(fn, ms),
		clear_timeout: (id) => window.clearTimeout(id),
		route: () => ((frappe && frappe.get_route && frappe.get_route()) || []).join("/"),
		page: () => ((frappe && frappe.get_route && frappe.get_route()) || [])[0] || "",
		set_replace: (on) => {
			if (!frappe || !frappe.route_flags) return;
			if (on) frappe.route_flags.replace_route = true;
			else if (frappe.route_flags.replace_route) frappe.route_flags.replace_route = false;
		},
		on_route_change: (fn) => {
			if (frappe && frappe.router && typeof frappe.router.on === "function") frappe.router.on("change", fn);
		},
		// frappe's one popstate listener (router.js, added at boot) calls `frappe.router.route()`. Wrapped
		// once here: during a popstate (`window.event`) the kit decides first, and for a popstate that
		// is the kit's own (a Back that closes a drawer) the router does not route, so the planner is
		// neither reloaded nor scrolled to the top. Any other call goes straight through. See history.js.
		gate_router: (decide) => {
			const router = frappe && frappe.router;
			if (!router || typeof router.route !== "function") return false;
			if (router.route.__planner_kit) return true;
			const original = router.route;
			const gated = function () {
				const ev = window.event;
				if (ev && ev.type === "popstate" && !ev.__planner_kit_seen) {
					let skip = false;
					try {
						skip = decide(ev) === true;
					} catch (e) {
						warn(e);
					}
					if (skip) return Promise.resolve();
				}
				return original.apply(this, arguments);
			};
			gated.__planner_kit = true;
			router.route = gated;
			return true;
		},
		hide_open_dialog: () => {
			if (frappe && frappe.ui && typeof frappe.ui.hide_open_dialog === "function") frappe.ui.hide_open_dialog();
		},
		user: () => (frappe && frappe.session && frappe.session.user) || "",
		call: (method, args) =>
			Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => (r && r.message) || null),
	};

	env.guard = create_guard(env).install();
	env.drawer = create_drawer(env);
	const kit = {
		version: VERSION,
		escape,
		safe_color,
		glow_key,
		glow_keys,
		glow_class: GLOW_CLASS,
		contact_href,
		format: { hours, drive, day_label, add_days, t },
		drawer: env.drawer,
		guard: env.guard,
		panel: create_panel(env),
		toast: create_toast(env),
		menu: create_menu(env),
		legend: create_legend(env),
		legend_html,
		hint: create_hint(env),
		hover_glow: create_glow(env),
		peeks: create_peeks(env),
		render: { person_week_html, day_overview_html, day_state },
		big_picture,
	};

	// Esc closes the topmost kit overlay (a menu before the drawer under it), unless something
	// else owns the key at that moment: a Frappe dialog on top, a drag in progress, an open
	// autocomplete list or date picker inside the drawer.
	document.addEventListener("keydown", (e) => {
		if (e.key !== "Escape" || e.defaultPrevented) return;
		const top = env.guard.top();
		if (!top || typeof top.on_escape !== "function") return;
		if (window.cur_dialog && window.cur_dialog.display) return;
		if (/(^|\s)\w+-drag-active(\s|$)/.test(document.body.className)) return;
		const target = e.target;
		if (target && target.getAttribute && target.getAttribute("aria-expanded") === "true") return;
		const picker = document.querySelector(".datepicker.active");
		if (picker && picker.offsetParent !== null) return;
		top.on_escape(e);
	});

	window.planner_kit = kit;
}

try {
	install();
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("planner_kit failed to start", e);
}
