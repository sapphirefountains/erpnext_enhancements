/*
 * scanner_kit entry point: the camera scanner the Desk pages share (v1.666.0, TASK-2026-02808).
 * The Device Console and the Inventory Scanner Audit each carried their own copy of the camera
 * sheet and of the history handling that makes the phone's Back button close a sheet; the
 * Device Console's copy had no jsQR, so it could not scan in iPhone Safari, which has no
 * BarcodeDetector. Both now load this one.
 *
 * Loaded only by a page that uses it, never app_include_js:
 *
 *   frappe.require(["scanner_kit.bundle.js"], () => { const kit = window.sf_scanner; ... });
 *
 * v16's frappe.require maps the name through frappe.boot.assets_json, so it resolves to the
 * content-hashed file (a raw /assets path is served immutable for a year with no hash). It
 * resolves even when the file failed to load, so a page checks window.sf_scanner afterwards and
 * carries on without the camera when it is not there.
 *
 * jsQR is NOT in this bundle: it is the vendored UMD file /stock-scan already ships, loaded raw
 * through frappe.require only on a device whose BarcodeDetector cannot read QR (scanner_kit/
 * reader.js says why). The kit has no stylesheet: the camera sheet is a frappe Dialog, and the
 * page passes the class its own CSS gives the video.
 *
 * What it installs is listed in scanner_kit/index.js. Run under node by scripts/test_scanner_kit.mjs
 * and, behind both pages, scripts/test_desk_page_history.js.
 */

import { install } from "./scanner_kit/index.js";

try {
	if (typeof window !== "undefined") install(window);
} catch (e) {
	if (typeof window !== "undefined" && window.console) window.console.warn("scanner_kit failed to start", e);
}
