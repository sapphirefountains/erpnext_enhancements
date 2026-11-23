/*
 * Procurement Tracker entry point (v1.811.0): the widget's pure decisions
 * (public/js/procurement_tracker/logic.js) as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by the Project form script (public/js/project_enhancements.js), never
 * app_include_js:
 *
 *   frappe.require("procurement_tracker.bundle.js", () => { const pt = window.sf_procurement_tracker; ... });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md). The form script
 * itself is a doctype_js file and travels inside the form's meta, so it needs no bundle; the
 * logic is split out so node can test it (scripts/test_procurement_tracker.mjs).
 *
 * It installs `window.sf_procurement_tracker`: every export of logic.js.
 */

import * as logic from "./procurement_tracker/logic.js";

// The logic's API LEVEL, which the form script checks before it mounts anything
// (REQUIRED_LOGIC_LEVEL in public/js/project_enhancements.js). BUMP IT whenever project_enhancements.js
// starts calling something this bundle did not export before, and bump REQUIRED_LOGIC_LEVEL to the same
// number in the same change: scripts/test_procurement_tracker.mjs fails the build if the two differ.
//
// Why it exists (v1.816.1): the form script is doctype_js and travels in the form's meta, the bundle is
// fetched through frappe.boot.assets_json. A Desk tab that was open across a deploy can end up with the NEW
// form script and the OLD logic (a stale assets_json names the old hashed file, or window.sf_procurement_tracker
// was installed before the deploy), and the form script then calls an export that is not there. The level is
// what lets it say so instead of throwing; it also makes a newer bundle replace an older copy in one window.
//
//   1  v1.811.0 - v1.815.0   the tracker before saved views
//   2  v1.816.0              saved views: merge_views, fallback_views, switch_grouping, write_view_query(.., rules)
const VERSION = 2;

try {
	if (
		typeof window !== "undefined" &&
		!(window.sf_procurement_tracker && window.sf_procurement_tracker.version >= VERSION)
	) {
		window.sf_procurement_tracker = Object.assign({ version: VERSION }, logic);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_procurement_tracker failed to start", e);
}
