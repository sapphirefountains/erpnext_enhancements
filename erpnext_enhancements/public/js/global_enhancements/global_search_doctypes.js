/**
 * global_search_doctypes.js — surface matching DocTypes, expanded into their
 * standard *views*, in the desk Global Search results page.
 *
 * The problem: pressing Enter in the awesomebar opens frappe's Global Search
 * modal (frappe.searchdialog), which searches document *content* (the
 * __global_search index) via frappe.search.utils.get_global_results — that
 * provider only emits `fetch_type: "Global"` sets and never lists DocTypes as
 * navigation targets. So a user who typed a DocType name saw it in the live
 * awesomebar dropdown, but hitting Enter landed on a results page with no way
 * to reach it. (Adding the DocType to Global Search Settings is not an option:
 * frappe hard-blocks Core-module doctypes there — "Core Modules … cannot be
 * searched in Global Search".)
 *
 * The fix: the SearchDialog already knows how to render `fetch_type: "Nav"`
 * result sets (render_data() gives them a sidebar entry + "All Results" section
 * via render_result, paginated through nav_lists) — nothing feeds it one by
 * default. We patch SearchDialog.parse_results to prepend a "DocTypes" Nav set.
 *
 * Rather than a single link per DocType, each matched DocType is expanded into
 * its available **standard views** — List, Report, Dashboard, Kanban,
 * Calendar/Gantt, Tree, Image, Map — mirroring frappe's own list-view switcher
 * (frappe/list/list_view_select.js), so you can jump straight to any view. We
 * deliberately never route to the DocType definition form for editing; only to
 * data views. Single-type doctypes (System Settings, etc.) have no list views,
 * so they keep a single link to their settings form (their only "view").
 *
 * View availability uses the same conditions frappe's switcher does:
 *   List/Dashboard  — always
 *   Report          — frappe.model.can_get_report(dt)
 *   Kanban          — always except File; opened via the same board lookup /
 *                     "create board" flow frappe uses (onclick, at click time)
 *   Calendar/Gantt  — a standard calendar is registered (frappe.views.calendar)
 *   Tree            — frappe.boot.tree_view_doctypes
 *   Image / Map     — best-effort from already-loaded meta (image_field / geo)
 * All checks are synchronous and in-memory, so parse_results stays synchronous.
 *
 * Gating: injection only in unfiltered global-search mode — skipped when a
 * DocType content filter is active (drill-down) or in "#tag" mode. Pagination
 * ("Show more") calls get_global_results directly and never routes through
 * parse_results, so it is unaffected too.
 *
 * Loaded via erpnext_enhancements.bundle.js (app_include_js). Prototype patch,
 * so it applies to the single frappe.searchdialog.search instance created in
 * frappe's toolbar.js regardless of creation order.
 */
frappe.provide("frappe.search");

$(document).on("app_ready", function () {
	const SearchDialog = frappe.search && frappe.search.SearchDialog;
	if (!SearchDialog || !SearchDialog.prototype || SearchDialog.prototype._ee_doctype_nav_patched) {
		return;
	}
	SearchDialog.prototype._ee_doctype_nav_patched = true;

	// Cap on distinct DocTypes expanded up front; the results UI paginates the
	// rest of the Nav section through its own "More" control (nav_lists).
	const EE_MAX_DOCTYPES = 6;

	// Standard views in the order we present them per DocType (a subset of
	// frappe.views.view_modes; "Inbox" is Communication-only and omitted).
	const EE_VIEW_ORDER = [
		"List",
		"Report",
		"Dashboard",
		"Kanban",
		"Calendar",
		"Gantt",
		"Tree",
		"Image",
		"Map",
	];

	// Highlighted DocType name for a label, reusing the awesomebar's own fuzzy
	// matcher so highlighting matches the live dropdown. Falls back to the plain
	// (escaped) translated name.
	function ee_marked_name(keyword, dt) {
		const utils = frappe.search && frappe.search.utils;
		try {
			if (utils && typeof utils.fuzzy_search === "function") {
				const r = utils.fuzzy_search(keyword, dt, true);
				if (r && r.marked_string) return r.marked_string;
			}
		} catch (e) {
			/* fall through to plain label */
		}
		return frappe.utils.escape_html(__(dt));
	}

	// Open a DocType's Kanban the way frappe's switcher does: route to an
	// existing board, else offer to create one. Deferred to click time so no
	// board lookups happen during search. Closes the search modal first.
	function ee_open_kanban(dt) {
		const sd =
			frappe.searchdialog &&
			frappe.searchdialog.search &&
			frappe.searchdialog.search.search_dialog;
		frappe.db.get_value("Kanban Board", { reference_doctype: dt }, "name", (r) => {
			if (sd && sd.hide) sd.hide();
			if (r && r.name) {
				frappe.set_route("list", dt, "kanban", r.name);
			} else if (frappe.views.KanbanView && frappe.views.KanbanView.show_kanban_dialog) {
				frappe.views.KanbanView.show_kanban_dialog(dt);
			} else {
				frappe.set_route("List", dt);
			}
		});
	}

	// Available standard views for a (non-single) DocType, as {view, route} or
	// {view, onclick}. Mirrors the conditions in frappe's list_view_select.js.
	function ee_view_specs(dt) {
		const specs = [{ view: "List", route: ["List", dt] }];

		if (frappe.model.can_get_report(dt)) {
			specs.push({ view: "Report", route: ["List", dt, "Report"] });
		}
		specs.push({ view: "Dashboard", route: ["List", dt, "Dashboard"] });

		if (dt !== "File") {
			specs.push({ view: "Kanban", onclick: () => ee_open_kanban(dt) });
		}

		// Standard calendar registered for this doctype → Calendar + Gantt.
		if (frappe.views.calendar && frappe.views.calendar[dt]) {
			specs.push({ view: "Calendar", route: ["List", dt, "Calendar", "default"] });
			specs.push({ view: "Gantt", route: ["List", dt, "Gantt"] });
		}

		if ((frappe.boot.tree_view_doctypes || []).includes(dt)) {
			specs.push({ view: "Tree", route: ["Tree", dt] });
		}

		// Best-effort from meta if it is already loaded (never forces a fetch).
		const meta = frappe.meta && frappe.meta.get_docfields ? frappe.get_meta(dt) : null;
		if (meta) {
			if (meta.image_field) {
				specs.push({ view: "Image", route: ["List", dt, "Image"] });
			}
			const fields = meta.fields || [];
			const has_geo =
				fields.some((f) => f.fieldname === "location" && f.fieldtype === "Geolocation") ||
				(fields.some((f) => f.fieldname === "latitude") &&
					fields.some((f) => f.fieldname === "longitude"));
			if (has_geo) {
				specs.push({ view: "Map", route: ["List", dt, "Map"] });
			}
		}

		// Present in EE_VIEW_ORDER order.
		return specs.sort(
			(a, b) => EE_VIEW_ORDER.indexOf(a.view) - EE_VIEW_ORDER.indexOf(b.view)
		);
	}

	// One Nav result: "Marketing List", "Marketing Report", … . onclick specs
	// carry no route (row click fires onclick); route specs navigate normally.
	function ee_nav_result(dt, marked, spec) {
		const view_label = __(spec.view);
		const res = {
			label: `${marked} ${view_label}`,
			value: `${dt} ${spec.view}`,
			match: dt,
		};
		if (spec.onclick) res.onclick = spec.onclick;
		else res.route = spec.route;
		return res;
	}

	// Build a `fetch_type: "Nav"` set of matching DocTypes expanded into views.
	// Reuses get_doctypes for permission-filtered, relevance-ranked matching.
	function ee_build_doctype_nav_set(keyword) {
		const utils = frappe.search && frappe.search.utils;
		if (!utils || typeof utils.get_doctypes !== "function") return null;
		if (!keyword || keyword.length < 2) return null;

		let options;
		try {
			options = utils.get_doctypes(keyword) || [];
		} catch (e) {
			console.error("[ERPNext Enhancements] get_doctypes failed in global search", e);
			return null;
		}

		// Collapse get_doctypes' per-view entries to unique DocTypes, keeping the
		// best relevance index. (get_doctypes already filtered by can_read /
		// can_search, so every DocType here is one the user may navigate.)
		const order = [];
		const by_dt = Object.create(null);
		options.forEach((o) => {
			if (!o || !o.match) return;
			const dt = o.match;
			if (by_dt[dt] === undefined) {
				by_dt[dt] = { dt, index: o.index || 0 };
				order.push(dt);
			} else {
				by_dt[dt].index = Math.max(by_dt[dt].index, o.index || 0);
			}
		});

		const singles = frappe.boot.single_types || [];
		const doctypes = order
			.map((dt) => by_dt[dt])
			.sort((a, b) => b.index - a.index)
			.slice(0, EE_MAX_DOCTYPES);

		const results = [];
		doctypes.forEach(({ dt }) => {
			const marked = ee_marked_name(keyword, dt);
			if (singles.includes(dt)) {
				// Single-type: no list views — link to its settings form (its only
				// view). This is the single's own document, not the DocType meta.
				results.push({ label: marked, value: dt, match: dt, route: ["Form", dt, dt] });
			} else {
				ee_view_specs(dt).forEach((spec) =>
					results.push(ee_nav_result(dt, marked, spec))
				);
			}
		});

		if (!results.length) return null;
		return { title: __("DocTypes"), fetch_type: "Nav", results: results };
	}

	const original_parse_results = SearchDialog.prototype.parse_results;
	SearchDialog.prototype.parse_results = function (result_sets, keyword) {
		try {
			const is_global = this.searches && this.search === this.searches["global_search"];
			// Skip when drilled into a single DocType's content or in "#tag" mode.
			if (is_global && !this.global_doctype_filter) {
				const nav_set = ee_build_doctype_nav_set(keyword);
				if (nav_set) {
					result_sets = [nav_set].concat(result_sets || []);
				}
			}
		} catch (e) {
			console.error("[ERPNext Enhancements] DocType view injection failed", e);
		}
		return original_parse_results.call(this, result_sets, keyword);
	};
});
