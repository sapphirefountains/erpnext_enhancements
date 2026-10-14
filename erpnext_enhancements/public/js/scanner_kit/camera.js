/*
 * scanner_kit: the Camera Scan sheet. One copy of what the Device Console and the Inventory Scanner
 * Audit each carried (v1.666.0); the Device Console had no jsQR, so it could not scan on an iPhone.
 *
 *   available()                    getUserMedia is there (https, or localhost, and a browser
 *                                  that allows camera access). This, not BarcodeDetector, decides
 *                                  whether a page shows its camera button.
 *   prepare({unavailable, no_reader}) -> Promise of a reader (reader.js) or null. Says why there
 *                                  is none itself, in frappe's message dialog, with the page's
 *                                  words when it passes them.
 *   show({nav, reader, page, name, title, hint, qr_hint, video_class, on_close})
 *                                  Puts the sheet up, then starts the camera. Call it as the
 *                                  `show` of nav.open(), so it runs once the sheet's route has
 *                                  settled. on_close({read, failure}) runs from the dialog's
 *                                  onhide, after the camera is off and nav.gone(dialog): `read`
 *                                  is the code read, `failure` the sentence to say when the camera
 *                                  could not be had. The page decides what each leads to.
 *   failure_message(err)           What to say for a getUserMedia rejection.
 *
 * iOS, which every iPhone browser is underneath:
 *
 *   - the <video> is `playsinline` (without it Safari takes the stream full screen and the sheet
 *     never sees a frame), `muted` and `autoplay`, and `muted` is set as a property as well: the
 *     attribute alone is not always reflected, and Safari plays a video inline, with no tap of its
 *     own, only when it is muted. play()'s promise is caught: a refusal must not surface as an
 *     unhandled rejection, and the frames still arrive.
 *   - the camera is asked for only in the chain a tap starts: show() runs from nav.open(), which
 *     a page calls from a click (or from a Forward onto the sheet's own entry, the same tap
 *     replayed), never from a page load, a reply or a timer of its own. The chain waits for the
 *     sheet's route to settle (about 100 ms) and, on an iPhone's first open, for jsQR to load:
 *     WebKit asks for no transient user activation for getUserMedia (getDisplayMedia is the one
 *     that needs it), and a muted inline video plays without one.
 *   - `facingMode: "environment"`, the back camera, as a preference (a bare value is `ideal`), so
 *     a laptop with only a front camera still gets one; `audio: false`.
 *   - every track is stopped on every way out, so the camera light goes off: the dialog closing
 *     (a read, X, Escape, Back, a route change), the document going hidden (the phone locked, the
 *     app switched), `pagehide` (the tab closed, or Safari putting the page in its back-forward
 *     cache), and frappe's "hide" on the page's wrapper (another Desk page shown; the router closes
 *     a displayed dialog then, but not one still fading in). A stream that arrives after the sheet
 *     has closed (the permission prompt answered late) is stopped as it arrives.
 *
 * Torch: where the track offers one (`getCapabilities().torch`, Chrome on Android), a Torch button
 * toggles it; iOS offers none, and the button stays hidden. Stopping the track turns it off.
 */

import { __ } from "../sf_kit/t.js";
import { create_reader } from "./reader.js";

// getUserMedia's error names, by what the person can do about them.
const DENIED = ["NotAllowedError", "SecurityError", "PermissionDeniedError"];
const NO_CAMERA = ["NotFoundError", "DevicesNotFoundError", "OverconstrainedError"];

let opened = 0; // each sheet's own namespace for the wrapper's "hide"

export function camera_available(env) {
	const media = env.navigator && env.navigator.mediaDevices;
	return !!(media && typeof media.getUserMedia === "function");
}

export function failure_message(err) {
	const name = (err && err.name) || "";
	if (DENIED.includes(name)) {
		return __("Camera access is blocked for this site. Allow the camera in the browser's settings, then try again.");
	}
	if (NO_CAMERA.includes(name)) return __("No camera was found on this device.");
	return __("Could not access the camera.");
}

export function prepare_camera(env, opts) {
	opts = opts || {};
	if (!camera_available(env)) {
		env.frappe.msgprint(
			opts.unavailable ||
				__("The camera needs a secure (https) connection and a browser that allows camera access. Use a hardware/Bluetooth scanner.")
		);
		return Promise.resolve(null);
	}
	return create_reader(env).then(
		(reader) => {
			if (!reader) {
				env.frappe.msgprint(
					opts.no_reader || __("Could not load the QR code reader. Check the connection, or use a hardware/Bluetooth scanner.")
				);
			}
			return reader;
		},
		() => null
	);
}

const esc = (value) =>
	String(value == null ? "" : value).replace(
		/[&<>"']/g,
		(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
	);

export function show_camera(env, opts) {
	const nav = opts.nav;
	const reader = opts.reader;
	const name = opts.name || "camera";
	const page = opts.page || null;
	const ns = `hide.sf_scanner_${++opened}`;
	const hint = reader.qr_only
		? opts.qr_hint || __("Point the camera at a QR code. For a barcode, use a hardware/Bluetooth scanner.")
		: opts.hint || __("Point the camera at a barcode or QR code.");

	const d = new env.frappe.ui.Dialog({ title: opts.title || __("Camera Scan"), size: "small" });
	d.$body.html(
		`<video class="${esc(opts.video_class || "sf-scanner-video")}" playsinline muted autoplay></video>` +
			`<div class="text-muted" style="margin-top:6px;">${esc(hint)}</div>` +
			`<button type="button" class="btn btn-default btn-sm sf-scanner-torch" aria-pressed="false" style="display:none;margin-top:6px;">${esc(__("Torch"))}</button>`
	);
	const video = d.$body.find("video")[0];
	video.muted = true; // iOS plays inline, unprompted, only when muted
	const $torch = d.$body.find(".sf-scanner-torch");

	let stream = null;
	let stopped = false;
	let timer = null;
	let read = null; // what the camera read, handed on once the sheet has closed
	let failure = null; // what to say when the camera could not be had

	const later = (fn, ms) => {
		if (!stopped) timer = env.set_timeout(fn, ms);
	};
	const stop_tracks = (s) => {
		if (!s) return;
		s.getTracks().forEach((track) => {
			try {
				track.stop();
			} catch (e) {
				// A track already ended has nothing left to stop.
			}
		});
	};
	// The light goes off now, and the sheet closes behind it (its onhide finds the camera off).
	const away = () => {
		stop();
		nav.hide(d);
	};
	const on_visibility = () => {
		if (env.document.hidden) away();
	};
	const on_pagehide = () => away();
	const stop = () => {
		stopped = true;
		env.clear_timeout(timer);
		env.document.removeEventListener("visibilitychange", on_visibility);
		env.unlisten("pagehide", on_pagehide);
		if (page) env.$(page).off(ns);
		stop_tracks(stream);
	};

	const tick = () => {
		if (stopped) return;
		if (video.readyState < 2) {
			later(tick, 200);
			return;
		}
		Promise.resolve()
			.then(() => reader.read(video))
			.then((value) => {
				if (stopped) return;
				if (value) {
					read = value;
					stop();
					nav.hide(d);
				} else {
					later(tick, 180);
				}
			})
			.catch(() => later(tick, 300));
	};

	const offer_torch = (track) => {
		let caps = null;
		try {
			caps = track && typeof track.getCapabilities === "function" ? track.getCapabilities() : null;
		} catch (e) {
			caps = null;
		}
		if (!caps || !caps.torch) return;
		let on = false;
		$torch.show();
		$torch.on("click", () => {
			const want = !on;
			Promise.resolve()
				.then(() => track.applyConstraints({ advanced: [{ torch: want }] }))
				.then(
					() => {
						on = want;
						$torch.attr("aria-pressed", want ? "true" : "false");
					},
					() => {}
				);
		});
	};

	d.onhide = () => {
		stop();
		nav.gone(d);
		if (opts.on_close) opts.on_close({ read, failure });
	};
	nav.sheet = { name, dialog: d };
	d.show();
	env.document.addEventListener("visibilitychange", on_visibility);
	env.listen("pagehide", on_pagehide);
	if (page) {
		env.$(page).on(ns, (e) => {
			if (e.target === page) away();
		});
	}
	// Started after the sheet is up, never before the route has settled.
	env.navigator.mediaDevices
		.getUserMedia({ video: { facingMode: "environment" }, audio: false })
		.then((s) => {
			if (stopped) {
				// Closed while the permission prompt was up.
				stop_tracks(s);
				return;
			}
			stream = s;
			video.srcObject = s;
			const playing = video.play();
			if (playing && playing.catch) playing.catch(() => {});
			const tracks = typeof s.getVideoTracks === "function" ? s.getVideoTracks() : s.getTracks();
			offer_torch(tracks && tracks[0]);
			tick();
		})
		.catch((err) => {
			if (stopped) return;
			failure = failure_message(err);
			nav.hide(d);
		});
	return d;
}
