/**
 * Embeddable Gantt widget — `erpnext_enhancements.gantt.mount(container, config)`.
 *
 * Loaded globally via erpnext_enhancements.bundle.js (app_include_js), so any
 * form script, desk page or Custom HTML Block can embed a Gantt with one call.
 * Rendering uses DHTMLX Gantt 10 Standard (MIT), vendored at
 * js/gantt_widget/lib/dhtmlxgantt.js — 600K, so it is lazy-loaded on the first
 * mount, never shipped in the global bundle.
 *
 * STYLES ARE PER ROOT NODE (ensure_styles): Custom HTML Blocks render inside a
 * SHADOW ROOT, which document-level stylesheets cannot cross — the Projects
 * Dashboard Gantt rendered completely unstyled until the skin and chrome were
 * linked into the shadow root itself. The document always gets a copy too, so
 * the skin's @font-face (grid expander icons) registers.
 *
 * Each mount gets its own instance
 * via `Gantt.getGanttInstance()`, so multiple embeds coexist on one page. Data
 * comes exclusively from the whitelisted
 * `erpnext_enhancements.api.gantt.get_gantt_data` endpoint, which re-validates
 * the entire config server-side (permissions, fieldnames, filters).
 *
 * GLOBALS SHIM: the desk already defines `window.Gantt` (the vendored
 * frappe-gantt UMD, a raw app_include_js — legacy; kept until it is formally
 * retired). The DHTMLX UMD assigns BOTH
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
 *     templates: { ... },                   // optional raw gantt.templates overrides
 *     tooltip: true,                        // optional hover tooltip (bundled ext)
 *     toolbar: {                            // optional header controls
 *       today: true,                        //   Today button + marker + default view
 *       filters: [{                         //   checkbox-dropdown value filters
 *         fieldname: "status", label: "Status",
 *         options: ["Open", "Working"],     //   selected: [...] to pre-narrow
 *       }],
 *     },
 *     group_by: "custom_master_project",    // optional: composite grouping
 *                                           //   (or a list: first non-empty wins)
 *     extra_fields: ["project_type"],       // optional: raw values per row
 *     children: { doctype, link_field, fields, ..., lazy: true },
 *     lazy_children: true,                  // pair with children.lazy: draws a
 *                                           //   caret per branch and defers load
 *     on_task_expand: (id, task) => {},     // optional; fetch + add_rows(...)
 *     on_task_collapse: (id, task) => {},   // optional
 *     on_task_click: (id, task) => {},      // optional; composite ids are
 *                                           //   prefixed — route via
 *                                           //   task.ref_doctype/ref_name
 *   });
 *   w.ready.then(...); w.refresh(); w.destroy();
 *   w.set_zoom("week");                     // quarter_day|half_day|day|week|month
 *   w.set_filters({...});                   // replace config.filters + refetch
 *   w.add_rows(tasks, links);               // merge rows (lazy branch load)
 *   w.open_task_ids();                      // currently expanded ids
 *   // other config keys (children, group_by, ...) may be mutated on
 *   // w.config followed by w.refresh()
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

	// Only the VENDORED skin is loaded from a raw /assets path — it never
	// changes, so the 1-year immutable cache on /assets cannot serve it stale.
	// Our own chrome ships as a hashed bundle entry (as a raw path it was
	// frozen at its v1.163.0 content); CHROME_CSS_BUNDLE is resolved through
	// assets.json at runtime.
	const LIB_JS = "/assets/erpnext_enhancements/js/gantt_widget/lib/dhtmlxgantt.js";
	const LIB_CSS = "/assets/erpnext_enhancements/css/gantt_widget/dhtmlxgantt.css";
	const CHROME_CSS_BUNDLE = "gantt_widget.bundle.css";
	const CHROME_CSS_FALLBACK = "/assets/erpnext_enhancements/css/gantt_widget.bundle.css";

	function chrome_css_href() {
		try {
			const resolved = frappe.assets.bundled_asset(CHROME_CSS_BUNDLE);
			// bundled_asset returns the input unchanged when assets.json has no
			// entry — that bare filename is not a usable URL, so fall back.
			if (resolved && resolved.startsWith("/")) {
				return resolved;
			}
		} catch (e) {
			// fall through
		}
		return CHROME_CSS_FALLBACK;
	}

	// Root nodes (document or a ShadowRoot) that already carry our stylesheets.
	// Custom HTML Blocks render inside a shadow root, and document-level styles
	// do NOT cross that boundary — each root needs its own <link>.
	const STYLED_ROOTS = new WeakSet();

	function link_style(target, href) {
		return new Promise((resolve) => {
			const link = document.createElement("link");
			link.rel = "stylesheet";
			link.href = href;
			// resolve either way: missing styling degrades, it must not block
			link.onload = resolve;
			link.onerror = resolve;
			target.appendChild(link);
		});
	}

	function ensure_styles(root) {
		const jobs = [];
		// Always style the document too, even for a shadow-DOM embed: @font-face
		// (the skin's dhx-gantt-icons) only registers from a document-level
		// stylesheet, so grid expander icons would otherwise be blank inside a
		// shadow root.
		if (!STYLED_ROOTS.has(document)) {
			STYLED_ROOTS.add(document);
			jobs.push(link_style(document.head, LIB_CSS), link_style(document.head, chrome_css_href()));
		}
		if (root && root !== document && root.nodeType === 11 && !STYLED_ROOTS.has(root)) {
			STYLED_ROOTS.add(root);
			jobs.push(link_style(root, LIB_CSS), link_style(root, chrome_css_href()));
		}
		return Promise.all(jobs);
	}

	const DAY_MS = 24 * 60 * 60 * 1000;

	let lib_promise = null;

	function ensure_lib() {
		if (lib_promise) {
			return lib_promise;
		}
		lib_promise = (async () => {
			// Stylesheets are handled per root node by ensure_styles(), not
			// here — a single document-level copy cannot serve a shadow-DOM
			// embed. This only loads the library itself; it deliberately does
			// not use frappe.require — see the GLOBALS SHIM note in the header.
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

	// Scale presets for set_zoom(); names mirror the legacy frappe-gantt view
	// modes so existing toolbar buttons map one-to-one.
	const ZOOM_PRESETS = {
		quarter_day: [
			{ unit: "day", step: 1, format: "%d %M" },
			{ unit: "hour", step: 6, format: "%H:%i" },
		],
		half_day: [
			{ unit: "day", step: 1, format: "%d %M" },
			{ unit: "hour", step: 12, format: "%H:%i" },
		],
		day: [
			{ unit: "month", step: 1, format: "%F %Y" },
			{ unit: "day", step: 1, format: "%d" },
		],
		week: [
			{ unit: "month", step: 1, format: "%F %Y" },
			{ unit: "week", step: 1, format: "%W" },
		],
		month: [
			{ unit: "year", step: 1, format: "%Y" },
			{ unit: "month", step: 1, format: "%M" },
		],
	};

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
			// config.today enables the marker/default view without the widget
			// toolbar (for hosts that render their own Today button);
			// toolbar.today additionally renders the button.
			return !!(this.config.today || (this.config.toolbar && this.config.toolbar.today));
		}

		async _init() {
			// Styles first: DHTMLX measures its container at init(), so the
			// host must already have its real height. getRootNode() puts the
			// stylesheets inside the shadow root for Custom HTML Block embeds.
			const [factory] = await Promise.all([ensure_lib(), ensure_styles(this.el.getRootNode())]);
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
			// Lazy branches: the server marks roots with `$has_child` +
			// `open: false` (see get_gantt_data children.lazy) so DHTMLX draws a
			// collapsed caret for children it has not loaded; opening one fires
			// on_task_expand, whose handler feeds rows back via add_rows().
			// open_tree_initially would force every branch open (and fire an
			// expand for each), so it defaults off whenever lazy is in play.
			g.config.branch_loading = !!this.config.lazy_children;
			g.config.open_tree_initially = !this.config.lazy_children;
			g.config.columns = this.config.columns || [
				{ name: "text", label: __("Task"), tree: true, width: "*" },
			];
			if (this._today_enabled()) {
				// Today indicator via core cell-class templates — the marker
				// extension is NOT in the Standard single-file bundle (its
				// bundled extensions are only fullscreen / keyboard_navigation /
				// quick_info / tooltip / export_api), so gantt.plugins({marker})
				// silently no-ops there. The check must be span-containment,
				// not date equality: in week/month zooms a cell's date is the
				// unit START (1st of month), which equals today one day a month.
				const cell_holds_today = (date) => {
					const scales = g.config.scales || [];
					const unit = (scales.length && scales[scales.length - 1].unit) || "day";
					const now = new Date();
					return date <= now && now < g.date.add(date, 1, unit);
				};
				g.templates.timeline_cell_class = (item, date) =>
					cell_holds_today(date) ? "ee-gantt-today-col" : "";
				g.templates.scale_cell_class = (date) =>
					cell_holds_today(date) ? "ee-gantt-today-col" : "";
			}
			if (this.config.tooltip) {
				// tooltip IS one of the extensions bundled in the Standard build
				g.plugins({ tooltip: true });
				const esc = (frappe.utils && frappe.utils.escape_html) || ((s) => String(s));
				g.templates.tooltip_text = (start, end, task) => {
					const dates = `${g.templates.tooltip_date_format(start)} – ${g.templates.tooltip_date_format(end)}`;
					const progress =
						task.progress != null ? `<br/>${Math.round(task.progress * 100)}%` : "";
					return `<b>${esc(task.text || "")}</b><br/>${dates}${progress}`;
				};
			}
			Object.assign(g.config, this.config.gantt || {});
			Object.assign(g.templates, this.config.templates || {});
			if (this.config.zoom && ZOOM_PRESETS[this.config.zoom]) {
				g.config.scales = ZOOM_PRESETS[this.config.zoom];
			}

			if (this.config.on_task_click) {
				g.attachEvent("onTaskClick", (id) => {
					this.config.on_task_click(id, g.isTaskExists(id) ? g.getTask(id) : null);
					return true;
				});
			}
			if (this.config.on_task_expand) {
				g.attachEvent("onTaskOpened", (id) => {
					if (g.isTaskExists(id)) {
						this.config.on_task_expand(id, g.getTask(id));
					}
					return true;
				});
			}
			if (this.config.on_task_collapse) {
				g.attachEvent("onTaskClosed", (id) => {
					if (g.isTaskExists(id)) {
						this.config.on_task_collapse(id, g.getTask(id));
					}
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
			if (this.destroyed) {
				return;
			}
			if (!this.gantt) {
				// Mount still initializing (or the library failed to load).
				// The pending first render reads the live config, so deferring
				// to `ready` both avoids a null deref and picks up any config
				// mutation the caller just made.
				return this.ready;
			}
			// Overlapping refreshes: only the latest requested may render, or a
			// slow earlier response would overwrite a newer one on arrival.
			const seq = (this._refresh_seq = (this._refresh_seq || 0) + 1);
			const r = await frappe.call({
				method: "erpnext_enhancements.api.gantt.get_gantt_data",
				args: { config: this._server_config() },
			});
			if (this.destroyed || seq !== this._refresh_seq) {
				return;
			}
			const data = r.message || { tasks: [], links: [], meta: {} };
			this.data = data; // last response, for hosts that build UI from it
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

		/**
		 * Merge extra rows into the live chart without a full reload — the
		 * lazy-branch path (children fetched when a caret opens). Rows already
		 * present are skipped, so a double expand cannot duplicate or throw.
		 * Returns the number of tasks actually added.
		 */
		add_rows(tasks, links) {
			if (!this.gantt || this.destroyed) {
				return 0;
			}
			const fresh = (tasks || []).filter((t) => t && !this.gantt.isTaskExists(t.id));
			const fresh_links = (links || []).filter((l) => {
				try {
					return l && !this.gantt.getLink(l.id);
				} catch (e) {
					return true; // getLink throws when absent
				}
			});
			if (!fresh.length && !fresh_links.length) {
				return 0;
			}
			this.gantt.parse({ data: fresh, links: fresh_links });
			return fresh.length;
		}

		/** Ids currently expanded — lets a host persist/restore the open tree. */
		open_task_ids() {
			if (!this.gantt || this.destroyed) {
				return [];
			}
			const open = [];
			this.gantt.eachTask((task) => {
				if (task.$open && task.id) {
					open.push(task.id);
				}
			});
			return open;
		}

		set_zoom(preset) {
			const scales = ZOOM_PRESETS[preset];
			if (!scales) {
				return;
			}
			// Persist the preset so a call during the initial mount is not
			// lost — _init applies config.zoom once the library is up.
			this.config.zoom = preset;
			if (!this.gantt) {
				return;
			}
			this.gantt.config.scales = scales;
			if (this._rendered) {
				this.gantt.render();
			}
		}

		set_filters(filters) {
			this.config.filters = filters;
			return this.refresh();
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
			// wire-format strings sort lexicographically. Skip dateless rows
			// (composite group / container rows carry no dates): a range
			// accidentally derived from `undefined` collapses to today±7d and
			// DHTMLX then DROPS every task outside the timescale.
			let min = null;
			let max = null;
			tasks.forEach((t) => {
				if (t.start_date && (!min || t.start_date < min)) {
					min = t.start_date;
				}
				if (t.end_date && (!max || t.end_date > max)) {
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
				group_by: c.group_by || null,
				children: c.children || null,
				extra_fields: c.extra_fields || null,
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
