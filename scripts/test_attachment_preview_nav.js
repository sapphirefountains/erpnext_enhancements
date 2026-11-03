#!/usr/bin/env node
/**
 * Attachment preview arrows (global_enhancements/attachment_preview_nav.js, v1.780.0), executed
 * against a stubbed frappe, jQuery, DOM and browser history rather than grepped.
 *
 * Nik, 2026-11-03: Previous / Next arrows and a "3 of 12" counter in the form sidebar's attachment
 * preview, every file in the sidebar's order, wrapping at the ends, Left / Right keys while the
 * pane is open, and Back closes the pane before it leaves the form. Every rule below fails silently
 * in production -- a wrong row, a counter off by one, a key that types into a field, a Forward that
 * reopens a pane:
 *
 *   1. The list is the rows as the sidebar draws them: get_attachments() de-duplicated by file_name
 *      (first occurrence wins, as render_attachments does) and REVERSED (each row is inserted
 *      directly under the heading), the rows behind "Show all" included.
 *   2. Next on the last row shows the first; Previous on the first shows the last.
 *   3. The counter reads "<row> of <rows>" through __().
 *   4. One file: no arrows and no counter, and the three frappe buttons are untouched.
 *   5. The buttons sit at the front of .attachment-preview-actions, before the open-in-new-tab
 *      link, carry aria-labels, and a click neither bubbles nor follows a link.
 *   6. Left / Right step; ignored inside an input, a textarea or a contenteditable, with a modifier
 *      held, and once the pane has left the document.
 *   7. Opening pushes ONE history entry with the marker and no URL; stepping pushes none; a second
 *      click on a row while the pane is open pushes none.
 *   8. The close button and Escape consume the entry (history.back()), and the pane stays closed on
 *      a later Forward onto the stale entry.
 *   9. popstate with the pane open and no marker closes the pane, through the original hide, and
 *      pushes or pops nothing more.
 *  10. A deleted current file: Next goes to the first row; an emptied list closes the pane.
 *  11. A frappe without show_attachment_preview is left exactly as it is: no wrap, no popstate
 *      listener; and a second evaluation on the same prototype installs nothing twice.
 *
 * If the module no longer exists this exits 2 rather than passing vacuously.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const FILE = path.join(
	ROOT,
	"erpnext_enhancements",
	"public",
	"js",
	"global_enhancements",
	"attachment_preview_nav.js"
);

if (!fs.existsSync(FILE)) {
	console.error(
		"MARKERS NOT FOUND: " +
			path.relative(process.cwd(), FILE) +
			" does not exist. The attachment preview arrows were moved or removed -- re-derive these " +
			"checks deliberately rather than deleting them."
	);
	process.exit(2);
}
const SOURCE = fs.readFileSync(FILE, "utf8");

let failures = 0;
function check(condition, message) {
	if (condition) console.log("  ok    " + message);
	else {
		failures += 1;
		console.error("  FAIL  " + message);
	}
}

// --- a minimal DOM: enough for what the module touches -------------------------------------

class Element {
	constructor(tag, doc) {
		this.nodeType = 1;
		this.tagName = tag.toUpperCase();
		this.attrs = {};
		this.children = [];
		this.parentNode = null;
		this.listeners = {};
		this.textContent = "";
		this.isContentEditable = false;
		this.ownerDocument = doc;
	}
	get className() {
		return this.attrs.class || "";
	}
	set className(value) {
		this.attrs.class = value;
	}
	get classList() {
		const el = this;
		const names = () => el.className.split(/\s+/).filter(Boolean);
		return {
			contains: (name) => names().includes(name),
			add: (...more) => {
				const set = new Set(names());
				more.forEach((name) => set.add(name));
				el.className = Array.from(set).join(" ");
			},
			remove: (...gone) => {
				el.className = names()
					.filter((name) => !gone.includes(name))
					.join(" ");
			},
		};
	}
	setAttribute(name, value) {
		this.attrs[name] = String(value);
	}
	getAttribute(name) {
		return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
	}
	appendChild(child) {
		child.remove();
		child.parentNode = this;
		this.children.push(child);
		return child;
	}
	insertBefore(child, reference) {
		child.remove();
		child.parentNode = this;
		const at = reference ? this.children.indexOf(reference) : -1;
		if (at === -1) this.children.push(child);
		else this.children.splice(at, 0, child);
		return child;
	}
	remove() {
		if (!this.parentNode) return;
		const at = this.parentNode.children.indexOf(this);
		if (at !== -1) this.parentNode.children.splice(at, 1);
		this.parentNode = null;
	}
	empty() {
		this.children.slice().forEach((child) => child.remove());
		this.textContent = "";
	}
	contains(node) {
		if (node === this) return true;
		return this.children.some((child) => child.contains(node));
	}
	matches(selector) {
		if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
		return this.tagName === selector.toUpperCase();
	}
	descendants() {
		const out = [];
		this.children.forEach((child) => {
			out.push(child);
			out.push(...child.descendants());
		});
		return out;
	}
	querySelector(selector) {
		return this.descendants().find((el) => el.matches(selector)) || null;
	}
	querySelectorAll(selector) {
		return this.descendants().filter((el) => el.matches(selector));
	}
	// Enough of a parser for the strings the module builds: a flat run of elements, each
	// `<tag attrs>content</tag>` with content read as text (the stubbed button.html emits no nesting).
	insertAdjacentHTML(position, html) {
		const nodes = parseHtml(html, this.ownerDocument);
		if (position === "afterbegin") {
			nodes.reverse().forEach((node) => this.insertBefore(node, this.children[0] || null));
		} else if (position === "beforeend") {
			nodes.forEach((node) => this.appendChild(node));
		} else {
			throw new Error("unsupported position " + position);
		}
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	dispatchEvent(event) {
		if (!event.target) event.target = this;
		event.currentTarget = this;
		(this.listeners[event.type] || []).slice().forEach((fn) => fn.call(this, event));
		if (!event.stopped && this.parentNode && this.parentNode.dispatchEvent) this.parentNode.dispatchEvent(event);
	}
	click() {
		const event = {
			type: "click",
			defaultPrevented: false,
			stopped: false,
			preventDefault() {
				this.defaultPrevented = true;
			},
			stopPropagation() {
				this.stopped = true;
			},
		};
		this.dispatchEvent(event);
		return event;
	}
}

function parseHtml(html, doc) {
	const nodes = [];
	let rest = html.trim();
	const open = /^<(\w+)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*>/;
	while (rest.length) {
		const start = open.exec(rest);
		if (!start) throw new Error("cannot parse: " + rest.slice(0, 60));
		const tag = start[1];
		const el = new Element(tag, doc);
		const attrRe = /([\w-]+)(?:="([^"]*)")?/g;
		let attr;
		while ((attr = attrRe.exec(start[2]))) el.setAttribute(attr[1], attr[2] === undefined ? "" : attr[2]);
		rest = rest.slice(start[0].length);
		// Content up to the matching close tag, nested same-named tags counted.
		let depth = 1;
		let at = 0;
		const openTag = new RegExp("<" + tag + "(?:\\s|>)");
		const closeTag = "</" + tag + ">";
		while (depth > 0) {
			const nextClose = rest.indexOf(closeTag, at);
			if (nextClose === -1) throw new Error("unclosed <" + tag + ">");
			const between = rest.slice(at, nextClose);
			const nextOpen = between.search(openTag);
			if (nextOpen !== -1) {
				depth += 1;
				at = at + nextOpen + 1;
				continue;
			}
			depth -= 1;
			at = nextClose + closeTag.length;
		}
		const inner = rest.slice(0, at - closeTag.length);
		rest = rest.slice(at).trim();
		if (/^<\w+/.test(inner.trim())) {
			parseHtml(inner, doc).forEach((child) => el.appendChild(child));
		} else {
			el.textContent = inner;
		}
		nodes.push(el);
	}
	return nodes;
}

// --- the world: document, jQuery for document events, history, frappe ------------------------

function makeWorld({ files, withShow = true } = {}) {
	const world = { pushes: [], backs: 0, forwards: 0, popstates: 0, log: [] };

	const document = {
		nodeType: 9,
		handlers: {},
		createElement: (tag) => new Element(tag, document),
	};
	document.body = new Element("body", document);
	document.body.parentNode = document;
	document.contains = (node) => node === document || document.body.contains(node);
	document.querySelector = (selector) => document.body.querySelector(selector);
	world.document = document;

	// jQuery, as far as the module and the stubbed frappe use it: namespaced document events.
	function $(target) {
		if (target !== document) throw new Error("the module may only use $ on document");
		const api = {
			on(name, fn) {
				document.handlers[name] = fn;
				return api;
			},
			off(name) {
				delete document.handlers[name];
				return api;
			},
		};
		return api;
	}
	world.$ = $;
	world.keydown = (key, extra) => {
		const event = Object.assign(
			{ type: "keydown", key, target: document.body, prevented: false, preventDefault() { this.prevented = true; } },
			extra || {}
		);
		// A handler may unbind another (Escape's hide unbinds ours), so read each one as it is reached.
		Object.keys(document.handlers)
			.filter((name) => name.startsWith("keydown"))
			.forEach((name) => {
				const fn = document.handlers[name];
				if (fn) fn(event);
			});
		return event;
	};

	// Browser history: entries with states, Back and Forward queued like a browser's (popstate lands
	// after the call returns), flushed by world.flush().
	const history = { entries: [{ state: null }], index: 0, state: null, pending: [] };
	history.pushState = function (state, title, url) {
		world.pushes.push({ state, title, url, argc: arguments.length });
		history.entries.splice(history.index + 1);
		history.entries.push({ state });
		history.index += 1;
		history.state = state;
	};
	history.replaceState = function () {
		throw new Error("the module must not replaceState");
	};
	history.back = () => {
		world.backs += 1;
		history.pending.push(-1);
	};
	history.forward = () => {
		world.forwards += 1;
		history.pending.push(1);
	};
	world.history = history;
	const windowListeners = {};
	const window = {
		history,
		document,
		addEventListener: (type, fn) => (windowListeners[type] = windowListeners[type] || []).push(fn),
	};
	world.window = window;
	world.windowListeners = windowListeners;
	world.flush = () => {
		while (history.pending.length) {
			const delta = history.pending.shift();
			const next = history.index + delta;
			if (next < 0 || next >= history.entries.length) continue;
			history.index = next;
			history.state = history.entries[next].state;
			world.popstates += 1;
			(windowListeners.popstate || []).forEach((fn) => fn({ type: "popstate", state: history.state }));
		}
	};
	// The user pressing Back or Forward.
	world.back = () => {
		history.back();
		world.backs -= 1; // the user's, not the module's
		world.flush();
	};
	world.forward = () => {
		history.forward();
		world.forwards -= 1;
		world.flush();
	};

	// frappe v16.53's Attachments, as far as the preview goes.
	class Attachments {
		constructor(opts) {
			Object.assign(this, opts);
			const pane = new Element("div", document);
			pane.className = "attachment-preview hidden";
			this.frm.page.sidebar.appendChild(pane);
			this.attachment_preview = { 0: pane, length: 1 };
			this.refreshes = 0;
		}
		refresh() {
			this.refreshes += 1;
		}
		get_attachments() {
			return this.frm.get_docinfo().attachments || [];
		}
		get_file_url(attachment) {
			return attachment.file_url || "/files/" + attachment.file_name;
		}
		hide_attachment_preview() {
			const pane = this.attachment_preview[0];
			pane.classList.add("hidden");
			pane.empty();
			world.log.push("hide");
			$(document).off("keydown.attachment_preview");
		}
	}
	if (withShow) {
		Attachments.prototype.show_attachment_preview = function (attachment, file_url) {
			const pane = this.attachment_preview[0];
			pane.classList.remove("hidden");
			pane.empty();
			world.log.push("show " + attachment.name);
			const header = new Element("div", document);
			header.className = "attachment-preview-header";
			const title = new Element("div", document);
			title.className = "attachment-preview-title";
			title.textContent = attachment.file_name;
			const actions = new Element("div", document);
			actions.className = "attachment-preview-actions";
			const open = new Element("a", document);
			open.className = "es-button attachment-preview-open-link";
			open.setAttribute("href", file_url);
			const copy = new Element("button", document);
			copy.className = "es-button attachment-preview-copy-link";
			const close = new Element("button", document);
			close.className = "es-button attachment-preview-close";
			close.addEventListener("click", () => this.hide_attachment_preview());
			actions.appendChild(open);
			actions.appendChild(copy);
			actions.appendChild(close);
			header.appendChild(title);
			header.appendChild(actions);
			pane.appendChild(header);
			const body = new Element("div", document);
			body.className = "attachment-preview-body";
			pane.appendChild(body);
			$(document)
				.off("keydown.attachment_preview")
				.on("keydown.attachment_preview", (event) => {
					if (event.key !== "Escape") return;
					if (!document.body.contains(this.attachment_preview[0])) {
						$(document).off("keydown.attachment_preview");
						return;
					}
					this.hide_attachment_preview();
				});
		};
	}

	const frappe = {
		ui: {
			form: { Attachments },
			button: {
				html(opts) {
					const attrs = Object.entries(opts.attrs || {})
						.map(([key, value]) => ` ${key}="${value}"`)
						.join("");
					return (
						`<button class="es-button ${opts.css_class || ""}" type="button" data-variant="${opts.variant}"` +
						` data-icon-button="true" title="${opts.title}"${attrs}>` +
						frappe.utils.icon(opts.icon, "sm") +
						"</button>"
					);
				},
			},
		},
		utils: {
			icon: (name) => `<svg class="icon icon-sm" data-icon="${name}"></svg>`,
			escape_html: (text) =>
				String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]),
		},
	};
	world.frappe = frappe;
	world.__ = (text, args) => "T:" + text.replace(/\{(\d+)\}/g, (m, i) => (args ? String(args[i]) : m));

	// The form: the page's sidebar holds the pane; docinfo holds the files.
	const sidebar = new Element("div", document);
	sidebar.className = "layout-side-section";
	document.body.appendChild(sidebar);
	world.docinfo = { attachments: files.slice() };
	world.frm = {
		get_docinfo: () => world.docinfo,
		page: { sidebar, wrapper: { addClass() {}, removeClass() {} } },
	};

	world.context = vm.createContext({
		window,
		document,
		history,
		frappe,
		$,
		__: world.__,
		console,
	});
	world.run = () => vm.runInContext(SOURCE, world.context, { filename: FILE });
	world.make = () => {
		world.attachments = new Attachments({ frm: world.frm, parent: null });
		return world.attachments;
	};
	world.pane = () => world.attachments.attachment_preview[0];
	world.counter = () => {
		const el = world.pane().querySelector(".attachment-preview-count");
		return el ? el.textContent : null;
	};
	world.shown = () => {
		const el = world.pane().querySelector(".attachment-preview-title");
		return el ? el.textContent : null;
	};
	world.open = (attachment) => {
		world.attachments.show_attachment_preview(attachment, world.attachments.get_file_url(attachment));
	};
	world.isOpen = () => document.body.contains(world.pane()) && !world.pane().classList.contains("hidden");
	return world;
}

// The server's array: File's default sort, creation descending (newest first), with a duplicate
// file name as the sidebar would carry after a re-upload. The rows are drawn in reverse.
const FILES = [
	{ name: "f5", file_name: "five.pdf", file_url: "/files/five.pdf" },
	{ name: "f4", file_name: "four.png", file_url: "/private/files/four.png", is_private: 1 },
	{ name: "f3b", file_name: "three.csv", file_url: "/files/three.csv" },
	{ name: "f3", file_name: "three.csv", file_url: "/files/three.csv" },
	{ name: "f2", file_name: "two.jpg", file_url: "/files/two.jpg" },
	{ name: "f1", file_name: "one.pdf", file_url: "/files/one.pdf" },
];
const DRAWN = ["one.pdf", "two.jpg", "three.csv", "four.png", "five.pdf"];

console.log("1. the order is the rows as drawn");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]); // one.pdf, the top row
	check(world.shown() === "one.pdf", "the top row opens");
	check(world.counter() === "T:1 of 5", "the counter reads 1 of 5 (the duplicate three.csv counted once): " + world.counter());
	const seen = [world.shown()];
	for (let i = 0; i < 4; i++) {
		world.pane().querySelector(".attachment-preview-next").click();
		seen.push(world.shown());
	}
	check(JSON.stringify(seen) === JSON.stringify(DRAWN), "Next walks the rows top to bottom: " + seen.join(", "));
	check(world.counter() === "T:5 of 5", "the counter follows: " + world.counter());
	// Open the second copy of three.csv (the row frappe keeps is f3b, the first in the array).
	world.open(FILES[2]);
	check(world.counter() === "T:3 of 5", "the kept duplicate is row 3: " + world.counter());
	world.open(FILES[3]);
	check(world.counter() === "T:3 of 5", "the dropped duplicate (not in the list by name) is found by file_url: " + world.counter());
}

console.log("2. wrap at both ends");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[0]); // five.pdf, the last row
	check(world.counter() === "T:5 of 5", "the last row is 5 of 5");
	world.pane().querySelector(".attachment-preview-next").click();
	check(world.shown() === "one.pdf" && world.counter() === "T:1 of 5", "Next on the last row shows the first");
	world.pane().querySelector(".attachment-preview-prev").click();
	check(world.shown() === "five.pdf" && world.counter() === "T:5 of 5", "Previous on the first row shows the last");
}

console.log("3. the counter goes through __()");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[4]);
	check(world.counter() === "T:2 of 5", "the text is __(\"{0} of {1}\", [2, 5]): " + world.counter());
	check(SOURCE.includes('"{0} of {1}"'), "the source carries the translatable string");
}

console.log("4. one file: nothing added");
{
	const world = makeWorld({ files: [FILES[0]] });
	world.run();
	world.make();
	world.open(FILES[0]);
	const actions = world.pane().querySelector(".attachment-preview-actions");
	check(actions.querySelector(".attachment-preview-prev") === null, "no Previous");
	check(actions.querySelector(".attachment-preview-next") === null, "no Next");
	check(actions.querySelector(".attachment-preview-count") === null, "no counter");
	check(
		actions.children.map((el) => el.className).join("|") ===
			"es-button attachment-preview-open-link|es-button attachment-preview-copy-link|es-button attachment-preview-close",
		"frappe's three actions are as they were"
	);
	check(world.pushes.length === 1, "the history entry is still pushed (Back still closes the pane)");
}

console.log("5. placement, labels, and a click that goes nowhere");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]);
	const actions = world.pane().querySelector(".attachment-preview-actions");
	const classes = actions.children.map((el) => el.className);
	check(classes[0] === "attachment-preview-nav", "the group is first in the actions: " + classes.join("|"));
	check(classes[1] === "es-button attachment-preview-open-link", "before the open-in-new-tab link");
	const nav = actions.children[0];
	check(
		nav.children.map((el) => el.className).join("|") ===
			"es-button attachment-preview-prev|attachment-preview-count|es-button attachment-preview-next",
		"Previous, the counter, Next"
	);
	const prev = nav.querySelector(".attachment-preview-prev");
	const next = nav.querySelector(".attachment-preview-next");
	check(prev.getAttribute("aria-label") === "T:Previous file" && prev.getAttribute("title") === "T:Previous file", "Previous is labelled");
	check(next.getAttribute("aria-label") === "T:Next file" && next.getAttribute("title") === "T:Next file", "Next is labelled");
	check(prev.getAttribute("data-variant") === "ghost" && next.getAttribute("data-variant") === "ghost", "ghost buttons, like frappe's");
	check(
		prev.querySelector("svg").getAttribute("data-icon") === "chevron-left" &&
			next.querySelector("svg").getAttribute("data-icon") === "chevron-right",
		"chevron-left and chevron-right"
	);
	let bubbled = false;
	world.document.body.addEventListener("click", () => (bubbled = true));
	const event = next.click();
	check(event.defaultPrevented && !bubbled, "a click is prevented and does not bubble to the body");
	check(world.shown() === "two.jpg", "and it stepped");
}

console.log("6. Left and Right keys");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	check(!("keydown.attachment_preview_nav" in world.document.handlers), "nothing is bound before the pane opens");
	world.open(FILES[5]);
	check("keydown.attachment_preview_nav" in world.document.handlers, "bound while the pane is open");
	let event = world.keydown("ArrowRight");
	check(world.shown() === "two.jpg" && event.prevented, "Right is next");
	event = world.keydown("ArrowLeft");
	check(world.shown() === "one.pdf" && event.prevented, "Left is previous");
	world.keydown("ArrowLeft");
	check(world.shown() === "five.pdf", "and wraps");
	const input = new Element("input", world.document);
	event = world.keydown("ArrowRight", { target: input });
	check(world.shown() === "five.pdf" && !event.prevented, "ignored inside an input");
	const area = new Element("textarea", world.document);
	world.keydown("ArrowRight", { target: area });
	check(world.shown() === "five.pdf", "ignored inside a textarea");
	const editable = new Element("div", world.document);
	editable.isContentEditable = true;
	world.keydown("ArrowRight", { target: editable });
	check(world.shown() === "five.pdf", "ignored inside a contenteditable");
	world.keydown("ArrowRight", { ctrlKey: true });
	world.keydown("ArrowRight", { altKey: true });
	world.keydown("ArrowRight", { metaKey: true });
	world.keydown("ArrowRight", { shiftKey: true });
	check(world.shown() === "five.pdf", "ignored with a modifier held");
	world.keydown("ArrowDown");
	check(world.shown() === "five.pdf", "other keys are not ours");
	world.pane().querySelector(".attachment-preview-close").click();
	check(!("keydown.attachment_preview_nav" in world.document.handlers), "unbound when the pane closes");
	world.keydown("ArrowRight");
	check(!world.isOpen(), "a key after the close opens nothing");
	// The router removed the pane element (formview.show_doc) without a hide.
	world.open(FILES[5]);
	world.pane().remove();
	world.keydown("ArrowRight");
	check(!("keydown.attachment_preview_nav" in world.document.handlers), "a key with the pane gone from the document unbinds itself");
	check(world.log[world.log.length - 1] === "show f1", "and shows nothing");
}

console.log("7. one history entry per open pane, no URL");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	check(world.pushes.length === 0, "nothing pushed at evaluation");
	world.open(FILES[5]);
	check(world.pushes.length === 1, "opening pushes one entry");
	const push = world.pushes[0];
	check(push.state && push.state.ee_attachment_preview === true, "with the marker");
	check(push.argc === 2 && push.url === undefined, "and no URL argument");
	check(world.history.state === push.state, "it is the current state");
	world.pane().querySelector(".attachment-preview-next").click();
	world.keydown("ArrowRight");
	world.open(FILES[0]); // a row clicked while the pane is open
	check(world.pushes.length === 1, "stepping and re-opening push nothing");
	check(world.backs === 0, "and pop nothing");
}

console.log("8. the close button and Escape consume the entry");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]);
	world.pane().querySelector(".attachment-preview-close").click();
	check(!world.isOpen(), "the pane closed");
	check(world.backs === 1, "history.back() was called once");
	world.flush();
	check(world.history.state === null && world.history.index === 0, "the entry is consumed");
	check(!world.isOpen() && world.log.filter((l) => l === "hide").length === 1, "that back's popstate hid nothing again");
	world.forward();
	check(!world.isOpen(), "Forward onto the stale entry does not reopen the pane");
	check(world.backs === 1 && world.pushes.length === 1, "and nothing else moved");
	// Escape goes through frappe's own handler, which calls hide: the wrapper consumes the same way.
	world.back();
	world.open(FILES[5]);
	check(world.pushes.length === 2, "opened again: a second entry");
	world.keydown("Escape");
	check(!world.isOpen(), "Escape closed the pane");
	check(world.backs === 2, "and consumed the entry");
	world.flush();
	check(world.history.index === 0, "back on the form's entry");
}

console.log("9. Back closes the pane");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]);
	world.pane().querySelector(".attachment-preview-next").click();
	const before = world.pushes.length;
	world.back();
	check(!world.isOpen(), "popstate without the marker closed the pane");
	check(world.log[world.log.length - 1] === "hide", "through the original hide");
	check(world.backs === 0 && world.pushes.length === before, "nothing pushed or popped by the close");
	check(world.history.index === 0, "the form's own entry is current: a second Back leaves the form");
	check(!("keydown.attachment_preview_nav" in world.document.handlers), "keys unbound");
	// A popstate while the pane is closed (any other Back on the form) touches nothing.
	world.forward();
	check(!world.isOpen() && world.log.filter((l) => l === "hide").length === 1, "Forward with the pane closed does nothing");
}

console.log("10. the list is read on every step");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[4]); // two.jpg, row 2
	// Deleted: frappe's remove_fileid rewrites docinfo.attachments and refreshes.
	world.docinfo.attachments = FILES.filter((f) => f.name !== "f2");
	world.attachments.refresh();
	check(world.counter() === "T:4 files", "after a delete of the shown file the counter says how many remain: " + world.counter());
	world.pane().querySelector(".attachment-preview-next").click();
	check(world.shown() === "one.pdf" && world.counter() === "T:1 of 4", "Next from a deleted file goes to the first row");
	// An upload: add_to_attachments pushes onto the array (drawn at the top), then refresh.
	world.docinfo.attachments.push({ name: "f0", file_name: "zero.txt", file_url: "/files/zero.txt" });
	world.attachments.refresh();
	check(world.counter() === "T:2 of 5", "an upload moves the counter: " + world.counter());
	world.pane().querySelector(".attachment-preview-prev").click();
	check(world.shown() === "zero.txt" && world.counter() === "T:1 of 5", "and the new row is reachable");
	// Everything deleted.
	world.docinfo.attachments = [];
	world.pane().querySelector(".attachment-preview-next").click();
	check(!world.isOpen(), "an emptied list closes the pane");
	check(world.backs === 1, "and consumes the entry");
	// Down to one file while open: refresh takes the arrows away.
	world.flush();
	world.docinfo.attachments = [FILES[0]];
	world.open(FILES[0]);
	check(world.pane().querySelector(".attachment-preview-nav") === null, "one file: no arrows");
	world.docinfo.attachments = FILES.slice(0, 2);
	world.attachments.refresh();
	check(world.pane().querySelector(".attachment-preview-nav") !== null, "a second file: arrows appear on refresh");
	world.docinfo.attachments = [FILES[0]];
	world.attachments.refresh();
	check(world.pane().querySelector(".attachment-preview-nav") === null, "back to one: gone again");
}

console.log("11. an older frappe is left alone, and a second evaluation installs nothing twice");
{
	const world = makeWorld({ files: FILES, withShow: false });
	const hide = world.frappe.ui.form.Attachments.prototype.hide_attachment_preview;
	const refresh = world.frappe.ui.form.Attachments.prototype.refresh;
	world.run();
	const proto = world.frappe.ui.form.Attachments.prototype;
	check(proto.hide_attachment_preview === hide && proto.refresh === refresh, "no method wrapped");
	check(!proto.__ee_preview_nav, "no marker");
	check(!(world.windowListeners.popstate || []).length, "no popstate listener");
	check(!("show_attachment_preview" in proto), "nothing invented");
}
{
	const world = makeWorld({ files: FILES });
	world.run();
	const once = world.frappe.ui.form.Attachments.prototype.show_attachment_preview;
	world.run();
	check(world.frappe.ui.form.Attachments.prototype.show_attachment_preview === once, "a second evaluation keeps the first wrap");
	check((world.windowListeners.popstate || []).length === 1, "and one popstate listener");
	world.make();
	world.open(FILES[5]);
	check(world.pushes.length === 1, "one entry, not two");
}

if (failures) {
	console.error("\n" + failures + " check(s) failed");
	process.exit(1);
}
console.log("\nall checks passed");
