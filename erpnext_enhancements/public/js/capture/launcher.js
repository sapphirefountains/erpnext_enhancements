/**
 * The floating "Report a problem" button for allowlisted web pages (WI-079 slice 2).
 *
 * The Desk does not get one: it has the Help menu item. The kiosk does not either: it has a row
 * in its Settings tab, and a floating button on a shared touchscreen would be pressed by accident
 * all day. So this shows only when the template asked for it (`EE_CAPTURE.launcher === true`),
 * the surface is a plain web page, and the `system_user=yes` cookie Frappe sets at login says
 * the viewer is a System User. The server checks that again on submit; the cookie only decides
 * whether a button that cannot succeed is worth drawing.
 *
 * Small, bottom-left, and a real <button>, so it is reachable by Tab and works with Enter and
 * Space. Bottom-left because bottom-right is where page chrome already lives: chat bubbles,
 * toasts, "next" buttons.
 */

const BUTTON_ID = "ee-cap-launcher";
const STYLE_ID = "ee-cap-launcher-style";
const LABEL = "Report a problem";

const CSS = `
.ee-cap-launcher{position:fixed;left:max(12px,env(safe-area-inset-left));bottom:max(12px,env(safe-area-inset-bottom));z-index:1025;
min-height:32px;padding:6px 12px;border:1px solid rgba(255,255,255,.25);border-radius:999px;background:rgba(17,24,39,.86);
color:#fff;font:500 12px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.2);
cursor:pointer;opacity:.85}
.ee-cap-launcher:hover,.ee-cap-launcher:focus-visible{opacity:1}
.ee-cap-launcher:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}
.ee-cap-launcher[aria-busy="true"]{cursor:progress;opacity:.6}
@media print{.ee-cap-launcher{display:none}}
`;

/**
 * Should this page get the button? Pure, so node can test it.
 *
 * `path` is checked as well as the surface: if a kiosk template ever forgot to say
 * `surface: "kiosk"`, the button still stays off the touchscreen.
 */
export function shouldShowLauncher(boot, cookie, path) {
	if (!boot || typeof boot !== "object" || boot.launcher !== true) return false;
	if (boot.surface && boot.surface !== "web") return false;
	if (/^\/kiosk(\/|$)/.test(String(path || ""))) return false;
	return /(?:^|;\s*)system_user=yes(?:;|$)/.test(String(cookie || ""));
}

function attach(win) {
	const doc = win.document;
	if (doc.getElementById(BUTTON_ID)) return;

	if (!doc.getElementById(STYLE_ID)) {
		const style = doc.createElement("style");
		style.id = STYLE_ID;
		style.textContent = CSS;
		(doc.head || doc.documentElement).appendChild(style);
	}

	const button = doc.createElement("button");
	button.type = "button";
	button.id = BUTTON_ID;
	button.className = "ee-cap-launcher";
	button.textContent = LABEL;
	button.setAttribute("aria-haspopup", "dialog");

	let busy = false;
	let resetTimer = null;
	button.addEventListener("click", () => {
		if (busy) return;
		const capture = win.ee_capture;
		if (!capture || typeof capture.open !== "function") return;
		busy = true;
		clearTimeout(resetTimer);
		button.textContent = LABEL;
		// aria-busy rather than `disabled`: a disabled button drops keyboard focus, and a keyboard
		// user would lose their place on the page.
		button.setAttribute("aria-busy", "true");
		Promise.resolve()
			.then(() => capture.open({ source: "launcher" }))
			.catch(() => {
				button.textContent = "Could not open. Try again";
				resetTimer = setTimeout(() => {
					button.textContent = LABEL;
				}, 4000);
			})
			.then(() => {
				busy = false;
				button.removeAttribute("aria-busy");
			});
	});

	doc.body.appendChild(button);
}

/** Draw the button if this page should have one. Never throws. */
export function mountLauncher(win) {
	try {
		if (!win || !win.document) return false;
		const doc = win.document;
		if (!shouldShowLauncher(win.EE_CAPTURE, doc.cookie, win.location && win.location.pathname)) return false;
		// No recorder, no button: one that does nothing when pressed is worse than none.
		if (!win.ee_capture || typeof win.ee_capture.open !== "function") return false;
		const run = () => {
			try {
				attach(win);
			} catch (e) {
				// No button, and the page is untouched.
			}
		};
		if (doc.body) run();
		else doc.addEventListener("DOMContentLoaded", run, { once: true });
		return true;
	} catch (e) {
		return false;
	}
}
