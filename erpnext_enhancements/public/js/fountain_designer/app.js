// The designer page's controller: the place (design and step, in the address), the model on screen, the
// live derive, the autosave, and every event, delegated from the page's root on data-fd-* attributes.
//
// What draws comes from the step modules (steps/*.js), the verdict panel (verdict.js) and the helpers in
// ui.js; what an edit does to the model comes from model.js; the address from router.js; the calls and
// the debounce from api.js. This file owns the DOM and the order things happen in.
//
// env: { page, root, sf, frappe, win, doc, demo_transport: () => Promise<transport>, __ }

import * as M from "./model.js";
import * as R from "./router.js";
import { create_api, create_deriver, debounce, PERMISSION_METHOD } from "./api.js";
import { strip_html, verdict_html } from "./verdict.js";
import { card, field_id, fmt } from "./ui.js";
import * as start from "./steps/start.js";
import * as site from "./steps/site.js";
import * as features from "./steps/features.js";
import * as basins from "./steps/basins.js";
import * as pump_room from "./steps/pump_room.js";
import * as review from "./steps/review.js";
import * as CALC from "./steps/calculator.js";
import * as outputs from "./steps/outputs.js";

const STEP_MODULES = { start, site, features, basins, pump_room, review, outputs };
const ADV_KEY = "sf_fountain_designer.adv";
const SAVE_DELAY = 1500;
const SEARCH_DELAY = 250;

export function mount(env) {
	return new Designer(env);
}

class Designer {
	constructor(env) {
		this.env = env;
		this.sf = env.sf;
		this.win = env.win;
		this.doc = env.doc;
		this.root = env.root;
		const __ = env.__ || ((text, args) => this.sf.t(text, args));
		this.h = {
			esc: this.sf.esc,
			__,
			chip: this.sf.chip,
			state_html: (kind, opts) => this.sf.state.html(kind, opts),
			parser: typeof this.win.DOMParser === "function" ? new this.win.DOMParser() : null,
		};
		this.place = { design: null, step: "start", demo: false };
		this.started_demo = null; // the demo flag the current api was built for
		this.ready = null;
		this.boot = null;
		this.model = null;
		this.meta = null; // the record's own fields: name, design_title, project, opportunity, customer, status
		this.reply = null;
		this.derive_state = { pending: false, error: "" };
		this.designs_state = { kind: "loading" };
		this.new_form = { kit: null, title: "", mode: "quick" };
		this.dirty = false;
		this.edit_seq = 0;
		this.save_state = { saving: false, error: "" };
		this.acking = null;
		this.busy = false;
		this.last_push = "";
		this.open_token = 0;
		this.list_token = 0;
		this.shown_step = null;
		this.adv_open = this.load_adv();
		this.autosave = debounce(() => this.save(), SAVE_DELAY);
		this.search = debounce((input) => this.run_search(input), SEARCH_DELAY);
		this.bind();
		this.win.addEventListener("beforeunload", (e) => {
			if (this.dirty && this.can_write()) {
				this.autosave.flush();
				e.preventDefault();
				e.returnValue = "";
			}
		});
	}

	// --- place and start-up ----------------------------------------------------------------------------

	// Every show: a reload, a link, Back or Forward (the router re-renders this page and copies the query
	// into frappe.route_options), or a return from another page.
	show() {
		const fr = this.env.frappe;
		// Every entry says where it is, and an entry with no keys is the list, out of the demo: Back onto
		// the bare address the page was opened at must land there, not keep the design on screen.
		const place = R.place_from(fr.route_options, this.win.location.search);
		if (fr.route_options && !Object.keys(fr.route_options).length) fr.route_options = null;
		if (this.started_demo !== null && place.demo !== this.started_demo) {
			// Into or out of the demo: a different source of every answer, so start again.
			this.autosave.flush();
			this.reset();
		}
		this.place.demo = place.demo;
		this.start_up().then((ok) => {
			if (ok) this.go_place(place);
		});
	}

	reset() {
		this.ready = null;
		this.boot = null;
		this.model = null;
		this.meta = null;
		this.reply = null;
		this.dirty = false;
		this.deriver && this.deriver.cancel();
		this.designs_state = { kind: "loading" };
	}

	start_up() {
		if (this.ready) return this.ready;
		const sf = this.sf;
		const demo = this.place.demo;
		this.started_demo = demo;
		sf.state.render(this.root, "loading", { title: this.h.__("Loading the Fountain Designer…") });
		const real = (method, args, opts) => sf.call(method, args, opts);
		const transport = demo
			? this.env.demo_transport().then((t) => (method, args, opts) => (method === PERMISSION_METHOD ? real(method, args, opts) : t(method, args, opts)))
			: Promise.resolve(real);
		this.ready = transport
			.then((t) => {
				this.api = create_api(t);
				this.deriver = create_deriver({
					derive: (model) => this.api.derive(model),
					delay: 400,
					set_timeout: (fn, ms) => this.win.setTimeout(fn, ms),
					clear_timeout: (id) => this.win.clearTimeout(id),
					on_pending: () => this.set_derive({ pending: true, error: "" }),
					on_result: (reply) => this.derived(reply),
					on_error: (err) => this.set_derive({ pending: false, error: this.h.__("Could not work out the design: {0}", [(err && err.message) || this.h.__("no answer")]) }),
				});
				return this.api.check_permission();
			})
			.then((allowed) => {
				if (!allowed) {
					sf.state.render(this.root, "no-access", { title: this.h.__("You do not have access to Water Feature Designs.") });
					return false;
				}
				return this.api.boot().then((boot) => {
					this.boot = boot || {};
					this.draw_shell();
					this.load_designs();
					return true;
				});
			})
			.catch((err) => {
				this.ready = null;
				this.failed(this.root, err || {}, demo);
				return false;
			});
		return this.ready;
	}

	failed(el, err, demo) {
		const { __ } = this.h;
		if (err.session_expired) {
			return this.sf.state.render(el, "error", {
				title: __("You have been signed out."),
				body: __("Reload the page to sign in again."),
				action_label: __("Reload"),
				on_action: () => this.win.location.reload(),
			});
		}
		if (err.status === 403) {
			return this.sf.state.render(el, "no-access", { title: __("You do not have access to Water Feature Designs."), body: err.message });
		}
		// The server side has been on every site since v1.768.0, so a failure here is a failure, not a missing
		// install: the panel says what the server said and offers Try again. The training demo is a link on
		// Start (v1.771.0, phase H).
		this.sf.state.render(el, "error", {
			title: __("Could not open the Fountain Designer."),
			body: err.message || (demo ? "" : __("Try again in a moment. If it keeps failing, tell the design team.")),
			on_action: () => this.show_again(),
		});
	}

	// A second button in a state panel, beside its own (sf_kit draws one).
	extra_button(el, label, on_click) {
		const btn = this.doc.createElement("button");
		btn.type = "button";
		btn.className = "sf-btn sf-btn--secondary fd-demo-link";
		btn.textContent = label;
		btn.addEventListener("click", on_click);
		const action = el.querySelector(".sf-state-action");
		if (action) action.insertAdjacentElement("afterend", btn);
		else el.appendChild(btn);
	}

	// Ask before something that cannot be undone: the Desk's own dialog when it is there.
	ask(message, yes) {
		const fr = this.env.frappe;
		if (fr && typeof fr.confirm === "function") return fr.confirm(this.h.esc(message), yes);
		if (this.win.confirm(message)) yes();
	}

	show_again() {
		this.ready = null;
		this.start_up().then((ok) => ok && this.go_place(this.place));
	}

	// Land on a place: open its design when it changed, put the step right for the mode, correct the
	// address in place. push: the person chose it (a new history entry).
	go_place(place, push) {
		if (place.demo !== this.started_demo) {
			this.place = Object.assign({}, place);
			R.write_address(this.win, this.place, push);
			this.autosave.flush();
			this.reset();
			return this.start_up().then((ok) => ok && this.go_place(place));
		}
		const changed = (place.design || null) !== (this.meta ? this.meta.name || null : null);
		if (changed) {
			this.autosave.flush();
			this.place = { design: place.design || null, step: place.design ? place.step : "start", demo: place.demo };
			R.write_address(this.win, this.place, push);
			if (place.design) return this.open_design(place.design);
			this.close_design();
			return this.draw();
		}
		const mode = this.model ? this.model.mode : "quick";
		const step = this.meta ? R.normalize_step(mode, place.step) : "start";
		if (step !== this.place.step) this.autosave.flush();
		this.place = { design: this.place.design, step, demo: place.demo };
		R.write_address(this.win, this.place, push);
		this.draw();
	}

	// The page head's New design: back to Start with no design open, the name field ready.
	new_design() {
		Promise.resolve(this.go_place({ design: null, step: "start", demo: this.place.demo }, true)).then(() => {
			const input = this.stage && this.stage.querySelector("#" + field_id("new.title"));
			if (input) input.focus();
		});
	}

	go_step(step, focus_path) {
		this.acking = null;
		this.pending_focus = focus_path || null;
		this.go_place(Object.assign({}, this.place, { step }), true);
	}

	// What belongs to the design on screen and must not follow the person to another one.
	forget_design_state() {
		this.last_push = "";
		this.acking = null;
		this.pending_focus = null;
	}

	close_design() {
		this.deriver.cancel();
		this.forget_design_state();
		this.model = null;
		this.meta = null;
		this.reply = null;
		this.dirty = false;
		this.set_derive({ pending: false, error: "" }, true);
		this.load_designs();
	}

	open_design(name) {
		const token = ++this.open_token;
		this.deriver.cancel();
		this.forget_design_state();
		this.model = null;
		this.meta = null;
		this.reply = null;
		this.draw_rail();
		this.sf.state.render(this.stage, "loading", { title: this.h.__("Opening {0}…", [name]) });
		this.api.get_design(name).then(
			(d) => {
				if (token !== this.open_token) return;
				if (!(d && d.model && d.model.schema === M.SCHEMA)) return this.cannot_open(name);
				const model = d.model;
				this.model = model;
				this.lookup_address = (model.site && model.site.address) || "";
				this.meta = {
					name: d.name,
					design_title: d.design_title || "",
					project: d.project || "",
					opportunity: d.opportunity || "",
					customer: d.customer || "",
					status: d.status || "",
					docstatus: d.docstatus || 0,
					can_write: d.can_write !== false && !d.docstatus,
				};
				this.dirty = false;
				this.place.step = R.normalize_step(model.mode, this.place.step);
				R.write_address(this.win, this.place, false);
				this.deriver.now(this.model);
				this.draw();
			},
			(err) => {
				if (token !== this.open_token) return;
				const { __ } = this.h;
				if (err && err.status === 404) {
					return this.sf.state.render(this.stage, "empty", {
						title: __("Design {0} was not found.", [name]),
						action_label: __("All designs"),
						on_action: () => this.go_place({ design: null, step: "start", demo: this.place.demo }, true),
					});
				}
				this.failed(this.stage, err || {}, true);
			}
		);
	}

	// A design with no schema-2 model (an old wizard record, or one made in the form): never opened as a
	// blank design, whose first autosave would write over what is stored.
	cannot_open(name) {
		const { __ } = this.h;
		const fr = this.env.frappe;
		this.model = null;
		this.meta = null;
		this.draw();
		this.sf.state.render(this.stage, "error", {
			title: __("{0} was not made in the Fountain Designer.", [name]),
			body: __("It is left as it is. Open it in the Water Feature Design form, or start a new design here."),
			action_label: __("All designs"),
			on_action: () => this.go_place({ design: null, step: "start", demo: this.place.demo }, true),
		});
		if (fr && typeof fr.set_route === "function") {
			this.extra_button(this.stage, __("Open the form"), () => fr.set_route("Form", "Water Feature Design", name));
		}
	}

	load_designs() {
		const token = ++this.list_token;
		this.designs_state = { kind: "loading" };
		this.paint_designs();
		this.api.list_designs().then(
			(r) => {
				if (token !== this.list_token) return;
				this.designs_state = { kind: "ready", rows: (r && r.designs) || [] };
				this.paint_designs();
			},
			(err) => {
				if (token !== this.list_token) return;
				this.designs_state = { kind: "error", message: (err && err.message) || "" };
				this.paint_designs();
			}
		);
	}

	paint_designs() {
		const el = this.root.querySelector("[data-fd-designs]");
		if (el) el.innerHTML = start.designs_html(this.ctx());
	}

	// --- drawing ----------------------------------------------------------------------------------------

	draw_shell() {
		const { esc, __ } = this.h;
		this.root.innerHTML = `
			<div class="fd-app">
				${this.place.demo ? `<p class="fd-demo-banner" role="note">${this.sf.chip(__("Demo data"), "warn")} ${esc(__("Answers come from a fixture in the browser. Nothing is saved to ERPNext."))}</p>` : ""}
				<nav class="fd-rail" aria-label="${esc(__("Steps"))}"><ol class="fd-rail-list" data-fd-rail></ol></nav>
				<div class="fd-strip" data-fd-strip aria-hidden="true"></div>
				<main class="fd-stage" data-fd-stage></main>
				<aside class="fd-verdict" aria-label="${esc(__("Verdict"))}" data-fd-verdict></aside>
				<footer class="fd-footer" data-fd-footer></footer>
			</div>`;
		this.stage = this.root.querySelector("[data-fd-stage]");
		const page = this.env.page;
		if (page && page.set_indicator) {
			if (this.place.demo) page.set_indicator(__("Demo data"), "orange");
			else if (page.clear_indicator) page.clear_indicator();
		}
	}

	draw() {
		if (!this.stage) return;
		this.draw_rail();
		this.draw_stage();
		this.paint();
	}

	ctx() {
		const model = this.model || M.blank_model("quick");
		const boot = this.boot || {};
		const kit = (boot.kits || []).find((k) => k.key === model.kit) || null;
		return {
			h: this.h,
			boot,
			model,
			kit,
			reply: this.reply,
			values: (this.reply && this.reply.values) || {},
			full: model.mode === "full",
			design: this.meta,
			can: boot.can || {},
			can_write: this.can_write(),
			can_create: Boolean(boot.can && boot.can.create),
			designs_state: this.designs_state,
			demo: Boolean(this.place && this.place.demo),
			new_form: this.new_form,
			acking: this.acking,
			calc: this.calc,
			busy: this.busy,
			last_push: this.last_push,
			adv: (id) => this.adv_open.has(id),
			section_card: (section) => this.section_card(section),
		};
	}

	can_write() {
		const can = (this.boot && this.boot.can) || {};
		return Boolean(can.write && (!this.meta || this.meta.can_write !== false));
	}

	// The setup policy of a section whose rows are cards of their own (features, basins).
	section_card(section) {
		const { __ } = this.h;
		const meta = M.SECTIONS.find((s) => s.key === section) || { label: section };
		const presets = ((this.boot.sections || []).find((s) => s.key === section) || {}).presets || [];
		return card(this.h, {
			id: "setup-" + section,
			title: __("{0} setup", [__(meta.label)]),
			section,
			full: true,
			model: this.model,
			presets,
			simple: "",
		});
	}

	draw_rail() {
		const rail = this.root.querySelector("[data-fd-rail]");
		if (!rail) return;
		const { esc, __ } = this.h;
		const mode = this.model ? this.model.mode : "quick";
		const steps = this.meta ? R.steps_for(mode) : ["start"];
		const current = this.place.step;
		const done = R.steps_for(mode).indexOf(current);
		rail.innerHTML = R.steps_for(mode)
			.map((key, i) => {
				const on = key === current;
				const open = steps.includes(key);
				const tick = open && i < done ? `<span class="fd-rail-tick" aria-hidden="true">✓</span>` : "";
				return `<li><button type="button" class="fd-rail-step${on ? " fd-rail-step--on" : ""}" data-fd-step="${key}"
					${on ? 'aria-current="step"' : ""}${open ? "" : " disabled"}>
					<span class="fd-rail-n" aria-hidden="true">${i + 1}</span><span>${esc(__(R.step_label(key)))}</span>${tick}</button></li>`;
			})
			.join("");
		// On a phone the rail is a bar that scrolls sideways: keep the current step in it.
		const nav = rail.parentElement;
		const on = rail.querySelector(".fd-rail-step--on");
		if (nav && on && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = Math.max(0, on.parentElement.offsetLeft - 16);
	}

	draw_stage() {
		const step = this.meta ? this.place.step : "start";
		const mod = STEP_MODULES[step] || start;
		const focused = this.doc.activeElement && this.root.contains(this.doc.activeElement) ? this.doc.activeElement.id : null;
		// A redraw of the same step keeps what the person had open or half typed: the drawers and a sign-off note.
		const same = this.shown_step === step;
		const open = same ? Array.from(this.stage.querySelectorAll("details[open][data-fd-drawer]")).map((d) => d.getAttribute("data-fd-drawer")) : [];
		const note = same ? this.stage.querySelector("#fd-ack-note") : null;
		const note_text = note ? note.value : "";
		this.stage.innerHTML = mod.render(this.ctx());
		for (const key of open) {
			const d = this.stage.querySelector(`details[data-fd-drawer="${css_escape(key)}"]`);
			if (d) d.open = true;
		}
		const again_note = note_text && this.stage.querySelector("#fd-ack-note");
		if (again_note) again_note.value = note_text;
		this.stage.setAttribute("data-fd-on", step);
		if (step === "start" && this.meta) this.mount_links();
		const moved = this.shown_step !== step;
		this.shown_step = step;
		this.signature = this.shape();
		if (this.pending_focus) {
			const target = this.stage.querySelector(`[data-fd-path="${css_escape(this.pending_focus)}"]`);
			this.pending_focus = null;
			if (target) return void target.focus();
		}
		if (moved) {
			const title = this.stage.querySelector("[data-fd-stage-title]");
			if (title && this.started) title.focus({ preventScroll: false });
			this.started = true;
		} else if (focused) {
			const again = this.doc.getElementById(focused);
			if (again) again.focus();
		}
	}

	// The verdict, the phone strip, the footer, every derived value and every input of an auto section,
	// in place: the step is not drawn again, so typing is never interrupted.
	paint() {
		const v = this.root.querySelector("[data-fd-verdict]");
		if (v) {
			v.innerHTML = this.meta
				? verdict_html(this.reply, this.h, Object.assign({ mode: this.model && this.model.mode }, this.derive_state))
				: `<p class="fd-muted">${this.h.esc(this.h.__("Open or start a design to see its verdict."))}</p>`;
		}
		const strip = this.root.querySelector("[data-fd-strip]");
		if (strip) strip.innerHTML = this.meta ? strip_html(this.reply, this.h) : "";
		this.paint_footer();
		if (!this.stage || !this.meta) return;
		const values = (this.reply && this.reply.values) || {};
		for (const el of this.stage.querySelectorAll("[data-fd-derived]")) {
			const value = M.get_in(values, el.getAttribute("data-fd-derived"));
			const text = fmt(value, { unit: el.getAttribute("data-fd-unit"), digits: el.getAttribute("data-fd-digits") });
			el.textContent = text || "—";
		}
		for (const el of this.stage.querySelectorAll("[data-fd-path]")) {
			if (el === this.doc.activeElement || el.hasAttribute("data-fd-catalog")) continue;
			const path = el.getAttribute("data-fd-path");
			const value = M.get_in(this.model, path);
			if (el.type === "checkbox") el.checked = Boolean(value);
			else if (el.tagName !== "BUTTON") {
				const text = value === null || value === undefined ? "" : String(value);
				if (el.value !== text) el.value = text;
			}
		}
	}

	paint_footer() {
		const footer = this.root.querySelector("[data-fd-footer]");
		if (!footer) return;
		if (!this.meta) return void (footer.innerHTML = "");
		const { esc, __ } = this.h;
		const mode = this.model.mode;
		const prev = R.prev_step(mode, this.place.step);
		const next = R.next_step(mode, this.place.step);
		const s = this.save_state;
		const status = !this.can_write()
			? __("Read only")
			: s.error
				? __("Not saved: {0}", [s.error])
				: s.saving
					? __("Saving…")
					: this.dirty
						? __("Unsaved changes")
						: __("Saved");
		footer.innerHTML = `
			<p class="fd-save${s.error ? " fd-save--error" : ""}" role="status">${esc(status)}${
			s.error ? ` <button type="button" class="fd-link" data-fd-action="save-now">${esc(__("Try again"))}</button>` : ""
		}</p>
			<div class="fd-footer-nav">
				${prev ? `<button type="button" class="sf-btn sf-btn--secondary" data-fd-step="${prev}">${esc(__("Back: {0}", [__(R.step_label(prev))]))}</button>` : ""}
				${next ? `<button type="button" class="sf-btn sf-btn--primary" data-fd-step="${next}">${esc(__("Next: {0}", [__(R.step_label(next))]))}</button>` : ""}
			</div>`;
	}

	// The rows the step draws: when a derive adds or removes one, the step is drawn again.
	shape() {
		const m = this.model;
		if (!m) return "";
		const ids = (list) => (list || []).map((r) => (r && (r.id || r.slot)) || "").join(",");
		return [ids(m.features), ids(m.basins), ids(M.get_in(m, "network.segments")), ids(M.get_in(m, "network.pumps")), ids(m.equipment), JSON.stringify(m.setup)].join("|");
	}

	set_derive(state, quiet) {
		this.derive_state = state;
		if (!quiet) this.paint();
	}

	derived(reply) {
		this.reply = reply || null;
		if (reply && reply.model) this.model = M.merge_derived(this.model, reply.model);
		this.derive_state = { pending: false, error: "" };
		// Review and Outputs are drawn from the reply itself; any other step only when a row came or went
		// (an input being typed in is never drawn over).
		if (this.shape() !== this.signature || ["review", "outputs"].includes(this.place.step)) this.draw_stage();
		this.paint();
	}

	// --- edits ------------------------------------------------------------------------------------------

	edit(path, value, structural) {
		if (!this.model || !this.can_write()) return;
		this.model = M.edit(this.model, path, value);
		this.changed(structural);
	}

	changed(structural) {
		this.dirty = true;
		this.edit_seq += 1;
		this.deriver.schedule(this.model);
		this.autosave();
		if (structural) {
			this.draw_rail();
			this.draw_stage();
		}
		this.paint();
	}

	set_field(fieldname, value) {
		if (!this.meta || !this.can_write() || (this.meta[fieldname] || "") === (value || "")) return;
		this.meta[fieldname] = value || "";
		this.dirty = true;
		this.edit_seq += 1;
		this.autosave();
		this.paint_footer();
	}

	// Resolves true once nothing is left unsaved, false when the save failed. One request at a time: a call
	// while one is out waits for it, then saves again if edits came in meanwhile.
	save() {
		if (this.save_promise) return this.save_promise.then(() => (this.dirty ? this.save() : true));
		if (!this.meta || !this.meta.name || !this.dirty || !this.can_write()) return Promise.resolve(true);
		const seq = this.edit_seq;
		const name = this.meta.name;
		const fields = { design_title: this.meta.design_title, project: this.meta.project || null, opportunity: this.meta.opportunity || null, customer: this.meta.customer || null };
		this.save_state = { saving: true, error: "" };
		this.paint_footer();
		this.save_promise = this.api
			.save_design(name, this.model, fields)
			.then(
				(r) => {
					this.save_state = { saving: false, error: "" };
					if (this.meta && this.meta.name === name) {
						if (seq === this.edit_seq) this.dirty = false;
						if (r && r.status) this.meta.status = r.status;
					}
					this.paint_footer();
					return true;
				},
				(err) => {
					this.save_state = { saving: false, error: (err && err.message) || this.h.__("no answer") };
					this.paint_footer();
					return false;
				}
			)
			.then((ok) => {
				this.save_promise = null;
				return ok;
			});
		return this.save_promise;
	}

	// Every edit on the server, now: before a call that reads the stored design (a push builds from the
	// stored BOM) or writes beside it (a sign-off, which a save landing later must not undo).
	saved() {
		this.autosave.cancel();
		return this.save();
	}

	not_saved(what) {
		const { __ } = this.h;
		this.busy = false;
		this.draw_stage();
		this.sf.toast(__("The design could not be saved, so {0}: {1}", [__(what), this.save_state.error]), { tone: "warning" });
	}

	create() {
		const { __ } = this.h;
		const form = this.new_form;
		const title = String(form.title || "").trim();
		if (!title) {
			const input = this.stage.querySelector(`#${field_id("new.title")}`);
			this.sf.toast(__("Give the design a name first."), { tone: "warning" });
			if (input) input.focus();
			return;
		}
		const kits = this.boot.kits || [];
		const kit_key = form.kit == null ? (kits[0] && kits[0].key) || "" : form.kit;
		const kit = kits.find((k) => k.key === kit_key) || null;
		const model = M.model_from_kit(kit, form.mode);
		if (!kit) Object.assign(model, M.add_basin(M.add_feature(model, ((this.boot.families || [])[0] || {}).key || null)));
		this.busy = true;
		this.draw_stage();
		this.api.save_design(null, model, { design_title: title }).then(
			(r) => {
				this.busy = false;
				this.new_form = { kit: null, title: "", mode: "quick" };
				this.go_place({ design: r.name, step: "site", demo: this.place.demo }, true);
			},
			(err) => {
				this.busy = false;
				this.draw_stage();
				this.sf.toast(__("Could not create the design: {0}", [(err && err.message) || __("no answer")]), { tone: "warning" });
			}
		);
	}

	push(kind) {
		const { esc, __ } = this.h;
		if (!this.meta || !this.meta.name || this.busy) return;
		const name = this.meta.name;
		this.busy = true;
		this.draw_stage();
		this.saved().then((ok) => (ok ? this.send_push(kind, name) : this.not_saved(__("nothing was pushed"))));
	}

	send_push(kind, name) {
		const { esc, __ } = this.h;
		const call = kind === "quotation" ? this.api.push_quotation(name) : this.api.push_material_request(name);
		call.then(
			(r) => {
				this.busy = false;
				const doctype = (r && r.doctype) || (kind === "quotation" ? "Quotation" : "Material Request");
				const route = r && r.name && !r.demo ? `/desk/${slug(doctype)}/${encodeURIComponent(r.name)}` : "";
				const words = __("{0} {1}: {2} item lines, {3} text lines.", [__(doctype), r && r.name, (r && r.lines) || 0, (r && r.text_lines) || 0]);
				// Shown on its own design only: the person may have opened another while the push was out.
				if (this.meta && this.meta.name === name) {
					this.last_push = route ? `<a href="${esc(route)}">${esc(words)}</a>` : esc(words) + (r && r.demo ? " " + esc(__("(demo: nothing was created)")) : "");
				}
				this.draw_stage();
				this.sf.toast(
					r && r.demo
						? __("Demo: a {0} would be made from the design. Nothing was created.", [__(doctype)])
						: __("{0} {1} made from the design.", [__(doctype), r && r.name]),
					{ tone: r && r.demo ? "info" : "success" }
				);
			},
			(err) => {
				this.busy = false;
				this.draw_stage();
				this.sf.toast((err && err.message) || __("Could not push the design."), { tone: "warning" });
			}
		);
	}

	// Send design: save, have the server make the chosen paper into a PDF on the design, then open
	// frappe's email composer on the record with it attached and the customer's address and body filled
	// in. The person sends it there; the composer links the email to the design's timeline.
	send_design(print_format) {
		const { __ } = this.h;
		if (!this.meta || !this.meta.name || this.busy) return;
		const name = this.meta.name;
		this.busy = true;
		this.draw_stage();
		this.saved().then((ok) => {
			if (!ok) return this.not_saved(__("nothing was sent"));
			this.api.email_draft(name, print_format).then(
				(r) => {
					this.busy = false;
					this.draw_stage();
					if (!r || r.demo) {
						this.sf.toast(__("Demo: the design would be emailed with its paper attached. Nothing was sent."), { tone: "info" });
						return;
					}
					this.open_composer(name, r);
				},
				(err) => {
					this.busy = false;
					this.draw_stage();
					this.sf.toast((err && err.message) || __("Could not prepare the email."), { tone: "warning" });
				}
			);
		});
	}

	open_composer(name, draft) {
		const fr = this.env.frappe;
		if (!fr || !fr.views || !fr.views.CommunicationComposer) {
			this.sf.toast(this.h.__("The email window is not available here. Open the design in the Desk form to send it."), { tone: "warning" });
			return;
		}
		const file = draft.attachment;
		const composer = new fr.views.CommunicationComposer({
			doc: { doctype: "Water Feature Design", name },
			subject: draft.subject,
			recipients: draft.recipients || "",
			message: draft.message,
			attachments: file ? [file] : [],
		});
		// The composer lists a passed attachment without ticking it (v16 communication.js); this ticks it.
		if (file && typeof composer.render_attachment_rows === "function") composer.render_attachment_rows(file);
	}

	// The Calculator drawer (steps/calculator.js). Its state outlives a redraw and a step change, never the
	// page: { key, raw: { input: text }, result, error, busy }.
	calc_state() {
		if (!this.calc) {
			const s = CALC.spec(this.boot);
			this.calc = { key: s ? s.key : null, raw: CALC.default_raw(s), result: null, error: null, busy: false };
		}
		return this.calc;
	}

	calc_input(path, el, committed) {
		if (path === CALC.PICK) {
			if (!committed) return;
			const s = CALC.spec(this.boot, el.value);
			this.calc_seq = (this.calc_seq || 0) + 1;
			this.calc = { key: s ? s.key : null, raw: CALC.default_raw(s), result: null, error: null, busy: false };
			return this.draw_stage();
		}
		this.calc_state().raw[path.slice(CALC.PREFIX.length)] = el.type === "checkbox" ? el.checked : el.value;
	}

	run_calc() {
		const { __ } = this.h;
		const state = this.calc_state();
		const s = CALC.spec(this.boot, state.key);
		if (!s || state.busy) return;
		const { inputs, errors } = CALC.coerce(s, state.raw);
		if (errors.length) {
			state.result = null;
			state.error = __("These do not read as a number or a list: {0}.", [errors.join(", ")]);
			return this.draw_stage();
		}
		// Only the newest answer is drawn: another calc picked, or another run, makes this one stale.
		const seq = (this.calc_seq = (this.calc_seq || 0) + 1);
		state.busy = true;
		state.error = null;
		this.draw_stage();
		return Promise.resolve(this.api.run_calc(s.key, inputs)).then(
			(r) => {
				if (seq !== this.calc_seq || this.calc !== state) return;
				state.busy = false;
				state.result = r || null;
				this.draw_stage();
			},
			(err) => {
				if (seq !== this.calc_seq || this.calc !== state) return;
				state.busy = false;
				state.result = null;
				state.error = String((err && err.message) || "") || __("The calculation could not run.");
				this.draw_stage();
			}
		);
	}

	acknowledge(key, note) {
		const { __ } = this.h;
		if (!this.meta || !this.meta.name) return;
		const name = this.meta.name;
		this.busy = true;
		this.saved().then((ok) => (ok ? this.send_ack(name, key, note) : this.not_saved(__("the warning was not signed off"))));
	}

	send_ack(name, key, note) {
		const { __ } = this.h;
		this.api.acknowledge_issue(name, key, note).then(
			(r) => {
				this.busy = false;
				if (!this.meta || this.meta.name !== name) return;
				this.acking = null;
				if (r && Array.isArray(r.acks)) {
					this.model = M.set_in(this.model, "acks", r.acks);
					// The stored model now holds the sign-off. An edit made while it was being written may have
					// gone out with the old acks, so the model is saved once more, after it.
					this.dirty = true;
					this.edit_seq += 1;
					this.autosave();
				}
				this.deriver.now(this.model);
				this.draw_stage();
				this.sf.toast(__("Warning signed off."), { tone: "success" });
			},
			(err) => {
				this.busy = false;
				this.sf.toast((err && err.message) || __("Could not sign it off."), { tone: "warning" });
			}
		);
	}

	// --- the catalog typeahead ------------------------------------------------------------------------

	run_search(input) {
		const cls = input.getAttribute("data-fd-catalog");
		const list = this.doc.getElementById(input.getAttribute("aria-controls"));
		if (!list || !input.isConnected) return;
		const token = (input._fd_token = (input._fd_token || 0) + 1);
		this.api.catalog_search(cls, input.value).then(
			(rows) => {
				if (token !== input._fd_token || !input.isConnected) return;
				const { esc, __ } = this.h;
				const items = (Array.isArray(rows) ? rows : []).slice(0, 12);
				list.innerHTML = items.length
					? items
							.map(
								(r, i) => `<li role="option" id="${esc(input.id)}-o${i}" class="fd-picker-option" data-fd-item="${esc(r.item)}" aria-selected="false">
						<strong>${esc(r.item_name || r.item)}</strong> <span class="fd-muted">${esc(r.item)}</span>
						${r.generic ? this.h.chip(__("Generic"), "warn") : ""}<br><span class="fd-muted">${esc(r.description || "")}</span></li>`
							)
							.join("")
					: `<li class="fd-picker-empty">${esc(__("Nothing in the catalog matches."))}</li>`;
				list.hidden = false;
				input.setAttribute("aria-expanded", "true");
			},
			() => {
				if (token !== input._fd_token) return;
				list.innerHTML = `<li class="fd-picker-empty">${this.h.esc(this.h.__("Could not search the catalog."))}</li>`;
				list.hidden = false;
			}
		);
	}

	close_picker(input) {
		const list = this.doc.getElementById(input.getAttribute("aria-controls"));
		if (list) list.hidden = true;
		input.setAttribute("aria-expanded", "false");
		input.removeAttribute("aria-activedescendant");
	}

	pick(input, item) {
		this.close_picker(input);
		input.value = item;
		this.edit(input.getAttribute("data-fd-path"), item);
	}

	// --- events -----------------------------------------------------------------------------------------

	bind() {
		const root = this.root;
		root.addEventListener("click", (e) => this.on_click(e));
		root.addEventListener("input", (e) => this.on_input(e, false));
		root.addEventListener("change", (e) => this.on_input(e, true));
		root.addEventListener("keydown", (e) => this.on_key(e));
		root.addEventListener("submit", (e) => this.on_submit(e));
		// An option takes no focus: pressing it must not blur the input, or the list closes under a slow
		// click or a touch before the click lands.
		root.addEventListener("mousedown", (e) => {
			if (e.target.closest && e.target.closest(".fd-picker-list")) e.preventDefault();
		});
		root.addEventListener("focusout", (e) => {
			const input = e.target;
			if (input && input.hasAttribute && input.hasAttribute("data-fd-catalog")) {
				this.win.setTimeout(() => {
					if (!input.parentNode || !input.parentNode.contains(this.doc.activeElement)) this.close_picker(input);
				}, 150);
			}
		});
	}

	on_click(e) {
		const t = e.target.closest ? e.target : e.target.parentElement;
		const el = t && t.closest("[data-fd-step],[data-fd-goto],[data-fd-open],[data-fd-action],[data-fd-remove],[data-fd-tile],[data-fd-adv],[data-fd-remedy],[data-fd-ack],[data-fd-item],.sf-state-action");
		if (!el || !this.root.contains(el)) return;
		const a = (name) => el.getAttribute(name);
		if (el.hasAttribute("data-fd-step")) return this.go_step(a("data-fd-step"));
		if (el.hasAttribute("data-fd-goto")) return this.go_step(a("data-fd-goto"), a("data-fd-focus"));
		if (el.hasAttribute("data-fd-open")) return this.go_place({ design: a("data-fd-open"), step: "review", demo: this.place.demo }, true);
		if (el.hasAttribute("data-fd-remove")) return this.remove(a("data-fd-remove"), a("data-fd-id"));
		if (el.hasAttribute("data-fd-tile")) return this.choose_tile(a("data-fd-tile"), a("data-fd-value"));
		if (el.hasAttribute("data-fd-adv")) return this.toggle_adv(el);
		if (el.hasAttribute("data-fd-remedy")) return this.remedy(a("data-fd-remedy"), Number(a("data-fd-n")));
		if (el.hasAttribute("data-fd-ack")) {
			this.acking = a("data-fd-ack");
			this.draw_stage();
			const note = this.stage.querySelector("#fd-ack-note");
			if (note) note.focus();
			return;
		}
		if (el.hasAttribute("data-fd-item")) {
			const input = el.closest(".fd-picker").querySelector("[data-fd-catalog]");
			return this.pick(input, a("data-fd-item"));
		}
		if (el.classList.contains("sf-state-action") && el.closest("[data-fd-designs]")) return this.load_designs();
		switch (a("data-fd-action")) {
			case "create":
				return this.create();
			case "all-designs":
				return this.go_place({ design: null, step: "start", demo: this.place.demo }, true);
			case "open-demo":
				return this.go_place({ design: null, step: "start", demo: true }, true);
			case "add-feature": {
				// The design's own family, else the first of boot.families (README, the contract).
				const first = (this.model.features || [])[0];
				const family = (first && first.family) || ((this.boot.families || [])[0] || {}).key || null;
				this.model = M.add_feature(this.model, family);
				return this.changed(true);
			}
			case "add-basin":
				// Adding a basin is an edit of the basins: the section is the person's from here on.
				this.model = M.add_basin(this.model);
				this.model = M.edit(this.model, "basins", this.model.basins);
				return this.changed(true);
			case "push-quotation":
				return this.push("quotation");
			case "push-mr":
				return this.push("material_request");
			case "send-design": {
				const select = this.stage && this.stage.querySelector("[data-fd-send-format]");
				return this.send_design(select ? select.value : null);
			}
			case "ack-cancel":
				this.acking = null;
				return this.draw_stage();
			case "save-now":
				return this.save();
			case "site-refresh":
				if (!this.model || !this.can_write()) return;
				this.lookup_address = (this.model.site && this.model.site.address) || "";
				this.model = M.clear_site_lookups(this.model);
				return this.changed(true);
		}
	}

	on_input(e, committed) {
		const el = e.target;
		if (!el || !el.getAttribute) return;
		if (el.hasAttribute("data-fd-catalog")) {
			if (!committed) return this.search(el);
			// Cleared: back to generic. Anything else typed without a pick goes back to what is stored.
			if (!el.value.trim()) return this.edit(el.getAttribute("data-fd-path"), null);
			const stored = M.get_in(this.model, el.getAttribute("data-fd-path"));
			el.value = stored == null ? "" : String(stored);
			return;
		}
		if (el.hasAttribute("data-fd-policy")) return committed && this.set_policy(el.getAttribute("data-fd-policy"), el.value);
		if (el.hasAttribute("data-fd-field")) return this.set_field(el.getAttribute("data-fd-field"), el.value.trim());
		const path = el.getAttribute("data-fd-path");
		if (!path) return;
		if (path.startsWith("new.")) {
			this.new_form[path.slice(4)] = el.value;
			return;
		}
		// The Calculator drawer's fields: never the model.
		if (path.startsWith(CALC.PREFIX)) return this.calc_input(path, el, committed);
		const kind = el.getAttribute("data-fd-kind");
		// A new address in quick mode, where the looked-up site values are never inputs: they belong to the
		// old address, so they are blanked and the next derive fills them for the new one.
		if (path === "site.address" && committed && this.model && this.model.mode !== "full") {
			const address = M.coerce("text", el.value);
			if (address !== this.lookup_address) {
				this.lookup_address = address;
				this.model = M.clear_site_lookups(M.edit(this.model, path, address));
				return this.changed(false);
			}
		}
		if (el.type === "checkbox") return committed && this.edit(path, el.checked);
		if (el.tagName === "SELECT") {
			if (!committed) return;
			const value = el.getAttribute("data-fd-cast") === "number" ? M.coerce("number", el.value) : el.value || null;
			if (/^features\.\d+\.family$/.test(path)) {
				// Another family reads other inputs and other looks: the old ones are dropped, not sent.
				const base = path.replace(/\.family$/, "");
				let next = M.edit(this.model, path, value);
				next = M.set_in(M.set_in(M.set_in(next, base + ".inputs", {}), base + ".effect", {}), base + ".type", "");
				this.model = next;
				return this.changed(true);
			}
			// A family change redraws the feature's card (its inputs are the family's).
			return this.edit(path, value, /\.family$/.test(path) || path === "pump_room.location" || /\.shape$/.test(path) || /\.control$/.test(path));
		}
		if (kind === "number" || kind === "int") {
			const text = el.value.trim();
			const value = M.coerce(kind, text);
			if (text && value === null) {
				el.setAttribute("aria-invalid", "true");
				return;
			}
			el.removeAttribute("aria-invalid");
			if (value !== M.get_in(this.model, path)) this.edit(path, value);
			return;
		}
		let value = M.coerce(kind, el.value);
		if (value === "" && el.hasAttribute("data-fd-null")) value = null;
		if ((value || "") !== (M.get_in(this.model, path) || "")) this.edit(path, value);
	}

	on_submit(e) {
		if (e.target.closest && e.target.closest("[data-fd-calc-form]")) {
			e.preventDefault();
			return this.run_calc();
		}
		const form = e.target.closest && e.target.closest("[data-fd-ack-form]");
		if (!form) return;
		e.preventDefault();
		const note = form.querySelector("textarea").value.trim();
		if (!note) return form.querySelector("textarea").focus();
		this.acknowledge(form.getAttribute("data-fd-ack-form"), note);
	}

	on_key(e) {
		const el = e.target;
		// The step rail: up and down (or left and right on the phone's top bar) move between steps.
		if (el.classList && el.classList.contains("fd-rail-step")) {
			const all = Array.from(this.root.querySelectorAll(".fd-rail-step:not([disabled])"));
			const at = all.indexOf(el);
			const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
			if (step) {
				e.preventDefault();
				const next = all[(at + step + all.length) % all.length];
				if (next) next.focus();
			} else if (e.key === "Home" || e.key === "End") {
				e.preventDefault();
				all[e.key === "Home" ? 0 : all.length - 1].focus();
			}
			return;
		}
		// Choice tiles: a radio group, so the arrows move and choose.
		if (el.hasAttribute && el.hasAttribute("data-fd-tile")) {
			const group = Array.from(el.closest("[role=radiogroup]").querySelectorAll("[data-fd-tile]"));
			const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
			if (!step) return;
			e.preventDefault();
			const next = group[(group.indexOf(el) + step + group.length) % group.length];
			this.choose_tile(next.getAttribute("data-fd-tile"), next.getAttribute("data-fd-value"));
			const again = this.root.querySelector(`[data-fd-tile="${css_escape(next.getAttribute("data-fd-tile"))}"][data-fd-value="${css_escape(next.getAttribute("data-fd-value"))}"]`);
			if (again) again.focus();
			return;
		}
		if (el.hasAttribute && el.hasAttribute("data-fd-catalog")) return this.picker_key(e, el);
	}

	picker_key(e, input) {
		const list = this.doc.getElementById(input.getAttribute("aria-controls"));
		if (!list || list.hidden) {
			if (e.key === "ArrowDown") {
				e.preventDefault();
				this.run_search(input);
			}
			return;
		}
		const options = Array.from(list.querySelectorAll("[data-fd-item]"));
		const current = options.findIndex((o) => o.getAttribute("aria-selected") === "true");
		if (e.key === "Escape") {
			e.preventDefault();
			return this.close_picker(input);
		}
		if (e.key === "Enter" && current >= 0) {
			e.preventDefault();
			return this.pick(input, options[current].getAttribute("data-fd-item"));
		}
		const step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
		if (!step || !options.length) return;
		e.preventDefault();
		const next = (current + step + options.length) % options.length;
		options.forEach((o, i) => o.setAttribute("aria-selected", String(i === next)));
		input.setAttribute("aria-activedescendant", options[next].id);
		options[next].scrollIntoView({ block: "nearest" });
	}

	choose_tile(path, value) {
		if (path.startsWith("new.")) {
			this.new_form[path.slice(4)] = value;
			return this.draw_stage();
		}
		if (path === "mode") {
			if (!this.model || !this.can_write() || this.model.mode === value) return;
			const go = () => {
				this.model = M.set_mode(this.model, value);
				this.place.step = R.normalize_step(this.model.mode, this.place.step);
				R.write_address(this.win, this.place, false);
				this.changed(true);
			};
			// Quick hands every section back to the engine: what a designer sized by hand would be
			// overwritten by the next derive and saved, so they are asked first.
			const pinned = value === "quick" ? M.pinned_sections(this.model) : [];
			if (!pinned.length) return go();
			const { __ } = this.h;
			const names = pinned.map((key) => __((M.SECTIONS.find((s) => s.key === key) || { label: key }).label)).join(", ");
			return this.ask(__("Quick mode works every section out again, so what you set by hand in {0} will be replaced. Switch to Quick?", [names]), go);
		}
		if (M.get_in(this.model, path) === value) return;
		this.edit(path, value, true);
	}

	set_policy(section, value) {
		if (!this.model || !this.can_write()) return;
		if (value.startsWith("preset:")) {
			const presets = ((this.boot.sections || []).find((s) => s.key === section) || {}).presets || [];
			const preset = presets.find((p) => "preset:" + p.key === value);
			this.model = M.apply_preset(this.model, section, preset);
		} else {
			this.model = M.set_policy(this.model, section, value);
		}
		this.changed(true);
	}

	remove(list_key, id) {
		if (!this.model || !this.can_write()) return;
		this.model = M.remove_row(this.model, list_key, id);
		this.changed(true);
	}

	remedy(key, n) {
		const issue = ((this.reply && this.reply.issues) || []).find((i) => i.key === key);
		const fix = issue && issue.remedies && issue.remedies[n];
		if (!fix || !fix.set || !this.can_write()) return;
		let model = this.model;
		for (const [path, value] of Object.entries(fix.set)) model = M.edit(model, path, value);
		this.model = model;
		this.changed(true);
		this.sf.toast(this.h.__("Applied: {0}", [fix.label]), { tone: "info" });
	}

	toggle_adv(btn) {
		const id = btn.getAttribute("data-fd-adv");
		const body = this.doc.getElementById(btn.getAttribute("aria-controls"));
		const open = !this.adv_open.has(id);
		if (open) this.adv_open.add(id);
		else this.adv_open.delete(id);
		btn.setAttribute("aria-expanded", String(open));
		btn.classList.toggle("fd-adv-toggle--on", open);
		if (body) body.hidden = !open;
		try {
			this.win.localStorage.setItem(ADV_KEY, JSON.stringify(Array.from(this.adv_open)));
		} catch (err) {
			// Not remembered; the reveal still works.
		}
	}

	load_adv() {
		try {
			const ids = JSON.parse(this.win.localStorage.getItem(ADV_KEY) || "[]");
			return new Set(Array.isArray(ids) ? ids : []);
		} catch (e) {
			return new Set();
		}
	}

	// Project, Opportunity and Customer as the Desk's own Link fields when the Desk is there; the plain
	// inputs drawn by the step stay otherwise.
	mount_links() {
		const fr = this.env.frappe;
		if (!fr || !fr.ui || !fr.ui.form || typeof fr.ui.form.make_control !== "function") return;
		for (const slot of this.stage.querySelectorAll("[data-fd-link]")) {
			const fieldname = slot.getAttribute("data-fd-link");
			const doctype = slot.getAttribute("data-fd-doctype");
			try {
				slot.innerHTML = "";
				const control = fr.ui.form.make_control({
					df: {
						fieldtype: "Link",
						options: doctype,
						fieldname,
						placeholder: this.h.__(doctype),
						read_only: this.can_write() ? 0 : 1,
						onchange: () => this.set_field(fieldname, control.get_value() || ""),
					},
					parent: slot,
					render_input: true,
					only_input: true,
				});
				control.set_value(this.meta[fieldname] || "");
			} catch (err) {
				slot.innerHTML = `<input type="text" class="fd-input" data-fd-field="${this.h.esc(fieldname)}" value="${this.h.esc(this.meta[fieldname] || "")}">`;
			}
		}
	}
}

function slug(doctype) {
	return String(doctype).toLowerCase().replace(/ /g, "-");
}

function css_escape(value) {
	return String(value).replace(/["\\]/g, "\\$&");
}
