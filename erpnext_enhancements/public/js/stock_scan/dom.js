/**
 * Node builders. **Nothing under `public/js/stock_scan/` assigns markup as a string, and
 * nothing may.**
 *
 * Item names, descriptions, supplier names, job names and error sentences on this page are
 * data somebody typed into ERPNext, and a scan code is whatever a sticker says. Every one of
 * them goes into the page as `textContent`; there is no string-to-markup path to get wrong.
 * `tests/test_stock_scan_surface.py` fails the build on the markup-assigning APIs.
 *
 * Modelled on `public/js/feedback/dom.js` and `public/js/marketing/dom.js`.
 */

import { initials, isSafeImage } from "./logic.js";

/** `el("div", "cls", "text")`: the workhorse. Text always goes in as `textContent`. */
export function el(tag, className, text) {
	const node = document.createElement(tag);
	if (className) node.className = className;
	if (text !== undefined && text !== null && text !== "") node.textContent = String(text);
	return node;
}

export function clear(node) {
	while (node && node.firstChild) node.removeChild(node.firstChild);
	return node;
}

export function append(parent, ...children) {
	for (const child of children) {
		if (child === null || child === undefined || child === false) continue;
		parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
	}
	return parent;
}

/** Replace a node's children: `clear` then `append`. */
export function fill(parent, ...children) {
	return append(clear(parent), ...children);
}

/**
 * A button. `label` is text, or an array of nodes/strings (a glyph plus words). Every button
 * is `type="button"`: there is no form on this page to submit by accident.
 */
export function button(label, className, onClick) {
	const node = el("button", className || "ee-ss-btn");
	node.type = "button";
	if (Array.isArray(label)) append(node, ...label);
	else if (label !== undefined && label !== null) node.textContent = String(label);
	if (onClick) node.addEventListener("click", onClick);
	return node;
}

/** A glyph that carries meaning next to a word, hidden from screen readers (the word says it). */
export function glyph(char, className) {
	const node = el("span", className ? `ee-ss-glyph ${className}` : "ee-ss-glyph", char);
	node.setAttribute("aria-hidden", "true");
	return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";

// Stroke paths on a 24-unit grid. Drawn rather than typed: symbol characters such as a QR
// mark render as empty boxes in some Android system fonts.
const ICONS = {
	scan: ["M4 8V5a1 1 0 0 1 1-1h3", "M16 4h3a1 1 0 0 1 1 1v3", "M20 16v3a1 1 0 0 1-1 1h-3", "M8 20H5a1 1 0 0 1-1-1v-3", "M7 12h10"],
	search: ["M10.5 4a6.5 6.5 0 1 1 0 13a6.5 6.5 0 1 1 0-13z", "M15.5 15.5L20 20"],
	back: ["M15 5l-7 7 7 7"],
	next: ["M9 5l7 7-7 7"],
	job: ["M4 8h16v11H4z", "M9 8V5h6v3", "M4 13h16"],
	pin: ["M12 21s-6.5-5.8-6.5-11a6.5 6.5 0 0 1 13 0c0 5.2-6.5 11-6.5 11z", "M12 7.5a2.5 2.5 0 1 1 0 5a2.5 2.5 0 1 1 0-5z"],
	move: ["M12 3v12", "M7 10l5 5 5-5", "M5 20h14"],
	undo: ["M9 14L4 9l5-5", "M4 9h10.5a5.5 5.5 0 0 1 0 11H11"],
	bolt: ["M13 3L5 14h6l-1 7 8-11h-6z"],
	close: ["M6 6l12 12", "M18 6L6 18"],
	// "Report a problem": a speech bubble, not a flag — Recent's "Review" pill already uses ⚑.
	report: ["M4 5h16v11H10l-4 4v-4H4z", "M12 8v3.5", "M12 13.5v.01"],
	// The receipt photo on a store run.
	camera: ["M4 8h3.5l1.5-2.5h6L16.5 8H20v11H4z", "M12 10.5a3 3 0 1 1 0 6a3 3 0 1 1 0-6z"],
};

/** A small line icon, `aria-hidden` (the words beside it carry the meaning). */
export function icon(name, className) {
	const svg = document.createElementNS(SVG_NS, "svg");
	svg.setAttribute("viewBox", "0 0 24 24");
	svg.setAttribute("class", className ? `ee-ss-icon ${className}` : "ee-ss-icon");
	svg.setAttribute("aria-hidden", "true");
	svg.setAttribute("focusable", "false");
	for (const d of ICONS[name] || []) {
		const path = document.createElementNS(SVG_NS, "path");
		path.setAttribute("d", d);
		svg.appendChild(path);
	}
	return svg;
}

/**
 * A text input at 16 px or more (iOS zooms the page into anything smaller).
 * `opts`: `{placeholder, value, inputmode, enterkeyhint, label, autocomplete}`.
 */
export function input(opts) {
	const o = opts || {};
	const node = document.createElement("input");
	node.type = o.type || "text";
	node.className = o.className || "ee-ss-input";
	node.autocomplete = o.autocomplete || "off";
	node.setAttribute("autocapitalize", "off");
	node.setAttribute("autocorrect", "off");
	node.spellcheck = false;
	if (o.placeholder) node.placeholder = o.placeholder;
	if (o.value !== undefined && o.value !== null) node.value = String(o.value);
	if (o.inputmode) node.setAttribute("inputmode", o.inputmode);
	if (o.enterkeyhint) node.setAttribute("enterkeyhint", o.enterkeyhint);
	if (o.label) node.setAttribute("aria-label", o.label);
	// A file input (the receipt photo): what it accepts, and — only when asked — `capture`,
	// which on a phone skips the photo library and goes straight to the camera.
	if (o.accept) node.setAttribute("accept", o.accept);
	if (o.capture) node.setAttribute("capture", o.capture);
	if (o.maxlength) node.setAttribute("maxlength", String(o.maxlength));
	return node;
}

/**
 * A `<select>` from `[value, label]` pairs (or plain strings), at 16 px like every input.
 * Option text is `textContent`: group and unit names are data.
 */
export function select(options, value, label) {
	const node = document.createElement("select");
	node.className = "ee-ss-input ee-ss-select";
	if (label) node.setAttribute("aria-label", label);
	for (const entry of options || []) {
		const [v, text] = Array.isArray(entry) ? entry : [entry, entry];
		const opt = document.createElement("option");
		opt.value = String(v);
		opt.textContent = String(text);
		if (String(v) === String(value)) opt.selected = true;
		node.appendChild(opt);
	}
	if (value !== undefined && value !== null) node.value = String(value);
	return node;
}

/**
 * The receipt photo made small enough to send on a warehouse's signal: the long edge at most
 * `maxEdge` px, re-encoded as JPEG. A phone photo is 3-12 MB; this is a few hundred KB and
 * still reads. Resolves the original file whenever anything fails (no canvas, a format the
 * browser cannot decode) — the upload then just takes longer. The browser applies the photo's
 * EXIF rotation when it draws it, so the receipt stays upright.
 */
export function shrinkPhoto(file, maxEdge = 1600, quality = 0.8) {
	return new Promise((resolve) => {
		let url = null;
		const done = (out) => {
			if (url) {
				try {
					URL.revokeObjectURL(url);
				} catch (e) {
					/* nothing to free */
				}
			}
			resolve(out);
		};
		try {
			if (!file || typeof document === "undefined" || typeof URL === "undefined" || typeof Image === "undefined") {
				done(file);
				return;
			}
			url = URL.createObjectURL(file);
			const image = new Image();
			image.onload = () => {
				try {
					const w0 = image.naturalWidth || image.width;
					const h0 = image.naturalHeight || image.height;
					if (!w0 || !h0) return done(file);
					const scale = Math.min(1, maxEdge / Math.max(w0, h0));
					const canvas = document.createElement("canvas");
					canvas.width = Math.max(1, Math.round(w0 * scale));
					canvas.height = Math.max(1, Math.round(h0 * scale));
					const ctx = canvas.getContext && canvas.getContext("2d");
					if (!ctx || !canvas.toBlob) return done(file);
					ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
					canvas.toBlob(
						(blob) => {
							if (!blob || !blob.size) return done(file);
							try {
								done(new File([blob], "receipt.jpg", { type: "image/jpeg" }));
							} catch (e) {
								done(blob);
							}
						},
						"image/jpeg",
						quality
					);
				} catch (e) {
					done(file);
				}
			};
			image.onerror = () => done(file);
			image.src = url;
		} catch (e) {
			done(file);
		}
	});
}

/**
 * An image, only from a source `isSafeImage` accepts; `null` otherwise so the caller draws
 * the monogram. A broken image removes itself for the same reason.
 */
export function img(src, className, onError) {
	if (!isSafeImage(src)) return null;
	const node = document.createElement("img");
	node.className = className || "";
	node.alt = "";
	node.loading = "lazy";
	node.decoding = "async";
	// Item pictures hosted elsewhere learn nothing about which location page asked for them.
	node.referrerPolicy = "no-referrer";
	node.src = src;
	if (onError) node.addEventListener("error", onError, { once: true });
	return node;
}

/** A square picture of an item: its image when it has a safe one, else its initials. */
export function thumb(item, size) {
	const box = el("span", `ee-ss-thumb${size === "lg" ? " is-lg" : ""}`);
	box.setAttribute("aria-hidden", "true");
	const name = (item && (item.item_name || item.item_code)) || "";
	const monogram = () => fill(box, el("span", "ee-ss-monogram", initials(name)));
	const picture = img(item && item.image, "ee-ss-thumb-img", monogram);
	if (picture) box.appendChild(picture);
	else monogram();
	return box;
}
