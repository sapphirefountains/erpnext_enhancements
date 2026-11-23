/*
 * Estimate lines editor entry point (estimating milestone 1): the editor (public/js/estimating/editor.js)
 * and its pricing engine (public/js/estimating/estimate_math.js) as one esbuild bundle with a
 * content-hashed name, plus its stylesheet.
 *
 * Loaded only by the Estimate form script (estimating/doctype/estimate/estimate.js), never
 * app_include_js:
 *
 *   frappe.require(["estimate_editor.bundle.js", "estimate_editor.bundle.css"], () => window.sf_estimate_editor.mount(frm));
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with no
 * hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md).
 */

import * as editor from "./estimating/editor.js";
import * as math from "./estimating/estimate_math.js";

// The editor's API LEVEL, which the form script checks before it mounts (REQUIRED_EDITOR_LEVEL in
// estimating/doctype/estimate/estimate.js). BUMP IT whenever the form script starts calling something
// this bundle did not export before, and bump REQUIRED_EDITOR_LEVEL to the same number in the same change:
// scripts/test_estimate_math.mjs fails the build if the two differ. The form script travels in the form's
// meta and this bundle through frappe.boot.assets_json, so a tab open across a deploy can pair a new script
// with an old bundle; the level lets the script say "reload" instead of throwing (CLAUDE.md, v1.816.1).
const VERSION = 1;

if (!window.sf_estimate_editor || !(window.sf_estimate_editor.version >= VERSION)) {
	window.sf_estimate_editor = Object.assign({ version: VERSION, math }, editor);
}
