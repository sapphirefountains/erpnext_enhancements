/**
 * Embeddable Gantt widget — `erpnext_enhancements.gantt.mount(container, config)`.
 *
 * Loaded globally via erpnext_enhancements.bundle.js (app_include_js), so any
 * form script, desk page or Custom HTML Block can embed a Gantt with one call.
 * Rendering uses DHTMLX Gantt 10 Standard (MIT), vendored at
 * js/gantt_widget/lib/dhtmlxgantt.js — 600K, so it is lazy-loaded on the first
 * mount, never shipped in the global bundle. Each mount gets its own instance
 * via `Gantt.getGanttInstance()`, so multiple embeds coexist on one page. Data
 * comes exclusively from the whitelisted
 * `erpnext_enhancements.api.gantt.get_gantt_data` endpoint, which re-validates
 * the entire config server-side (permissions, fieldnames, filters).
 *
 * GLOBALS SHIM: the desk already defines `window.Gantt` (the vendored
 * frappe-gantt UMD, a raw app_include_js — used by the Projects Dashboard
 * portfolio Gantt and the Task list gantt). The DHTMLX UMD assigns BOTH
 * `window.Gantt` (factory) and `window.gantt` (a default singleton) while
 * evaluating. The loader therefore fetches the library source and evaluates it
 * SYNCHRONOUSLY between a snapshot and restore of those globals — an atomic
 * bracket no other code can interleave with (a <script src> tag, or
 * frappe.require's parallel asset loading, would leave window.Gantt clobbered
 * until an async callback ran). Fetching also makes retry real: frappe.require
 * marks even failed loads as executed and never re-fetches, whereas a failed
 * fetch here is simply re-attempted on the next mount. Nothing outside this
 * file ever sees DHTMLX globals.
 *
 * Usage:
 *   const w = erpnext_enhancements.gantt.mount(el, {
 *     doctype: "Task",
 *     fields: { text: "subject", start: "exp_start_date", end: "exp_end_date",
 *               progress: "progress", parent: "parent_task" },
 *     filters: { project: "PRJ-0001" },     // optional
 *     dependencies: "depends_on",           // optional: Table field with links
 *     order_by: "exp_start_date asc",       // optional
 *     limit: 500,                           // optional (server-clamped)
 *     columns: [...],                       // optional DHTMLX column defs
 *     gantt: { ... },                       // optional raw gantt.config overrides
 *     toolbar: {                            // optional header controls
 *       today: true,                        //   Today button + marker + default view
 *       filters: [{                         //   checkbox-dropdown value filters
 *         fieldname: "status", label: "Status",
 *         options: ["Open", "Working"],     //   selected: [...] to pre-narrow
 *       }],
 *     },
 *     on_task_click: (id) => {},            // optional
 *   });
 *   w.ready.then(...); w.refresh(); w.destroy();
 *
 * Toolbar filters apply as server-side `["in", [...]]` filters (fieldnames are
 * re-validated server-side like everything else) and re-fetch on change,
 * debounced. With `toolbar.today` the chart opens scrolled to today, tints
 * today's column (red-edged, via core cell-class templates), and pads the
 * scale so today is always inside the range; later refreshes (realtime,
 * filter changes) preserve the scroll position instead.
 *
 * Widgets are read-only. `config.editable` is reserved for a later milestone
 * (per-embed opt-in) and currently logs a warning and stays read-only.
 * Re-mounting onto a container that already hosts a widget destroys the old
 * instance first, so callers may simply mount again on every form refresh.
 */
frappe.provide("erpnext_enhancements.gantt");

(function () {
	const NS = erpnext_enhancements.gantt;
	if (NS.mount) {
		return; // already initialized (double bundle evaluation)
	}

	const LIB_JS = "/assets/erpnext_enhancements/js/gantt_widget/lib/dhtmlxgantt.js";
	const LIB_CSS = "/assets/erpnext_enhancements/css/gantt_widget/dhtmlxgantt.css";
	const WIDGET_CSS = "/assets/erpnext_enhancements/css/gantt_widget/gantt_widget.css";

	const DAY_MS = 24 * 60 * 60 * 1000;

	let lib_promise = null;

	function ensure_lib() {
		if (lib_promise) {
			return lib_promise;
		}
		lib_promise = (async () => {
			// Styles go through frappe.require (it handles css; a css failure
			// only degrades styling). The library JS deliberately does NOT —
			// see the GLOBALS SHIM note in the file header.
			await new Promise((resolve) => frappe.require([LIB_CSS, WIDGET_CSS], resolve));
			const resp = await fetch(LIB_JS);
			if (!resp.ok) {
				throw new Error(`dhtmlx-gantt fetch failed (HTTP ${resp.status})`);
			}
			const src = await resp.text();

			const had_upper = Object.prototype.hasOwnProperty.call(window, "Gantt");
			const prev_upper = window.Gantt;
			const had_lower = Object.prototype.hasOwnProperty.call(window, "gantt");
			const prev_lower = window.gantt;
			let factory;
			try {
				// Synchronous global-scope eval (inline <script> injection) —
				// the snapshot/restore bracket is atomic.
				frappe.dom.eval(src);
				factory = window.Gantt;
			} finally {
				// Restore the desk's globals no matter what happened, so a
				// broken eval can never leave frappe-gantt clobbered.
				if (had_upper) {
					window.Gantt = prev_upper;
				} else {
					delete window.Gantt;
				}
				if (had_lower) {
					window.gantt = prev_lower;
				} else {
					delete window.gantt;
				}
			}
			if (!factory || typeof factory.getGanttInstance !== "function") {
				throw new Error("dhtmlx-gantt failed to load (no factory)");
			}
			NS._factory = factory;
			return factory;
		})();
		// A failed load should not poison every future mount — the next mount
		// re-fetches (the fetch is not cached-as-executed the way frappe.require
		// assets are).
		lib_promise.catch(() => {
			lib_promise = null;
		});
		return lib_promise;
	}

	// container element -> live widget (auto-destroy on remount; WeakMap so a
	// container removed from the DOM without an explicit destroy() can be GC'd)
	const REGISTRY = new WeakMap();

	function resolve_container(container) {
		let el = container;
		if (typeof container === "string") {
			el = document.querySelector(container);
		} else if (container && container.jquery) {
			el = container.get(0);
		}
		if (!el || el.nodeType !== 1) {
			throw new Error("erpnext_enhancements.gantt.mount: container element not found");
		}
		return el;
	}

	// "%Y-%m-%d %H:%M" (the API's wire format). Manual parse — Safari rejects
	// the bare "YYYY-MM-DD HH:MM" form in new Date().
	function str_to_date(s) {
		const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(s || "");
		if (!m) {
			return null;
		}
		return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
	}

	class GanttWidget {
		constructor(el, config) {
			this.el = el;
			this.config = config || {};
			this.gantt = null;
			this.destroyed = false;
			this._rendered = false;
			// fieldname -> Set of selected options (all selected by default)
			this._filter_state = {};
			this._toolbar_filters().forEach((f) => {
				this._filter_state[f.fieldname] = new Set(f.selected || f.options || []);
			});
			if (this.config.editable) {
				// eslint-disable-next-line no-console
				console.warn("erpnext_enhancements.gantt: editable embeds are not implemented yet; staying read-only");
			}
			this.ready = this._init();
			this.ready.catch((e) => {
				// eslint-disable-next-line no-console
				console.error("erpnext_enhancements.gantt:", e);
				this._show_overlay(__("Could not load the Gantt chart."), "error");
			});
		}

		_toolbar_filters() {
			return (this.config.toolbar && this.config.toolbar.filters) || [];
		}

		_today_enabled() {
			return !!(this.config.toolbar && this.config.toolbar.today);
		}

		async _init() {
			const factory = await ensure_lib();
			if (this.destroyed) {
				return;
			}

			this.el.classList.add("ee-gantt-widget");
			this.el.innerHTML = "";
			if (this.config.toolbar) {
				this._build_toolbar();
			}
			this.wrap = document.createElement("div");
			this.wrap.className = "ee-gantt-chart-wrap";
			this.chart_el = document.createElement("div");
			this.chart_el.className = "ee-gantt-chart";
			this.wrap.appendChild(this.chart_el);
			this.el.appendChild(this.wrap);

			const g = factory.getGanttInstance();
			this.gantt = g;

			// Read-only until the edit milestone lands (per-embed opt-in then).
			g.config.readonly = true;
			g.config.date_format = "%Y-%m-%d %H:%i";
			g.config.open_tree_initially = true;
			g.config.columns = this.config.columns || [
				{ name: "text", label: __("Task"), tree: true, width: "*" },
			];
			if (this._today_enabled()) {
				// Today indicator via core cell-class templates — the marker
				// extension is NOT in the Standard single-file bundle (its
				// bundled extensions are only fullscreen / keyboard_navigation /
				// quick_info / tooltip / export_api), so gantt.plugins({marker})
				// silently no-ops there.
				const is_today = (date) => {
					const now = new Date();
					return (
						date.getFullYear() === now.getFullYear() &&
						date.getMonth() === now.getMonth() &&
						date.getDate() === now.getDate()
					);
				};
				g.templates.timeline_cell_class = (item, date) =>
					is_today(date) ? "ee-gantt-today-col" : "";
				g.templates.scale_cell_class = (date) => (is_today(date) ? "ee-gantt-today-col" : "");
			}
			Object.assign(g.config, this.config.gantt || {});

			if (this.config.on_task_click) {
				g.attachEvent("onTaskClick", (id) => {
					this.config.on_task_click(id);
					return true;
				});
			}

			g.init(this.chart_el);
			await this.refresh();
		}

		// ------------------------------------------------------------------
		// Toolbar (filter dropdowns + Today)
		// ------------------------------------------------------------------

		_build_toolbar() {
			const bar = document.createElement("div");
			bar.className = "ee-gantt-toolbar";
			this._toolbar_filters().forEach((f) => bar.appendChild(this._build_filter(f)));
			const spacer = document.createElement("div");
			spacer.className = "ee-gantt-toolbar-spacer";
			bar.appendChild(spacer);
			if (this._today_enabled()) {
				const btn = document.createElement("button");
				btn.type = "button";
				btn.className = "btn btn-default btn-sm ee-gantt-today-btn";
				btn.textContent = __("Today");
				btn.addEventListener("click", () => this.scroll_to_today());
				bar.appendChild(btn);
			}
			this.el.appendChild(bar);
			this.toolbar = bar;
			// close open filter menus on outside clicks
			this._doc_click = (e) => {
				if (!bar.contains(e.target)) {
					bar.querySelectorAll(".ee-gantt-filter.open").forEach((n) => n.classList.remove("open"));
				}
			};
			document.addEventListener("mousedown", this._doc_click);
		}

		_build_filter(f) {
			const wrap = document.createElement("div");
			wrap.className = "ee-gantt-filter";
			const btn = document.createElement("button");
			btn.type = "button";
			btn.className = "btn btn-default btn-sm ee-gantt-filter-btn";
			const menu = document.createElement("div");
			menu.className = "ee-gantt-filter-menu";
			const selected = this._filter_state[f.fieldname];
			const options = f.options || [];
			const sync_label = () => {
				const label = f.label || f.fieldname;
				btn.textContent =
					selected.size === options.length
						? `${label}: ${__("All")}`
						: `${label}: ${__("{0} of {1}", [selected.size, options.length])}`;
			};
			options.forEach((opt) => {
				const row = document.createElement("label");
				row.className = "ee-gantt-filter-option";
				const cb = document.createElement("input");
				cb.type = "checkbox";
				cb.checked = selected.has(opt);
				cb.addEventListener("change", () => {
					if (cb.checked) {
						selected.add(opt);
					} else {
						selected.delete(opt);
					}
					sync_label();
					this._queue_filter_refresh();
				});
				row.appendChild(cb);
				row.appendChild(document.createTextNode(" " + opt));
				menu.appendChild(row);
			});
			btn.addEventListener("click", () => wrap.classList.toggle("open"));
			wrap.appendChild(btn);
			wrap.appendChild(menu);
			sync_label();
			return wrap;
		}

		_queue_filter_refresh() {
			// debounce rapid checkbox toggling into one fetch
			clearTimeout(this._filter_timer);
			this._filter_timer = setTimeout(() => {
				if (!this.destroyed) {
					this.refresh();
				}
			}, 400);
		}

		_filters_narrowed() {
			return this._toolbar_filters().some((f) => {
				const sel = this._filter_state[f.fieldname];
				return sel && sel.size < (f.options || []).length;
			});
		}

		_effective_filters() {
			const base = this.config.filters;
			const extra = [];
			this._toolbar_filters().forEach((f) => {
				const sel = this._filter_state[f.fieldname];
				// all options selected = no narrowing (also keeps rows whose
				// value is empty/unknown visible)
				if (sel && sel.size < (f.options || []).length) {
					extra.push([f.fieldname, "in", [...sel]]);
				}
			});
			if (!extra.length) {
				return base || null;
			}
			if (Array.isArray(base)) {
				return [...base, ...extra];
			}
			const merged = Object.assign({}, base || {});
			extra.forEach(([fieldname, op, values]) => {
				merged[fieldname] = [op, values];
			});
			return merged;
		}

		// ------------------------------------------------------------------
		// Data
		// ------------------------------------------------------------------

		async refresh() {
			const r = await frappe.call({
				method: "erpnext_enhancements.api.gantt.get_gantt_data",
				args: { config: this._server_config() },
			});
			if (this.destroyed) {
				return;
			}
			const data = r.message || { tasks: [], links: [], meta: {} };
			// Re-renders (realtime updates, filter changes) keep the viewport;
			// only the very first render auto-scrolls to today.
			const scroll = this._rendered ? this.gantt.getScrollState() : null;
			this._apply_range(data.tasks);
			this.gantt.clearAll();
			this.gantt.parse({ data: data.tasks, links: data.links });
			this._clear_overlays();
			if (!data.tasks.length) {
				this._show_overlay(
					this._filters_narrowed()
						? __("Nothing matches the current filters.")
						: __("Nothing scheduled to show."),
					"empty"
				);
			} else if (data.meta && data.meta.unscheduled) {
				this._show_note(
					__("{0} unscheduled (no dates) — not shown", [data.meta.unscheduled])
				);
			}
			if (!this._rendered && this._today_enabled() && data.tasks.length) {
				this.gantt.showDate(new Date());
			} else if (scroll) {
				this.gantt.scrollTo(scroll.x, scroll.y);
			}
			this._rendered = true;
			return data;
		}

		scroll_to_today() {
			if (this.gantt && this._rendered) {
				this.gantt.showDate(new Date());
			}
		}

		// With the today marker on, pad the scale a week past the data range
		// and force it to include today — DHTMLX otherwise clamps the scale to
		// the data, leaving today (marker, showDate target) out of range.
		_apply_range(tasks) {
			if (!this._today_enabled()) {
				return;
			}
			if (!tasks.length) {
				this.gantt.config.start_date = undefined;
				this.gantt.config.end_date = undefined;
				return;
			}
			// wire-format strings sort lexicographically
			let min = tasks[0].start_date;
			let max = tasks[0].end_date;
			tasks.forEach((t) => {
				if (t.start_date < min) {
					min = t.start_date;
				}
				if (t.end_date > max) {
					max = t.end_date;
				}
			});
			const today = new Date();
			today.setHours(0, 0, 0, 0);
			let start = str_to_date(min);
			let end = str_to_date(max);
			if (!start || start > today) {
				start = today;
			}
			if (!end || end < today) {
				end = today;
			}
			this.gantt.config.start_date = new Date(start.getTime() - 7 * DAY_MS);
			this.gantt.config.end_date = new Date(end.getTime() + 7 * DAY_MS);
		}

		_server_config() {
			const c = this.config;
			return {
				doctype: c.doctype,
				fields: c.fields,
				filters: this._effective_filters(),
				dependencies: c.dependencies || null,
				order_by: c.order_by || null,
				limit: c.limit || null,
			};
		}

		// ------------------------------------------------------------------
		// Overlays (over the chart area only — the toolbar stays clickable so
		// a filter that matched nothing can be widened again)
		// ------------------------------------------------------------------

		_overlay_host() {
			return this.wrap || this.el;
		}

		_show_overlay(message, kind) {
			if (this.destroyed) {
				return;
			}
			this._clear_overlays();
			const div = document.createElement("div");
			div.className = "ee-gantt-overlay" + (kind === "error" ? " ee-gantt-overlay-error" : "");
			div.textContent = message;
			this._overlay_host().appendChild(div);
		}

		_show_note(message) {
			const div = document.createElement("div");
			div.className = "ee-gantt-note";
			div.textContent = message;
			this._overlay_host().appendChild(div);
		}

		_clear_overlays() {
			this._overlay_host()
				.querySelectorAll(":scope > .ee-gantt-overlay, :scope > .ee-gantt-note")
				.forEach((n) => n.remove());
		}

		destroy() {
			if (this.destroyed) {
				return;
			}
			this.destroyed = true;
			clearTimeout(this._filter_timer);
			if (this._doc_click) {
				document.removeEventListener("mousedown", this._doc_click);
				this._doc_click = null;
			}
			if (this.gantt) {
				this.gantt.destructor();
				this.gantt = null;
			}
			this.el.innerHTML = "";
			this.el.classList.remove("ee-gantt-widget");
			this.wrap = null;
			this.chart_el = null;
			this.toolbar = null;
			if (REGISTRY.get(this.el) === this) {
				REGISTRY.delete(this.el);
			}
		}
	}

	NS.mount = function (container, config) {
		const el = resolve_container(container);
		const previous = REGISTRY.get(el);
		if (previous) {
			previous.destroy();
		}
		const widget = new GanttWidget(el, config);
		REGISTRY.set(el, widget);
		return widget;
	};
})();
