/**
 * The capture recorder (WI-079 slice 2, ADR 0016 §4). Installs `window.ee_capture`.
 *
 * It starts at page load and keeps three small rings: the last 20 console errors, uncaught
 * errors and unhandled rejections; the last 20 requests that failed or were slow; and the last
 * ten routes. `ee_capture.open()` freezes them into a snapshot and hands it to the report form,
 * which is loaded only then. The form shows the user every captured value before anything is
 * sent, and the only place it is ever sent is ERPNext's own `submit_capture`.
 *
 * It ships in the Desk bundle, on every Desk page, and in `capture.bundle.js` on the allowlisted
 * web pages. It is imported first in both, so it wraps fetch, XHR and console before any other
 * script can use them. That makes the rules strict:
 *
 *   - It never breaks the page. Each piece of recorder work is in its own try/catch, and a
 *     wrapped function always calls through to the original and returns what it returned.
 *   - It never swallows the page's errors. A wrapped fetch hands back a promise that settles
 *     exactly as the original did, rejections included. Listeners never call preventDefault.
 *   - It never keeps a body. A failed JSON response is read once, from a clone, to pull out
 *     Frappe's `exc_type` string. Nothing else from it is kept.
 *   - On web and kiosk pages it never reads the page's query string: only
 *     `location.pathname` is kept. On the Desk the query string is kept, decoded and then
 *     scrubbed (state.js), because list filters live there. Request paths always lose their
 *     query string before they are stored.
 *
 * Installs once per window, however many bundles include it.
 */

import { scrubPath, scrubText } from "./scrub.js";
import { collectDevice, collectPage, installFormBaseline } from "./state.js";

export const LIMITS = { console: 20, requests: 20, routes: 10 };
export const SLOW_MS = 3000;
const PANEL_TIMEOUT_MS = 15000;
const MAX_ERROR_BODY = 262144;
const MAX_APP_STATE = 20000;

// ---- pure ring helpers (node-tested by scripts/test_capture_recorder.js) -----------------

/** Append, dropping the oldest entries past `max`. Oldest first, always. */
export function pushRing(ring, item, max) {
	ring.push(item);
	while (ring.length > max) ring.shift();
	return ring;
}

/**
 * A console entry, collapsed into the previous one when it repeats it. A render loop that
 * throws the same error 400 times must not push the one different error out of the ring.
 * `at` moves to the latest repeat, which keeps the ring in time order.
 */
export function pushConsole(ring, entry, max) {
	const last = ring[ring.length - 1];
	if (last && last.level === entry.level && last.message === entry.message) {
		last.count = (last.count || 1) + 1;
		last.at = entry.at;
		return ring;
	}
	entry.count = entry.count || 1;
	return pushRing(ring, entry, max);
}

/** A route, skipped when it repeats the last one (Frappe's router often says it twice). */
export function pushRoute(ring, entry, max) {
	const last = ring[ring.length - 1];
	if (last && last.path === entry.path) return ring;
	return pushRing(ring, entry, max);
}

/** "failed" (network error or >= 400), "slow" (>= 3 s, even a 200), or null to ignore. */
export function classifyRequest(status, durationMs) {
	const code = Number(status) || 0;
	if (code === 0 || code >= 400) return "failed";
	if (Number(durationMs) >= SLOW_MS) return "slow";
	return null;
}

/** Frappe's exception class name from an error body, or "". Never anything else from it. */
export function excTypeFrom(payload) {
	const value = payload && typeof payload === "object" ? payload.exc_type : null;
	return typeof value === "string" && /^[A-Za-z_][\w.]{0,80}$/.test(value) ? value : "";
}

/** One console argument as a short string. Never a whole object graph, never function source. */
export function describe(value) {
	try {
		if (value === null || value === undefined) return String(value);
		const t = typeof value;
		if (t === "string") return value;
		if (t === "function") return "[function " + (value.name || "anonymous") + "]";
		if (t !== "object") return String(value);
		if (typeof value.message === "string") {
			return typeof value.name === "string" && value.name ? value.name + ": " + value.message : value.message;
		}
		const short = (v) => {
			const vt = typeof v;
			return v === null || vt === "string" || vt === "number" || vt === "boolean" ? String(v).slice(0, 60) : vt;
		};
		if (Array.isArray(value)) return "[" + value.slice(0, 6).map(short).join(", ") + "]";
		return "{" + Object.keys(value).slice(0, 6).map((k) => k + ": " + short(value[k])).join(", ") + "}";
	} catch (e) {
		return "[unreadable]";
	}
}

// ---- install --------------------------------------------------------------------------------

/**
 * The surface decides what may be read. Anything not positively the Desk is treated as a web
 * page, which is the stricter case: no query string. So a template that forgets
 * `window.EE_CAPTURE` fails safe.
 */
function resolveConfig(win) {
	const boot = win.EE_CAPTURE;
	if (boot && typeof boot === "object") {
		return {
			surface: boot.surface === "kiosk" ? "kiosk" : "web",
			user: String(boot.user || ""),
			panel_url: String(boot.panel_url || ""),
			launcher: boot.launcher === true,
		};
	}
	const frappe = win.frappe;
	const path = (win.location && win.location.pathname) || "";
	const desk = !!(frappe && frappe.boot && /^\/(desk|app)(\/|$)/.test(path));
	return { surface: desk ? "desk" : "web", user: desk ? deskUser(frappe) : "", panel_url: "", launcher: false };
}

/** `frappe.session` is filled in when the Desk app starts, after this bundle runs; boot has it sooner. */
function deskUser(frappe) {
	try {
		const user = (frappe.session && frappe.session.user) || (frappe.boot && frappe.boot.user && frappe.boot.user.name);
		return String(user || "");
	} catch (e) {
		return "";
	}
}

// Frappe reads a `sid` key anywhere in a request as a session id, and fakes "session expired".
const dropReserved = (key, value) => (key === "sid" ? undefined : value);

export function install(win) {
	try {
		if (!win || (win.ee_capture && win.ee_capture.installed)) return (win && win.ee_capture) || null;
	} catch (e) {
		return null;
	}

	const rings = { console: [], requests: [], routes: [] };
	const states = [];
	const config = resolveConfig(win);
	const perf = win.performance;
	const clock = perf && typeof perf.now === "function" ? () => perf.now() : () => Date.now();
	const iso = () => new Date().toISOString();
	let origin = "";
	try {
		origin = win.location.origin || "";
	} catch (e) {
		// No origin: every URL is kept as //host/path, which is safe.
	}
	let loading = null;

	function noteConsole(level, message) {
		const text = scrubText(message, 500);
		if (text) pushConsole(rings.console, { at: iso(), level, message: text, count: 1 }, LIMITS.console);
	}

	function noteRequest(meta, status, duration, kind) {
		const path = scrubPath(meta.url, origin);
		// Socket.IO's polling fallback holds each request open for ~25 s by design. Recording it
		// would fill the ring with "slow" requests that are working perfectly.
		if (path.indexOf("/socket.io/") === 0) return null;
		const record = {
			at: iso(),
			method: meta.method,
			path,
			status,
			duration_ms: Math.max(0, Math.round(duration)),
			exc_type: "",
			kind,
		};
		pushRing(rings.requests, record, LIMITS.requests);
		return record;
	}

	function noteRoute() {
		try {
			const path = scrubPath((win.location && win.location.pathname) || "");
			if (path) pushRoute(rings.routes, { at: iso(), path }, LIMITS.routes);
			// form.bundle.js is loaded before the app bundle today. If that ever changes, the
			// first route change is late enough.
			if (config.surface === "desk") installFormBaseline(win);
		} catch (e) {
			// A lost route is not worth a broken navigation.
		}
	}

	// An aborted request is usually the page changing its mind (a typeahead, a closed dialog).
	// It only counts when it was slow, which is what a timeout abort looks like.
	function noteAborted(meta, duration) {
		if (duration >= SLOW_MS) noteRequest(meta, 0, duration, "slow");
	}

	function onFetchResponse(res, meta) {
		const duration = clock() - meta.start;
		// An opaque (no-cors) response reports status 0 whether or not it worked.
		if (!res || res.type === "opaque" || res.type === "opaqueredirect") return;
		const status = Number(res.status) || 0;
		const kind = classifyRequest(status, duration);
		if (!kind) return;
		const record = noteRequest(meta, status, duration, kind);
		if (!record || status < 400 || typeof res.clone !== "function" || !res.headers) return;
		const type = String(res.headers.get("content-type") || "");
		const size = Number(res.headers.get("content-length") || 0);
		if (type.indexOf("json") === -1 || size > MAX_ERROR_BODY) return;
		// A clone, read now, before the page's own handler runs. The page's body is untouched.
		res.clone()
			.json()
			.then(
				(body) => {
					record.exc_type = excTypeFrom(body);
				},
				() => {}
			);
	}

	function wrapFetch() {
		const original = win.fetch;
		if (typeof original !== "function") return;
		win.fetch = function (input, init) {
			let meta = null;
			try {
				const method = (init && init.method) || (input && typeof input === "object" && input.method) || "GET";
				const url = typeof input === "string" ? input : input && (input.url || input.href || String(input));
				meta = { method: String(method).toUpperCase().slice(0, 10), url: String(url || ""), start: clock() };
			} catch (e) {
				meta = null;
			}
			// Called with the page's own `this` and arguments, so it fails exactly as it would
			// have without the wrapper.
			const promise = original.apply(this, arguments);
			if (!meta || !promise || typeof promise.then !== "function") return promise;
			return promise.then(
				(res) => {
					try {
						onFetchResponse(res, meta);
					} catch (e) {
						// The response is the page's; it goes back regardless.
					}
					return res;
				},
				(err) => {
					try {
						const duration = clock() - meta.start;
						if (err && err.name === "AbortError") noteAborted(meta, duration);
						else noteRequest(meta, 0, duration, "failed");
					} catch (e) {
						// Nothing to add; the page still gets its rejection.
					}
					throw err;
				}
			);
		};
	}

	function xhrExcType(xhr) {
		try {
			if (xhr.responseType === "json") return excTypeFrom(xhr.response);
			if (xhr.responseType && xhr.responseType !== "text") return "";
			const text = xhr.responseText;
			if (!text || text.length > MAX_ERROR_BODY || text.charAt(0) !== "{") return "";
			return excTypeFrom(JSON.parse(text));
		} catch (e) {
			return "";
		}
	}

	function wrapXhr() {
		const proto = win.XMLHttpRequest && win.XMLHttpRequest.prototype;
		if (!proto || typeof proto.open !== "function" || typeof proto.send !== "function") return;
		const metas = new WeakMap();
		const originalOpen = proto.open;
		const originalSend = proto.send;

		function onAbort() {
			try {
				const meta = metas.get(this);
				if (meta) meta.aborted = true;
			} catch (e) {
				// Ignored.
			}
		}

		function onLoadEnd() {
			try {
				const meta = metas.get(this);
				if (!meta || !meta.inFlight) return;
				const duration = clock() - meta.start;
				meta.inFlight = false;
				if (meta.aborted) return noteAborted(meta, duration);
				const status = Number(this.status) || 0;
				const kind = classifyRequest(status, duration);
				if (!kind) return;
				const record = noteRequest(meta, status, duration, kind);
				if (record && status >= 400) record.exc_type = xhrExcType(this);
			} catch (e) {
				// Ignored: the request itself has already finished for the page.
			}
		}

		proto.open = function (method, url) {
			try {
				const previous = metas.get(this);
				metas.set(this, {
					method: String(method || "GET").toUpperCase().slice(0, 10),
					url: String(url || ""),
					start: 0,
					inFlight: false,
					aborted: false,
					hooked: !!(previous && previous.hooked),
				});
			} catch (e) {
				// Unrecorded, not broken.
			}
			return originalOpen.apply(this, arguments);
		};

		proto.send = function () {
			try {
				const meta = metas.get(this);
				if (meta) {
					meta.start = clock();
					meta.inFlight = true;
					meta.aborted = false;
					// A reused XHR keeps its listeners, so they are added once per object.
					if (!meta.hooked) {
						meta.hooked = true;
						this.addEventListener("abort", onAbort);
						this.addEventListener("loadend", onLoadEnd);
					}
				}
			} catch (e) {
				// Unrecorded, not broken.
			}
			return originalSend.apply(this, arguments);
		};
	}

	function wrapConsole() {
		const con = win.console;
		if (!con || typeof con.error !== "function") return;
		const original = con.error;
		let busy = false;
		con.error = function () {
			// `busy` stops a describe() that logs (a toString with a console.error in it) from
			// recursing forever.
			if (!busy) {
				busy = true;
				try {
					noteConsole("error", Array.prototype.slice.call(arguments, 0, 8).map(describe).join(" "));
				} catch (e) {
					// The page's message still prints.
				}
				busy = false;
			}
			return original.apply(this, arguments);
		};
	}

	function listenForErrors() {
		// Capture phase, so a <script> or stylesheet that fails to load is seen too. Those do not
		// bubble, and a stale bundle URL after a deploy is one of the failures worth reporting.
		win.addEventListener(
			"error",
			(ev) => {
				try {
					const target = ev && ev.target;
					if (target && target !== win && target.tagName) {
						const tag = String(target.tagName).toLowerCase();
						// Images and icons fail all the time and say nothing about the software.
						if (tag === "script" || (tag === "link" && target.rel === "stylesheet")) {
							noteConsole("uncaught", "Could not load " + tag + " " + scrubPath(target.src || target.href, origin));
						}
						return;
					}
					let message = (ev && ev.message) || (ev && ev.error ? describe(ev.error) : "Unknown error");
					if (ev && ev.filename) message += " (" + scrubPath(ev.filename, origin) + ":" + (ev.lineno || 0) + ")";
					noteConsole("uncaught", message);
				} catch (e) {
					// Ignored.
				}
			},
			true
		);
		win.addEventListener("unhandledrejection", (ev) => {
			try {
				noteConsole("rejection", describe(ev && ev.reason));
			} catch (e) {
				// Ignored.
			}
		});
	}

	function watchRoutes() {
		noteRoute();
		const history = win.history;
		for (const name of ["pushState", "replaceState"]) {
			const original = history && history[name];
			if (typeof original !== "function") continue;
			history[name] = function () {
				const result = original.apply(this, arguments);
				noteRoute();
				return result;
			};
		}
		win.addEventListener("popstate", noteRoute);
		const router = config.surface === "desk" && win.frappe && win.frappe.router;
		if (router && typeof router.on === "function") router.on("change", noteRoute);
	}

	function collectApp() {
		const app = {};
		for (const fn of states.slice()) {
			try {
				const out = fn();
				if (!out || typeof out !== "object" || Array.isArray(out)) continue;
				const json = JSON.stringify(out, dropReserved);
				if (json && json.length <= MAX_APP_STATE) Object.assign(app, JSON.parse(json));
			} catch (e) {
				// One bad registration costs its own keys, not the snapshot.
			}
		}
		return app;
	}

	function snapshot() {
		const capturedAt = iso();
		try {
			if (config.surface === "desk" && win.frappe) config.user = deskUser(win.frappe) || config.user;
			const snap = {
				schema: 1,
				captured_at: capturedAt,
				surface: config.surface,
				page: collectPage(win, config.surface),
				routes: rings.routes,
				console: rings.console,
				requests: rings.requests,
				app: collectApp(),
				device: collectDevice(win),
			};
			// A round trip, so the caller gets a copy that later recording cannot change, and
			// nothing that would not survive the trip to the server anyway.
			return JSON.parse(JSON.stringify(snap, dropReserved));
		} catch (e) {
			return { schema: 1, captured_at: capturedAt, surface: config.surface, page: null, routes: [], console: [], requests: [], app: {}, device: {} };
		}
	}

	/**
	 * Load the panel once. The Desk resolves the hashed bundle name with
	 * `frappe.assets.bundled_asset`; a web page cannot, so its template gives the resolved URL
	 * in `EE_CAPTURE.panel_url`. Both then inject a <script>.
	 *
	 * Not `frappe.require` on the Desk, although it would resolve the name itself: v16 resolves
	 * it even when the script fails and records the path as executed either way, so after one
	 * failed load every later click would "succeed" without fetching anything. It also freezes
	 * the whole Desk while it loads. It stays only as the fallback when the name cannot be
	 * resolved. A failed load removes its <script> and clears `loading`, so the next click tries
	 * again.
	 */
	function loadPanel() {
		const ready = () => win.ee_capture_panel && typeof win.ee_capture_panel.open === "function";
		if (ready()) return Promise.resolve();
		if (loading) return loading;
		loading = new Promise((resolve, reject) => {
			const timer = setTimeout(() => reject(new Error("The report form took too long to load.")), PANEL_TIMEOUT_MS);
			const done = (err) => {
				clearTimeout(timer);
				if (err) reject(err instanceof Error ? err : new Error("The report form could not be loaded."));
				else resolve();
			};
			try {
				const frappe = win.frappe;
				let url = config.panel_url || "";
				if (config.surface === "desk" && !url) {
					const name = "capture_panel.bundle.js";
					let resolved = "";
					try {
						resolved = frappe && frappe.assets && typeof frappe.assets.bundled_asset === "function" ? frappe.assets.bundled_asset(name) : "";
					} catch (e) {
						resolved = "";
					}
					if (resolved && resolved !== name) {
						url = resolved;
					} else if (frappe && typeof frappe.require === "function") {
						const pending = frappe.require(name, () => done());
						if (pending && typeof pending.then === "function") pending.then(() => done(), done);
						return;
					}
				}
				if (!url) return done(new Error("The report form is not available on this page."));
				const doc = win.document;
				const script = doc.createElement("script");
				script.src = url;
				script.async = true;
				script.onload = () => done();
				script.onerror = () => {
					try {
						script.remove();
					} catch (e) {
						// A stray failed <script> costs nothing; the next try adds a fresh one.
					}
					done(new Error("The report form could not be loaded."));
				};
				(doc.head || doc.documentElement).appendChild(script);
			} catch (e) {
				done(e);
			}
		}).then(
			() => {
				loading = null;
				if (!ready()) throw new Error("The report form did not load.");
			},
			(err) => {
				loading = null;
				throw err;
			}
		);
		return loading;
	}

	const api = {
		installed: true,
		config,
		registerCaptureState(fn) {
			if (typeof fn !== "function") return () => {};
			states.push(fn);
			return () => {
				const i = states.indexOf(fn);
				if (i !== -1) states.splice(i, 1);
			};
		},
		snapshot,
		open(opts) {
			try {
				// Taken before the panel loads, so the panel's own requests are not in it.
				const snap = snapshot();
				return loadPanel().then(() => win.ee_capture_panel.open(snap, opts || {}));
			} catch (e) {
				return Promise.reject(e);
			}
		},
		/**
		 * Whether the report panel is open, or is still removing its history entry. Pages with
		 * history of their own ignore `popstate` while this is true, because the panel handles it
		 * (panel.js, `isPanelOpen`). False until the panel has loaded, and false if asking fails.
		 */
		isOpen() {
			try {
				const panel = win.ee_capture_panel;
				return !!(panel && typeof panel.isOpen === "function" && panel.isOpen());
			} catch (e) {
				return false;
			}
		},
	};

	try {
		win.ee_capture = api;
	} catch (e) {
		return null;
	}
	for (const step of [wrapFetch, wrapXhr, wrapConsole, listenForErrors, watchRoutes]) {
		try {
			step();
		} catch (e) {
			// Each piece stands alone: a browser without one API still gets the others.
		}
	}
	if (config.surface === "desk") installFormBaseline(win);
	return api;
}

if (typeof window !== "undefined") {
	try {
		install(window);
	} catch (e) {
		// The recorder is optional. The page is not.
	}
}
