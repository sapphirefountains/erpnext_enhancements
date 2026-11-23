/*
 * Pay application lines editor entry point (estimating milestone 2): the editor
 * (public/js/estimating/pay_app_editor.js) and the G703/G702 twin (public/js/estimating/pay_app_math.js)
 * as one esbuild bundle with a content-hashed name. Its stylesheet is pay_app_editor.bundle.css, on top of
 * estimate_editor.bundle.css, whose cards, rows and bars it shares.
 *
 * Loaded only by the Pay Application form script (estimating/doctype/pay_application/pay_application.js),
 * never app_include_js. A bundle, not a raw /assets path, because raw paths are served immutable for a year
 * with no hash (CLAUDE.md).
 */

import * as editor from "./estimating/pay_app_editor.js";
import * as math from "./estimating/pay_app_math.js";

// The editor's API LEVEL, which the form script checks before it mounts (REQUIRED_PAY_EDITOR_LEVEL in
// estimating/doctype/pay_application/pay_application.js). Bump both together whenever the form script
// starts calling something this bundle did not export before: scripts/test_estimate_math.mjs fails the
// build if they differ. A tab open across a deploy can pair a new script with an old bundle (CLAUDE.md).
const VERSION = 1;

if (!window.sf_pay_app_editor || !(window.sf_pay_app_editor.version >= VERSION)) {
	window.sf_pay_app_editor = Object.assign({ version: VERSION, math }, editor);
}
