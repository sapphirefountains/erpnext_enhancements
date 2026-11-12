/*
 * Warehouse Map entry point (v1.815.0): the page's pure decisions
 * (public/js/warehouse_map/logic.js) as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by the Warehouse Map Desk page (inventory_enhancements/page/warehouse_map), never
 * app_include_js, together with sf_kit:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "warehouse_map.bundle.js"], () => {
 *       const wm = window.sf_warehouse_map; ...
 *   });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_warehouse_map`: every export of logic.js, and `version`.
 */

import * as logic from "./warehouse_map/logic.js";

const VERSION = 1;

try {
	if (typeof window !== "undefined" && !(window.sf_warehouse_map && window.sf_warehouse_map.version >= VERSION)) {
		window.sf_warehouse_map = Object.assign({ version: VERSION }, logic);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_warehouse_map failed to start", e);
}
