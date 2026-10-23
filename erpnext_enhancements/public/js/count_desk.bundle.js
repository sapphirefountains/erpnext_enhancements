/*
 * Count Desk entry point (v1.736.0): the page's pure decisions (public/js/count_desk/logic.js) and
 * its controller (count_desk/controller.js), as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by the Count Desk Desk page (inventory_enhancements/page/count_desk), never
 * app_include_js, together with sf_kit:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "count_desk.bundle.js"], () => {
 *       const cd = window.sf_count_desk; ...
 *   });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_count_desk`: every export of logic.js, and `Controller`.
 */

import * as logic from "./count_desk/logic.js";
import { Controller } from "./count_desk/controller.js";

const VERSION = 1;

try {
	if (typeof window !== "undefined" && !(window.sf_count_desk && window.sf_count_desk.version >= VERSION)) {
		window.sf_count_desk = Object.assign({ version: VERSION, Controller }, logic);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_count_desk failed to start", e);
}
