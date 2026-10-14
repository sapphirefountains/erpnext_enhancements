/*
 * Guided Entry entry point (form kit P4-B): the pure decisions of the guided step-by-step page
 * (enhancements_core/page/guided_entry), as one esbuild bundle with a content-hashed name.
 *
 * Loaded only by that page, never app_include_js, together with sf_kit and the two stylesheets:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "guided_entry.bundle.js",
 *                   "guided_entry.bundle.css"], () => { const kit = window.sf_form_kit; ... });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with
 * no hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 *
 * It installs `window.sf_form_kit`: every export of public/js/form_kit/index.js (routes, step
 * activeness, missing-per-step, input modes, the text-list table, conflict diffing and the save
 * queue). The page's DOM and Frappe wiring stay in the page script; the decisions live here so
 * tests/test_guided_entry.py can run them under node. It also carries the DocType extensions the
 * page draws step blocks with (public/js/guided_item/ for Item), found through
 * sf_form_kit.extension_for(doctype).
 */

import * as form_kit from "./form_kit/index.js";
// Each DocType's page extension registers itself with form_kit/extensions.js (form kit P8.1):
// the Item flow's name builder and its supplier, stock and asset blocks.
import "./guided_item/extension.js";

// 2 (form kit P8.1): extension_for, ends_core and opens_by_default.
const VERSION = 2;

try {
	if (typeof window !== "undefined" && !(window.sf_form_kit && window.sf_form_kit.version >= VERSION)) {
		window.sf_form_kit = Object.assign({ version: VERSION }, form_kit);
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_form_kit failed to start", e);
}
