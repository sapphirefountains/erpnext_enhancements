/*
 * Opportunity board entry point (form kit P6, TASK-2026-02774): the board's pure decisions
 * (public/js/opportunity_board/board.js), as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by the board page (crm_enhancements/page/opportunity_board), never app_include_js,
 * together with sf_kit and the two stylesheets:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "opportunity_board.bundle.js",
 *                   "opportunity_board.bundle.css"], () => { const kit = window.sf_opportunity_board; ... });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_opportunity_board`: every export of opportunity_board/board.js (routes,
 * the model, ages, rows by value stream, the forecast, and what a drop means). The page's DOM and
 * Frappe wiring stay in the page script; the decisions live here so
 * tests/test_opportunity_board.py can run them under node.
 */

import * as board from "./opportunity_board/board.js";

// 2 since v1.684.0: card_parts / card_text, which the page now calls.
const VERSION = 2;

try {
	if (typeof window !== "undefined" && !(window.sf_opportunity_board && window.sf_opportunity_board.version >= VERSION)) {
		window.sf_opportunity_board = Object.assign({ version: VERSION }, board);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_opportunity_board failed to start", e);
}
