/*
 * scanner_kit: what the kit reads off the page, gathered once from the window it was installed on.
 *
 * Everything is read through `win` at the moment it is used (frappe, jQuery, the history, the
 * BarcodeDetector, a jsQR that arrives later), never captured when the bundle loads: the bundle can
 * arrive before frappe has finished booting, and jsQR is loaded only on first need. Binding to the
 * window passed in, rather than to whatever `window` means at call time, lets a test install one
 * copy of the kit per fake Desk and keep each copy on its own Desk.
 */

const NAMES = ["frappe", "$", "document", "navigator", "history"];

export function make_env(win) {
	const env = {
		win,
		set_timeout: (fn, ms) => win.setTimeout(fn, ms),
		clear_timeout: (id) => win.clearTimeout(id),
		// The window's own events (pagehide). Guarded: not every host has them.
		listen(type, fn) {
			if (typeof win.addEventListener === "function") win.addEventListener(type, fn);
		},
		unlisten(type, fn) {
			if (typeof win.removeEventListener === "function") win.removeEventListener(type, fn);
		},
	};
	NAMES.forEach((name) => Object.defineProperty(env, name, { get: () => win[name], enumerable: true }));
	return env;
}
