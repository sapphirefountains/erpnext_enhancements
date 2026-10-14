#!/usr/bin/env node
/**
 * The scanner kit (public/js/scanner_kit/, v1.666.0), executed under node.
 *
 * The kit is what the Device Console and the Inventory Scanner Audit share: the camera sheet,
 * the decoder behind it, and the history handling that makes the phone's Back button close a
 * sheet. Each module takes the page's window through `install(win)` / `make_env(win)`, so it runs
 * here against a stand-in window: a stubbed `navigator.mediaDevices`, a BarcodeDetector that is
 * present (reading QR), absent (an iPhone) or present without QR, a jsQR that frappe.require can
 * deliver or fail to, a frappe Dialog, a little jQuery, and a model of v16's router and the
 * session history. What this pins:
 *
 *   - the decoder: native BarcodeDetector where it lists qr_code, and then jsQR is NEVER asked
 *     for; jsQR alone (QR only) with no BarcodeDetector, or one that reads nothing; both where the
 *     detector reads barcodes but not QR; null when nothing can read, and the detector's barcodes
 *     still when only jsQR failed. jsQR is loaded by its bare vendored path, once;
 *   - the camera: asked for only after the sheet is up, the back camera, no audio; the video
 *     `playsinline`, `muted` (attribute and property) and `autoplay`; every track stopped on close,
 *     on visibilitychange to hidden, on pagehide and on frappe's "hide" of the page's wrapper, and
 *     a stream that arrives after the sheet closed is stopped as it arrives; a read hands its code
 *     to the page once the camera is off; the failure sentence by getUserMedia's error name; the
 *     torch only where the track offers one;
 *   - the sheets: a sheet pushes exactly one entry, marked in history.state, and is shown only once
 *     its route has settled; Back pops it, the router closes the sheet, and nothing steps back
 *     twice; a sheet closed another way steps back off its own entry once; a reload or a foreign
 *     link on a sheet URL never reopens it; route_flags.replace_route never outlives its call;
 *   - the bundle installs window.sf_scanner once.
 *
 * Run: node scripts/test_scanner_kit.mjs
 */

import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const KIT = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "scanner_kit");
const BUNDLE = path.join(HERE, "..", "erpnext_enhancements", "public", "js", "scanner_kit.bundle.js");
const kit = await import(pathToFileURL(path.join(KIT, "index.js")).href);
const JSQR = "/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js";

let failures = 0;
let checks = 0;
const unhandled = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));

async function test(name, fn) {
	checks += 1;
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.stack ? err.stack : String(err)).split("\n").join("\n       "));
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function drain(rounds = 8) {
	for (let i = 0; i < rounds; i += 1) await tick();
}

// ------------------------------------------------------------------ a stand-in window

function element(label, extra) {
	return Object.assign({ label, events: [], children: {}, attrs: {}, html: "", shown: false }, extra || {});
}

// The part of jQuery the kit uses: on / one / off (with namespaces), trigger, html, find, show, attr.
function jq(el) {
	const self = {
		0: el,
		length: 1,
		on(type, fn) {
			const dot = type.indexOf(".");
			el.events.push({ type: dot < 0 ? type : type.slice(0, dot), ns: dot < 0 ? null : type.slice(dot + 1), fn, once: false });
			return self;
		},
		one(type, fn) {
			self.on(type, fn);
			el.events[el.events.length - 1].once = true;
			return self;
		},
		off(type) {
			const dot = type.indexOf(".");
			const base = dot < 0 ? type : type.slice(0, dot);
			const ns = dot < 0 ? null : type.slice(dot + 1);
			el.events = el.events.filter((h) => !((!base || h.type === base) && (!ns || h.ns === ns)));
			return self;
		},
		// "shown.bs.modal" is the event "shown" in the namespace "bs.modal", as in jQuery.
		trigger(type, target) {
			const dot = type.indexOf(".");
			const base = dot < 0 ? type : type.slice(0, dot);
			const ns = dot < 0 ? null : type.slice(dot + 1);
			const hs = el.events.filter((h) => h.type === base && (!ns || h.ns === ns));
			el.events = el.events.filter((h) => !(h.once && hs.includes(h)));
			hs.forEach((h) => h.fn({ type: base, target: target || el }));
			return self;
		},
		html(text) {
			el.html = text;
			return self;
		},
		find(sel) {
			if (!el.children[sel]) {
				el.children[sel] =
					sel === "video"
						? element("video", { readyState: 4, videoWidth: 1280, videoHeight: 720, muted: false, plays: 0, play() { this.plays += 1; return Promise.resolve(); } })
						: element(sel);
			}
			return jq(el.children[sel]);
		},
		show() {
			el.shown = true;
			return self;
		},
		attr(name, value) {
			el.attrs[name] = value;
			return self;
		},
	};
	return self;
}

// opts: detector "qr" (default) | "none" | "no-qr" | "nothing"; jsqr: true (default) | false;
// camera: "allow" (default) | "manual" | an error name to reject with; torch: false.
function makeWin(opts) {
	opts = opts || {};
	const win = { required: [], messages: [], dialogs: [], cameras: [], detected: [], read: null };

	// ---- clock
	let now = 0;
	let seq = 0;
	const timers = new Map();
	win.setTimeout = (fn, ms) => {
		const id = ++seq;
		timers.set(id, { id, at: now + Math.max(0, Number(ms) || 0), fn });
		return id;
	};
	win.clearTimeout = (id) => timers.delete(id);
	win.wait = async (ms) => {
		const end = now + (ms || 0);
		for (;;) {
			await drain();
			let next = null;
			for (const t of timers.values()) if (t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
			if (!next) break;
			timers.delete(next.id);
			now = next.at;
			next.fn();
		}
		now = end;
		await drain();
	};
	win.settle = () => win.wait(2000);

	// ---- window and document events
	const listeners = {};
	win.addEventListener = (type, fn) => (listeners[type] = listeners[type] || []).push(fn);
	win.removeEventListener = (type, fn) => (listeners[type] = (listeners[type] || []).filter((f) => f !== fn));
	win.fire = (type) => (listeners[type] || []).slice().forEach((fn) => fn({ type }));
	const docListeners = {};
	win.document = {
		hidden: false,
		addEventListener: (type, fn) => (docListeners[type] = docListeners[type] || []).push(fn),
		removeEventListener: (type, fn) => (docListeners[type] = (docListeners[type] || []).filter((f) => f !== fn)),
		createElement: (tag) =>
			element(tag, {
				getContext: () => ({ drawImage() {}, getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }) }),
			}),
	};
	win.fireDoc = (type) => (docListeners[type] || []).slice().forEach((fn) => fn({ type }));
	win.docListeners = (type) => (docListeners[type] || []).length;
	win.winListeners = (type) => (listeners[type] || []).length;
	win.$ = (el) => jq(el);

	// ---- decoders
	const detector = opts.detector || "qr";
	if (detector !== "none") {
		win.BarcodeDetector = class {
			static getSupportedFormats() {
				if (detector === "nothing") return Promise.resolve([]);
				return Promise.resolve(detector === "qr" ? ["qr_code", "code_128"] : ["code_128", "ean_13"]);
			}
			detect() {
				win.detected.push("native");
				const code = win.read && !String(win.read).startsWith("QR:") ? win.read : null;
				if (code) win.read = null;
				return Promise.resolve(code ? [{ rawValue: code }] : []);
			}
		};
	}
	const jsQR = () => {
		win.detected.push("jsqr");
		const code = win.read && String(win.read).startsWith("QR:") ? win.read : null;
		if (code) win.read = null;
		return code ? { data: code } : null;
	};

	// ---- camera
	win.navigator = {
		mediaDevices: {
			getUserMedia(constraints) {
				const req = { constraints, stream: null, shownFirst: win.dialogs.some((d) => d.display || d.fading) };
				req.promise = new Promise((resolve, reject) => {
					req.allow = () => {
						const track = {
							stopped: 0,
							stop() {
								this.stopped += 1;
							},
							torch: [],
						};
						if (opts.torch) {
							track.getCapabilities = () => ({ torch: true });
							track.applyConstraints = (c) => {
								track.torch.push(c.advanced[0].torch);
								return Promise.resolve();
							};
						}
						req.track = track;
						req.stream = { getTracks: () => [track], getVideoTracks: () => [track] };
						resolve(req.stream);
					};
					req.deny = (name) => {
						const err = new Error(name);
						err.name = name;
						reject(err);
					};
				});
				win.cameras.push(req);
				const camera = opts.camera || "allow";
				if (camera === "allow") win.setTimeout(() => req.allow(), 10);
				else if (camera !== "manual") win.setTimeout(() => req.deny(camera), 10);
				return req.promise;
			},
		},
	};
	win.live = () => win.cameras.filter((c) => c.track && !c.track.stopped).length;

	// ---- session history and v16's router, as far as the sheets lean on them
	const entries = [{ url: "/desk/home", state: null }];
	let index = 0;
	const routeOf = (url) => url.replace(/^\/desk\//, "").split("/").filter(Boolean);
	const changeHandlers = [];
	let current = ["home"];
	win.history = {
		get state() {
			return entries[index].state;
		},
		replaceState(state, title, url) {
			entries[index] = { url: url || entries[index].url, state };
		},
		pushState(state, title, url) {
			entries.splice(index + 1);
			entries.push({ url, state });
			index += 1;
		},
		back() {
			win.setTimeout(() => {
				if (index > 0) {
					index -= 1;
					route();
				}
			}, 0);
		},
		forward() {
			win.setTimeout(() => {
				if (index < entries.length - 1) {
					index += 1;
					route();
				}
			}, 0);
		},
	};
	win.entries = () => entries.map((e) => e.url);
	win.at = () => index;
	win.shows = 0;
	win.onShow = null; // the page's on_page_show, set by a test
	function route() {
		current = routeOf(entries[index].url);
		// set_history closes the open dialog on every route change.
		win.dialogs.filter((d) => d.display).forEach((d) => d.hide());
		changeHandlers.splice(0).forEach((fn) => fn());
		win.shows += 1;
		if (win.onShow) win.onShow();
	}
	win.frappe = {
		route_flags: {},
		get_route: () => current.slice(),
		set_route(...parts) {
			const url = "/desk/" + parts.join("/");
			win.sets = (win.sets || 0) + 1;
			if (win.frappe.route_flags.replace_route) win.history.replaceState(null, "", url);
			else win.history.pushState(null, "", url);
			route();
			return new Promise((resolve) =>
				win.setTimeout(() => {
					win.frappe.route_flags = {};
					resolve();
				}, 100)
			);
		},
		router: { once: (type, fn) => type === "change" && changeHandlers.push(fn) },
		require(paths, cb) {
			const list = [].concat(paths);
			win.required.push(...list);
			if (list.includes(JSQR) && opts.jsqr !== false) win.jsQR = jsQR;
			if (cb) cb();
			return Promise.resolve();
		},
		msgprint: (m) => win.messages.push(m),
		ui: {
			Dialog: class {
				constructor(o) {
					this.title = o.title;
					this.$body = jq(element("body"));
					this.$wrapper = jq(element("wrapper"));
					this.display = false;
					this.fading = false;
					win.dialogs.push(this);
				}
				show() {
					if (opts.fade) {
						this.fading = true;
						return;
					}
					this.display = true;
					this.$wrapper.trigger("shown.bs.modal");
				}
				shown() {
					this.fading = false;
					this.display = true;
					this.$wrapper.trigger("shown.bs.modal");
				}
				hide() {
					if (!this.display) return; // Bootstrap ignores hide() during the fade-in
					this.display = false;
					if (this.onhide) this.onhide();
				}
			},
		},
	};
	win.open = () => win.dialogs.filter((d) => d.display).map((d) => d.title);
	win.go = (url) => {
		entries.splice(index + 1);
		entries.push({ url, state: null });
		index = entries.length - 1;
		route();
	};
	return win;
}

const PAGE = "scanner-page";
function setup(opts) {
	const win = makeWin(opts);
	const sf = kit.install(win);
	const nav = sf.sheets({ route: PAGE, names: ["camera", "find"], mark: "test_sheet", back_timeout_ms: 1500 });
	const wrapper = element("page:" + PAGE);
	nav.watch(wrapper);
	win.closed = [];
	win.onShow = () => nav.show({ reopen: () => false });
	win.go("/desk/" + PAGE);
	// The page's own camera: reader, then the sheet, then the camera, as the two pages do it.
	const openCamera = (reopen) =>
		sf.camera.prepare().then((reader) => {
			if (!reader) return null;
			win.reader = reader;
			nav.open(
				"camera",
				() =>
					sf.camera.show({
						nav,
						reader,
						page: wrapper,
						video_class: "x-video",
						on_close: (closed) => nav.leave("camera", () => win.closed.push(closed)),
					}),
				reopen
			);
			return reader;
		});
	return { win, sf, nav, wrapper, openCamera };
}
const camera = (win) => win.dialogs.filter((d) => d.title === "Camera Scan").pop();

// ------------------------------------------------------------------ the decoder

console.log("the decoder");

await test("BarcodeDetector reading QR: native, and jsQR is never loaded", async () => {
	const win = makeWin({ detector: "qr" });
	const reader = await kit.create_reader(kit.make_env(win));
	assert.equal(reader.mode, "native");
	assert.equal(reader.qr_only, false);
	assert.deepEqual(win.required, []);
	win.read = "ABC-1";
	assert.equal(await reader.read({}), "ABC-1");
	assert.deepEqual(win.detected, ["native"]);
});

await test("no BarcodeDetector (an iPhone): jsQR alone, QR only, loaded by its bare vendored path", async () => {
	const win = makeWin({ detector: "none" });
	const env = kit.make_env(win);
	const reader = await kit.create_reader(env);
	assert.equal(reader.mode, "jsqr");
	assert.equal(reader.qr_only, true);
	assert.deepEqual(win.required, [JSQR]);
	win.read = "QR:LOC-1";
	assert.equal(await reader.read({ videoWidth: 1920, videoHeight: 1080 }), "QR:LOC-1");
	assert.equal(await reader.read({ videoWidth: 0, videoHeight: 0 }), null, "no frame yet: nothing read");
	await kit.create_reader(env);
	assert.deepEqual(win.required, [JSQR], "loaded once per page");
});

await test("BarcodeDetector without QR: both — the detector's barcodes, jsQR's QR codes", async () => {
	const win = makeWin({ detector: "no-qr" });
	const reader = await kit.create_reader(kit.make_env(win));
	assert.equal(reader.mode, "both");
	assert.equal(reader.qr_only, false);
	assert.deepEqual(win.required, [JSQR]);
	win.read = "IMEI-35";
	assert.equal(await reader.read({ videoWidth: 640, videoHeight: 480 }), "IMEI-35");
	win.read = "QR:LOC-2";
	assert.equal(await reader.read({ videoWidth: 640, videoHeight: 480 }), "QR:LOC-2");
});

await test("a BarcodeDetector that reads nothing is left alone: jsQR, QR only", async () => {
	const win = makeWin({ detector: "nothing" });
	const reader = await kit.create_reader(kit.make_env(win));
	assert.equal(reader.mode, "jsqr");
	assert.equal(reader.qr_only, true);
});

await test("jsQR failing to load: no reader on an iPhone, the detector's barcodes where there is one", async () => {
	const iphone = makeWin({ detector: "none", jsqr: false });
	assert.equal(await kit.create_reader(kit.make_env(iphone)), null);
	const android = makeWin({ detector: "no-qr", jsqr: false });
	const reader = await kit.create_reader(kit.make_env(android));
	assert.equal(reader.mode, "native");
});

await test("prepare() says why there is no camera, in the page's words when it gives them", async () => {
	const win = makeWin({ detector: "none", jsqr: false });
	const sf = kit.install(win);
	assert.equal(await sf.camera.prepare({ no_reader: "No reader here." }), null);
	assert.deepEqual(win.messages, ["No reader here."]);
	delete win.navigator.mediaDevices;
	assert.equal(sf.camera.available(), false);
	assert.equal(await sf.camera.prepare({ unavailable: "Needs https." }), null);
	assert.deepEqual(win.messages, ["No reader here.", "Needs https."]);
	assert.deepEqual(win.required, [JSQR], "an unavailable camera loads nothing");
});

// ------------------------------------------------------------------ the camera

console.log("the camera");

await test("asked for only once the sheet is up: back camera, no audio, an inline muted video", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	assert.equal(win.cameras.length, 1);
	assert.equal(win.cameras[0].shownFirst, true, "the sheet was up before getUserMedia");
	assert.deepEqual(win.cameras[0].constraints, { video: { facingMode: "environment" }, audio: false });
	const body = camera(win).$body[0];
	assert.match(body.html, /<video class="x-video" playsinline muted autoplay><\/video>/);
	const video = body.children.video;
	assert.equal(video.muted, true, "muted as a property too: iOS plays inline, unprompted, only when muted");
	assert.equal(video.srcObject, win.cameras[0].stream);
	assert.equal(video.plays, 1);
	assert.equal(win.live(), 1);
});

await test("a read stops every track, closes the sheet, and hands the code over", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	win.read = "DEV-9";
	await win.settle();
	assert.equal(win.live(), 0);
	assert.deepEqual(win.open(), []);
	assert.deepEqual(win.closed, [{ read: "DEV-9", failure: null }]);
	assert.equal(win.cameras[0].track.stopped >= 1, true);
	assert.equal(win.docListeners("visibilitychange"), 0, "its listeners go with it");
	assert.equal(win.winListeners("pagehide"), 0);
});

await test("closing the sheet (X) stops every track", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	camera(win).hide();
	assert.equal(win.live(), 0, "at once");
	await win.settle();
	assert.deepEqual(win.closed, [{ read: null, failure: null }]);
});

await test("the document going hidden (the phone locked) stops the camera at once", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	win.document.hidden = true;
	win.fireDoc("visibilitychange");
	assert.equal(win.live(), 0);
	assert.deepEqual(win.open(), []);
});

await test("pagehide (the tab closed, or put in the back-forward cache) stops the camera", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	win.fire("pagehide");
	assert.equal(win.live(), 0);
});

await test("frappe's hide of the page's wrapper stops a camera still fading in", async () => {
	const { win, wrapper, openCamera } = setup({ fade: true });
	await openCamera();
	await win.settle();
	const d = camera(win);
	assert.equal(d.fading, true);
	assert.equal(win.live(), 1);
	// A Bootstrap "hide.bs.*" bubbling up from inside the page is not the page being left.
	win.$(wrapper).trigger("hide", element("modal"));
	assert.equal(win.live(), 1);
	win.$(wrapper).trigger("hide");
	assert.equal(win.live(), 0, "the light goes off at once");
	d.shown();
	assert.equal(d.display, false, "and the sheet closes as soon as Bootstrap lets it");
	assert.equal(wrapper.events.filter((h) => h.ns && h.ns.startsWith("sf_scanner")).length, 0, "its handler goes with it");
});

await test("a stream that arrives after the sheet closed is stopped as it arrives", async () => {
	const { win, openCamera } = setup({ camera: "manual" });
	await openCamera();
	await win.settle();
	camera(win).hide();
	win.cameras[0].allow();
	await win.settle();
	assert.equal(win.live(), 0);
});

for (const [name, said] of [
	["NotAllowedError", "Camera access is blocked for this site. Allow the camera in the browser's settings, then try again."],
	["SecurityError", "Camera access is blocked for this site. Allow the camera in the browser's settings, then try again."],
	["NotFoundError", "No camera was found on this device."],
	["OverconstrainedError", "No camera was found on this device."],
	["NotReadableError", "Could not access the camera."],
]) {
	await test(`getUserMedia refused with ${name} says so`, async () => {
		const { win, openCamera } = setup({ camera: name });
		await openCamera();
		await win.settle();
		assert.deepEqual(win.open(), []);
		assert.deepEqual(win.closed, [{ read: null, failure: said }]);
	});
}

await test("the torch shows only where the track offers one, and toggles it", async () => {
	const without = setup();
	await without.openCamera();
	await without.win.settle();
	assert.equal(camera(without.win).$body[0].children[".sf-scanner-torch"].shown, false);
	const { win, openCamera } = setup({ torch: true });
	await openCamera();
	await win.settle();
	const torch = camera(win).$body[0].children[".sf-scanner-torch"];
	assert.equal(torch.shown, true);
	win.$(torch).trigger("click");
	await win.settle();
	assert.deepEqual(win.cameras[0].track.torch, [true]);
	assert.equal(torch.attrs["aria-pressed"], "true");
	assert.match(camera(win).$body[0].html, /<button type="button"[^>]*>Torch<\/button>/, "a named button");
});

await test("the hint says QR only where only jsQR reads", async () => {
	const iphone = setup({ detector: "none" });
	await iphone.openCamera();
	await iphone.win.settle();
	assert.match(camera(iphone.win).$body[0].html, /Point the camera at a QR code\./);
	const android = setup();
	await android.openCamera();
	await android.win.settle();
	assert.match(camera(android.win).$body[0].html, /Point the camera at a barcode or QR code\./);
});

// ------------------------------------------------------------------ the sheets

console.log("the sheets");

await test("a sheet pushes one entry, marked, and is shown only once its route settles", async () => {
	const { win, openCamera } = setup();
	assert.deepEqual(win.entries(), ["/desk/home", "/desk/" + PAGE]);
	const opening = openCamera();
	await opening;
	await drain();
	assert.deepEqual(win.entries(), ["/desk/home", "/desk/" + PAGE, "/desk/" + PAGE + "/camera"], "one entry pushed");
	assert.deepEqual(win.history.state, { test_sheet: "camera" });
	assert.deepEqual(win.open(), [], "not before the route settles");
	assert.equal(win.frappe.route_flags.replace_route, false, "no replace left set");
	await win.settle();
	assert.deepEqual(win.open(), ["Camera Scan"]);
	assert.equal(win.entries().length, 3, "and only one");
});

await test("Back pops the sheet's entry: the router closes it, the camera stops, nothing steps back twice", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	win.history.back();
	await win.settle();
	assert.deepEqual(win.open(), []);
	assert.equal(win.live(), 0);
	assert.equal(win.at(), 1, "on the page's own entry, not behind it");
	assert.deepEqual(win.frappe.get_route(), [PAGE]);
	assert.equal(win.entries().length, 3, "the sheet's entry is still ahead, for Forward");
});

await test("a sheet closed another way steps back off its own entry, once", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	camera(win).hide();
	await win.settle();
	assert.equal(win.at(), 1);
	assert.deepEqual(win.frappe.get_route(), [PAGE]);
	assert.deepEqual(win.closed, [{ read: null, failure: null }], "handed over once back on the page's entry");
});

await test("Back pressed before the sheet's route settles never shows it", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.wait(40);
	win.history.back();
	await win.settle();
	assert.deepEqual(win.open(), []);
	assert.equal(win.cameras.length, 0);
});

await test("a reload or a pasted link on a sheet URL never opens the sheet", async () => {
	const win = makeWin();
	const sf = kit.install(win);
	const nav = sf.sheets({ route: PAGE, names: ["camera"], mark: "test_sheet" });
	let reopened = 0;
	win.onShow = () => nav.show({ reopen: () => (reopened += 1) });
	win.go("/desk/" + PAGE + "/camera");
	// Cleared as soon as set_route returned, long before set_route's own reset 100 ms later.
	assert.equal(win.frappe.route_flags.replace_route, false);
	await win.settle();
	assert.deepEqual(win.frappe.get_route(), [PAGE], "the entry became the page");
	assert.deepEqual(win.entries(), ["/desk/home", "/desk/" + PAGE], "replaced, not pushed");
	assert.equal(reopened, 0);
});

await test("a reload on a sheet entry the page marked steps back onto the page's own entry", async () => {
	const { win, openCamera } = setup();
	await openCamera();
	await win.settle();
	// The reload: the old page and its sheet are gone, and a new page's first show lands on the
	// sheet entry the old one marked (history.state survives a reload).
	win.dialogs.length = 0;
	const fresh = win.sf_scanner.sheets({ route: PAGE, names: ["camera", "find"], mark: "test_sheet" });
	win.onShow = () => fresh.show({ reopen: () => assert.fail("reopened") });
	const shown = fresh.show({ reopen: () => assert.fail("reopened") });
	assert.deepEqual(shown, { sheet: "camera", came: undefined });
	await win.settle();
	assert.equal(win.at(), 1, "stepped back onto the page's own entry");
	assert.equal(win.entries().length, 3, "no second page entry");
	assert.equal(win.cameras.length, 1, "no camera nobody tapped for");
});

await test("a held entry is neither reopened nor stepped off", async () => {
	const { win, nav, openCamera } = setup();
	await openCamera();
	await win.settle();
	win.history.back();
	await win.settle();
	win.onShow = () => nav.show({ hold: (sheet) => sheet === "camera", reopen: () => assert.fail("reopened") });
	win.history.forward();
	await win.settle();
	assert.deepEqual(win.frappe.get_route(), [PAGE, "camera"]);
	assert.equal(win.at(), 2);
});

// ------------------------------------------------------------------ the bundle

console.log("the bundle");

await test("the bundle installs window.sf_scanner once", async () => {
	const win = makeWin();
	globalThis.window = win;
	await import(pathToFileURL(BUNDLE).href);
	assert.equal(win.sf_scanner.version, kit.VERSION);
	for (const name of ["sheets"]) assert.equal(typeof win.sf_scanner[name], "function");
	for (const name of ["available", "prepare", "reader", "show", "failure_message"]) assert.equal(typeof win.sf_scanner.camera[name], "function");
	assert.equal(win.sf_scanner.QR_DECODER, JSQR);
	const first = win.sf_scanner;
	kit.install(win);
	assert.equal(win.sf_scanner, first, "a second install leaves it alone");
	delete globalThis.window;
});

await drain();
if (unhandled.length) {
	failures += 1;
	console.log("  FAIL unhandled rejections: " + unhandled.map(String).join("; "));
}
if (failures) {
	console.error(`\n${failures} of ${checks} checks failed`);
	process.exit(1);
}
console.log(`\nall ${checks} checks passed`);
