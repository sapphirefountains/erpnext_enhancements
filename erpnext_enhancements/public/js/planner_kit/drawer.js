/*
 * Planner kit: the right-side drawer.
 *
 * A panel on the right that leaves the page visible and usable behind it: no backdrop, nothing
 * blocks a pointer, so the calendar still scrolls and still takes a drag while a drawer is open.
 * It has a title, an optional subtitle, optional header tools (the person drawer's week arrows), a
 * body, footer actions and a close button. Esc closes it, and so does Back (history.js). Opening a
 * second drawer replaces the first in place, on the same history entry, rather than stacking.
 * Under 768px wide it fills the screen.
 *
 *   const handle = planner_kit.drawer.open({
 *     title, subtitle, body,           // body: an HTML string (already escaped), an Element or jQuery
 *     tools, actions: [{label, primary, on_click(handle)}],
 *     width: 440,                      // px on a wide screen
 *     key: "person:RES-00001",         // what it shows, for the page's own bookkeeping
 *     owner: "pp",                     // which page opened it (both planners share one drawer)
 *     push: element,                   // squeezed by the drawer's width on a wide screen, so the
 *                                      // calendar's right-hand days are not hidden under it
 *     reopen: () => ...,               // how to open it again (Forward onto its history entry)
 *     on_click: (event, handle) => ..., // one delegated click listener for whatever the body shows
 *     on_close: (how) => ...,          // "user" | "back" | "route" | "replaced" | "parent"
 *   });
 *   handle.set_title / set_subtitle / set_body / set_tools / set_actions / close / is_open
 *   planner_kit.drawer.current()  ·  .close()  ·  .element()  ·  .route(fn, keep)
 */

import { escape } from "./escape.js";

const PHONE_MAX = 767;
const PUSH_MIN = 1100;
const PUSH_ROOM = 600;

export function create_drawer(env) {
	const doc = env.doc;
	const win = env.win;
	const guard = env.guard;
	let root = null;
	let parts = null;
	let current = null;
	let pushed = null; // {el, margin}

	const overlay = {
		kind: "drawer",
		close: (how) => close_ui(how, true),
		on_escape: () => api.close(),
		get reopen() {
			return current && current.opts.reopen;
		},
	};

	function build() {
		if (root) return;
		root = doc.createElement("aside");
		root.className = "pk-drawer";
		root.setAttribute("role", "dialog");
		root.setAttribute("aria-modal", "false");
		root.setAttribute("aria-labelledby", "pk-drawer-title");
		root.setAttribute("tabindex", "-1");
		root.hidden = true;
		root.innerHTML = `
			<div class="pk-drawer-head">
				<div class="pk-drawer-heading">
					<h3 class="pk-drawer-title" id="pk-drawer-title"></h3>
					<div class="pk-drawer-sub"></div>
				</div>
				<div class="pk-drawer-tools"></div>
				<button type="button" class="pk-drawer-close" aria-label="${escape(env.t("Close"))}" title="${escape(
			env.t("Close (Esc)")
		)}">&times;</button>
			</div>
			<div class="pk-drawer-body"></div>
			<div class="pk-drawer-foot"></div>`;
		parts = {
			title: root.querySelector(".pk-drawer-title"),
			sub: root.querySelector(".pk-drawer-sub"),
			tools: root.querySelector(".pk-drawer-tools"),
			body: root.querySelector(".pk-drawer-body"),
			foot: root.querySelector(".pk-drawer-foot"),
		};
		root.querySelector(".pk-drawer-close").addEventListener("click", () => api.close());
		// One delegated listener for whatever the drawer shows: `on_click(event, handle)`. Enter or
		// Space on a role="button" element inside it is a click, as on a button.
		root.addEventListener("click", (e) => {
			if (current && typeof current.opts.on_click === "function") current.opts.on_click(e, current);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			const el = e.target && e.target.closest ? e.target.closest('[role="button"]') : null;
			if (!el || el.tagName === "BUTTON" || el.tagName === "A") return;
			e.preventDefault();
			el.click();
		});
		doc.body.appendChild(root);
		win.addEventListener("resize", () => {
			if (current) apply_push(current.opts.push);
		});
	}

	function fill(el, content) {
		while (el.firstChild) el.removeChild(el.firstChild);
		if (content === null || content === undefined || content === "") return;
		if (typeof content === "string") {
			el.innerHTML = content;
			return;
		}
		const nodes = content.jquery ? Array.from(content) : [content];
		nodes.forEach((node) => node && el.appendChild(node));
	}

	function set_actions(actions) {
		fill(parts.foot, null);
		(actions || []).forEach((action) => {
			if (!action || !action.label) return;
			const button = doc.createElement("button");
			button.type = "button";
			button.className = `btn btn-sm ${action.primary ? "btn-primary" : "btn-default"}`;
			button.textContent = action.label;
			if (action.title) button.title = action.title;
			button.addEventListener("click", () => {
				if (current && typeof action.on_click === "function") action.on_click(current);
			});
			parts.foot.appendChild(button);
		});
		parts.foot.hidden = !parts.foot.firstChild;
	}

	function width_of(opts) {
		const wanted = Math.max(280, Number(opts.width) || 440);
		return win.innerWidth <= PHONE_MAX ? win.innerWidth : Math.min(wanted, win.innerWidth);
	}

	// Squeeze the page by however much of it the drawer covers, on a screen wide enough to keep a
	// usable calendar beside it (at least PUSH_ROOM px); otherwise the drawer simply lies over the
	// page's right-hand side. A phone gets a full-screen drawer instead.
	function apply_push(el) {
		restore_push();
		if (!el || !current || win.innerWidth < PUSH_MIN) return;
		if (win.innerWidth - width_of(current.opts) < PUSH_ROOM) return;
		const rect = el.getBoundingClientRect();
		const overlap = Math.ceil(rect.right - (win.innerWidth - width_of(current.opts)) + 12);
		if (overlap <= 0) return;
		pushed = { el, margin: el.style.marginRight };
		el.style.marginRight = `${overlap}px`;
		el.classList.add("pk-pushed");
	}

	function restore_push() {
		if (!pushed) return;
		pushed.el.style.marginRight = pushed.margin || "";
		pushed.el.classList.remove("pk-pushed");
		pushed = null;
	}

	function close_ui(how, from_guard) {
		const handle = current;
		if (!handle) return;
		current = null;
		root.hidden = true;
		root.classList.remove("pk-open");
		doc.body.classList.remove("pk-drawer-open");
		restore_push();
		fill(parts.body, null);
		fill(parts.tools, null);
		fill(parts.foot, null);
		if (!from_guard) guard.close(overlay, how || "user", handle.opts.reopen);
		if (typeof handle.opts.on_close === "function") {
			try {
				handle.opts.on_close(how || "user");
			} catch (e) {
				if (env.warn) env.warn(e);
			}
		}
	}

	function make_handle(opts) {
		const handle = {
			opts,
			key: opts.key || null,
			owner: opts.owner || null,
			get el() {
				return root;
			},
			get body() {
				return parts.body;
			},
			is_open: () => current === handle,
			set_title: (text) => {
				if (current === handle) parts.title.textContent = text || "";
			},
			set_subtitle: (text) => {
				if (current !== handle) return;
				parts.sub.textContent = text || "";
				parts.sub.hidden = !text;
			},
			set_body: (content) => {
				if (current === handle) fill(parts.body, content);
			},
			set_tools: (content) => {
				if (current === handle) fill(parts.tools, content);
			},
			set_actions: (actions) => {
				if (current === handle) set_actions(actions);
			},
			close: () => {
				if (current === handle) close_ui("user", false);
			},
		};
		return handle;
	}

	const api = {
		open(opts) {
			opts = opts || {};
			build();
			const previous = current;
			if (previous) {
				// A second drawer replaces the first, on the same history entry.
				current = null;
				restore_push();
				fill(parts.body, null);
				if (typeof previous.opts.on_close === "function") {
					try {
						previous.opts.on_close("replaced");
					} catch (e) {
						if (env.warn) env.warn(e);
					}
				}
			}
			const handle = make_handle(opts);
			current = handle;
			root.style.setProperty("--pk-w", `${width_of(opts)}px`);
			doc.body.style.setProperty("--pk-drawer-w", `${width_of(opts)}px`);
			parts.title.textContent = opts.title || "";
			parts.sub.textContent = opts.subtitle || "";
			parts.sub.hidden = !opts.subtitle;
			fill(parts.tools, opts.tools);
			fill(parts.body, opts.body);
			set_actions(opts.actions);
			root.hidden = false;
			root.classList.add("pk-open");
			root.setAttribute("data-owner", opts.owner || "");
			doc.body.classList.add("pk-drawer-open");
			parts.body.scrollTop = 0;
			apply_push(opts.push);
			guard.open(overlay);
			if (!previous) {
				try {
					root.focus({ preventScroll: true });
				} catch (e) {
					// Courtesy.
				}
			}
			return handle;
		},
		current() {
			return current;
		},
		close() {
			if (current) close_ui("user", false);
		},
		// The drawer's element, made on first use and kept: a page binds its pointerdown here once,
		// so a booking can be dragged out of the drawer onto the calendar.
		element() {
			build();
			return root;
		},
		route(fn, keep) {
			return guard.route(fn, keep);
		},
		// Close the drawer and then do `fn` (an "Open project" button): a route change it makes
		// replaces the drawer's history entry instead of stacking on it.
		navigate(fn) {
			api.close();
			return fn();
		},
	};
	return api;
}
