/*
 * The Fountain Designer's demo fixture (?demo=1): every designer call answered in the browser from the
 * JSON in public/js/fountain_designer/demo/, whose shapes are the contract phase E's api/designer.py
 * honors (public/js/fountain_designer/README.md). A bundle of its own so the page's own bundle does not
 * carry 75 KB of fixture for everyone: the page frappe.require's it only in demo mode.
 *
 * It installs `window.sf_fountain_designer_demo`: `create(env)` -> a transport for api.create_api, and
 * the `fixture` itself.
 */

import boot from "./fountain_designer/demo/boot.json";
import catalog from "./fountain_designer/demo/catalog.json";
import designs from "./fountain_designer/demo/designs.json";
import lobby from "./fountain_designer/demo/derive_lobby.json";
import plaza from "./fountain_designer/demo/derive_plaza.json";
import pond from "./fountain_designer/demo/derive_pond.json";
import { create_demo } from "./fountain_designer/demo/demo.js";

const VERSION = 1;

const fixture = {
	boot,
	catalog,
	designs,
	replies: {
		[lobby.model.kit]: lobby,
		[plaza.model.kit]: plaza,
		[pond.model.kit]: pond,
	},
};

try {
	if (typeof window !== "undefined" && !(window.sf_fountain_designer_demo && window.sf_fountain_designer_demo.version >= VERSION)) {
		window.sf_fountain_designer_demo = { version: VERSION, fixture, create: (env) => create_demo(fixture, env) };
	}
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("sf_fountain_designer_demo failed to start", e);
}
