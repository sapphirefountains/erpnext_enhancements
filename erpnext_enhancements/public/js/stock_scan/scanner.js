/**
 * The in-page camera scanner: a full-screen sheet that reads a QR label (or an item barcode)
 * and hands the text to `onCode`.
 *
 * Why the page scans for itself rather than sending people back to the phone's camera app:
 * iOS asks for camera permission again on every page load, and a technician working down a
 * shelf would answer that prompt once per bin. Inside the page it is asked once per visit.
 * The same reason is why the page never changes the URL: its history entries carry none (nav.js).
 *
 * Two decoders:
 *
 *  - the browser's own `BarcodeDetector` where it exists AND lists `qr_code` (Chrome on
 *    Android); it also reads the common 1-D item barcodes;
 *  - otherwise jsQR (every iPhone), a vendored file loaded only when first needed from the
 *    URL the boot payload carries, then run on frames drawn to a small canvas. jsQR reads QR
 *    codes only, which is what the labels are.
 *
 * Whatever happens with the camera, the sheet keeps a "Type a code" box, which is also where
 * a Bluetooth or USB scanner gun's keystrokes and Enter land.
 *
 * Every camera track is stopped on close, on a hit, when the page is hidden, and on pagehide:
 * a live track keeps the camera light on and, on iOS, the capture indicator in the status bar.
 */

import { append, button, el, icon, input } from "./dom.js";
import { buzz, notOurs, sheet } from "./ui.js";

const FORMATS = ["qr_code", "code_128", "ean_13", "ean_8", "upc_a", "upc_e"];

/** About five looks a second: quick enough to feel instant, light enough for an old phone. */
const SCAN_EVERY_MS = 180;

/** Frames are shrunk to this width before jsQR reads them; a label fills the middle of the view anyway. */
const FRAME_WIDTH = 480;

// jsQR copies the options it is given into its module-level defaults, permanently. Passing the
// same values on every call is the only way to know what it will do on the next one.
const JSQR_OPTIONS = { inversionAttempts: "dontInvert" };

let decoderLoading = null;

/** Load the vendored jsQR once per page; a failed load may be retried on the next open. */
function loadDecoder(url) {
	if (typeof window.jsQR === "function") return Promise.resolve();
	if (!decoderLoading) {
		decoderLoading = new Promise((resolve, reject) => {
			if (!url) {
				reject(new Error("no decoder"));
				return;
			}
			const tag = document.createElement("script");
			tag.src = url;
			tag.async = true;
			tag.onload = () => (typeof window.jsQR === "function" ? resolve() : reject(new Error("no jsQR")));
			tag.onerror = () => reject(new Error("decoder failed to load"));
			document.head.appendChild(tag);
		}).catch((e) => {
			decoderLoading = null;
			throw e;
		});
	}
	return decoderLoading;
}

/** The browser's own detector, only when it can read QR codes (some builds list formats but not QR). */
async function nativeDetector() {
	const Detector = window.BarcodeDetector;
	if (typeof Detector !== "function" || typeof Detector.getSupportedFormats !== "function") return null;
	try {
		const supported = (await Detector.getSupportedFormats()) || [];
		if (supported.indexOf("qr_code") === -1) return null;
		return new Detector({ formats: FORMATS.filter((f) => supported.indexOf(f) !== -1) });
	} catch (e) {
		return null;
	}
}

/** What went wrong with the camera, said as what to do next. */
function cameraProblem(error) {
	const name = (error && error.name) || "";
	if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
		return {
			text: "Camera access is off for this site. Allow the camera in your browser's settings for this site, or type the code below.",
			retry: true,
		};
	}
	if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
		return { text: "No camera was found. Type the code below, or close this and use Search.", retry: false };
	}
	if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
		return { text: "Another app is using the camera. Close it and tap Try again, or type the code below.", retry: true };
	}
	return { text: "The camera did not start. Tap Try again, or type the code below.", retry: true };
}

/**
 * Open the scanner. `onCode(text)` is called once, after the sheet closes, with the raw text
 * of the first code read or typed. `onClose()` is called if the person closes it without one.
 * Returns `{close}`.
 */
export function openScanner(opts) {
	const o = opts || {};
	let stream = null;
	let running = false;
	let timer = null;
	let finished = false;
	let delivered = false;
	let detector = null;
	let torchOn = false;
	let ctx = null;
	let attempt = 0;
	let misses = 0;
	const canvas = document.createElement("canvas");

	// --- the view -------------------------------------------------------------------------
	const video = document.createElement("video");
	video.className = "ee-ss-cam-video";
	// All three before srcObject: iOS will not play inline, or at all, without them.
	video.setAttribute("playsinline", "");
	video.setAttribute("muted", "");
	video.setAttribute("autoplay", "");
	video.muted = true;
	video.playsInline = true;

	const frame = el("div", "ee-ss-cam-frame");
	frame.setAttribute("aria-hidden", "true");
	const status = el("p", "ee-ss-cam-status", "Starting camera…");
	status.setAttribute("role", "status");
	const problemText = el("p", "ee-ss-cam-problem-text");
	const retryBtn = button([icon("undo"), el("span", null, "Try again")], "ee-ss-btn ee-ss-btn-outline", () => start());
	const problem = append(el("div", "ee-ss-cam-problem"), problemText, retryBtn);
	problem.setAttribute("role", "alert");
	problem.hidden = true;
	const camera = append(el("div", "ee-ss-cam"), video, frame, status, problem);

	const torchBtn = button([icon("bolt"), el("span", null, "Light")], "ee-ss-btn ee-ss-btn-outline ee-ss-cam-torch", () => toggleTorch());
	torchBtn.hidden = true;
	torchBtn.setAttribute("aria-pressed", "false");

	const codeInput = input({
		placeholder: "Type a code",
		label: "Type a code",
		enterkeyhint: "go",
	});
	const goBtn = button("Go", "ee-ss-btn ee-ss-btn-primary ee-ss-cam-go", () => submitTyped());
	codeInput.addEventListener("keydown", (ev) => {
		if (ev.key === "Enter") {
			ev.preventDefault();
			submitTyped();
		}
	});
	const typed = append(
		el("div", "ee-ss-cam-type"),
		el("p", "ee-ss-cam-type-label", "No camera? Type the code on the label."),
		append(el("div", "ee-ss-cam-type-row"), codeInput, goBtn)
	);

	const handle = sheet({
		title: "Scan a label",
		full: true,
		className: "is-camera",
		// Focus the sheet, not the box: on a phone a focused box raises the keyboard over the camera.
		initialFocus: "sheet",
		body: (body) => append(body, camera, append(el("div", "ee-ss-cam-tools"), torchBtn), typed),
		onClose: () => {
			teardown();
			if (!delivered && o.onClose) o.onClose();
		},
	});

	// --- camera lifecycle -----------------------------------------------------------------
	function setStatus(text) {
		status.textContent = text;
		status.hidden = !text;
	}

	function showProblem(p) {
		stopCamera();
		setStatus("");
		frame.hidden = true;
		problemText.textContent = p.text;
		retryBtn.hidden = !p.retry;
		problem.hidden = false;
	}

	function stopCamera() {
		running = false;
		if (timer) clearTimeout(timer);
		timer = null;
		if (stream) {
			stream.getTracks().forEach((t) => {
				try {
					t.stop();
				} catch (e) {
					/* already stopped */
				}
			});
		}
		stream = null;
		// A stopped track takes the light with it; the button must say so, or the next start
		// shows it lit while the light is off, and the next tap turns it on with no change.
		torchOn = false;
		torchBtn.classList.remove("is-on");
		torchBtn.setAttribute("aria-pressed", "false");
		try {
			video.pause();
		} catch (e) {
			/* nothing playing */
		}
		video.srcObject = null;
	}

	async function start() {
		if (finished) return;
		// Each start supersedes the last: a Try again tap or the page coming back into view
		// while an earlier getUserMedia is still pending must not leave two cameras running.
		const mine = ++attempt;
		const stale = () => finished || mine !== attempt;
		stopCamera();
		problem.hidden = true;
		frame.hidden = false;
		setStatus("Starting camera…");
		if (typeof window.isSecureContext === "boolean" && !window.isSecureContext) {
			showProblem({ text: "The camera needs a secure (https) connection. Type the code below, or close this and use Search.", retry: false });
			return;
		}
		if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
			showProblem({ text: "This browser cannot use the camera here. Type the code below, or close this and use Search.", retry: false });
			return;
		}
		// The decoder downloads while the camera starts: on an iPhone both take a moment.
		const decoderReady = nativeDetector().then((native) => {
			detector = native;
			return native ? null : loadDecoder(o.decoderUrl);
		});
		let media;
		try {
			media = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
		} catch (e) {
			if (!stale()) showProblem(cameraProblem(e));
			return;
		}
		if (stale() || document.hidden) {
			media.getTracks().forEach((t) => t.stop());
			return;
		}
		stream = media;
		video.srcObject = media;
		try {
			await video.play();
		} catch (e) {
			/* muted inline video autoplays; the loop waits for frames either way */
		}
		if (stale()) return;
		offerTorch();
		try {
			await decoderReady;
		} catch (e) {
			if (!stale()) showProblem({ text: "The scanner could not load. Type the code below, or close this and use Search.", retry: true });
			return;
		}
		if (stale() || !stream) return;
		setStatus("Point at a label");
		misses = 0;
		running = true;
		tick();
	}

	function tick() {
		timer = null;
		if (!running) return;
		readFrame().then(
			(code) => {
				misses = 0;
				if (!running) return;
				if (code) deliver(code);
				else timer = setTimeout(tick, SCAN_EVERY_MS);
			},
			() => {
				// A frame that was not ready, or a detector hiccup: look again. A native detector
				// that keeps failing is abandoned for jsQR rather than left spinning in silence.
				misses += 1;
				if (detector && misses > 20) {
					detector = null;
					loadDecoder(o.decoderUrl).then(
						() => running && (timer = setTimeout(tick, SCAN_EVERY_MS)),
						() => showProblem({ text: "The scanner could not load. Type the code below, or close this and use Search.", retry: true })
					);
					return;
				}
				if (running) timer = setTimeout(tick, SCAN_EVERY_MS);
			}
		);
	}

	async function readFrame() {
		if (video.readyState < 2 || !video.videoWidth) return null;
		if (detector) {
			const codes = (await detector.detect(video)) || [];
			const first = codes.find((c) => c && c.rawValue);
			return first ? first.rawValue : null;
		}
		const w = Math.min(FRAME_WIDTH, video.videoWidth);
		const h = Math.round(video.videoHeight * (w / video.videoWidth));
		if (canvas.width !== w) canvas.width = w;
		if (canvas.height !== h) canvas.height = h;
		ctx = ctx || canvas.getContext("2d", { willReadFrequently: true });
		ctx.drawImage(video, 0, 0, w, h);
		const found = window.jsQR(ctx.getImageData(0, 0, w, h).data, w, h, JSQR_OPTIONS);
		return found && found.data ? found.data : null;
	}

	function offerTorch() {
		let caps = null;
		const track = stream && stream.getVideoTracks()[0];
		try {
			caps = track && track.getCapabilities ? track.getCapabilities() : null;
		} catch (e) {
			caps = null;
		}
		torchBtn.hidden = !(caps && caps.torch);
		torchBtn.setAttribute("aria-pressed", "false");
	}

	function toggleTorch() {
		const track = stream && stream.getVideoTracks()[0];
		if (!track) return;
		const next = !torchOn;
		track.applyConstraints({ advanced: [{ torch: next }] }).then(
			() => {
				torchOn = next;
				torchBtn.setAttribute("aria-pressed", next ? "true" : "false");
				torchBtn.classList.toggle("is-on", next);
			},
			() => {
				torchBtn.hidden = true;
			}
		);
	}

	// --- results --------------------------------------------------------------------------
	function deliver(code) {
		if (delivered) return;
		delivered = true;
		finished = true;
		stopCamera();
		buzz(30);
		handle.close("action");
		if (o.onCode) o.onCode(String(code));
	}

	function submitTyped() {
		const text = codeInput.value.trim();
		if (!text) {
			codeInput.focus();
			return;
		}
		deliver(text);
	}

	// A scanner gun "types" into whatever has focus. Keep the camera view clear of the phone's
	// keyboard by not focusing the box up front, and move focus into it on the first keystroke
	// instead; the rest of the code and its Enter then land in the box. Not a key typed into
	// something over the page, such as the report form (`notOurs`).
	function onKeydown(ev) {
		if (finished || ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey || notOurs(ev.target)) return;
		if (ev.key && ev.key.length === 1 && document.activeElement !== codeInput) codeInput.focus();
	}

	function onVisibility() {
		if (document.hidden) stopCamera();
		else if (!finished) start();
	}

	function onPageHide() {
		stopCamera();
	}

	function teardown() {
		finished = true;
		stopCamera();
		document.removeEventListener("keydown", onKeydown, true);
		document.removeEventListener("visibilitychange", onVisibility);
		window.removeEventListener("pagehide", onPageHide);
	}

	document.addEventListener("keydown", onKeydown, true);
	document.addEventListener("visibilitychange", onVisibility);
	window.addEventListener("pagehide", onPageHide);
	start();

	return { close: () => handle.close("programmatic") };
}
