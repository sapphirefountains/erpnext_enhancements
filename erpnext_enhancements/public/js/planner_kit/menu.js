/*
 * Planner kit: a small anchored menu (Phase 6B's right-click menu builds on it).
 *
 *   const menu = planner_kit.menu({
 *     anchor: element | {x, y},          // under an element, or at a point (a right-click)
 *     items: [
 *       { label: "Move to tomorrow", on_click: () => ..., hint: "Ctrl+→", disabled: false, danger: false },
 *       { divider: true },
 *     ],
 *     title: "TASK-1 · Dig",             // optional heading
 *     owner: "pp", on_close: (how) => ...
 *   });
 *   menu.close();  planner_kit.menu.close();
 *
 * It closes on an item (the menu goes first, then the item runs, so an item may open a drawer),
 * Esc, a click anywhere else, and Back: it is an overlay of history.js like the drawer, sharing the
 * one kit history entry. Opening a second menu replaces the first. Arrow keys move between items;
 * Enter or Space runs one. Labels are set as text, never HTML.
 */

export function create_menu(env) {
	const doc = env.doc;
	const win = env.win;
	const guard = env.guard;
	let current = null;

	function close_ui(how, from_guard) {
		const item = current;
		if (!item) return;
		current = null;
		doc.removeEventListener("pointerdown", item.away, true);
		win.removeEventListener("resize", item.away_resize);
		if (item.el.parentNode) item.el.parentNode.removeChild(item.el);
		// A menu replaced by the next one hands its entry over (the guard takes over an entry whose
		// cleanup Back has not gone yet), so it closes as a "user" close does.
		if (!from_guard) guard.close(item.overlay, how === "replaced" ? "user" : how || "user");
		if (typeof item.opts.on_close === "function") {
			try {
				item.opts.on_close(how || "user");
			} catch (e) {
				if (env.warn) env.warn(e);
			}
		}
		if (item.return_focus && typeof item.return_focus.focus === "function" && how !== "route") {
			try {
				item.return_focus.focus({ preventScroll: true });
			} catch (e) {
				// Courtesy.
			}
		}
	}

	function place(el, anchor) {
		let x = 0;
		let y = 0;
		if (anchor && typeof anchor.getBoundingClientRect === "function") {
			const rect = anchor.getBoundingClientRect();
			x = rect.left;
			y = rect.bottom + 4;
		} else if (anchor) {
			x = Number(anchor.x) || 0;
			y = Number(anchor.y) || 0;
		}
		el.style.left = "0px";
		el.style.top = "0px";
		const box = el.getBoundingClientRect();
		const width = box.width || 220;
		const height = box.height || 0;
		x = Math.max(8, Math.min(x, win.innerWidth - width - 8));
		if (y + height > win.innerHeight - 8) y = Math.max(8, y - height - 8);
		el.style.left = `${Math.round(x)}px`;
		el.style.top = `${Math.round(y)}px`;
	}

	function buttons(el) {
		return Array.from(el.querySelectorAll(".pk-menu-item:not([disabled])"));
	}

	function menu(opts) {
		opts = opts || {};
		if (current) close_ui("replaced", false);
		const el = doc.createElement("div");
		el.className = "pk-menu";
		el.setAttribute("role", "menu");
		if (opts.title) {
			const head = doc.createElement("div");
			head.className = "pk-menu-title";
			head.textContent = opts.title;
			el.appendChild(head);
		}
		(opts.items || []).forEach((entry) => {
			if (!entry) return;
			if (entry.divider) {
				const line = doc.createElement("div");
				line.className = "pk-menu-divider";
				line.setAttribute("role", "separator");
				el.appendChild(line);
				return;
			}
			const button = doc.createElement("button");
			button.type = "button";
			button.className = `pk-menu-item${entry.danger ? " pk-menu-danger" : ""}`;
			button.setAttribute("role", "menuitem");
			const label = doc.createElement("span");
			label.textContent = entry.label || "";
			button.appendChild(label);
			if (entry.hint) {
				const hint = doc.createElement("span");
				hint.className = "pk-menu-hint";
				hint.textContent = entry.hint;
				button.appendChild(hint);
			}
			if (entry.disabled) button.disabled = true;
			button.addEventListener("click", () => {
				close_ui("user", false);
				if (typeof entry.on_click === "function") entry.on_click();
			});
			el.appendChild(button);
		});
		el.addEventListener("keydown", (e) => {
			const list = buttons(el);
			const index = list.indexOf(doc.activeElement);
			if (e.key === "ArrowDown" || e.key === "ArrowUp") {
				e.preventDefault();
				const step = e.key === "ArrowDown" ? 1 : -1;
				const next = list[(index + step + list.length) % list.length];
				if (next) next.focus();
			}
		});
		const item = {
			el,
			opts,
			return_focus: doc.activeElement,
			away: (e) => {
				if (current === item && !el.contains(e.target)) close_ui("user", false);
			},
			away_resize: () => {
				if (current === item) close_ui("user", false);
			},
		};
		item.overlay = {
			kind: "menu",
			close: (how) => {
				if (current === item) close_ui(how, true);
			},
			on_escape: () => {
				if (current === item) close_ui("user", false);
			},
		};
		doc.body.appendChild(el);
		place(el, opts.anchor);
		current = item;
		doc.addEventListener("pointerdown", item.away, true);
		win.addEventListener("resize", item.away_resize);
		guard.open(item.overlay);
		const first = buttons(el)[0];
		if (first) {
			try {
				first.focus({ preventScroll: true });
			} catch (e) {
				// Courtesy.
			}
		}
		return {
			el,
			close: () => {
				if (current === item) close_ui("user", false);
			},
		};
	}

	menu.close = () => close_ui("user", false);
	menu.current = () => (current ? current.el : null);
	return menu;
}
