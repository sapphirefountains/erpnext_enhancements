/*
 * Bin Manager entry point (v1.693.0, ER-2026-1320545): the page's pure decisions
 * (public/js/bin_manager/logic.js) and its controller (bin_manager/controller.js), as one esbuild
 * bundle with a content-hashed name.
 *
 * Loaded only by the Bin Manager Desk page (inventory_enhancements/page/bin_manager), never
 * app_include_js, together with sf_kit:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "bin_manager.bundle.js"], () => {
 *       const bm = window.sf_bin_manager; ...
 *   });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_bin_manager`: every export of logic.js, `Controller`, and (v1.806.1)
 * `views`, the filters and the views beyond one bin (bin_manager/views.js).
 */

import * as logic from "./bin_manager/logic.js";
import * as views from "./bin_manager/views.js";
import { Controller } from "./bin_manager/controller.js";

// 2 since v1.806.1: a page that asks for the views must not be handed a copy without them.
const VERSION = 2;

try {
	if (typeof window !== "undefined" && !(window.sf_bin_manager && window.sf_bin_manager.version >= VERSION)) {
		window.sf_bin_manager = Object.assign({ version: VERSION, Controller, views }, logic);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_bin_manager failed to start", e);
}
