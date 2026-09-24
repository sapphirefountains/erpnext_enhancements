/**
 * Node builders. **Nothing in this app assigns `innerHTML`, and nothing may.**
 *
 * Every caption, first comment, asset title and alt text on this page was typed by an employee
 * and is shown to other employees, the approver among them. One `innerHTML` is a stored-XSS path
 * from any drafter to the person whose click publishes, so the rule is "none at all", which
 * needs no judgement at the call site. `scripts/test_marketing_source_rules.js` fails the build
 * on the word. Build nodes with `el()` and `fill()`.
 *
 * Modelled on `public/js/feedback/dom.js` (itself from the retired chat SPA's).
 */

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
		if (child) parent.appendChild(child);
	}
	return parent;
}

/** Replace a node's children: `clear` then `append`. */
export function fill(parent, ...children) {
	return append(clear(parent), ...children);
}

export function button(label, className, onClick) {
	const node = el("button", className || "ee-mk-btn", label);
	node.type = "button";
	if (onClick) node.addEventListener("click", onClick);
	return node;
}

/** A labelled control. Returns `{row, input}` so the caller keeps the input. */
export function field(labelText, control, help) {
	const row = el("label", "ee-mk-field");
	append(row, el("span", "ee-mk-field-label", labelText), control);
	if (help) append(row, el("span", "ee-mk-field-help", help));
	return { row, input: control };
}

export function input(type, value, placeholder) {
	const node = document.createElement("input");
	node.type = type || "text";
	node.className = "ee-mk-input";
	if (value !== undefined && value !== null) node.value = value;
	if (placeholder) node.placeholder = placeholder;
	return node;
}

export function textarea(value, rows, placeholder) {
	const node = document.createElement("textarea");
	node.className = "ee-mk-input ee-mk-textarea";
	node.value = value || "";
	node.rows = rows || 5;
	if (placeholder) node.placeholder = placeholder;
	return node;
}

export function select(options, value) {
	const node = document.createElement("select");
	node.className = "ee-mk-input";
	for (const option of options) {
		const pair = option && typeof option === "object";
		const item = document.createElement("option");
		item.value = pair ? option.value : option;
		item.textContent = pair ? option.label : option;
		node.appendChild(item);
	}
	if (value !== undefined && value !== null) node.value = value;
	return node;
}

export function checkbox(checked) {
	const node = document.createElement("input");
	node.type = "checkbox";
	node.className = "ee-mk-check";
	node.checked = !!checked;
	return node;
}

/** An internal link that routes client-side, leaving modifier-clicks to the browser. */
export function link(text, href, onNavigate, className) {
	const node = el("a", className || "ee-mk-link", text);
	node.href = href;
	node.addEventListener("click", (ev) => {
		if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
		ev.preventDefault();
		onNavigate(href);
	});
	return node;
}

/**
 * A link that leaves the app, in a new tab -- **only to an http(s) address**. A permalink can be
 * typed by a person ("It was published", with a link), and `javascript:` in an `href` runs in
 * this page when someone else clicks it. Anything else is shown as plain text.
 */
export function outLink(text, href) {
	if (!/^https?:\/\//i.test(String(href || ""))) return el("span", "ee-mk-muted", text);
	const node = el("a", "ee-mk-link", text);
	node.href = href;
	node.target = "_blank";
	node.rel = "noopener noreferrer";
	return node;
}

/** A small picture of an asset: the image or video itself when the file is here, else its type. */
export function assetThumb(asset, className) {
	const box = el("div", className || "ee-mk-thumb");
	const kind = (asset && asset.asset_type) || "";
	if (asset && asset.preview_url && kind === "Image") {
		const img = document.createElement("img");
		img.src = asset.preview_url;
		img.alt = asset.alt_text || asset.title || "";
		img.loading = "lazy";
		box.appendChild(img);
	} else if (asset && asset.preview_url && kind === "Video") {
		const video = document.createElement("video");
		video.src = asset.preview_url;
		video.preload = "metadata";
		video.muted = true;
		video.setAttribute("aria-label", asset.title || "Video");
		box.appendChild(video);
	} else {
		box.appendChild(el("span", "ee-mk-thumb-icon", { Image: "🖼", Video: "🎬", Document: "📄" }[kind] || "?"));
	}
	return box;
}

/**
 * A status pill with a **non-colour cue**: every status has its own glyph, because a colour-only
 * indicator fails anyone who cannot tell the colours apart. Do not delete the glyph to tidy up.
 */
function pill(value, marks, prefix) {
	const slug = String(value || "").toLowerCase().replace(/[^a-z]+/g, "-");
	const node = el("span", `ee-mk-pill ${prefix}-${slug || "none"}`);
	append(node, el("span", "ee-mk-pill-mark", marks[value] || "•"), el("span", null, value || "—"));
	return node;
}

/** `Social Post.status`. "Canceled", one l, as the Select spells it. */
export function statusPill(status) {
	return pill(
		status,
		{
			Draft: "✎",
			"Pending Approval": "…",
			Approved: "✓",
			Scheduled: "◷",
			Publishing: "▸",
			Published: "✓✓",
			"Partially Published": "◐",
			Failed: "!",
			Canceled: "✕",
		},
		"ee-mk-status"
	);
}

/** `Social Publish Job.state`. */
export function statePill(state) {
	return pill(
		state,
		{
			Pending: "◷",
			"In Progress": "▸",
			Published: "✓",
			Failed: "!",
			Unconfirmed: "?",
			Canceled: "✕",
		},
		"ee-mk-state"
	);
}

/** A network's name with its own mark, so the four read apart without colour. */
export function networkTag(network) {
	const marks = { Facebook: "f", Instagram: "◎", LinkedIn: "in", YouTube: "▶" };
	const slug = String(network || "").toLowerCase();
	const node = el("span", `ee-mk-net ee-mk-net-${slug || "none"}`);
	node.title = network || "";
	append(node, el("span", "ee-mk-net-mark", marks[network] || "•"), el("span", "ee-mk-net-name", network || ""));
	return node;
}

/**
 * A modal dialog, built on `<dialog>`: `{node, close}`. Focus is trapped by the browser and
 * Escape closes it. `actions` is `[{label, primary, onClick}]`; an action's click resolves
 * with whatever it returns and closes the dialog unless it returns `false`.
 */
export function dialog(title, body, actions) {
	const node = el("dialog", "ee-mk-dialog");
	const form = el("div", "ee-mk-dialog-body");
	const heading = el("h2", "ee-mk-dialog-title", title);
	const footer = el("div", "ee-mk-dialog-actions");
	const close = () => {
		if (node.open) node.close();
		node.remove();
	};
	for (const action of actions || []) {
		const btn = button(action.label, action.primary ? "ee-mk-btn ee-mk-btn-primary" : "ee-mk-btn", async () => {
			const result = action.onClick ? await action.onClick() : undefined;
			if (result !== false) close();
		});
		footer.appendChild(btn);
	}
	append(form, heading, ...(Array.isArray(body) ? body : [body]), footer);
	node.appendChild(form);
	node.addEventListener("close", () => node.remove());
	document.body.appendChild(node);
	node.showModal();
	return { node, close };
}

/**
 * Close every open dialog, as Escape would: the router calls this on every screen change. A
 * dialog is bound to the screen that opened it, and Back does not close one by itself on iOS or a
 * desktop. No history entry per dialog: Android's Back already closes a modal `<dialog>` without
 * moving the history, so an entry pushed for one would be left behind there.
 */
export function closeDialogs() {
	for (const node of document.querySelectorAll("dialog.ee-mk-dialog")) {
		if (node.open) node.close();
	}
}
