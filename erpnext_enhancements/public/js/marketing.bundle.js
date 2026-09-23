/*
 * Marketing app entry point (TASK-2026-01487): a single esbuild bundle, content-hashed.
 *
 * Loaded by www/marketing.html through `bundled_asset('marketing.bundle.js')`, i.e. resolved
 * through assets.json. NOT a raw /assets path: those are served with a 1-year immutable
 * Cache-Control and carry no content hash, so an edit never reaches a device that already cached
 * it -- the "fix works on desktop, phones still broken" bug this app has shipped once. ADR 0008.
 *
 * NOT loaded on the Desk. Only the website route references it, so it costs nothing anywhere
 * else.
 *
 * NO VUE. The repo vendors a UMD Vue that sets window.Vue via app_include_js, on Desk pages; this
 * bundle contains none and loads only on a website route, so the two runtimes cannot share a
 * document. `scripts/test_marketing_source_rules.js` fails the build if that changes.
 */

import { MarketingApp } from "./marketing/app.js";

function start() {
	const root = document.getElementById("ee-marketing-root");
	if (!root) return;
	const app = new MarketingApp(root, window.EE_MARKETING_BOOT || {});
	// For the console only. Nothing in the bundle reads it: a module that reached for
	// `window.eeMarketing` instead of its import would be untestable.
	window.eeMarketing = app;
	app.mount();
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", start);
} else {
	start();
}
