/*
 * The reorder cells' DOM (v1.720.0): wiring a root's boxes to a ReorderEditor, and painting an item's
 * entry and status into its cell without disturbing a box the person is typing in. Plain DOM only
 * (no jQuery), so the node harness can run it over a model of the few calls it makes.
 *
 * A cell is `<div class="ro" data-ro-item="CODE">` holding `input[data-ro-field="level"|"qty"]` and
 * `.ro-status` (editor.cell_html). One delegated listener per root, however often it is redrawn.
 */

import { cell_html } from "./editor.js";
import { number_text } from "./logic.js";

const FIELDS = ["level", "qty"];

function cell_of(node) {
	for (let el = node; el; el = el.parentNode) {
		if (el.getAttribute && el.getAttribute("data-ro-item") !== null && el.getAttribute("data-ro-item") !== undefined) return el;
	}
	return null;
}

function field_of(node) {
	const name = node && node.getAttribute ? node.getAttribute("data-ro-field") : null;
	return FIELDS.includes(name) ? name : null;
}

// `change` fires when a box loses focus having been edited, and on Enter in a text box: either way
// the two boxes are saved together (the editor drops a save that changes nothing). Enter also leaves
// the box, so the phone's keyboard closes on the saved value, and then runs the page's `after_enter`
// (if it gave one) to put focus where it wants it. Escape puts the boxes back.
export function bind(root, editor, opts) {
	const after_enter = (opts && opts.after_enter) || null;
	root.addEventListener("change", (event) => {
		const cell = field_of(event.target) && cell_of(event.target);
		if (cell) editor.commit(cell.getAttribute("data-ro-item"));
	});
	root.addEventListener("keydown", (event) => {
		if (!field_of(event.target)) return;
		const cell = cell_of(event.target);
		if (!cell) return;
		if (event.key === "Enter") {
			event.preventDefault();
			// A box not yet "changed" (the person typed and pressed Enter in one go) fires change on blur.
			if (event.target.blur) event.target.blur();
			// Where the page wants the person next (the scanner's counted-quantity box): a blur alone drops
			// focus on the page, and the next wedge scan is typed into nothing.
			if (after_enter) after_enter(cell.getAttribute("data-ro-item"), event.target);
		} else if (event.key === "Escape") {
			editor.revert(cell.getAttribute("data-ro-item"));
		}
	});
}

// The text of one item's two boxes, for env.read.
export function read_boxes(root, item_code) {
	const out = { level: "", qty: "" };
	each_cell(root, item_code, (cell) => {
		FIELDS.forEach((name) => {
			const input = cell.querySelector(`[data-ro-field="${name}"]`);
			if (input) out[name] = input.value;
		});
	});
	return out;
}

function each_cell(root, item_code, fn) {
	const cells = root.querySelectorAll("[data-ro-item]");
	for (let i = 0; i < cells.length; i++) {
		if (!item_code || cells[i].getAttribute("data-ro-item") === item_code) fn(cells[i]);
	}
}

// Paint one item's cell (or every cell under `root`): a cell that has its boxes gets its values and
// its status line put in place, and a box with focus keeps what is typed in it; a cell in another
// state (not read yet, a rule it may not edit) is redrawn whole. `force` also resets a focused box
// (Escape). `active` is the focused element (document.activeElement).
export function paint(root, editor, esc, tr, item_code, active, force, labels) {
	each_cell(root, item_code, (cell) => {
		const code = cell.getAttribute("data-ro-item");
		const view = editor.view(code);
		const has_boxes = !!cell.querySelector("[data-ro-field]");
		const wants_boxes = view.ready && !view.error && view.editable && !!view.entry;
		if (!has_boxes || !wants_boxes) {
			cell.outerHTML = cell_html(esc, tr, view, code, labels && labels[code]);
			return;
		}
		const values = { level: number_text(view.entry.level), qty: number_text(view.entry.qty) };
		// A box is set only when the item's number is not the one it last showed: a refused save leaves
		// the item as it was, and the text typed into the box must stay to be corrected.
		FIELDS.forEach((name) => {
			const input = cell.querySelector(`[data-ro-field="${name}"]`);
			if (!input) return;
			const stale = input.getAttribute("data-ro-shown") !== values[name];
			if ((force || (stale && input !== active)) && (stale || input.value !== values[name])) {
				input.value = values[name];
				input.setAttribute("data-ro-shown", values[name]);
			}
		});
		const line = cell.querySelector(".ro-status");
		if (line) {
			const status = view.status;
			line.textContent = status ? status.message : "";
			line.className = status ? `ro-status ro-${status.state}` : "ro-status";
			line.setAttribute("role", status && status.state === "error" ? "alert" : "status");
		}
	});
}
