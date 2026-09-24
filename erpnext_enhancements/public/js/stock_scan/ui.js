/**
 * The page's interruptions: bottom sheets, toasts, a busy button, and the theme switch.
 *
 * Ported from the kiosk's `public/js/kiosk/ui.js` (the best mobile primitives in this app)
 * as an ES module built on `dom.js`: no global side effects at import, no `window.KioskUI`.
 *
 * SHEETS are `role="dialog"` panels over a backdrop. Escape and a backdrop tap close one
 * (unless `dismissible: false`), Tab is trapped inside it, focus returns to whatever opened
 * it, and sheets stack — only the top one owns the keyboard.
 *
 * Their host is appended to `<body>` so a sheet escapes the app's stacking and scrolling, and
 * that puts it OUTSIDE `#ee-stock-scan-root`. The host therefore carries the `ee-ss-root`
 * class: every color token and heading rule is scoped to it, and without it a sheet's `<h2>`
 * keeps Frappe's dark website heading color on a dark sheet — the kiosk shipped exactly that
 * (v1.483.3). The toast region carries the class for the same reason.
 */

import { append, button, clear, el, glyph } from "./dom.js";

const FOCUSABLE =
	'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const stack = [];
const onClear = [];
const onChange = [];
let keyListening = false;

/**
 * `fn(opened)` whenever a sheet opens (true) or closes (false). app.js keeps the phone's Back in
 * step with them (nav.js): Back closes the open sheets (all of them, as one marker covers the
 * stack) instead of leaving the screen.
 */
export function onSheetChange(fn) {
	onChange.push(fn);
}

function tell(opened) {
	for (const fn of onChange.slice()) {
		try {
			fn(opened);
		} catch (e) {
			/* the sheet still opens and closes */
		}
	}
}

export function reducedMotion() {
	try {
		return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
	} catch (e) {
		return false;
	}
}

/** A short buzz on phones that have one. iPhones ignore it; nothing depends on it. */
export function buzz(pattern) {
	try {
		if (navigator.vibrate) navigator.vibrate(pattern || 30);
	} catch (e) {
		/* vibration is a nicety */
	}
}

function sheetHost() {
	let host = document.getElementById("ee-ss-sheets");
	if (!host) {
		host = el("div", "ee-ss-sheet-host ee-ss-root");
		host.id = "ee-ss-sheets";
		document.body.appendChild(host);
	}
	return host;
}

function focusables(node) {
	return Array.prototype.slice
		.call(node.querySelectorAll(FOCUSABLE))
		.filter((n) => n.offsetParent !== null || n === document.activeElement);
}

/**
 * Is `target` something that is not this page's? The page, its sheets and its toasts all sit
 * inside an `.ee-ss-root`; `<body>` is where focus falls when nothing has it, and counts as the
 * page's. Anything else is another layer over the page — the report form mounts on `<body>` —
 * and a key aimed there is not a sheet's or the camera's to act on: Escape would close a sheet
 * under the form, Tab would pull focus into it, and the camera would move every letter typed
 * into the form into its own code box. app.js opens no sheet under the form; this is the
 * backstop.
 */
export function notOurs(target) {
	if (!target || target === document.body || target === document.documentElement) return false;
	return typeof target.closest === "function" && !target.closest(".ee-ss-root");
}

function onKeydown(ev) {
	const top = stack[stack.length - 1];
	if (!top || notOurs(ev.target)) return;
	if (ev.key === "Escape" || ev.key === "Esc") {
		if (top.dismissible) {
			ev.preventDefault();
			top.close("dismiss");
		}
		return;
	}
	if (ev.key !== "Tab") return;
	const list = focusables(top.el);
	if (!list.length) {
		ev.preventDefault();
		return;
	}
	const first = list[0];
	const last = list[list.length - 1];
	if (ev.shiftKey && (document.activeElement === first || !top.el.contains(document.activeElement))) {
		ev.preventDefault();
		last.focus();
	} else if (!ev.shiftKey && (document.activeElement === last || !top.el.contains(document.activeElement))) {
		ev.preventDefault();
		first.focus();
	}
}

export function sheetDepth() {
	return stack.length;
}

/**
 * Run `fn` now if no sheet is open, else as soon as the last one closes. Toasts sit BELOW the
 * sheets (a toast over a sheet would cover its buttons), so a message that must be seen — a
 * save that failed after the person moved on to the camera or a search — waits here instead
 * of expiring unseen behind a full-height sheet.
 */
export function whenNoSheets(fn) {
	if (!stack.length) fn();
	else onClear.push(fn);
}

function flushClear() {
	if (stack.length) return;
	for (const fn of onClear.splice(0)) {
		try {
			fn();
		} catch (e) {
			/* one caller's problem must not swallow the others */
		}
	}
}

/**
 * Open a bottom sheet.
 *
 *   title         heading text
 *   body          a Node, or `function (bodyEl, handle)` that fills it
 *   actions       [{label, kind: "primary"|"take"|"add"|"ghost"|"outline", onClick(handle, btn), close}]
 *                 an action closes the sheet unless `close: false` or its onClick returns false
 *   full          full-height (pickers, the camera)
 *   dismissible   false: no Escape, no backdrop tap, no close button
 *   onClose       (reason) => … — "action" | "dismiss" | "programmatic"
 *   initialFocus  a Node to focus first, or "sheet" for the panel itself (focused
 *                 synchronously, inside the tap that opened the sheet: iOS shows the keyboard
 *                 only for a focus made during a gesture)
 *   className     extra class on the sheet
 *
 * Returns `{el, body, close(reason), setActions(list), setTitle(text)}`.
 */
export function sheet(opts) {
	const o = opts || {};
	if (!keyListening) {
		document.addEventListener("keydown", onKeydown);
		keyListening = true;
	}
	const host = sheetHost();
	const opener = document.activeElement;
	const dismissible = o.dismissible !== false;
	let closed = false;

	const titleId = `ee-ss-sheet-${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
	const heading = el("h2", "ee-ss-sheet-title", o.title || "");
	heading.id = titleId;
	const closeBtn = dismissible ? button("×", "ee-ss-sheet-close", () => handle.close("dismiss")) : null;
	if (closeBtn) closeBtn.setAttribute("aria-label", "Close");
	const head = append(el("div", "ee-ss-sheet-head"), heading, closeBtn);
	const body = el("div", "ee-ss-sheet-body");
	const foot = el("div", "ee-ss-sheet-foot");
	const panel = append(
		el("div", `ee-ss-sheet${o.full ? " is-full" : ""}${o.className ? ` ${o.className}` : ""}`),
		o.full ? null : el("div", "ee-ss-sheet-handle"),
		head,
		body,
		foot
	);
	panel.setAttribute("role", "dialog");
	panel.setAttribute("aria-modal", "true");
	panel.setAttribute("aria-labelledby", titleId);
	panel.tabIndex = -1;
	const backdrop = el("div", "ee-ss-sheet-backdrop");
	const layer = append(el("div", "ee-ss-sheet-layer"), backdrop, panel);

	const handle = {
		el: panel,
		body,
		dismissible,
		close(reason) {
			if (closed) return;
			closed = true;
			const at = stack.indexOf(handle);
			if (at !== -1) {
				stack.splice(at, 1);
				tell(false);
			}
			layer.classList.remove("is-open");
			const done = () => {
				if (layer.parentNode) layer.parentNode.removeChild(layer);
				if (!stack.length) document.body.classList.remove("ee-ss-locked");
			};
			if (reducedMotion()) done();
			else setTimeout(done, 180);
			if (o.onClose) {
				try {
					o.onClose(reason || "programmatic");
				} catch (e) {
					/* the caller's problem; the sheet still closes */
				}
			}
			if (opener && opener.focus && opener !== document.body && document.body.contains(opener)) {
				try {
					opener.focus({ preventScroll: true });
				} catch (e) {
					/* focus is best effort */
				}
			}
			flushClear();
		},
		setActions(list) {
			clear(foot);
			for (const action of list || []) {
				const btn = button(
					action.glyph ? [glyph(action.glyph), el("span", null, action.label)] : action.label,
					`ee-ss-btn ee-ss-btn-${action.kind || "outline"}${action.large ? " is-lg" : ""}`,
					() => {
						const result = action.onClick ? action.onClick(handle, btn) : undefined;
						if (action.close !== false && result !== false) handle.close("action");
					}
				);
				if (action.disabled) btn.disabled = true;
				foot.appendChild(btn);
			}
		},
		setTitle(text) {
			heading.textContent = text || "";
		},
	};

	if (typeof o.body === "function") o.body(body, handle);
	else if (o.body) body.appendChild(o.body);
	handle.setActions(o.actions);

	backdrop.addEventListener("click", () => {
		if (dismissible) handle.close("dismiss");
	});

	host.appendChild(layer);
	stack.push(handle);
	tell(true);
	document.body.classList.add("ee-ss-locked");

	// Focus now, inside the tap, so iOS raises the keyboard for an input; then animate in.
	// "sheet" focuses the panel itself: for a sheet whose first control is an input that must
	// NOT raise the keyboard (the camera, the job list).
	let target = o.initialFocus === "sheet" ? panel : o.initialFocus || null;
	if (!target) {
		const list = focusables(body).concat(focusables(foot));
		target = list.length ? list[0] : closeBtn || panel;
	}
	try {
		target.focus({ preventScroll: true });
	} catch (e) {
		/* focus is best effort */
	}
	// Read a layout value so the off-screen start is computed, then open: the slide-in runs
	// without waiting for an animation frame, which a backgrounded tab may never deliver.
	void panel.offsetHeight;
	layer.classList.add("is-open");
	return handle;
}

/** Close every open sheet (a scan result replaces whatever was being asked). */
export function closeAllSheets() {
	stack.slice().reverse().forEach((s) => s.close("programmatic"));
}

/**
 * A yes/no sheet — the page's `confirm()`. Resolves `true` for the yes button, `false` for
 * anything else. `opts`: `{title, body (text or Node), ok, okKind, okGlyph, cancel}`.
 */
export function ask(opts) {
	const o = opts || {};
	return new Promise((resolve) => {
		let settled = false;
		const finish = (value) => {
			if (!settled) {
				settled = true;
				resolve(value);
			}
		};
		sheet({
			title: o.title || "Are you sure?",
			body: typeof o.body === "string" ? el("p", "ee-ss-sheet-text", o.body) : o.body,
			onClose: () => finish(false),
			actions: [
				{ label: o.ok || "OK", kind: o.okKind || "primary", glyph: o.okGlyph, large: true, onClick: () => finish(true) },
				{ label: o.cancel || "Cancel", kind: "ghost", onClick: () => finish(false) },
			],
		});
	});
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

const MAX_TOASTS = 2;

/**
 * Create the one persistent live region. It must exist before the first message, or a
 * screen reader never announces it; `app.js` calls this on mount.
 */
export function mountToasts() {
	let region = document.getElementById("ee-ss-toasts");
	if (!region) {
		region = el("div", "ee-ss-toasts ee-ss-root");
		region.id = "ee-ss-toasts";
		region.setAttribute("aria-live", "polite");
		region.setAttribute("aria-atomic", "false");
		document.body.appendChild(region);
	}
	return region;
}

/**
 * Show a message above the bottom bar. `opts`: `{actionLabel, onAction, kind: "ok"|"error"|"info", ms}`.
 * Returns `{close}`. At most two at once: on a phone a third would cover the page.
 */
export function toast(message, opts) {
	const o = opts || {};
	const region = mountToasts();
	const kind = o.kind || "info";
	const box = el("div", `ee-ss-toast is-${kind}`);
	const mark = { ok: "✓", error: "!", info: "i" }[kind] || "i";
	append(box, glyph(mark, "ee-ss-toast-mark"), el("span", "ee-ss-toast-text", message));
	let timer = null;
	const close = () => {
		if (timer) clearTimeout(timer);
		timer = null;
		if (!box.parentNode) return;
		box.classList.add("is-leaving");
		setTimeout(() => {
			if (box.parentNode) box.parentNode.removeChild(box);
		}, reducedMotion() ? 0 : 160);
	};
	if (o.actionLabel && o.onAction) {
		append(
			box,
			button(o.actionLabel, "ee-ss-toast-action", () => {
				close();
				o.onAction();
			})
		);
	}
	while (region.children.length >= MAX_TOASTS) region.removeChild(region.firstChild);
	region.appendChild(box);
	// Long enough to reach Undo with a hand full of parts; errors linger longer still.
	const ms = o.ms || (kind === "error" ? 8000 : o.actionLabel ? 7000 : 4000);
	timer = setTimeout(close, ms);
	return { close };
}

// ---------------------------------------------------------------------------
// Busy buttons
// ---------------------------------------------------------------------------

const saved = new WeakMap();

/** Disable a button and show `label` ("Saving…") while work runs; `on=false` puts it back. */
export function busy(btn, on, label) {
	if (!btn) return;
	if (on) {
		if (!saved.has(btn)) saved.set(btn, { nodes: Array.prototype.slice.call(btn.childNodes), disabled: btn.disabled });
		btn.disabled = true;
		btn.setAttribute("aria-busy", "true");
		btn.classList.add("is-busy");
		clear(btn);
		append(btn, el("span", "ee-ss-spinner"), el("span", null, label || "Working…"));
		btn.firstChild.setAttribute("aria-hidden", "true");
	} else {
		const before = saved.get(btn);
		btn.removeAttribute("aria-busy");
		btn.classList.remove("is-busy");
		if (before) {
			clear(btn);
			append(btn, ...before.nodes);
			btn.disabled = before.disabled;
			saved.delete(btn);
		} else {
			btn.disabled = false;
		}
	}
}

// ---------------------------------------------------------------------------
// Theme: system / light / dark, like the kiosk
// ---------------------------------------------------------------------------

export const THEME_KEY = "ee_ss_theme";
export const THEME_MODES = ["system", "light", "dark"];

export function readTheme() {
	try {
		const value = window.localStorage.getItem(THEME_KEY);
		return THEME_MODES.indexOf(value) === -1 ? "system" : value;
	} catch (e) {
		return "system";
	}
}

/**
 * Apply and remember a theme. `<html data-theme>` absent means "follow the phone"; the
 * inline script in `stock-scan.html` applies the stored choice before the stylesheet parses.
 */
export function setTheme(mode) {
	const value = THEME_MODES.indexOf(mode) === -1 ? "system" : mode;
	try {
		if (value === "system") window.localStorage.removeItem(THEME_KEY);
		else window.localStorage.setItem(THEME_KEY, value);
	} catch (e) {
		/* storage blocked: the choice still applies until the page reloads */
	}
	const root = document.documentElement;
	if (value === "system") root.removeAttribute("data-theme");
	else root.setAttribute("data-theme", value);
	syncThemeMeta();
	return value;
}

/** Keep `<meta name="theme-color">` (the phone's status bar) equal to the page ground. */
export function syncThemeMeta() {
	const meta = document.querySelector('meta[name="theme-color"]');
	if (!meta) return;
	let bg = "";
	try {
		bg = getComputedStyle(document.documentElement).getPropertyValue("--ss-bg").trim();
	} catch (e) {
		bg = "";
	}
	if (bg) meta.setAttribute("content", bg);
}
