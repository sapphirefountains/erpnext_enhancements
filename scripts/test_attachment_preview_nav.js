#!/usr/bin/env node
/**
 * Attachment preview arrows (global_enhancements/attachment_preview_nav.js, v1.780.0), executed
 * against a stubbed frappe, jQuery, DOM, router and browser history rather than grepped.
 *
 * Nik, 2026-11-03: Previous / Next arrows and a "3 of 12" counter in the form sidebar's attachment
 * preview, every file in the sidebar's order, wrapping at the ends, and Left / Right keys while the
 * pane is open. Every rule below fails silently in production -- a wrong row, a counter off by one,
 * a key that types into a field, arrow keys hijacked on a list after leaving the form:
 *
 *   1. The list is the rows as the sidebar has drawn them, read from the `.attachment-row` pills
 *      (the File id in the icon link, else the label's URL), then the files "Show all" has not drawn
 *      yet in the order it would draw them: get_attachments() de-duplicated by file_name (first
 *      occurrence wins, as render_attachments does) and REVERSED. The counter stays on the row when
 *      frappe's order differs from the array's (an upload at the top, a duplicate outside the
 *      slice); with no rows to read, the array order is used.
 *   2. Next on the last row shows the first; Previous on the first shows the last.
 *   3. The counter reads "<row> of <rows>" through __().
 *   4. One file: no arrows and no counter, and the three frappe buttons are untouched.
 *   5. The buttons sit at the front of .attachment-preview-actions, before the open-in-new-tab
 *      link, carry aria-labels, and a click neither bubbles nor follows a link.
 *   6. Left / Right step; ignored inside an input, a textarea or a contenteditable, with a modifier
 *      held, when something already called preventDefault, on a held key's repeats, while a
 *      .modal.show dialog is open, once the pane has left the document, and once the form's page
 *      is hidden under another page (the keys unbind themselves on that press).
 *   7. No history entry, ever: nothing pushed, replaced or popped by opening, stepping or closing,
 *      and no popstate listener besides frappe's own.
 *   8. Leaving the form for a list (the page is hidden, the pane element stays) closes the pane
 *      through the original hide on the router's "change" and releases the keys; coming Back
 *      (frappe's popstate -> route() -> show_doc teardown and a sidebar rebuild) binds nothing.
 *   9. The user's Back with the pane open: frappe's popstate listener re-renders the form (the pane
 *      removed, a new Sidebar and Attachments built); the old instance is released, nothing is bound
 *      and the new instance opens cleanly.
 *  10. A sidebar rebuild with the pane open (Save, frm.refresh(), a realtime update: page.sidebar
 *      emptied, a new Attachments constructed) releases the old instance from setup_preview_area.
 *  11. The list is read on every step: a deleted current file steps to the first row, an upload is
 *      counted, an emptied list closes the pane, one file takes the arrows away.
 *  12. A frappe without show_attachment_preview, setup_preview_area, frappe.ui.button.html or
 *      frappe.utils.escape_html is left exactly as it is; a second evaluation on the same prototype
 *      installs nothing twice.
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
		this.style = {};
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
	get nextSibling() {
		if (!this.parentNode) return null;
		const kids = this.parentNode.children;
		return kids[kids.indexOf(this) + 1] || null;
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
	// A tag, or one or more classes (".modal.show": every class present).
	matches(selector) {
		if (selector.startsWith(".")) {
			return selector
				.split(".")
				.filter(Boolean)
				.every((name) => this.classList.contains(name));
		}
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

// --- the world: document, jQuery, history, a router that re-renders on popstate, frappe ------

function makeWorld({ files, withShow = true, withSetup = true, withButton = true, withEscape = true } = {}) {
	const world = { pushes: [], replaces: [], backs: 0, log: [], routes: 0, instances: [] };

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

	// jQuery's :visible: in the document, and no `hidden` class or display: none on the way up.
	function visible(el) {
		let node = el;
		while (node && node.nodeType === 1) {
			if (node.classList.contains("hidden") || node.style.display === "none") return false;
			node = node.parentNode;
		}
		return node === document;
	}

	// jQuery, as far as the module and the stubbed frappe use it: namespaced document events, and
	// `.is(":visible")` on an element.
	function $(target) {
		if (target === document) {
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
		if (target && target.nodeType === 1) {
			return {
				is(selector) {
					if (selector !== ":visible") throw new Error("the module may only ask an element :visible");
					return visible(target);
				},
			};
		}
		throw new Error("the module may only use $ on document or an element");
	}
	world.$ = $;
	world.keydown = (key, extra) => {
		const event = Object.assign(
			{
				type: "keydown",
				key,
				target: document.body,
				prevented: false,
				defaultPrevented: false,
				repeat: false,
				preventDefault() {
					this.prevented = true;
					this.defaultPrevented = true;
				},
			},
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

	// Browser history: the module must leave it alone, so every write is recorded; popstate is what
	// the user's Back delivers, and frappe's router listens to it first (router.js:18-22).
	const history = { state: null };
	history.pushState = function (state, title, url) {
		world.pushes.push({ state, title, url });
	};
	history.replaceState = function (state, title, url) {
		world.replaces.push({ state, title, url });
	};
	history.back = () => {
		world.backs += 1;
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

	// frappe v16.53's Attachments, as far as the sidebar rows and the preview go.
	class Attachments {
		constructor(opts) {
			Object.assign(this, opts);
			this.attachments_page_length = opts.page_length || 10;
			this.show_all_attachments = false;
			this.refreshes = 0;
			world.instances.push(this);
			this.make();
		}
		make() {
			this.add_attachment_wrapper = this.parent ? this.parent.querySelector(".attachments-actions") : null;
			this.setup_preview_area();
		}
		refresh() {
			this.refreshes += 1;
			if (!this.parent) return;
			this.parent.querySelectorAll(".attachment-row").forEach((row) => row.remove());
			let attachments = this.get_attachments();
			if (!this.show_all_attachments && attachments.length > this.attachments_page_length) {
				// render last n attachments as they are at the top
				attachments = attachments.slice(attachments.length - this.attachments_page_length);
			}
			const exists = {};
			attachments
				.filter((attachment) =>
					Object.prototype.hasOwnProperty.call(exists, attachment.file_name)
						? false
						: (exists[attachment.file_name] = true)
				)
				.forEach((attachment) => this.add_attachment(attachment));
		}
		add_attachment(attachment) {
			const row = new Element("div", document);
			row.className = "attachment-row";
			const pill = new Element("button", document);
			pill.className = "data-pill btn";
			const icon = new Element("a", document);
			icon.className = "attachment-icon";
			icon.setAttribute("href", "/desk/file/" + encodeURIComponent(attachment.name));
			const label = new Element("a", document);
			label.className = "ellipsis attachment-file-label ellipsis-width";
			label.setAttribute("href", this.get_file_url(attachment));
			label.textContent = attachment.file_name;
			pill.appendChild(icon);
			pill.appendChild(label);
			row.appendChild(pill);
			// .insertAfter(this.add_attachment_wrapper): directly under the heading, so reversed.
			this.add_attachment_wrapper.parentNode.insertBefore(row, this.add_attachment_wrapper.nextSibling);
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
	if (withSetup) {
		Attachments.prototype.setup_preview_area = function () {
			if (this.attachment_preview) return;
			const pane = new Element("div", document);
			pane.className = "attachment-preview hidden";
			this.frm.page.sidebar.appendChild(pane);
			this.attachment_preview = { 0: pane, length: 1 };
		};
	} else {
		Attachments.prototype.setup_preview_area = "not a function";
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
			if (withEscape) {
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
			}
		};
	}

	// frappe's router, as far as this module meets it: `on("change")`, and a popstate listener,
	// registered BEFORE the module runs as frappe's is, that re-renders the current route.
	const router = {
		handlers: {},
		on(event, fn) {
			(this.handlers[event] = this.handlers[event] || []).push(fn);
		},
		trigger(event) {
			(this.handlers[event] || []).slice().forEach((fn) => fn());
		},
		route() {
			world.routes += 1;
			world.render_route();
			this.trigger("change");
		},
	};
	window.addEventListener("popstate", () => router.route());

	const frappe = {
		router,
		ui: {
			form: { Attachments },
			button: withButton
				? {
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
				  }
				: {},
		},
		utils: {
			icon: (name) => `<svg class="icon icon-sm" data-icon="${name}"></svg>`,
			escape_html: (text) =>
				String(text).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]),
		},
	};
	world.frappe = frappe;
	world.__ = (text, args) => "T:" + text.replace(/\{(\d+)\}/g, (m, i) => (args ? String(args[i]) : m));

	// The form's page: a page div (hidden by frappe.container.change_to when another page shows)
	// holding the sidebar; the sidebar holds the attachments section and the pane.
	const page = new Element("div", document);
	page.className = "page-container";
	document.body.appendChild(page);
	const sidebar = new Element("div", document);
	sidebar.className = "layout-side-section";
	page.appendChild(sidebar);
	world.page = page;
	world.sidebar = sidebar;
	world.docinfo = { attachments: files.slice() };
	world.frm = {
		get_docinfo: () => world.docinfo,
		page: { sidebar, wrapper: { 0: page, addClass() {}, removeClass() {} } },
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
	// form.js render_form: a new Sidebar empties page.sidebar and constructs a new Attachments,
	// which Sidebar.refresh() then refreshes. `parent: null` is a sidebar with no attachments section.
	world.make = ({ page_length, noParent = false } = {}) => {
		let parent = null;
		if (!noParent) {
			parent = new Element("div", document);
			parent.className = "form-attachments";
			const actions = new Element("div", document);
			actions.className = "attachments-actions";
			parent.appendChild(actions);
			sidebar.appendChild(parent);
		}
		world.attachments = new Attachments({ frm: world.frm, parent, page_length });
		world.attachments.refresh();
		return world.attachments;
	};
	world.rebuild_sidebar = (opts) => {
		sidebar.empty();
		return world.make(opts);
	};
	// Routes: "form" renders the form (FormFactory.show_doc: clear_attachment_preview_state, then
	// frm.refresh(name) -> render_form -> a new Sidebar); "list" hides the form's page div and
	// leaves its DOM exactly as it was.
	world.route = "form";
	world.route_stack = [];
	world.render_route = () => {
		if (world.route === "form") {
			document.body.querySelectorAll(".attachment-preview").forEach((el) => el.remove());
			delete document.handlers["keydown.attachment_preview"];
			page.style.display = "";
			world.rebuild_sidebar();
		} else {
			page.style.display = "none";
		}
	};
	// A click on a link: set_route -> push_state -> route().
	world.go = (route) => {
		world.route_stack.push(world.route);
		world.route = route;
		router.route();
	};
	// The user's Back: the browser delivers popstate; frappe's listener re-renders.
	world.back = () => {
		world.route = world.route_stack.pop() || "form";
		(windowListeners.popstate || []).forEach((fn) => fn({ type: "popstate", state: history.state }));
	};
	world.pane = () => world.attachments.attachment_preview[0];
	world.rows = () =>
		world.attachments.parent
			? world.attachments.parent.querySelectorAll(".attachment-row").map((row) => row.querySelector(".attachment-file-label").textContent)
			: [];
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
	world.keysBound = () => "keydown.attachment_preview_nav" in document.handlers;
	world.historyUntouched = () => world.pushes.length === 0 && world.replaces.length === 0 && world.backs === 0;
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
	check(world.rows().join(", ") === DRAWN.join(", "), "the stub draws the rows as frappe does: " + world.rows().join(", "));
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
{
	// Ten rows behind "Show all": frappe slices the LAST three of the array (the oldest) before it
	// de-duplicates, so the drawn three.csv row is f3, not f3b.
	const world = makeWorld({ files: FILES });
	world.run();
	world.make({ page_length: 3 });
	check(world.rows().join(", ") === "one.pdf, two.jpg, three.csv", "three rows drawn, the oldest at the top: " + world.rows().join(", "));
	world.open(FILES[3]); // f3, the drawn three.csv row
	check(world.counter() === "T:3 of 5", "the drawn row is 3 of 5, the files behind Show all counted after it: " + world.counter());
	world.pane().querySelector(".attachment-preview-next").click();
	check(world.shown() === "four.png" && world.counter() === "T:4 of 5", "Next reaches a file that has no row yet");
	world.pane().querySelector(".attachment-preview-next").click();
	world.pane().querySelector(".attachment-preview-next").click();
	check(world.shown() === "one.pdf" && world.counter() === "T:1 of 5", "and wraps to the top row");
	world.open(FILES[2]); // f3b: no row of its own, same name and URL as the drawn f3
	check(world.counter() === "T:3 of 5", "the undrawn duplicate resolves to the drawn row: " + world.counter());
	// Show all: every file drawn, now with f3b as the three.csv row.
	world.attachments.show_all_attachments = true;
	world.attachments.refresh();
	check(world.rows().length === 5, "Show all draws every row");
	check(world.counter() === "T:3 of 5", "the counter is unchanged by Show all: " + world.counter());
	// An upload lands at the END of the array (add_to_attachments) and so at the TOP of the rows
	// until reload_docinfo re-sorts it; the counter follows the rows, not the array.
	world.attachments.show_all_attachments = false;
	world.docinfo.attachments.push({ name: "f0", file_name: "zero.txt", file_url: "/files/zero.txt" });
	world.open(FILES[4]); // two.jpg
	world.attachments.refresh();
	check(world.rows().join(", ") === "zero.txt, one.pdf, two.jpg", "the upload is the top row: " + world.rows().join(", "));
	check(world.counter() === "T:3 of 6", "two.jpg is row 3 of 6 while the upload sits at the top: " + world.counter());
	world.pane().querySelector(".attachment-preview-prev").click();
	world.pane().querySelector(".attachment-preview-prev").click();
	check(world.shown() === "zero.txt" && world.counter() === "T:1 of 6", "Previous twice reaches the upload at the top");
}
{
	// No rows to read (a sidebar without an attachments section): the array order, reversed.
	const world = makeWorld({ files: FILES });
	world.run();
	world.make({ noParent: true });
	world.open(FILES[3]);
	check(world.counter() === "T:3 of 5", "with no rows the array order is used: " + world.counter());
	world.pane().querySelector(".attachment-preview-next").click();
	check(world.shown() === "four.png", "and stepping works");
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
	check(world.historyUntouched(), "and the history is untouched");
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
	check(prev.getAttribute("aria-label") === "T:Previous file" && prev.getAttribute("title") === "T:Previous file", "Previous is labeled");
	check(next.getAttribute("aria-label") === "T:Next file" && next.getAttribute("title") === "T:Next file", "Next is labeled");
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
	check(!world.keysBound(), "nothing is bound before the pane opens");
	world.open(FILES[5]);
	check(world.keysBound(), "bound while the pane is open");
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
	event = world.keydown("ArrowRight", { defaultPrevented: true });
	check(world.shown() === "five.pdf" && world.keysBound(), "ignored when something already called preventDefault");
	event = world.keydown("ArrowRight", { repeat: true });
	check(world.shown() === "five.pdf" && !event.prevented, "ignored on a held key's repeats");
	const modal = new Element("div", world.document);
	modal.className = "modal fade show";
	world.document.body.appendChild(modal);
	event = world.keydown("ArrowRight");
	check(world.shown() === "five.pdf" && !event.prevented, "ignored while a dialog is open");
	modal.remove();
	world.keydown("ArrowDown");
	check(world.shown() === "five.pdf", "other keys are not ours");
	world.keydown("ArrowRight");
	check(world.shown() === "one.pdf", "and Right works again with the dialog gone");
	world.pane().querySelector(".attachment-preview-close").click();
	check(!world.keysBound(), "unbound when the pane closes");
	world.keydown("ArrowRight");
	check(!world.isOpen(), "a key after the close opens nothing");
	// The router removed the pane element (formview.show_doc) without a hide.
	world.open(FILES[5]);
	world.pane().remove();
	event = world.keydown("ArrowRight");
	check(!world.keysBound() && !event.prevented, "a key with the pane gone from the document unbinds itself, unprevented");
	check(world.log[world.log.length - 1] === "show f1", "and shows nothing");
}
{
	// The form's page hidden under another page (a list, a report, a workspace) with the pane
	// element exactly as it was: the key must not be taken, and the handler lets go.
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]);
	world.page.style.display = "none";
	const event = world.keydown("ArrowRight");
	check(!event.prevented, "a Right on a hidden form page is not prevented");
	check(world.log[world.log.length - 1] === "show f1", "and steps nowhere");
	check(!world.keysBound(), "and the keys are unbound by that press");
	world.page.style.display = "";
	world.keydown("ArrowRight");
	check(world.log[world.log.length - 1] === "show f1", "nothing comes back on its own once released");
}

console.log("7. no history entry, no popstate listener");
{
	const world = makeWorld({ files: FILES });
	world.run();
	check((world.windowListeners.popstate || []).length === 1, "the only popstate listener is frappe's");
	world.make();
	world.open(FILES[5]);
	world.pane().querySelector(".attachment-preview-next").click();
	world.keydown("ArrowRight");
	world.open(FILES[0]); // a row clicked while the pane is open
	world.pane().querySelector(".attachment-preview-close").click();
	world.open(FILES[5]);
	world.keydown("Escape");
	check(!world.isOpen(), "Escape closed the pane through frappe's handler");
	check(world.historyUntouched(), "opening, stepping and closing pushed, replaced and popped nothing");
	const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
	check(!/pushState|replaceState|history\.back|history\.go|popstate/.test(code), "the source never touches history or popstate");
}

console.log("8. leaving the form for a list closes the pane and releases the keys");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.open(FILES[5]);
	world.go("list");
	check(world.page.style.display === "none", "the form's page is hidden, its DOM left as it was");
	check(world.log[world.log.length - 1] === "hide", "the router's change closed the pane through the original hide");
	check(!world.isOpen() && !world.keysBound(), "pane closed, keys unbound");
	const event = world.keydown("ArrowRight");
	check(!event.prevented && world.log[world.log.length - 1] === "hide", "a Right on the list is left alone");
	// Back to the form: frappe's popstate listener re-renders it (show_doc teardown, new Sidebar).
	world.back();
	check(world.routes === 2 && world.page.style.display === "", "frappe re-rendered the form");
	check(!world.keysBound() && !world.isOpen(), "nothing bound, nothing open");
	check(world.historyUntouched(), "and the history untouched throughout");
	world.open(FILES[4]);
	check(world.counter() === "T:2 of 5" && world.keysBound(), "the rebuilt instance opens and binds");
}

console.log("9. the user's Back with the pane open: frappe re-renders, the old instance is released");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	world.route_stack.push("form"); // the entry before this form is another form
	const old = world.attachments;
	world.open(FILES[5]);
	world.keydown("ArrowRight");
	world.back();
	check(world.routes === 1, "frappe's popstate listener ran route()");
	check(world.attachments !== old && world.document.body.contains(world.pane()), "a new Attachments was built with a fresh pane");
	check(!world.document.body.contains(old.attachment_preview[0]), "the old pane element is gone");
	check(!world.keysBound(), "the keys were released");
	const event = world.keydown("ArrowRight");
	check(!event.prevented && world.log[world.log.length - 1] === "show f2", "a Right afterwards does nothing");
	check(world.historyUntouched(), "nothing was pushed or popped by the module");
	world.open(FILES[5]);
	check(world.isOpen() && world.counter() === "T:1 of 5" && world.keysBound(), "the new instance opens cleanly");
}

console.log("10. a sidebar rebuild with the pane open releases the old instance");
{
	const world = makeWorld({ files: FILES });
	world.run();
	world.make();
	const old = world.attachments;
	world.open(FILES[5]);
	check(world.keysBound(), "bound");
	// Save / frm.refresh() / reload_doc: render_form builds a new Sidebar, which empties
	// page.sidebar and constructs a new Attachments; no hide runs.
	world.rebuild_sidebar();
	check(!world.document.body.contains(old.attachment_preview[0]), "the old pane was deleted without a hide");
	check(world.log.indexOf("hide") === -1, "(no hide ran)");
	check(!world.keysBound(), "setup_preview_area on the new instance released the keys");
	let event = world.keydown("ArrowRight");
	check(!event.prevented && world.log[world.log.length - 1] === "show f1", "a Right afterwards does nothing");
	check(!world.isOpen(), "the new pane is closed");
	world.open(FILES[4]);
	check(world.counter() === "T:2 of 5" && world.keysBound(), "the new instance opens with its counter");
	// The refresh wrapper on the OLD instance (a late realtime callback) touches nothing visible.
	old.refresh();
	check(world.counter() === "T:2 of 5", "a refresh on the dead instance changes nothing");
	check(world.historyUntouched(), "history untouched");
}

console.log("11. the list is read on every step");
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
	// Everything deleted: the refresh takes the arrows away, and a Right on the empty list closes.
	world.docinfo.attachments = [];
	world.attachments.refresh();
	check(world.pane().querySelector(".attachment-preview-nav") === null, "an emptied list has no arrows");
	world.keydown("ArrowRight");
	check(!world.isOpen(), "and a Right on it closes the pane");
	check(!world.keysBound(), "and unbinds the keys");
	// Down to one file while open: refresh takes the arrows away.
	world.docinfo.attachments = [FILES[0]];
	world.attachments.refresh();
	world.open(FILES[0]);
	check(world.pane().querySelector(".attachment-preview-nav") === null, "one file: no arrows");
	world.docinfo.attachments = FILES.slice(0, 2);
	world.attachments.refresh();
	check(world.pane().querySelector(".attachment-preview-nav") !== null, "a second file: arrows appear on refresh");
	world.docinfo.attachments = [FILES[0]];
	world.attachments.refresh();
	check(world.pane().querySelector(".attachment-preview-nav") === null, "back to one: gone again");
	check(world.historyUntouched(), "history untouched");
}

console.log("12. an older frappe is left alone, and a second evaluation installs nothing twice");
function untouched(world, label) {
	const proto = world.frappe.ui.form.Attachments.prototype;
	const hide = proto.hide_attachment_preview;
	const refresh = proto.refresh;
	const setup = proto.setup_preview_area;
	const show = proto.show_attachment_preview;
	world.run();
	check(
		proto.hide_attachment_preview === hide &&
			proto.refresh === refresh &&
			proto.setup_preview_area === setup &&
			proto.show_attachment_preview === show,
		label + ": no method wrapped"
	);
	check(!proto.__ee_preview_nav, label + ": no marker");
	check(!world.frappe.router.handlers.change, label + ": no router handler");
}
untouched(makeWorld({ files: FILES, withShow: false }), "no show_attachment_preview");
untouched(makeWorld({ files: FILES, withSetup: false }), "no setup_preview_area");
untouched(makeWorld({ files: FILES, withButton: false }), "no frappe.ui.button.html");
{
	const world = makeWorld({ files: FILES });
	delete world.frappe.utils.escape_html;
	untouched(world, "no frappe.utils.escape_html");
}
{
	const world = makeWorld({ files: FILES });
	world.run();
	const once = world.frappe.ui.form.Attachments.prototype.show_attachment_preview;
	world.run();
	check(world.frappe.ui.form.Attachments.prototype.show_attachment_preview === once, "a second evaluation keeps the first wrap");
	world.make();
	world.open(FILES[5]);
	world.open(FILES[4]);
	check((world.frappe.router.handlers.change || []).length === 1, "one router change handler, bound on the first open");
	check((world.windowListeners.popstate || []).length === 1, "and still only frappe's popstate listener");
}

if (failures) {
	console.error("\n" + failures + " check(s) failed");
	process.exit(1);
}
console.log("\nall checks passed");
