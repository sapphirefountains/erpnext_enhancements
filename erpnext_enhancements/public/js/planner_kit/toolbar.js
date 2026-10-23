/*
 * Planner kit: the toolbar's tabs, its menu buttons and the phone's "Filters and tools" sheet (planner
 * toolbar redesign, v1.687.0; toolbar v2, P1 of the planner UX redesign). Both planners keep one bar:
 * ‹ Today ›, the range, the view tabs, the filters, the alert chips, "+ Add ▾", "More ▾", then the
 * undo icon, ? and refresh. More holds four sections (Display, Views, Tools, Print); under 767px the
 * same sections fill one bottom sheet.
 *
 *   const tabs = planner_kit.toolbar.tabs(container, { label: "Planner views" });
 *   tabs.sync("week");                        // aria-selected + roving tabindex from data-tb-tab
 *
 *   const items = planner_kit.toolbar.sections_to_items([   // one menu list out of named sections
 *     { title: "Display", items: [...] }, { title: "Print", items: [...] },
 *   ]);
 *
 *   const handle = planner_kit.toolbar.menu(button, {
 *     label: "Display",                     // the menu's accessible name
 *     items: () => [                        // built every time it opens, so states are current
 *       { heading: "Color by" },
 *       { label: "Color: Person", radio: true, checked: true, on_click },
 *       { label: "Draft mode", checked: false, on_click, hint: "D" },   // a menuitemcheckbox
 *       { label: "Copy week…", on_click, disabled: false },
 *       { label: "Plaza fountain", sample_html: "<i …></i>", info: true },   // read-only (a key)
 *       { divider: true },
 *     ],
 *   });
 *   handle.open(); handle.close(); handle.is_open();
 *
 *   const sheet = planner_kit.toolbar.sheet({
 *     title: "Filters and tools", owner: "pp",
 *     top: (slot) => { ... },               // the page moves its own fields in (and out in on_close)
 *     sections: () => [{ title: "Edit", items: [...] }],   // the same item shapes as a menu
 *     on_close: (how) => { ... }, return_focus: button,
 *   });
 *   sheet.refresh(); sheet.close();
 *
 * A menu is the WAI-ARIA menu button pattern: the button carries aria-haspopup="menu" and
 * aria-expanded; the list is role="menu" with menuitem / menuitemcheckbox / menuitemradio items
 * (aria-checked, and a check mark that is not color alone); ArrowDown/ArrowUp move (and wrap),
 * Home/End jump, Esc closes and gives focus back to the button, Tab closes and lets focus go on, and
 * a click anywhere else or a route change closes it. An item closes the menu first and then runs, so an item may open a
 * drawer or a print window (the click is still the user activation a pop-up needs).
 *
 * A menu pushes NO history entry: it is part of the toolbar, not a screen, and it closes on any click
 * outside it. That is the difference from planner_kit.menu (the right-click menu), which is an
 * overlay of history.js like the drawer. Its list carries pk-menu as well, so the planners' own
 * shortcut handlers already leave its keys alone.
 *
 * The sheet IS a screen on a phone, so it is an overlay of history.js like the drawer: it shares the
 * one kit history entry, Back closes it without the router reloading the planner, and Esc closes it
 * through the kit's Esc handler. Opened from a tap (a user activation), so the entry is never skipped.
 * Its items close it before they run, except a toggle (keep_open), which redraws it in place.
 *
 * Labels are set as text, never HTML; `sample_html` is the page's own markup for a color swatch,
 * built from fixed strings and escaped data (as legend.js's).
 */

let seq = 0;

function next_id(prefix) {
	seq += 1;
	return `${prefix}-${Date.now().toString(36)}${seq.toString(36)}`;
}

// What an item is, for the menu's roles: a heading, a divider, or one of the three item roles.
export function item_role(entry) {
	if (!entry || entry.divider || entry.heading) return null;
	if (entry.radio) return "menuitemradio";
	if (typeof entry.checked === "boolean") return "menuitemcheckbox";
	return "menuitem";
}

// An item that does something when chosen (not a heading, a divider, a key entry or a disabled one).
export function item_active(entry) {
	return !!item_role(entry) && !entry.info && !entry.disabled && typeof entry.on_click === "function";
}

// Headings and dividers at the ends, or two in a row, say nothing: drop them.
export function tidy(items) {
	const list = (items || []).filter(Boolean);
	const out = [];
	list.forEach((entry, index) => {
		if (entry.divider) {
			if (!out.length || out[out.length - 1].divider) return;
			out.push(entry);
			return;
		}
		if (entry.heading) {
			// A heading with nothing under it before the next heading or divider.
			const next = list[index + 1];
			if (!next || next.divider || next.heading) return;
		}
		out.push(entry);
	});
	while (out.length && (out[out.length - 1].divider || out[out.length - 1].heading)) out.pop();
	return out;
}

// Named sections as one menu list: a heading over each section's items, a divider between sections.
// A section with nothing in it is left out, so a screen that offers nothing under "Display" shows no
// "Display" heading either.
export function sections_to_items(sections) {
	const out = [];
	(sections || []).forEach((section) => {
		if (!section) return;
		const items = tidy(section.items);
		if (!items.some((entry) => !entry.divider && !entry.heading)) return;
		if (out.length) out.push({ divider: true });
		if (section.title) out.push({ heading: section.title, section: true });
		items.forEach((entry) => out.push(entry));
	});
	return out;
}

export function create_toolbar(env) {
	const doc = env.doc;
	const win = env.win;
	let open_menu = null;

	const warn = (e) => {
		if (env.warn) env.warn(e);
	};
	const focus = (el) => {
		if (!el || typeof el.focus !== "function") return;
		try {
			el.focus({ preventScroll: true });
		} catch (e) {
			// Courtesy.
		}
	};
	const run = (entry) => {
		try {
			entry.on_click();
		} catch (e) {
			warn(e);
		}
	};

	// One item's content: an optional swatch, the label, an optional hint, and for a checkable item a
	// check mark that is there or not (so the state is a shape, never color alone).
	function fill(el, entry, role) {
		if (role === "menuitemcheckbox" || role === "menuitemradio") {
			const mark = doc.createElement("span");
			mark.className = "pk-tb-check";
			mark.setAttribute("aria-hidden", "true");
			mark.textContent = entry.checked ? "✓" : "";
			el.appendChild(mark);
		}
		if (entry.sample_html) {
			const sample = doc.createElement("span");
			sample.className = "pk-tb-sample";
			sample.setAttribute("aria-hidden", "true");
			sample.innerHTML = String(entry.sample_html);
			el.appendChild(sample);
		}
		const label = doc.createElement("span");
		label.className = "pk-tb-label";
		label.textContent = entry.label || "";
		el.appendChild(label);
		if (entry.hint) {
			const hint = doc.createElement("span");
			hint.className = "pk-menu-hint";
			hint.textContent = entry.hint;
			el.appendChild(hint);
		}
		if (entry.title) el.setAttribute("title", entry.title);
	}

	// ------------------------------------------------------------------ the view tabs

	// The bar's view switcher as a WAI-ARIA tablist. The page builds the buttons (each with a
	// data-tb-tab naming its view) and keeps its own click handlers; this gives the group its roles,
	// a roving tabindex (the tab on screen is the one Tab reaches) and the arrow keys. An arrow key
	// moves focus only: choosing a tab is a route change with its own history entry, so Enter or Space
	// (the button's click) does it, never a stray arrow.
	function tabs(container, opts) {
		opts = opts || {};
		const buttons = () =>
			Array.from(container.querySelectorAll ? container.querySelectorAll("[data-tb-tab]") : []);
		const shown = () => buttons().filter((button) => !button.hidden && !(button.style && button.style.display === "none"));
		container.setAttribute("role", "tablist");
		if (opts.label) container.setAttribute("aria-label", opts.label);
		container.addEventListener("keydown", (e) => {
			if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
			const list = shown();
			const index = list.indexOf(doc.activeElement);
			if (index < 0) return;
			e.preventDefault();
			let next = index;
			if (e.key === "ArrowRight") next = (index + 1) % list.length;
			else if (e.key === "ArrowLeft") next = (index - 1 + list.length) % list.length;
			else next = e.key === "Home" ? 0 : list.length - 1;
			focus(list[next]);
		});
		function sync(selected) {
			const list = buttons();
			// With nothing selected (a screen that is no tab, such as My week), the first one stays reachable.
			const active = list.find((button) => button.getAttribute("data-tb-tab") === selected) || shown()[0] || null;
			list.forEach((button) => {
				button.setAttribute("role", "tab");
				const on = button.getAttribute("data-tb-tab") === selected;
				button.setAttribute("aria-selected", on ? "true" : "false");
				button.setAttribute("tabindex", button === active ? "0" : "-1");
			});
		}
		sync(opts.selected);
		return { sync, buttons };
	}

	// ------------------------------------------------------------------ the menu button

	function menu(button, opts) {
		opts = opts || {};
		const list_id = next_id("pk-tb-menu");
		button.setAttribute("aria-haspopup", "menu");
		button.setAttribute("aria-expanded", "false");
		button.setAttribute("aria-controls", list_id);
		let state = null; // {el, items: [button], away, resize, scroll}

		function items_of() {
			let list = [];
			try {
				list = tidy(typeof opts.items === "function" ? opts.items() : opts.items);
			} catch (e) {
				warn(e);
			}
			return list;
		}

		function place(el) {
			const rect = typeof button.getBoundingClientRect === "function" ? button.getBoundingClientRect() : null;
			let x = rect ? rect.left : 8;
			let y = rect ? rect.bottom + 4 : 8;
			el.style.left = "0px";
			el.style.top = "0px";
			const box = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : {};
			const width = box.width || 240;
			const height = box.height || 0;
			const vw = Number(win.innerWidth) || 1024;
			const vh = Number(win.innerHeight) || 768;
			x = Math.max(8, Math.min(x, vw - width - 8));
			if (rect && y + height > vh - 8 && rect.top - height - 4 > 8) y = rect.top - height - 4;
			el.style.left = `${Math.round(x)}px`;
			el.style.top = `${Math.round(y)}px`;
		}

		function close(how) {
			const current = state;
			if (!current) return;
			state = null;
			if (open_menu === handle) open_menu = null;
			doc.removeEventListener("pointerdown", current.away, true);
			win.removeEventListener("resize", current.resize);
			win.removeEventListener("scroll", current.scroll, true);
			if (current.el.parentNode) current.el.parentNode.removeChild(current.el);
			button.setAttribute("aria-expanded", "false");
			if (button.classList) button.classList.remove("pk-tb-open");
			// Esc and a chosen item give focus back to the button; a click elsewhere or Tab leave it
			// where the person put it.
			if (how === "escape" || how === "item") focus(button);
			if (typeof opts.on_close === "function") {
				try {
					opts.on_close(how);
				} catch (e) {
					warn(e);
				}
			}
		}

		function move(list, index, step) {
			if (!list.length) return;
			const next = list[(index + step + list.length) % list.length];
			focus(next);
		}

		function open(which) {
			if (state) return;
			if (open_menu && open_menu !== handle) open_menu.close("other");
			const entries = items_of();
			const el = doc.createElement("div");
			el.className = "pk-menu pk-tb-menu";
			el.id = list_id;
			el.setAttribute("role", "menu");
			el.setAttribute("aria-label", opts.label || button.textContent || "");
			el.setAttribute("tabindex", "-1");
			const focusable = [];
			entries.forEach((entry) => {
				if (entry.divider) {
					const line = doc.createElement("div");
					line.className = "pk-menu-divider";
					line.setAttribute("role", "separator");
					el.appendChild(line);
					return;
				}
				if (entry.heading) {
					const head = doc.createElement("div");
					head.className = `pk-menu-title${entry.section ? " pk-tb-section" : ""}`;
					head.setAttribute("role", "presentation");
					head.textContent = entry.heading;
					el.appendChild(head);
					return;
				}
				const role = item_role(entry);
				const item = doc.createElement("button");
				item.type = "button";
				item.className = `pk-menu-item pk-tb-item${entry.info ? " pk-tb-info" : ""}`;
				item.setAttribute("role", role);
				item.setAttribute("tabindex", "-1");
				if (role !== "menuitem") item.setAttribute("aria-checked", entry.checked ? "true" : "false");
				if (entry.info || entry.disabled) item.setAttribute("aria-disabled", "true");
				fill(item, entry, role);
				item.addEventListener("click", (e) => {
					if (e && typeof e.preventDefault === "function") e.preventDefault();
					if (!item_active(entry)) return;
					close("item");
					run(entry);
				});
				focusable.push(item);
				el.appendChild(item);
			});
			if (!focusable.length) {
				const empty = doc.createElement("div");
				empty.className = "pk-menu-title";
				empty.setAttribute("role", "presentation");
				empty.textContent = env.t ? env.t("Nothing here on this screen") : "Nothing here on this screen";
				el.appendChild(empty);
			}
			el.addEventListener("keydown", (e) => {
				const index = focusable.indexOf(doc.activeElement);
				if (e.key === "ArrowDown" || e.key === "ArrowUp") {
					e.preventDefault();
					move(focusable, index < 0 ? (e.key === "ArrowDown" ? -1 : 0) : index, e.key === "ArrowDown" ? 1 : -1);
				} else if (e.key === "Home" || e.key === "End") {
					e.preventDefault();
					focus(e.key === "Home" ? focusable[0] : focusable[focusable.length - 1]);
				} else if (e.key === "Escape" || e.key === "Esc") {
					// Ours: nothing behind the menu (a drawer, the selection, a highlight) takes it too.
					e.preventDefault();
					if (typeof e.stopPropagation === "function") e.stopPropagation();
					close("escape");
				} else if (e.key === "Tab") {
					// The list sits at the end of <body>, so the browser's own Tab would leave for the end
					// of the page. Focus goes back to the button first and the Tab is not stopped: the
					// browser then moves on from the button, to the control after it (or, with Shift,
					// before it), as if the menu had never been open.
					focus(button);
					close("tab");
				}
			});
			const current = {
				el,
				items: focusable,
				away: (e) => {
					if (state !== current) return;
					const target = e && e.target;
					if (target && (el.contains(target) || button.contains(target))) return;
					close("outside");
				},
				resize: () => {
					if (state === current) close("outside");
				},
				// The menu is fixed under its button; a page scroll would leave it behind. A scroll
				// inside the menu (a long color key) is its own.
				scroll: (e) => {
					if (state !== current) return;
					if (e && e.target && typeof el.contains === "function" && el.contains(e.target)) return;
					close("outside");
				},
			};
			state = current;
			open_menu = handle;
			doc.body.appendChild(el);
			place(el);
			button.setAttribute("aria-expanded", "true");
			if (button.classList) button.classList.add("pk-tb-open");
			doc.addEventListener("pointerdown", current.away, true);
			win.addEventListener("resize", current.resize);
			win.addEventListener("scroll", current.scroll, true);
			if (focusable.length) focus(which === "last" ? focusable[focusable.length - 1] : focusable[0]);
			else focus(el);
		}

		button.addEventListener("click", (e) => {
			if (e && typeof e.preventDefault === "function") e.preventDefault();
			if (state) close("toggle");
			else open("first");
		});
		button.addEventListener("keydown", (e) => {
			if (e.key === "ArrowDown" || e.key === "ArrowUp") {
				e.preventDefault();
				if (!state) open(e.key === "ArrowUp" ? "last" : "first");
				else if (state.items.length) focus(e.key === "ArrowUp" ? state.items[state.items.length - 1] : state.items[0]);
			} else if ((e.key === "Escape" || e.key === "Esc") && state) {
				e.preventDefault();
				if (typeof e.stopPropagation === "function") e.stopPropagation();
				close("escape");
			}
		});

		const handle = {
			button,
			open: (which) => open(which || "first"),
			close: (how) => close(how || "user"),
			is_open: () => !!state,
			element: () => (state ? state.el : null),
			items: () => (state ? state.items.slice() : []),
		};
		return handle;
	}

	// ------------------------------------------------------------------ the phone's sheet

	let open_sheet = null;

	// Enabled, not hidden (itself or an ancestor), and laid out (offsetParent is null for display:
	// none, here or above; a fixed-position element has none either, but nothing in the sheet is).
	function focusable_now(node) {
		if (!node || node.disabled || node.hidden) return false;
		if (typeof node.closest === "function" && node.closest("[hidden]")) return false;
		return node.offsetParent !== null;
	}

	function sheet(opts) {
		opts = opts || {};
		if (open_sheet) open_sheet.close("replaced");
		if (open_menu) open_menu.close("other");
		const guard = env.guard;
		const return_focus = opts.return_focus || doc.activeElement;
		const backdrop = doc.createElement("div");
		backdrop.className = "pk-sheet-backdrop";
		const el = doc.createElement("div");
		el.className = "pk-sheet";
		el.setAttribute("role", "dialog");
		el.setAttribute("aria-modal", "true");
		el.setAttribute("tabindex", "-1");
		const title_id = next_id("pk-sheet-title");
		el.setAttribute("aria-labelledby", title_id);
		const head = doc.createElement("div");
		head.className = "pk-sheet-head";
		const title = doc.createElement("h2");
		title.className = "pk-sheet-heading";
		title.id = title_id;
		title.textContent = opts.title || "";
		head.appendChild(title);
		const close_button = doc.createElement("button");
		close_button.type = "button";
		close_button.className = "pk-sheet-close";
		close_button.setAttribute("aria-label", env.t ? env.t("Close") : "Close");
		close_button.setAttribute("title", env.t ? env.t("Close (Esc)") : "Close (Esc)");
		close_button.textContent = "×";
		head.appendChild(close_button);
		el.appendChild(head);
		const top = doc.createElement("div");
		top.className = "pk-sheet-top";
		el.appendChild(top);
		const body = doc.createElement("div");
		body.className = "pk-sheet-body";
		el.appendChild(body);

		let closed = false;
		const overlay = {
			kind: "sheet",
			close: (how) => finish(how || "route", true),
			on_escape: () => finish("user", false),
		};

		function finish(how, from_guard) {
			if (closed) return;
			closed = true;
			if (open_sheet === handle) open_sheet = null;
			win.removeEventListener("resize", resize);
			if (typeof opts.on_close === "function") {
				try {
					opts.on_close(how);
				} catch (e) {
					warn(e);
				}
			}
			if (el.parentNode) el.parentNode.removeChild(el);
			if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
			if (!from_guard && guard) guard.close(overlay, how === "replaced" ? "user" : how);
			// A route change took the screen away: there is no button to go back to.
			if (how !== "route") focus(return_focus);
		}

		function render() {
			while (body.firstChild) body.removeChild(body.firstChild);
			let sections = [];
			try {
				sections = typeof opts.sections === "function" ? opts.sections() : opts.sections || [];
			} catch (e) {
				warn(e);
			}
			(sections || []).forEach((section) => {
				const items = tidy(section && section.items).filter((entry) => !entry.divider);
				if (!items.length) return;
				const box = doc.createElement("section");
				box.className = "pk-sheet-section";
				const heading = doc.createElement("h3");
				heading.className = "pk-sheet-title";
				heading.textContent = section.title || "";
				box.appendChild(heading);
				const group = doc.createElement("div");
				group.className = "pk-sheet-items";
				group.setAttribute("role", "group");
				group.setAttribute("aria-label", section.title || "");
				items.forEach((entry) => {
					if (entry.heading) {
						const sub = doc.createElement("div");
						sub.className = "pk-sheet-sub";
						sub.textContent = entry.heading;
						group.appendChild(sub);
						return;
					}
					const role = item_role(entry);
					const button = doc.createElement(entry.info ? "div" : "button");
					if (!entry.info) button.type = "button";
					button.className = `pk-sheet-item${entry.info ? " pk-tb-info" : ""}${entry.tone ? ` pk-sheet-${entry.tone}` : ""}`;
					if (role === "menuitemcheckbox" || role === "menuitemradio") {
						button.setAttribute("aria-pressed", entry.checked ? "true" : "false");
					}
					if (entry.disabled && !entry.info) button.setAttribute("aria-disabled", "true");
					fill(button, entry, role);
					if (!entry.info) {
						button.addEventListener("click", (e) => {
							if (e && typeof e.preventDefault === "function") e.preventDefault();
							if (!item_active(entry)) return;
							if (entry.keep_open) {
								run(entry);
								if (!closed) render();
								return;
							}
							// The sheet goes first (its history entry is handed over to whatever the item
							// opens, or stepped off), then the item runs.
							finish("user", false);
							run(entry);
						});
					}
					group.appendChild(button);
				});
				box.appendChild(group);
				body.appendChild(box);
			});
		}

		// A phone turned on its side past the breakpoint is not a phone: the toolbar is back.
		const resize = () => {
			if (opts.max_width && Number(win.innerWidth) > opts.max_width) finish("user", false);
		};

		close_button.addEventListener("click", () => finish("user", false));
		backdrop.addEventListener("click", () => finish("user", false));
		el.addEventListener("keydown", (e) => {
			// Keep Tab inside the sheet while it is open (it is modal).
			if (e.key !== "Tab") return;
			const all = [close_button].concat(Array.from(el.querySelectorAll ? el.querySelectorAll("button, select, input, [tabindex='0']") : []));
			// Only what can take focus: a field the page has hidden (the Project filter on the
			// Utilization view) is in the sheet but not on screen, and Tab must not escape onto it.
			const usable = all.filter((node, index) => all.indexOf(node) === index && focusable_now(node));
			if (usable.length < 2) return;
			const first = usable[0];
			const last = usable[usable.length - 1];
			if (e.shiftKey && doc.activeElement === first) {
				e.preventDefault();
				focus(last);
			} else if (!e.shiftKey && doc.activeElement === last) {
				e.preventDefault();
				focus(first);
			}
		});

		const handle = {
			el,
			top,
			body,
			refresh: () => {
				if (!closed) render();
			},
			close: (how) => finish(how || "user", false),
			is_open: () => !closed,
		};

		doc.body.appendChild(backdrop);
		doc.body.appendChild(el);
		open_sheet = handle;
		if (typeof opts.top === "function") {
			try {
				opts.top(top);
			} catch (e) {
				warn(e);
			}
		}
		render();
		win.addEventListener("resize", resize);
		if (guard) guard.open(overlay);
		focus(close_button);
		return handle;
	}

	// A route change (another week, view or page) closes an open menu: its list is on <body>, and
	// the screen it was for has gone. A page hiding calls close_all itself.
	if (typeof env.on_route_change === "function") {
		try {
			env.on_route_change(() => {
				if (open_menu) open_menu.close("route");
			});
		} catch (e) {
			warn(e);
		}
	}

	return {
		menu,
		sheet,
		tabs,
		sections_to_items,
		item_role,
		item_active,
		tidy,
		current_menu: () => open_menu,
		current_sheet: () => open_sheet,
		close_all: (how) => {
			if (open_menu) open_menu.close(how || "user");
			if (open_sheet) open_sheet.close(how || "user");
		},
	};
}
