/*
 * Capture panel entry point (WI-079 slice 2) — a single esbuild bundle, content-hashed.
 *
 * Never loaded at page load. `window.ee_capture.open()` (capture/recorder.js) fetches it the
 * first time somebody asks to report a problem:
 *   - on the Desk by injecting a <script> for the URL `frappe.assets.bundled_asset()` resolves
 *     (not `frappe.require`, which memoizes a failed load as done and freezes the Desk while
 *     it loads);
 *   - on the kiosk and allowlisted web pages by injecting `<script src=EE_CAPTURE.panel_url>`,
 *     the URL the template resolved through `bundled_asset()` — web `frappe.require` cannot
 *     resolve bundle names there.
 * Either way it ends up here. This file installs the global the recorder calls and offers
 * any reports saved on this device (the kiosk preloads it, so there that happens at load). The recorder stays a few KB because everything heavy (the annotator, IndexedDB, the
 * stylesheet) lives behind this lazy load.
 *
 * A content-hashed bundle, not a raw /assets path, for the reason ADR 0008 records: raw paths
 * are served immutable for a year, so a fix would never reach a tablet that cached the old one.
 *
 * The global carries no `erpnext_enhancements.` prefix, like `ee_capture`: the Help menu's
 * action string reaches this through `ee_capture.open()`, and
 * `tests/test_feedback_capture_surface.py` forbids that prefix in the action.
 */

import { openPanel, sendSavedDrafts, clearSavedDrafts, offerSavedDraftsOnLoad } from "./capture/panel.js";

try {
	if (typeof window !== "undefined") {
		window.ee_capture_panel = {
			open: openPanel,
			// Not called by the recorder. For surfaces that want to offer or discard saved
			// reports themselves — e.g. clearing them when somebody signs out of the kiosk.
			sendDrafts: sendSavedDrafts,
			clearDrafts: clearSavedDrafts,
		};
		offerSavedDraftsOnLoad();
	}
} catch (e) {
	// A frozen or proxied window must not turn a lazy load into a page error.
}
