/*
 * Feedback SPA entry point — a single esbuild bundle (content-hashed filename).
 *
 * Loaded by www/feedback.html through `bundled_asset('feedback.bundle.js')`, i.e. resolved
 * through assets.json. NOT a raw /assets path: those are served with a 1-year immutable
 * Cache-Control and carry no content hash, so an edit never reaches a device that already
 * cached it — the "fix works on desktop, phones still broken" bug this app has shipped once.
 * ADR 0008.
 *
 * NOT loaded on Desk. This bundle is referenced only by the website route, so it costs
 * nothing on any other page.
 *
 * NO VUE. The repo vendors a UMD Vue that sets window.Vue via app_include_js, i.e. on Desk
 * pages; this bundle contains none and is loaded only on a website route, so the two runtimes
 * cannot share a document. Same structural resolution as the chat SPA.
 */

import { FeedbackApp } from "./feedback/app.js";

function start() {
	const root = document.getElementById("ee-feedback-root");
	if (!root) return;
	const app = new FeedbackApp(root, window.EE_FEEDBACK_BOOT || {});
	// Exposed for debugging from the console. Not read by anything in the bundle — a module
	// that reached for `window.eeFeedback` instead of its import would be untestable.
	window.eeFeedback = app;
	app.mount();
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", start);
} else {
	start();
}
