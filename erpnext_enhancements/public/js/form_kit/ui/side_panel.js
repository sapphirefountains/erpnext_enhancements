/*
 * form kit P4.4, part 2 (ERPNext TASK-2026-02762): the side panel. A record edited from a list or a
 * board without leaving it, in the planner kit's drawer, by the SAME record editor the guided page
 * runs (./editor.js): the same fields, the same save queue and conflict chooser, the same phone
 * journal, the same Set as Lost. The board's P6.6 (tap to edit; required fields on a drop) and P6.7
 * (quick add) are its first users.
 *
 *   const panel = new sf_form_kit.SidePanel({ doctype, owner, push, host });
 *   panel.show({ kind: "edit", name, step })     a record, one step at a time
 *   panel.show({ kind: "move", name, status })   what a move into `status` needs, then the move
 *   panel.show({ kind: "new", stage })           quick add, created through create_record
 *   panel.hide(how) · panel.request_close() · panel.is_open() · panel.key()
 *
 * The page owns the address (Nik's rule, 2026-09-24: never break Back/Forward). Every panel is a
 * route of the page's own, so reload reopens it and Back closes it; the drawer is opened with
 * `history: false` and pushes nothing of its own. The panel tells the page what happened through
 * `host`, and the page changes the route:
 *
 *   host.close(reason)            the person closed it (Done, Cancel, the close button, Esc)
 *   host.step(key)                they moved to another step (the page REPLACES its route, so Back
 *                                 still closes the panel rather than walking its steps)
 *   host.committed(info)          a move's required fields and the move itself are saved
 *   host.created(info)            quick add made the record ({ name }): the page shows it for editing
 *   host.open_full(name, step)    "Open full page": the guided page
 *   host.changed(name)            the record changed (a save, an Undo): the page may refresh
 *
 * Moves (P6.6). Nothing reaches the record until the person commits: the panel's save queue is
 * `manual` and keeps no phone copy (editor.js hold_saves), and Closed Won's ranks go in the same
 * save as the status, locked on `modified` like every save. A refused save keeps the panel open with
 * the reason; Cancel (or Back) drops what was typed and the card never moved. Lost runs the record's
 * own Declare Lost (editor.js send_lost) with the fields the Set as Lost dialog asks for.
 *
 * Undo (P4.4). Closing an edit that saved something shows a toast with Undo, which saves each field
 * back to what it was when the panel opened, locked on `modified`: someone else's change since is
 * never overwritten (it says so instead).
 *
 * Browser only. Its decisions are ../panel.js, pure, run under node by the board's panel suite.
 */

import { RecordEditor, GE_API, ge_clone, ge_has } from "./editor.js";

const WIDTH = 520;

export class SidePanel extends RecordEditor {
	constructor(opts) {
		super();
		this.opts = opts || {};
		this.host = this.opts.host || {};
		this.sf = window.sf_kit;
		this.kit = window.sf_form_kit;
		this.pk = window.planner_kit;
		this.id_prefix = "fkp";
		this.$root = $('<div class="fk-panel"></div>');
		this.retired = [];
		this.bases = {};
		this.held = {};
		this.failed_keys = new Set();
		this.signal_timer = null;
		this.load_seq = 0;
		this.handle = null;
		this.spec = null;
		this.reopening = false;
		this.reset();

		window.addEventListener("beforeunload", (event) => {
			if (!this.has_unsaved() || this.kept_on_phone()) return;
			event.preventDefault();
			event.returnValue = "";
			return "";
		});
		window.addEventListener("online", () => this.signal_back());
		window.addEventListener("focus", () => {
			if (this.queue && this.queue.state === "expired") this.queue.retry().catch(() => {});
		});
		// Esc closes an open (i) first, as on the guided page; the page's own Esc handler sees the
		// event as handled (defaultPrevented) and leaves the panel open.
		this.$root.on("keydown", (event) => {
			if (event.key !== "Escape") return;
			const $open = this.$root.find(".ge-help-btn[aria-expanded='true']");
			if ($open.length) {
				event.preventDefault();
				$open.each((i, button) => this.toggle_help($(button), false));
				$open.last().trigger("focus");
			}
		});
		this.$root.on("click", "[data-fk]", (event) => this.on_action(event, $(event.currentTarget).attr("data-fk")));
		this.$root.on("change", "select[data-fk-step]", (event) => {
			const key = $(event.currentTarget).val();
			if (key && key !== this.step_key) this.go_step(key);
		});
		// The Closed Won prompt's "No" un-wins a deal on the server (create_project_prompt.js).
		$(document).on("sf-record-changed", (event, doctype, name) => {
			if (this.is_open() && this.spec && this.spec.kind === "edit" && this.doctype === doctype && this.name === name) {
				this.reload_record();
			}
		});
		this.sweep_journal();
	}

	reset() {
		// P8.1: a DocType's step blocks go with the record (editor.js open_extension).
		this.leave_extension();
		this.ext = null;
		if (this.queue && this.queue.has_unsaved()) this.retired.push(this.queue);
		this.retired = this.retired.filter((queue) => queue.has_unsaved());
		this.flow = null;
		this.doctype = null;
		this.name = null;
		this.is_new = false;
		this.values = {};
		this.saved = {};
		this.completeness = null;
		this.step_key = null;
		this.step_override = null;
		this.fg = null;
		this.lost_fg = null;
		this.suppress = new Set();
		this.queue = null;
		this.creating = false;
		this.new_dirty = false;
		this.defs = {};
		this.conflict = null;
		this.notice = null;
		this.carry = {};
		this.started_with = "";
		this.client_id = null;
		this.hold_saves = false;
		this.gate = null;
		this.committing = false;
		// Edit: the record as the panel opened it, and each field the person changed in it (the Undo
		// toast, offered once everything they changed has saved).
		this.opened = null;
		this.touched = new Set();
	}

	// ------------------------------------------------------------------ what it shows

	key() {
		return this.spec ? this.kit.panel_key(this.spec) : "";
	}

	is_open() {
		return !!(this.handle && this.handle.is_open());
	}

	showing() {
		return this.is_open();
	}

	// Show `spec`. The same panel already open only follows a step change; anything else is loaded.
	show(spec) {
		const next = Object.assign({ kind: "edit" }, spec || {});
		if (this.is_open() && this.kit.panel_key(next) === this.key()) {
			if (next.kind === "edit" && next.step && this.flow && next.step !== this.step_key) this.show_step(next.step);
			return;
		}
		this.leave();
		this.spec = next;
		this.open_drawer();
		this.load(this.opts.doctype, next.kind === "new" ? null : next.name, next.step || null);
	}

	open_drawer() {
		const title =
			this.spec.kind === "new"
				? __("New {0}", [__(this.opts.doctype)])
				: this.spec.kind === "move"
				? __("Move to {0}", [__(this.spec.status || "")])
				: this.spec.name || "";
		if (this.is_open()) {
			this.handle.set_title(title);
			this.handle.set_subtitle("");
			return;
		}
		this.reopening = true;
		try {
			this.handle = this.pk.drawer.open({
				title,
				body: this.$root[0],
				width: WIDTH,
				owner: "fk",
				key: this.key(),
				push: this.opts.push || null,
				history: false,
				// The drawer's own × takes the panel's close path too: an edit saves first.
				request_close: () => this.request_close(),
				tools: this.close_button(),
				on_close: (how) => this.drawer_closed(how),
			});
		} finally {
			this.reopening = false;
		}
	}

	close_button() {
		const button = document.createElement("button");
		button.type = "button";
		button.className = "sf-btn sf-btn--quiet fk-close";
		button.setAttribute("data-fk-close", "1");
		button.textContent = this.spec && this.spec.kind === "edit" ? __("Done") : __("Cancel");
		button.addEventListener("click", () => this.request_close());
		return button;
	}

	// The drawer went away. Closed by this panel (hide): nothing more. Replaced by another drawer, or
	// closed some other way: the page is told, so its address follows.
	drawer_closed(how) {
		if (this.reopening || this.hiding) return;
		this.handle = null;
		this.leave();
		if (typeof this.host.close === "function") this.host.close(how || "user");
	}

	// The person asked to close it. An edit saves first, and a save the server refuses keeps the
	// panel open with the reason (pressing again with the same reason showing closes it: the change
	// stays queued and the saving line says so, as on the guided page). A move or a quick add closes
	// at once: a move that was not committed did not happen, and an unsent quick add is kept on the
	// phone (journal.js) for the next one.
	request_close() {
		if (!this.is_open()) return;
		const done = () => {
			if (typeof this.host.close === "function") this.host.close("user");
		};
		if (this.spec && this.spec.kind === "edit" && this.queue) {
			this.save_before_moving()
				.then(done)
				.catch(() => {});
			return;
		}
		done();
	}

	// The page's route no longer has the panel (Back, a route change, a close it asked for).
	hide(how) {
		this.leave();
		if (!this.is_open()) return;
		this.hiding = true;
		try {
			this.handle.close();
		} finally {
			this.hiding = false;
			this.handle = null;
		}
	}

	// Done with what is showing: an edit sends what it holds (and offers Undo once it has); a move
	// that was never committed is dropped, never sent.
	leave() {
		if (!this.spec) return;
		const spec = this.spec;
		this.toggle_all_help(false);
		if (spec.kind === "move" && this.queue) {
			Object.keys(this.queue.pending()).forEach((fieldname) => this.queue.discard(fieldname));
			this.queue.cancel();
		}
		if (spec.kind === "edit" && this.queue) {
			const queue = this.queue;
			const opened = this.opened;
			const flow = this.flow;
			const doctype = this.doctype;
			const name = this.name;
			const title = this.title_text();
			this.collect_step();
			const saved = {};
			this.touched.forEach((fieldname) => (saved[fieldname] = ge_clone(this.values[fieldname])));
			queue
				.flush()
				.then(() => this.offer_undo({ queue, flow, opened, saved, doctype, name, title }))
				.catch(() => {});
		}
		this.load_seq += 1;
		this.reset();
		this.spec = null;
	}

	// ------------------------------------------------------------------ loading

	load(doctype, name, step_key) {
		const spec = this.spec;
		if (!spec) return;
		const seq = ++this.load_seq;
		this.reset();
		this.doctype = doctype;
		this.name = name || null;
		this.is_new = !name;
		this.open_extension(doctype);
		// A move's fields are not saved until the move is (editor.js make_queue, write_journal).
		this.hold_saves = spec.kind === "move";
		this.$root.empty();
		this.sf.state.render(this.$root, "loading");
		this.ensure_meta(doctype)
			.then(() =>
				this.sf.call(GE_API + "get_flow", { doctype, name: name || null }, { quiet: true, method: "GET" })
			)
			.then((flow) => {
				if (seq !== this.load_seq || this.spec !== spec) return;
				this.take_flow(flow);
				if (spec.kind === "new") this.start_new(spec);
				else if (spec.kind === "move") this.start_move(spec);
				else this.start_edit(spec, step_key);
			})
			.catch((err) => {
				if (seq !== this.load_seq || this.spec !== spec) return;
				this.show_load_error(err || {}, () => this.load(doctype, name, step_key));
			});
	}

	show_load_error(err, retry) {
		if (err.status === 403 && !err.session_expired) {
			this.sf.state.render(this.$root, "no-access", { body: err.message });
			return;
		}
		if (err.status === 404) {
			this.sf.state.render(this.$root, "empty", { title: __("This record no longer exists.") });
			return;
		}
		this.sf.state.render(this.$root, "error", {
			title: err.session_expired ? __("You are signed out.") : __("This could not be opened."),
			body: err.session_expired ? __("Sign in again, then come back to this tab and try again.") : err.message,
			on_action: retry,
		});
	}

	title_text() {
		if (!this.flow) return this.name || "";
		const model = this.kit.summary_model(this.doctype, this.name, this.values, this.completeness);
		return model.title || this.name || __("New {0}", [__(this.doctype)]);
	}

	// The panel's frame: notices, the conflict chooser, the stage the fields are drawn on, a footer.
	frame(head_html, foot_html) {
		const esc = this.sf.esc;
		this.$root.html(`
			${head_html || ""}
			<div class="ge-notices" aria-live="polite"></div>
			<div class="ge-conflict-slot"></div>
			<section class="ge-stage fk-stage"></section>
			<div class="fk-foot" role="group">${foot_html || ""}</div>`);
		this.$root.find(".fk-foot").attr("aria-label", __("Actions"));
	}

	button(action, label, tone, extra) {
		const esc = this.sf.esc;
		return `<button type="button" class="sf-btn sf-btn--${tone || "secondary"}${extra ? ` ${extra}` : ""}" data-fk="${esc(
			action
		)}">${esc(label)}</button>`;
	}

	step_head($stage, eyebrow, title, intro) {
		const $head = $('<header class="ge-step-head"></header>').appendTo($stage);
		if (eyebrow) $('<div class="ge-step-eyebrow"></div>').text(eyebrow).appendTo($head);
		$('<h2 class="ge-step-title" tabindex="-1"></h2>').text(title || "").appendTo($head);
		if (intro) $('<p class="ge-step-intro"></p>').text(intro).appendTo($head);
		return $head;
	}

	// ------------------------------------------------------------------ edit

	start_edit(spec, step_key) {
		this.opened = ge_clone(this.flow.values || {});
		const kept = this.restore_kept();
		this.frame(
			`<div class="fk-head">
				<div class="fk-progress">
					<div class="ge-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100"><div class="ge-progress-bar"></div></div>
					<div class="fk-progress-text"></div>
				</div>
				<div class="fk-head-row">
					<label class="fk-step-pick"><span>${this.sf.esc(__("Step"))}</span><select data-fk-step></select></label>
					${this.button("full", __("Open full page"), "quiet", "fk-full")}
				</div>
			</div>`,
			`<div class="fk-foot-row">
				${this.button("prev", __("Previous"), "secondary", "fk-prev")}
				<button type="button" class="ge-saveline" aria-live="polite"></button>
				${this.button("next", __("Next"), "primary", "fk-next")}
			</div>`
		);
		this.$root.find(".ge-saveline").on("click", () => {
			if (this.queue && ["error", "expired"].includes(this.queue.state)) {
				this.collect_step();
				this.queue.retry().catch(() => {});
			}
		});
		this.handle && this.handle.set_title(this.title_text());
		this.handle && this.handle.set_subtitle(`${__(this.doctype)} · ${this.name}`);
		// The deal's comments under the step, as on the guided page (editor.js mount_comments).
		$('<section class="ge-comments fk-comments" hidden></section>').insertAfter(this.$root.find(".ge-stage"));
		this.mount_comments();
		this.show_step(step_key || (spec && spec.step) || null);
		this.refresh_notices();
		if (kept) this.after_restore(kept);
	}

	show_step(step_key) {
		if (!this.flow) return;
		const steps = this.flow.steps || [];
		const resolved = this.kit.resolve_step(steps, this.values, step_key);
		const key = resolved.key;
		if (!key) {
			this.$root.find(".ge-stage").html(this.sf.state.html("empty", { title: __("There is nothing to fill in here.") }));
			return;
		}
		if (key === this.step_key && this.fg) {
			this.refresh_chrome();
			return;
		}
		const step = this.kit.find_step(steps, key);
		const moved = this.step_key !== null;
		this.step_key = key;
		this.fg = null;
		this.toggle_all_help(false);
		const pos = this.kit.position(steps, this.values, key);
		const editable = !!this.flow.can_write && !step.read_only;
		const $stage = this.$root.find(".ge-stage").empty();
		const $head = this.step_head(
			$stage,
			pos.total ? __("Step {0} of {1}", [pos.number, pos.total]) : step.tab || "",
			step.title,
			step.intro
		);
		if (!this.flow.can_write) {
			$('<p class="ge-readonly-note"></p>').text(__("You can read this record but not change it.")).appendTo($stage);
		}
		const $fields = $('<div class="ge-fields"></div>').appendTo($stage);
		if (step.link) {
			$(this.button("full", __(step.link.label), "secondary", "ge-step-link")).attr("data-tab", step.link.tab).appendTo($stage);
		}
		this.render_fields(step, $fields, editable);
		this.refresh_notices();
		this.refresh_chrome();
		// The address follows the step (replaced: Back still closes the panel).
		if (resolved.replace || moved || (this.spec && this.spec.step !== key)) {
			if (this.spec) this.spec.step = key;
			if (typeof this.host.step === "function") this.host.step(key);
		}
		if (moved) {
			const body = this.handle && this.handle.body;
			if (body) body.scrollTop = 0;
			$head.find(".ge-step-title").trigger("focus");
		}
	}

	// Save, then move, as the guided page's Next and Back do: a refusal keeps the person on the step.
	go_step(key) {
		if (!key || key === this.step_key) return;
		this.save_before_moving()
			.then(() => this.show_step(key))
			.catch(() => {
				// Stay; put the picker back on the step showing.
				this.$root.find("select[data-fk-step]").val(this.step_key);
			});
	}

	refresh_chrome() {
		if (!this.flow) return;
		if (this.spec && this.spec.kind === "edit" && this.step_key) {
			const kit = this.kit;
			const steps = this.flow.steps || [];
			const esc = this.sf.esc;
			const model = kit.summary_model(this.doctype, this.name, this.values, this.completeness);
			const missing = kit.missing_by_step(steps, (this.completeness || {}).missing);
			const options = kit
				.active_steps(steps, this.values)
				.map((step, index) => {
					const left = (missing[step.key] || []).length;
					const note = left ? ` (${left === 1 ? __("1 to fill") : __("{0} to fill", [left])})` : "";
					return `<option value="${esc(step.key)}"${step.key === this.step_key ? " selected" : ""}>${esc(
						`${index + 1}. ${step.title}${note}`
					)}</option>`;
				})
				.join("");
			const $select = this.$root.find("select[data-fk-step]");
			if ($select.length && !$select.is(":focus")) $select.html(options);
			this.$root.find(".ge-progress-bar").css("width", `${model.percent}%`);
			this.$root
				.find(".ge-progress-track")
				.attr("aria-valuenow", model.percent)
				.attr("aria-label", __("Complete for this stage"));
			this.$root
				.find(".fk-progress-text")
				.text(model.total ? __("{0} of {1} filled for {2}", [model.done, model.total, __(model.stage || "")]) : "");
			const prev = kit.neighbor(steps, this.values, this.step_key, -1);
			const next = kit.neighbor(steps, this.values, this.step_key, 1);
			this.$root.find(".fk-prev").prop("disabled", !prev);
			this.$root.find(".fk-next").prop("disabled", !next);
			if (this.handle) this.handle.set_title(model.title || this.name);
		}
		this.render_saveline();
	}

	// What the person changes in an edit, for Undo.
	set_value_local(fieldname, value, quiet_chrome) {
		super.set_value_local(fieldname, value, quiet_chrome);
		if (!this.is_new && this.spec && this.spec.kind === "edit") this.touched.add(fieldname);
	}

	// Someone else's version of a field becomes what Undo goes back to: Undo takes back only what this
	// person changed, never the other person's save.
	on_conflict(result, sent) {
		const server = (result && result.server) || {};
		if (this.opened) Object.keys(server).forEach((fieldname) => (this.opened[fieldname] = ge_clone(server[fieldname])));
		return super.on_conflict(result, sent);
	}

	// A move commits when the save carrying its status comes back with it (after a conflict chosen
	// too); any other save tells the page the record changed.
	on_saved(result, sent) {
		super.on_saved(result, sent);
		const returned = (result && result.values) || {};
		if (this.spec && this.spec.kind === "move" && this.gate && this.gate.kind !== "lost") {
			const field = this.status_field();
			if (ge_has(sent, field) && returned[field] === this.spec.status) this.committed(result);
		} else if (typeof this.host.changed === "function") {
			this.host.changed(this.name);
		}
	}

	offer_undo({ queue, flow, opened, saved, doctype, name, title }) {
		const undo = this.kit.undo_values(opened || {}, saved || {});
		const count = Object.keys(undo).length;
		if (!count) return;
		const said = count === 1 ? __("Saved a change to {0}", [title]) : __("Saved {0} changes to {1}", [count, title]);
		// No Undo for an edit that moved the record into or out of a closing status (Closed Won, Lost,
		// ...): a plain save back would skip the un-win refusal and leave the won date (panel.js).
		if (!this.kit.undo_allowed(flow, opened || {}, saved || {})) {
			this.sf.toast(said, { tone: "success", timeout: 6000 });
			return;
		}
		this.sf.toast(said, {
			tone: "success",
			timeout: 10000,
			action_label: __("Undo"),
			on_action: () => this.undo({ queue, undo, doctype, name, title }),
		});
	}

	// Each field back to what it was when the panel opened, on the record as this panel last saved
	// it. Someone else's save since is a conflict, and nothing is overwritten.
	undo({ queue, undo, doctype, name, title }) {
		this.sf
			.call(GE_API + "save_step", { doctype, name, values: undo, modified: queue.modified }, { quiet: true })
			.then((result) => {
				if (result && result.conflict) {
					this.sf.toast(
						__("{0} was changed by {1} since, so it was not undone.", [title, result.modified_by || __("someone else")]),
						{ tone: "warning", timeout: 8000 }
					);
					return;
				}
				if (result && result.modified) queue.set_modified(result.modified);
				this.sf.toast(__("Undone: {0} is back as it was.", [title]), { tone: "info" });
				if (typeof this.host.changed === "function") this.host.changed(name);
				// The guided page (and this panel, if it is open on the record) reload rather than meet
				// the Undo as a conflict on their next save.
				$(document).trigger("sf-record-changed", [doctype, name]);
			})
			.catch((err) => this.sf.toast((err && err.message) || __("It could not be undone."), { tone: "warning" }));
	}

	// ------------------------------------------------------------------ a move's required fields (P6.6)

	status_field() {
		const f = this.flow || {};
		return (this.gate && this.gate.kind === "lost" ? f.lost && f.lost.status_field : f.won && f.won.status_field) || "status";
	}

	start_move(spec) {
		const esc = this.sf.esc;
		this.gate = this.kit.gate_for(this.flow, spec.status) || { kind: "plain", status: spec.status };
		const field = this.status_field();
		const already = this.values[field] === spec.status;
		const label = this.gate.kind === "lost" ? __("Declare lost") : __("Move to {0}", [__(spec.status)]);
		this.frame(
			"",
			already
				? `<div class="fk-foot-row">${this.button("cancel", __("Close"), "secondary")}</div>`
				: `<div class="fk-foot-row">
					${this.button("cancel", __("Cancel"), "secondary")}
					<span class="fk-foot-note">${esc(__("Nothing moves until you do this."))}</span>
					${this.button("commit", label, "primary", "fk-commit")}
				</div>`
		);
		if (this.handle) {
			this.handle.set_title(this.title_text());
			this.handle.set_subtitle(__("Move to {0}", [__(spec.status)]));
		}
		const $stage = this.$root.find(".ge-stage").empty();
		if (already) {
			this.step_head($stage, "", __("Already in {0}", [__(spec.status)]), "");
			return;
		}
		if (!this.flow.can_write) {
			this.step_head($stage, "", __("You can read this record but not change it."), "");
			this.$root.find(".fk-commit").remove();
			return;
		}
		if (this.gate.kind === "lost") {
			this.step_head(
				$stage,
				__("Before it moves"),
				__("Why was it lost?"),
				__("The reasons and competitors are saved on the deal, as the form's Set as Lost does.")
			);
			const $fields = $('<div class="ge-fields fk-lost-fields"></div>').appendTo($stage);
			this.lost_fg = new frappe.ui.FieldGroup({ fields: this.lost_fields(), body: $fields[0], no_submit_on_enter: true });
			this.lost_fg.make();
		} else {
			const fields = this.gate.kind === "won" ? this.kit.field_defs(this.flow, this.gate.fieldnames) : [];
			const step = this.kit.panel_step(
				"move",
				fields.length ? __("Before it moves") : __("Move"),
				fields.length
					? __("{0} needs these filled in. Nothing is saved until you move it.", [__(spec.status)])
					: __("Move this deal to {0}.", [__(spec.status)])
			);
			step.fields = fields;
			this.step_override = step;
			this.step_key = step.key;
			this.step_head($stage, step.title, __("Move to {0}", [__(spec.status)]), step.intro);
			const $fields = $('<div class="ge-fields"></div>').appendTo($stage);
			if (fields.length) this.render_fields(step, $fields, true);
			// A rank the person may not write is drawn read-only; the move could not pass.
			if (fields.some((f) => f.read_only)) {
				$('<p class="ge-readonly-note"></p>').text(__("You cannot change some of these, so you cannot make this move.")).appendTo($stage);
			}
		}
		this.refresh_notices();
		this.render_saveline();
		const first = this.$root.find(".ge-fields input, .ge-fields select, .ge-fields textarea").filter(":visible").first();
		if (first.length) setTimeout(() => first.trigger("focus"), 50);
	}

	commit() {
		if (this.committing || !this.spec || this.spec.kind !== "move" || !this.gate) return;
		if (this.gate.kind === "lost") {
			this.commit_lost();
			return;
		}
		this.collect_step();
		const step = this.step_override || { fields: [] };
		const missing = this.kit.gate_missing(step.fields, this.values);
		if (missing.length) {
			this.notice = {
				kind: "create",
				title: __("Fill these in first"),
				body: missing.map((f) => __(f.label || f.fieldname)).join(", "),
			};
			this.refresh_notices();
			this.focus_field(missing[0].fieldname);
			return;
		}
		this.notice = null;
		const field = this.status_field();
		this.note_base(field);
		this.queue.change(field, this.spec.status);
		this.committing = true;
		this.$root.find(".fk-commit").prop("disabled", true);
		this.queue
			.flush()
			.catch(() => {
				// The reason is on screen (a refusal, a conflict to choose, no signal); nothing moved.
			})
			.finally(() => {
				this.committing = false;
				this.$root.find(".fk-commit").prop("disabled", false);
			});
	}

	commit_lost() {
		const values = this.lost_fg ? this.lost_fg.get_values() : null;
		if (!values) return; // the FieldGroup has marked what is missing
		const spec = this.spec;
		this.committing = true;
		this.notice = null;
		this.refresh_notices();
		this.$root.find(".fk-commit").prop("disabled", true);
		this.send_lost(values)
			.then((result) => {
				if (this.spec !== spec) return;
				if (result && result.conflict) {
					this.sf.toast(
						__("{0} changed this deal since the board loaded. Check it and declare it lost again.", [
							result.modified_by || __("Someone else"),
						]),
						{ tone: "warning", timeout: 8000 }
					);
					this.load(this.doctype, this.name, null);
					return;
				}
				this.committed(result || {});
			})
			.catch((err) => {
				this.notice = { kind: "save", error: err || {}, retry: () => this.commit() };
				this.refresh_notices();
			})
			.finally(() => {
				this.committing = false;
				this.$root.find(".fk-commit").prop("disabled", false);
			});
	}

	committed(result) {
		const spec = this.spec;
		if (!spec || spec.kind !== "move") return;
		const info = { name: this.name, status: spec.status, result, title: this.title_text() };
		if (this.queue) this.queue.cancel();
		if (typeof this.host.committed === "function") this.host.committed(info);
	}

	// ------------------------------------------------------------------ quick add (P6.7)

	start_new(spec) {
		const start = this.kit.quick_start(this.flow, (frappe.session && frappe.session.user) || "", spec.stage || "");
		if (!start) {
			this.sf.state.render(this.$root, "empty", { title: __("Quick add is not set up for {0}.", [__(this.doctype)]) });
			return;
		}
		Object.assign(this.values, start.values);
		this.restore_new();
		// The column it was added from wins over a stage a kept start carried.
		this.carry = Object.assign({}, this.carry || {}, start.carry);
		const fields = this.kit.field_defs(this.flow, start.fieldnames);
		const step = this.kit.panel_step(
			"quick",
			__("Quick add"),
			spec.stage
				? __("A new {0} in {1}. Everything else can be filled in once it exists.", [__(this.doctype), __(spec.stage)])
				: __("A new {0}. Everything else can be filled in once it exists.", [__(this.doctype)])
		);
		step.fields = fields;
		this.step_override = step;
		this.step_key = step.key;
		this.frame(
			"",
			`<div class="fk-foot-row">
				${this.button("cancel", __("Cancel"), "secondary")}
				<button type="button" class="ge-saveline" aria-live="polite"></button>
				${this.button("create", __("Create"), "primary", "fk-create")}
			</div>`
		);
		if (this.handle) {
			this.handle.set_title(__("New {0}", [__(this.doctype)]));
			this.handle.set_subtitle(spec.stage ? __("In {0}", [__(spec.stage)]) : "");
		}
		const $stage = this.$root.find(".ge-stage").empty();
		this.step_head($stage, step.title, __("New {0}", [__(this.doctype)]), step.intro);
		const $fields = $('<div class="ge-fields"></div>').appendTo($stage);
		this.render_fields(step, $fields, true);
		this.refresh_notices();
		this.render_saveline();
		const first = this.$root.find(".ge-fields input, .ge-fields textarea").filter(":visible").first();
		if (first.length) setTimeout(() => first.trigger("focus"), 50);
	}

	still_new(doctype) {
		return this.is_open() && !!this.spec && this.spec.kind === "new" && this.doctype === doctype;
	}

	after_create({ created, still_here }) {
		this.sf.toast(__("{0} created.", [created.name]), { tone: "success", timeout: 4000 });
		if (typeof this.host.changed === "function") this.host.changed(created.name);
		if (still_here && typeof this.host.created === "function") this.host.created({ name: created.name });
	}

	show_created(doctype, name) {
		if (typeof this.host.created === "function") this.host.created({ name });
	}

	render_saveline() {
		super.render_saveline();
		if (this.spec && this.spec.kind === "new") {
			this.$root.find(".fk-create").prop("disabled", !!this.creating).text(this.creating ? __("Creating…") : __("Create"));
		}
	}

	// ------------------------------------------------------------------ actions

	on_action(event, action) {
		event.preventDefault();
		if (action === "done" || action === "cancel") this.request_close();
		else if (action === "commit") this.commit();
		else if (action === "create") this.create();
		else if (action === "next" || action === "prev") {
			const key = this.kit.neighbor(this.flow.steps, this.values, this.step_key, action === "next" ? 1 : -1);
			if (key) this.go_step(key);
		} else if (action === "full") {
			const tab = $(event.currentTarget).attr("data-tab") || null;
			const name = this.name;
			const step = this.step_key;
			this.save_before_moving()
				.then(() => {
					if (typeof this.host.open_full === "function") this.host.open_full(name, step, tab);
				})
				.catch(() => {});
		}
	}

	has_unsaved() {
		if (this.queue && !this.queue.manual && this.queue.has_unsaved()) return true;
		if (this.retired.some((queue) => !queue.manual && queue.has_unsaved())) return true;
		return !!(this.is_new && this.is_open() && (this.new_dirty || this.creating));
	}
}
