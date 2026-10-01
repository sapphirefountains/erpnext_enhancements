/*
 * Review Room entry point (/review) — a single esbuild bundle with a content-hashed filename.
 *
 * Loaded by www/review.html through `bundled_asset('design_review.bundle.js')`, never a raw
 * /assets path (those are cached for a year with no hash). Not loaded on the Desk.
 *
 * No Vue and no frappe.js: plain DOM, like the /feedback app. The app lives in
 * public/js/design_review/.
 */

import { ReviewApp } from "./design_review/app.js";

function start() {
	const root = document.getElementById("ee-review-root");
	if (!root) return;
	const app = new ReviewApp(root, window.EE_REVIEW_BOOT || {});
	window.eeReview = app; // for the console only; nothing in the bundle reads it
	app.mount();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
