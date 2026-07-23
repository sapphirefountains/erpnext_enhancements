/**
 * Embeddable Gantt widget — `erpnext_enhancements.gantt.mount(container, config)`.
 *
 * Loaded globally via erpnext_enhancements.bundle.js (app_include_js), so any
 * form script, desk page or Custom HTML Block can embed a Gantt with one call.
 * Rendering uses DHTMLX Gantt 10 Standard (MIT), vendored at
 * js/gantt_widget/lib/dhtmlxgantt.js — 600K, so it is lazy-loaded through
 * frappe.require on the first mount, never shipped in the global bundle.
 * Each mount gets its own instance via `Gantt.getGanttInstance()`, so multiple
 * embeds coexist on one page. Data comes exclusively from the whitelisted
 * `erpnext_enhancements.api.gantt.get_gantt_data` endpoint, which re-validates
 * the entire config server-side (permissions, fieldnames, filters).
 *
 * GLOBALS SHIM: the desk already defines `window.Gantt` (the vendored
 * frappe-gantt UMD, a raw app_include_js — used by the Project Schedule tab and
 * the Task list gantt). The DHTMLX UMD assigns BOTH `window.Gantt` (factory)
 * and `window.gantt` (a default singleton) while evaluating. The loader
 * therefore fetches the library source and evaluates it SYNCHRONOUSLY between
 * a snapshot and restore of those globals — an atomic bracket no other code
 * can interleave with (a <script src> tag, or frappe.require's parallel asset
 * loading, would leave window.Gantt clobbered until an async callback ran).
 * Fetching also makes retry real: frappe.require marks even failed loads as
 * executed and never re-fetches, whereas a failed fetch here is simply
 * re-attempted on the next mount. Nothing outside this file ever sees DHTMLX
 * globals.
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
 *     on_task_click: (id) => {},            // optional
 *   });
 *   w.ready.then(...); w.refresh(); w.destroy();
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

	class GanttWidget {
		constructor(el, config) {
			this.el = el;
			this.config = config || {};
			this.gantt = null;
			this.destroyed = false;
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

		async _init() {
			const factory = await ensure_lib();
			if (this.destroyed) {
				return;
			}
			const g = factory.getGanttInstance();
			this.gantt = g;

			// Read-only until the edit milestone lands (per-embed opt-in then).
			g.config.readonly = true;
			g.config.date_format = "%Y-%m-%d %H:%i";
			g.config.open_tree_initially = true;
			g.config.columns = this.config.columns || [
				{ name: "text", label: __("Task"), tree: true, width: "*" },
			];
			Object.assign(g.config, this.config.gantt || {});

			if (this.config.on_task_click) {
				g.attachEvent("onTaskClick", (id) => {
					this.config.on_task_click(id);
					return true;
				});
			}

			this.el.classList.add("ee-gantt-widget");
			g.init(this.el);
			await this.refresh();
		}

		async refresh() {
			const r = await frappe.call({
				method: "erpnext_enhancements.api.gantt.get_gantt_data",
				args: { config: this._server_config() },
			});
			if (this.destroyed) {
				return;
			}
			const data = r.message || { tasks: [], links: [], meta: {} };
			this.gantt.clearAll();
			this.gantt.parse({ data: data.tasks, links: data.links });
			this._clear_overlays();
			if (!data.tasks.length) {
				this._show_overlay(__("Nothing scheduled to show."), "empty");
			} else if (data.meta && data.meta.unscheduled) {
				this._show_note(
					__("{0} unscheduled (no dates) — not shown", [data.meta.unscheduled])
				);
			}
			return data;
		}

		_server_config() {
			const c = this.config;
			return {
				doctype: c.doctype,
				fields: c.fields,
				filters: c.filters || null,
				dependencies: c.dependencies || null,
				order_by: c.order_by || null,
				limit: c.limit || null,
			};
		}

		_show_overlay(message, kind) {
			if (this.destroyed) {
				return;
			}
			this._clear_overlays();
			const div = document.createElement("div");
			div.className = "ee-gantt-overlay" + (kind === "error" ? " ee-gantt-overlay-error" : "");
			div.textContent = message;
			this.el.appendChild(div);
		}

		_show_note(message) {
			const div = document.createElement("div");
			div.className = "ee-gantt-note";
			div.textContent = message;
			this.el.appendChild(div);
		}

		_clear_overlays() {
			this.el
				.querySelectorAll(":scope > .ee-gantt-overlay, :scope > .ee-gantt-note")
				.forEach((n) => n.remove());
		}

		destroy() {
			if (this.destroyed) {
				return;
			}
			this.destroyed = true;
			if (this.gantt) {
				this.gantt.destructor();
				this.gantt = null;
			}
			this.el.innerHTML = "";
			this.el.classList.remove("ee-gantt-widget");
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
