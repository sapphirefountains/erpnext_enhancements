/**
 * Node builders. **Nothing in this file assigns `innerHTML`, and nothing may.**
 *
 * This page renders text employees wrote — titles, descriptions, rejection reasons, and a
 * model's prose about all three. Every one of those reaches a reviewer's browser, so a single
 * `innerHTML` here is a stored-XSS path from any employee to every reviewer. `textContent`
 * throughout; the one place markup is unavoidable (`richText`, for the HTML bodies the Text
 * Editor produces) sanitises to a small tag allowlist first.
 *
 * Modelled on `public/js/chat/dom.js`.
 */

/** `el("div", "cls", "text")` — the workhorse. */
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
		if (child) parent.appendChild(child);
	}
	return parent;
}

export function button(label, className, onClick) {
	const node = el("button", className || "ee-fb-btn", label);
	node.type = "button";
	if (onClick) node.addEventListener("click", onClick);
	return node;
}

/** A labelled form control. Returns `{row, input}` so the caller keeps the input handle. */
export function field(labelText, input, help) {
	const row = el("label", "ee-fb-field");
	append(row, el("span", "ee-fb-field-label", labelText), input);
	if (help) append(row, el("span", "ee-fb-field-help", help));
	return { row, input };
}

export function input(type, placeholder, value) {
	const node = document.createElement("input");
	node.type = type || "text";
	if (placeholder) node.placeholder = placeholder;
	if (value !== undefined && value !== null) node.value = value;
	node.className = "ee-fb-input";
	return node;
}

export function textarea(placeholder, value, rows) {
	const node = document.createElement("textarea");
	if (placeholder) node.placeholder = placeholder;
	node.value = value || "";
	node.rows = rows || 6;
	node.className = "ee-fb-input ee-fb-textarea";
	return node;
}

export function select(options, value) {
	const node = document.createElement("select");
	node.className = "ee-fb-input";
	for (const option of options) {
		const item = document.createElement("option");
		const isPair = option && typeof option === "object";
		item.value = isPair ? option.value : option;
		item.textContent = isPair ? option.label : option;
		node.appendChild(item);
	}
	if (value !== undefined && value !== null) node.value = value;
	return node;
}

export function checkbox(checked) {
	const node = document.createElement("input");
	node.type = "checkbox";
	node.className = "ee-fb-check";
	node.checked = !!checked;
	return node;
}

/**
 * A status pill. Carries a **non-colour cue** as well as the colour class: a reader with any
 * colour-vision difference must be able to tell Rejected from Approved, and
 * `prefers-reduced-motion` users lose the only other differentiator this page has. Do not
 * delete the glyph to tidy the markup.
 */
function pill(status, marks, prefix) {
	const slug = String(status || "").toLowerCase().replace(/[^a-z]+/g, "-");
	const node = el("span", `ee-fb-pill ${prefix}-${slug || "unknown"}`);
	append(node, el("span", "ee-fb-pill-mark", marks[status] || "•"), el("span", null, status || "—"));
	return node;
}

/** `Enhancement Request.status`. */
export function statusPill(status) {
	return pill(
		status,
		{
			Submitted: "•",
			Approved: "▸",
			"Breakdown Ready": "◆",
			"Breakdown Failed": "!",
			"Tasks Created": "✓",
			Rejected: "✕",
			Duplicate: "⧉",
		},
		"ee-fb-pill"
	);
}

/**
 * `Task.status`, for the work this request produced.
 *
 * A separate vocabulary from the request's on purpose — these are ERPNext's own Task states,
 * read live off the board. **Spelled `Canceled`, one l**, which is what this site's Select
 * actually offers; the British spelling matches nothing and would render as an unstyled
 * fallback chip rather than an error.
 */
export function taskPill(status) {
	return pill(
		status,
		{
			Open: "•",
			Working: "▸",
			"Pending Review": "◆",
			Overdue: "!",
			Completed: "✓",
			Canceled: "✕",
			Invoiced: "✓",
			Template: "⧉",
		},
		"ee-fb-task"
	);
}

/** An internal link that routes client-side instead of reloading the shell. */
export function link(text, href, onNavigate) {
	const node = el("a", "ee-fb-link", text);
	node.href = href;
	node.addEventListener("click", (ev) => {
		// Let the browser handle modifier-clicks: "open in a new tab" on a request is a
		// reasonable thing to want and hijacking it is the sort of thing people notice.
		if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
		ev.preventDefault();
		onNavigate(href);
	});
	return node;
}

/** An external link to the desk. `rel` set because these open in a new tab. */
export function deskLink(text, path) {
	const node = el("a", "ee-fb-link", text);
	node.href = path;
	node.target = "_blank";
	node.rel = "noopener noreferrer";
	return node;
}

const ALLOWED_TAGS = new Set([
	"P", "BR", "B", "STRONG", "I", "EM", "U", "UL", "OL", "LI", "CODE", "PRE", "H3", "H4", "BLOCKQUOTE",
]);

/**
 * Render Text-Editor HTML into a node, keeping only a small tag allowlist and **no
 * attributes at all**.
 *
 * Attributes are dropped wholesale rather than filtered. That removes `href`, which costs a
 * clickable link inside a description and buys immunity to `javascript:` URLs, `onerror=`,
 * `srcset`, and every future attribute somebody thinks of — a tag allowlist with an attribute
 * filter is a list you have to keep winning, and this one does not need to be won.
 *
 * Parsed with DOMParser rather than assigned: assigning to `innerHTML` on a live node runs
 * `<img onerror>` immediately, before any sanitising can happen. A detached document does not.
 */
export function richText(html) {
	const wrapper = el("div", "ee-fb-rich");
	const source = String(html || "").trim();
	if (!source) return wrapper;

	let parsed;
	try {
		parsed = new DOMParser().parseFromString(source, "text/html");
	} catch (e) {
		wrapper.textContent = source.replace(/<[^>]*>/g, " ");
		return wrapper;
	}

	const copy = (from, to) => {
		for (const child of Array.from(from.childNodes)) {
			if (child.nodeType === 3) {
				to.appendChild(document.createTextNode(child.nodeValue));
			} else if (child.nodeType === 1) {
				if (ALLOWED_TAGS.has(child.tagName)) {
					const clone = document.createElement(child.tagName.toLowerCase());
					copy(child, clone);
					to.appendChild(clone);
				} else {
					// Unwrap rather than drop: the text inside a <div> or a <span> is content,
					// and deleting it loses the description while looking like the model
					// returned nothing.
					copy(child, to);
				}
			}
		}
	};
	copy(parsed.body, wrapper);
	return wrapper;
}

/** "12 Aug" for a date, or "" — for a due date, where relative time reads as noise. */
export function shortDate(value) {
	if (!value) return "";
	const when = new Date(String(value).replace(" ", "T"));
	if (isNaN(when.getTime())) return "";
	return when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "3 minutes ago" / "12 Aug". Absolute past a day, because "6 days ago" needs arithmetic. */
export function relativeTime(value) {
	if (!value) return "";
	const then = new Date(String(value).replace(" ", "T"));
	if (isNaN(then.getTime())) return "";
	const seconds = Math.round((Date.now() - then.getTime()) / 1000);
	if (seconds < 60) return "just now";
	if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
	if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
	return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
