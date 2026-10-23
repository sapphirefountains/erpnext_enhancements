/*
 * The reorder editor (v1.725.0): the state behind the reorder cells on an audit screen, and its
 * server calls, with no DOM. A page hands it an `env`, asks it for each item's cell
 * (`cell_html`), and wires its boxes (dom.js); the node harness hands it a model instead.
 *
 *   env.call(method, args, opts)   sf_kit.call(..., { quiet: true }): a promise of r.message that
 *                                  rejects { status, message, ... }
 *   env.read(item_code)            { level, qty }: the text now in the item's two boxes
 *   env.paint(item_code)           show the item's entry and status in its cell, leaving a box the
 *                                  person is typing in alone
 *   env.paint_all()               the same for every cell on screen (a read has landed)
 *   env.confirm(text, yes, no)     ask a yes/no question in a dialog (frappe.confirm): `yes` or `no` runs
 *                                  once. A dialog pushes no history entry.
 *   env.later(fn, ms)              setTimeout
 *   env.tr(text)                   translate
 *
 * NO HISTORY. Nothing here routes, and the cells are not screens: a save never pushes a history
 * entry, so Back and Forward are the page's own (the Bin Manager's bins, the count's sheets).
 *
 * ONE SAVE AT A TIME PER ITEM. The level box's blur and then the quantity box's blur are two saves
 * of the same item, each carrying the Item's `modified` the read gave; the second would be refused
 * as stale if it left before the first's answer brought the new stamp. So a commit while the item is
 * saving only marks it dirty, and the answer commits again from the boxes as they are then.
 */

import * as logic from "./logic.js";

const SAVED_SHOWN_MS = 2500;

export class ReorderEditor {
	constructor(env) {
		this.env = env;
		this.warehouse = "";
		this.entries = {};
		this.can_edit = false;
		this.ready = false; // a read has answered for this warehouse
		this.error = ""; // the read failed: the sentence, shown in place of the numbers
		this.status = {}; // item_code -> { state: "saving" | "saved" | "error" | "note", message }
		this.busy = {};
		this.dirty = {};
		this.asking = {}; // item_code -> a "Remove this reorder rule?" dialog is open
		this.saves = {}; // item_code -> how many saves have landed: a read that began before one must not undo it
		this.seq = 0;
	}

	tr(text) {
		return this.env.tr ? this.env.tr(text) : text;
	}

	// The cells' state for one item, for cell_html: nothing known yet, the read failed, or the entry.
	view(item_code) {
		const entry = this.entries[item_code] || null;
		return {
			ready: this.ready,
			error: this.error,
			entry,
			editable: this.can_edit && logic.has_place(entry),
			can_edit: this.can_edit,
			status: this.status[item_code] || null,
		};
	}

	// Read the rules of `codes` as the screen for `warehouse` shows them. A different warehouse starts
	// over; the same one keeps what is on screen until the answer lands. An answer for a read that has
	// been replaced is dropped. Never rejects: a failure is `error`, said where the numbers would be.
	load(warehouse, codes) {
		const where = warehouse || "";
		if (where !== this.warehouse) {
			this.warehouse = where;
			this.entries = {};
			this.status = {};
			this.busy = {};
			this.dirty = {};
			this.asking = {};
			this.saves = {};
			this.ready = false;
			this.error = "";
		}
		const seq = ++this.seq;
		const list = Array.from(new Set((codes || []).filter(Boolean)));
		const saves_then = Object.assign({}, this.saves);
		if (!list.length) {
			this.ready = true;
			this.env.paint_all();
			return Promise.resolve();
		}
		return this.env
			.call(logic.READ, { warehouse: where, item_codes: JSON.stringify(list) }, { quiet: true })
			.then(
				(out) => {
					if (seq !== this.seq) return;
					const levels = (out && out.levels) || {};
					// An item mid-save keeps the entry its save is about to replace.
					list.forEach((code) => {
						// Not an item mid-save, and not one saved since this read began: its answer is older than ours.
						if (!this.busy[code] && (this.saves[code] || 0) === (saves_then[code] || 0)) {
							if (levels[code]) this.entries[code] = levels[code];
							else delete this.entries[code];
						}
					});
					this.can_edit = !!(out && out.can_edit);
					this.ready = true;
					this.error = "";
					this.env.paint_all();
				},
				(err) => {
					if (seq !== this.seq) return;
					this.ready = true;
					this.error = logic.error_text(err, this.tr("Reorder levels could not be loaded."));
					this.env.paint_all();
				}
			);
	}

	set_status(item_code, state, message) {
		if (state) this.status[item_code] = { state, message: message || "" };
		else delete this.status[item_code];
		this.env.paint(item_code);
	}

	// A box changed (blur or Enter): do what the two boxes now say, if that is a change: save it, say
	// what is missing, or (a box of an existing rule cleared) ask whether to remove the rule.
	commit(item_code) {
		const entry = this.entries[item_code];
		if (!entry || !this.can_edit || !logic.has_place(entry)) return Promise.resolve();
		if (this.busy[item_code]) {
			this.dirty[item_code] = true;
			return Promise.resolve();
		}
		if (this.asking[item_code]) return Promise.resolve();
		const boxes = this.env.read(item_code) || {};
		const plan = logic.plan_save(entry, boxes.level, boxes.qty, (s) => this.tr(s));
		if (plan.kind === "none") {
			if (this.status[item_code]) this.set_status(item_code, null);
			return Promise.resolve();
		}
		if (plan.kind === "invalid") {
			this.set_status(item_code, "error", plan.message);
			return Promise.resolve();
		}
		if (plan.kind === "incomplete") {
			this.set_status(item_code, "note", plan.message);
			return Promise.resolve();
		}
		if (plan.kind === "remove") return this.ask_remove(item_code);
		return this.send(item_code, logic.save_request(entry, this.warehouse, plan), this.tr("Saved."));
	}

	// "Remove this reorder rule?" Yes removes it; No (or closing the dialog) puts the boxes back.
	ask_remove(item_code) {
		this.asking[item_code] = true;
		return new Promise((resolve) => {
			const done = (go) => {
				if (!this.asking[item_code]) return;
				delete this.asking[item_code];
				const entry = this.entries[item_code];
				if (go && entry) resolve(this.send(item_code, logic.remove_request(entry, this.warehouse), this.tr("Removed.")));
				else {
					this.revert(item_code);
					resolve();
				}
			};
			this.env.confirm(this.tr(logic.REMOVE_ASK), () => done(true), () => done(false));
		});
	}

	// One write for an item, on its own, with the status line telling how it went.
	send(item_code, req, saved_text) {
		this.busy[item_code] = true;
		this.set_status(item_code, "saving", this.tr("Saving…"));
		return this.env.call(req.method, req.args, { quiet: true }).then(
			(out) => {
				if (out && out.entry) this.entries[item_code] = out.entry;
				this.saves[item_code] = (this.saves[item_code] || 0) + 1;
				this.finish(item_code);
				this.set_status(item_code, "saved", (out && out.saved) === false ? this.tr("No change.") : saved_text);
				this.env.later(() => {
					const now = this.status[item_code];
					if (now && now.state === "saved") this.set_status(item_code, null);
				}, SAVED_SHOWN_MS);
				return this.rerun(item_code);
			},
			(err) => {
				this.finish(item_code);
				const message = logic.error_text(err, this.tr("Could not save."));
				this.dirty[item_code] = false;
				if (logic.is_conflict(err)) {
					// Someone changed the item: show their numbers, and say so.
					return this.reload_one(item_code).then(() => this.set_status(item_code, "error", message));
				}
				this.set_status(item_code, "error", message);
				return undefined;
			}
		);
	}

	finish(item_code) {
		delete this.busy[item_code];
	}

	// Edits made while the save was out: save them now, from the boxes as they are.
	rerun(item_code) {
		if (!this.dirty[item_code]) return Promise.resolve();
		this.dirty[item_code] = false;
		return this.commit(item_code);
	}

	reload_one(item_code) {
		const seq = this.seq;
		return this.env
			.call(logic.READ, { warehouse: this.warehouse, item_codes: JSON.stringify([item_code]) }, { quiet: true })
			.then(
				(out) => {
					if (seq !== this.seq) return;
					const levels = (out && out.levels) || {};
					if (levels[item_code]) this.entries[item_code] = levels[item_code];
					this.env.paint(item_code);
				},
				() => {
					// The sentence about the conflict is on screen; the numbers stay as they were.
				}
			);
	}

	// Escape: put the boxes back to what the item says.
	revert(item_code) {
		if (this.status[item_code] && this.status[item_code].state !== "saving") this.set_status(item_code, null);
		this.env.paint(item_code, true);
	}
}

// The cell for one item, as markup: two boxes (or plain text where the person may not edit), the
// warehouse the rule belongs to when it is not the screen's, and a status line. `esc` escapes text for
// HTML; `view` is ReorderEditor.view; `label` is the item's name, for the boxes' accessible names.
export function cell_html(esc, tr, view, item_code, label) {
	const say = tr || ((s) => s);
	const code = esc(item_code);
	const wrap = (inner) => `<div class="ro" data-ro-item="${code}">${inner}</div>`;
	if (!view.ready) return wrap(`<span class="ro-muted">${esc(say("Reorder: …"))}</span>`);
	if (view.error) return wrap(`<span class="ro-muted" title="${esc(view.error)}">${esc(say("Reorder: not available"))}</span>`);
	const entry = view.entry;
	if (!entry) return wrap("");
	const level = logic.number_text(entry.level);
	const qty = logic.number_text(entry.qty);
	const where = entry.elsewhere
		? `<span class="ro-where">${esc(say("Rule is on {0}").replace("{0}", entry.warehouse_label || entry.row_warehouse || ""))}</span>`
		: "";
	const status = view.status
		? `<span class="ro-status ro-${esc(view.status.state)}" role="${view.status.state === "error" ? "alert" : "status"}">${esc(view.status.message)}</span>`
		: `<span class="ro-status" role="status"></span>`;
	if (!view.editable) {
		const text = entry.has_rule
			? say("Reorder at {0}, buy {1}").replace("{0}", level).replace("{1}", qty)
			: say("No reorder rule");
		return wrap(`<span class="ro-text">${esc(text)}</span>${where}`);
	}
	const name = label || item_code;
	const box = (field, caption, value) =>
		`<label class="ro-field"><span class="ro-label">${esc(caption)}</span>` +
		`<input type="text" inputmode="decimal" autocomplete="off" class="ro-input" data-ro-field="${field}" ` +
		`value="${esc(value)}" data-ro-shown="${esc(value)}" placeholder="${esc(say("none"))}" ` +
		`aria-label="${esc(caption + ": " + name)}"></label>`;
	return wrap(
		`<div class="ro-boxes">${box("level", say("Reorder at"), level)}${box("qty", say("Buy"), qty)}</div>` + where + status
	);
}
