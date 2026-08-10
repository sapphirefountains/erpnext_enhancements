/**
 * DOM construction helpers. **There is no `innerHTML` in this application.**
 *
 * Not "no `innerHTML` with user data" — none at all, so the rule needs no judgement call at
 * the call site and `scripts/test_chat_no_innerhtml.js` can enforce it as a plain source
 * scan. Message bodies, sender names, room titles, filenames and citation labels are all
 * user-authored, and the path from any employee to every employee runs through exactly one
 * careless template literal.
 *
 * `el()` takes text as text. If you find yourself wanting markup, build the children.
 */

/**
 * Create an element.
 * @param {string} tag
 * @param {object} [attrs] `class`, `text`, `aria-*`, `data-*`, `on*` handlers, anything else
 *        becomes a plain attribute.
 * @param {Array<Node|string>} [children]
 */
export function el(tag, attrs, children) {
	const node = document.createElement(tag);
	const a = attrs || {};
	for (const key of Object.keys(a)) {
		const value = a[key];
		if (value == null || value === false) continue;
		if (key === "text") {
			node.textContent = String(value);
		} else if (key === "class") {
			node.className = String(value);
		} else if (key === "html") {
			throw new Error("ee-chat: dom.el() does not accept html. Build the children.");
		} else if (key.startsWith("on") && typeof value === "function") {
			node.addEventListener(key.slice(2).toLowerCase(), value);
		} else if (key === "dataset") {
			Object.assign(node.dataset, value);
		} else {
			node.setAttribute(key, value === true ? "" : String(value));
		}
	}
	for (const child of children || []) {
		if (child == null) continue;
		node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
	}
	return node;
}

/** Empty a node without `innerHTML = ""` (which is still an HTML parse in some engines). */
export function clear(node) {
	while (node && node.firstChild) node.removeChild(node.firstChild);
	return node;
}

/** Replace a node's children in one go. */
export function fill(node, children) {
	clear(node);
	for (const child of children || []) {
		if (child == null) continue;
		node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
	}
	return node;
}

/**
 * Linkify bare URLs in a text run, as DOM.
 *
 * Coworker message bodies are plain text — the Google Chat leg is text-only under user auth,
 * so rendering them as markdown would make ERPNext show formatting the native client does
 * not, for the same message. Linkifying URLs is the one affordance worth the exception, and
 * it is done by building anchors rather than by rewriting the string.
 *
 * Only `http`/`https`. A `javascript:` scheme is not a link, and neither is anything else a
 * message body can contain.
 */
export function linkify(text) {
	const nodes = [];
	const source = String(text == null ? "" : text);
	const re = /\bhttps?:\/\/[^\s<>"')\]]+/gi;
	let last = 0;
	let match;
	while ((match = re.exec(source)) !== null) {
		if (match.index > last) nodes.push(document.createTextNode(source.slice(last, match.index)));
		// Trailing sentence punctuation is almost never part of the URL.
		let url = match[0];
		const trailing = url.match(/[.,;:!?]+$/);
		if (trailing) url = url.slice(0, -trailing[0].length);
		nodes.push(
			el("a", {
				class: "ee-chat-link",
				href: url,
				target: "_blank",
				rel: "noopener noreferrer",
				text: url,
			})
		);
		if (trailing) nodes.push(document.createTextNode(trailing[0]));
		last = match.index + match[0].length;
	}
	if (last < source.length) nodes.push(document.createTextNode(source.slice(last)));
	return nodes;
}

/** Relative time, short form. No library: five branches beats 40 KB on every page load. */
export function relativeTime(iso, now) {
	if (!iso) return "";
	const then = Date.parse(String(iso).replace(" ", "T"));
	if (Number.isNaN(then)) return "";
	const seconds = Math.floor(((now || Date.now()) - then) / 1000);
	if (seconds < 45) return "now";
	if (seconds < 3600) return Math.round(seconds / 60) + "m";
	if (seconds < 86400) return Math.round(seconds / 3600) + "h";
	if (seconds < 604800) return Math.round(seconds / 86400) + "d";
	return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Absolute clock time for the hover/focus timestamp. */
export function clockTime(iso) {
	if (!iso) return "";
	const then = Date.parse(String(iso).replace(" ", "T"));
	if (Number.isNaN(then)) return "";
	return new Date(then).toLocaleString();
}

/** The day separator label. */
export function dayLabel(iso) {
	if (!iso) return "";
	const then = new Date(Date.parse(String(iso).replace(" ", "T")));
	if (Number.isNaN(then.getTime())) return "";
	const today = new Date();
	const sameDay = (a, b) =>
		a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
	if (sameDay(then, today)) return "Today";
	const yesterday = new Date(today.getTime() - 86400000);
	if (sameDay(then, yesterday)) return "Yesterday";
	return then.toLocaleDateString(undefined, {
		weekday: "short",
		month: "short",
		day: "numeric",
		year: then.getFullYear() === today.getFullYear() ? undefined : "numeric",
	});
}

/** Whether two messages should be visually grouped (same sender, close in time). */
export function shouldGroup(previous, current, windowMs = 5 * 60 * 1000) {
	if (!previous || !current) return false;
	if ((previous.sender || previous.sender_email) !== (current.sender || current.sender_email)) return false;
	if (previous.sender_kind !== current.sender_kind) return false;
	if (previous.is_deleted || current.is_deleted) return false;
	const a = Date.parse(String(previous.creation || "").replace(" ", "T"));
	const b = Date.parse(String(current.creation || "").replace(" ", "T"));
	if (Number.isNaN(a) || Number.isNaN(b)) return false;
	return b - a <= windowMs;
}

/** Initials for an avatar with no image. Two characters, never more. */
export function initials(name) {
	const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
	if (!parts.length) return "?";
	if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
	return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Human file size. */
export function fileSize(bytes) {
	const n = Number(bytes) || 0;
	if (n < 1024) return n + " B";
	if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
	return (n / (1024 * 1024)).toFixed(1) + " MB";
}
