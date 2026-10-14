/*
 * scanner_kit: what reads a code off a camera frame.
 *
 *   create_reader(env) -> Promise of { mode, qr_only, read(video) } or null
 *
 * `read(video)` reads one frame and gives back the code, or null (a value or a promise of one).
 * Three modes, chosen from what the browser has:
 *
 *   native  BarcodeDetector lists qr_code (Chrome on Android, Safari on a Mac). jsQR is never
 *           loaded: those devices pay nothing for it. The detector is built with no formats, so it
 *           reads every 1D barcode the device supports as well as QR.
 *   both    BarcodeDetector is there and reads some formats, but not QR. It still reads the
 *           barcodes; jsQR reads the QR codes it cannot.
 *   jsqr    No BarcodeDetector (every iPhone: Safari has none), or one that reads nothing. jsQR,
 *           QR only, so `qr_only` is set and the page says so in its hint.
 *
 * jsQR is the vendored copy /stock-scan already ships (js/stock_scan/lib, Apache-2.0, never
 * edited; its filename carries its version, so the year-immutable /assets cache cannot serve a
 * stale one). It is a UMD file that sets `window.jsQR` from a plain <script>, so it stays a raw
 * file loaded on first need rather than going into the bundle: bundled, it would cost every Android
 * phone 130 KB it never runs, and capture the export instead of setting the global. v16's
 * frappe.require loads it with a <script> tag and appends its own ?v=; the path must stay bare,
 * because one that already carries a query string loads as nothing. frappe.require resolves even
 * when the file failed to load, so window.jsQR is checked afterwards.
 *
 * Without a reader (jsQR failed to load, with no BarcodeDetector to fall back on) this resolves
 * to null, and the page says why instead of opening a camera that can never read anything.
 */

export const QR_DECODER = "/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js";

// jsQR decodes a frame drawn at most this wide: a QR label fills a good share of the frame, and a
// full 1080p frame costs about five times the time for no better read.
export const QR_FRAME_WIDTH = 480;

// Always these same options: jsQR folds the options it is given into its module defaults, so one
// call with others would change every later call.
const JSQR_OPTIONS = Object.freeze({ inversionAttempts: "dontInvert" });

// The formats the browser's BarcodeDetector reads: null when there is none, [] when it cannot say.
export function supported_formats(env) {
	const BD = env.win.BarcodeDetector;
	if (typeof BD !== "function") return Promise.resolve(null);
	if (typeof BD.getSupportedFormats !== "function") return Promise.resolve([]);
	return Promise.resolve()
		.then(() => BD.getSupportedFormats())
		.then(
			(formats) => (Array.isArray(formats) ? formats : []),
			() => []
		);
}

// window.jsQR, loaded once per page on first need. A load that fails is asked for again next time
// (v16 marks even a failed file as loaded, so that only helps where the page has been reloaded,
// but it never leaves a promise of nothing cached here).
export function load_jsqr(env) {
	const have = () => (typeof env.win.jsQR === "function" ? env.win.jsQR : null);
	if (have()) return Promise.resolve(have());
	if (!env.jsqr_loading) {
		env.jsqr_loading = new Promise((resolve) => {
			try {
				Promise.resolve(env.frappe.require(QR_DECODER)).then(
					() => resolve(have()),
					() => resolve(null)
				);
			} catch (e) {
				resolve(null);
			}
		}).then((jsQR) => {
			if (!jsQR) env.jsqr_loading = null;
			return jsQR;
		});
	}
	return env.jsqr_loading;
}

function native_read(detector) {
	return (video) =>
		Promise.resolve()
			.then(() => detector.detect(video))
			.then((codes) => (codes && codes.length ? codes[0].rawValue : null));
}

function jsqr_read(env, jsQR) {
	const canvas = env.document.createElement("canvas");
	const ctx = canvas && typeof canvas.getContext === "function" ? canvas.getContext("2d", { willReadFrequently: true }) : null;
	if (!ctx) return null;
	return (video) => {
		const vw = video.videoWidth;
		const vh = video.videoHeight;
		if (!vw || !vh) return null;
		const w = Math.min(QR_FRAME_WIDTH, vw);
		const h = Math.round(vh * (w / vw));
		if (canvas.width !== w || canvas.height !== h) {
			canvas.width = w;
			canvas.height = h;
		}
		ctx.drawImage(video, 0, 0, w, h);
		const hit = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, JSQR_OPTIONS);
		return hit && hit.data ? hit.data : null;
	};
}

export function create_reader(env) {
	return supported_formats(env).then((formats) => {
		let native = null;
		if (formats && formats.length) {
			try {
				native = native_read(new env.win.BarcodeDetector());
			} catch (e) {
				native = null;
			}
		}
		if (native && formats.includes("qr_code")) return { mode: "native", qr_only: false, read: native };
		return load_jsqr(env).then((jsQR) => {
			const qr = jsQR ? jsqr_read(env, jsQR) : null;
			if (native && qr) {
				return {
					mode: "both",
					qr_only: false,
					read: (video) =>
						native(video)
							.catch(() => null)
							.then((code) => code || qr(video)),
				};
			}
			// jsQR would not load: the barcodes the detector does read are still worth a camera.
			if (native) return { mode: "native", qr_only: false, read: native };
			if (qr) return { mode: "jsqr", qr_only: true, read: qr };
			return null;
		});
	});
}
