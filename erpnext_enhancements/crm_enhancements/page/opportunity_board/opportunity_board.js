// Opportunity Board (form kit P6, ERPNext TASK-2026-02774): the board that is to replace the
// patched stock Kanban. Part A (v1.654.0) built P6.1 (cards), P6.2 (rows by value stream), P6.5
// (the forecast strip) and P6.9 (the phone layout). Part B (v1.663.0) adds P6.3 (the board, a
// sortable table and a timeline of close dates, under one filter bar), P6.4 (the needs-attention
// list) and P6.8 (On Hold folded, snooze, quick filters and saved views). Part C (v1.675.0) adds the
// side panel: P6.6 (tap a deal to edit it there; a drop on Closed Won or Lost asks for that stage's
// required fields there before anything is saved) and P6.7 (quick add per column, bulk actions on
// selected deals). The stock Kanban, its patches and the list's redirect to it are untouched until
// P6.10; the /sales-pipeline TV board stays separate.
//
// THE SIDE PANEL (part C) is form kit P4.4's: public/js/form_kit/ui/side_panel.js, the guided page's
// own record editor (fields, save queue, conflicts, the phone's journal) in the planner kit's drawer.
// Its files load on demand, the first time a panel opens (OB_PANEL_ASSETS, frappe.require, as the
// planners load theirs); if they do not arrive, a deal opens the normal way and a closing drop opens
// the guided Close step, as before part C. Each panel is a route of this page (below), so reload
// reopens it and Back closes it; the drawer pushes no history of its own.
//
// THE FILTER BAR (P6.3, P6.8) is page state, not the address: switching views keeps it, and Back
// walks the views. It is remembered per person on the server (api/planner_views_prefs.py, planner
// "board", the planners' own store in __UserSettings), as "last used" a moment after it changes
// and as named views on request, so it survives a reload and follows the person to another
// device. A bare /desk/opportunity-board opens the last-used view; any other address wins.
//
// The data is one GET (opportunity_board.py get_board: the open deals through frappe.get_list,
// so the caller's permissions apply). Every decision about it (ages, rows, totals, forecast, what
// a drop means, the routes) is a pure ES module, public/js/opportunity_board/board.js, loaded as
// opportunity_board.bundle.js (window.sf_opportunity_board) and run under node by
// tests/test_opportunity_board.py. This file draws and wires.
//
// Screens, each its own history entry so Back and Forward walk them (Nik's rule, 2026-09-24):
//
//   /desk/opportunity-board[/<layout>]                     the board: stages | streams (rows by stream)
//   /desk/opportunity-board/<layout>/<stage>               the phone's stage tab
//   /desk/opportunity-board/<table|timeline|attention>     the other views
//   /desk/opportunity-board/<...>/move/<name>              the deal panel (move, snooze), over any view
//   /desk/opportunity-board/<...>/edit/<name>[/<step>]     the side panel, editing a deal (its step is
//                                                          replaced in the address, never pushed, so
//                                                          Back closes the panel)
//   /desk/opportunity-board/<...>/closing/<name>/<stage>   the side panel: Closed Won's or Lost's
//                                                          required fields, then the move
//   /desk/opportunity-board/<...>/new/<stage>              the side panel: quick add in that stage
//   /desk/opportunity-board/<...>/bulk/<action>            the side panel: a bulk action (the selection
//                                                          is kept per tab, so a reload finds it)
//
// The page moves only with frappe.set_route(kit.build_route(...)); frappe owns history, and Back
// or Forward re-render the route through on_page_show. There is no popstate listener. The panel
// is pushed over the view it opened from, so Back closes it before it changes the view; closing it
// any other way steps back through that same entry when this page pushed it (history.state is
// tagged ob_panel), and replaces it in place when the panel was opened from a link or a reload.
//
// MOVING A CARD. Desktop and tablet: drag, with pointer events; a mouse lifts the card after 6px,
// a touch after a 300 ms hold and a finger that moves first is scrolling (the planners' numbers,
// kit.HOLD_MS / kit.DRAG_PX). Phone (under 768px): tap the card, then tap a stage in the panel
// (the planners' tap-to-move); every card also has a Move button that opens the same panel, which
// is the way in for a keyboard. A move between open stages (and On Hold) saves the status through
// the form kit's own save path, form_kit.api.save_step, carrying the card's `modified`: a
// conflict ({conflict: true}, returned without saving) puts the card back, says who changed it,
// and reloads the board; nothing is overwritten. A drop on Closed Won or Lost saves nothing by
// itself: those need the ranks or the lost reasons, so the side panel asks for them (P6.6) and the
// move is saved with them, in one save locked on `modified`, only when the person commits. Cancel
// (or Back) leaves the card where it was; it never moved.
//
// A card's click (or tap) opens the deal in the side panel (P6.6), as do the table's, the timeline's
// and the attention list's rows. The panel's "Open full page" and the move panel's "Open opportunity"
// open the record with frappe.set_route("Form", "Opportunity", name), which
// public/js/guided_default.js sends to the guided page (v1.644.0).
//
// QUICK ADD AND BULK (P6.7). Each column's "+" opens quick add in the side panel: the account, a name,
// the value streams and a summary, created through form_kit.api.create_record with its client_id,
// already in that column's stage. "Select" turns on selection; the bar it shows changes the selected
// deals' stage (a closing stage asks for its required fields first, as a drop does), owner, expected
// close date or snooze, through opportunity_board.bulk_update, which takes each deal through the same
// save a single change takes and answers deal by deal. What failed stays selected.
//
// Every request goes through sf_kit.call(..., { quiet: true }), never frappe.call, so Frappe's
// own dialogs never cover the page. Reads are GET, writes POST. The board refreshes on the
// existing sales_pipeline_updated realtime event (published on every Opportunity save), debounced,
// and never while a card is being dragged or saved.

frappe.pages["opportunity-board"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Opportunity Board"),
		single_column: true,
	});
	// v16's container.change_to ends every route change with frappe.utils.scroll_to(0) unless the page
	// wrapper sets disable_scroll_to_top (frappe/public/js/frappe/views/container.js, version-16).
	// Every screen of this page is a route (the move panel, the phone's stage tabs, the layout), so
	// opening Move on a card far down a column would jump the board to the top. The page scrolls to
	// the top itself only when it is arrived at from somewhere else (on_page_show, below).
	wrapper.disable_scroll_to_top = true;
	wrapper.opportunity_board = new OpportunityBoard(page, wrapper);
	$(wrapper).on("hide", () => wrapper.opportunity_board.on_hide());
};

frappe.pages["opportunity-board"].on_page_show = function (wrapper) {
	const board = wrapper.opportunity_board;
	if (!board) return;
	if (board.arriving) {
		board.arriving = false;
		if (frappe.utils && typeof frappe.utils.scroll_to === "function") frappe.utils.scroll_to(0);
	}
	board.route_changed();
};

const OB_ASSETS = [
	"sf_kit.bundle.js",
	"sf_kit.bundle.css",
	"opportunity_board.bundle.js",
	"opportunity_board.bundle.css",
];
// The side panel's files (part C), loaded the first time a panel opens: the planner kit's drawer and
// its stylesheet (since v1.673.0 the kit's CSS is a file of its own, no longer injected by its
// script), and the form kit's record editor and its stylesheet.
const OB_PANEL_ASSETS = [
	"planner_kit.bundle.js",
	"planner_kit.bundle.css",
	"guided_entry.bundle.js",
	"guided_entry.bundle.css",
];
const OB_API = "erpnext_enhancements.crm_enhancements.page.opportunity_board.opportunity_board.";
const OB_FORM_KIT = "erpnext_enhancements.form_kit.api.";
const OB_DOCTYPE = "Opportunity";
const OB_PAGE = "opportunity-board";
const OB_GUIDED_PAGE = "guided-entry";
const OB_CLOSE_STEP = "close";
const OB_REFRESH_DELAY_MS = 1500;
const OB_STALE_AFTER_MS = 60 * 1000;
const OB_CLICK_BLOCK_MS = 400;
const OB_PREFS = "erpnext_enhancements.api.planner_views_prefs.";
const OB_PREFS_PLANNER = "board";
const OB_LAST_VIEW_DELAY_MS = 1500;
const OB_FOCUS_ATTRS = [
	"data-to",
	"data-move",
	"data-open",
	"data-tab",
	"data-layout",
	"data-panel-close",
	"data-view",
	"data-toggle",
	"data-filter",
	"data-sort",
	"data-hold",
	"data-saved-view",
	"data-snooze-until",
	"data-snooze-reason",
	"data-view-name",
	"data-add",
	"data-pick",
	"data-select-mode",
	"data-bulk",
];

// The selection (P6.7), kept for this tab: sessionStorage can be refused (a private window), so every
// read and write is guarded, and a refusal only means a reload forgets it.
function ob_read_picks(key) {
	try {
		const list = JSON.parse(window.sessionStorage.getItem(key) || "[]");
		return Array.isArray(list) ? list : [];
	} catch (e) {
		return [];
	}
}

function ob_write_picks(key, list) {
	try {
		if (list && list.length) window.sessionStorage.setItem(key, JSON.stringify(list));
		else window.sessionStorage.removeItem(key);
	} catch (e) {
		// Not kept: a reload starts with nothing selected.
	}
}

class OpportunityBoard {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="ob-app"></div>').appendTo(page.main);
		this.model = null;
		this.target = null;
		this.loading = null;
		this.loaded_at = 0;
		this.stale = false;
		this.refresh_waiting = false;
		this.refresh_timer = null;
		this._route_timer = null;
		// True until the first show and again after every hide: the one show that scrolls to the top.
		this.arriving = true;
		// Bumped when a move starts. A get_board load remembers the value it started under, and its
		// payload is dropped if a move began since (or one is still saving): see kit.load_is_current.
		this.move_epoch = 0;
		this.saving = new Set();
		this.drag = null;
		this.click_blocked_until = 0;
		this.focused_panel = "";
		this.panel_return = "";
		this.media = window.matchMedia ? window.matchMedia("(max-width: 767px)") : null;
		// The filter bar and the table's sort (page state, see the head of this file), the board's
		// layout as last shown (the other views' routes do not carry it), and the saved views.
		this.filters = null;
		this.sort = null;
		this.layout = "stages";
		this.saved_views = [];
		this.current_view = "";
		this.prefs = null;
		this.prefs_ready = false;
		this.last_timer = null;
		this.last_sent = "";
		this.filters_open = false;
		this.naming_view = false;
		// What is typed into the panel's snooze form and the view-name box, kept across redraws.
		this.snooze_draft = null;
		this.view_name_draft = "";
		// Part C: the side panel (made the first time one opens), the selection and the bulk panel.
		this.side = null;
		this.side_ready = null;
		this.selecting = false;
		this.picks = null; // read from the tab's storage once the kit (and its key) is here
		this.bulk = null; // { action, handle, stage, fg, outcome, busy }
		this.bulk_flow = null;

		// v16's frappe.require runs its callback even when a file failed to load (assets.js: "resolve
		// even on error"), so the callback is not proof the bundles are here: both are checked, and a
		// board without them says so instead of waiting for good.
		this.ready = new Promise((resolve) => frappe.require(OB_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_opportunity_board ? window.sf_kit : null;
			this.kit = this.sf ? window.sf_opportunity_board : null;
			if (this.kit) {
				this.filters = this.kit.clean_filters(this.kit.DEFAULT_FILTERS);
				this.sort = Object.assign({}, this.kit.DEFAULT_SORT);
				this.picks = ob_read_picks(this.kit.PICKS_KEY);
				if (this.picks.length) this.selecting = true;
			}
		});

		page.set_secondary_action(__("Refresh"), () => this.refresh(), "refresh");
		this.bind();
		frappe.realtime.on("sales_pipeline_updated", () => this.schedule_refresh());
		if (this.media) {
			const relayout = () => {
				if (this.showing() && this.model) {
					this.end_drag();
					this.render();
				}
			};
			if (this.media.addEventListener) this.media.addEventListener("change", relayout);
			else if (this.media.addListener) this.media.addListener(relayout);
		}
	}

	showing() {
		return (frappe.get_route() || [])[0] === OB_PAGE;
	}

	is_phone() {
		if (this.media) return this.media.matches;
		return this.kit ? this.kit.is_phone(window.innerWidth) : false;
	}

	on_hide() {
		this.arriving = true;
		this.end_drag();
		clearTimeout(this.refresh_timer);
		this.refresh_timer = null;
		document.body.classList.remove("ob-panel-open");
		// The drawer lives on <body>: leaving the board closes it (an edit saves what it holds).
		if (this.side) this.side.hide("route");
		this.close_bulk();
	}

	// ------------------------------------------------------------------ routing

	route_changed() {
		// One handle_route per task: a replace followed by a push fires on_page_show twice.
		if (this._route_timer) return;
		this._route_timer = setTimeout(() => {
			this._route_timer = null;
			this.ready.then(() => this.handle_route());
		}, 0);
	}

	handle_route() {
		if (!this.kit) {
			this.show_broken();
			return;
		}
		const target = this.kit.parse_route(frappe.get_route());
		if (!target) return;
		// The board's layout is in its own route; the other views keep the one last shown.
		if (target.view === "board" && !target.bare) this.layout = target.layout;
		if (target.view === "board") target.layout = this.layout;
		const view_changed = !this.target || this.target.view !== target.view;
		this.target = target;
		if (view_changed) this.remember();
		if (!this.prefs) {
			this.prefs = this.load_prefs().then(() => {
				if (this.showing() && this.model) this.render();
			});
		}
		if (!this.model) {
			this.refresh();
			return;
		}
		this.render();
		if (this.stale || Date.now() - this.loaded_at > OB_STALE_AFTER_MS) this.refresh();
	}

	// Put a screen in the address: a new history entry, or (replace) in place of the current one.
	go(next, replace) {
		if (!this.showing() || !this.kit) return false;
		const route = this.kit.build_route(next);
		if (this.kit.route_key(this.kit.parse_route(route)) === this.kit.route_key(this.target)) return false;
		if (frappe.route_options) frappe.route_options = null;
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(route);
		// Read only by push_state, which has already run; left set, frappe clears it only after
		// 100 ms and every pending request, and the next screen would be a replace too.
		delete frappe.route_flags.replace_route;
		return true;
	}

	open_record(name) {
		if (name) frappe.set_route("Form", OB_DOCTYPE, name);
	}

	// The guided page's Close step for a deal dropped on Closed Won or Lost, when the side panel's
	// files did not load (part C's fallback; before part C, every closing drop). From a panel the
	// panel's own entry is replaced, so Back from the guided page lands on the board, not the panel.
	open_close_step(name, replace) {
		if (!name) return;
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route(OB_GUIDED_PAGE, OB_DOCTYPE, name, OB_CLOSE_STEP);
		delete frappe.route_flags.replace_route;
	}

	open_panel(name) {
		if (!name || !this.target) return;
		this.panel_return = name;
		if (this.go(Object.assign({}, this.target, { panel: name }))) {
			// Tag the entry this page pushed, so closing the panel steps back through it.
			try {
				window.history.replaceState(Object.assign({}, window.history.state, { ob_panel: name }), "");
			} catch (e) {
				// No tag: closing replaces the entry instead, which still leaves Back working.
			}
		}
	}

	close_panel() {
		if (!this.target || !this.target.panel) return;
		const state = window.history.state || {};
		if (state.ob_panel === this.target.panel) {
			window.history.back();
			return;
		}
		this.go(Object.assign({}, this.target, { panel: "" }), true);
	}

	// ------------------------------------------------------------------ the side panel (part C)

	// A side panel over the view: pushed, and tagged so closing it steps back through its entry.
	// `replace` takes the place of the move panel it was chosen from (the phone's tap to Closed Won),
	// still tagged when that entry was this page's.
	open_side(side, replace) {
		if (!side || !this.target || !this.kit) return;
		const state = window.history.state || {};
		const ours = !!(state.ob_panel || state.ob_side);
		if (!this.go(Object.assign({}, this.target, { panel: "", side }), !!replace)) return;
		if (replace && !ours) return;
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { ob_side: this.kit.side_key(side) }), "");
		} catch (e) {
			// No tag: closing replaces the entry instead, which still leaves Back working.
		}
	}

	// Back through the panel's entry when this page pushed it; otherwise the view in its place.
	close_side() {
		if (!this.target || !this.target.side) return;
		const state = window.history.state || {};
		if (state.ob_side && state.ob_side === this.kit.side_key(this.target.side)) {
			window.history.back();
			return;
		}
		this.go(Object.assign({}, this.target, { side: null }), true);
	}

	// The address follows the panel in place (its step; the deal quick add made), keeping the entry's
	// tag: the router's replaceState writes a null state, and an untagged panel would close by replace.
	replace_side(side) {
		const state = window.history.state || {};
		const tagged = state.ob_side && this.target && state.ob_side === this.kit.side_key(this.target.side);
		if (!this.go(Object.assign({}, this.target, { panel: "", side }), true)) return;
		if (!tagged) return;
		try {
			window.history.replaceState(Object.assign({}, window.history.state, { ob_side: this.kit.side_key(side) }), "");
		} catch (e) {
			// Untagged: closing replaces instead of stepping back.
		}
	}

	// The panel's files, the first time a panel opens: the planner kit's drawer and the form kit's
	// record editor. null when they did not arrive (frappe.require calls back even then).
	ensure_side() {
		if (!this.side_ready) {
			this.side_ready = new Promise((resolve) => frappe.require(OB_PANEL_ASSETS, resolve)).then(() => {
				const fk = window.sf_form_kit;
				if (!window.planner_kit || !fk || typeof fk.SidePanel !== "function") return null;
				this.side = new fk.SidePanel({ doctype: OB_DOCTYPE, push: this.$root[0], host: this.side_host() });
				return this.side;
			});
		}
		return this.side_ready;
	}

	side_host() {
		return {
			close: (how) => {
				if (how !== "replaced") this.close_side();
			},
			step: (key) => {
				const side = this.target && this.target.side;
				if (side && side.kind === this.kit.EDIT) this.replace_side(Object.assign({}, side, { step: key }));
			},
			committed: (info) => this.side_committed(info),
			created: (info) => {
				this.replace_side({ kind: this.kit.EDIT, name: info.name, step: "" });
				this.schedule_refresh();
			},
			open_full: (name, step, tab) => this.open_full(name, step, tab),
			changed: () => this.schedule_refresh(),
		};
	}

	// What the address's side panel asks the panel to show, or null.
	side_spec(side) {
		const kit = this.kit;
		if (side.kind === kit.EDIT) return { kind: "edit", name: side.name, step: side.step || null };
		if (side.kind === kit.CLOSING) {
			const stage = kit.closing_stage(this.model, side.stage);
			return stage ? { kind: "move", name: side.name, status: stage.label } : null;
		}
		if (side.kind === kit.NEW) {
			const stage = kit.stage_by_key(this.model, side.stage);
			return stage ? { kind: "new", stage: stage.label } : null;
		}
		return null;
	}

	// The drawer follows the address: open on what it names, closed when it names nothing.
	sync_side() {
		const kit = this.kit;
		const side = this.target && this.target.side;
		if (!side || side.kind !== kit.BULK) this.close_bulk();
		if ((!side || side.kind === kit.BULK) && this.side && this.side.is_open()) this.side.hide("route");
		if (!side) return;
		if (side.kind === kit.BULK) {
			this.open_bulk(side.action);
			return;
		}
		const spec = this.side_spec(side);
		if (!spec) return;
		const wanted = kit.route_key(this.target);
		this.ensure_side().then((panel) => {
			if (!this.showing() || !this.target || kit.route_key(this.target) !== wanted) return;
			if (!panel) this.side_fallback(side);
			else panel.show(spec);
		});
	}

	// The panel's files did not load: the deal opens the way it did before part C.
	side_fallback(side) {
		const kit = this.kit;
		if (side.kind === kit.CLOSING) {
			this.open_close_step(side.name, true);
		} else if (side.kind === kit.EDIT) {
			frappe.route_flags.replace_route = true;
			this.open_record(side.name);
			delete frappe.route_flags.replace_route;
		} else {
			const stage = kit.stage_by_key(this.model, side.stage);
			this.go(Object.assign({}, this.target, { side: null }), true);
			frappe.new_doc(OB_DOCTYPE, stage ? { status: stage.label } : {});
		}
	}

	// A closing move went through (the panel saved the ranks and the status, or Declare Lost ran): the
	// deal leaves the open board now, and the refresh that follows confirms it. Closed Won's project
	// prompt comes from the server, as it does for any save into Closed Won.
	side_committed(info) {
		const deal = this.model && this.model.by_name[info.name];
		const title = (deal && deal.title) || info.title || info.name;
		this.move_epoch += 1;
		if (this.model) this.model = this.kit.remove_deal(this.model, info.name);
		this.close_side();
		this.sf.toast(__("{0} moved to {1}", [title, __(info.status)]), { tone: "success" });
		this.schedule_refresh();
	}

	// The panel's "Open full page": the guided page in place of the panel's entry, so Back from it lands
	// on the board. A link into one tab of the form opens the form there.
	open_full(name, step, tab) {
		if (!name) return;
		if (tab && window.sf_guided && typeof window.sf_guided.to_form === "function") {
			window.sf_guided.to_form(OB_DOCTYPE, name, tab);
			return;
		}
		frappe.route_flags.replace_route = true;
		frappe.set_route(step ? [OB_GUIDED_PAGE, OB_DOCTYPE, name, step] : [OB_GUIDED_PAGE, OB_DOCTYPE, name]);
		delete frappe.route_flags.replace_route;
	}

	// ------------------------------------------------------------------ loading

	schedule_refresh() {
		if (!this.showing()) {
			this.stale = true;
			return;
		}
		clearTimeout(this.refresh_timer);
		this.refresh_timer = setTimeout(() => {
			this.refresh_timer = null;
			if (this.showing()) this.refresh();
			else this.stale = true;
		}, OB_REFRESH_DELAY_MS);
	}

	refresh() {
		if (!this.kit) return this.ready.then(() => (this.kit ? this.refresh() : this.show_broken()));
		// A refresh would redraw the card under a finger or a save; it runs when they finish.
		if (this.drag || this.saving.size) {
			this.refresh_waiting = true;
			return Promise.resolve();
		}
		if (this.loading) return this.loading;
		this.refresh_waiting = false;
		this.stale = false;
		if (!this.model) this.sf.state.render(this.$root[0], "loading");
		// A load that was already in flight when a card was dropped answers with the card's old stage
		// and old `modified`; drawn, it would put the card back in the wrong column and the save's new
		// `modified` would land on that stale copy (so Undo did nothing). Such a payload is dropped,
		// and a fresh load follows once nothing is saving or being dragged.
		const started = this.move_epoch;
		let dropped = false;
		this.loading = this.sf
			.call(OB_API + "get_board", {}, { quiet: true, method: "GET" })
			.then((data) => {
				if (!this.kit.load_is_current(started, this.move_epoch, this.saving.size + (this.drag ? 1 : 0))) {
					dropped = true;
					this.refresh_waiting = true;
					return;
				}
				this.model = this.kit.prepare(data);
				this.loaded_at = Date.now();
				// The first draw waits for the person's last-used filters (loaded alongside, and never
				// rejecting), so the board does not flash unfiltered first.
				return Promise.resolve(this.prefs).then(() => {
					if (this.showing()) this.render();
				});
			})
			.catch((err) => this.load_failed(err || {}))
			.finally(() => {
				this.loading = null;
				if (dropped && this.refresh_waiting && !this.saving.size && !this.drag) this.refresh();
			});
		return this.loading;
	}

	// The page's own files did not arrive (a deploy mid-session, a blocked request): plain text, since
	// the kit that would draw a panel is what is missing.
	show_broken() {
		const note = document.createElement("p");
		note.className = "ob-note";
		note.textContent = __("The Opportunity Board could not load its files. Reload the page to try again.");
		this.$root[0].replaceChildren(note);
	}

	load_failed(err) {
		if (this.model) {
			this.sf.toast(err.message || __("The board could not be refreshed."), {
				tone: "warning",
				action_label: __("Try again"),
				on_action: () => this.refresh(),
			});
			return;
		}
		if (err.status === 403 && !err.session_expired) {
			this.sf.state.render(this.$root[0], "no-access", { body: err.message });
			return;
		}
		this.sf.state.render(this.$root[0], "error", { body: err.message, on_action: () => this.refresh() });
	}

	// ------------------------------------------------------------------ drawing

	money(value) {
		return format_currency(value || 0, this.model.currency, 0);
	}

	month_name(key) {
		const m = /^(\d{4})-(\d{2})$/.exec(key || "");
		if (!m) return key || "";
		try {
			return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, 1)).toLocaleString(undefined, {
				month: "long",
				timeZone: "UTC",
			});
		} catch (e) {
			return key;
		}
	}

	render() {
		if (!this.model || !this.target || !this.kit) return;
		const phone = this.is_phone();
		// What the filter bar shows (kit.filtered): every view, the counts and the forecast strip
		// draw from it. The panel reads the whole model, so it opens on a deal the filters hide too.
		this.vm = this.kit.filtered(this.model, this.filters, frappe.session.user);
		const view = this.target.view;
		const stage = this.kit.current_stage(this.vm, this.target.stage);
		const panel_deal = this.target.panel ? this.model.by_name[this.target.panel] : null;
		// The selection holds only deals still on the board (one moved to Closed Won has left it).
		const picks = this.kit.clean_picks(this.model, this.picks || []);
		if (picks.length !== (this.picks || []).length) this.set_picks(picks);
		const parts = [this.nav_html(), this.filters_html(phone), this.head_html()];
		if (!this.kit.board_stages(this.model).length) {
			parts.push(this.sf.state.html("empty", { title: __("The Opportunity stages could not be read.") }));
		} else if (!this.model.deals.length) {
			parts.push(this.sf.state.html("empty", { title: __("No open opportunities.") }));
		} else if (view === "table") {
			parts.push(this.vm.deals.length ? this.table_html() : this.no_match_html());
		} else if (view === "timeline") {
			parts.push(this.vm.deals.length ? this.timeline_html() : this.no_match_html());
		} else if (view === "attention") {
			parts.push(this.attention_html());
		} else {
			parts.push(this.strip_html(phone, stage));
			if (phone) parts.push(this.tabs_html(stage));
			if (!this.vm.deals.length) parts.push(this.no_match_html());
			else if (this.layout === "streams") parts.push(this.rows_html(phone, stage));
			else parts.push(this.columns_html(phone, stage));
			parts.push(this.closebar_html());
		}
		if (this.selecting) parts.push(this.bulkbar_html());
		if (panel_deal) parts.push(this.panel_html(panel_deal));
		const focus = this.focus_marker();
		this.$root.html(parts.join(""));
		this.restore_focus(focus);
		document.body.classList.toggle("ob-panel-open", !!panel_deal);
		this.$root[0].classList.toggle("is-selecting", !!this.selecting);

		if (this.target.panel && !panel_deal) {
			// A move panel for a deal no longer on the board (moved, closed, or never readable).
			const target = Object.assign({}, this.target, { panel: "" });
			setTimeout(() => this.go(target, true), 0);
		}
		this.settle_focus(panel_deal);
		const side = this.target.side;
		// A bulk panel already open stays open with its answer, even once every deal it changed has
		// left the selection; the check is for arriving on its address (a reload, Forward).
		const bulk_open = !!side && side.kind === this.kit.BULK && !!this.bulk && this.bulk.action === side.action;
		if (side && !bulk_open && !this.kit.side_ok(this.model, side, this.picks)) {
			// A side panel for a deal that has left the board, a stage that is gone, or a bulk action
			// with nothing selected (a reload in another tab): the view in its place.
			const target = Object.assign({}, this.target, { side: null });
			setTimeout(() => this.go(target, true), 0);
			this.sync_side_closed();
		} else {
			this.sync_side();
		}
	}

	sync_side_closed() {
		this.close_bulk();
		if (this.side && this.side.is_open()) this.side.hide("route");
	}

	set_picks(list) {
		this.picks = list || [];
		ob_write_picks(this.kit.PICKS_KEY, this.picks);
	}

	// A redraw (a realtime refresh, a save settling) replaces every element, so the control that
	// had focus is found again by what it does, or a keyboard user would start over from the top.
	focus_marker() {
		const active = document.activeElement;
		if (!active || !this.$root[0].contains(active) || !active.getAttribute) return null;
		const attr = OB_FOCUS_ATTRS.find((name) => active.hasAttribute(name));
		return attr ? { attr, value: active.getAttribute(attr), in_panel: !!active.closest(".ob-panel") } : null;
	}

	restore_focus(marker) {
		if (!marker) return;
		const el = Array.from(this.$root[0].querySelectorAll(`[${marker.attr}]`)).find(
			(node) => node.getAttribute(marker.attr) === marker.value && !!node.closest(".ob-panel") === marker.in_panel
		);
		if (el) el.focus();
	}

	// Focus follows the panel: into it when it opens, back to the card's Move button when it closes.
	settle_focus(panel_deal) {
		if (panel_deal) {
			if (this.focused_panel === panel_deal.name) return;
			this.focused_panel = panel_deal.name;
			const first = this.$root[0].querySelector(".ob-stage-btn:not([disabled])");
			if (first) first.focus();
			return;
		}
		if (!this.focused_panel) return;
		const name = this.focused_panel;
		this.focused_panel = "";
		const back = Array.from(this.$root[0].querySelectorAll("[data-move]")).find(
			(el) => el.getAttribute("data-move") === name
		);
		if (back) back.focus();
	}

	// P6.3: the views, each a route. Needs attention carries its count.
	nav_html() {
		const esc = this.sf.esc;
		const count = this.kit.attention(this.vm).total;
		const views = [
			["board", __("Board")],
			["table", __("Table")],
			["timeline", __("Timeline")],
			["attention", count ? __("Needs attention ({0})", [count]) : __("Needs attention")],
		];
		const buttons = views
			.map(
				([key, label]) =>
					`<button type="button" class="ob-view" data-view="${esc(key)}"${
						this.target.view === key ? ' aria-current="page"' : ""
					}>${esc(label)}</button>`
			)
			.join("");
		return `<nav class="ob-views" aria-label="${esc(__("Views"))}">${buttons}</nav>`;
	}

	// P6.8: the quick filters and the saved views, shared by every view. On a phone it folds
	// behind one button (an inline disclosure, not a panel, so it takes no history entry).
	filters_html(phone) {
		const esc = this.sf.esc;
		const f = this.filters;
		const on = this.kit.active_filters(f);
		const toggle = (key, label) =>
			`<button type="button" class="ob-toggle" data-toggle="${esc(key)}" aria-pressed="${!!f[key]}">${esc(label)}</button>`;
		const option = (value, label, selected) =>
			`<option value="${esc(value)}"${selected ? " selected" : ""}>${esc(label)}</option>`;
		// A saved owner or stream no deal has now is still a choice, and selected: the filter applies.
		const owner_name =
			f.owner && frappe.user && typeof frappe.user.full_name === "function" ? frappe.user.full_name(f.owner) : "";
		const owners = [option("", __("Anyone"), !f.owner)]
			.concat(this.kit.owners(this.model, f.owner, owner_name).map((o) => option(o.id, o.name, f.owner === o.id)))
			.join("");
		const streams = [option("", __("Any"), !f.stream)]
			.concat(
				this.kit
					.stream_choices(this.model, f.stream)
					.map((s) =>
						option(s, s === this.kit.NO_STREAM_FILTER ? __("No value stream") : __(s), f.stream === s)
					)
			)
			.join("");
		const snoozed = this.vm.snoozed;
		const snooze_toggle =
			snoozed || f.show_snoozed
				? toggle("show_snoozed", __("Show snoozed ({0})", [snoozed]))
				: "";
		const clear = on
			? `<button type="button" class="sf-btn sf-btn--quiet" data-clear-filters>${esc(__("Clear filters"))}</button>`
			: "";
		const views = [option("", __("Saved views"), !this.current_view)]
			.concat(this.saved_views.map((v) => option(v.name, v.name, v.name === this.current_view)))
			.join("");
		const naming = this.naming_view
			? `<span class="ob-name-view">
					<label class="ob-field"><span>${esc(__("Name"))}</span>
						<input type="text" maxlength="60" data-view-name value="${esc(this.view_name_draft)}"></label>
					<button type="button" class="sf-btn sf-btn--secondary" data-view-save>${esc(__("Save"))}</button>
					<button type="button" class="sf-btn sf-btn--quiet" data-view-cancel>${esc(__("Cancel"))}</button>
				</span>`
			: `<button type="button" class="sf-btn sf-btn--quiet" data-view-start>${esc(__("Save this view"))}</button>`;
		const remove = this.current_view
			? `<button type="button" class="sf-btn sf-btn--quiet" data-view-delete>${esc(
					__("Delete \"{0}\"", [this.current_view])
			  )}</button>`
			: "";
		const open = !phone || this.filters_open;
		const disclosure = phone
			? `<button type="button" class="ob-filters-toggle" data-filters-toggle aria-expanded="${open}">${esc(
					on ? __("Filters ({0} on)", [on]) : __("Filters and views")
			  )}</button>`
			: "";
		return `
			<section class="ob-filters" aria-label="${esc(__("Filters"))}">
				${disclosure}
				<div class="ob-filters-body"${open ? "" : " hidden"}>
					<div class="ob-filter-row">
						${toggle("mine", __("My deals"))}
						<label class="ob-field"><span>${esc(__("Owner"))}</span><select data-filter="owner">${owners}</select></label>
						<label class="ob-field"><span>${esc(__("Value stream"))}</span><select data-filter="stream">${streams}</select></label>
						<label class="ob-field"><span>${esc(__("Over $"))}</span><input type="text" inputmode="decimal" data-filter="min_amount" value="${esc(
							f.min_amount
						)}" placeholder="${esc(__("Any amount"))}"></label>
						${toggle("closing_month", __("Closing this month"))}
						${snooze_toggle}${clear}
					</div>
					<div class="ob-filter-row">
						<label class="ob-field"><span class="ob-visually-hidden">${esc(__("Saved views"))}</span><select data-saved-view>${views}</select></label>
						${naming}${remove}
					</div>
					<div class="ob-filter-row">${this.card_fields_html()}</div>
				</div>
			</section>`;
	}

	// v1.684.0: which parts a card shows beside its title, saved with the view like the filters.
	card_fields_html() {
		const esc = this.sf.esc;
		const shown = new Set(this.card_parts());
		const names = {
			account: __("Account"),
			streams: __("Value streams"),
			owner: __("Owner"),
			amount: __("Amount"),
			probability: __("Probability"),
			close: __("Close date"),
			age: __("Days in stage"),
			meter: __("Completeness"),
		};
		const boxes = this.kit.CARD_PARTS.map(
			(part) =>
				`<label class="ob-card-part"><input type="checkbox" data-card-part="${esc(part)}"${
					shown.has(part) ? " checked" : ""
				}><span>${esc(names[part] || part)}</span></label>`
		).join("");
		return `<details class="ob-card-fields"${this.card_fields_open ? " open" : ""}><summary data-card-fields>${esc(
			__("Card fields")
		)}</summary>${boxes}</details>`;
	}

	// The count and value the filters show, beside the board's layout switch.
	head_html() {
		const esc = this.sf.esc;
		const shown = this.kit.totals(this.vm.deals);
		const all = this.model.deals.length;
		const layout = this.layout;
		const seg = (key, label) =>
			`<button type="button" class="ob-seg" data-layout="${esc(key)}" aria-pressed="${layout === key}">${esc(label)}</button>`;
		const count =
			shown.count === all
				? shown.count === 1
					? __("1 open deal")
					: __("{0} open deals", [shown.count])
				: __("{0} of {1} open deals", [shown.count, all]);
		const hidden = this.vm.hidden ? ` &middot; ${esc(__("{0} snoozed, hidden", [this.vm.hidden]))}` : "";
		const switcher =
			this.target.view === "board"
				? `<div class="ob-layout" role="group" aria-label="${esc(__("Show the board"))}">
					${seg("stages", __("By stage"))}${seg("streams", __("By value stream"))}
				</div>`
				: "";
		const note = this.model.truncated
			? `<p class="ob-note">${esc(__("Showing the {0} most recently changed open deals.", [this.model.deals.length]))}</p>`
			: "";
		// P6.7: selection for the bulk actions (page state, like the filters: no history entry).
		const select = `<button type="button" class="ob-seg ob-select-mode" data-select-mode aria-pressed="${!!this.selecting}">${esc(
			this.selecting ? __("Done selecting") : __("Select")
		)}</button>`;
		return `
			<div class="ob-head">
				<p class="ob-summary">${esc(count)} &middot; <strong>${esc(this.money(shown.total))}</strong>${hidden}</p>
				<div class="ob-head-tools">${switcher}${select}</div>
			</div>${note}`;
	}

	no_match_html() {
		return this.sf.state.html("empty", {
			title: __("No deals match these filters."),
			action_label: this.kit.active_filters(this.filters) ? __("Clear filters") : "",
		});
	}

	tile_html(label, figures, extra_class) {
		const esc = this.sf.esc;
		const count = figures.count === 1 ? __("1 deal") : __("{0} deals", [figures.count]);
		const weighted =
			figures.weighted === null
				? __("Not forecast")
				: __("{0} weighted", [this.money(figures.weighted)]);
		return `
			<div class="ob-tile ${extra_class || ""}">
				<div class="ob-tile-label">${esc(label)}</div>
				<div class="ob-tile-value">${esc(this.money(figures.total))}</div>
				<div class="ob-tile-meta">${esc(count)} &middot; ${esc(weighted)}</div>
			</div>`;
	}

	// P6.5: per stage, then this month and next by expected close date. On a phone, only the
	// overall figure, the stage on screen, and the two months.
	strip_html(phone, stage_key) {
		const f = this.kit.forecast(this.vm);
		const tiles = [this.tile_html(__("All open"), f.overall, "ob-tile--all")];
		f.stages.forEach((s) => {
			if (phone && s.stage.key !== stage_key) return;
			tiles.push(this.tile_html(s.stage.label, s, s.stage.kind === "parked" ? "ob-tile--parked" : ""));
		});
		f.months.forEach((month, index) => {
			const when = index === 0 ? __("Closing this month") : __("Closing next month");
			tiles.push(this.tile_html(`${when} (${this.month_name(month.key)})`, month, "ob-tile--month"));
		});
		return `<section class="ob-strip" aria-label="${this.sf.esc(__("Forecast"))}">${tiles.join("")}</section>`;
	}

	// P6.9: one stage at a time on a phone; each tab is a history entry.
	tabs_html(current) {
		const esc = this.sf.esc;
		const tabs = this.kit.columns(this.vm).map((col) => {
			const on = col.stage.key === current;
			return `<button type="button" class="ob-tab" role="tab" data-tab="${esc(col.stage.key)}" aria-selected="${on}">
				<span class="ob-tab-label">${esc(col.stage.label)}</span>
				<span class="ob-tab-count">${col.count}</span>
			</button>`;
		});
		return `<div class="ob-tabs" role="tablist" aria-label="${esc(__("Stages"))}">${tabs.join("")}</div>`;
	}

	// P6.8: On Hold folds into a count by default (36 of 74 open deals sat there on 2026-10-14). A
	// folded column is still a drop target, and its count and value are still in every total.
	folded(stage) {
		return !!stage && stage.kind === "parked" && !this.filters.hold_open;
	}

	col_head_html(col) {
		const esc = this.sf.esc;
		const fold =
			col.stage.kind === "parked" && !this.folded(col.stage) && col.count
				? `<button type="button" class="sf-btn sf-btn--quiet ob-fold-btn" data-hold="close">${esc(__("Fold"))}</button>`
				: "";
		// P6.7: quick add, a deal created already in this stage (an open stage or On Hold).
		const add = this.kit.can_add(this.model, col.stage.key)
			? `<button type="button" class="ob-add" data-add="${esc(col.stage.key)}" aria-label="${esc(
					__("New deal in {0}", [col.stage.label])
			  )}" title="${esc(__("New deal in {0}", [col.stage.label]))}">+</button>`
			: "";
		return `
			<header class="ob-col-head">
				<div class="ob-col-titlebar"><h3 class="ob-col-title">${esc(col.stage.label)}</h3>${add}</div>
				<span class="ob-col-meta">${esc(col.count === 1 ? __("1 deal") : __("{0} deals", [col.count]))} &middot; ${esc(
					this.money(col.total)
				)}</span>${fold}
			</header>`;
	}

	fold_html(count) {
		const esc = this.sf.esc;
		if (!count) return `<p class="ob-empty">${esc(__("No deals"))}</p>`;
		return `<div class="ob-fold">
			<p>${esc(count === 1 ? __("1 deal on hold, folded away") : __("{0} deals on hold, folded away", [count]))}</p>
			<button type="button" class="sf-btn sf-btn--secondary" data-hold="open">${esc(__("Show them"))}</button>
		</div>`;
	}

	cards_html(items) {
		if (!items.length) return `<p class="ob-empty">${this.sf.esc(__("No deals"))}</p>`;
		return items.map((item) => this.card_html(item.deal, item.also)).join("");
	}

	// The grid's columns: a folded On Hold takes a narrow one. A custom property, so the phone's
	// one-column rule in the stylesheet still wins.
	grid_style(cols) {
		const template = cols
			.map((col) => (this.folded(col.stage) ? "minmax(150px, 190px)" : "minmax(240px, 1fr)"))
			.join(" ");
		return `--ob-template: ${template}`;
	}

	columns_html(phone, current) {
		const cols = this.kit.columns(this.vm).filter((col) => !phone || col.stage.key === current);
		const html = cols
			.map(
				(col) => `
				<section class="ob-col ob-col--${col.stage.kind}${this.folded(col.stage) ? " is-folded" : ""}" data-drop="${this.sf.esc(
					col.stage.key
				)}">
					${this.col_head_html(col)}
					<div class="ob-cards">${
						this.folded(col.stage)
							? this.fold_html(col.count)
							: this.cards_html(col.deals.map((deal) => ({ deal, also: [] })))
					}</div>
				</section>`
			)
			.join("");
		return `<div class="ob-scroll"><div class="ob-grid" style="${this.grid_style(cols)}">${html}</div></div>`;
	}

	// `figures` is what the head counts: the whole row on a wide screen, the one stage on a phone.
	row_head_html(row, figures) {
		const esc = this.sf.esc;
		const label = row.stream || __("No value stream");
		const f = figures || row;
		return `
			<header class="ob-row-head">
				${this.sf.chip(label, row.pillar)}
				<span class="ob-row-meta">${esc(f.count === 1 ? __("1 deal") : __("{0} deals", [f.count]))} &middot; ${esc(
					this.money(f.total)
				)}</span>
			</header>`;
	}

	// P6.2: a row per value stream. A deal with several streams is in each of its rows, marked
	// "Also in ..."; each row's total counts it in full, and the column heads (the plain board's
	// totals) count it once.
	rows_html(phone, current) {
		const rows = this.kit.stream_rows(this.vm);
		if (phone) {
			const col = this.kit.columns(this.vm).find((c) => c.stage.key === current);
			if (col && this.folded(col.stage)) {
				return `<div class="ob-phone-rows">${this.col_head_html(col)}${this.fold_html(col.count)}</div>`;
			}
			const sections = rows
				.map((row) => {
					const cell = this.kit.row_cells(this.vm, row).find((c) => c.stage.key === current);
					if (!cell || !cell.items.length) return "";
					return `<section class="ob-row">${this.row_head_html(row, cell)}
						<div class="ob-cards">${this.cards_html(cell.items)}</div></section>`;
				})
				.join("");
			return `<div class="ob-phone-rows">${col ? this.col_head_html(col) : ""}${
				sections || `<p class="ob-empty">${this.sf.esc(__("No deals"))}</p>`
			}</div>`;
		}
		const cols = this.kit.columns(this.vm);
		const heads = cols
			.map(
				(col) =>
					`<div class="ob-col ob-col--head ob-col--${col.stage.kind}">${this.col_head_html(col)}${
						this.folded(col.stage) ? this.fold_html(col.count) : ""
					}</div>`
			)
			.join("");
		const body = rows
			.map((row) => {
				const cells = this.kit
					.row_cells(this.vm, row)
					.map((cell) => {
						const inner = this.folded(cell.stage)
							? `<p class="ob-empty">${this.sf.esc(
									cell.count === 1 ? __("1 deal, folded") : __("{0} deals, folded", [cell.count])
							  )}</p>`
							: this.cards_html(cell.items);
						return `<div class="ob-cell ob-col--${cell.stage.kind}" data-drop="${this.sf.esc(cell.stage.key)}">${inner}</div>`;
					})
					.join("");
				return `<section class="ob-row">${this.row_head_html(row)}<div class="ob-grid" style="${this.grid_style(
					cols
				)}">${cells}</div></section>`;
			})
			.join("");
		return `<div class="ob-scroll"><div class="ob-grid ob-grid--heads" style="${this.grid_style(
			cols
		)}">${heads}</div>${body}</div>`;
	}

	age_html(deal) {
		const esc = this.sf.esc;
		if (deal.days === null || deal.days === undefined) return "";
		if (deal.kind === "parked") {
			const label = deal.days === 1 ? __("1 day on hold") : __("{0} days on hold", [deal.days]);
			return `<span class="ob-age">${esc(label)}</span>`;
		}
		const days = deal.days === 1 ? __("1 day in stage") : __("{0} days in stage", [deal.days]);
		if (deal.stale === 2) return this.sf.chip(__("{0}, stalled", [days]), "danger");
		if (deal.stale === 1) return this.sf.chip(__("{0}, slowing", [days]), "warn");
		return `<span class="ob-age">${esc(days)}</span>`;
	}

	// P4.6, the completeness meter: how much of what this stage asks for is filled in.
	meter_html(deal) {
		const esc = this.sf.esc;
		const m = this.kit.meter(deal.completeness);
		if (!m) return "";
		const said = __("{0} of {1} filled in for this stage", [m.done, m.total]);
		const title = m.missing.length ? `${said}. ${__("Missing: {0}", [m.missing.join(", ")])}` : said;
		return `
			<span class="ob-meter" title="${esc(title)}">
				<span class="ob-meter-track" role="img" aria-label="${esc(said)}"><span class="ob-meter-bar" style="width: ${m.percent}%"></span></span>
				<span class="ob-meter-text">${m.done}/${m.total}</span>
			</span>`;
	}

	card_html(deal, also) {
		const esc = this.sf.esc;
		const classes = ["ob-card"];
		if (deal.stale) classes.push(`ob-card--stale-${deal.stale}`);
		if (this.saving.has(deal.name)) classes.push("is-saving");
		if (deal.snooze === "snoozed") classes.push("is-snoozed");
		if (this.selecting && (this.picks || []).includes(deal.name)) classes.push("is-picked");
		// A drop on Closed Won or Lost waiting on its required fields in the side panel (P6.6).
		const side = this.target && this.target.side;
		const gated = !!side && side.kind === this.kit.CLOSING && side.name === deal.name;
		if (gated) classes.push("is-gated");
		const gated_to = gated ? this.kit.closing_stage(this.model, side.stage) : null;
		const band = this.kit
			.band(deal)
			.map((pillar) => `<span class="ob-band-seg ob-band-seg--${esc(pillar)}"></span>`)
			.join("");
		const chips = this.kit
			.streams_of(deal)
			.map((s) => this.sf.chip(s.stream, s.pillar))
			.join(" ");
		const also_html =
			also && also.length ? `<p class="ob-also">${esc(__("Also in {0}", [also.join(", ")]))}</p>` : "";
		const amount =
			deal.amount !== null
				? `<span class="ob-amount">${esc(this.money(deal.amount))}</span>`
				: `<span class="ob-amount ob-amount--none">${esc(__("No amount"))}</span>`;
		const close = deal.expected_closing
			? __("Closes {0}", [frappe.datetime.str_to_user(deal.expected_closing)])
			: __("No close date");
		// v1.684.0: the parts this person shows on a card (the card as it always was, by default).
		const parts = new Set(this.card_parts());
		const account =
			parts.has("account") && deal.account && deal.account !== deal.title
				? `<span class="ob-card-account">${esc(deal.account)}</span>`
				: "";
		const owner = parts.has("owner") && deal.owner_name ? `<span class="ob-card-owner">${esc(deal.owner_name)}</span>` : "";
		const facts = [
			parts.has("amount") ? amount : "",
			parts.has("probability") ? `<span class="ob-card-probability">${esc(`${this.kit.probability_of(deal)}%`)}</span>` : "",
			parts.has("close") ? `<span class="ob-close">${esc(close)}</span>` : "",
		].join("");
		const foot = (parts.has("age") ? this.age_html(deal) : "") + (parts.has("meter") ? this.meter_html(deal) : "");
		return `
			<article class="${classes.join(" ")}" data-name="${esc(deal.name)}">
				<div class="ob-band" aria-hidden="true">${band}</div>
				<div class="ob-card-body">
					<div class="ob-card-top">${this.pick_html(deal)}
					<button type="button" class="ob-card-open" data-open="${esc(deal.name)}">
						<span class="ob-card-title">${esc(deal.title)}</span>${account}${owner}
					</button></div>
					${chips && parts.has("streams") ? `<div class="ob-chips">${chips}</div>` : ""}${also_html}${this.snooze_mark_html(deal)}${
						gated_to ? `<div class="ob-chips">${this.sf.chip(__("Moving to {0}…", [gated_to.label]), "warn")}</div>` : ""
					}
					${facts ? `<div class="ob-facts">${facts}</div>` : ""}
					<div class="ob-foot">
						${foot}
						<button type="button" class="ob-move" data-move="${esc(deal.name)}" aria-label="${esc(
							__("Move or snooze {0}", [deal.title])
						)}">${esc(__("Move"))}</button>
					</div>
				</div>
			</article>`;
	}

	date_text(value) {
		return value ? frappe.datetime.str_to_user(value) : "";
	}

	// P6.8: a snoozed deal (shown only with "Show snoozed") and one whose snooze has run out.
	snooze_mark_html(deal) {
		if (deal.snooze === "returning") {
			return `<div class="ob-chips">${this.sf.chip(__("Back from snooze"), "warn")}</div>`;
		}
		if (deal.snooze === "snoozed") {
			return `<div class="ob-chips">${this.sf.chip(
				__("Snoozed until {0}", [this.date_text(deal.snoozed_until)]),
				"neutral"
			)}</div>`;
		}
		return "";
	}

	// ------------------------------------------------------------------ the table (P6.3)

	table_html() {
		const esc = this.sf.esc;
		const rows = this.kit.table_rows(this.vm, this.sort);
		const columns = [
			["title", __("Opportunity")],
			["stage", __("Stage")],
			["amount", __("Amount")],
			["probability", __("Probability")],
			["weighted", __("Weighted")],
			["closing", __("Expected close")],
			["days", __("Days in stage")],
			["owner", __("Owner")],
			["complete", __("Complete")],
		];
		const heads = columns
			.map(([key, label]) => {
				const on = this.sort.sort === key;
				const sorted = on ? (this.sort.desc ? "descending" : "ascending") : "none";
				const arrow = on ? (this.sort.desc ? " ↓" : " ↑") : "";
				return `<th scope="col" aria-sort="${sorted}"><button type="button" class="ob-sort" data-sort="${esc(key)}">${esc(
					label
				)}${arrow}</button></th>`;
			})
			.join("");
		const body = rows
			.map((deal) => {
				const stage = this.kit.stage_by_key(this.model, deal.stage);
				const streams = this.kit
					.streams_of(deal)
					.map((s) => this.sf.chip(s.stream, s.pillar))
					.join(" ");
				const overdue = this.kit.attention_reasons(deal, this.model.today).includes("overdue");
				const weighted =
					deal.kind === "parked" ? __("Not forecast") : deal.amount === null ? "" : this.money(this.kit.weighted_of(deal));
				const age = deal.days === null || deal.days === undefined ? "" : this.age_html(deal);
				return `<tr data-name="${esc(deal.name)}"${this.selecting && (this.picks || []).includes(deal.name) ? ' class="is-picked"' : ""}>
					<td data-label="${esc(__("Opportunity"))}">
						${this.pick_html(deal)}<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
						${deal.account && deal.account !== deal.title ? `<span class="ob-card-account">${esc(deal.account)}</span>` : ""}
						${streams ? `<div class="ob-chips">${streams}</div>` : ""}${this.snooze_mark_html(deal)}
					</td>
					<td data-label="${esc(__("Stage"))}">${esc(stage ? stage.label : deal.status)}</td>
					<td data-label="${esc(__("Amount"))}" class="ob-num">${
						deal.amount === null
							? `<span class="ob-amount--none">${esc(__("No amount"))}</span>`
							: esc(this.money(deal.amount))
					}</td>
					<td data-label="${esc(__("Probability"))}" class="ob-num">${esc(`${deal.probability}%`)}</td>
					<td data-label="${esc(__("Weighted"))}" class="ob-num">${esc(weighted)}</td>
					<td data-label="${esc(__("Expected close"))}">${
						deal.expected_closing
							? `${esc(this.date_text(deal.expected_closing))}${overdue ? ` ${this.sf.chip(__("Past"), "danger")}` : ""}`
							: `<span class="ob-amount--none">${esc(__("None"))}</span>`
					}</td>
					<td data-label="${esc(__("Days in stage"))}">${age}</td>
					<td data-label="${esc(__("Owner"))}">${esc(deal.owner_name)}</td>
					<td data-label="${esc(__("Complete"))}">${this.meter_html(deal)}</td>
					<td class="ob-row-actions"><button type="button" class="ob-move" data-move="${esc(deal.name)}" aria-label="${esc(
						__("Move or snooze {0}", [deal.title])
					)}">${esc(__("Move"))}</button></td>
				</tr>`;
			})
			.join("");
		return `<div class="ob-table-wrap"><table class="ob-table">
			<caption class="ob-visually-hidden">${esc(__("Open opportunities"))}</caption>
			<thead><tr>${heads}<th scope="col"><span class="ob-visually-hidden">${esc(__("Actions"))}</span></th></tr></thead>
			<tbody>${body}</tbody></table></div>`;
	}

	// ------------------------------------------------------------------ the timeline (P6.3)

	month_label(key) {
		const m = /^(\d{4})-(\d{2})$/.exec(key || "");
		return m ? `${this.month_name(key)} ${m[1]}` : __("No expected close date");
	}

	timeline_html() {
		const esc = this.sf.esc;
		const groups = this.kit.timeline(this.vm);
		return `<div class="ob-timeline">${groups
			.map((group) => {
				const mark = group.past
					? this.sf.chip(__("Past"), "danger")
					: group.current
					? this.sf.chip(__("This month"), "good")
					: "";
				const weighted = __("{0} weighted", [this.money(group.weighted)]);
				const items = group.deals
					.map(
						(deal) => `<li class="ob-tl-item${this.selecting && (this.picks || []).includes(deal.name) ? " is-picked" : ""}" data-name="${esc(deal.name)}">
							<span class="ob-tl-date">${esc(deal.expected_closing ? this.date_text(deal.expected_closing) : "")}</span>
							<span class="ob-tl-main">
								${this.pick_html(deal)}<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
								<span class="ob-card-account">${esc(
									[this.kit.stage_by_key(this.model, deal.stage)?.label || deal.status, deal.account]
										.filter(Boolean)
										.join(" · ")
								)}</span>
							</span>
							<span class="ob-tl-amount">${
								deal.amount === null
									? `<span class="ob-amount--none">${esc(__("No amount"))}</span>`
									: esc(this.money(deal.amount))
							}</span>
							<button type="button" class="ob-move" data-move="${esc(deal.name)}" aria-label="${esc(
								__("Move or snooze {0}", [deal.title])
							)}">${esc(__("Move"))}</button>
						</li>`
					)
					.join("");
				return `<section class="ob-month${group.past ? " is-past" : ""}">
					<header class="ob-month-head">
						<h3 class="ob-col-title">${esc(this.month_label(group.key))}</h3>
						<span class="ob-col-meta">${esc(
							group.count === 1 ? __("1 deal") : __("{0} deals", [group.count])
						)} &middot; ${esc(this.money(group.total))} &middot; ${esc(weighted)}</span>
						${mark}
					</header>
					<ol class="ob-tl-list">${items}</ol>
				</section>`;
			})
			.join("")}</div>`;
	}

	// ------------------------------------------------------------------ needs attention (P6.4)

	reason_html(deal, reason) {
		if (reason === "stale") {
			return this.sf.chip(__("{0} days in stage", [deal.days]), deal.stale === 2 ? "danger" : "warn");
		}
		if (reason === "overdue") {
			return this.sf.chip(__("Past close date {0}", [this.date_text(deal.expected_closing)]), "danger");
		}
		if (reason === "no_amount") return this.sf.chip(__("No amount"), "neutral");
		const m = this.kit.meter(deal.completeness);
		const missing = m && m.missing.length ? m.missing.join(", ") : "";
		return this.sf.chip(missing ? __("Missing: {0}", [missing]) : __("Missing what this stage needs"), "neutral");
	}

	attention_html() {
		const esc = this.sf.esc;
		const list = this.kit.attention(this.vm);
		const labels = {
			stale: __("Stale"),
			overdue: __("Past close date"),
			no_amount: __("No amount"),
			incomplete: __("Missing what the stage needs"),
		};
		const counts = this.kit.REASONS.map(
			(reason) => `<li><strong>${list.counts[reason]}</strong> ${esc(labels[reason])}</li>`
		).join("");
		if (!list.total) {
			return this.sf.state.html("empty", {
				title: this.kit.active_filters(this.filters)
					? __("Nothing among these deals needs attention.")
					: __("Nothing needs attention."),
			});
		}
		// Each deal opens in the side panel (form kit P6.6, on P4.4's panel), as P6.4 asked.
		const items = list.items
			.map(
				({ deal, reasons }) => `<li class="ob-att-item${
					this.selecting && (this.picks || []).includes(deal.name) ? " is-picked" : ""
				}" data-name="${esc(deal.name)}">
					<div class="ob-att-main">
						${this.pick_html(deal)}<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
						<span class="ob-card-account">${esc(
							[this.kit.stage_by_key(this.model, deal.stage)?.label || deal.status, deal.account, deal.owner_name]
								.filter(Boolean)
								.join(" · ")
						)}</span>
						<div class="ob-chips">${reasons.map((reason) => this.reason_html(deal, reason)).join(" ")}</div>
					</div>
					<span class="ob-tl-amount">${
						deal.amount === null ? `<span class="ob-amount--none">${esc(__("No amount"))}</span>` : esc(this.money(deal.amount))
					}</span>
					<button type="button" class="ob-move" data-move="${esc(deal.name)}" aria-label="${esc(
						__("Move or snooze {0}", [deal.title])
					)}">${esc(__("Snooze"))}</button>
				</li>`
			)
			.join("");
		return `<section class="ob-attention" aria-label="${esc(__("Needs attention"))}">
			<ul class="ob-att-counts">${counts}</ul>
			<ol class="ob-att-list">${items}</ol>
		</section>`;
	}

	// The closing stages, shown along the bottom only while a card is being dragged.
	closebar_html() {
		const esc = this.sf.esc;
		const zones = this.kit
			.closing_stages(this.model)
			.map(
				(stage) =>
					`<div class="ob-drop-close" data-drop="${esc(stage.key)}">${esc(__("Drop here: {0}", [stage.label]))}</div>`
			)
			.join("");
		return zones ? `<div class="ob-closebar" aria-hidden="true">${zones}</div>` : "";
	}

	panel_html(deal) {
		const esc = this.sf.esc;
		const now = this.kit.stage_by_key(this.model, deal.stage);
		const choices = this.kit
			.move_choices(this.model, deal)
			.map((choice) => {
				const note = choice.current
					? __("Now here")
					: choice.action === "close"
					? choice.stage.gate === "lost"
						? __("Asks why it was lost")
						: __("Asks for the ranks")
					: "";
				return `<button type="button" class="ob-stage-btn ob-stage-btn--${esc(choice.stage.kind)}" data-to="${esc(
					choice.stage.key
				)}"${choice.current ? ' aria-current="true" disabled' : ""}>
					<span>${esc(choice.stage.label)}</span>${note ? `<span class="ob-stage-note">${esc(note)}</span>` : ""}
				</button>`;
			})
			.join("");
		return `
			<div class="ob-panel-backdrop" data-panel-close></div>
			<section class="ob-panel" role="dialog" aria-modal="true" aria-labelledby="ob-panel-title">
				<header class="ob-panel-head">
					<h2 class="ob-panel-title" id="ob-panel-title">${esc(deal.title)}</h2>
					<button type="button" class="sf-btn sf-btn--quiet" data-panel-close>${esc(__("Close"))}</button>
				</header>
				<p class="ob-panel-intro">${esc(
					__("In {0}. Choose the stage it moves to.", [now ? now.label : deal.status])
				)}</p>
				<div class="ob-panel-stages">${choices}</div>
				${this.snooze_form_html(deal)}
				<div class="ob-panel-foot">
					<button type="button" class="sf-btn sf-btn--secondary" data-open="${esc(deal.name)}">${esc(
						__("Open opportunity")
					)}</button>
				</div>
			</section>`;
	}

	// P6.8: "Snooze until a date", with an optional reason; or, for a snoozed or returning deal,
	// what it was snoozed for and a way to wake it. What is typed is kept across redraws.
	snooze_form_html(deal) {
		const esc = this.sf.esc;
		if (!this.model.can_snooze) return "";
		const draft = this.snooze_draft && this.snooze_draft.name === deal.name ? this.snooze_draft : null;
		const reason = deal.snooze_reason ? `: ${deal.snooze_reason}` : "";
		let state = "";
		if (deal.snooze === "snoozed") {
			state = `<p class="ob-snooze-state">${esc(
				__("Snoozed until {0}{1}", [this.date_text(deal.snoozed_until), reason])
			)}</p><button type="button" class="sf-btn sf-btn--secondary" data-wake>${esc(__("Wake it now"))}</button>`;
		} else if (deal.snooze === "returning") {
			state = `<p class="ob-snooze-state">${esc(
				__("Back from a snooze that ran to {0}{1}", [this.date_text(deal.snoozed_until), reason])
			)}</p><button type="button" class="sf-btn sf-btn--secondary" data-wake>${esc(__("Clear the mark"))}</button>`;
		}
		return `<section class="ob-snooze" aria-labelledby="ob-snooze-title">
			<h3 class="ob-snooze-title" id="ob-snooze-title">${esc(__("Snooze"))}</h3>
			${state}
			<div class="ob-snooze-form">
				<label class="ob-field ob-field--stack"><span>${esc(__("Hide it until"))}</span>
					<input type="date" data-snooze-until min="${esc(this.kit.snooze_min(this.model.today))}" max="${esc(
					this.kit.snooze_max(this.model.today)
				)}" value="${esc(
						draft ? draft.until : ""
					)}"></label>
				<label class="ob-field ob-field--stack"><span>${esc(__("Why (optional)"))}</span>
					<textarea data-snooze-reason maxlength="500" rows="2">${esc(draft ? draft.reason : "")}</textarea></label>
				<button type="button" class="sf-btn sf-btn--primary" data-snooze-save>${esc(__("Snooze"))}</button>
			</div>
		</section>`;
	}

	// ------------------------------------------------------------------ clicks and keys

	bind() {
		const root = this.$root[0];
		root.addEventListener("click", (e) => this.on_click(e));
		root.addEventListener("pointerdown", (e) => this.on_down(e));
		document.addEventListener("pointermove", (e) => this.on_move(e));
		document.addEventListener("pointerup", (e) => this.on_up(e));
		document.addEventListener("pointercancel", (e) => this.on_cancel(e));
		// Once a card has lifted, the finger moving it must not scroll the page. Non-passive, or
		// the browser ignores preventDefault here.
		document.addEventListener(
			"touchmove",
			(e) => {
				if (this.drag && this.drag.active) e.preventDefault();
			},
			{ passive: false }
		);
		root.addEventListener("contextmenu", (e) => {
			if (this.drag) e.preventDefault();
		});
		// The filter bar's selects and amount apply on change (a blur or Enter), never per key, so a
		// redraw never lands while someone is typing. The snooze form and the view name only keep
		// their drafts as they are typed.
		root.addEventListener("change", (e) => this.on_change(e));
		root.addEventListener("input", (e) => this.on_input(e));
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" || !e.target || !e.target.matches) return;
			if (e.target.matches("[data-view-name]")) {
				e.preventDefault();
				this.save_named_view();
			}
		});
		document.addEventListener("keydown", (e) => {
			if (e.key !== "Escape" || !this.showing()) return;
			if (this.drag) {
				e.preventDefault();
				this.end_drag();
			} else if (this.target && this.target.side) {
				// The side panel closes on Esc, unless the key belongs to something on top of it: a
				// dialog (Set as Lost), an open autocomplete or date picker, or an (i) the panel closed.
				if (e.defaultPrevented || (window.cur_dialog && window.cur_dialog.display)) return;
				const t = e.target;
				if (t && t.getAttribute && t.getAttribute("aria-expanded") === "true") return;
				const picker = document.querySelector(".datepicker.active");
				if (picker && picker.offsetParent !== null) return;
				e.preventDefault();
				if (this.target.side.kind !== this.kit.BULK && this.side && this.side.is_open()) this.side.request_close();
				else this.close_side();
			} else if (this.target && this.target.panel) {
				e.preventDefault();
				this.close_panel();
			}
		});
	}

	on_click(e) {
		const el = e.target;
		if (!el || !el.closest || !this.target || !this.kit) return;
		if (Date.now() < this.click_blocked_until) {
			e.preventDefault();
			return;
		}
		let hit = el.closest("[data-panel-close]");
		if (hit) return this.close_panel();
		hit = el.closest("[data-to]");
		if (hit) return this.choose_stage(hit.getAttribute("data-to"));
		hit = el.closest("[data-snooze-save]");
		if (hit) return this.save_snooze();
		hit = el.closest("[data-wake]");
		if (hit) return this.wake(this.target.panel);
		hit = el.closest("[data-layout]");
		if (hit) {
			const change = this.kit.layout_change(this.target, hit.getAttribute("data-layout"));
			this.layout = change.next.layout;
			this.remember();
			// When the address already says this layout there is no route change (go() returns false)
			// and so no handle_route: draw here, or the click would do nothing.
			if (!change.navigate || !this.go(change.next)) {
				if (this.target.view === "board") this.target.layout = this.layout;
				this.render();
			}
			return;
		}
		hit = el.closest("[data-tab]");
		if (hit) return this.go({ view: "board", layout: this.layout, stage: hit.getAttribute("data-tab"), panel: "" });
		hit = el.closest("[data-view]");
		if (hit) return this.show_view(hit.getAttribute("data-view"));
		hit = el.closest("[data-toggle]");
		if (hit) {
			const key = hit.getAttribute("data-toggle");
			return this.set_filters({ [key]: !this.filters[key] });
		}
		hit = el.closest("[data-hold]");
		if (hit) return this.set_filters({ hold_open: hit.getAttribute("data-hold") === "open" });
		if (el.closest("[data-clear-filters], .sf-state-action")) return this.clear_filters();
		hit = el.closest("[data-sort]");
		if (hit) {
			this.sort = this.kit.next_sort(this.sort, hit.getAttribute("data-sort"));
			this.current_view = "";
			this.remember();
			return this.render();
		}
		if (el.closest("[data-filters-toggle]")) {
			this.filters_open = !this.filters_open;
			return this.render();
		}
		if (el.closest("[data-view-start]")) {
			this.naming_view = true;
			this.view_name_draft = this.current_view || "";
			this.render();
			const input = this.$root[0].querySelector("[data-view-name]");
			if (input) input.focus();
			return;
		}
		if (el.closest("[data-view-cancel]")) {
			this.naming_view = false;
			return this.render();
		}
		if (el.closest("[data-view-save]")) return this.save_named_view();
		if (el.closest("[data-view-delete]")) return this.delete_named_view();
		// P6.7: quick add on a column, selection, and the bulk bar.
		hit = el.closest("[data-add]");
		if (hit) return this.open_side({ kind: this.kit.NEW, stage: hit.getAttribute("data-add") });
		if (el.closest("[data-select-mode]")) return this.set_selecting(!this.selecting);
		hit = el.closest("[data-pick]");
		if (hit) return this.toggle_pick(hit.getAttribute("data-pick"));
		hit = el.closest("[data-bulk]");
		if (hit) {
			if (!(this.picks || []).length) return;
			return this.open_side({ kind: this.kit.BULK, action: hit.getAttribute("data-bulk") });
		}
		if (el.closest("[data-bulk-clear]")) {
			this.set_picks([]);
			return this.render();
		}
		hit = el.closest("[data-move]");
		if (hit) return this.open_panel(hit.getAttribute("data-move"));
		if (el.closest(".ob-panel")) {
			hit = el.closest("[data-open]");
			if (hit) this.open_record(hit.getAttribute("data-open"));
			return;
		}
		if (el.closest("select, input, textarea, label")) return;
		// P6.6: a deal opens in the side panel, from its card (a tap, on a phone too: the Move button
		// is the way to the move panel) and from anywhere on its row in the table, the timeline and
		// the attention list. While selecting, the same tap selects it instead.
		hit = el.closest(".ob-card[data-name], [data-open], tr[data-name], .ob-att-item[data-name], .ob-tl-item[data-name]");
		if (!hit) return;
		const name = hit.getAttribute("data-open") || hit.getAttribute("data-name");
		if (!name) return;
		if (this.selecting) return this.toggle_pick(name);
		this.open_side({ kind: this.kit.EDIT, name, step: "" });
	}

	// ------------------------------------------------------------------ selection and bulk (P6.7)

	set_selecting(on) {
		this.selecting = !!on;
		if (!this.selecting) this.set_picks([]);
		this.render();
	}

	toggle_pick(name) {
		if (!name || !this.model || !this.model.by_name[name]) return;
		this.set_picks(this.kit.clean_picks(this.model, this.kit.toggle_pick(this.picks || [], name)));
		this.selecting = true;
		this.render();
		this.update_bulk_count();
	}

	pick_html(deal) {
		if (!this.selecting) return "";
		const esc = this.sf.esc;
		const on = (this.picks || []).includes(deal.name);
		return `<button type="button" class="ob-pick" data-pick="${esc(deal.name)}" aria-pressed="${on}" aria-label="${esc(
			__("Select {0}", [deal.title])
		)}"><span class="ob-pick-box" aria-hidden="true">${on ? "✓" : ""}</span></button>`;
	}

	// The bar along the bottom while selecting: how many, and what to do to them.
	bulkbar_html() {
		const esc = this.sf.esc;
		const count = (this.picks || []).length;
		const none = count ? "" : " disabled";
		const action = (key, label) =>
			`<button type="button" class="sf-btn sf-btn--secondary" data-bulk="${esc(key)}"${none}>${esc(label)}</button>`;
		return `<section class="ob-bulkbar" aria-label="${esc(__("Selected deals"))}">
			<p class="ob-bulkbar-count" aria-live="polite">${esc(
				count === 1 ? __("1 deal selected") : __("{0} deals selected", [count])
			)}</p>
			<div class="ob-bulkbar-actions">
				${action("stage", __("Change stage"))}
				${action("owner", __("Reassign"))}
				${action("close_date", __("Set close date"))}
				${this.model.can_snooze ? action("snooze", __("Snooze")) : ""}
				<button type="button" class="sf-btn sf-btn--quiet" data-bulk-clear${none}>${esc(__("Clear"))}</button>
			</div>
		</section>`;
	}

	close_bulk() {
		const bulk = this.bulk;
		this.bulk = null;
		if (bulk && bulk.handle && bulk.handle.is_open()) {
			this.bulk_closing = true;
			try {
				bulk.handle.close();
			} finally {
				this.bulk_closing = false;
			}
		}
	}

	update_bulk_count() {
		if (!this.bulk || !this.bulk.body) return;
		const el = this.bulk.body.querySelector(".ob-bulk-count");
		if (el) el.textContent = this.bulk_count_text();
	}

	bulk_count_text() {
		const count = this.kit.clean_picks(this.model, this.picks || []).length;
		return count === 1 ? __("1 deal selected") : __("{0} deals selected", [count]);
	}

	// The bulk panel: the planner kit's drawer, like the side panel, on its own route (.../bulk/<action>).
	open_bulk(action) {
		if (this.bulk && this.bulk.action === action && this.bulk.handle && this.bulk.handle.is_open()) return;
		this.ensure_side().then(() => {
			const pk = window.planner_kit;
			const side = this.target && this.target.side;
			if (!pk || !side || side.kind !== this.kit.BULK || side.action !== action || !this.showing()) return;
			if (this.bulk && this.bulk.action === action && this.bulk.handle && this.bulk.handle.is_open()) return;
			if (this.side && this.side.is_open()) this.side.hide("route");
			this.close_bulk();
			const body = document.createElement("div");
			body.className = "ob-bulk";
			const close = document.createElement("button");
			close.type = "button";
			close.className = "sf-btn sf-btn--quiet fk-close";
			close.textContent = __("Close");
			close.addEventListener("click", () => this.close_side());
			const titles = {
				stage: __("Change stage"),
				owner: __("Reassign"),
				close_date: __("Set close date"),
				snooze: __("Snooze"),
			};
			const bulk = { action, body, stage: "", fg: null, outcome: null, busy: false, error: "" };
			this.bulk = bulk;
			bulk.handle = pk.drawer.open({
				title: titles[action] || "",
				body,
				width: 480,
				owner: "ob-bulk",
				push: this.$root[0],
				history: false,
				// The drawer's × closes through the route, as the Close button does.
				request_close: () => this.close_side(),
				tools: close,
				on_close: (how) => {
					if (this.bulk_closing || this.bulk !== bulk) return;
					this.bulk = null;
					if (how !== "replaced") this.close_side();
				},
			});
			body.addEventListener("click", (e) => this.on_bulk_click(e));
			this.render_bulk();
		});
	}

	render_bulk() {
		const bulk = this.bulk;
		if (!bulk || !this.model) return;
		const esc = this.sf.esc;
		const names = this.kit.clean_picks(this.model, this.picks || []);
		const titles = names.map((name) => (this.model.by_name[name] || {}).title || name);
		const shown = titles.slice(0, 6).map((t) => `<li>${esc(t)}</li>`).join("");
		const more = titles.length > 6 ? `<li>${esc(__("and {0} more", [titles.length - 6]))}</li>` : "";
		// A stage move takes at most 25 deals a request, the rest 100 (kit.bulk_cap, the server's caps).
		const cap = this.kit.bulk_cap(bulk.action);
		const over = names.length > cap;
		const cap_note = over
			? `<p class="ob-bulk-error" role="alert">${esc(
					__("This changes at most {0} deals at once, and {1} are selected. Clear some, then try again.", [cap, names.length])
			  )}</p>`
			: "";
		const head = `<p class="ob-bulk-count">${esc(this.bulk_count_text())}</p><ul class="ob-bulk-names">${shown}${more}</ul>${cap_note}`;
		if (bulk.outcome) {
			bulk.body.innerHTML = head + this.bulk_outcome_html(bulk.outcome);
			bulk.fg = null;
			return;
		}
		let body = "";
		let commit = "";
		if (bulk.action === "stage" && !bulk.stage) {
			const buttons = this.kit
				.bulk_stages(this.model)
				.map(
					(stage) =>
						`<button type="button" class="ob-stage-btn ob-stage-btn--${esc(stage.kind)}" data-bulk-stage="${esc(stage.key)}"><span>${esc(
							stage.label
						)}</span>${
							stage.kind === "close"
								? `<span class="ob-stage-note">${esc(__("Asks why they were lost"))}</span>`
								: ""
						}</button>`
				)
				.join("");
			// Closed Won is never offered here (kit.bulk_stages): a deliberate departure from P6.7.
			body = `<p class="ob-panel-intro">${esc(__("Choose the stage they move to."))}</p><div class="ob-panel-stages">${buttons}</div>
				<p class="ob-panel-intro">${esc(__("Win deals one at a time: each needs its own ranks and hand-off."))}</p>`;
		} else {
			const stage = bulk.stage ? this.kit.stage_by_key(this.model, bulk.stage) : null;
			const intro = {
				stage: stage
					? stage.gate === "lost"
						? __("Why they were lost, saved on each deal as Set as Lost does.")
						: __("Each deal moves to {0}, checked and saved the way a single move is.", [stage.label])
					: "",
				owner: __("Who owns them from now on."),
				close_date: __("When they are expected to close."),
				snooze: __("Hidden from the board until this date, then back, marked."),
			}[bulk.action];
			const label = {
				stage: stage ? __("Move {0} to {1}", [names.length, stage.label]) : "",
				owner: __("Reassign {0}", [names.length]),
				close_date: __("Set it on {0}", [names.length]),
				snooze: __("Snooze {0}", [names.length]),
			}[bulk.action];
			body = `${stage ? `<p class="ob-bulk-stage">${esc(__("To {0}", [stage.label]))} <button type="button" class="sf-btn sf-btn--quiet" data-bulk-restage>${esc(__("Change"))}</button></p>` : ""}
				<p class="ob-panel-intro">${esc(intro || "")}</p>
				<div class="ob-bulk-fields"></div>
				${bulk.error ? `<p class="ob-bulk-error" role="alert">${esc(bulk.error)}</p>` : ""}`;
			commit = `<div class="ob-bulk-foot"><button type="button" class="sf-btn sf-btn--primary" data-bulk-commit${
				bulk.busy || !names.length || over ? " disabled" : ""
			}>${esc(bulk.busy ? __("Working…") : label)}</button></div>`;
		}
		bulk.body.innerHTML = head + body + commit;
		bulk.fg = null;
		const holder = bulk.body.querySelector(".ob-bulk-fields");
		if (holder) this.bulk_fields(holder);
	}

	// The fields an action needs, as frappe controls. Lost's are the Set as Lost fields, from the deal's
	// own flow, read once with get_flow.
	bulk_fields(holder) {
		const bulk = this.bulk;
		const make = (fields) => {
			if (this.bulk !== bulk || !fields.length || !holder.isConnected) return;
			bulk.fg = new frappe.ui.FieldGroup({ fields, body: holder, no_submit_on_enter: true });
			bulk.fg.make();
			if (bulk.values) bulk.fg.set_values(bulk.values);
		};
		if (bulk.action === "owner") {
			make([
				{
					fieldtype: "Link",
					fieldname: "owner",
					options: "User",
					label: __("Opportunity Owner"),
					reqd: 1,
					get_query: () => ({ filters: { enabled: 1, user_type: "System User" } }),
				},
			]);
		} else if (bulk.action === "close_date") {
			make([{ fieldtype: "Date", fieldname: "date", label: __("Expected Closing Date"), reqd: 1 }]);
		} else if (bulk.action === "snooze") {
			make([
				{ fieldtype: "Date", fieldname: "until", label: __("Hide them until"), reqd: 1 },
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Why (optional)") },
			]);
		} else if (bulk.action === "stage" && bulk.stage) {
			const stage = this.kit.stage_by_key(this.model, bulk.stage);
			if (!stage || stage.gate !== "lost") return;
			this.bulk_flow_for().then((flow) => {
				const fk = window.sf_form_kit;
				if (!flow || !fk || !flow.lost) {
					const note = document.createElement("p");
					note.className = "ob-bulk-error";
					note.setAttribute("role", "alert");
					note.textContent = __("The fields this stage needs could not be read. Choose the stage again to retry.");
					if (holder.isConnected) holder.replaceChildren(note);
					return;
				}
				make(fk.lost_fields(flow.lost, __));
			});
		}
	}

	// A deal's flow (any selected one: the ranks' and the lost tables' definitions do not differ by
	// deal), read once and kept; the Opportunity meta first, for the Table MultiSelect.
	bulk_flow_for() {
		if (this.bulk_flow) return Promise.resolve(this.bulk_flow);
		const name = (this.picks || [])[0];
		if (!name) return Promise.resolve(null);
		return new Promise((resolve) => {
			try {
				frappe.model.with_doctype(OB_DOCTYPE, () => resolve());
			} catch (e) {
				resolve();
			}
		})
			.then(() => this.sf.call(OB_FORM_KIT + "get_flow", { doctype: OB_DOCTYPE, name }, { quiet: true, method: "GET" }))
			.then((flow) => {
				this.bulk_flow = flow || null;
				return this.bulk_flow;
			})
			.catch(() => null);
	}

	on_bulk_click(e) {
		const el = e.target;
		const bulk = this.bulk;
		if (!el || !el.closest || !bulk) return;
		let hit = el.closest("[data-bulk-stage]");
		if (hit) {
			bulk.stage = hit.getAttribute("data-bulk-stage");
			bulk.error = "";
			const stage = this.kit.stage_by_key(this.model, bulk.stage);
			const fits = this.kit.clean_picks(this.model, this.picks || []).length <= this.kit.bulk_cap(bulk.action);
			if (stage && stage.kind !== "close" && fits) return this.bulk_commit();
			return this.render_bulk();
		}
		if (el.closest("[data-bulk-restage]")) {
			bulk.stage = "";
			bulk.values = null;
			return this.render_bulk();
		}
		if (el.closest("[data-bulk-commit]")) return this.bulk_commit();
		if (el.closest("[data-bulk-done]")) return this.close_side();
		if (el.closest("[data-bulk-again]")) {
			bulk.outcome = null;
			return this.render_bulk();
		}
	}

	// What the action sends for each deal, through opportunity_board.bulk_update (POST): every deal
	// with the `modified` the board loaded, checked and saved one by one on the server.
	bulk_commit() {
		const bulk = this.bulk;
		if (!bulk || bulk.busy) return;
		let values = {};
		if (bulk.fg) {
			values = bulk.fg.get_values();
			if (!values) return; // the FieldGroup has marked what is missing
			bulk.values = values;
		} else if (bulk.action !== "stage") {
			return;
		}
		let payload_values = values;
		if (bulk.action === "stage") {
			const stage = this.kit.stage_by_key(this.model, bulk.stage);
			if (!stage) return;
			payload_values = { status: stage.label };
			if (stage.gate === "won") return; // never offered; the server refuses it too
			if (stage.gate === "lost") {
				Object.assign(payload_values, {
					reasons: values.reasons || [],
					competitors: values.competitors || [],
					detail: values.detail || "",
				});
			}
		}
		const request = this.kit.bulk_request(this.model, this.picks || [], bulk.action, payload_values);
		// Over the action's cap: the panel says so, and nothing is sent until the selection fits.
		if (!request.names.length || request.over > 0) return this.render_bulk();
		bulk.busy = true;
		bulk.error = "";
		this.render_bulk();
		this.move_epoch += 1;
		request.names.forEach((name) => this.saving.add(name));
		this.sf
			.call(OB_API + "bulk_update", { names: request.names, action: request.action, values: request.values, modified: request.modified }, { quiet: true })
			.then((answer) => {
				const outcome = this.kit.bulk_outcome(request.names, answer && answer.results);
				this.set_picks(this.kit.after_bulk(this.picks || [], outcome));
				if (this.bulk === bulk) bulk.outcome = outcome;
			})
			.catch((err) => {
				if (this.bulk === bulk) bulk.error = (err && err.message) || __("Nothing was changed. Try again.");
			})
			.finally(() => {
				bulk.busy = false;
				request.names.forEach((name) => this.saving.delete(name));
				if (this.bulk === bulk) this.render_bulk();
				if (this.showing()) this.render();
				this.refresh();
			});
	}

	bulk_outcome_html(outcome) {
		const esc = this.sf.esc;
		const title = (name) => (this.model.by_name[name] || {}).title || name;
		const done = outcome.ok.length;
		const head =
			done === outcome.total
				? __("All {0} done.", [outcome.total])
				: __("{0} of {1} done. The rest are still selected.", [done, outcome.total]);
		const rows = []
			.concat(
				outcome.conflicts.map(
					(c) =>
						`<li class="ob-bulk-fail"><strong>${esc(title(c.name))}</strong>: ${esc(
							__("changed by {0} after the board loaded, so it was left alone.", [c.modified_by || __("someone else")])
						)}</li>`
				),
				outcome.failed.map(
					(f) =>
						`<li class="ob-bulk-fail"><strong>${esc(title(f.name))}</strong>: ${esc(
							f.message || __("it could not be changed.")
						)}</li>`
				)
			)
			.join("");
		return `<div class="ob-bulk-outcome" role="status">
			<p class="ob-bulk-head">${esc(head)}</p>
			${rows ? `<ul class="ob-bulk-fails">${rows}</ul>` : ""}
			<div class="ob-bulk-foot">
				${rows ? `<button type="button" class="sf-btn sf-btn--secondary" data-bulk-again>${esc(__("Try the rest again"))}</button>` : ""}
				<button type="button" class="sf-btn sf-btn--primary" data-bulk-done>${esc(__("Done"))}</button>
			</div>
		</div>`;
	}

	on_change(e) {
		const el = e.target;
		if (!el || !el.matches || !this.kit) return;
		if (el.matches("[data-filter]")) {
			this.set_filters({ [el.getAttribute("data-filter")]: el.value });
		} else if (el.matches("[data-saved-view]")) {
			this.apply_named_view(el.value);
		} else if (el.matches("[data-card-part]")) {
			const part = el.getAttribute("data-card-part");
			const parts = this.card_parts().filter((p) => p !== part);
			if (el.checked) parts.push(part);
			this.card = this.kit.card_parts(this.kit.card_text(parts));
			this.card_fields_open = true;
			this.current_view = "";
			this.remember();
			this.render();
			const again = this.$root[0].querySelector(`[data-card-part="${part}"]`);
			if (again) again.focus();
		}
	}

	on_input(e) {
		const el = e.target;
		if (!el || !el.matches) return;
		if (el.matches("[data-snooze-until], [data-snooze-reason]")) {
			const panel = this.$root[0].querySelector(".ob-panel");
			const until = panel && panel.querySelector("[data-snooze-until]");
			const reason = panel && panel.querySelector("[data-snooze-reason]");
			this.snooze_draft = {
				name: this.target && this.target.panel,
				until: until ? until.value : "",
				reason: reason ? reason.value : "",
			};
		} else if (el.matches("[data-view-name]")) {
			this.view_name_draft = el.value;
		}
	}

	// ------------------------------------------------------------------ views and filters

	// P6.3: a view is a route, so Back returns to the one before. The filters stay as they are.
	show_view(view) {
		if (!this.kit.VIEWS.includes(view)) return;
		this.go({ view, layout: this.layout, stage: "", panel: "" });
	}

	set_filters(changes) {
		this.filters = this.kit.clean_filters(Object.assign({}, this.filters, changes));
		this.current_view = "";
		this.remember();
		this.render();
	}

	clear_filters() {
		const keep = { hold_open: this.filters.hold_open, show_snoozed: false };
		this.filters = this.kit.clean_filters(Object.assign({}, this.kit.DEFAULT_FILTERS, keep));
		this.current_view = "";
		this.remember();
		this.render();
	}

	// Everything a saved view holds, as VIEW_SCHEMA names it.
	view_state() {
		return this.kit.pick_view(
			Object.assign({}, this.filters, {
				view: (this.target && this.target.view) || "board",
				layout: this.layout,
				sort: this.sort.sort,
				desc: this.sort.desc,
				card: this.kit.card_text(this.card_parts()),
			})
		);
	}

	// What each card shows beside its title (v1.684.0, board.js card_parts): today's card unless the
	// person changed it.
	card_parts() {
		return this.card || this.kit.DEFAULT_CARD.slice();
	}

	// A view's filters, sort and layout on the page; with `navigate`, its view too (a new history
	// entry, as any change of view is).
	// `take_layout` false keeps the board's layout as the address set it: an explicit /stages or
	// /streams wins over the last-used view (kit.last_layout_applies).
	apply_state(state, navigate, take_layout = true) {
		const s = this.kit.pick_view(state || {});
		this.filters = this.kit.clean_filters(Object.assign({}, this.kit.DEFAULT_FILTERS, s));
		this.sort = {
			sort: s.sort || this.kit.DEFAULT_SORT.sort,
			desc: s.desc === undefined ? this.kit.DEFAULT_SORT.desc : !!s.desc,
		};
		if (s.layout && take_layout) this.layout = s.layout;
		// A view saved before cards could be chosen shows the card as it always was.
		this.card = this.kit.card_parts(s.card);
		if (navigate) {
			const moved = this.go({ view: s.view || "board", layout: this.layout, stage: "", panel: "" });
			if (!moved) this.render();
		}
	}

	// The saved views and the last-used view, once per page load. A bare address opens the last
	// view; any other address wins over it, as on the planners. A failed load leaves the defaults and
	// never saves over what is stored.
	load_prefs() {
		return this.sf
			.call(OB_PREFS + "get_views", { planner: OB_PREFS_PLANNER }, { quiet: true, method: "GET" })
			.then((data) => {
				this.saved_views = (data && data.views) || [];
				const last = (data && data.last) || null;
				// The filters and sort always come back; the layout only where the address leaves it
				// open, so an explicit /stages is never drawn as rows.
				if (last) this.apply_state(last, false, this.kit.last_layout_applies(this.target));
				if (this.target && this.target.view === "board") this.target.layout = this.layout;
				this.last_sent = JSON.stringify(this.view_state());
				this.prefs_ready = true;
				const target = this.target;
				if (last && target && target.bare && !target.panel && this.showing()) {
					const view = this.kit.VIEWS.includes(last.view) ? last.view : "board";
					if (this.go({ view, layout: this.layout, stage: "", panel: "" }, true)) {
						// handle_route follows on the next tick; the draw that waits on this promise
						// must already be the remembered view, or the board flashes first.
						const now = this.kit.parse_route(frappe.get_route());
						if (now) {
							if (now.view === "board") now.layout = this.layout;
							this.target = now;
						}
					}
				}
			})
			.catch(() => {
				this.prefs_ready = false;
			});
	}

	// Remember the view as "last used", a moment after the last change (planner_views_prefs
	// save_last_view: written to the table at once, not to frappe's Redis-only user settings).
	remember() {
		if (!this.prefs_ready) return;
		clearTimeout(this.last_timer);
		this.last_timer = setTimeout(() => {
			const state = this.view_state();
			const text = JSON.stringify(state);
			if (text === this.last_sent) return;
			this.last_sent = text;
			this.sf
				.call(OB_PREFS + "save_last_view", { planner: OB_PREFS_PLANNER, data: state }, { quiet: true })
				.catch(() => {
					this.last_sent = "";
				});
		}, OB_LAST_VIEW_DELAY_MS);
	}

	apply_named_view(name) {
		const view = this.saved_views.find((v) => v.name === name);
		if (!view) {
			this.current_view = "";
			this.render();
			return;
		}
		this.current_view = view.name;
		this.naming_view = false;
		this.apply_state(view.data, true);
		this.remember();
	}

	save_named_view() {
		const name = (this.view_name_draft || "").trim();
		if (!name) {
			this.sf.toast(__("Give the view a name."), { tone: "warning" });
			return;
		}
		this.sf
			.call(OB_PREFS + "save_view", { planner: OB_PREFS_PLANNER, name, data: this.view_state() }, { quiet: true })
			.then((result) => {
				this.saved_views = (result && result.views) || this.saved_views;
				this.current_view = (result && result.saved) || name;
				this.naming_view = false;
				this.view_name_draft = "";
				this.render();
				this.sf.toast(__("Saved the view \"{0}\"", [this.current_view]), { tone: "success" });
			})
			.catch((err) => this.sf.toast((err && err.message) || __("The view could not be saved."), { tone: "warning" }));
	}

	delete_named_view() {
		const name = this.current_view;
		if (!name) return;
		this.sf
			.call(OB_PREFS + "delete_view", { planner: OB_PREFS_PLANNER, name }, { quiet: true })
			.then((result) => {
				this.saved_views = (result && result.views) || this.saved_views.filter((v) => v.name !== name);
				this.current_view = "";
				this.render();
				this.sf.toast(__("Deleted the view \"{0}\"", [name]), { tone: "info" });
			})
			.catch((err) => this.sf.toast((err && err.message) || __("The view could not be deleted."), { tone: "warning" }));
	}

	// ------------------------------------------------------------------ snoozing (P6.8)

	save_snooze() {
		const name = this.target && this.target.panel;
		const deal = name && this.model ? this.model.by_name[name] : null;
		if (!deal) return;
		const panel = this.$root[0].querySelector(".ob-panel");
		const until = ((panel && panel.querySelector("[data-snooze-until]")) || {}).value || "";
		const reason = ((panel && panel.querySelector("[data-snooze-reason]")) || {}).value || "";
		if (!until) {
			this.sf.toast(__("Pick the date it comes back."), { tone: "warning" });
			return;
		}
		this.close_panel();
		this.send_snooze(deal, until, reason);
	}

	wake(name) {
		const deal = name && this.model ? this.model.by_name[name] : null;
		if (!deal) return;
		this.close_panel();
		this.send_snooze(deal, "", "");
	}

	// Through opportunity_board.snooze (POST), locked on the deal's `modified` like a move. A conflict
	// saves nothing, says who changed the deal and reloads, as a move's does.
	send_snooze(deal, until, reason) {
		if (this.saving.has(deal.name)) return;
		const before = deal;
		this.move_epoch += 1;
		this.saving.add(deal.name);
		this.snooze_draft = null;
		let reload = false;
		this.sf
			.call(OB_API + "snooze", { name: deal.name, until, reason, modified: deal.modified }, { quiet: true })
			.then((result) => {
				if (result && result.conflict) {
					reload = true;
					this.sf.toast(
						__("{0} was changed by {1} after the board loaded, so it was not snoozed. The board now shows the latest.", [
							before.title,
							result.modified_by || __("someone else"),
						]),
						{ tone: "warning" }
					);
					return;
				}
				this.model = this.kit.set_snooze(
					this.model,
					deal.name,
					(result && result.snoozed_until) || "",
					(result && result.snooze_reason) || "",
					result && result.modified
				);
				const options = { tone: "success" };
				if (until) {
					options.action_label = __("Undo");
					options.on_action = () => {
						const latest = this.model && this.model.by_name[deal.name];
						// Back to how it was: snoozed to its old date, or awake (a returning deal's old
						// date has passed, and the server only takes a date after today).
						if (latest) {
							const was = before.snooze === "snoozed";
							this.send_snooze(latest, was ? before.snoozed_until : "", was ? before.snooze_reason : "");
						}
					};
				}
				this.sf.toast(
					until
						? __("{0} is snoozed until {1}", [before.title, this.date_text(result.snoozed_until)])
						: __("{0} is awake", [before.title]),
					options
				);
			})
			.catch((err) => {
				this.sf.toast((err && err.message) || __("{0} could not be snoozed.", [before.title]), { tone: "warning" });
			})
			.finally(() => {
				this.saving.delete(deal.name);
				if (this.showing()) this.render();
				if (reload || this.refresh_waiting) this.refresh();
			});
	}

	choose_stage(key) {
		const name = this.target && this.target.panel;
		const deal = name && this.model ? this.model.by_name[name] : null;
		if (!deal) return;
		const action = this.kit.drop_action(this.model, deal, key);
		if (action === "close") {
			// P6.6: the stage's required fields in the side panel, in place of this panel's entry.
			this.open_side({ kind: this.kit.CLOSING, name, stage: key }, true);
			return;
		}
		if (action === "none") return;
		this.close_panel();
		if (action === "save") this.move(deal, key);
		else this.refuse(key);
	}

	refuse(key) {
		const stage = this.kit.stage_by_key(this.model, key);
		this.sf.toast(__("{0} is no longer a stage, so nothing can be moved into it.", [stage ? stage.label : key]), {
			tone: "warning",
		});
	}

	// ------------------------------------------------------------------ dragging

	on_down(e) {
		if (e.button > 0 || this.drag || !this.model || this.is_phone() || !e.target.closest) return;
		// While selecting (P6.7) a press selects; it never lifts a card.
		if (this.selecting) return;
		if (e.target.closest(".ob-move, .ob-panel, .ob-panel-backdrop, .ob-pick, .ob-add")) return;
		const card = e.target.closest(".ob-card[data-name]");
		if (!card || !this.$root[0].contains(card)) return;
		const name = card.getAttribute("data-name");
		if (this.saving.has(name) || !this.model.by_name[name]) return;
		this.drag = { name, el: card, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
		if (this.drag.touch) {
			this.drag.timer = setTimeout(() => this.lift(), this.kit.HOLD_MS);
		} else {
			// No mousedown, so a drag does not select the text it crosses. The click still fires.
			e.preventDefault();
		}
	}

	on_move(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > this.kit.DRAG_PX;
		if (!drag.active) {
			if (drag.touch) {
				// Moved before the hold finished: that is a scroll, not a drag.
				if (far) this.end_drag();
				return;
			}
			if (!far) return;
			this.lift();
		}
		e.preventDefault();
		this.place_ghost(e.clientX, e.clientY);
		this.mark_target(this.find_target(e.clientX, e.clientY));
		this.edge_scroll(e.clientX, e.clientY);
	}

	on_up(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const was_active = drag.active;
		const target = was_active ? this.find_target(e.clientX, e.clientY) : null;
		this.end_drag();
		if (!was_active) return;
		this.click_blocked_until = Date.now() + OB_CLICK_BLOCK_MS;
		if (target) this.drop(drag.name, target.key);
	}

	on_cancel(e) {
		if (this.drag && e.pointerId === this.drag.id) this.end_drag();
	}

	lift() {
		const drag = this.drag;
		if (!drag || drag.active) return;
		drag.active = true;
		const rect = drag.el.getBoundingClientRect();
		drag.dx = drag.x - rect.left;
		drag.dy = drag.y - rect.top;
		drag.ghost = drag.el.cloneNode(true);
		drag.ghost.classList.add("ob-ghost");
		drag.ghost.setAttribute("aria-hidden", "true");
		drag.ghost.style.width = `${Math.max(rect.width, 200)}px`;
		document.body.appendChild(drag.ghost);
		drag.el.classList.add("is-lifted");
		this.$root[0].classList.add("is-dragging");
		document.body.classList.add("ob-drag-active");
		this.place_ghost(drag.x, drag.y);
		if (drag.touch && navigator.vibrate) navigator.vibrate(12);
	}

	place_ghost(x, y) {
		const drag = this.drag;
		if (!drag || !drag.ghost) return;
		drag.ghost.style.left = `${x - drag.dx}px`;
		drag.ghost.style.top = `${y - drag.dy}px`;
	}

	// The stage under the pointer: a column, a row's cell, or a closing zone.
	find_target(x, y) {
		const el = document.elementFromPoint(x, y);
		if (!el || !el.closest || !this.$root[0].contains(el)) return null;
		const zone = el.closest("[data-drop]");
		return zone ? { el: zone, key: zone.getAttribute("data-drop") } : null;
	}

	mark_target(target) {
		const drag = this.drag;
		if (!drag) return;
		if (drag.over && (!target || drag.over !== target.el)) drag.over.classList.remove("is-over");
		drag.over = target ? target.el : null;
		if (drag.over) drag.over.classList.add("is-over");
	}

	// Near the top or bottom of the window, scroll it; near the side of the board, scroll that.
	edge_scroll(x, y) {
		const edge = 56;
		if (y < edge) window.scrollBy(0, -14);
		else if (y > window.innerHeight - edge) window.scrollBy(0, 14);
		const scroller = this.$root[0].querySelector(".ob-scroll");
		if (!scroller) return;
		const rect = scroller.getBoundingClientRect();
		if (x < rect.left + 40) scroller.scrollLeft -= 14;
		else if (x > rect.right - 40) scroller.scrollLeft += 14;
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		if (drag.over) drag.over.classList.remove("is-over");
		drag.el.classList.remove("is-lifted");
		this.$root[0].classList.remove("is-dragging");
		document.body.classList.remove("ob-drag-active");
		if (this.refresh_waiting && !this.saving.size) setTimeout(() => this.refresh(), 0);
	}

	drop(name, key) {
		const deal = this.model && this.model.by_name[name];
		const action = this.kit.drop_action(this.model, deal, key);
		if (action === "save") this.move(deal, key);
		// P6.6: the stage's required fields in the side panel; the card stays put until they are saved.
		else if (action === "close") this.open_side({ kind: this.kit.CLOSING, name, stage: key });
		else if (action === "refuse") this.refuse(key);
	}

	// ------------------------------------------------------------------ saving a move

	put_back(before) {
		this.model = this.kit.replace_deal(this.model, before);
	}

	move(deal, key) {
		const stage = this.kit.stage_by_key(this.model, key);
		if (!deal || !stage || this.saving.has(deal.name)) return;
		const before = deal;
		const from = this.kit.stage_by_key(this.model, deal.stage);
		// Any get_board already in flight now answers for a board from before this move (refresh()).
		this.move_epoch += 1;
		this.saving.add(deal.name);
		this.model = this.kit.move_deal(this.model, deal.name, stage.label);
		this.render();
		let reload = false;
		this.sf
			.call(
				OB_FORM_KIT + "save_step",
				{ doctype: OB_DOCTYPE, name: deal.name, values: { status: stage.label }, modified: deal.modified },
				{ quiet: true }
			)
			.then((result) => {
				if (result && result.conflict) {
					// Saved by someone else since the board loaded: nothing was written. Their
					// version wins on screen; the person can move it again from there.
					this.put_back(before);
					reload = true;
					this.sf.toast(
						__("{0} was changed by {1} after the board loaded, so it was not moved. The board now shows the latest.", [
							before.title,
							result.modified_by || __("someone else"),
						]),
						{ tone: "warning" }
					);
					return;
				}
				const values = (result && result.values) || {};
				if (values.status !== stage.label) {
					this.put_back(before);
					this.sf.toast(__("You cannot change the stage of {0}.", [before.title]), { tone: "warning" });
					return;
				}
				const now = this.model.by_name[deal.name];
				if (now) {
					this.model = this.kit.replace_deal(
						this.model,
						Object.assign({}, now, {
							modified: result.modified || now.modified,
							completeness: result.completeness || now.completeness,
						})
					);
				}
				const options = { tone: "success" };
				if (from && from.kind !== "other") {
					options.action_label = __("Undo");
					options.on_action = () => {
						const latest = this.model && this.model.by_name[deal.name];
						if (latest && latest.stage === stage.key) this.move(latest, from.key);
					};
				}
				this.sf.toast(__("{0} moved to {1}", [before.title, stage.label]), options);
			})
			.catch((err) => {
				this.put_back(before);
				this.sf.toast((err && err.message) || __("{0} could not be moved.", [before.title]), { tone: "warning" });
			})
			.finally(() => {
				this.saving.delete(deal.name);
				if (this.showing()) this.render();
				if (reload || this.refresh_waiting) this.refresh();
			});
	}
}
