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
		if (!this.kit) return; // left before the bundle arrived: nothing typed yet
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
		this.open_extension(doctype);
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
					<section class="ge-comments" hidden></section>
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
		// Under the steps on every step, once the record exists (editor.js mount_comments). The shell
		// is drawn again when a new record is created, which mounts them then.
		if (typeof this.mount_comments === "function") this.mount_comments();
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
}
