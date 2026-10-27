// Guided Entry (form kit P4-B, ERPNext TASK-2026-02742): a record filled in one step at a time,
// each step saved onto the real record as it goes. The server engine is form_kit/ (P4-A; its
// README is the payload reference); this page draws it. Generic over form_kit.config.CONFIGS,
// so it serves whatever DocType has a config there (Opportunity today).
//
// It is where those records open, new and existing (P5.2, public/js/guided_default.js): the
// form is the fallback, reached with "Open form", which this tab then remembers for the record.
// A new record started somewhere else (the Customer form's Create > Opportunity) arrives with
// route options, which become step 1's starting values (form_kit/opening.js split_prefill).
//
// Screens, each its own history entry so the phone's Back and Forward walk the steps (Nik's rule,
// 2026-09-24: never break Back/Forward):
//
//   /desk/guided-entry                              nothing chosen: how to start
//   /desk/guided-entry/<DocType>/new                step 1 of a record that does not exist yet
//   /desk/guided-entry/<DocType>/<name>/<step>      one step of a record
//
// The page moves only with frappe.set_route (public/js/form_kit/route.js builds every route), and
// frappe owns history: Back and Forward re-render the route, which fires on_page_show ->
// route_changed -> handle_route. There is no popstate listener here.
//
// NEW -> CREATED. Step 1 inserts the record (form_kit.api.create_record). The "new" entry must
// not survive that, or Back would land on an empty step 1 and Next would insert a second record.
// So the page replaces it in place (frappe.route_flags.replace_route: the router's push_state
// uses history.replaceState) with the record's own first step, then pushes the next step as an
// ordinary entry. History reads [... , <name>/<step 1>, <name>/<step 2>]: Back from step 2 goes
// to step 1 of the saved record, and Back again leaves the page the way the person came in. Both
// router calls run push_state synchronously, so route_changed coalesces the two on_page_show
// calls into one handle_route that reads the final route. If the person has already left the
// "new" entry when the insert returns (Back while it was in flight), there is nothing to replace:
// the page remembers the record (this.orphan) and says so, and a later Forward onto that "new"
// entry opens the created record instead of a blank one.
//
// SAVING. Values save onto the record, never a draft store: on leaving a step (Next, Back, the
// rail) when anything changed, and 2.5 s after the last change while typing. All of it goes
// through one save queue (public/js/form_kit/save_queue.js; the offline queue, P4.7, wraps it):
// one request in flight, changes coalesce by field, every request carries the `modified` of the
// last good load or save. A conflict ({conflict: true}, returned without saving) is settled
// three ways (conflict.js): a field only this person changed is kept, a field both changed is
// shown side by side with "Keep mine" / "Use theirs", then saved again on the new `modified`.
// A validation error (417) is shown at the top of the step and keeps the person on it; a lapsed
// session shows a sign-in panel and keeps everything typed in memory.
//
// EXTENSIONS (form kit P8.1). A step whose config names an `extension` gets a block above its
// fields, drawn by the DocType's extension (public/js/form_kit/extensions.js; the Item flow's
// name builder and its supplier, stock and asset blocks are public/js/guided_item/). The record
// editor (form_kit/ui/editor.js) mounts it and lends it a ctx; this page only picks the extension
// when a record opens (open_extension) and drops it on reset. A block that throws is caught, so
// the step's own fields always show. "More" steps (config `more`) are the rest of the form past a
// flow's core: Next on the last core step offers Done, with More details beside the step and every
// step still in the rail.
//
// NO SIGNAL (P4.7). What has not reached the server is kept on the phone (localStorage, one
// entry per record: form_kit/journal.js), with the server's value from before each change. A save
// that fails for want of signal says "Saved on this phone" rather than "Not saved", and is retried
// when the signal returns (the window's "online" event, and every 20 s while waiting). Opening the
// record again puts kept changes back: still the server's old value, saved; already there,
// dropped; changed by someone else, asked, with the same chooser as any conflict. A record not
// created yet is kept the same way (step 1 as typed, and the create's id); a create that cannot be
// sent waits for the signal, and the id makes the server answer a resend with the record the first
// attempt made, if it arrived after all. Closing the tab warns only when the phone could not keep
// something (storage refused).
//
// THE RECORD EDITOR (P4.4, v1.675.0). Drawing a step's fields, the save queue, conflicts, the phone's
// journal, step 1's create and Set as Lost are public/js/form_kit/ui/editor.js, which the side panel
// (form_kit/ui/side_panel.js, over the Opportunity board) runs too: one renderer, not two. This file
// keeps what only the page has (its routes, the band, rail, summary and footer, the start screen) and
// the editor's host hooks (after_create, show_created, still_new). The editor's methods are mixed onto
// GuidedEntry when the bundle arrives; see the constructor.
//
// VIEW SETTINGS (v1.684.0). A saved record is laid out the way each person chooses, per DocType:
// One page (the default: every step a section on one scrolling page, under its tab, the tab bar a
// sticky jump menu with scroll-spy), Tabs (one tab at a time; a tab is a route, so Back walks the
// tabs) or Step by step (the setup flow's own page); where the steps sit; the summary; notes and
// comments; the record tools; density, hints, text size, contrast, focus mode; pinned sections and
// where a record opens; fields and sections put away; summary fields; named presets; shortcuts. The
// decisions are public/js/form_kit/view.js (pure, run under node); the choices live in the person's
// own __UserSettings row (api/planner_views_prefs.py get_guided_view / save_guided_view), never in
// the browser and never anyone else's. A layout is only a presentation: every one draws the same
// steps through the same editor (One page and Tabs draw one FieldGroup per step, held together by
// form_kit/field_groups.js), so the fields, controls, saving, conflicts and the phone's journal are
// the same everywhere. A new record's setup flow, and the rest of the walk after its step 1 creates
// it, is always Step by step (view.js SETUP_VIEW), whatever the person chose.
//
// Every request this page makes goes through sf_kit.call(..., { quiet: true }): frappe.call's own
// dialogs (403, 5xx, QueryTimeout) would interrupt a page that already says it once, in its
// own panel. The one framework loader it uses is frappe.model.with_doctype, for the child-table
// metas a Table MultiSelect needs (frappe.get_meta), and only when they are not loaded already.

frappe.pages["guided-entry"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Guided Entry"),
		single_column: true,
	});
	wrapper.guided_entry = new GuidedEntry(page, wrapper);
	// Leaving for another Desk page (the sidebar, a link) saves what was typed, as a step change does.
	$(wrapper).on("hide", () => {
		document.body.classList.remove(GE_BODY_CLASS);
		wrapper.guided_entry.on_hide();
	});
};

frappe.pages["guided-entry"].on_page_show = function (wrapper) {
	// While this page shows, the Triton assistant button floats above the sticky footer instead of
	// on its Next button (guided_entry.bundle.css). Cleared on hide, so other pages keep it as is.
	document.body.classList.add(GE_BODY_CLASS);
	if (wrapper.guided_entry) wrapper.guided_entry.route_changed();
};

const GE_BODY_CLASS = "ge-page-active";
const GE_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "guided_entry.bundle.js", "guided_entry.bundle.css"];
const GE_API = "erpnext_enhancements.form_kit.api.";
// The person's view settings: the planners' durable per-user store (v1.684.0).
const GE_PREFS = "erpnext_enhancements.api.planner_views_prefs.";
// How far below the top of the window a section counts as the one being read (under the Desk's
// navbar and this page's sticky tab bar); also the scroll margin a jump leaves above a section.
const GE_SPY_LINE = 150;
const GE_GEAR_ICON =
	'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true" focusable="false">' +
	'<circle cx="8" cy="8" r="2.4" stroke="currentColor" stroke-width="1.4"/>' +
	'<path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4" ' +
	'stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>';
const GE_PIN_ICON =
	'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true" focusable="false">' +
	'<path d="M10 1.8 14.2 6l-1.5.4-2.7 2.7.4 3-1.2 1.2-6.3-6.3 1.2-1.2 3 .4 2.7-2.7z" stroke="currentColor" ' +
	'stroke-width="1.4" stroke-linejoin="round"/><path d="M5.2 10.8 1.8 14.2" stroke="currentColor" stroke-width="1.4" ' +
	'stroke-linecap="round"/></svg>';

function ge_clone(value) {
	return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function ge_has(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

// One string for what a new record was started with, "" for nothing.
function ge_start_key(options) {
	if (!options || typeof options !== "object") return "";
	const keys = Object.keys(options).filter((key) => options[key] !== null && options[key] !== undefined && options[key] !== "");
	if (!keys.length) return "";
	return JSON.stringify(keys.sort().map((key) => [key, options[key]]));
}

// Table rows as route options carry them: an array, or the JSON v16's set_route wrote for one.
function ge_rows(value) {
	if (Array.isArray(value)) return value;
	try {
		const rows = JSON.parse(value);
		return Array.isArray(rows) ? rows : null;
	} catch (e) {
		return null;
	}
}

class GuidedEntry {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="ge-app"></div>').appendTo(page.main);
		// Queues of records the page has moved away from while they still held something.
		this.retired = [];
		this.reset();
		this.orphan = null;
		this.load_seq = 0;
		this._route_timer = null;
		// P4.4: the record editor (values, fields, the save queue, conflicts, the phone's journal) is
		// public/js/form_kit/ui/editor.js, shared with the side panel. This class is defined before
		// that bundle arrives, so the bundle's methods are mixed onto it once it has (mix_editor; a
		// method this class defines is never replaced). Nothing below calls one before then.
		this.ready = new Promise((resolve) => frappe.require(GE_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit;
			this.kit = window.sf_form_kit;
			if (this.kit && this.kit.mix_editor) this.kit.mix_editor(GuidedEntry.prototype);
			// v1.684.0: the person's view settings, read once per page and kept in memory after.
			this.view_loaded = this.load_view_store();
		});
		this.view_store = null;
		this.view_store_ok = false;
		this.setup_for = null;
		this.entry_timer = null;
		this.entry_parts = null;
		this.last_timer = null;
		this.block_open = { notes: false, tools: false };
		// Scroll-spy (One page and Tabs): the section being read, once per frame at most.
		this.spy_frame = null;
		window.addEventListener("scroll", () => this.on_scroll(), { passive: true });
		// The page's shortcuts (view.js SHORTCUTS), only while it shows. Capture, so "?" opens this
		// page's list rather than the Desk's (which the list links to).
		document.addEventListener("keydown", (event) => this.on_key(event), true);
		// Closing or reloading the tab: the section in view goes as a beacon, so a reload opens there.
		window.addEventListener("pagehide", () => {
			if (!this.kit) return;
			this.flush_entry();
			this.send_position(true);
		});

		// Closing or reloading the tab with anything unsaved asks first. A record's queued changes
		// count wherever the person is on the Desk (they are only in this page's memory); an
		// uncreated step 1 counts only while this page is showing.
		window.addEventListener("beforeunload", (event) => {
			if (!this.has_unsaved() || this.kept_on_phone()) return;
			event.preventDefault();
			event.returnValue = "";
			return "";
		});
		// The server's value from before each unsent change, per record (journal.js `base`).
		this.bases = {};
		// Kept values waiting on the person's choice after a restore, per record (write_journal).
		this.held = {};
		// Keys the phone refused to write: closing the tab warns while any remain (kept_on_phone).
		this.failed_keys = new Set();
		this.signal_timer = null;
		this.ready.then(() => {
			this.sweep_journal();
			// P4.7: the signal is back; send what waited for it.
			window.addEventListener("online", () => this.signal_back());
			// Back from signing in on another tab: try the save that the lapsed session refused.
			window.addEventListener("focus", () => {
				if (this.queue && this.queue.state === "expired") this.queue.retry().catch(() => {});
			});
		});
		this.$root.on("keydown", (event) => {
			if (event.key !== "Escape") return;
			const $open = this.$root.find(".ge-help-btn[aria-expanded='true']");
			if ($open.length) {
				event.preventDefault();
				$open.each((i, button) => this.toggle_help($(button), false));
				$open.last().trigger("focus");
			}
		});
		// P4.5. The Closed Won prompt (create_project_prompt.js) changes the record on the server:
		// "No" un-wins it, "Yes" makes the project. It says so with this event, as it reloads a form.
		$(document).on("sf-record-changed", (event, doctype, name) => {
			if (this.showing() && !this.is_new && this.doctype === doctype && this.name === name) this.reload_record();
		});
		// The project made in the background reports here, as it does to the form (opportunity.js).
		frappe.realtime.on("project_creation_status", (data) => this.on_project_status(data || {}));
		this.nudged = new Set();
	}

	showing() {
		return (frappe.get_route() || [])[0] === (this.kit ? this.kit.PAGE : "guided-entry");
	}

	reset() {
		// P8.1: the step blocks' extension goes with the record (editor.js; on the first reset, from
		// the constructor, the editor's methods are not mixed in yet and there is none to leave).
		if (this.ext && this.leave_extension) this.leave_extension();
		this.ext = null;
		// v1.711.0: the landing and loading screens take no dropped file (record_tools.js bind_drop).
		if (this.record_tools && typeof this.record_tools.unbind_drop === "function") this.record_tools.unbind_drop();
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
		this.screen_key = "";
		this.fg = null;
		this.suppress = new Set();
		this.queue = null;
		this.creating = false;
		this.new_dirty = false;
		this.defs = {};
		this.conflict = null;
		this.notice = null;
		this.$shell = null;
		// What a new record was started with: fields beyond step 1 sent with the create, and the
		// options themselves, so a second start with different values is told from Back.
		this.carry = {};
		this.started_with = "";
		// v1.694.0: every start value, the ones no field takes too (a buying line's supplier and
		// rate), for the DocType's extension to put where its blocks have a place (ctx.started).
		this.start_options = {};
		this.client_id = null;
		// v1.684.0: how this record is laid out, and what the person did to it on this visit.
		this.entry = null;
		this.view = null;
		this.sections = null;
		this.collapsed = new Set();
		this.reveal_hidden = false;
		this.revealed = null;
		this.filled_snapshot = null;
		this.viewbar_key = "";
		this.away = { person: 0, filled: 0 };
	}

	// ------------------------------------------------------------------ routing

	route_changed() {
		// One handle_route per task: after step 1 creates the record, the replace and the push
		// each fire on_page_show, and only the final route matters.
		if (this._route_timer) return;
		this._route_timer = setTimeout(() => {
			this._route_timer = null;
			this.ready.then(() => this.handle_route());
		}, 0);
	}

	handle_route() {
		const target = this.kit.parse_route(frappe.get_route());
		if (!target) return;
		// A panel or a pop-up is not a screen: moving to another screen closes it (an address corrected
		// in place, which go() has already noted as the screen showing, does not).
		if (this.kit.route_key(target) !== this.screen_key) {
			this.close_view_settings(false);
			this.close_overlay();
		}
		// A new record's route options are what it was started with (frappe.new_doc's options,
		// through guided_default.js). Nothing else here reads them, and left set, v16's set_route
		// would write them into the address of every step this page pushes.
		const options = target.is_new && frappe.route_options ? ge_clone(frappe.route_options) : null;
		if (frappe.route_options) frappe.route_options = null;
		if (!target.doctype) {
			this.leave_record();
			this.show_landing();
			return;
		}
		if (target.is_new) {
			this.open_new(target.doctype, options);
			return;
		}
		if (this.flow && !this.is_new && this.doctype === target.doctype && this.name === target.name) {
			this.save_in_background();
			this.show_step(target.step);
			return;
		}
		this.leave_record();
		this.load(target.doctype, target.name, target.step);
	}

	open_new(doctype, options) {
		const started_with = ge_start_key(options);
		const orphan_start = this.orphan ? this.orphan.started_with || "" : "";
		if (this.orphan && this.orphan.doctype === doctype && (!started_with || started_with === orphan_start)) {
			// Forward onto a "new" entry whose record was created after the person left it.
			const orphan = this.orphan;
			this.orphan = null;
			this.reset();
			this.doctype = doctype;
			this.name = orphan.name;
			this.go(null, true);
			return;
		}
		if (this.flow && this.is_new && this.doctype === doctype && (!started_with || started_with === this.started_with)) {
			// Back to step 1 of the record being started: what was typed is still here.
			this.show_step(null);
			return;
		}
		this.leave_record();
		this.load(doctype, null, null, options);
	}

	// Put a step of this record in the address: a new history entry, or (replace) in place of the
	// current one. Each pushed entry is tagged in history.state with the screen it was pushed from,
	// so the footer's Back can step back through history instead of stacking a new entry.
	go(step_key, replace) {
		if ((frappe.get_route() || [])[0] !== this.kit.PAGE) return;
		const route = this.kit.build_route(this.doctype, this.name, step_key);
		const target = this.kit.parse_route(route);
		const next_key = this.kit.route_key(target);
		const behind = this.screen_key;
		const kept = ((window.history.state || {}).ge || {}).back;
		const before = window.location.href;
		if (frappe.route_options) frappe.route_options = null;
		const toggle = ((window.history.state || {}).sf_toggle || "") || null;
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(route);
		// Read only by push_state, which has already run; left set, frappe clears it only after
		// 100 ms and every pending request, and the next step would be a replace too.
		delete frappe.route_flags.replace_route;
		this.screen_key = next_key;
		if (window.location.href === before) return;
		// A replace writes a null history.state; the form/guided switch tag of the entry stays.
		if (replace && toggle) {
			try {
				window.history.replaceState(Object.assign({}, window.history.state, { sf_toggle: toggle }), "");
			} catch (e) {
				// Untagged, the form's Guided button pushes rather than steps back.
			}
		}
		const back = replace ? kept : behind;
		if (!back) return;
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { ge: { back } }), "");
		} catch (e) {
			// Untagged, the footer's Back pushes rather than steps back.
		}
	}

	// Save, then move. A save the server refused keeps the person on the step, with the reason.
	navigate(step_key) {
		if (this.is_new) return;
		this.save_before_moving()
			.then(() => this.go(step_key))
			.catch(() => {});
	}

	// The step Back and Next move to: the neighboring step, or in Tabs the neighboring tab's first
	// step (One page has no Back, and Next is Done).
	neighbor_screen(dir) {
		if (!this.sections_mode()) return this.kit.neighbor(this.flow.steps, this.values, this.step_key, dir);
		if (this.layout() !== "tabs" || !this.sections) return null;
		const tab = this.kit.neighbor_tab(this.view_model().tabs, this.sections.tab, dir);
		return tab ? this.kit.tab_step(tab) : null;
	}

	back() {
		const prev = this.is_new ? null : this.neighbor_screen(-1);
		if (!prev || this.is_new) return;
		this.save_before_moving()
			.then(() => {
				const tag = ((window.history.state || {}).ge || {}).back;
				const prev_key = this.kit.route_key({ doctype: this.doctype, name: this.name, step: prev });
				if (tag && tag === prev_key) window.history.back();
				else this.go(prev);
			})
			.catch(() => {});
	}

	next() {
		if (this.is_new) {
			this.create();
			return;
		}
		const next = this.neighbor_screen(1);
		// The last core step's Next is Done; the "more" steps are behind More details (P8.1).
		if (next && !this.ends_core()) {
			this.navigate(next);
			return;
		}
		// Done: the record opens here by default now, so Done goes back to the list.
		this.save_before_moving()
			.then(() => frappe.set_route("List", this.doctype))
			.catch(() => {});
	}

	on_hide() {
		if (!this.kit) return; // left before the bundle arrived: nothing typed yet
		this.toggle_all_help(false);
		this.close_view_settings(false);
		this.close_overlay();
		this.flush_entry();
		this.send_position(false);
		this.save_in_background();
	}

	ends_core() {
		// Done after the core steps is Step by step's (P8.1); One page and Tabs show every section.
		if (this.sections) return false;
		return !this.is_new && !!this.kit.ends_core && this.kit.ends_core(this.flow.steps, this.values, this.step_key);
	}

	has_unsaved() {
		if (this.queue && this.queue.has_unsaved()) return true;
		if (this.retired.some((queue) => queue.has_unsaved())) return true;
		if (this.is_new && (this.new_dirty || this.creating)) {
			return (frappe.get_route() || [])[0] === (this.kit ? this.kit.PAGE : "guided-entry");
		}
		return false;
	}

	// Leaving this record for another (or for the landing screen): send what it still holds.
	leave_record() {
		if (this.queue) {
			this.collect_step();
			this.queue.flush().catch(() => {});
		}
		// The next visit to this record decides the won-without-project reminder afresh.
		if (this.name) this.nudged.delete(this.name);
	}

	// ------------------------------------------------------------------ loading

	load(doctype, name, step_key, options) {
		const seq = ++this.load_seq;
		this.reset();
		this.doctype = doctype;
		this.name = name || null;
		this.is_new = !name;
		// A tab still holding sf_form_kit v2 (before P8.1) mixed in no open_extension: no blocks, as before.
		if (typeof this.open_extension === "function") this.open_extension(doctype);
		this.page.set_title(__("Guided Entry"));
		this.sf.state.render(this.$root, "loading");
		// The walk that created a record stays step by step until the person leaves that record.
		if (!this.setup_for || this.setup_for.doctype !== doctype || this.setup_for.name !== name) this.setup_for = null;
		if (!this.view_store_ok && this.view_store) this.view_loaded = this.load_view_store();
		this.ensure_meta(doctype)
			.then(() =>
				Promise.all([
					this.sf.call(GE_API + "get_flow", { doctype, name: name || null }, { quiet: true, method: "GET" }),
					this.view_loaded,
				])
			)
			.then(([flow]) => {
				if (seq !== this.load_seq) return;
				this.take_flow(flow);
				this.take_view();
				if (this.is_new) this.apply_start(options);
				if (this.is_new) this.restore_new();
				const kept = this.is_new ? null : this.restore_kept();
				const resend = this.pending_resend;
				if (resend && !this.is_new && resend.doctype === this.doctype && resend.name === this.name) {
					this.pending_resend = null;
					this.resend_typed(resend.values);
				}
				this.render_shell();
				this.show_step(step_key);
				this.refresh_notices();
				if (kept) this.after_restore(kept);
				this.nudge_won_without_project();
				this.maybe_intro();
			})
			.catch((err) => {
				if (seq !== this.load_seq) return;
				this.show_load_error(err || {}, () => this.load(doctype, name, step_key, options));
			});
	}

	// A new record started somewhere else: step 1 takes its fields, the other fields the server
	// will take (flow.prefill_fields) go with the create, and a real field it cannot take is
	// named, with the full form offered instead. A table value must arrive as rows.
	apply_start(options) {
		// What equals the new record's own default is not something it was started with (the
		// Kanban's add card passes its whole draft; the list passes its filters).
		const defaults = this.flow.values || {};
		const given = {};
		Object.keys(options || {}).forEach((key) => {
			if (!this.kit.same_value(options[key], defaults[key])) given[key] = options[key];
		});
		this.started_with = ge_start_key(given);
		this.start_options = ge_clone(given);
		// Once read, the values live here; a long one (a Lead's details) would make the address too
		// long to reload. Back and Forward to this entry find the record being started in memory.
		if (window.location.search) {
			try {
				window.history.replaceState(window.history.state, "", window.location.pathname + window.location.hash);
			} catch (e) {
				// The address keeps its query; nothing is lost.
			}
		}
		if (!this.started_with) return;
		const step = (this.flow.steps || []).find((s) => s.create) || (this.flow.steps || [])[0] || {};
		const split = this.kit.split_prefill(given, this.flow.prefill_fields || [], this.kit.editable_fieldnames(step));
		const as_value = (fieldname, value) => {
			const def = this.defs[fieldname] || {};
			if (def.fieldtype === "Table" || def.fieldtype === "Table MultiSelect") return ge_rows(value);
			return value;
		};
		Object.keys(split.values).forEach((fieldname) => {
			const value = as_value(fieldname, split.values[fieldname]);
			if (value !== null) this.values[fieldname] = value;
		});
		this.carry = {};
		Object.keys(split.carry).forEach((fieldname) => {
			const value = as_value(fieldname, split.carry[fieldname]);
			if (value !== null) this.carry[fieldname] = value;
		});
		const known = split.dropped.filter((fieldname) => frappe.meta && frappe.meta.has_field(this.doctype, fieldname));
		if (known.length) {
			this.notice = {
				kind: "carry",
				fields: known.map((fieldname) => __(frappe.meta.get_label(this.doctype, fieldname) || fieldname)),
			};
		}
	}

	// The full form for a record not created yet, starting from what is here.
	open_new_in_form() {
		this.collect_step();
		const doctype = this.doctype;
		const values = Object.assign({}, this.carry, this.values);
		// The full form holds it from here; the phone's copy would come back on the next New.
		this.forget_new();
		this.reset();
		if (window.sf_guided && window.sf_guided.new_in_form) window.sf_guided.new_in_form(doctype, values);
		else frappe.new_doc(doctype);
	}

	// The full form for this record, which this tab then opens it in (guided_default.js).
	open_in_form(tab) {
		if (window.sf_guided && window.sf_guided.to_form) {
			window.sf_guided.to_form(this.doctype, this.name, tab);
			return;
		}
		if (tab) frappe.route_hash = `#${tab}`;
		frappe.set_route("Form", this.doctype, this.name);
	}

	// ------------------------------------------------------------------ what the form's scripts did (P4.5)

	// The Desk form offers to make the project when it opens a won deal that has none
	// (crm_enhancements/opportunity.js); so does this page, once per visit, and only for a deal
	// that was already won when the visit began (the form's _ee_loaded_status rule). A deal won
	// during the visit has the server's own prompt, and a reload after its "Yes" comes while the
	// project is still being made: asking again there could queue a second project.
	nudge_won_without_project() {
		const crm = window.erpnext_enhancements && erpnext_enhancements.crm;
		const v = this.values || {};
		if (this.is_new || this.doctype !== "Opportunity" || !crm || !crm.confirm_won_next_step) return;
		if (this.nudged.has(this.name)) return;
		this.nudged.add(this.name);
		if (v.status !== "Closed Won" || v.custom_created_project || v.custom_is_historical) return;
		// v1.749.0: the check this page makes for its own edits. Creating the project needs write
		// on the Opportunity, so a read-only visitor is not asked to start what will be refused.
		if (!(this.flow && this.flow.can_write)) return;
		crm.confirm_won_next_step(this.name, { mode: "reopen" });
	}

	on_project_status(data) {
		if (this.doctype !== "Opportunity" || !this.showing() || data.opportunity_name !== this.name) return;
		// v1.747.0: while the progress dialog (create_project_prompt.js) watches this creation, it says
		// how it went and reloads this page itself, through sf-record-changed.
		const crm = window.erpnext_enhancements && erpnext_enhancements.crm;
		if (crm && crm.is_tracking_project_creation && crm.is_tracking_project_creation(data.opportunity_name)) return;
		// An open form of the same record says this already (opportunity.js binds it there).
		const view = frappe.views && frappe.views.formview && frappe.views.formview.Opportunity;
		const form_says = !!(view && view.frm && view.frm.doc && view.frm.doc.name === data.opportunity_name);
		if (!form_says) {
			if (data.status === "blocked") {
				frappe.msgprint({
					title: __("Hand-Off Required"),
					indicator: "orange",
					message: frappe.utils.escape_html(data.blocked_reason || ""),
				});
			} else if (data.status === "success" && data.project_doc) {
				const link = frappe.utils.get_form_link("Project", data.project_doc.name, true);
				const project = data.project_doc.name;
				const made = __("Project {0} was created.", [project]);
				// v1.747.0: the next step the dialog offers too, only where it would work (build_out_offer).
				const offer = crm && crm.build_out_offer ? crm.build_out_offer() : Promise.resolve(false);
				offer.then((yes) =>
					this.sf.toast(
						made,
						yes
							? {
									tone: "success",
									timeout: 15000,
									action_label: __("Build out from a planner template"),
									on_action: () => crm.open_planner_build_out(project),
								}
							: { tone: "success", timeout: 8000 }
					)
				);
				if (data.drive_error) {
					frappe.msgprint({
						title: __("Google Drive Integration Failed"),
						indicator: "orange",
						message: `${__("Project {0} was created, but its Drive folder was not.", [link])}<pre>${frappe.utils.escape_html(String(data.drive_error))}</pre>`,
					});
				}
			} else if (data.status === "exists" && data.project) {
				// v1.747.0: it already had one (or one already pointed at it, and is now linked).
				this.sf.toast(__("This opportunity has project {0}.", [data.project]), { tone: "success", timeout: 8000 });
			} else {
				const failed = __("Project creation failed. The Error Log has the details.");
				this.sf.toast(data.reason ? `${failed} ${data.reason}` : failed, { tone: "warning", timeout: 8000 });
			}
		}
		this.reload_record();
	}

	// The start screen: for each guided DocType, a way to start a new record and the person's own
	// open ones (form_kit.api.my_records), each opening here.
	show_landing() {
		this.reset();
		this.clear_view_attrs();
		this.page.set_title(__("Guided Entry"));
		const guided = (frappe.boot && frappe.boot.sf_guided) || {};
		// A flow that is not where its records open (Item, P8.1) is listed only for people who can
		// start one: a salesperson who can read Items has no Item flow to pick up.
		const doctypes = Object.keys(guided).filter(
			(doctype) =>
				// v2 of the kit has no opens_by_default: every flow listed, as before P8.1.
				!this.kit.opens_by_default ||
				this.kit.opens_by_default(guided[doctype]) ||
				(frappe.model.can_create && frappe.model.can_create(doctype))
		);
		if (!doctypes.length) doctypes.push("Opportunity");
		this.$root.html(`
			<div class="ge-landing">
				<header class="ge-landing-head">
					<h1 class="ge-landing-title"></h1>
					<p class="ge-landing-intro"></p>
				</header>
				<div class="ge-landing-lists"></div>
			</div>`);
		this.$root.find(".ge-landing-title").text(__("Guided Entry"));
		this.$root
			.find(".ge-landing-intro")
			.text(__("Records open here one step at a time. Pick up one of yours, or start a new one."));
		const $lists = this.$root.find(".ge-landing-lists");
		doctypes.forEach((doctype) => this.render_mine(doctype, $('<section class="sf-card ge-mine"></section>').appendTo($lists)));
	}

	render_mine(doctype, $card) {
		const can_create = frappe.model && frappe.model.can_create && frappe.model.can_create(doctype);
		$card.html(`
			<div class="ge-mine-head">
				<h2 class="sf-card-title ge-mine-title"></h2>
				<button type="button" class="sf-btn sf-btn--primary ge-mine-new"></button>
			</div>
			<div class="ge-mine-body"></div>
			<button type="button" class="sf-btn sf-btn--quiet ge-mine-all"></button>`);
		$card.find(".ge-mine-title").text(__(doctype));
		const $new = $card.find(".ge-mine-new");
		if (can_create) {
			// frappe.new_doc reaches this page only for a DocType that opens here by default; one
			// reached on purpose (Item, P8.1) would open its form, so its New starts here directly.
			const by_default = window.sf_guided && window.sf_guided.is_guided && window.sf_guided.is_guided(doctype, true);
			$new.text(__("New {0}", [__(doctype)])).on("click", () =>
				by_default ? frappe.new_doc(doctype) : frappe.set_route(this.kit.build_route(doctype, null, null))
			);
		} else {
			$new.remove();
		}
		$card
			.find(".ge-mine-all")
			.text(__("All in the {0} list", [__(doctype)]))
			.on("click", () => frappe.set_route("List", doctype));
		const $body = $card.find(".ge-mine-body");
		const load = () => {
			this.sf.state.render($body, "loading");
			this.sf
				.call(GE_API + "my_records", { doctype }, { quiet: true, method: "GET" })
				.then((mine) => this.render_mine_rows(doctype, $card, $body, mine || {}))
				.catch((err) => this.sf.state.render($body, "error", { body: (err || {}).message, on_action: load }));
		};
		load();
	}

	render_mine_rows(doctype, $card, $body, mine) {
		const rows = mine.rows || [];
		if (mine.label) $card.find(".ge-mine-title").text(mine.label);
		if (!rows.length) {
			this.sf.state.render($body, "empty", { title: __("Nothing open is yours right now.") });
			return;
		}
		const $list = $('<ul class="ge-mine-list"></ul>');
		rows.forEach((row) => {
			const route = this.kit.build_route(doctype, row.name, null);
			const href = "/desk/" + route.map((part) => encodeURIComponent(part)).join("/");
			const $link = $('<a class="ge-mine-row"></a>').attr("href", href);
			$('<span class="ge-mine-name"></span>').text(row.title || row.name).appendTo($link);
			const details = [row.name].concat(row.details || []).filter(Boolean);
			$('<span class="ge-mine-details"></span>').text(details.join(" · ")).appendTo($link);
			$link.on("click", (event) => {
				if (event.ctrlKey || event.metaKey || event.shiftKey || event.button === 1) return;
				event.preventDefault();
				frappe.set_route(route);
			});
			$("<li></li>").append($link).appendTo($list);
		});
		$body.empty().append($list);
		if (rows.length >= (mine.limit || 0)) {
			$('<p class="ge-mine-more"></p>')
				.text(__("Showing the {0} changed most recently.", [rows.length]))
				.appendTo($body);
		}
	}

	show_load_error(err, retry) {
		if (err.session_expired) {
			this.sf.state.render(this.$root, "error", {
				title: __("You are signed out."),
				body: __("Sign in again, then come back to this tab and try again."),
				action_label: __("Sign in"),
				on_action: () => this.open_login(),
			});
			const $again = $(`<button type="button" class="sf-btn sf-btn--quiet ge-again"></button>`).text(__("Try again"));
			$again.on("click", retry);
			this.$root.find(".sf-state").append($again);
			return;
		}
		if (err.status === 403) {
			this.sf.state.render(this.$root, "no-access", { body: err.message });
			return;
		}
		this.sf.state.render(this.$root, "error", {
			title: __("This could not be opened."),
			body: err.message,
			on_action: retry,
		});
	}

	// ------------------------------------------------------------------ the shell

	render_shell() {
		this.page.set_title(this.is_new ? __("New {0}", [__(this.doctype)]) : this.name);
		// Drawn again below: whatever panel or pop-up was open goes with the old shell.
		this.files_focus = null;
		this.$panel = null;
		this.$overlay = null;
		this.sections = null;
		this.$root.html(`
			<div class="ge-band">
				<div class="ge-band-inner">
					<div class="ge-band-text">
						<div class="ge-eyebrow"></div>
						<h1 class="ge-title"></h1>
						<div class="ge-chips">
							<span class="ge-stage-chip" aria-live="polite"></span>
							<button type="button" class="ge-files-chip" aria-controls="ge-files-region" hidden></button>
						</div>
					</div>
					<div class="ge-band-actions"></div>
				</div>
				<div class="ge-progress">
					<div class="ge-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100">
						<div class="ge-progress-bar"></div>
					</div>
					<div class="ge-progress-text"></div>
				</div>
			</div>
			<div class="ge-stripe"></div>
			<div class="ge-layout">
				<nav class="ge-rail" aria-label="${frappe.utils.escape_html(__("Steps"))}"><ol></ol></nav>
				<main class="ge-main">
					<div class="ge-notices" aria-live="polite"></div>
					<div class="ge-conflict-slot"></div>
					<div class="ge-viewbar" hidden></div>
					<section class="ge-stage"></section>
					<section class="ge-files" id="ge-files-region" hidden></section>
					<div class="ge-block ge-block--notes">
						<button type="button" class="ge-block-bar" aria-expanded="false" aria-controls="ge-comments-region" hidden></button>
						<section class="ge-comments" hidden></section>
					</div>
				</main>
				<div class="ge-aside">
					<aside class="ge-summary" aria-label="${frappe.utils.escape_html(__("Summary"))}"></aside>
					<div class="ge-block ge-block--tools">
						<button type="button" class="ge-block-bar" aria-expanded="false" aria-controls="ge-tools-region" hidden></button>
						<section class="ge-tools" hidden></section>
					</div>
				</div>
			</div>
			<div class="ge-footer">
				<button type="button" class="sf-btn sf-btn--secondary ge-back"></button>
				<button type="button" class="ge-saveline" aria-live="polite"></button>
				<button type="button" class="sf-btn sf-btn--primary ge-next"></button>
			</div>`);
		this.$shell = this.$root;
		this.$root.find(".ge-back").text(__("Back")).on("click", () => this.back());
		this.$root.find(".ge-next").on("click", () => this.next());
		this.$root.find(".ge-saveline").on("click", () => {
			if (this.queue && ["error", "expired"].includes(this.queue.state)) {
				this.collect_step();
				this.queue.retry().catch(() => {});
			}
		});
		this.$root.find(".ge-rail").on("click", "button[data-step]", (event) => {
			const key = $(event.currentTarget).attr("data-step");
			if (key && key !== this.step_key) this.navigate(key);
		});
		// One page and Tabs: the tab bar (v1.684.0).
		this.$root.find(".ge-rail").on("click", "button[data-tab]", (event) => {
			this.open_tab($(event.currentTarget).attr("data-tab"));
		});
		this.$root.find(".ge-summary").on("click", "button[data-step]", (event) => {
			const key = $(event.currentTarget).attr("data-step");
			const fieldname = $(event.currentTarget).attr("data-field");
			if (key === this.step_key || this.is_drawn(key)) this.focus_field(fieldname);
			else if (key) {
				this.pending_focus = fieldname;
				this.navigate(key);
			}
		});
		// On a phone the summary folds away behind its title (v1.684.0).
		this.$root.find(".ge-summary").on("click", ".ge-sum-toggle", () => {
			this.summary_open = !this.summary_open;
			this.render_summary_open();
		});
		this.$root.find(".ge-block-bar").on("click", (event) => {
			const kind = $(event.currentTarget).closest(".ge-block").hasClass("ge-block--tools") ? "tools" : "notes";
			this.block_open[kind] = !this.block_open[kind];
			this.refresh_blocks();
		});
		// The attachments chip in the band: the files section, opened and scrolled to (v1.711.0).
		// With none yet it opens the uploader instead (record_tools.js chip_action).
		this.$root.find(".ge-files-chip").on("click", () => {
			if (this.record_tools && typeof this.record_tools.chip_action === "function") this.record_tools.chip_action();
			else this.show_files();
		});
		this.$root.find(".ge-comments").attr("id", "ge-comments-region");
		this.$root.find(".ge-tools").attr("id", "ge-tools-region");
		this.watch_blocks();
		this.render_view_controls();
		this.apply_view();
		$(`<button type="button" class="sf-btn sf-btn--quiet ge-open-form"></button>`)
			.text(__("Open form"))
			.on("click", () => {
				if (this.is_new) {
					this.open_new_in_form();
					return;
				}
				this.save_before_moving()
					.then(() => this.open_in_form())
					.catch(() => {});
			})
			.appendTo(this.$root.find(".ge-band-actions"));
		// Under the steps on every step, once the record exists (editor.js mount_comments). The shell
		// is drawn again when a new record is created, which mounts them then.
		if (typeof this.mount_comments === "function") this.mount_comments();
	}

	// The band's stage chip (v1.711.0): the field the DocType's config names under `header` (on
	// Opportunity its `status`, the board's own stage), with the board's words and a tone, redrawn as
	// the value changes: chosen on the Close step, set by a save, or put back by a conflict choice.
	// Drawn only when its content changes, because the span is a live region.
	render_stage_chip() {
		const $chip = this.$root.find(".ge-stage-chip");
		// A tab that loaded an older kit bundle before the deploy (the board's side panel does) has no
		// header_status: no chip, not a TypeError out of refresh_chrome.
		const status = typeof this.kit.header_status === "function" ? this.kit.header_status(this.flow && this.flow.header, this.values) : null;
		const key = status ? `${status.tone}|${status.label}|${status.value}` : "";
		if ($chip.attr("data-key") === key) return;
		$chip.attr("data-key", key).removeAttr("title").empty();
		if (!status) return;
		$chip.append($('<span class="sr-only"></span>').text(`${status.label}: `));
		$chip.append(this.sf.chip(__(status.value), status.tone));
		$chip.attr("title", status.aria);
	}

	// Whether the files section starts open: it does, except in focus mode (record_tools.js).
	files_open_default() {
		return !(this.view && this.view.focus === "on");
	}

	// The attachments chip's click: open the files section and bring it into view, focus on its
	// first control (a phone has no other way to reach it without scrolling the whole page).
	show_files() {
		const $files = this.$root.find(".ge-files");
		if (!$files.length || $files.prop("hidden")) return;
		$files.find("details").prop("open", true);
		this.scroll_into($files[0], $files.find("summary, button, a").first()[0]);
	}

	// Band, rail, progress, summary and footer: everything that follows the values.
	refresh_chrome() {
		if (!this.$shell || !this.flow) return;
		const kit = this.kit;
		const steps = this.flow.steps || [];
		const model = kit.summary_model(this.doctype, this.name, this.values, this.completeness);
		const pos = kit.position(steps, this.values, this.step_key);

		this.$root
			.find(".ge-eyebrow")
			.text(this.is_new ? __("New {0}", [__(this.doctype)]) : `${__(this.doctype)} · ${this.name}`);
		this.$root.find(".ge-title").text(model.title || (this.is_new ? __("New {0}", [__(this.doctype)]) : this.name));
		this.$root
			.find(".ge-stripe")
			.attr("class", `ge-stripe ge-stripe--${kit.lead_pillar(this.doctype, this.values)}`);
		this.render_stage_chip();

		const $track = this.$root.find(".ge-progress-track");
		$track.attr("aria-valuenow", model.percent).attr("aria-label", __("Complete for this stage"));
		this.$root.find(".ge-progress-bar").css("width", `${model.percent}%`);
		this.$root
			.find(".ge-progress-text")
			.text(
				model.total
					? __("{0} of {1} filled for {2}", [model.done, model.total, __(model.stage || "")])
					: ""
			);

		// What shows follows the record too (a new completeness, a status, the steps that apply).
		this.apply_visibility(false);
		if (this.sections) {
			this.render_nav();
		} else {
			this.render_rail(steps, pos);
		}
		this.render_summary(model, steps);
		this.render_footer(steps, pos);
		this.render_saveline();
	}

	render_rail(steps, pos) {
		const kit = this.kit;
		const missing = kit.missing_by_step(steps, (this.completeness || {}).missing);
		const active_index = {};
		kit.active_steps(steps, this.values).forEach((step, index) => (active_index[step.key] = index));
		const current = active_index[this.step_key];
		const $ol = this.$root.find(".ge-rail ol").empty();
		this.$root.find(".ge-rail").attr("aria-label", __("Steps"));
		const pinned = new Set(this.entry && !this.is_new ? this.entry.pinned : []);
		steps.forEach((step) => {
			const active = ge_has(active_index, step.key);
			const reachable = active && !this.is_new;
			const index = active_index[step.key];
			const left = (missing[step.key] || []).length;
			const is_current = step.key === this.step_key;
			const done = active && !is_current && index < current && !left;
			const $li = $('<li class="ge-rail-item"></li>')
				.toggleClass("is-current", is_current)
				.toggleClass("is-off", !active)
				.toggleClass("is-done", done);
			const $button = $('<button type="button" class="ge-rail-step"></button>')
				.attr("data-step", reachable ? step.key : null)
				.prop("disabled", !reachable && !is_current)
				.attr("aria-current", is_current ? "step" : null);
			$('<span class="ge-rail-mark" aria-hidden="true"></span>')
				.text(done ? "✓" : active ? String(index + 1) : "–")
				.appendTo($button);
			const $text = $('<span class="ge-rail-text"></span>').appendTo($button);
			const $title = $('<span class="ge-rail-title"></span>').text(step.title).appendTo($text);
			if (pinned.has(step.key)) {
				$('<span class="ge-pin-mark"></span>').html(GE_PIN_ICON).attr("title", __("Pinned")).appendTo($title);
				$('<span class="sr-only"></span>').text(` (${__("pinned")})`).appendTo($title);
			}
			let meta = "";
			if (!active) meta = __("Not for this record");
			else if (left) meta = left === 1 ? __("1 to fill") : __("{0} to fill", [left]);
			else if (done) meta = __("Done");
			if (meta) $('<span class="ge-rail-meta"></span>').text(meta).appendTo($text);
			$li.append($button).appendTo($ol);
		});
		// On a phone the rail is a strip: keep the current step in view.
		const el = $ol.find(".is-current")[0];
		if (el && el.scrollIntoView && window.matchMedia && window.matchMedia("(max-width: 1023px)").matches) {
			try {
				el.scrollIntoView({ block: "nearest", inline: "center" });
			} catch (e) {
				// older engines: no options object
			}
		}
		if (pos.total) {
			this.$root.find(".ge-rail").attr("data-position", __("Step {0} of {1}", [pos.number, pos.total]));
		}
	}

	render_summary(model, steps) {
		const sf = this.sf;
		const esc = sf.esc;
		const $box = this.$root.find(".ge-summary").empty();
		const rows = [];
		const row = (label, html) =>
			rows.push(`<div class="ge-sum-row"><dt>${esc(label)}</dt><dd>${html || "<span class='ge-muted'>–</span>"}</dd></div>`);
		const config = this.kit.SUMMARY[this.doctype];
		const label_of = (fieldname, fallback) => __((this.defs[fieldname] || {}).label || fallback);
		// v1.684.0: the fields this person chose for the summary, in their order, instead of the
		// DocType's own rows. Read-only text, each value through the same formatting as the rest.
		const chosen = !this.is_new && this.entry && Array.isArray(this.entry.summary_fields) ? this.entry.summary_fields : null;
		if (chosen) {
			chosen.forEach((fieldname) => {
				const def = this.defs[fieldname];
				if (!def) return;
				row(label_of(fieldname, fieldname), this.summary_value(def, this.values[fieldname], config));
			});
		} else if (config) {
			// The party is an Account at Sapphire (Customer is relabeled "Account" on the Desk).
			row(__("Account"), esc(model.account));
			row(label_of(config.title, "Name"), esc(model.title === model.name ? "" : model.title));
			row(
				label_of(config.streams.field, "Value streams"),
				model.streams.map((s) => sf.chip(__(s.stream), s.pillar)).join(" ")
			);
			const amount =
				model.amount === null
					? ""
					: format_currency(model.amount, this.values.currency || frappe.boot.sysdefaults.currency);
			row(label_of(config.amount, "Amount"), esc(amount));
			row(label_of(config.status, "Status"), model.status ? sf.chip(__(model.status), "neutral") : "");
		}
		const where = this.kit.field_steps(steps);
		const missing = model.missing
			.map((item) => {
				const key = where[item.fieldname];
				const label = esc(item.label);
				return key
					? `<li><button type="button" class="ge-sum-missing" data-step="${esc(key)}" data-field="${esc(item.fieldname)}">${label}</button></li>`
					: `<li>${label}</li>`;
			})
			.join("");
		const head = model.total
			? __("{0}: {1} of {2}", [__(model.stage || ""), model.done, model.total])
			: __("Checklist");
		const checklist = model.total
			? missing
				? `<ul class="ge-checklist">${missing}</ul>`
				: `<p class="ge-muted">${esc(__("Everything this stage asks for is filled in."))}</p>`
			: "";
		// The toggle shows only on a narrow screen (guided_entry.bundle.css), where the summary sits
		// above the form, folded; on a desktop the card is always open.
		const brief = model.total ? __("{0} of {1} filled", [model.done, model.total]) : "";
		$box.html(
			`<section class="sf-card ge-sum-card"><h2 class="sf-card-title ge-sum-title">${esc(__("Summary"))}</h2>` +
				`<button type="button" class="ge-sum-toggle" aria-controls="ge-sum-body"><span class="ge-sum-toggle-text">${esc(
					__("Summary")
				)}</span><span class="ge-sum-toggle-meta">${esc(brief)}</span></button>` +
				`<div class="ge-sum-body" id="ge-sum-body">` +
				(rows.length ? `<dl class="ge-sum-list">${rows.join("")}</dl>` : "") +
				`<h3 class="ge-sum-head">${esc(head)}</h3>${checklist}</div></section>`
		);
		this.render_summary_open();
	}

	render_summary_open() {
		const open = !!this.summary_open;
		this.$root.find(".ge-summary").toggleClass("is-open", open);
		this.$root.find(".ge-sum-toggle").attr("aria-expanded", open ? "true" : "false");
	}

	// One chosen field's value in the summary, as HTML: the streams and the status as chips, an
	// amount in the record's currency, anything else as the conflict chooser writes it. Escaped.
	summary_value(def, value, config) {
		const sf = this.sf;
		const esc = sf.esc;
		if (!this.kit.has_value(def, value)) return "";
		if (config && config.streams && def.fieldname === config.streams.field) {
			return this.kit
				.streams_of(this.doctype, this.values)
				.map((s) => sf.chip(__(s.stream), s.pillar))
				.join(" ");
		}
		if (config && def.fieldname === config.status) return sf.chip(__(String(value)), "neutral");
		if (def.fieldtype === "Currency") {
			return esc(format_currency(value, this.values.currency || frappe.boot.sysdefaults.currency));
		}
		if (def.fieldtype === "Check") return esc(__("Yes"));
		if (def.fieldtype === "Date" && frappe.datetime) return esc(frappe.datetime.str_to_user(value));
		if (def.fieldtype === "Datetime" && frappe.datetime) return esc(frappe.datetime.str_to_user(value));
		return esc(this.kit.value_text(value));
	}

	render_footer(steps, pos) {
		const kit = this.kit;
		const prev = this.is_new ? null : this.neighbor_screen(-1);
		const next = this.is_new || this.ends_core() ? null : this.neighbor_screen(1);
		this.$root.find(".ge-back").toggleClass("is-hidden", !prev).prop("disabled", !prev);
		let label;
		if (this.is_new) label = this.creating ? __("Creating…") : __("Create and continue");
		else if (next) label = __("Next");
		else label = __("Done");
		let title = next ? (kit.find_step(steps, next) || {}).title || "" : "";
		if (next && this.sections) title = (kit.tab_of(this.view_model().tabs, next) || {}).label || title;
		this.$root
			.find(".ge-next")
			.text(label)
			.prop("disabled", !!this.creating)
			.attr("aria-label", next ? __("Next: {0}", [title]) : label);
	}

	// ------------------------------------------------------------------ a step

	show_step(step_key) {
		if (!this.flow) return;
		const steps = this.flow.steps || [];
		let key = step_key;
		if (this.is_new) {
			key = (steps.find((step) => step.create) || steps[0] || {}).key;
		} else {
			// v1.684.0: an address that names no step opens where the person chose (view.js
			// start_step: where they left this record, by default), corrected in place.
			const start = step_key ? null : this.start_key();
			const resolved = this.kit.resolve_step(steps, this.values, step_key || start);
			key = resolved.key;
			if (this.sections_mode() && this.layout() === "page") {
				// One page is one screen: the record's own address, whatever section is in view.
				if (step_key && key) this.go(null, true);
			} else if ((resolved.replace || (start && !step_key)) && key) {
				this.go(key, true);
			}
		}
		if (!this.is_new && this.sections_mode() && key) {
			const page = this.layout() === "page";
			this.screen_key = this.kit.route_key({ doctype: this.doctype, name: this.name, step: page ? "" : key, is_new: false });
			this.show_section(key);
			return;
		}
		this.screen_key = this.kit.route_key({
			doctype: this.doctype,
			name: this.name,
			step: this.is_new ? "" : key,
			is_new: this.is_new,
		});
		if (!key) {
			this.$root.find(".ge-stage").html(this.sf.state.html("empty", { title: __("There is nothing to fill in here.") }));
			return;
		}
		if (key === this.step_key && this.fg && !this.sections) {
			this.refresh_chrome();
			return;
		}
		this.render_step(key);
	}

	render_step(key) {
		const step = this.kit.find_step(this.flow.steps, key);
		const moved = this.step_key !== null;
		this.step_key = key;
		this.fg = null;
		this.sections = null;
		this.toggle_all_help(false);
		const pos = this.kit.position(this.flow.steps, this.values, key);
		const editable = !!this.flow.can_write && !step.read_only;
		const $stage = this.$root.find(".ge-stage").empty().removeClass("ge-stage--sections");
		const $head = $('<header class="ge-step-head"></header>').appendTo($stage);
		$('<div class="ge-step-eyebrow"></div>')
			.text(
				this.is_new
					? __("Step 1: creates the {0}", [__(this.doctype)])
					: pos.total
						? __("Step {0} of {1}", [pos.number, pos.total])
						: step.tab || ""
			)
			.appendTo($head);
		$('<h2 class="ge-step-title" tabindex="-1"></h2>').text(step.title).appendTo($head);
		if (step.intro) $('<p class="ge-step-intro"></p>').text(step.intro).appendTo($head);
		if (!this.flow.can_write) {
			$('<p class="ge-readonly-note"></p>')
				.text(__("You can read this record but not change it."))
				.appendTo($stage);
		}
		const $fields = $('<div class="ge-fields"></div>').appendTo($stage);
		if (step.link && !this.is_new) {
			$(`<button type="button" class="sf-btn sf-btn--secondary ge-step-link"></button>`)
				.text(__(step.link.label))
				.on("click", () =>
					this.save_before_moving()
						.then(() => this.open_in_form(step.link.tab))
						.catch(() => {})
				)
				.appendTo($stage);
		}

		this.render_fields(step, $fields, editable);
		if (this.ends_core()) {
			// The rest of the form, past the flow's core: one tap on, never in the way of Done (P8.1).
			const next = this.kit.neighbor(this.flow.steps, this.values, key, 1);
			$('<button type="button" class="sf-btn sf-btn--secondary ge-more"></button>')
				.text(__("More details: {0}", [(this.kit.find_step(this.flow.steps, next) || {}).title || ""]))
				.on("click", () => {
					const now = this.kit.neighbor(this.flow.steps, this.values, this.step_key, 1);
					if (now) this.navigate(now);
				})
				.appendTo($stage);
		}
		this.apply_visibility();

		this.refresh_notices();
		this.refresh_chrome();
		this.note_position();
		if (moved) {
			const top = this.$root.offset().top - 60;
			if (window.scrollY > top) window.scrollTo({ top: Math.max(0, top) });
			$head.find(".ge-step-title").trigger("focus");
		}
		if (this.pending_focus) {
			const fieldname = this.pending_focus;
			this.pending_focus = null;
			setTimeout(() => this.focus_field(fieldname), 50);
		}
	}

	// ------------------------------------------------------------------ after a create (the editor's hooks)

	// Whether the "new" entry of `doctype` is still the one showing, when its create comes back.
	still_new(doctype) {
		const target = this.kit.parse_route(frappe.get_route());
		return !!target && target.is_new && target.doctype === doctype;
	}

	// Step 1 made the record (editor.js create): move to its real address.
	after_create({ doctype, created, started_with, still_here }) {
		if (!still_here) {
			// Left while it was being created: nothing to replace. Forward onto that "new"
			// entry opens this record (open_new) rather than a blank one.
			this.orphan = { doctype, name: created.name, started_with };
			this.sf.toast(__("{0} {1} was created.", [__(doctype), created.name]), {
				tone: "success",
				action_label: __("Open"),
				on_action: () => {
					this.orphan = null;
					frappe.set_route(this.kit.build_route(doctype, created.name, null));
				},
			});
			return;
		}
		const steps = this.flow.steps || [];
		const first = (steps.find((s) => s.create) || steps[0]).key;
		const next = this.kit.neighbor(steps, this.values, first, 1);
		// The rest of the walk stays step by step (view.js SETUP_VIEW) until the person leaves it.
		this.setup_for = { doctype, name: created.name };
		this.take_view();
		this.render_shell();
		this.fg = null;
		this.step_key = null;
		// Replace the "new" entry with the record's first step, then push the next one.
		this.screen_key = "";
		this.go(first, true);
		if (next) this.go(next);
		this.sf.toast(__("{0} created.", [created.name]), { tone: "success", timeout: 4000 });
	}

	// A start this phone kept turned out to have been created after all (editor.js check_created).
	show_created(doctype, name) {
		frappe.route_flags.replace_route = true;
		frappe.set_route(this.kit.build_route(doctype, name, null));
		delete frappe.route_flags.replace_route;
	}

	// ================================================================== view settings (v1.684.0)
	// The decisions are form_kit/view.js; what follows draws them. See the head of this file.

	load_view_store() {
		return this.sf
			.call(GE_PREFS + "get_guided_view", {}, { quiet: true, method: "GET" })
			.then((store) => {
				const s = store && typeof store === "object" ? store : {};
				this.view_store = { intro_seen: s.intro_seen === true, doctypes: s.doctypes || {} };
				this.view_store_ok = true;
			})
			.catch(() => {
				// The defaults for now, asked again on the next record. No first-time pop-up: it could not
				// be remembered, and it must never show twice.
				this.view_store = { intro_seen: true, doctypes: {} };
				this.view_store_ok = false;
			});
	}

	// A new record, or the walk its step 1 started: the setup flow's own page.
	in_setup() {
		if (this.is_new) return true;
		return !!this.setup_for && this.setup_for.doctype === this.doctype && this.setup_for.name === this.name;
	}

	// The person's entry for this DocType, and what this record shows.
	take_view() {
		this.entry = this.kit.entry_for(this.view_store || { doctypes: {} }, this.doctype);
		this.view = this.kit.effective_view(this.entry.prefs, this.in_setup(), this.steps_only());
		this.hints_hidden = this.kit.shell_attrs(this.view).hints === "hidden";
	}

	layout() {
		return (this.view && this.view.layout) || "steps";
	}

	// A DocType with step blocks (form kit P8.1: the Item flow) stays on Step by step (view.js
	// effective_view). A kit without has_extension (older than P8.1) has no blocks either.
	steps_only() {
		return !!(this.kit && this.kit.has_extension && this.doctype && this.kit.has_extension(this.doctype));
	}

	sections_mode() {
		return !this.is_new && !!this.flow && this.kit.SECTION_LAYOUTS.includes(this.layout());
	}

	// The tabs and the pinned group as the page draws them now.
	view_model() {
		const steps = (this.flow && this.flow.steps) || [];
		const pinned = this.entry && !this.in_setup() ? this.entry.pinned : [];
		return {
			tabs: this.kit.view_tabs(steps, this.values, this.kit.step_active, pinned),
			pinned: this.kit.pinned_group(steps, this.values, this.kit.step_active, pinned, __("Pinned")),
		};
	}

	is_drawn(step_key) {
		return !!(this.fg && this.fg.multi && this.fg.group_of(step_key));
	}

	section_el(step_key) {
		return this.$root.find(".ge-section").filter((i, el) => el.getAttribute("data-step") === step_key);
	}

	// Where a record opens when its address names no step (view.js start_step).
	start_key() {
		if (this.in_setup() || !this.entry || !this.flow) return null;
		return this.kit.start_step(this.entry, this.name, this.flow.steps, this.values, this.kit.step_active);
	}

	clear_view_attrs() {
		const el = this.$root && this.$root[0];
		if (!el) return;
		Array.from(el.attributes)
			.map((attr) => attr.name)
			.filter((name) => name.startsWith("data-ge-"))
			.forEach((name) => el.removeAttribute(name));
	}

	// The shell's attributes (guided_entry.bundle.css lays the page out from them) and where the
	// notes and the record tools sit.
	apply_view() {
		if (!this.$shell || !this.view) return;
		const attrs = this.kit.shell_attrs(this.view);
		Object.keys(attrs).forEach((key) => this.$root.attr(`data-ge-${key}`, attrs[key]));
		this.$root.attr("data-ge-setup", this.in_setup() ? "1" : "0");
		this.hints_hidden = attrs.hints === "hidden";
		const $main = this.$root.find(".ge-main");
		const $aside = this.$root.find(".ge-aside");
		const $notes = this.$root.find(".ge-block--notes");
		const $tools = this.$root.find(".ge-block--tools");
		// Side column: the summary, the record tools, then notes. Below the form: notes, then tools.
		if (attrs.notes === "side") $notes.appendTo($aside);
		else $notes.insertAfter(this.$root.find(".ge-stage"));
		if (attrs.tools === "side") $tools.insertAfter(this.$root.find(".ge-summary"));
		else $tools.appendTo($main);
		// The files stay straight under the steps, whatever the notes and tools do (v1.711.0), and
		// fold away in focus mode as the notes and tools do; the band's chip opens them. The tools list
		// them too, in a group the stylesheet shows only while the tools sit beside the form (v1.748.0).
		this.$root.find(".ge-files").insertAfter(this.$root.find(".ge-stage"));
		const focus = attrs.focus === "on";
		if (focus !== this.files_focus) {
			this.files_focus = focus;
			this.$root.find(".ge-files details").prop("open", !focus);
		}
		this.refresh_blocks();
		this.refresh_view_controls();
	}

	// Notes and comments, and the record tools: hidden while they have nothing to show; behind a bar
	// when the person chose "collapsed" (or focus mode), opened by it.
	refresh_blocks() {
		if (!this.$shell) return;
		const attrs = this.kit.shell_attrs(this.view || {});
		let side_blocks = 0;
		[
			["notes", ".ge-block--notes", ".ge-comments", __("Notes and comments")],
			["tools", ".ge-block--tools", ".ge-tools", __("Record tools")],
		].forEach(([kind, block, inner, label]) => {
			const $block = this.$root.find(block);
			const $inner = $block.find(inner);
			const has = $inner.length > 0 && !$inner.prop("hidden") && $inner.children().length > 0;
			const collapsible = attrs[kind] === "collapsed";
			const open = !collapsible || !!this.block_open[kind];
			$block.toggleClass("is-collapsible", collapsible).toggleClass("is-collapsed", collapsible && !open);
			$block.prop("hidden", !has);
			if (has && $block.parent().hasClass("ge-aside")) side_blocks += 1;
			const count = kind === "notes" ? this.comments_count() : 0;
			$block
				.children(".ge-block-bar")
				.prop("hidden", !collapsible || !has)
				.attr("aria-expanded", open ? "true" : "false")
				.text(count ? __("{0} ({1})", [label, count]) : label);
		});
		// The side column sticks only while it holds the summary alone (guided_entry.bundle.css).
		this.$root.attr("data-ge-aside-blocks", side_blocks ? "1" : "0");
	}

	// Whether the record tools have drawn anything into .ge-tools.
	tools_present() {
		const $tools = this.$root.find(".ge-tools");
		return $tools.length > 0 && !$tools.prop("hidden") && $tools.children().length > 0;
	}

	// How many notes and comments the record has, as drawn (the Notes list's total, and the Comments
	// App's own items).
	comments_count() {
		const comments = this.$root.find(".ge-comments .comment-item").length;
		return (Number(this.notes_total) || 0) + comments;
	}

	// The editor's notes list was drawn (editor.js render_notes).
	comments_changed() {
		this.refresh_blocks();
	}

	// The comments and the record tools fill in on their own time (another request, the Comments App,
	// whatever mounts into .ge-tools): their bars follow.
	watch_blocks() {
		if (this.block_observer) this.block_observer.disconnect();
		if (typeof MutationObserver === "undefined") return;
		let pending = null;
		this.block_observer = new MutationObserver(() => {
			if (pending) return;
			pending = setTimeout(() => {
				pending = null;
				this.refresh_blocks();
			}, 100);
		});
		this.$root.find(".ge-comments, .ge-tools").each((i, el) =>
			this.block_observer.observe(el, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] })
		);
	}

	// The band's View and Focus buttons (a saved record only).
	render_view_controls() {
		const $actions = this.$root.find(".ge-band-actions");
		if (this.is_new || !$actions.length) return;
		const $view = $('<button type="button" class="sf-btn sf-btn--quiet ge-view-btn" aria-haspopup="dialog" aria-expanded="false"></button>')
			.html(GE_GEAR_ICON)
			.attr("title", __("View settings (V)"))
			.on("click", () => this.toggle_view_settings());
		$('<span class="ge-view-btn-text"></span>').text(__("View")).appendTo($view);
		const $focus = $('<button type="button" class="sf-btn sf-btn--quiet ge-focus-btn" aria-pressed="false"></button>')
			.text(__("Focus"))
			.attr("title", __("Focus mode: only the form (F)"))
			.on("click", () => this.toggle_focus());
		$actions.prepend($focus).prepend($view);
	}

	refresh_view_controls() {
		const on = !!this.view && this.view.focus === "on";
		this.$root.find(".ge-focus-btn").attr("aria-pressed", on ? "true" : "false").toggleClass("is-on", on);
	}

	toggle_focus() {
		if (this.is_new || !this.entry) return;
		const on = this.entry.prefs.focus === "on";
		this.set_prefs({ focus: on ? "off" : "on" });
		this.sf.toast(on ? __("Focus mode is off.") : __("Focus mode: only the form. Press F to leave it."), {
			tone: "info",
			timeout: 3000,
		});
	}

	// ------------------------------------------------------------------ changing the view

	set_prefs(changes) {
		if (this.is_new || !this.entry) return;
		const before = this.kit.shell_attrs(this.view || {});
		this.entry.prefs = this.kit.clean_view(Object.assign({}, this.entry.prefs, changes || {}));
		// A choice the person makes ends the setup walk's own layout on this record.
		this.setup_for = null;
		this.store_entry();
		this.save_entry({ prefs: this.entry.prefs });
		this.view = this.kit.effective_view(this.entry.prefs, this.in_setup(), this.steps_only());
		const after = this.kit.shell_attrs(this.view);
		this.apply_view();
		if (before.layout !== after.layout || before.hints !== after.hints) this.redraw_stage();
		else {
			this.apply_visibility();
			this.refresh_chrome();
		}
		this.refresh_settings_state();
	}

	store_entry() {
		if (!this.view_store) this.view_store = { intro_seen: true, doctypes: {} };
		this.view_store.doctypes = this.view_store.doctypes || {};
		this.view_store.doctypes[this.doctype] = this.entry;
	}

	// Saved a moment after the last change; one request for a run of changes.
	save_entry(parts) {
		if (this.is_new || !this.doctype) return;
		if (this.entry_doctype && this.entry_doctype !== this.doctype) this.flush_entry();
		this.entry_doctype = this.doctype;
		this.entry_parts = Object.assign(this.entry_parts || {}, parts || {});
		clearTimeout(this.entry_timer);
		this.entry_timer = setTimeout(() => this.flush_entry(), 600);
	}

	flush_entry() {
		clearTimeout(this.entry_timer);
		this.entry_timer = null;
		const parts = this.entry_parts;
		const doctype = this.entry_doctype;
		this.entry_parts = null;
		if (!parts || !doctype || !this.sf) return;
		this.sf
			.call(GE_PREFS + "save_guided_view", { doctype, data: JSON.stringify(parts) }, { quiet: true })
			.catch(() => {
				if (this.entry_warned) return;
				this.entry_warned = true;
				this.sf.toast(__("Your view settings could not be saved. They apply here until you leave the page."), {
					tone: "warning",
					timeout: 8000,
				});
			});
	}

	// The step (or sections) drawn again in the layout now chosen, at the same place, with what is
	// typed kept (it is in this.values and the queue, never only in a control).
	redraw_stage() {
		const key = this.step_key;
		if (!this.flow || !key || this.is_new) return;
		this.collect_step();
		this.fg = null;
		this.sections = null;
		this.step_key = null;
		const target = this.kit.parse_route(frappe.get_route()) || {};
		if (this.sections_mode()) this.show_section(key, true);
		else this.render_step(key);
		// The address says what the screen is: a step (Step by step, Tabs) or the record (One page).
		const want = this.layout() === "page" ? "" : key;
		if ((target.step || "") !== want) this.go(want || null, true);
	}

	// ------------------------------------------------------------------ One page and Tabs

	show_section(key, quiet) {
		const kit = this.kit;
		const mode = this.layout();
		const model = this.view_model();
		const home = kit.tab_of(model.tabs, key);
		let tab_key = "*";
		if (mode === "tabs") {
			const shown = this.sections && this.sections.mode === "tabs" ? this.sections.tab : null;
			tab_key = home ? home.key : shown || (model.tabs.find((tab) => tab.active) || {}).key || "";
		}
		const fresh = !this.sections || this.sections.mode !== mode || this.sections.tab !== tab_key;
		const moved = this.step_key !== null && this.step_key !== key;
		this.step_key = key;
		if (fresh) this.render_sections(mode, tab_key);
		else this.refresh_chrome();
		const top = this.visible_sections()[0];
		if (key === top || (model.pinned && model.pinned.steps.some((s) => s.key === key) && mode === "tabs" && fresh)) {
			if (moved && !quiet) {
				const $group = this.$root.find(".ge-group").first();
				this.scroll_into(($group[0] || this.$root.find(".ge-stage")[0]), $group.find(".ge-group-title")[0]);
			}
		} else {
			this.scroll_to_section(key, moved && !quiet);
		}
		this.note_position();
		if (this.pending_focus) {
			const fieldname = this.pending_focus;
			this.pending_focus = null;
			setTimeout(() => this.focus_field(fieldname), 50);
		}
	}

	render_sections(mode, tab_key) {
		const kit = this.kit;
		const steps = this.flow.steps || [];
		const model = this.view_model();
		this.toggle_all_help(false);
		this.fg = null;
		this.begin_groups();
		const $stage = this.$root.find(".ge-stage").empty().addClass("ge-stage--sections");
		if (!this.flow.can_write) {
			$('<p class="ge-readonly-note"></p>').text(__("You can read this record but not change it.")).appendTo($stage);
		}
		const groups = [];
		if (model.pinned) groups.push(model.pinned);
		model.tabs.forEach((tab) => {
			if (mode === "page" || tab.key === tab_key) groups.push(tab);
		});
		groups.forEach((group) => {
			const id = `ge-group-${group.key}`;
			const $group = $('<section class="ge-group"></section>')
				.attr({ "data-tab": group.key, "aria-labelledby": id })
				.toggleClass("ge-group--pinned", !!group.pinned)
				.appendTo($stage);
			const $title = $('<h2 class="ge-group-title" tabindex="-1"></h2>').attr("id", id).appendTo($group);
			if (group.pinned) $('<span class="ge-pin-mark"></span>').html(GE_PIN_ICON).appendTo($title);
			$('<span class="ge-group-name"></span>').text(group.label).appendTo($title);
			group.steps.forEach((item) => {
				const step = kit.find_step(steps, item.key);
				if (step) this.render_section(step, $group);
			});
		});
		this.sections = { mode, tab: tab_key, keys: kit.section_keys(model.tabs, mode, tab_key, model.pinned) };
		this.apply_visibility();
		this.refresh_notices();
		this.refresh_chrome();
	}

	// One step as a section: its head (a disclosure button, how much is left, a pin), its intro, and
	// its fields drawn by the editor exactly as Step by step draws them.
	render_section(step, $group) {
		const editable = !!this.flow.can_write && !step.read_only;
		const id = `ge-sec-${step.key}`;
		const $sec = $('<section class="ge-section"></section>').attr({ id, "data-step": step.key }).appendTo($group);
		const $head = $('<div class="ge-section-head"></div>').appendTo($sec);
		const $heading = $('<h3 class="ge-section-heading"></h3>').appendTo($head);
		const $toggle = $('<button type="button" class="ge-section-toggle" aria-expanded="true"></button>')
			.attr("aria-controls", `${id}-body`)
			.on("click", () => this.toggle_section(step.key))
			.appendTo($heading);
		$('<span class="ge-section-chevron" aria-hidden="true"></span>').appendTo($toggle);
		$('<span class="ge-section-title"></span>').text(step.title).appendTo($toggle);
		$('<span class="ge-section-meta"></span>').appendTo($toggle);
		const pinned = (this.entry.pinned || []).includes(step.key);
		const pin_label = pinned ? __("Unpin {0}", [step.title]) : __("Pin {0} to the top", [step.title]);
		$('<button type="button" class="ge-pin"></button>')
			.html(GE_PIN_ICON)
			.attr({ "aria-pressed": pinned ? "true" : "false", "aria-label": pin_label, title: pin_label })
			.on("click", () => this.toggle_pin(step.key))
			.appendTo($head);
		const $body = $('<div class="ge-section-body"></div>').attr("id", `${id}-body`).appendTo($sec);
		if (step.intro) $('<p class="ge-step-intro"></p>').text(step.intro).appendTo($body);
		const $fields = $('<div class="ge-fields"></div>').appendTo($body);
		$('<p class="ge-muted ge-section-empty" hidden></p>').text(__("Nothing filled in here yet.")).appendTo($body);
		if (step.link) {
			$(`<button type="button" class="sf-btn sf-btn--secondary ge-step-link"></button>`)
				.text(__(step.link.label))
				.on("click", () =>
					this.save_before_moving()
						.then(() => this.open_in_form(step.link.tab))
						.catch(() => {})
				)
				.appendTo($body);
		}
		this.render_fields(step, $fields, editable);
		$sec.on("focusin", () => this.section_reached(step.key));
		if (this.collapsed.has(step.key)) this.set_collapsed($sec, true);
	}

	// The sections showing, in page order.
	visible_sections() {
		const out = [];
		this.$root.find(".ge-section").each((i, el) => {
			if (!el.hidden && !$(el).closest(".ge-group").prop("hidden")) out.push(el.getAttribute("data-step"));
		});
		return out;
	}

	toggle_section(step_key, force) {
		const $sec = this.section_el(step_key);
		if (!$sec.length) return;
		const collapse = force === undefined ? !this.collapsed.has(step_key) : !!force;
		if (collapse) this.collapsed.add(step_key);
		else this.collapsed.delete(step_key);
		this.set_collapsed($sec, collapse);
	}

	set_collapsed($sec, collapse) {
		$sec.toggleClass("is-collapsed", collapse);
		$sec.find("> .ge-section-head .ge-section-toggle").attr("aria-expanded", collapse ? "false" : "true");
		$sec.children(".ge-section-body").prop("hidden", collapse);
		if (!collapse) {
			const group = this.fg && this.fg.multi ? this.fg.group_of($sec.attr("data-step")) : null;
			if (group) group.refresh_dependency();
			this.fit_texts($sec);
		}
	}

	toggle_pin(step_key) {
		if (!this.entry) return;
		const on = !this.entry.pinned.includes(step_key);
		if (on && this.entry.pinned.length >= this.kit.MAX_PINNED) {
			this.sf.toast(__("You can pin {0} sections. Unpin one first.", [this.kit.MAX_PINNED]), { tone: "warning" });
			return;
		}
		this.entry.pinned = this.kit.toggle_key(this.entry.pinned, step_key, on, this.kit.MAX_PINNED);
		this.store_entry();
		this.save_entry({ pinned: this.entry.pinned });
		if (this.sections) {
			this.redraw_stage();
			this.scroll_to_section(step_key, false);
			this.section_el(step_key).find("> .ge-section-head .ge-pin").trigger("focus");
		} else {
			this.refresh_chrome();
		}
		this.refresh_settings_state();
	}

	// The person is in this section now (scrolled to it, or typing in it): the nav follows, and the
	// record remembers it.
	section_reached(step_key) {
		if (!step_key || step_key === this.step_key) return;
		this.step_key = step_key;
		this.mark_nav();
		this.note_position();
	}

	scroll_to_section(step_key, focus) {
		const $sec = this.section_el(step_key);
		if (!$sec.length) return;
		if ($sec.prop("hidden")) return;
		if (this.collapsed.has(step_key) && focus) this.toggle_section(step_key, false);
		this.scroll_into($sec[0], focus ? $sec.find(".ge-section-toggle")[0] : null);
		this.mark_nav();
	}

	scroll_into(el, focus_el) {
		if (!el) return;
		// The spy would otherwise pick the section above a short last one at once.
		this.spy_hold_until = Date.now() + 900;
		try {
			el.scrollIntoView({ block: "start" });
		} catch (e) {
			el.scrollIntoView();
		}
		if (focus_el) {
			try {
				focus_el.focus({ preventScroll: true });
			} catch (e) {
				focus_el.focus();
			}
		}
	}

	on_scroll() {
		if (!this.sections || this.spy_frame) return;
		const raf = window.requestAnimationFrame || ((fn) => setTimeout(fn, 16));
		this.spy_frame = raf(() => {
			this.spy_frame = null;
			this.spy();
		});
	}

	spy() {
		if (!this.sections || !this.$shell || !this.showing() || Date.now() < (this.spy_hold_until || 0)) return;
		const list = [];
		this.$root.find(".ge-section").each((i, el) => {
			if (el.hidden || $(el).closest(".ge-group").prop("hidden")) return;
			list.push({ key: el.getAttribute("data-step"), top: el.getBoundingClientRect().top });
		});
		const key = this.kit.spy_pick(list, GE_SPY_LINE);
		if (key) this.section_reached(key);
	}

	// The tab bar of One page and Tabs: the pinned group (when there is one) and every tab with an
	// active step, each with what it still lacks.
	render_nav() {
		const $nav = this.$root.find(".ge-rail").attr("aria-label", this.layout() === "tabs" ? __("Tabs") : __("Sections"));
		const $ol = $nav.find("ol").empty();
		const model = this.view_model();
		const missing = this.kit.missing_by_step(this.flow.steps, (this.completeness || {}).missing);
		const items = [];
		if (model.pinned && model.pinned.active) items.push(model.pinned);
		model.tabs.filter((tab) => tab.active).forEach((tab) => items.push(tab));
		items.forEach((tab) => {
			const $li = $('<li class="ge-rail-item ge-tab-item"></li>').attr("data-tab", tab.key);
			const $button = $('<button type="button" class="ge-rail-step ge-tab"></button>').attr("data-tab", tab.key);
			const $mark = $('<span class="ge-rail-mark" aria-hidden="true"></span>').appendTo($button);
			if (tab.pinned) $mark.addClass("ge-rail-mark--pin").html(GE_PIN_ICON);
			else $mark.text(String(items.filter((t) => !t.pinned).indexOf(tab) + 1));
			const $text = $('<span class="ge-rail-text"></span>').appendTo($button);
			$('<span class="ge-rail-title"></span>').text(tab.label).appendTo($text);
			const left = this.kit.tab_missing(tab, missing);
			if (left) $('<span class="ge-rail-meta"></span>').text(left === 1 ? __("1 to fill") : __("{0} to fill", [left])).appendTo($text);
			$li.append($button).appendTo($ol);
		});
		this.mark_nav(model);
	}

	// The current tab: the one shown (Tabs), or the one being read (One page).
	mark_nav(model) {
		if (!this.sections) return;
		const m = model || this.view_model();
		let current;
		if (this.sections.mode === "tabs") current = this.sections.tab;
		else if (m.pinned && m.pinned.steps.some((s) => s.key === this.step_key)) current = "pinned";
		else current = (this.kit.tab_of(m.tabs, this.step_key) || {}).key;
		const aria = this.sections.mode === "tabs" ? "page" : "location";
		const $items = this.$root.find(".ge-rail .ge-tab-item");
		$items.each((i, el) => {
			const on = el.getAttribute("data-tab") === current;
			$(el).toggleClass("is-current", on);
			$(el).find("button").attr("aria-current", on ? aria : null);
		});
		const el = $items.filter(".is-current")[0];
		const strip = this.$root.attr("data-ge-nav") === "top" || (window.matchMedia && window.matchMedia("(max-width: 1023px)").matches);
		if (el && strip && el.scrollIntoView) {
			const $ol = this.$root.find(".ge-rail ol");
			const box = $ol[0];
			if (box && (el.offsetLeft < box.scrollLeft || el.offsetLeft + el.offsetWidth > box.scrollLeft + box.clientWidth)) {
				box.scrollLeft = Math.max(0, el.offsetLeft - 16);
			}
		}
	}

	// A tab in the tab bar: Tabs shows it (a route, so Back comes back); One page jumps to it.
	open_tab(tab_key) {
		if (!tab_key || !this.sections) return;
		const model = this.view_model();
		if (tab_key === "pinned") {
			const $group = this.$root.find(".ge-group--pinned");
			this.scroll_into($group[0], $group.find(".ge-group-title")[0]);
			const first = model.pinned ? this.kit.tab_step(model.pinned) : null;
			if (first) this.section_reached(first);
			return;
		}
		const tab = model.tabs.find((t) => t.key === tab_key);
		const step = tab ? this.kit.tab_step(tab) : null;
		if (!step) return;
		if (this.sections.mode === "tabs" && this.sections.tab !== tab_key) {
			this.navigate(step);
			return;
		}
		const $group = this.$root.find(".ge-group").filter((i, el) => el.getAttribute("data-tab") === tab_key);
		this.scroll_into($group[0], $group.find(".ge-group-title")[0]);
		this.section_reached(step);
	}

	// Each drawn section: hidden while its step does not apply (or the person put it away), and how
	// much it still lacks for the stage.
	refresh_section_states() {
		if (!this.sections || !this.flow) return;
		const missing = this.kit.missing_by_step(this.flow.steps, (this.completeness || {}).missing);
		const opts = this.visibility_opts();
		this.$root.find(".ge-section").each((i, el) => {
			const key = el.getAttribute("data-step");
			const step = this.kit.find_step(this.flow.steps, key);
			if (!step) return;
			const active = this.kit.step_active(step, this.values);
			// A section with the cursor in it is never put away under the person.
			const away = this.kit.step_hidden(step, this.values, opts) && !el.contains(document.activeElement);
			const was_hidden = el.hidden;
			el.hidden = !active || away;
			const listed = (opts.hidden_steps || []).includes(key);
			$(el).toggleClass("ge-revealed", !!opts.reveal && listed && active);
			const left = (missing[key] || []).length;
			$(el)
				.find("> .ge-section-head .ge-section-meta")
				.text(left ? (left === 1 ? __("1 to fill") : __("{0} to fill", [left])) : "");
			if (was_hidden && !el.hidden) {
				const group = this.fg && this.fg.multi ? this.fg.group_of(key) : null;
				if (group) group.refresh_dependency();
				this.fit_texts($(el));
			}
		});
		this.$root.find(".ge-group").each((i, el) => {
			el.hidden = !$(el).find(".ge-section").filter((j, sec) => !sec.hidden).length;
		});
	}

	// ------------------------------------------------------------------ what shows

	visibility_opts() {
		const missing = new Set((((this.completeness || {}).missing) || []).map((item) => item && item.fieldname));
		const own = !this.in_setup() && !!this.entry;
		return {
			hidden_fields: own ? this.entry.hidden_fields : [],
			hidden_steps: own && this.sections ? this.entry.hidden_steps : [],
			missing,
			filled_only: own && !!this.view && this.view.fields === "filled",
			filled_snapshot: this.filled_snapshot || null,
			reveal: !!this.reveal_hidden,
			revealed: this.revealed || null,
		};
	}

	// Fields put away (by the person, or "only fields with a value"), as classes: the controls stay
	// drawn and keep their values; only what shows changes. Counted for the bar over the form.
	//
	// Asked again on every refresh_chrome (review of #1299), which follows each save's new
	// completeness and each status change: a field that becomes required or stage-needed comes back
	// at once, and the count stays true. `refresh_filled` takes a new snapshot of the empty fields
	// "only fields with a value" puts away (view.js field_hidden): when the section is drawn or the
	// person turns it on, never while they type.
	apply_visibility(refresh_filled = true) {
		if (!this.fg || !this.flow) return;
		if (refresh_filled || !this.filled_snapshot) {
			this.filled_snapshot = this.kit.filled_snapshot(this.shown_steps(), this.values);
		}
		const opts = this.visibility_opts();
		let person = 0;
		let filled = 0;
		this.shown_steps().forEach((step) => {
			const group = this.fg.multi ? this.fg.group_of(step.key) : this.fg;
			if (!group) return;
			let showing = 0;
			let $fields = null;
			(step.fields || []).forEach((field) => {
				if (!field || !field.fieldname || this.kit.is_layout(field)) return;
				const control = group.fields_dict[field.fieldname] || group.fields_dict[field.fieldname + "__ge_list"];
				if (!control) return;
				const $w = control.$wrapper || $(control.wrapper);
				if (!$fields) $fields = $w.closest(".ge-fields");
				let result = this.kit.field_hidden(field, this.values[field.fieldname], opts);
				// Never away from under the cursor: the field being typed in stays, and joins `revealed`.
				if (result.hidden && $w[0] && $w[0].contains(document.activeElement)) {
					this.on_field_edited(field.fieldname);
					result = { hidden: false, by: null };
				}
				const value = this.values[field.fieldname];
				const listed =
					opts.reveal && (opts.hidden_fields || []).includes(field.fieldname) && this.kit.can_hide(field, value, opts.missing);
				$w.toggleClass("ge-away", result.hidden).toggleClass("ge-revealed", !!listed);
				if (result.by === "person") person += 1;
				else if (result.by === "filled") filled += 1;
				if (!result.hidden) showing += 1;
			});
			if ($fields) {
				// A form section whose every field is put away goes too, heading and all.
				$fields.find(".form-section").each((i, sec) => {
					const $controls = $(sec).find(".frappe-control");
					$(sec).toggleClass("ge-away-section", $controls.length > 0 && !$controls.filter(":not(.ge-away)").length);
				});
				$fields.siblings(".ge-section-empty").prop("hidden", showing > 0);
			}
		});
		if (this.sections) {
			(this.flow.steps || []).forEach((step) => {
				if (this.is_drawn(step.key) && this.kit.step_hidden(step, this.values, opts)) person += 1;
			});
			this.refresh_section_states();
		}
		this.away = { person, filled };
		this.render_viewbar();
	}

	// Over the form: "Only fields with a value", and how many are put away with "Show hidden". Never
	// silent: whatever the person's view leaves out is counted here.
	render_viewbar() {
		const $bar = this.$root.find(".ge-viewbar");
		if (!$bar.length) return;
		if (this.in_setup() || !this.entry || !this.view) {
			this.viewbar_key = "";
			$bar.prop("hidden", true).empty();
			return;
		}
		const filled = this.view.fields === "filled";
		// Drawn again only when what it says changes: it is asked on every refresh, and redrawing it
		// would take the focus off its own buttons.
		const key = [filled, this.away.filled, this.away.person, this.reveal_hidden, (this.entry.hidden_fields || []).length].join("|");
		if (key === this.viewbar_key && !$bar.prop("hidden")) return;
		this.viewbar_key = key;
		$bar.empty().prop("hidden", false);
		$('<button type="button" class="sf-btn sf-btn--quiet ge-viewbar-btn ge-filled-btn"></button>')
			.attr("aria-pressed", filled ? "true" : "false")
			.text(__("Only fields with a value"))
			.on("click", () => this.set_prefs({ fields: filled ? "all" : "filled" }))
			.appendTo($bar);
		if (filled && this.away.filled) {
			$('<span class="ge-viewbar-note"></span>')
				.text(this.away.filled === 1 ? __("1 empty field not shown") : __("{0} empty fields not shown", [this.away.filled]))
				.appendTo($bar);
		}
		const listed = (this.entry.hidden_fields || []).length + (this.sections ? (this.entry.hidden_steps || []).length : 0);
		if (this.away.person || (this.reveal_hidden && listed)) {
			const count = this.away.person;
			$('<button type="button" class="sf-btn sf-btn--quiet ge-viewbar-btn ge-reveal-btn"></button>')
				.attr("aria-pressed", this.reveal_hidden ? "true" : "false")
				.text(
					this.reveal_hidden
						? __("Showing what you hid")
						: count === 1
							? __("1 hidden by you · Show hidden")
							: __("{0} hidden by you · Show hidden", [count])
				)
				.on("click", () => {
					this.reveal_hidden = !this.reveal_hidden;
					this.apply_visibility(false);
					this.$root.find(".ge-reveal-btn").trigger("focus");
				})
				.appendTo($bar);
		}
	}

	// focus_field's hook (editor.js): open a collapsed section, show a field that is put away.
	before_focus(fieldname) {
		if (!this.fg || !fieldname) return;
		const step = this.field_step(fieldname);
		if (this.sections && step) {
			const $sec = this.section_el(step.key);
			if ($sec.length && $sec.prop("hidden") && this.kit.step_active(step, this.values)) {
				this.reveal_hidden = true;
				this.apply_visibility(false);
			}
			if (this.collapsed.has(step.key)) this.toggle_section(step.key, false);
		}
		const control = this.fg.fields_dict[fieldname] || this.fg.fields_dict[fieldname + "__ge_list"];
		const $w = control ? control.$wrapper || $(control.wrapper) : null;
		if ($w && $w.hasClass("ge-away")) {
			// Shown to be filled in, and kept shown through the refreshes that follow its change.
			if (!this.revealed) this.revealed = new Set();
			this.revealed.add(fieldname);
			$w.removeClass("ge-away").addClass("ge-revealed");
		}
	}

	// editor.js set_value_local: a field the person changed stays in view for the rest of this visit,
	// whatever their list or "only fields with a value" says (view.js field_hidden `revealed`).
	on_field_edited(fieldname) {
		if (!fieldname || this.is_new) return;
		if (!this.revealed) this.revealed = new Set();
		this.revealed.add(fieldname);
	}

	// editor.js collect_steps: a drawn section is read back only while it shows (review of #1299).
	step_visible(step) {
		if (!this.sections || !step) return true;
		const $sec = this.section_el(step.key);
		if (!$sec.length) return false;
		return !$sec.prop("hidden") && !$sec.closest(".ge-group").prop("hidden");
	}

	// ------------------------------------------------------------------ where the record opens

	note_position() {
		if (this.is_new || !this.name || !this.step_key || !this.entry) return;
		if (this.kit.last_step(this.entry.last, this.name) === this.step_key) return;
		this.entry.last = this.kit.remember_last(this.entry.last, this.name, this.step_key);
		this.store_entry();
		this.last_pending = { doctype: this.doctype, name: this.name, step: this.step_key };
		clearTimeout(this.last_timer);
		this.last_timer = setTimeout(() => this.send_position(false), 1500);
	}

	// Sent once the person has settled; on leaving the tab, as a beacon so a reload still finds it.
	send_position(leaving) {
		clearTimeout(this.last_timer);
		this.last_timer = null;
		const last = this.last_pending;
		this.last_pending = null;
		if (!last || !this.sf) return;
		const method = GE_PREFS + "remember_guided_section";
		if (leaving && navigator.sendBeacon && typeof FormData !== "undefined") {
			try {
				const form = new FormData();
				Object.keys(last).forEach((key) => form.append(key, last[key]));
				form.append("csrf_token", frappe.csrf_token || "");
				if (navigator.sendBeacon(`/api/method/${method}`, form)) return;
			} catch (e) {
				// Falls through to a plain request.
			}
		}
		this.sf.call(method, last, { quiet: true }).catch(() => {});
	}

	// ------------------------------------------------------------------ the settings panel

	toggle_view_settings() {
		if (this.$panel && this.$panel.is(":visible")) this.close_view_settings(true);
		else this.open_view_settings("main");
	}

	open_view_settings(page) {
		if (this.is_new || !this.entry || !this.$shell) return;
		this.close_overlay();
		if (!this.$panel) {
			this.$panel = $('<div class="ge-settings" role="dialog" aria-modal="false" aria-labelledby="ge-settings-title"></div>')
				.on("keydown", (event) => {
					if (event.key !== "Escape") return;
					event.preventDefault();
					event.stopPropagation();
					this.close_view_settings(true);
				})
				.appendTo(this.$root);
		}
		this.settings_page = page || "main";
		this.render_settings();
		this.$panel.prop("hidden", false);
		this.$root.find(".ge-view-btn").attr("aria-expanded", "true");
		const title = this.$panel.find(".ge-settings-title")[0];
		if (title) title.focus();
		if (!this.outside_bound) {
			this.outside_bound = true;
			document.addEventListener("mousedown", (event) => {
				if (!this.$panel || this.$panel.prop("hidden")) return;
				const t = event.target;
				if (this.$panel[0].contains(t) || $(t).closest(".ge-view-btn, .modal, .ge-overlay").length) return;
				this.close_view_settings(false);
			});
		}
	}

	close_view_settings(return_focus) {
		if (!this.$panel || this.$panel.prop("hidden")) return;
		this.$panel.prop("hidden", true);
		this.$root.find(".ge-view-btn").attr("aria-expanded", "false");
		if (return_focus) this.$root.find(".ge-view-btn").trigger("focus");
	}

	render_settings() {
		const $panel = this.$panel.empty();
		const $head = $('<div class="ge-settings-head"></div>').appendTo($panel);
		if (this.settings_page !== "main") {
			$('<button type="button" class="sf-btn sf-btn--quiet ge-settings-back"></button>')
				.text(__("← View settings"))
				.on("click", () => this.open_view_settings("main"))
				.appendTo($head);
		}
		const titles = {
			main: __("View settings"),
			sections: __("Sections and fields"),
			summary: __("Summary fields"),
		};
		$('<h2 class="ge-settings-title" id="ge-settings-title" tabindex="-1"></h2>')
			.text(titles[this.settings_page] || titles.main)
			.appendTo($head);
		$('<button type="button" class="ge-settings-close"></button>')
			.text("×")
			.attr("aria-label", __("Close view settings"))
			.on("click", () => this.close_view_settings(true))
			.appendTo($head);
		const $body = $('<div class="ge-settings-body"></div>').appendTo($panel);
		if (this.settings_page === "sections") this.render_settings_sections($body);
		else if (this.settings_page === "summary") this.render_settings_summary($body);
		else this.render_settings_main($body);
	}

	render_settings_main($body) {
		$('<p class="ge-set-note"></p>')
			.text(__("Only your own view of {0} records changes. It is saved for you, on every device.", [__(this.doctype)]))
			.appendTo($body);
		this.render_presets($body);
		const prefs = this.entry.prefs;
		const group = (key, legend, options, note) => {
			const $set = $('<fieldset class="ge-set-group"></fieldset>').attr("data-key", key).appendTo($body);
			$('<legend class="ge-set-legend"></legend>').text(legend).appendTo($set);
			const $options = $('<div class="ge-set-options"></div>').appendTo($set);
			options.forEach(([value, label]) => {
				const id = `ge-set-${key}-${value}`;
				const $label = $('<label class="ge-set-option"></label>').attr("for", id).appendTo($options);
				$('<input type="radio">')
					.attr({ id, name: `ge-set-${key}`, value })
					.prop("checked", prefs[key] === value)
					.on("change", () => this.set_prefs({ [key]: value }))
					.appendTo($label);
				$('<span class="ge-set-option-text"></span>').text(label).appendTo($label);
			});
			if (note) $('<p class="ge-set-hint"></p>').text(note).appendTo($set);
		};
		group("layout", __("Layout"), [
			["page", __("One page")],
			["tabs", __("Tabs")],
			["steps", __("Step by step")],
		]);
		if (this.steps_only()) {
			// The Item flow's blocks need Step by step (view.js effective_view): say so, and offer nothing
			// that would not apply.
			const $layout = $body.find('fieldset[data-key="layout"]');
			$layout.find("input").prop("disabled", true);
			$layout.find('input[value="steps"]').prop("checked", true);
			$('<p class="ge-set-hint"></p>')
				.text(__("{0} records always open step by step here: their steps have blocks of their own.", [__(this.doctype)]))
				.appendTo($layout);
		}
		group(
			"nav",
			__("Where the steps sit"),
			[
				["left", __("Left")],
				["top", __("Top")],
				["right", __("Right")],
			],
			__("Always along the top on a phone.")
		);
		group("summary", __("Summary"), [
			["right", __("Right")],
			["left", __("Left")],
			["hidden", __("Hidden")],
		]);
		group("notes", __("Notes and comments"), [
			["below", __("Below the form")],
			["side", __("Side column")],
			["collapsed", __("Collapsed")],
		]);
		// Offered only once something has mounted into .ge-tools (the record tools, a separate
		// change, draw there). Until then the choice would do nothing, so it is not shown; whatever
		// fills .ge-tools makes it appear, with nothing to change here.
		if (this.tools_present()) {
			group("tools", __("Record tools"), [
				["side", __("Side column")],
				["below", __("Below the form")],
				["collapsed", __("Collapsed")],
			]);
		}
		group(
			"focus",
			__("Focus mode"),
			[
				["off", __("Off")],
				["on", __("On")],
			],
			__("Only the form and its steps: no summary, no side column, no hints. Press F.")
		);
		group("text", __("Text size"), [
			["normal", __("Normal")],
			["large", __("Large")],
			["larger", __("Larger")],
		]);
		group("contrast", __("Contrast"), [
			["normal", __("Normal")],
			["high", __("High")],
		]);
		group("density", __("Spacing"), [
			["comfortable", __("Comfortable")],
			["compact", __("Compact")],
		]);
		group(
			"hints",
			__("Field hints"),
			[
				["shown", __("Under each field")],
				["hidden", __("Only in the (i)")],
			]
		);
		group(
			"fields",
			__("Fields"),
			[
				["all", __("All fields")],
				["filled", __("Only fields with a value")],
			],
			__("A field that is required, or that this stage still needs, always shows.")
		);
		// Where a record opens.
		const $land = $('<div class="ge-set-group ge-set-landing"></div>').appendTo($body);
		$('<label class="ge-set-legend" for="ge-set-landing"></label>').text(__("Open records on")).appendTo($land);
		const $select = $('<select class="form-control ge-set-select" id="ge-set-landing"></select>').appendTo($land);
		$("<option></option>").attr("value", "last").text(__("Where I left off")).appendTo($select);
		$("<option></option>").attr("value", "first").text(__("The first section")).appendTo($select);
		(this.flow.steps || []).forEach((step) => $("<option></option>").attr("value", step.key).text(step.title).appendTo($select));
		$select.val(this.entry.landing).on("change", () => {
			this.entry.landing = this.kit.clean_landing($select.val());
			this.store_entry();
			this.save_entry({ landing: this.entry.landing });
		});
		const $more = $('<div class="ge-set-actions"></div>').appendTo($body);
		const action = (label, fn, cls) =>
			$(`<button type="button" class="sf-btn sf-btn--secondary ${cls || ""}"></button>`).text(label).on("click", fn).appendTo($more);
		action(__("Pinned, hidden and shown sections and fields…"), () => this.open_view_settings("sections"), "ge-set-sections");
		action(__("Summary fields…"), () => this.open_view_settings("summary"), "ge-set-summary");
		action(__("Keyboard shortcuts"), () => this.show_shortcuts(), "ge-set-keys");
		action(
			__("Reset to defaults"),
			() => {
				this.set_prefs(this.kit.VIEW_DEFAULTS);
				this.sf.toast(__("Your view is back to the defaults."), { tone: "info", timeout: 3000 });
			},
			"ge-set-reset"
		);
	}

	// Named sets of these settings ("My setup", "Phone"): pick one to switch to it.
	render_presets($body) {
		const $set = $('<fieldset class="ge-set-group ge-set-presets"></fieldset>').appendTo($body);
		$('<legend class="ge-set-legend"></legend>').text(__("Saved views")).appendTo($set);
		const presets = this.entry.presets || [];
		const current = this.kit.current_preset(presets, this.entry.prefs);
		if (presets.length) {
			const $row = $('<div class="ge-set-row"></div>').appendTo($set);
			const $select = $('<select class="form-control ge-set-select ge-preset-select"></select>')
				.attr("aria-label", __("Saved view"))
				.appendTo($row);
			$("<option></option>").attr("value", "").text(__("Choose a saved view")).appendTo($select);
			presets.forEach((preset) =>
				$("<option></option>")
					.attr("value", preset.name)
					.text(current && current.name === preset.name ? __("{0} (showing)", [preset.name]) : preset.name)
					.appendTo($select)
			);
			$select.val(current ? current.name : "");
			$('<button type="button" class="sf-btn sf-btn--secondary ge-preset-apply"></button>')
				.text(__("Use"))
				.on("click", () => {
					const preset = presets.find((p) => p.name === $select.val());
					if (!preset) return;
					this.set_prefs(preset.prefs);
					this.sf.toast(__("Showing your view {0}.", [preset.name]), { tone: "info", timeout: 3000 });
				})
				.appendTo($row);
			$('<button type="button" class="sf-btn sf-btn--quiet ge-preset-delete"></button>')
				.text(__("Delete"))
				.on("click", () => {
					const name = $select.val();
					if (!name) return;
					this.entry.presets = this.kit.delete_preset(this.entry.presets, name);
					this.store_entry();
					this.save_entry({ presets: this.entry.presets });
					this.render_settings();
					this.$panel.find(".ge-preset-name").trigger("focus");
				})
				.appendTo($row);
		}
		const $new = $('<div class="ge-set-row"></div>').appendTo($set);
		const $name = $('<input type="text" class="form-control ge-preset-name">')
			.attr({ maxlength: this.kit.MAX_PRESET_NAME, placeholder: __("A name, such as Phone"), "aria-label": __("Name for this view") })
			.appendTo($new);
		const save = () => {
			const result = this.kit.save_preset(this.entry.presets, $name.val(), this.entry.prefs);
			if (!result.ok) {
				this.sf.toast(
					result.reason === "full"
						? __("You can keep {0} saved views. Delete one first.", [this.kit.MAX_PRESETS])
						: __("Give the view a name."),
					{ tone: "warning" }
				);
				return;
			}
			this.entry.presets = result.presets;
			this.store_entry();
			this.save_entry({ presets: this.entry.presets });
			this.sf.toast(__("Saved as {0}.", [result.name]), { tone: "success", timeout: 3000 });
			this.render_settings();
		};
		$name.on("keydown", (event) => {
			if (event.key === "Enter") {
				event.preventDefault();
				save();
			}
		});
		$('<button type="button" class="sf-btn sf-btn--secondary ge-preset-save"></button>')
			.text(__("Save this view"))
			.on("click", save)
			.appendTo($new);
	}

	// After a change made elsewhere (a preset, the Focus button, a pin): the panel shows it.
	refresh_settings_state() {
		if (!this.$panel || this.$panel.prop("hidden") || !this.entry) return;
		if (this.settings_page !== "main") return;
		this.$panel.find("fieldset.ge-set-group[data-key]").each((i, el) => {
			const key = el.getAttribute("data-key");
			$(el)
				.find("input[type=radio]")
				.each((j, input) => {
					input.checked = this.entry.prefs[key] === input.value;
				});
		});
		const presets = this.entry.presets || [];
		const current = this.kit.current_preset(presets, this.entry.prefs);
		const $select = this.$panel.find(".ge-preset-select");
		$select.find("option").each((i, option) => {
			const name = option.value;
			if (!name) return;
			option.textContent = current && current.name === name ? __("{0} (showing)", [name]) : name;
		});
		if (!$select.is(":focus")) $select.val(current ? current.name : "");
	}

	// Pin, show or put away each section, and each field in it.
	render_settings_sections($body) {
		$('<p class="ge-set-note"></p>')
			.text(
				__(
					"Pinned sections come first in One page and Tabs. Put away what you never use: a field that is required and empty, or that this stage still needs, always shows, and the bar over the form counts what is put away."
				)
			)
			.appendTo($body);
		$('<button type="button" class="sf-btn sf-btn--secondary ge-set-showall"></button>')
			.text(__("Show everything and unpin all"))
			.on("click", () => {
				this.entry.pinned = [];
				this.entry.hidden_fields = [];
				this.entry.hidden_steps = [];
				this.store_entry();
				this.save_entry({ pinned: [], hidden_fields: [], hidden_steps: [] });
				if (this.sections) this.redraw_stage();
				else this.apply_visibility();
				this.render_settings();
			})
			.appendTo($body);
		const missing = new Set((((this.completeness || {}).missing) || []).map((item) => item && item.fieldname));
		const $list = $('<ul class="ge-set-sections"></ul>').appendTo($body);
		(this.flow.steps || []).forEach((step) => {
			const $item = $('<li class="ge-set-section"></li>').appendTo($list);
			const $row = $('<div class="ge-set-row"></div>').appendTo($item);
			$('<span class="ge-set-section-title"></span>').text(step.title).appendTo($row);
			const pinned = this.entry.pinned.includes(step.key);
			$('<button type="button" class="sf-btn sf-btn--quiet ge-set-pin"></button>')
				.attr("aria-pressed", pinned ? "true" : "false")
				.attr("aria-label", pinned ? __("Unpin {0}", [step.title]) : __("Pin {0}", [step.title]))
				.html(GE_PIN_ICON)
				.append($("<span></span>").text(pinned ? __("Pinned") : __("Pin")))
				.on("click", () => {
					this.toggle_pin(step.key);
					this.render_settings();
					this.$panel.find(".ge-set-pin").eq((this.flow.steps || []).indexOf(step)).trigger("focus");
				})
				.appendTo($row);
			const show_id = `ge-set-step-${step.key}`;
			const $show = $('<label class="ge-set-check"></label>').attr("for", show_id).appendTo($row);
			$('<input type="checkbox">')
				.attr("id", show_id)
				.prop("checked", !this.entry.hidden_steps.includes(step.key))
				.on("change", (event) => {
					this.entry.hidden_steps = this.kit.toggle_key(this.entry.hidden_steps, step.key, !event.currentTarget.checked, this.kit.MAX_HIDDEN);
					this.store_entry();
					this.save_entry({ hidden_steps: this.entry.hidden_steps });
					this.apply_visibility(false);
				})
				.appendTo($show);
			$("<span></span>").text(__("Show")).appendTo($show);
			// A section with a field the record still needs shows whatever is ticked: say so.
			if (!this.kit.step_hidden(step, this.values, { hidden_steps: [step.key], missing })) {
				$('<span class="ge-set-why"></span>').text(__("(shows while a needed field is empty)")).appendTo($row);
			}
			const fields =(step.fields || []).filter((field) => field && field.fieldname && !this.kit.is_layout(field));
			if (!fields.length) return;
			const $details = $('<details class="ge-set-fields"></details>').appendTo($item);
			$("<summary></summary>").text(__("Fields ({0})", [fields.length])).appendTo($details);
			fields.forEach((field) => {
				const id = `ge-set-field-${field.fieldname}`;
				const value = this.values[field.fieldname];
				const may = this.kit.can_hide(field, value, missing);
				const $label = $('<label class="ge-set-check"></label>').attr("for", id).appendTo($details);
				$('<input type="checkbox">')
					.attr("id", id)
					.prop("checked", !this.entry.hidden_fields.includes(field.fieldname))
					.prop("disabled", !may)
					.on("change", (event) => {
						this.entry.hidden_fields = this.kit.toggle_key(
							this.entry.hidden_fields,
							field.fieldname,
							!event.currentTarget.checked,
							this.kit.MAX_HIDDEN
						);
						this.store_entry();
						this.save_entry({ hidden_fields: this.entry.hidden_fields });
						this.apply_visibility(false);
					})
					.appendTo($label);
				$("<span></span>").text(__(field.label || field.fieldname)).appendTo($label);
				if (!may) $('<span class="ge-set-why"></span>').text(__("(always shown: needed)")).appendTo($label);
			});
		});
		$('<p class="ge-set-hint"></p>').text(__("Sections you put away stay in Step by step, which walks every step.")).appendTo($body);
	}

	// The fields the summary shows: the DocType's own summary, or up to 12 of the record's fields.
	render_settings_summary($body) {
		const max = this.kit.MAX_SUMMARY_FIELDS;
		$('<p class="ge-set-note"></p>')
			.text(__("Choose up to {0} of the record's fields for the summary, in the order you tick them.", [max]))
			.appendTo($body);
		const custom = Array.isArray(this.entry.summary_fields);
		$('<button type="button" class="sf-btn sf-btn--secondary ge-set-sumreset"></button>')
			.text(__("Use the standard summary"))
			.prop("disabled", !custom)
			.on("click", () => {
				this.entry.summary_fields = null;
				this.store_entry();
				this.save_entry({ summary_fields: null });
				this.refresh_chrome();
				this.render_settings();
			})
			.appendTo($body);
		const config = this.kit.SUMMARY[this.doctype] || {};
		const standard = [config.account, config.title, config.streams && config.streams.field, config.amount, config.status].filter(
			(fieldname) => fieldname && this.defs[fieldname]
		);
		const chosen = custom ? this.entry.summary_fields.slice() : standard;
		const $list = $('<div class="ge-set-summary-list"></div>').appendTo($body);
		(this.flow.steps || []).forEach((step) => {
			const fields = (step.fields || []).filter((field) => field && field.fieldname && !this.kit.is_layout(field));
			if (!fields.length) return;
			const $set = $('<fieldset class="ge-set-group"></fieldset>').appendTo($list);
			$('<legend class="ge-set-legend"></legend>').text(step.title).appendTo($set);
			fields.forEach((field) => {
				const id = `ge-set-sum-${field.fieldname}`;
				const $label = $('<label class="ge-set-check"></label>').attr("for", id).appendTo($set);
				$('<input type="checkbox">')
					.attr("id", id)
					.prop("checked", chosen.includes(field.fieldname))
					.on("change", (event) => {
						const base = Array.isArray(this.entry.summary_fields) ? this.entry.summary_fields.slice() : standard.slice();
						let next = base.filter((name) => name !== field.fieldname);
						if (event.currentTarget.checked) {
							if (next.length >= max) {
								event.currentTarget.checked = false;
								this.sf.toast(__("The summary holds {0} fields. Untick one first.", [max]), { tone: "warning" });
								return;
							}
							next = next.concat([field.fieldname]);
						}
						this.entry.summary_fields = next;
						this.store_entry();
						this.save_entry({ summary_fields: next });
						this.refresh_chrome();
						this.$panel.find(".ge-set-sumreset").prop("disabled", false);
					})
					.appendTo($label);
				$("<span></span>").text(__(field.label || field.fieldname)).appendTo($label);
			});
		});
	}

	// ------------------------------------------------------------------ pop-ups

	open_overlay(build, label_id) {
		this.close_overlay();
		this.overlay_return = document.activeElement;
		const $overlay = (this.$overlay = $('<div class="ge-overlay"></div>').appendTo(this.$root));
		const $box = $('<div class="ge-overlay-box" role="dialog" aria-modal="true"></div>').attr("aria-labelledby", label_id).appendTo($overlay);
		build($box);
		$overlay.on("mousedown", (event) => {
			if (event.target === $overlay[0]) this.close_overlay();
		});
		$box.on("keydown", (event) => {
			if (event.key === "Escape") {
				event.preventDefault();
				event.stopPropagation();
				this.close_overlay();
				return;
			}
			if (event.key !== "Tab") return;
			// Keep Tab inside the pop-up while it is open.
			const items = $box.find("button, a[href], input, select, textarea").filter(":visible").toArray();
			if (!items.length) return;
			const first = items[0];
			const last = items[items.length - 1];
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		});
		const $first = $box.find(".ge-overlay-primary, button").first();
		if ($first.length) $first.trigger("focus");
	}

	close_overlay() {
		if (!this.$overlay) return;
		this.$overlay.remove();
		this.$overlay = null;
		const back = this.overlay_return;
		this.overlay_return = null;
		if (back && document.contains(back) && typeof back.focus === "function") back.focus();
	}

	// The first time a person opens a saved record here: what they can make of the page. Once per
	// person, remembered on the server the moment it shows, so it is never shown twice.
	maybe_intro(tries) {
		if (this.is_new || this.in_setup() || !this.view_store_ok || !this.view_store || this.view_store.intro_seen) return;
		// Never over something that asks the person to decide: the chooser for kept changes
		// (after_restore), a conflict, or a dialog such as the Closed Won nudge, which may open a moment
		// after the record. Asked again until the way is clear, on this record only.
		const doctype = this.doctype;
		const name = this.name;
		const n = tries || 0;
		// This page's own pop-up (the shortcuts list) counts too: the intro would close it.
		const busy =
			!!this.conflict ||
			!!this.$overlay ||
			!!document.querySelector(".modal.show, .ge-overlay") ||
			!!(window.cur_dialog && window.cur_dialog.display);
		if (n === 0 || busy) {
			if (n > 120) return;
			clearTimeout(this.intro_timer);
			this.intro_timer = setTimeout(() => {
				if (this.showing() && this.doctype === doctype && this.name === name) this.maybe_intro(n + 1);
			}, n === 0 ? 700 : 1000);
			return;
		}
		this.view_store.intro_seen = true;
		this.sf.call(GE_PREFS + "mark_guided_intro_seen", {}, { quiet: true }).catch(() => {});
		this.open_overlay(($box) => {
			$('<h2 class="ge-overlay-title" id="ge-intro-title"></h2>').text(__("Make this page yours")).appendTo($box);
			$('<p class="ge-overlay-text"></p>')
				.text(__("A saved record can be laid out the way you like. Only your own view changes, on every device."))
				.appendTo($box);
			const $list = $('<ul class="ge-overlay-list"></ul>').appendTo($box);
			[
				__("Layout: everything on one page, one tab at a time, or step by step."),
				__("Where the steps sit: on the left, on the right or along the top."),
				__("Pin the sections you use most, and they come first."),
				__("Focus mode keeps just the form. Press F."),
				__("Save a setup as a named view, such as Phone, and switch between them."),
				__("Keyboard shortcuts: press ? to see them."),
			].forEach((line) => $("<li></li>").text(line).appendTo($list));
			const $actions = $('<div class="ge-overlay-actions"></div>').appendTo($box);
			$('<button type="button" class="sf-btn sf-btn--primary ge-overlay-primary"></button>')
				.text(__("Open view settings"))
				.on("click", () => {
					this.close_overlay();
					this.open_view_settings("main");
				})
				.appendTo($actions);
			$('<button type="button" class="sf-btn sf-btn--secondary ge-overlay-close"></button>')
				.text(__("Got it"))
				.on("click", () => this.close_overlay())
				.appendTo($actions);
		}, "ge-intro-title");
	}

	show_shortcuts() {
		const what = {
			next: __("Next section"),
			prev: __("Previous section"),
			save: __("Save now"),
			comments: __("Go to notes and comments"),
			settings: __("Open view settings"),
			focus: __("Focus mode on or off"),
			help: __("This list"),
		};
		this.open_overlay(($box) => {
			$('<h2 class="ge-overlay-title" id="ge-keys-title"></h2>').text(__("Keyboard shortcuts")).appendTo($box);
			$('<p class="ge-overlay-text"></p>')
				.text(__("On this page, while you are not typing in a field. Save works anywhere."))
				.appendTo($box);
			const $list = $('<dl class="ge-keys"></dl>').appendTo($box);
			this.kit.SHORTCUTS.forEach((shortcut) => {
				const $row = $('<div class="ge-keys-row"></div>').appendTo($list);
				$("<dt></dt>").append($("<kbd></kbd>").text(shortcut.label)).appendTo($row);
				$("<dd></dd>").text(what[shortcut.action] || shortcut.action).appendTo($row);
			});
			const $actions = $('<div class="ge-overlay-actions"></div>').appendTo($box);
			$('<button type="button" class="sf-btn sf-btn--primary ge-overlay-primary"></button>')
				.text(__("Close"))
				.on("click", () => this.close_overlay())
				.appendTo($actions);
			const keys = frappe.ui && frappe.ui.keys;
			if (keys && typeof keys.show_keyboard_shortcut_dialog === "function") {
				$('<button type="button" class="sf-btn sf-btn--quiet"></button>')
					.text(__("All Desk shortcuts"))
					.on("click", () => {
						this.close_overlay();
						keys.show_keyboard_shortcut_dialog();
					})
					.appendTo($actions);
			}
		}, "ge-keys-title");
	}

	// ------------------------------------------------------------------ keyboard

	on_key(event) {
		if (!this.kit || !this.$shell || !this.flow || !this.showing()) return;
		const target = event.target;
		const typing =
			!!target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
		const action = this.kit.shortcut_for(event, typing);
		if (!action) return;
		// A dialog in front (a quick entry, Set as Lost) keeps its own keys.
		if (document.querySelector(".modal.show")) return;
		if (this.$overlay && action !== "help") return;
		if (this.is_new && action !== "help") return;
		event.preventDefault();
		event.stopPropagation();
		this.run_shortcut(action);
	}

	run_shortcut(action) {
		if (action === "next" || action === "prev") this.move_section(action === "next" ? 1 : -1);
		else if (action === "save") this.save_shortcut();
		else if (action === "comments") this.jump_to_comments();
		else if (action === "settings") this.toggle_view_settings();
		else if (action === "focus") this.toggle_focus();
		else if (action === "help") {
			if (this.$overlay) this.close_overlay();
			else this.show_shortcuts();
		}
	}

	move_section(dir) {
		if (!this.sections) {
			const key = this.kit.neighbor(this.flow.steps, this.values, this.step_key, dir);
			if (key) this.navigate(key);
			return;
		}
		const key = this.kit.neighbor_section(this.visible_sections(), this.step_key, dir);
		if (key) {
			this.step_key = key;
			this.scroll_to_section(key, true);
			this.note_position();
			return;
		}
		const target = this.neighbor_screen(dir);
		if (target) this.navigate(target);
	}

	// Ctrl/Cmd+S: send what is waiting now. The editor's own save-now when it has one.
	save_shortcut() {
		if (typeof this.save_now === "function") {
			this.save_now();
			return;
		}
		if (this.is_new || !this.queue) return;
		this.collect_step();
		const waiting = this.queue.has_unsaved();
		this.queue
			.flush()
			.then(() => this.sf.toast(waiting ? __("Saved.") : __("Everything is saved."), { tone: "success", timeout: 2500 }))
			.catch(() => {});
	}

	jump_to_comments() {
		const $comments = this.$root.find(".ge-comments");
		if (!$comments.length || $comments.prop("hidden")) return;
		this.block_open.notes = true;
		this.refresh_blocks();
		const el = $comments.find(".ge-comments-title")[0] || $comments[0];
		el.setAttribute("tabindex", "-1");
		this.scroll_into(el, el);
	}
}
