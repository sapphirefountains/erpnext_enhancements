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
 *   conflicts.center(opts)              the Conflict center drawer (Phase 6D; api/planner_conflicts.py)
 *   blocks.form / note_form / chip_html / notes_html   personal blocks and day notes (Phase 6D)
 *   glow_key / glow_keys / format.{hours, drive, day_label, add_days}
 *   big_picture                         Phase 6C: card colors, the team capacity strip, saved views, print
 *   toolbar.menu(button, opts) / sheet(opts)  the toolbar's menu buttons (no history entry) and the
 *                                       phone's "Filters and tools" sheet (one, like a drawer)
 *   tour.open({title, steps, owner, on_close})  the first-visit tour (P12): a few short steps in one
 *                                       modal dialog, skippable everywhere, keyboard-complete, and
 *                                       never a history entry (planner_kit/tour.js)
 *   simple.mount(root, opts)            Simple mode: a card per job or site, Done and Coming up, one search
 *                                       box (planner_kit/simple.js; docs/planner-simple-mode.md)
 *   toolbar.tabs(container, opts)       the view buttons as a WAI-ARIA tablist (toolbar v2)
 *   toolbar.sections_to_items(sections) named sections as one menu list (More's Display/Views/Tools/Print)
 *   quiet_call(method, args, opts)      sf_kit's call on its quiet path: a fetch, so frappe shows no
 *                                       dialog; resolves r.message, rejects {status, message,
 *                                       exc_type, session_expired} (docs/app-design-system.md)
 *   state_html(kind, opts)              sf_kit's state panel markup (loading, empty, error, no-access)
 *   records.record_of(kind, item) / open(record) / open_item(kind, item)
 *                                       a tap opens the item's own record in the same tab (Nik,
 *                                       2026-10-27), asking first when the side panel has unsaved
 *                                       edits (planner_kit/records.js)
 *
 * See "Planner kit" in project_enhancements/README.md for how to use each one, and
 * planner_kit/history.js for how Back closes a drawer without the router reloading the page.
 */

import { escape, safe_color, glow_key, glow_keys, t, hours, drive, day_label, add_days, contact_href } from "./planner_kit/escape.js";
import { create_guard } from "./planner_kit/history.js";
import { create_drawer } from "./planner_kit/drawer.js";
import { create_panel } from "./planner_kit/panel.js";
import { create_records } from "./planner_kit/records.js";
import { create_toast } from "./planner_kit/toast.js";
import { create_menu } from "./planner_kit/menu.js";
import { create_legend, legend_html } from "./planner_kit/legend.js";
import { create_hint } from "./planner_kit/hint.js";
import { create_glow, GLOW_CLASS } from "./planner_kit/glow.js";
import { create_peeks, person_week_html, day_overview_html, day_state } from "./planner_kit/peeks.js";
import * as conflicts from "./planner_kit/conflicts.js";
import * as blocks from "./planner_kit/blocks.js";
import { CSS, STYLE_ID } from "./planner_kit/styles.js";
import * as big_picture from "./planner_kit/big_picture.js";
import { create_toolbar } from "./planner_kit/toolbar.js";
import { create_tour } from "./planner_kit/tour.js";
import { create_simple, card_html as simple_card_html, initials as simple_initials, legend_sections as simple_legend } from "./planner_kit/simple.js";
import { call as sf_call } from "./sf_kit/call.js";
import { state_html } from "./sf_kit/state.js";

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
		style.textContent = CSS + conflicts.CONFLICTS_CSS + blocks.BLOCKS_CSS;
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

	// sf_kit's quiet call reads frappe, fetch and the cookie when a call is made, as sf_kit's own
	// bundle does; planner_kit carries it so a planner need not load sf_kit for one request.
	const call_env = { cookie: () => document.cookie };
	Object.defineProperty(call_env, "frappe", { get: () => window.frappe, enumerable: true });
	Object.defineProperty(call_env, "fetch", {
		get: () => (typeof window.fetch === "function" ? (url, init) => window.fetch(url, init) : undefined),
		enumerable: true,
	});

	env.guard = create_guard(env).install();
	env.drawer = create_drawer(env);
	env.toast = create_toast(env);
	const panel = create_panel(env);
	// A tap opens the record (records.js); the quick looks (peeks.js) mark their bookings with it.
	env.unsaved = panel.unsaved;
	env.records = create_records(env);
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
		panel,
		records: env.records,
		toast: env.toast,
		menu: create_menu(env),
		legend: create_legend(env),
		legend_html,
		hint: create_hint(env),
		hover_glow: create_glow(env),
		peeks: create_peeks(env),
		// Phase 6D UI: the Conflict center, and personal blocks and day notes on screen.
		conflicts: Object.assign(conflicts.create_conflicts(env), {
			html: conflicts.conflicts_html,
			picker_html: conflicts.picker_html,
			free_people: conflicts.free_people,
			fix_args: conflicts.fix_args,
			open_count: conflicts.open_count,
			index_by_ref: conflicts.index_by_ref,
			kinds: conflicts.KIND_INFO,
			methods: conflicts.METHODS,
		}),
		blocks: Object.assign(blocks.create_blocks(env), {
			chip_html: blocks.chip_html,
			notes_html: blocks.notes_html,
			note_list_html: blocks.note_list_html,
			block_text: blocks.block_text,
			block_slot: blocks.block_slot,
			block_window: blocks.block_window,
			methods: blocks.METHODS,
		}),
		render: { person_week_html, day_overview_html, day_state },
		big_picture,
		// The planner toolbar redesign (v1.687.0): menu buttons and the phone's sheet.
		toolbar: create_toolbar(env),
		// The first-visit tour (P12). Not part of the history guard: it takes no entry of its own.
		tour: create_tour(env),
		// Simple mode (docs/planner-simple-mode.md): a card per job or site, Done and Coming up. Reads only.
		simple: Object.assign(create_simple(env), { card_html: simple_card_html, initials: simple_initials, legend_sections: simple_legend }),
		// A request whose refusal the page reports in its own panel (the Utilization view).
		quiet_call: (method, args, opts) => sf_call(call_env, method, args, Object.assign({}, opts, { quiet: true })),
		state_html,
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
