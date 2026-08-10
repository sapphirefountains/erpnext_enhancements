/*
 * Chat SPA entry point — a single esbuild bundle (content-hashed filename).
 *
 * Loaded by www/chat.html through `bundled_asset('chat.bundle.js')`, i.e. resolved through
 * assets.json. NOT a raw /assets path: those are served with a 1-year immutable
 * Cache-Control and carry no content hash, so an edit never reaches a device that already
 * cached it (the "fix works on desktop, phones still broken" bug this app has shipped once).
 *
 * NOT loaded on Desk. This bundle is referenced only by the website route, so it costs
 * nothing on the ~40 other pages the bubble ships to. The bubble's own footprint inside
 * erpnext_enhancements.bundle.js is what taxes every ERPNext page, and it is kept small on
 * purpose — see public/js/chat/README.md for the before/after sizes.
 *
 * NO VUE, deliberately and structurally. The repo vendors a UMD Vue that sets window.Vue via
 * app_include_js, i.e. on Desk pages. This bundle contains no Vue at all and is loaded only
 * on a website route, so the two runtimes cannot share a document — §4.1 option (c), reached
 * by the SPA being a separate page the bubble NAVIGATES to rather than mounting in place.
 * scripts/test_chat_single_vue.js fails the build if either bundle ever grows one.
 */

import { start } from "./chat/app.js";

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", start);
} else {
	start();
}
