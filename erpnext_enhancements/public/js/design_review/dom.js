/**
 * DOM helpers for the Review Room. Text always goes in through textContent: notes and names are
 * text employees wrote, and this page never interpolates them into markup.
 */

export function el(tag, cls, text) {
	const node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text != null) node.textContent = String(text);
	return node;
}

export function btn(cls, text, onClick, attrs) {
	const b = el("button", cls, text);
	b.type = "button";
	if (onClick) b.addEventListener("click", onClick);
	for (const [k, v] of Object.entries(attrs || {})) {
		if (v === false || v == null) continue;
		b.setAttribute(k, v === true ? "" : String(v));
	}
	return b;
}

export function clear(node) {
	while (node.firstChild) node.removeChild(node.firstChild);
	return node;
}

export function append(parent, ...kids) {
	for (const k of kids) if (k != null && k !== false) parent.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
	return parent;
}

export function lsGet(key) {
	try {
		return localStorage.getItem(key);
	} catch (e) {
		return null;
	}
}

export function lsSet(key, value) {
	try {
		localStorage.setItem(key, value);
	} catch (e) {
		/* private window or blocked storage: the preference just is not kept */
	}
}
