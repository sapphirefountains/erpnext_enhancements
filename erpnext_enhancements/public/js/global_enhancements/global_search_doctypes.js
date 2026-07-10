/**
 * global_search_doctypes.js — surface matching DocTypes in the desk Global
 * Search results page.
 *
 * The problem: pressing Enter in the awesomebar opens frappe's Global Search
 * modal (frappe.searchdialog), which searches document *content* (the
 * __global_search index) via frappe.search.utils.get_global_results — that
 * provider only ever emits `fetch_type: "Global"` sets and never lists DocTypes
 * as navigation targets. So a user who typed a DocType name saw it in the live
 * awesomebar dropdown, but hitting Enter landed them on a results page with no
 * way to reach that DocType (and "No Results found" when only the DocType name,
 * not any document content, matched).
 *
 * The fix: the SearchDialog already knows how to render `fetch_type: "Nav"`
 * result sets — render_data() splits nav vs. global sets, gives nav sets their
 * own sidebar entry + "All Results" section (render_result / add_section_to_
 * summary), and paginates them via nav_lists. Nothing feeds it one by default.
 * We patch SearchDialog.parse_results to prepend a "DocTypes" Nav set built from
 * the SAME frappe.search.utils.get_doctypes() the awesomebar dropdown uses, so
 * the matching DocTypes appear (clickable, ranked identically) at the top of the
 * results page.
 *
 * Gating: injection only happens in unfiltered global-search mode. When a
 * DocType content filter is active (this.global_doctype_filter) the user has
 * drilled into one type's documents, and in tag search ("#tag") mode the search
 * isn't global — both are left untouched. Pagination ("Show more" / list-more)
 * calls get_global_results directly and never routes through parse_results, so
 * it is unaffected too.
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

	// Cap on DocType nav entries surfaced up front; the results UI paginates the
	// rest through its own "More" control (nav_lists).
	const EE_MAX_DOCTYPE_NAV_RESULTS = 20;

	// Primary navigation entry per matched DocType: the List/Tree link, or the
	// form link for single-types (get_doctypes tags those with type ""). We drop
	// the "New {0}" creatables (onclick-only, no route) and the per-DocType
	// "Report" duplicates so the section reads as a clean "jump to this DocType".
	const EE_DOCTYPE_NAV_TYPES = { "": 1, List: 1, Tree: 1 };

	// Build a `fetch_type: "Nav"` result set of DocTypes matching `keyword`,
	// reusing the awesomebar's own producer so labels/ranking match the dropdown.
	// Returns null when nothing usable matches (caller then injects nothing).
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

		const results = options
			.filter((o) => o && o.route && EE_DOCTYPE_NAV_TYPES[o.type || ""])
			.sort((a, b) => (b.index || 0) - (a.index || 0))
			.slice(0, EE_MAX_DOCTYPE_NAV_RESULTS);

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
			console.error("[ERPNext Enhancements] DocType nav injection failed", e);
		}
		return original_parse_results.call(this, result_sets, keyword);
	};
});
