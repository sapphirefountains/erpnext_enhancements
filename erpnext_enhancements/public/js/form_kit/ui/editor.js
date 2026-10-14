/*
 * form kit P4.4: the record editor both shells run (ERPNext TASK-2026-02762).
 *
 * One renderer, one save queue, one conflict chooser and one offline journal, shared by the guided
 * page (enhancements_core/page/guided_entry) and the side panel (./side_panel.js, the planner kit's
 * drawer over a list or board). Everything here was the guided page's own code until v1.675.0; it
 * moved here unchanged except where a second shell needed a seam, each marked "P4.4" below.
 *
 * What it owns, for one record at a time:
 *   - the flow (get_flow), the values on screen and as saved, each field's definition
 *   - one step's fields drawn in a frappe.ui.FieldGroup (render_fields), the field help, the
 *     text-list table, depends_on evaluated against the whole record
 *   - the save queue (../save_queue.js) and its conflict chooser (../conflict.js)
 *   - the phone's copy of anything unsent (../journal.js, P4.7), and a create that waits for signal
 *   - step 1's create (create_record, with the create's client_id), Set as Lost, `distinct`, `clears`
 *   - the Party entry component (./party.js, P3.2, v1.677.0) on the account, contact and address
 *     fields: "+ New" inline and linked to the account, re-suggested when the account changes
 *   - a DocType's step blocks (form kit P8.1, ../extensions.js; the Item flow's name builder and its
 *     supplier, stock and asset blocks): mounted above a step's fields when the step names an
 *     `extension`, told of each change, asked before a create; a block that throws stays in its box
 *
 * What it asks of the shell that hosts it (the "host" methods; the guided page and the side panel
 * each define their own):
 *   showing()                     is this shell on screen now
 *   load(doctype, name, step)     open a record (or a new one, name null) and draw it
 *   refresh_chrome()              redraw whatever follows the values (rail, summary, saving line)
 *   after_create(info)            step 1 made the record: { doctype, created, values, started_with,
 *                                 still_here }; return nothing
 *   show_created(doctype, name)   a kept start turned out to exist on the server: open it
 *   still_new(doctype)            is the shell still on the "new" screen of this DocType
 * and the DOM it draws into, under this.$root: .ge-notices, .ge-conflict-slot, .ge-saveline. A
 * shell's load() also calls open_extension(doctype) (P8.1), so a step that names a block can draw
 * it, and its reset() drops it (leave_extension, then this.ext = null).
 *
 * Two ways in:
 *   - class SidePanel extends RecordEditor (an ES module: ./side_panel.js)
 *   - mix_editor(GuidedEntry.prototype) for the guided page, a plain page script that cannot
 *     import: its class is defined before the bundle has loaded, so the bundle copies these methods
 *     onto it once it arrives, where the page does not define its own. Same functions, same `this`.
 *
 * Runs in the browser only (it draws with frappe.ui and jQuery). The decisions it makes are the
 * pure modules beside it in public/js/form_kit/, which tests/test_guided_entry.py runs under node.
 */

import { PartyEntry } from "./party.js";

export const GE_API = "erpnext_enhancements.form_kit.api.";
export const GE_LIST_SUFFIX = "__ge_list";
const GE_INFO_ICON =
	'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true" focusable="false">' +
	'<circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.4"/>' +
	'<circle cx="8" cy="4.7" r="0.95" fill="currentColor"/>' +
	'<rect x="7.2" y="6.8" width="1.6" height="5" rx="0.8" fill="currentColor"/></svg>';

export function ge_clone(value) {
	return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

export function ge_has(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

// localStorage, which a private window or blocked site data can refuse: every read and write is
// guarded, and a refused write reports false so the page warns before the tab closes.
export function ge_storage() {
	try {
		return window.localStorage || null;
	} catch (e) {
		return null;
	}
}

export function ge_read_kept(key) {
	const store = ge_storage();
	if (!store) return null;
	try {
		return JSON.parse(store.getItem(key) || "null");
	} catch (e) {
		return null;
	}
}

// The signed-in user: journal keys carry it, so a shared phone never mixes two people's copies.
export function ge_user() {
	return (frappe.session && frappe.session.user) || "";
}

// Every stored key that starts with `prefix`.
export function ge_keys(prefix) {
	const store = ge_storage();
	const out = [];
	if (!store) return out;
	try {
		for (let i = 0; i < store.length; i += 1) {
			const key = store.key(i);
			if (key && key.startsWith(prefix)) out.push(key);
		}
	} catch (e) {
		// Unreadable storage: nothing to find.
	}
	return out;
}

// Keep `entry` under `key`, or forget it (null). True when the phone holds what it should.
export function ge_write_kept(key, entry) {
	const store = ge_storage();
	if (!store) return !entry;
	try {
		if (entry) store.setItem(key, JSON.stringify(entry));
		else store.removeItem(key);
		return true;
	} catch (e) {
		return false;
	}
}

// The id of one create (saver.create_record's client_id): a UUID where the browser has one.
export function ge_client_id() {
	try {
		if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
	} catch (e) {
		// Falls through to the timestamp-and-random id.
	}
	return `ge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export class RecordEditor {
	// ------------------------------------------------------------------ host defaults (P4.4)
	// A shell overrides these; mix_editor never copies one over a method the page already has.

	showing() {
		return false;
	}

	refresh_chrome() {
		this.render_saveline();
	}

	after_create() {}

	show_created() {}

	still_new() {
		return false;
	}

	// v16's own login path. In a new tab, so nothing typed here is lost: the Desk that boots there
	// broadcasts its new CSRF token to this tab (desk.js setup_broadcast_listeners). P4.4: here rather
	// than on the guided page, so the side panel's "Sign in" (refresh_notices) works too.
	open_login() {
		const here = window.location.pathname + window.location.search;
		window.open(`/login?redirect-to=${encodeURIComponent(here)}`, "_blank", "noopener");
	}

	// The step whose fields are on screen. A shell that draws fields the flow has in no single step
	// (the side panel's required fields on a move, its quick add) sets `step_override`.
	current_step() {
		if (this.step_override) return this.step_override;
		return this.flow ? this.kit.find_step(this.flow.steps, this.step_key) : null;
	}

	// The record as the server now has it, after what is queued has gone.
	reload_record() {
		if (!this.doctype || !this.name || this.is_new) return;
		const doctype = this.doctype;
		const name = this.name;
		const step = this.step_key;
		this.save_before_moving()
			.catch(() => {})
			.then(() => {
				// What did not reach the server stays on screen; its next save settles it (a conflict
				// if the record moved on), rather than a reload dropping it.
				if (this.queue && this.queue.has_unsaved()) return;
				if (this.doctype === doctype && this.name === name) this.load(doctype, name, step);
			});
	}

	// The form's Set as Lost: choosing Lost asks for the reasons and runs the DocType's own method.
	// The reasons table is read-only in the flow, so a plain save of Lost could never pass.
	intercept_change(fieldname, value) {
		const lost = this.flow && this.flow.lost;
		if (!lost || this.is_new || fieldname !== lost.status_field) return false;
		if (value !== lost.status || this.saved[fieldname] === lost.status) return false;
		this.show_value(fieldname, this.values[fieldname]);
		this.open_lost_dialog();
		return true;
	}

	open_lost_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Set as Lost"),
			fields: [{ fieldtype: "HTML", fieldname: "error" }].concat(this.lost_fields()),
			primary_action_label: __("Declare Lost"),
			primary_action: (values) => this.declare_lost(dialog, values || {}),
		});
		dialog.show();
	}

	// What Set as Lost asks for, as the Desk form's dialog asks it. P4.4: the side panel draws the
	// same three fields when a card is dropped on Lost.
	lost_fields() {
		return this.kit.lost_fields(this.flow.lost, __);
	}

	declare_lost(dialog, values) {
		const doctype = this.doctype;
		const name = this.name;
		const $error = dialog.fields_dict.error.$wrapper.empty();
		const refuse = (message) => {
			dialog.enable_primary_action();
			$('<p class="ge-lost-error" role="alert"></p>').text(message).appendTo($error);
		};
		dialog.disable_primary_action();
		this.send_lost(values)
			.then((result) => {
				dialog.hide();
				if (result && result.conflict) {
					this.sf.toast(__("Someone else changed this record, so it has been reloaded. Choose Lost again."), {
						tone: "warning",
						timeout: 8000,
					});
				} else {
					this.sf.toast(__("{0} was declared lost.", [name]), { tone: "success", timeout: 4000 });
				}
				if (this.doctype === doctype && this.name === name) this.load(doctype, name, this.step_key);
			})
			.catch((err) => refuse((err && err.message) || __("It could not be declared lost.")));
	}

	// The record's own Declare Lost (the config's `lost`), locked on `modified`: resolves the save
	// result or the conflict shape, rejects with { message } when it was refused or not sent.
	send_lost(values) {
		const v = values || {};
		return this.save_before_moving().then(() => {
			// A change that has not reached the server would be lost behind the new version.
			if (this.queue && this.queue.has_unsaved()) {
				throw { message: __("Your last change has not saved yet. Try again in a moment.") };
			}
			return this.sf.call(
				GE_API + "declare_lost",
				{
					doctype: this.doctype,
					name: this.name,
					reasons: v.reasons || [],
					competitors: v.competitors || [],
					detail: v.detail || "",
					modified: this.queue ? this.queue.modified : this.flow.modified,
				},
				{ quiet: true }
			);
		});
	}

	// Fields that should not share a value (the config's `distinct`): said, never blocked.
	distinct_warnings() {
		return ((this.flow && this.flow.distinct) || [])
			.filter((rule) => {
				const seen = rule.fields.map((fieldname) => this.values[fieldname]).filter((value) => value !== null && value !== undefined && String(value).trim() !== "");
				return new Set(seen.map(String)).size < seen.length;
			})
			.map((rule) => rule.message);
	}

	// A Table MultiSelect reads its link column from the child's meta (frappe.get_meta), which the
	// Desk has only after the DocType has been opened. Loaded once, only when missing.
	ensure_meta(doctype) {
		if (locals.DocType && locals.DocType[doctype]) return Promise.resolve();
		return new Promise((resolve) => {
			try {
				frappe.model.with_doctype(doctype, () => resolve());
			} catch (e) {
				resolve();
			}
		});
	}

	take_flow(flow) {
		this.flow = flow;
		this.doctype = flow.doctype;
		this.name = flow.name || null;
		this.is_new = !flow.name;
		this.values = ge_clone(flow.values || {});
		this.saved = ge_clone(flow.values || {});
		this.completeness = flow.completeness || null;
		this.defs = {};
		(flow.steps || []).forEach((step) =>
			(step.fields || []).forEach((field) => {
				if (field.fieldname && !this.kit.is_layout(field)) this.defs[field.fieldname] = field;
			})
		);
		if (this.queue && this.queue.has_unsaved()) this.retired.push(this.queue);
		this.queue = this.is_new ? null : this.make_queue(flow.modified);
		if (this.is_new && !this.client_id) this.client_id = ge_client_id();
		this.party_entry().reset();
	}

	// P3.2: the Party entry component (./party.js), one per editor: the account, contact and
	// addresses picked from the account's own, made inline and linked to it, re-suggested when the
	// account changes.
	party_entry() {
		if (!this.party_ui) this.party_ui = new PartyEntry(this);
		return this.party_ui;
	}

	// The record is fixed when the queue is made: a queue still sending after the page has moved to
	// another record saves onto its own record, and its answers no longer touch the screen.
	make_queue(modified) {
		const doctype = this.doctype;
		const name = this.name;
		const current = () => this.queue === queue && this.name === name;
		const queue = this.kit.create_save_queue({
			delay: 2500,
			// P4.4: the side panel's required fields on a move send nothing until the move is committed.
			manual: !!this.hold_saves,
			send: (values, at) =>
				this.sf.call(GE_API + "save_step", { doctype, name, values, modified: at }, { quiet: true }),
			on_state: (state, error) => {
				if (!queue.manual) this.write_journal(doctype, name);
				if (state === "error" && this.kit.try_later(error) && !queue.manual) this.wait_for_signal();
				if (current()) this.on_save_state(state, error);
			},
			on_saved: (result, sent) => current() && this.on_saved(result, sent),
			on_conflict: (result, sent) => (current() ? this.on_conflict(result, sent) : Promise.resolve(null)),
			set_timeout: (fn, ms) => window.setTimeout(fn, ms),
			clear_timeout: (id) => window.clearTimeout(id),
		});
		queue.set_modified(modified);
		queue.record_key = this.record_key(doctype, name);
		queue.manual = !!this.hold_saves;
		return queue;
	}

	// One step's fields, drawn into $fields: the FieldGroup and the values it starts with, the text
	// lists, the help, and the grids' listeners. P4.4: the guided page and the side panel both draw
	// a step with this; each draws its own head around it.
	render_fields(step, $fields, editable) {
		// P8.1: whatever block the last step drew is going away with it.
		this.leave_extension();
		const dfs = (step.fields || []).map((field) => this.control_def(field, step, editable));
		this.fg = new frappe.ui.FieldGroup({ fields: dfs, body: $fields[0], no_submit_on_enter: true });
		this.fg.make();
		const fg = this.fg;
		const initial = {};
		(step.fields || []).forEach((field) => {
			if (this.kit.is_layout(field) || this.kit.text_list_column(field)) return;
			if (field.fieldtype === "Table") return; // the grid was given its rows (df.data)
			if (ge_has(this.values, field.fieldname)) {
				initial[field.fieldname] = ge_clone(this.values[field.fieldname]);
				this.suppress.add(field.fieldname);
			}
		});
		fg.set_values(initial).then(() => {
			Object.keys(initial).forEach((fieldname) => this.suppress.delete(fieldname));
			if (this.fg === fg) fg.refresh_dependency();
		});
		(step.fields || []).forEach((field) => {
			if (this.kit.text_list_column(field)) this.render_text_list(field, editable && !field.read_only);
		});
		this.decorate_controls(step);
		this.party_entry().decorate(step, editable);
		this.mount_extension(step, $fields);
		// A grid says nothing when a row is removed or moved, and a cell edit may not reach the
		// table's own onchange: read the grids again after either (delete_rows runs in 0.1 s steps).
		$fields.on("click", ".grid-remove-rows, .grid-delete-row, .grid-add-row, .grid-move-row", () =>
			setTimeout(() => this.collect_tables(), 350)
		);
		$fields.on("change", ".form-grid input, .form-grid textarea, .form-grid select", () =>
			setTimeout(() => this.collect_tables(), 0)
		);
	}

	render_saveline() {
		const $line = this.$root.find(".ge-saveline");
		if (!$line.length) return;
		let text = "";
		let tone = "saved";
		let retry = false;
		if (this.is_new && this.notice && this.notice.kind === "create-offline" && !this.creating) {
			text = __("Saved on this phone — created when there is signal");
			tone = "offline";
		} else if (this.is_new) {
			text = this.creating ? __("Saving…") : __("Not saved yet");
			tone = this.creating ? "saving" : "dirty";
		} else if (!this.flow.can_write) {
			text = __("Read only");
		} else {
			const state = this.queue ? this.queue.state : "saved";
			tone = state;
			if (state === "saved") text = __("Saved");
			else if (state === "dirty" || state === "saving") text = __("Saving…");
			else if (state === "conflict") text = __("Not saved — choose above");
			else if (state === "error" && this.kit.try_later(this.queue.error) && this.kept_on_phone()) {
				// No signal, and the phone holds it: it is safe, just not sent.
				text = __("Saved on this phone — sends when there is signal");
				tone = "offline";
				retry = true;
			} else {
				text = __("Not saved — retry");
				retry = true;
			}
		}
		$line
			.text(text)
			.attr("class", `ge-saveline ge-saveline--${tone}`)
			.prop("disabled", !retry)
			.attr("aria-label", retry ? __("Not saved. Retry saving.") : text);
	}

	control_def(field, step, editable) {
		const kit = this.kit;
		const df = Object.assign({}, field);
		delete df.child_fields;
		delete df.hint;
		delete df.help;
		delete df.create;
		delete df.anchor;
		// FieldGroup.make() writes a field's `default` over the values it is given on a new record;
		// the defaults are already in get_flow's values.
		delete df.default;
		["depends_on", "mandatory_depends_on", "read_only_depends_on"].forEach((key) => {
			if (df[key]) df[key] = this.condition(df[key]);
		});
		if (kit.is_layout(field)) return df;

		const read_only = !editable || !!field.read_only;
		if (read_only) df.read_only = 1;
		df.parent = this.doctype;
		df.description = field.hint || "";
		df.ee_desc_inline = 1;

		if (kit.text_list_column(field)) {
			// Drawn by render_text_list into this placeholder.
			return {
				fieldtype: "HTML",
				fieldname: field.fieldname + GE_LIST_SUFFIX,
				depends_on: df.depends_on,
			};
		}
		if (field.fieldtype === "Table") {
			const fieldname = field.fieldname;
			df.fields = (field.child_fields || []).map((child) => {
				const out = Object.assign({}, child, {
					parent: field.options,
					description: child.hint || "",
					ee_desc_inline: 1,
				});
				delete out.hint;
				delete out.help;
				delete out.default;
				if (read_only) out.read_only = 1;
				else out.onchange = () => this.table_touched(fieldname);
				return out;
			});
			df.data = ge_clone(this.values[fieldname] || []);
			df.in_place_edit = true;
			df.cannot_add_rows = read_only;
			df.cannot_delete_rows = read_only;
			df.on_add_row = () => setTimeout(() => this.table_touched(fieldname), 0);
			return df;
		}
		if (field.fieldtype === "Dynamic Link") {
			df.get_options = () => this.current(field.options);
		}
		if (field.fieldtype === "Link" && kit.PARTY_QUERIES[field.options]) {
			// Only Contact and Address: any other Link keeps its own filters untouched.
			df.get_query = () => kit.link_query(field, kit.party_of(this.flow.steps, this.current_values())) || {};
		}
		df.onchange = () => this.field_changed(field.fieldname);
		return df;
	}

	// depends_on is written against the whole record (doc.status, doc.custom_value_stream ...),
	// but a FieldGroup only knows the fields of this one step: evaluate it against the record's
	// values with the step's live inputs laid over them.
	condition(expression) {
		if (typeof expression !== "string") return expression;
		return (step_values) => {
			const doc = Object.assign({}, this.values, step_values || {});
			try {
				if (expression.startsWith("eval:")) {
					return frappe.utils.eval(expression.slice(5), { doc, parent: doc });
				}
				const value = doc[expression];
				return Array.isArray(value) ? value.length > 0 : !!value;
			} catch (e) {
				// A condition that cannot be read shows the field: never hide one by accident.
				return true;
			}
		};
	}

	current(fieldname) {
		const control = this.fg && this.fg.fields_dict[fieldname];
		if (control && control.get_value) return control.get_value();
		return this.values[fieldname];
	}

	current_values() {
		return Object.assign({}, this.values);
	}

	// inputmode per field, the (i) beside each label that has longer help.
	decorate_controls(step) {
		const registry = frappe.boot && frappe.boot.sf_normalize;
		(step.fields || []).forEach((field) => {
			if (this.kit.is_layout(field)) return;
			const control = this.fg.fields_dict[field.fieldname];
			if (!control) return;
			const mode = this.kit.inputmode_for(control.df, registry, this.doctype);
			if (mode && control.$input && control.$input.length) control.$input.attr("inputmode", mode);
			if (field.help) this.add_help(control.$wrapper, field, step);
		});
	}

	add_help($wrapper, field, step) {
		if (!$wrapper || !$wrapper.length) return;
		// P4.4: the shell's own prefix, so the guided page and a side panel open over it never share an id.
		const id = `${this.id_prefix || "ge"}-help-${field.fieldname}`;
		const label = __(field.label || field.fieldname);
		const $button = $(`<button type="button" class="ge-help-btn" aria-expanded="false"></button>`)
			.attr("aria-controls", id)
			.attr("aria-label", __("More about {0}", [label]))
			.html(GE_INFO_ICON);
		const $panel = $('<div class="ge-help-panel" hidden></div>').attr("id", id);
		$('<p class="ge-help-text"></p>').text(field.help).appendTo($panel);
		const article = this.flow.help_article;
		if (article) {
			const href = `${frappe.utils.get_form_link("Knowledge Article", article)}#${encodeURIComponent(field.anchor || step.key)}`;
			$('<a class="ge-help-link"></a>').attr("href", href).text(__("Learn more")).appendTo($panel);
		}
		$button.on("click", (event) => {
			event.preventDefault();
			this.toggle_help($button, $button.attr("aria-expanded") !== "true");
		});
		// Beside the label, never inside it: set_label rewrites the label's innerHTML on refresh,
		// and a button inside a Check's <label> would tick the box.
		const $check_label = $wrapper.find(".checkbox label").first();
		const $label = $wrapper.find("label.control-label, .ge-list-label").first();
		if ($check_label.length) $check_label.after($button);
		else if ($label.length) $label.after($button);
		else $wrapper.prepend($button);
		const $group = $wrapper.find(".form-group").first();
		($group.length ? $group : $wrapper).append($panel);
	}

	toggle_help($button, open) {
		const $panel = this.$root.find(`#${$button.attr("aria-controls")}`);
		$button.attr("aria-expanded", open ? "true" : "false");
		$panel.prop("hidden", !open);
	}

	toggle_all_help(open) {
		this.$root.find(".ge-help-btn").each((i, button) => this.toggle_help($(button), open));
	}

	focus_field(fieldname) {
		if (!fieldname || !this.fg) return;
		const control = this.fg.fields_dict[fieldname] || this.fg.fields_dict[fieldname + GE_LIST_SUFFIX];
		if (!control || !control.$wrapper) return;
		const el = control.$wrapper[0];
		if (el && el.scrollIntoView) el.scrollIntoView({ block: "center" });
		const $input = control.$wrapper.find("input, textarea, select, button").filter(":visible").first();
		if ($input.length) $input.trigger("focus");
	}

	// ------------------------------------------------------------------ extensions (P8.1)

	// The DocType's extension (../extensions.js), fresh for each record a shell opens: null when it
	// has none (Opportunity). The shells call this from load(); the one it replaces is left first.
	open_extension(doctype) {
		this.leave_extension();
		this.ext = this.kit && this.kit.extension_for ? this.kit.extension_for(doctype) : null;
	}

	// The step's block, above its fields, when the config names one and the DocType's extension
	// draws it. Anything the block throws stays in the block: the fields below always show.
	mount_extension(step, $fields) {
		if (!this.ext || !step || !step.extension || typeof this.ext.render !== "function") return;
		const $box = $('<div class="ge-ext"></div>').attr("data-extension", step.extension).insertBefore($fields);
		try {
			this.ext.render(step.extension, $box, this.ext_context(step));
		} catch (e) {
			if (window.console) window.console.error("[Guided Entry] the step's block failed", e);
			$box.remove();
		}
	}

	leave_extension() {
		if (!this.ext || typeof this.ext.leave !== "function") return;
		try {
			this.ext.leave();
		} catch (e) {
			// A block that cannot tidy up has nothing left to say.
		}
	}

	ext_notify(fieldname, value) {
		if (!this.ext || typeof this.ext.on_change !== "function") return;
		try {
			this.ext.on_change(fieldname, value, this.ext_context(this.current_step()));
		} catch (e) {
			if (window.console) window.console.error("[Guided Entry] the step's block failed", e);
		}
	}

	// What the editor lends a block (../extensions.js documents it). The record is read when called,
	// never captured, so a block outliving a step cannot write to another record.
	ext_context(step) {
		const doctype = this.doctype;
		const name = this.name;
		const same = () => this.doctype === doctype && this.name === name;
		return {
			doctype,
			name,
			is_new: this.is_new,
			step,
			sf: this.sf,
			kit: this.kit,
			can_write: !!(this.flow && this.flow.can_write) && !(step && step.read_only),
			values: () => (same() ? Object.assign({}, this.values) : {}),
			set_value: (fieldname, value) => {
				if (!same() || this.kit.same_value(value, this.values[fieldname])) return;
				this.set_value_local(fieldname, value);
				this.show_value(fieldname, value);
			},
			save_first: () => (same() ? this.save_before_moving() : Promise.reject(new Error(__("The record changed.")))),
			record_changed: () => {
				if (same() && !this.is_new) this.refresh_from_server();
			},
		};
	}

	// ------------------------------------------------------------------ the text-list table

	render_text_list(field, editable) {
		const column = this.kit.text_list_column(field);
		const control = this.fg.fields_dict[field.fieldname + GE_LIST_SUFFIX];
		if (!control || !column) return;
		const rows = this.values[field.fieldname] || [];
		const label = __(field.label || field.fieldname);
		const list_id = `${this.id_prefix || "ge"}-list-${field.fieldname}`;
		const $box = $('<div class="ge-list" role="group"></div>').attr("aria-labelledby", `${list_id}-label`);
		$(`<div class="clearfix"><span class="control-label ge-list-label"></span></div>`)
			.find(".ge-list-label")
			.attr("id", `${list_id}-label`)
			.text(label)
			.end()
			.appendTo($box);
		if (field.hint) $('<p class="help-box small text-muted ge-list-hint"></p>').text(field.hint).appendTo($box);
		const $ol = $('<ol class="ge-list-rows"></ol>').appendTo($box);
		if (!rows.length) {
			$('<p class="ge-list-empty"></p>').text(editable ? __("Nothing added yet.") : __("None.")).appendTo($box);
		}
		rows.forEach((row, index) => {
			const $li = $('<li class="ge-list-row"></li>').attr("data-index", index).appendTo($ol);
			const number = __("{0} {1}", [label, index + 1]);
			if (!editable) {
				$('<p class="ge-list-text"></p>').text(row[column] || "").appendTo($li);
				return;
			}
			const $input = $('<textarea class="form-control ge-list-input" rows="2"></textarea>')
				.attr("aria-label", number)
				.val(row[column] || "")
				.appendTo($li);
			this.grow($input[0]);
			$input.on("input", () => {
				this.grow($input[0]);
				this.values[field.fieldname] = this.kit.set_row_text(this.values[field.fieldname], index, column, $input.val());
				this.list_changed(field);
			});
			const $tools = $('<div class="ge-list-tools"></div>').appendTo($li);
			const tool = (cls, text, aria, disabled, fn) =>
				$(`<button type="button" class="sf-btn sf-btn--quiet ge-list-tool ${cls}"></button>`)
					.text(text)
					.attr("aria-label", aria)
					.prop("disabled", disabled)
					.on("click", fn)
					.appendTo($tools);
			tool("ge-up", "↑", __("Move {0} up", [number]), index === 0, () => this.list_edit(field, "move", index, -1));
			tool("ge-down", "↓", __("Move {0} down", [number]), index === rows.length - 1, () =>
				this.list_edit(field, "move", index, 1)
			);
			tool("ge-remove", __("Remove"), __("Remove {0}", [number]), false, () => this.list_edit(field, "remove", index));
		});
		if (editable) {
			$('<button type="button" class="sf-btn sf-btn--secondary ge-list-add"></button>')
				.text(__("Add"))
				.attr("aria-label", __("Add to {0}", [label]))
				.on("click", () => this.list_edit(field, "add"))
				.appendTo($box);
		}
		$(control.wrapper).empty().append($box);
		if (field.help) this.add_help($(control.wrapper), field, this.current_step() || {});
	}

	grow(el) {
		if (!el) return;
		el.style.height = "auto";
		el.style.height = `${Math.max(el.scrollHeight, 88)}px`;
	}

	list_edit(field, action, index, dir) {
		const column = this.kit.text_list_column(field);
		const rows = this.values[field.fieldname] || [];
		let focus_index = index;
		let focus = ".ge-list-input";
		if (action === "add") {
			this.values[field.fieldname] = this.kit.add_row(rows, column, "");
			focus_index = rows.length;
		} else if (action === "remove") {
			this.values[field.fieldname] = this.kit.remove_row(rows, index);
			focus_index = Math.min(index, rows.length - 2);
		} else {
			this.values[field.fieldname] = this.kit.move_row(rows, index, dir);
			focus_index = index + dir;
			focus = dir < 0 ? ".ge-up" : ".ge-down";
		}
		this.render_text_list(field, true);
		this.list_changed(field);
		const control = this.fg && this.fg.fields_dict[field.fieldname + GE_LIST_SUFFIX];
		if (!control) return;
		const $row = $(control.wrapper).find(`.ge-list-row[data-index="${focus_index}"]`);
		let $target = $row.find(focus).first();
		if (!$target.length || $target.prop("disabled")) $target = $row.find(".ge-list-input").first();
		if (!$target.length) $target = $(control.wrapper).find(".ge-list-add");
		$target.trigger("focus");
	}

	list_changed(field) {
		const column = this.kit.text_list_column(field);
		this.set_value_local(field.fieldname, this.kit.clean_rows(this.values[field.fieldname], [column]), true);
	}

	// ------------------------------------------------------------------ values

	field_changed(fieldname) {
		if (!this.fg || this.suppress.has(fieldname)) return;
		const control = this.fg.fields_dict[fieldname];
		if (!control || !control.get_value) return;
		let value = control.get_value();
		if (value === undefined) value = null;
		const def = this.defs[fieldname];
		if (def && def.fieldtype === "Table MultiSelect") {
			value = this.kit.clean_rows(value, (def.child_fields || []).map((child) => child.fieldname));
		}
		if (this.intercept_change(fieldname, value)) return;
		const before = this.values[fieldname];
		const changed = !this.kit.same_value(value, before);
		this.set_value_local(fieldname, value);
		if (changed) {
			((this.flow.clears || {})[fieldname] || []).forEach((cleared) => {
				if (this.kit.same_value(this.values[cleared], null)) return;
				this.set_value_local(cleared, null);
				this.show_value(cleared, null);
			});
			this.refresh_distinct();
			this.party_entry().changed(fieldname, before, value);
			this.ext_notify(fieldname, value);
		}
		this.fg.refresh_dependency();
	}

	refresh_distinct() {
		this.refresh_notices();
	}

	table_touched(fieldname) {
		if (!this.fg) return;
		const control = this.fg.fields_dict[fieldname];
		const def = this.defs[fieldname];
		if (!control || !def || !control.get_value) return;
		const columns = (def.child_fields || []).map((child) => child.fieldname);
		this.set_value_local(fieldname, this.kit.clean_rows(control.get_value() || [], columns));
	}

	// Grids say nothing when a row is removed or moved: compare them with what is held.
	collect_tables() {
		if (!this.fg || !this.flow) return;
		const step = this.current_step();
		((step && step.fields) || []).forEach((field) => {
			if (field.fieldtype === "Table" && !field.read_only && !this.kit.text_list_column(field)) {
				this.table_touched(field.fieldname);
			}
		});
	}

	// Everything on the step into this.values (a Back or Forward has no tap to blur the field
	// being typed in, and some inputs commit on change), before the step goes away.
	collect_step() {
		if (!this.fg || !this.flow) return;
		const active = document.activeElement;
		if (active && this.$root[0].contains(active) && typeof active.blur === "function") active.blur();
		const step = this.current_step();
		((step && step.fields) || []).forEach((field) => {
			if (this.kit.is_layout(field) || field.read_only || this.kit.text_list_column(field)) return;
			if (field.fieldtype === "Table") return;
			this.field_changed(field.fieldname);
		});
		this.collect_tables();
	}

	// One field's new value on screen: queued when it differs from what is saved, dropped from
	// the queue when it is back to it. `quiet_chrome` skips the rail redraw while typing in a list.
	set_value_local(fieldname, value, quiet_chrome) {
		const before_active = this.kit.active_steps(this.flow.steps, this.values).map((s) => s.key).join();
		this.values[fieldname] = value;
		if (this.is_new) {
			this.new_dirty = true;
			this.write_new_journal();
		} else if (this.queue) {
			if (this.kit.same_value(value, this.saved[fieldname])) {
				this.queue.discard(fieldname);
			} else {
				this.note_base(fieldname);
				this.queue.change(fieldname, value);
			}
			this.write_journal(this.doctype, this.name);
		}
		const after_active = this.kit.active_steps(this.flow.steps, this.values).map((s) => s.key).join();
		if (!quiet_chrome || before_active !== after_active) this.refresh_chrome();
		else this.render_saveline();
	}

	// Put a value the server returned on screen, if its field is on this step.
	show_value(fieldname, value) {
		if (!this.fg) return;
		const def = this.defs[fieldname];
		const list = this.fg.fields_dict[fieldname + GE_LIST_SUFFIX];
		if (list) {
			const focused = list.wrapper && list.wrapper.contains(document.activeElement);
			if (!focused) this.render_text_list(def, !!this.flow.can_write && !def.read_only);
			return;
		}
		const control = this.fg.fields_dict[fieldname];
		if (!control) return;
		const focused = control.$wrapper && control.$wrapper[0].contains(document.activeElement);
		if (def && def.fieldtype === "Table") {
			if (focused) return;
			control.df.data = ge_clone(value || []);
			if (control.grid) control.grid.refresh();
			return;
		}
		if (focused && this.kit.same_value(control.get_value(), value)) return;
		this.suppress.add(fieldname);
		Promise.resolve(control.set_value(ge_clone(value))).then(
			() => this.suppress.delete(fieldname),
			() => this.suppress.delete(fieldname)
		);
	}

	// ------------------------------------------------------------------ no signal (P4.7)

	record_key(doctype, name) {
		return this.kit.journal_key(doctype, name, ge_user());
	}

	// The server's value from before this field's first unsent change: what reconcile compares with.
	note_base(fieldname) {
		const key = this.record_key(this.doctype, this.name);
		const bases = this.bases[key] || (this.bases[key] = {});
		if (!ge_has(bases, fieldname)) bases[fieldname] = ge_clone(this.saved[fieldname]);
	}

	// Keep on the phone everything not stored for this record, or forget it once all is stored. It is
	// the union of every queue still holding the record (one retired by a reload or Back keeps
	// sending), each its own: in flight, held by a conflict prompt, and typed since. Kept values that
	// wait on the person's choice (`held`) come first; the newest typing wins.
	write_journal(doctype, name) {
		if (!doctype || !name || !this.kit) return;
		const key = this.record_key(doctype, name);
		// P4.4: a move's required fields are not a change until the move is committed, so the phone
		// keeps none of them (put back later, they would commit a move nobody confirmed). The queue
		// says so itself (`manual`); this editor then leaves the record's entry exactly as it is.
		if (this.queue && this.queue.manual && this.queue.record_key === key) return;
		const held = this.held[key] || {};
		const unsent = {};
		const base = {};
		Object.keys(held).forEach((fieldname) => {
			unsent[fieldname] = held[fieldname].mine;
			base[fieldname] = held[fieldname].base;
		});
		let modified = null;
		[...this.retired, this.queue].forEach((queue) => {
			if (!queue || queue.manual || queue.record_key !== key) return;
			Object.assign(unsent, queue.in_flight() || {}, queue.conflict_sent || {}, queue.pending());
			modified = queue.modified || modified;
		});
		const bases = this.bases[key] || {};
		Object.keys(bases).forEach((fieldname) => {
			if (!ge_has(unsent, fieldname)) delete bases[fieldname];
			else if (!ge_has(base, fieldname)) base[fieldname] = bases[fieldname];
		});
		const entry = this.kit.make_entry({ doctype, name, modified, unsent, base, at: Date.now() });
		this.mark_kept(key, ge_write_kept(key, entry));
	}

	// Which keys the phone failed to write: any one of them means closing the tab would lose it.
	mark_kept(key, kept) {
		if (kept) this.failed_keys.delete(key);
		else this.failed_keys.add(key);
	}

	// Whether closing the tab now loses nothing: the phone holds every record's unsent changes, and
	// step 1 of a record not created yet.
	kept_on_phone() {
		return this.failed_keys.size === 0 && !!ge_storage();
	}

	new_key(client_id) {
		return this.kit.new_key(this.doctype, ge_user(), client_id || this.client_id);
	}

	// Step 1 of a record not created yet, as typed, with the create's id and what it carries.
	write_new_journal() {
		if (!this.is_new || !this.flow || !this.kit) return false;
		const steps = this.flow.steps || [];
		const step = steps.find((s) => s.create) || steps[0] || {};
		const values = {};
		this.kit.editable_fieldnames(step).forEach((fieldname) => {
			if (ge_has(this.values, fieldname)) values[fieldname] = ge_clone(this.values[fieldname]);
		});
		const key = this.new_key();
		const entry = { doctype: this.doctype, client_id: this.client_id, values, carry: this.carry || {}, at: Date.now() };
		const kept = ge_write_kept(key, entry);
		this.mark_kept(key, kept);
		return kept;
	}

	// Only this create's own copy: another tab's start, or a newer one, is left alone.
	forget_new(client_id) {
		if (!this.kit || !this.doctype) return;
		const key = this.new_key(client_id);
		ge_write_kept(key, null);
		this.failed_keys.delete(key);
	}

	// The newest start this person kept for `doctype` on this phone, or null.
	newest_kept_new(doctype) {
		const prefix = this.kit.new_key_prefix(doctype, ge_user());
		let newest = null;
		ge_keys(prefix).forEach((key) => {
			const entry = ge_read_kept(key);
			if (this.kit.usable_new(entry, Date.now()) && (!newest || Number(entry.at) > Number(newest.at))) newest = entry;
		});
		return newest;
	}

	// A record this phone started and did not create: put step 1 back, with the same create id. Not
	// over a start from somewhere else. The first attempt may have arrived with its answer lost, so
	// the server is asked; if it made the record, that opens instead of being typed again.
	restore_new() {
		if (this.started_with) return;
		const entry = this.newest_kept_new(this.doctype);
		if (!entry) return;
		Object.keys(entry.values).forEach((fieldname) => (this.values[fieldname] = ge_clone(entry.values[fieldname])));
		this.carry = entry.carry || {};
		if (entry.client_id) this.client_id = entry.client_id;
		this.new_dirty = true;
		this.notice = { kind: "kept-new", at: entry.at };
		if (entry.client_id) this.check_created(entry.client_id);
	}

	check_created(client_id) {
		const doctype = this.doctype;
		this.sf
			.call(GE_API + "find_created", { doctype, client_id }, { quiet: true, method: "GET" })
			.then((found) => {
				if (!found || !found.name || !this.is_new || this.doctype !== doctype || this.client_id !== client_id) return;
				// Step 1 as it stands now (typed since the restore, maybe) goes onto the record.
				this.collect_step();
				const steps = this.flow.steps || [];
				const step = steps.find((s) => s.create) || steps[0] || {};
				const typed = {};
				this.kit.editable_fieldnames(step).forEach((fieldname) => {
					if (ge_has(this.values, fieldname)) typed[fieldname] = ge_clone(this.values[fieldname]);
				});
				this.pending_resend = { doctype, name: found.name, values: typed };
				this.forget_new(client_id);
				this.new_dirty = false;
				this.sf.toast(__("{0} was created after all; it is open now.", [found.name]), { tone: "info", timeout: 6000 });
				this.show_created(doctype, found.name);
			})
			.catch(() => {});
	}

	// The record just loaded: put back what this phone kept for it (journal.js reconcile), for fields
	// the person may still change. Returns what to tell them once the step is on screen, or null.
	restore_kept() {
		const key = this.record_key(this.doctype, this.name);
		// An older queue for this record still in this page's memory (a reload, Set as Lost or Back
		// while it could not send) is newer than the phone's copy: what it waits to send comes over
		// to this queue, through the same reconcile, and leaves the old one. What it has in flight
		// stays with it and settles on its own.
		const old = this.retired.filter((queue) => queue.record_key === key && Object.keys(queue.pending()).length);
		let entry;
		if (old.length) {
			const unsent = {};
			old.forEach((queue) => Object.assign(unsent, queue.pending()));
			entry = { unsent, base: Object.assign({}, this.bases[key] || {}) };
			old.forEach((queue) => Object.keys(queue.pending()).forEach((fieldname) => queue.discard(fieldname)));
		} else {
			entry = ge_read_kept(key);
			if (!entry) return null;
			if (!this.kit.usable_entry(entry, Date.now())) {
				ge_write_kept(key, null);
				return null;
			}
			// A queue still sending for this record holds the rest: the phone's copy is not newer.
			if (this.retired.some((queue) => queue.record_key === key && queue.has_unsaved())) return null;
		}
		const editable = new Set();
		(this.flow.steps || []).forEach((step) => {
			if (!step.read_only) this.kit.editable_fieldnames(step).forEach((fieldname) => editable.add(fieldname));
		});
		const plan = this.kit.reconcile(entry, this.flow.values);
		const lost = Object.keys(plan.reapply).filter((fieldname) => !editable.has(fieldname)).length +
			plan.conflicts.filter((item) => !editable.has(item.fieldname)).length;
		const bases = (this.bases[key] = {});
		const held = (this.held[key] = {});
		Object.keys(plan.reapply).forEach((fieldname) => {
			if (!editable.has(fieldname)) return;
			bases[fieldname] = ge_clone(this.saved[fieldname]);
			this.values[fieldname] = ge_clone(plan.reapply[fieldname]);
			this.queue.change(fieldname, plan.reapply[fieldname]);
		});
		const conflicts = plan.conflicts.filter((item) => editable.has(item.fieldname));
		// Until the person chooses, the phone keeps "mine" for these: closing first loses nothing.
		conflicts.forEach((item) => {
			held[item.fieldname] = { mine: ge_clone(item.mine), base: ge_clone((entry.base || {})[item.fieldname]) };
		});
		this.write_journal(this.doctype, this.name);
		const put_back = Object.keys(plan.reapply).filter((fieldname) => editable.has(fieldname)).length;
		if (!put_back && !conflicts.length && !lost) return null;
		return { put_back, conflicts, lost, key };
	}

	after_restore(kept) {
		if (kept.put_back) {
			this.sf.toast(__("Put back {0} change(s) this phone kept from before.", [kept.put_back]), {
				tone: "info",
				timeout: 6000,
			});
		}
		if (kept.lost) {
			this.sf.toast(__("{0} kept change(s) could not be put back: those fields can no longer be changed here.", [kept.lost]), {
				tone: "warning",
				timeout: 8000,
			});
		}
		if (!kept.conflicts.length) return;
		// Changed on the server since: the person chooses, as with any conflict.
		const doctype = this.doctype;
		const name = this.name;
		this.show_conflict({ modified_by: null }, { conflicts: kept.conflicts, resend: {} }, (decision) => {
			if (!decision || this.doctype !== doctype || this.name !== name) return;
			delete this.held[kept.key];
			const bases = this.bases[kept.key] || (this.bases[kept.key] = {});
			Object.keys(decision.save || {}).forEach((fieldname) => {
				const value = decision.save[fieldname];
				bases[fieldname] = ge_clone(this.saved[fieldname]);
				this.values[fieldname] = ge_clone(value);
				this.show_value(fieldname, value);
				this.queue.change(fieldname, value);
			});
			this.write_journal(doctype, name);
			this.refresh_chrome();
		});
	}

	// Entries past keeping, for anyone, go when the page first loads: a record nobody reopened
	// would otherwise keep its entry for good.
	sweep_journal() {
		const now = Date.now();
		[this.kit.JOURNAL_PREFIX, this.kit.NEW_PREFIX].forEach((prefix) => {
			ge_keys(prefix).forEach((key) => {
				if (this.kit.stale_entry(ge_read_kept(key), now)) ge_write_kept(key, null);
			});
		});
	}

	// Saves that failed for want of signal try again every 20 s until one gets through.
	wait_for_signal() {
		if (this.signal_timer) return;
		this.signal_timer = window.setTimeout(() => {
			this.signal_timer = null;
			this.signal_back();
		}, 20000);
	}

	signal_back() {
		if (this.is_new && this.notice && this.notice.kind === "create-offline" && !this.creating && this.showing()) {
			this.create();
		}
		[this.queue, ...this.retired].forEach((queue) => {
			// A held queue (a move's required fields) is sent only when the person commits the move.
			if (queue && !queue.manual && queue.state === "error" && this.kit && this.kit.try_later(queue.error)) {
				queue.retry().catch(() => {});
			}
		});
	}

	// ------------------------------------------------------------------ saving

	save_in_background() {
		if (!this.queue) return;
		this.collect_step();
		this.queue.flush().catch(() => {});
	}

	// Before a move the person asked for. Refused by the server (a validation, a conflict, a lapsed
	// session): stay, with the reason on screen. Not reached at all: move on; what was typed stays
	// queued and the saving line says "Not saved — retry".
	//
	// A refusal already on screen does not hold the person a second time: pressing Next again with
	// the same message showing moves on (the change stays queued, the line says "Not saved"), so a
	// problem in a field on another step can never trap them on this one.
	save_before_moving() {
		this.collect_step();
		if (this.is_new || !this.queue) return Promise.resolve();
		const shown = this.notice && this.notice.kind === "save" ? (this.notice.error || {}).message : null;
		return this.queue.flush().catch((err) => {
			if (!err || (!err.conflict && !err.session_expired && !(err.status && err.status < 500))) return;
			if (shown && err.status === 417 && err.message === shown) return;
			throw err;
		});
	}

	on_save_state(state, error) {
		if (state === "saved" || state === "saving") {
			if (this.notice && this.notice.kind !== "create") this.notice = null;
		} else if (state === "error") {
			if (this.kit.try_later(error) && this.kept_on_phone()) {
				// Kept on the phone and retried when the signal returns: the saving line says so.
				if (this.notice && this.notice.kind === "save") this.notice = null;
			} else {
				this.notice = { kind: "save", error: error || {} };
			}
		} else if (state === "expired") {
			this.notice = { kind: "expired" };
		}
		this.refresh_notices();
		this.render_saveline();
	}

	on_saved(result, sent) {
		const returned = result.values || {};
		const pending = this.queue ? this.queue.pending() : {};
		Object.keys(returned).forEach((fieldname) => {
			this.saved[fieldname] = ge_clone(returned[fieldname]);
			// Typed again since this was sent: what is on screen is newer.
			if (ge_has(pending, fieldname)) return;
			const same = this.kit.same_value(this.values[fieldname], returned[fieldname]);
			this.values[fieldname] = ge_clone(returned[fieldname]);
			const def = this.defs[fieldname];
			const table = def && (def.fieldtype === "Table" || def.fieldtype === "Table MultiSelect");
			// A table's stored rows now have names; anything else only when normalizing changed it.
			if (!same || (table && def.fieldtype === "Table" && !this.kit.text_list_column(def))) {
				this.show_value(fieldname, returned[fieldname]);
			}
		});
		if (result.completeness) this.completeness = result.completeness;
		if (this.after_conflict) {
			this.after_conflict = false;
			this.refresh_from_server();
		}
		this.refresh_chrome();
	}

	on_conflict(result, sent) {
		const server = result.server || {};
		const split = this.kit.split_conflict(this.saved, sent, server);
		// Until the person chooses, what was sent lives only in this prompt: the phone keeps it too.
		const queue = this.queue;
		if (queue) queue.conflict_sent = sent;
		this.write_journal(this.doctype, this.name);
		const done = (decision) => {
			if (queue) delete queue.conflict_sent;
			return decision;
		};
		// What the server holds now is the base from here on, on screen and on the phone.
		const bases = this.bases[this.record_key(this.doctype, this.name)] || {};
		Object.keys(server).forEach((fieldname) => {
			this.saved[fieldname] = ge_clone(server[fieldname]);
			if (ge_has(bases, fieldname)) bases[fieldname] = ge_clone(server[fieldname]);
		});
		this.after_conflict = true;
		if (!split.conflicts.length) {
			return Promise.resolve({ save: split.resend, modified: result.modified }).then(done);
		}
		return new Promise((resolve) => this.show_conflict(result, split, resolve)).then(done);
	}

	show_conflict(result, split, resolve) {
		const sf = this.sf;
		const esc = sf.esc;
		const choices = {};
		split.conflicts.forEach((item) => (choices[item.fieldname] = "mine"));
		const who = result.modified_by
			? (frappe.user_info && frappe.user_info(result.modified_by).fullname) || result.modified_by
			: "";
		const $slot = this.$root.find(".ge-conflict-slot").empty();
		const $box = $('<section class="ge-conflict sf-card" role="alert"></section>').appendTo($slot);
		$('<h2 class="ge-conflict-title"></h2>')
			.text(
				who
					? __("{0} changed this record while you were working.", [who])
					: __("Someone else changed this record while you were working.")
			)
			.appendTo($box);
		$('<p class="ge-conflict-intro"></p>')
			.text(__("Choose which version to keep for each field, then save."))
			.appendTo($box);
		const $all = $('<div class="ge-conflict-all"></div>').appendTo($box);
		const $list = $('<div class="ge-conflict-list"></div>').appendTo($box);
		const paint = () => {
			$list.find(".ge-conflict-row").each((i, el) => {
				const fieldname = $(el).attr("data-field");
				$(el)
					.find("button[data-pick]")
					.each((j, button) => {
						const on = $(button).attr("data-pick") === choices[fieldname];
						$(button).attr("aria-pressed", on ? "true" : "false").toggleClass("is-on", on);
					});
			});
		};
		split.conflicts.forEach((item) => {
			const def = this.defs[item.fieldname] || {};
			const label = __(def.label || item.fieldname);
			const $row = $(`<div class="ge-conflict-row" role="group"></div>`)
				.attr("data-field", item.fieldname)
				.attr("aria-label", label)
				.appendTo($list);
			$('<div class="ge-conflict-label"></div>').text(label).appendTo($row);
			const side = (pick, title, value) => {
				const $side = $('<div class="ge-conflict-side"></div>').appendTo($row);
				$('<div class="ge-conflict-side-title"></div>').text(title).appendTo($side);
				$('<div class="ge-conflict-value"></div>')
					.text(this.kit.value_text(value) || __("(empty)"))
					.appendTo($side);
				$(`<button type="button" class="sf-btn sf-btn--secondary"></button>`)
					.attr("data-pick", pick)
					.text(pick === "mine" ? __("Keep mine") : __("Use theirs"))
					.on("click", () => {
						choices[item.fieldname] = pick;
						paint();
					})
					.appendTo($side);
			};
			side("mine", __("Yours"), item.mine);
			side("theirs", __("Theirs"), item.theirs);
		});
		const pick_all = (pick) => {
			split.conflicts.forEach((item) => (choices[item.fieldname] = pick));
			paint();
		};
		$(`<button type="button" class="sf-btn sf-btn--quiet"></button>`)
			.text(__("Keep all mine"))
			.on("click", () => pick_all("mine"))
			.appendTo($all);
		$(`<button type="button" class="sf-btn sf-btn--quiet"></button>`)
			.text(__("Use all theirs"))
			.on("click", () => pick_all("theirs"))
			.appendTo($all);
		$(`<button type="button" class="sf-btn sf-btn--primary ge-conflict-save"></button>`)
			.text(__("Save my choices"))
			.on("click", () => {
				const decided = this.kit.resolve(split.resend, split.conflicts, choices);
				const pending = this.queue ? this.queue.pending() : {};
				Object.keys(decided.take).forEach((fieldname) => {
					if (ge_has(pending, fieldname)) return;
					this.values[fieldname] = ge_clone(decided.take[fieldname]);
					this.show_value(fieldname, decided.take[fieldname]);
				});
				$slot.empty();
				this.conflict = null;
				resolve({ save: decided.save, modified: result.modified });
			})
			.appendTo($box);
		this.conflict = { result, split };
		paint();
		$box.find(".ge-conflict-title").attr("tabindex", "-1").trigger("focus");
		this.render_saveline();
	}

	// After a conflict, the rest of the record may have moved too: show what it holds now for every
	// field nobody is typing in. Its `modified` is taken only when nothing is waiting to be saved,
	// so a newer change can never be overwritten on the strength of a refresh.
	refresh_from_server() {
		const name = this.name;
		this.sf
			.call(GE_API + "get_flow", { doctype: this.doctype, name }, { quiet: true, method: "GET" })
			.then((flow) => {
				if (!flow || this.name !== name || !this.queue) return;
				const pending = this.queue.pending();
				const busy = this.queue.has_unsaved();
				Object.keys(flow.values || {}).forEach((fieldname) => {
					if (ge_has(pending, fieldname)) return;
					if (busy && this.queue.in_flight() && ge_has(this.queue.in_flight(), fieldname)) return;
					const value = flow.values[fieldname];
					this.saved[fieldname] = ge_clone(value);
					if (!this.kit.same_value(this.values[fieldname], value)) {
						this.values[fieldname] = ge_clone(value);
						this.show_value(fieldname, value);
					}
				});
				if (!busy && flow.modified) this.queue.set_modified(flow.modified);
				if (flow.completeness) this.completeness = flow.completeness;
				this.refresh_chrome();
			})
			.catch(() => {});
	}

	// Step 1 of a new record: check what it needs, insert it, and move to its real address.
	create() {
		if (this.creating || !this.flow) return;
		this.collect_step();
		const step = (this.flow.steps || []).find((s) => s.create) || this.flow.steps[0];
		// The step's block may know the insert would be refused (the Item flow's naming guard, P8.1).
		let held = "";
		try {
			held = this.ext && typeof this.ext.before_create === "function" ? this.ext.before_create() : "";
		} catch (e) {
			// A block that cannot answer holds nothing back: the server still checks the insert.
			if (window.console) window.console.error("[Guided Entry] the step's block failed", e);
		}
		if (held) {
			this.notice = { kind: "create", title: __("Not created yet"), body: held };
			this.refresh_notices();
			return;
		}
		const missing = this.kit.missing_required(step.fields, this.values);
		if (missing.length) {
			this.notice = {
				kind: "create",
				title: __("Fill these in first"),
				body: missing.map((field) => __(field.label || field.fieldname)).join(", "),
			};
			this.refresh_notices();
			this.focus_field(missing[0].fieldname);
			return;
		}
		const values = {};
		this.kit.editable_fieldnames(step).forEach((fieldname) => {
			if (!ge_has(this.values, fieldname)) return;
			const def = this.defs[fieldname] || {};
			const value = this.values[fieldname];
			values[fieldname] =
				def.fieldtype === "Table" || def.fieldtype === "Table MultiSelect"
					? this.kit.clean_rows(value || [], (def.child_fields || []).map((child) => child.fieldname))
					: value;
		});
		// P4.4: create_record assigns only step 1's "create" fields. Anything else the step shows that
		// the person changed (the summary, a close date) goes with the create as prefill too, which the
		// server takes for any field the flow lets them write; before v1.675.0 it was dropped.
		const extra = this.kit.create_extras(step, values, (this.flow && this.flow.values) || {});
		const doctype = this.doctype;
		const started_with = this.started_with;
		this.creating = true;
		this.notice = null;
		this.refresh_notices();
		this.refresh_chrome();
		const args = { doctype, values, client_id: this.client_id };
		const prefill = Object.assign({}, this.carry || {}, extra);
		if (Object.keys(prefill).length) args.prefill = prefill;
		this.sf
			.call(GE_API + "create_record", args, { quiet: true })
			.then((created) =>
				// The flow for the record as it now exists: set-only-once fields lock, and each
				// step's permissions are worked out for the saved document.
				this.sf
					.call(GE_API + "get_flow", { doctype, name: created.name }, { quiet: true, method: "GET" })
					.then((flow) => ({ created, flow }))
					.catch(() => ({ created, flow: null }))
			)
			.then(({ created, flow }) => {
				this.creating = false;
				this.new_dirty = false;
				// This create's own copy only: a newer start typed meanwhile keeps its own.
				ge_write_kept(this.kit.new_key(doctype, ge_user(), args.client_id), null);
				const still_here = this.still_new(doctype);
				if (flow) {
					this.take_flow(flow);
				} else {
					this.flow.name = created.name;
					this.flow.modified = created.modified;
					this.take_flow(Object.assign({}, this.flow, { values: Object.assign({}, this.values, created.values) }));
				}
				if (created.completeness) this.completeness = created.completeness;
				// An earlier attempt made the record; step 1 may have changed since, so what differs is
				// saved on it now.
				if (created.deduped && this.queue) this.resend_typed(values);
				// Where the shell goes from here (the guided page replaces its "new" entry; the side
				// panel shows the deal it made).
				this.after_create({ doctype, created, started_with, still_here });
			})
			.catch((err) => {
				this.creating = false;
				if (this.kit.try_later(err) && this.write_new_journal()) {
					// No signal: kept on the phone, created when it returns (signal_back).
					this.notice = { kind: "create-offline" };
					this.wait_for_signal();
				} else {
					this.notice = err && err.session_expired ? { kind: "expired" } : { kind: "save", error: err || {} };
				}
				this.refresh_notices();
				this.refresh_chrome();
			});
	}

	// Step 1 as typed, over a record an earlier attempt of the same create made.
	resend_typed(values) {
		Object.keys(values || {}).forEach((fieldname) => {
			if (this.kit.same_value(values[fieldname], this.values[fieldname])) return;
			this.note_base(fieldname);
			this.values[fieldname] = ge_clone(values[fieldname]);
			this.queue.change(fieldname, values[fieldname]);
		});
		this.write_journal(this.doctype, this.name);
	}

	refresh_notices() {
		const $slot = this.$root.find(".ge-notices");
		if (!$slot.length) return;
		// A distinct-values warning shows whenever nothing more urgent does, so a save that clears
		// the notice does not clear it.
		const warnings = this.flow ? this.distinct_warnings() : [];
		const notice = this.notice || (warnings.length ? { kind: "distinct", messages: warnings } : null);
		if (!notice) {
			$slot.empty();
			return;
		}
		if (notice.kind === "expired") {
			this.sf.state.render($slot, "error", {
				title: __("You are signed out, so this was not saved."),
				body: __(
					"What you typed is kept on this page. Sign in on the new tab, come back here, and it will save."
				),
				action_label: __("Sign in"),
				on_action: () => this.open_login(),
			});
			return;
		}
		if (notice.kind === "create") {
			this.sf.state.render($slot, "error", { title: notice.title, body: notice.body });
			return;
		}
		if (notice.kind === "kept-new") {
			this.sf.state.render($slot, "empty", {
				title: __("Put back the new {0} you started on this phone.", [__(this.doctype)]),
				body: __("Check it and create it, or start over."),
				action_label: __("Start over"),
				on_action: () => {
					const doctype = this.doctype;
					// Every start kept for this DocType, or the next-newest would come back in its place.
					const prefix = this.kit.new_key_prefix(doctype, ge_user());
					ge_keys(prefix).forEach((key) => ge_write_kept(key, null));
					[...this.failed_keys].forEach((key) => key.startsWith(prefix) && this.failed_keys.delete(key));
					this.load(doctype, null, null);
				},
			});
			return;
		}
		if (notice.kind === "create-offline") {
			this.sf.state.render($slot, "empty", {
				title: __("No signal. This is kept on the phone and will be created when there is signal."),
				action_label: __("Try now"),
				on_action: () => this.create(),
			});
			return;
		}
		if (notice.kind === "distinct") {
			this.sf.state.render($slot, "empty", { title: notice.messages.join(" ") });
			return;
		}
		if (notice.kind === "carry") {
			this.sf.state.render($slot, "empty", {
				title: __("Not carried over here: {0}.", [notice.fields.join(", ")]),
				body: __("The full form keeps every value it was started with."),
				// Only the guided page starts a record from elsewhere (and has open_new_in_form); a shell
				// without it shows the notice with no action rather than a button that throws.
				action_label: typeof this.open_new_in_form === "function" ? __("Use the full form") : "",
				on_action: () => this.open_new_in_form(),
			});
			return;
		}
		const error = notice.error || {};
		this.sf.state.render($slot, "error", {
			title: __("Not saved."),
			body: error.message || __("The server refused this change."),
			action_label: __("Try again"),
			on_action: () => {
				// P4.4: a refusal that came from somewhere other than the save queue (the side panel's
				// Declare Lost on a move) says how to try it again.
				if (typeof notice.retry === "function") notice.retry();
				else if (this.is_new) this.create();
				else if (this.queue) {
					this.collect_step();
					this.queue.retry().catch(() => {});
				}
			},
		});
	}
}

// The guided page's way in (see the head of this file): RecordEditor's methods onto a class that
// was defined before this bundle loaded, wherever that class has none of its own. Returns the
// names it added.
export function mix_editor(proto) {
	const added = [];
	Object.getOwnPropertyNames(RecordEditor.prototype).forEach((name) => {
		if (name === "constructor" || Object.prototype.hasOwnProperty.call(proto, name)) return;
		Object.defineProperty(proto, name, {
			value: RecordEditor.prototype[name],
			writable: true,
			configurable: true,
			enumerable: false,
		});
		added.push(name);
	});
	return added;
}
