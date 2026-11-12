/*
 * Procurement Tracker entry point (v1.806.0): the widget's pure decisions
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

const VERSION = 1;

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
