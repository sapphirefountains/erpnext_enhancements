/*
 * Planner kit: the hover card on a planner's chips (Nikolas, 2026-11-03: "on the popup when hovering
 * have times and other details"). A styled card in place of the browser's own tooltip: drawn after a
 * short delay from what the page says about the chip under the mouse, gone when the mouse leaves it,
 * on Esc, on a press (a drag or a right-click is starting), a scroll or a route change.
 *
 *   const hover = planner_kit.popover.attach(root, {
 *     selector: ".pp-card[data-task], .pp-fcard",   // the chips (closest() from the element under the mouse)
 *     delay: 350,                                   // ms before the card shows (default 350)
 *     describe: (el) => ({                          // null for a chip with nothing to say
 *       title: "Excavate basin",
 *       subtitle: "Riverwalk",
 *       tags: ["Pencil"],                           // short marks under the title
 *       rows: [["When", "Mon, Nov 3 – Thu, Nov 6"], ["Time", "8:00–12:00"], ["Crew", "Kyle Jones (lead), Ana Hill"]],
 *       warnings: ["Kyle Jones, Tue Nov 4: Over by 2h"],   // red lines (conflicts, missing qualifications)
 *       notes: ["Hours: 6h of 8h"],                        // plain lines
 *     }),
 *   });
 *   hover.close();  hover.destroy();
 *
 * A chip that leaves the document (the board is redrawn by innerHTML after a load, a drop, an undo)
 * takes its card with it: the pages close the card when they render, the kit closes it when a
 * MutationObserver sees the chip detached, the next mouse move over anything closes a card whose chip
 * is gone, and a card is never drawn for a detached chip. After a press on a chip (a drag, a menu, a
 * tap) no card is drawn for that chip until the mouse has left it.
 *
 * Mouse and pen only: on a touch screen the planners' hold-to-open quick look stays as it is. While the
 * card is up the chip's `title` is held aside (so the browser's tooltip does not show under the card)
 * and the chip says `aria-describedby` to the card; on close the title comes back, so the chip's
 * accessible description is what it was. One card at a time in the document. Every value is set as
 * text (popover_html escapes), so a task name cannot break out of the card.
 */

import { escape } from "./escape.js";

const ID = "pk-pop";

const text = (value) => (value == null ? "" : String(value));

// The card's markup from a description. Pure, for the tests.
export function popover_html(desc) {
	const d = desc || {};
	const parts = [];
	if (d.title) parts.push(`<div class="pk-pop-title">${escape(text(d.title))}</div>`);
	if (d.subtitle) parts.push(`<div class="pk-pop-sub">${escape(text(d.subtitle))}</div>`);
	const tags = (d.tags || []).map(text).filter(Boolean);
	if (tags.length) {
		parts.push(`<div class="pk-pop-tags">${tags.map((tag) => `<span class="pk-pop-tag">${escape(tag)}</span>`).join("")}</div>`);
	}
	const rows = (d.rows || []).filter((row) => row && text(row[1]));
	if (rows.length) {
		parts.push(
			`<dl class="pk-pop-rows">${rows
				.map((row) => `<dt>${escape(text(row[0]))}</dt><dd>${escape(text(row[1]))}</dd>`)
				.join("")}</dl>`
		);
	}
	const warnings = (d.warnings || []).map(text).filter(Boolean);
	if (warnings.length) {
		parts.push(`<ul class="pk-pop-warnings">${warnings.map((line) => `<li>${escape(line)}</li>`).join("")}</ul>`);
	}
	const notes = (d.notes || []).map(text).filter(Boolean);
	if (notes.length) {
		parts.push(`<ul class="pk-pop-notes">${notes.map((line) => `<li>${escape(line)}</li>`).join("")}</ul>`);
	}
	return parts.join("");
}

// Where the card goes: under the chip, aligned to its left edge, kept inside the viewport; above it
// when there is no room below. Pure: `rect` is the chip's, `box` the card's, `view` {w, h}.
export function place(rect, box, view) {
	const gap = 6;
	const width = box.width || 240;
	const height = box.height || 0;
	let x = rect.left;
	let y = rect.bottom + gap;
	x = Math.max(8, Math.min(x, view.w - width - 8));
	if (y + height > view.h - 8 && rect.top - height - gap >= 8) y = rect.top - height - gap;
	// Room neither below nor above (a tall card on a short window): as high as it must be, and never
	// above the top edge.
	if (y + height > view.h - 8) y = Math.max(8, view.h - height - 8);
	return { x: Math.round(x), y: Math.round(y) };
}

export function create_popover(env) {
	const doc = env.doc;
	const win = env.win;
	let current = null; // {el, card, title, had_title}
	let timer = null;

	function cancel() {
		if (timer != null) env.clear_timeout(timer);
		timer = null;
	}

	function close() {
		cancel();
		const item = current;
		if (!item) return;
		current = null;
		if (item.card.parentNode) item.card.parentNode.removeChild(item.card);
		if (item.el && item.el.isConnected !== false) {
			if (item.had_title) item.el.setAttribute("title", item.title);
			item.el.removeAttribute("aria-describedby");
		}
	}

	function dragging() {
		return /(^|\s)\w+-drag-active(\s|$)/.test((doc.body && doc.body.className) || "");
	}

	function show(el, desc) {
		close();
		if (!desc || dragging() || el.isConnected === false) return;
		const card = doc.createElement("div");
		card.className = "pk-pop";
		card.id = ID;
		card.setAttribute("role", "tooltip");
		card.innerHTML = popover_html(desc);
		doc.body.appendChild(card);
		const rect = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : { left: 8, top: 8, bottom: 8 };
		const box = typeof card.getBoundingClientRect === "function" ? card.getBoundingClientRect() : {};
		const at = place(rect, box, { w: Number(win.innerWidth) || 1024, h: Number(win.innerHeight) || 768 });
		card.style.left = `${at.x}px`;
		card.style.top = `${at.y}px`;
		const had_title = el.hasAttribute("title");
		current = { el, card, title: had_title ? el.getAttribute("title") : "", had_title };
		if (had_title) el.removeAttribute("title");
		el.setAttribute("aria-describedby", ID);
	}

	function attach(root, opts) {
		opts = opts || {};
		const selector = opts.selector || "[title]";
		const delay = Number(opts.delay) >= 0 ? Number(opts.delay) : 350;
		const describe = typeof opts.describe === "function" ? opts.describe : () => null;
		const chip_of = (node) => (node && typeof node.closest === "function" ? node.closest(selector) : null);
		const mouse = (e) => !e.pointerType || e.pointerType === "mouse" || e.pointerType === "pen";

		let suppressed = null; // a chip pressed on: no card until the mouse has left it
		const over = (e) => {
			if (!mouse(e)) return;
			// A card whose chip the last redraw took away goes on the next move over anything.
			if (current && current.el.isConnected === false) close();
			const el = chip_of(e.target);
			if (!el || el === suppressed) return;
			if (current && current.el === el) return;
			cancel();
			timer = env.set_timeout(() => {
				timer = null;
				let desc = null;
				try {
					desc = describe(el);
				} catch (err) {
					if (env.warn) env.warn(err);
				}
				show(el, desc);
			}, delay);
		};
		const out = (e) => {
			const el = chip_of(e.target);
			if (!el) return;
			// Moving between a chip's own parts is not leaving it.
			if (e.relatedTarget && typeof el.contains === "function" && el.contains(e.relatedTarget)) return;
			cancel();
			if (el === suppressed) suppressed = null;
			if (current && current.el === el) close();
		};
		const down = (e) => {
			suppressed = chip_of(e.target);
			close();
		};
		const key = (e) => {
			if (e.key === "Escape" && current) close();
		};
		const scroll = () => close();
		root.addEventListener("pointerover", over);
		root.addEventListener("pointerout", out);
		root.addEventListener("pointerdown", down, true);
		doc.addEventListener("keydown", key, true);
		doc.addEventListener("scroll", scroll, true);
		if (typeof env.on_route_change === "function") env.on_route_change(close);
		// The chip under the card leaving the document (a redraw) takes the card with it.
		let observer = null;
		if (typeof win.MutationObserver === "function" && typeof root.contains === "function") {
			observer = new win.MutationObserver(() => {
				if (current && current.el.isConnected === false) close();
			});
			observer.observe(root, { childList: true, subtree: true });
		}
		return {
			close,
			destroy() {
				close();
				if (observer) observer.disconnect();
				root.removeEventListener("pointerover", over);
				root.removeEventListener("pointerout", out);
				root.removeEventListener("pointerdown", down, true);
				doc.removeEventListener("keydown", key, true);
				doc.removeEventListener("scroll", scroll, true);
			},
		};
	}

	return {
		attach,
		close,
		current: () => (current ? current.card : null),
		html: popover_html,
		place,
	};
}
