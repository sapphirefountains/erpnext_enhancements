/*
 * Reorder levels on the audit screens (v1.725.0): the shared editor behind the reorder boxes on the
 * Bin Manager and the Inventory Scanner Audit (public/js/reorder_levels/), as one esbuild bundle with
 * a content-hashed name.
 *
 * Loaded only by those two Desk pages, never app_include_js:
 *
 *   frappe.require(["reorder_levels.bundle.js", "reorder_levels.bundle.css"], () => {
 *       const ro = window.sf_reorder; ...
 *   });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with no
 * hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md). A page whose copy of it
 * did not load simply shows no reorder boxes: both pages test `window.sf_reorder` before using it.
 *
 * It installs `window.sf_reorder`: { version, ReorderEditor, cell_html, bind, paint, read_boxes, logic }.
 */

import * as logic from "./reorder_levels/logic.js";
import { ReorderEditor, cell_html } from "./reorder_levels/editor.js";
import { bind, paint, read_boxes } from "./reorder_levels/dom.js";

const VERSION = 1;

try {
	if (typeof window !== "undefined" && !(window.sf_reorder && window.sf_reorder.version >= VERSION)) {
		window.sf_reorder = { version: VERSION, ReorderEditor, cell_html, bind, paint, read_boxes, logic };
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_reorder failed to start", e);
}
