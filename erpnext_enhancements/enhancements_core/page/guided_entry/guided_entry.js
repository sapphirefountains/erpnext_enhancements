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
// name builder and its supplier, stock and asset blocks are public/js/guided_item/). The page
// knows only that small interface: render, on_change, before_create, leave, and the ctx it lends
// (ext_context). A block that throws is caught here, so the step's own fields always show.
// "More" steps (config `more`) are the rest of the form past a flow's core: Next on the last core
// step offers Done, with More details beside the step and every step still in the rail.
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
const GE_LIST_SUFFIX = "__ge_list";
const GE_INFO_ICON =
	'<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true" focusable="false">' +
	'<circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.4"/>' +
	'<circle cx="8" cy="4.7" r="0.95" fill="currentColor"/>' +
	'<rect x="7.2" y="6.8" width="1.6" height="5" rx="0.8" fill="currentColor"/></svg>';

function ge_clone(value) {
	return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function ge_has(object, key) {
	return !!object && Object.prototype.hasOwnProperty.call(object, key);
}

// localStorage, which a private window or blocked site data can refuse: every read and write is
// guarded, and a refused write reports false so the page warns before the tab closes.
function ge_storage() {
	try {
		return window.localStorage || null;
	} catch (e) {
		return null;
	}
}

function ge_read_kept(key) {
	const store = ge_storage();
	if (!store) return null;
	try {
		return JSON.parse(store.getItem(key) || "null");
	} catch (e) {
		return null;
	}
}

// The signed-in user: journal keys carry it, so a shared phone never mixes two people's copies.
function ge_user() {
	return (frappe.session && frappe.session.user) || "";
}

// Every stored key that starts with `prefix`.
function ge_keys(prefix) {
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
function ge_write_kept(key, entry) {
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
function ge_client_id() {
	try {
		if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
	} catch (e) {
		// Falls through to the timestamp-and-random id.
	}
	return `ge-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
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
		this.ready = new Promise((resolve) => frappe.require(GE_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit;
			this.kit = window.sf_form_kit;
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
		// P4.7: the signal is back; send what waited for it.
		window.addEventListener("online", () => this.signal_back());
		// The server's value from before each unsent change, per record (journal.js `base`).
		this.bases = {};
		// Kept values waiting on the person's choice after a restore, per record (write_journal).
		this.held = {};
		// Keys the phone refused to write: closing the tab warns while any remain (kept_on_phone).
		this.failed_keys = new Set();
		this.signal_timer = null;
		this.ready.then(() => this.sweep_journal());
		// Back from signing in on another tab: try the save that the lapsed session refused.
		window.addEventListener("focus", () => {
			if (this.queue && this.queue.state === "expired") this.queue.retry().catch(() => {});
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

	reset() {
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
		this.client_id = null;
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

	back() {
		const prev = this.kit.neighbor(this.flow.steps, this.values, this.step_key, -1);
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
		const next = this.kit.neighbor(this.flow.steps, this.values, this.step_key, 1);
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
		this.toggle_all_help(false);
		this.save_in_background();
	}

	ends_core() {
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
		this.ext = this.kit.extension_for ? this.kit.extension_for(doctype) : null;
		this.page.set_title(__("Guided Entry"));
		this.sf.state.render(this.$root, "loading");
		this.ensure_meta(doctype)
			.then(() =>
				this.sf.call(GE_API + "get_flow", { doctype, name: name || null }, { quiet: true, method: "GET" })
			)
			.then((flow) => {
				if (seq !== this.load_seq) return;
				this.take_flow(flow);
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
		crm.confirm_won_next_step(this.name, { mode: "reopen" });
	}

	on_project_status(data) {
		if (this.doctype !== "Opportunity" || !this.showing() || data.opportunity_name !== this.name) return;
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
				this.sf.toast(__("Project {0} was created.", [data.project_doc.name]), { tone: "success", timeout: 8000 });
				if (data.drive_error) {
					frappe.msgprint({
						title: __("Google Drive Integration Failed"),
						indicator: "orange",
						message: `${__("Project {0} was created, but its Drive folder was not.", [link])}<pre>${frappe.utils.escape_html(String(data.drive_error))}</pre>`,
					});
				}
			} else {
				this.sf.toast(__("Project creation failed. The Error Log has the details."), { tone: "warning", timeout: 8000 });
			}
		}
		this.reload_record();
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
		const lost = this.flow.lost;
		const dialog = new frappe.ui.Dialog({
			title: __("Set as Lost"),
			fields: [
				{ fieldtype: "HTML", fieldname: "error" },
				{ fieldtype: "Table MultiSelect", label: __("Lost Reasons"), fieldname: "reasons", options: lost.reasons, reqd: 1 },
				{ fieldtype: "Table MultiSelect", label: __("Competitors"), fieldname: "competitors", options: lost.competitors },
				{ fieldtype: "Small Text", label: __("Detailed Reason"), fieldname: "detail" },
			],
			primary_action_label: __("Declare Lost"),
			primary_action: (values) => this.declare_lost(dialog, values || {}),
		});
		dialog.show();
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
		this.save_before_moving()
			.then(() => {
				// A change that has not reached the server would be lost behind the new version.
				if (this.queue && this.queue.has_unsaved()) {
					refuse(__("Your last change has not saved yet. Try again in a moment."));
					return null;
				}
				return this.sf.call(
					GE_API + "declare_lost",
					{
						doctype,
						name,
						reasons: values.reasons || [],
						competitors: values.competitors || [],
						detail: values.detail || "",
						modified: this.queue ? this.queue.modified : this.flow.modified,
					},
					{ quiet: true }
				);
			})
			.then((result) => {
				if (result === null) return;
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
	}

	// The record is fixed when the queue is made: a queue still sending after the page has moved to
	// another record saves onto its own record, and its answers no longer touch the screen.
	make_queue(modified) {
		const doctype = this.doctype;
		const name = this.name;
		const current = () => this.queue === queue && this.name === name;
		const queue = this.kit.create_save_queue({
			delay: 2500,
			send: (values, at) =>
				this.sf.call(GE_API + "save_step", { doctype, name, values, modified: at }, { quiet: true }),
			on_state: (state, error) => {
				this.write_journal(doctype, name);
				if (state === "error" && this.kit.try_later(error)) this.wait_for_signal();
				if (current()) this.on_save_state(state, error);
			},
			on_saved: (result, sent) => current() && this.on_saved(result, sent),
			on_conflict: (result, sent) => (current() ? this.on_conflict(result, sent) : Promise.resolve(null)),
			set_timeout: (fn, ms) => window.setTimeout(fn, ms),
			clear_timeout: (id) => window.clearTimeout(id),
		});
		queue.set_modified(modified);
		queue.record_key = this.record_key(doctype, name);
		return queue;
	}

	// The start screen: for each guided DocType, a way to start a new record and the person's own
	// open ones (form_kit.api.my_records), each opening here.
	show_landing() {
		this.reset();
		this.page.set_title(__("Guided Entry"));
		const guided = (frappe.boot && frappe.boot.sf_guided) || {};
		// A flow that is not where its records open (Item, P8.1) is listed only for people who can
		// start one: a salesperson who can read Items has no Item flow to pick up.
		const doctypes = Object.keys(guided).filter(
			(doctype) => this.kit.opens_by_default(guided[doctype]) || (frappe.model.can_create && frappe.model.can_create(doctype))
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
			const by_default = window.sf_guided && window.sf_guided.is_guided && window.sf_guided.is_guided(doctype);
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

	// v16's own login path. In a new tab, so nothing typed here is lost: the Desk that boots there
	// broadcasts its new CSRF token to this tab (desk.js setup_broadcast_listeners).
	open_login() {
		const here = window.location.pathname + window.location.search;
		window.open(`/login?redirect-to=${encodeURIComponent(here)}`, "_blank", "noopener");
	}

	// ------------------------------------------------------------------ the shell

	render_shell() {
		this.page.set_title(this.is_new ? __("New {0}", [__(this.doctype)]) : this.name);
		this.$root.html(`
			<div class="ge-band">
				<div class="ge-band-inner">
					<div class="ge-band-text">
						<div class="ge-eyebrow"></div>
						<h1 class="ge-title"></h1>
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
					<section class="ge-stage"></section>
				</main>
				<aside class="ge-summary" aria-label="${frappe.utils.escape_html(__("Summary"))}"></aside>
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
		this.$root.find(".ge-summary").on("click", "button[data-step]", (event) => {
			const key = $(event.currentTarget).attr("data-step");
			const fieldname = $(event.currentTarget).attr("data-field");
			if (key === this.step_key) this.focus_field(fieldname);
			else if (key) {
				this.pending_focus = fieldname;
				this.navigate(key);
			}
		});
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

		this.render_rail(steps, pos);
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
			$('<span class="ge-rail-title"></span>').text(step.title).appendTo($text);
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
		if (config) {
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
		$box.html(
			`<section class="sf-card ge-sum-card"><h2 class="sf-card-title">${esc(__("Summary"))}</h2>` +
				(rows.length ? `<dl class="ge-sum-list">${rows.join("")}</dl>` : "") +
				`<h3 class="ge-sum-head">${esc(head)}</h3>${checklist}</section>`
		);
	}

	render_footer(steps, pos) {
		const kit = this.kit;
		const prev = this.is_new ? null : kit.neighbor(steps, this.values, this.step_key, -1);
		const next = this.is_new || this.ends_core() ? null : kit.neighbor(steps, this.values, this.step_key, 1);
		this.$root.find(".ge-back").toggleClass("is-hidden", !prev).prop("disabled", !prev);
		let label;
		if (this.is_new) label = this.creating ? __("Creating…") : __("Create and continue");
		else if (next) label = __("Next");
		else label = __("Done");
		this.$root
			.find(".ge-next")
			.text(label)
			.prop("disabled", !!this.creating)
			.attr("aria-label", next ? __("Next: {0}", [(kit.find_step(steps, next) || {}).title || ""]) : label);
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

	// ------------------------------------------------------------------ a step

	show_step(step_key) {
		if (!this.flow) return;
		const steps = this.flow.steps || [];
		let key = step_key;
		if (this.is_new) {
			key = (steps.find((step) => step.create) || steps[0] || {}).key;
		} else {
			const resolved = this.kit.resolve_step(steps, this.values, step_key);
			key = resolved.key;
			if (resolved.replace && key) this.go(key, true);
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
		if (key === this.step_key && this.fg) {
			this.refresh_chrome();
			return;
		}
		this.render_step(key);
	}

	render_step(key) {
		const step = this.kit.find_step(this.flow.steps, key);
		const moved = this.step_key !== null;
		this.leave_extension();
		this.step_key = key;
		this.fg = null;
		this.toggle_all_help(false);
		const pos = this.kit.position(this.flow.steps, this.values, key);
		const editable = !!this.flow.can_write && !step.read_only;
		const $stage = this.$root.find(".ge-stage").empty();
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
		this.mount_extension(step, $fields);
		if (this.ends_core()) {
			// The rest of the form, past the flow's core: one tap on, never in the way of Done.
			const next = this.kit.neighbor(this.flow.steps, this.values, key, 1);
			$('<button type="button" class="sf-btn sf-btn--secondary ge-more"></button>')
				.text(__("More details: {0}", [(this.kit.find_step(this.flow.steps, next) || {}).title || ""]))
				.on("click", () => {
					const now = this.kit.neighbor(this.flow.steps, this.values, this.step_key, 1);
					if (now) this.navigate(now);
				})
				.appendTo($stage);
		}
		// A grid says nothing when a row is removed or moved, and a cell edit may not reach the
		// table's own onchange: read the grids again after either (delete_rows runs in 0.1 s steps).
		$fields.on("click", ".grid-remove-rows, .grid-delete-row, .grid-add-row, .grid-move-row", () =>
			setTimeout(() => this.collect_tables(), 350)
		);
		$fields.on("change", ".form-grid input, .form-grid textarea, .form-grid select", () =>
			setTimeout(() => this.collect_tables(), 0)
		);

		this.refresh_notices();
		this.refresh_chrome();
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

	control_def(field, step, editable) {
		const kit = this.kit;
		const df = Object.assign({}, field);
		delete df.child_fields;
		delete df.hint;
		delete df.help;
		delete df.create;
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

	// ------------------------------------------------------------------ extensions (P8.1)

	// The step's block, above its fields, when the config names one and the DocType's extension
	// draws it. Anything the block throws stays in the block: the fields below always show.
	mount_extension(step, $fields) {
		if (!this.ext || !step.extension || typeof this.ext.render !== "function") return;
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
		const step = this.kit.find_step(this.flow.steps, this.step_key);
		try {
			this.ext.on_change(fieldname, value, this.ext_context(step));
		} catch (e) {
			if (window.console) window.console.error("[Guided Entry] the step's block failed", e);
		}
	}

	// What the page lends a block (public/js/form_kit/extensions.js documents it). The record is
	// read when called, never captured, so a block outliving a step cannot write to another record.
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

	add_help($wrapper, field, step) {
		if (!$wrapper || !$wrapper.length) return;
		const id = `ge-help-${field.fieldname}`;
		const label = __(field.label || field.fieldname);
		const $button = $(`<button type="button" class="ge-help-btn" aria-expanded="false"></button>`)
			.attr("aria-controls", id)
			.attr("aria-label", __("More about {0}", [label]))
			.html(GE_INFO_ICON);
		const $panel = $('<div class="ge-help-panel" hidden></div>').attr("id", id);
		$('<p class="ge-help-text"></p>').text(field.help).appendTo($panel);
		const article = this.flow.help_article;
		if (article) {
			const href = `${frappe.utils.get_form_link("Knowledge Article", article)}#${encodeURIComponent(step.key)}`;
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

	// ------------------------------------------------------------------ the text-list table

	render_text_list(field, editable) {
		const column = this.kit.text_list_column(field);
		const control = this.fg.fields_dict[field.fieldname + GE_LIST_SUFFIX];
		if (!control || !column) return;
		const rows = this.values[field.fieldname] || [];
		const label = __(field.label || field.fieldname);
		const list_id = `ge-list-${field.fieldname}`;
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
		if (field.help) this.add_help($(control.wrapper), field, this.kit.find_step(this.flow.steps, this.step_key));
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
		const changed = !this.kit.same_value(value, this.values[fieldname]);
		this.set_value_local(fieldname, value);
		if (changed) {
			((this.flow.clears || {})[fieldname] || []).forEach((cleared) => {
				if (this.kit.same_value(this.values[cleared], null)) return;
				this.set_value_local(cleared, null);
				this.show_value(cleared, null);
			});
			this.refresh_distinct();
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
		const step = this.kit.find_step(this.flow.steps, this.step_key);
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
		const step = this.kit.find_step(this.flow.steps, this.step_key);
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
		const held = this.held[key] || {};
		const unsent = {};
		const base = {};
		Object.keys(held).forEach((fieldname) => {
			unsent[fieldname] = held[fieldname].mine;
			base[fieldname] = held[fieldname].base;
		});
		let modified = null;
		[...this.retired, this.queue].forEach((queue) => {
			if (!queue || queue.record_key !== key) return;
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
				frappe.route_flags.replace_route = true;
				frappe.set_route(this.kit.build_route(doctype, found.name, null));
				delete frappe.route_flags.replace_route;
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
			if (queue && queue.state === "error" && this.kit && this.kit.try_later(queue.error)) {
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
		// The step's block may know the insert would be refused (the Item flow's naming guard).
		const held = this.ext && typeof this.ext.before_create === "function" ? this.ext.before_create() : "";
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
		const doctype = this.doctype;
		const started_with = this.started_with;
		this.creating = true;
		this.notice = null;
		this.refresh_notices();
		this.refresh_chrome();
		const args = { doctype, values, client_id: this.client_id };
		if (Object.keys(this.carry || {}).length) args.prefill = this.carry;
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
				const still_here = (() => {
					const target = this.kit.parse_route(frappe.get_route());
					return !!target && target.is_new && target.doctype === doctype;
				})();
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
				this.render_shell();
				this.fg = null;
				this.step_key = null;
				// Replace the "new" entry with the record's first step, then push the next one.
				this.screen_key = "";
				this.go(first, true);
				if (next) this.go(next);
				this.sf.toast(__("{0} created.", [created.name]), { tone: "success", timeout: 4000 });
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
				action_label: __("Use the full form"),
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
				if (this.is_new) this.create();
				else if (this.queue) {
					this.collect_step();
					this.queue.retry().catch(() => {});
				}
			},
		});
	}
}
