/*
 * Capture recorder for web pages — a single esbuild bundle (content-hashed filename).
 * WI-079 slice 2, ADR 0016 §4.
 *
 * Included ONLY by the allowlisted templates' own script blocks (/kiosk, /feedback, /itinerary,
 * /travel_guidelines), and NEVER from `web_include_js`: Frappe emits that on every website page,
 * including the guest, token and customer pages (/pay, /contract-sign, /login ...) that must not
 * load any capture code at all. A bench-free test pins both halves of that rule.
 *
 * The template sets `window.EE_CAPTURE = {surface, user, panel_url, launcher, csrf_token}`
 * BEFORE this script, and includes this script before the page's own bundle, so the recorder
 * wraps fetch, XHR and console before the page starts using them.
 *
 * The Desk does not load this file. It gets the same recorder from erpnext_enhancements.bundle.js,
 * and the recorder installs once per window if both ever meet.
 *
 * The report form itself (capture_panel.bundle.js) is not in here. It is loaded when somebody
 * opens it, so the cost on every page load is just the recorder.
 */

import "./capture/recorder.js";
import { mountLauncher } from "./capture/launcher.js";

if (typeof window !== "undefined") {
	mountLauncher(window);
}
