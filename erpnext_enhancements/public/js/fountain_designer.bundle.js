/*
 * Fountain Designer entry point (phase F of the rebuild, docs/fountain-designer/PLAN.md 3.5): the
 * guided single-page designer's modules (public/js/fountain_designer/), as one esbuild bundle with a
 * content-hashed name.
 *
 * Loaded only by the Fountain Designer Desk page (water_engineering/page/fountain_designer), never
 * app_include_js, together with sf_kit and its own stylesheet:
 *
 *   frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css", "fountain_designer.bundle.js",
 *       "fountain_designer.bundle.css"], () => { const fd = window.sf_fountain_designer; ... });
 *
 * A bundle rather than a raw /assets path because raw paths are served immutable for a year with no
 * hash (the "fix works on desktop, phones still broken" trap in CLAUDE.md). The demo fixture is a
 * bundle of its own (fountain_designer_demo.bundle.js), fetched only with ?demo=1.
 *
 * It installs `window.sf_fountain_designer`: `mount(env)` (the page's controller, app.js) and the pure
 * modules, which scripts/test_fountain_designer.mjs runs under node.
 */

import * as model from "./fountain_designer/model.js";
import * as router from "./fountain_designer/router.js";
import * as api from "./fountain_designer/api.js";
import * as verdict from "./fountain_designer/verdict.js";
import * as chart from "./fountain_designer/chart.js";
import { mount } from "./fountain_designer/app.js";

const VERSION = 1;

try {
	if (typeof window !== "undefined" && !(window.sf_fountain_designer && window.sf_fountain_designer.version >= VERSION)) {
		window.sf_fountain_designer = { version: VERSION, mount, model, router, api, verdict, chart };
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_fountain_designer failed to start", e);
}
