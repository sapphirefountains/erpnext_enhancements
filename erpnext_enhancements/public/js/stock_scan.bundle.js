/*
 * Stock Scan entry point — a single esbuild bundle (content-hashed filename).
 *
 * Loaded by www/stock-scan.html through `bundled_asset('stock_scan.bundle.js')`, i.e. resolved
 * through assets.json. NOT a raw /assets path: those are served with a one-year immutable
 * Cache-Control and carry no content hash, so an edit would never reach a phone that already
 * cached it. ADR 0008.
 *
 * NOT loaded on Desk: only the website route references it, so it costs nothing elsewhere.
 * No framework and no Desk client API: plain DOM, like the feedback page.
 *
 * The QR decoder for phones without BarcodeDetector (every iPhone) is NOT in this bundle: it is
 * a separate vendored file that scanner.js loads the first time the camera opens on such a
 * phone, from the URL in the boot payload.
 */

import { StockScanApp } from "./stock_scan/app.js";

function start() {
	const root = document.getElementById("ee-stock-scan-root");
	if (!root) return;
	const app = new StockScanApp(root, window.EE_STOCK_SCAN_BOOT || {});
	// Exposed for debugging from the console. Nothing in the bundle reads it — a module that
	// reached for `window.eeStockScan` instead of its import would be untestable.
	window.eeStockScan = app;
	app.mount();
}

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", start);
} else {
	start();
}
