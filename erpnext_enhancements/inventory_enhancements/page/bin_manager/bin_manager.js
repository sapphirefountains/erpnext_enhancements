// Bin Manager (v1.693.0, ER-2026-1320545, TASK-2026-02827): one bin's items on a PC, without
// scanning. The stock lead picks a bin and sees every item there with its quantity, and from there
// changes a quantity (a Stock Reconciliation: a count correction, never a Material Issue), moves an
// item to another bin, unassigns it (with stock still there: Move or Send to boneyard first), adds
// an existing item, or creates a new one already on the bin. Every save writes a Stock Scan Log row
// and can be undone from the page, as on /stock-scan, whose endpoints this calls:
//
//   bin_assignments.set_count / move_to / send_to_boneyard / unassign / assign   (PR 1, v1.689.0)
//   bin_assignments.create_and_assign, bin_assignments.manager_boot             (this page)
//   api.stock_scan.get_location / get_recent / search_items / check_new_item / undo
//
// The decisions (routes, which buttons show, the make-home box, each request) are pure:
// public/js/bin_manager/logic.js, and the screen state and server calls are the controller,
// public/js/bin_manager/controller.js, both in bin_manager.bundle.js (window.sf_bin_manager) and run
// under node by scripts/test_bin_manager.mjs. This file draws and wires.
//
// SCREENS, each its own history entry (never break Back/Forward):
//
//   /desk/bin-manager               the picker: every bin, filterable, with its counts
//   /desk/bin-manager/<warehouse>   one bin: its items, the actions, and this bin's recent saves
//   /desk/bin-manager?warehouse=X   a deep link: opens X in place of the picker entry
//
// and since v1.806.1 (Nik, 2026-11-12: "more filters and different views... by Project which items
// are at the Job Site"):
//
//   /desk/bin-manager/by-project             the jobs with material on the Project Shelf or at a site
//   /desk/bin-manager/by-project/<project>   one job: its shelf and its site, and moves between them
//   /desk/bin-manager/job-sites              every job site with its job, item count and value
//   /desk/bin-manager/problems               assigned but empty, stock with no assignment, stock still
//                                            held for a finished job
//
// The four tabs are routes, so each is one history entry. The picker's FILTERS (what is typed, the
// kind of place, a job, an item, a quick filter) are written into the picker's own address in place
// (?kind=job_site&project=PRJ-00597, history.replaceState): a filter is never a history entry, and
// Back from a bin lands on the picker as it was left, read back out of the address like a deep link.
// What each view shows is public/js/bin_manager/views.js; its numbers are bin_views.py's.
//
// Picking a bin is one frappe.set_route; Back is the browser's and lands on the picker; Forward lands
// on the bin. Dialogs (frappe.ui.Dialog) push nothing, and the router closes an open one on any route
// change (router.set_history -> frappe.ui.hide_open_dialog), so Back with a dialog open closes it and
// returns to the picker. frappe.route_options is read on every show (the address's query lands there
// too since v16.50), never location.search.
//
// WHO SEES WHAT comes from the server (manager_boot), never a role list here: assign, unassign, keep,
// boneyard, new item and "make it the item's bin" need the bin-assignment gate (can_manage); a count
// needs Stock Reconciliation create and submit (can_count); a move needs Stock Entry create and
// submit (can_move); New item needs Item create too. The boneyard is read by the server from
// Inventory Scanner Settings: the page never reads a Settings Single. A refusal is the server's own
// sentence, shown inline in the dialog that asked.
//
// Every request goes through sf_kit.call(..., { quiet: true }), so Frappe's own dialogs never cover
// the page. The CSS is this folder's bin_manager.css, every selector under .bm- (a Desk Page's
// stylesheet is global once loaded).

frappe.pages["bin-manager"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Bin Manager"),
		single_column: true,
	});
	wrapper.bin_manager = new BinManagerPage(page, wrapper);
};

frappe.pages["bin-manager"].on_page_show = function (wrapper) {
	if (wrapper.bin_manager) wrapper.bin_manager.route_changed();
};

const BM_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "bin_manager.bundle.js"];
// The reorder boxes (v1.725.0): asked for apart from the page's own files, and optional. A page whose
// copy of them did not load shows no Reorder column and works as before.
const BM_REORDER_ASSETS = ["reorder_levels.bundle.js", "reorder_levels.bundle.css"];
// The person's own switch for the Reorder column (TASK-2026-02920): off until they turn it on, and kept for
// good in the same per-user store as the planners' saved views (api/planner_views_prefs.py).
const BM_REORDER_PREF = "erpnext_enhancements.api.planner_views_prefs.set_show_reorder";
const BM_PAGE = "bin-manager";
// The Reorder Board (v1.730.0): buyers pick reorders from it now that ERPNext no longer raises them on its own, and
// a bin or a count is where a shortage is seen. The link shows only to the roles that may open that page
// (BOARD_ROLES in inventory_enhancements/reorder_board.py, which tests/test_bin_manager.py pins this list to). The
// page is gated on the server too: this only keeps a link that ends in "no permission" off the screen. The check is
// the Desk's own (frappe.user), never a read of a Settings Single.
const BM_BOARD_ROUTE = "reorder-board";
const BM_BOARD_ROLES = ["Purchase Manager", "Purchase User", "Stock Manager", "System Manager"];
const BM_SEARCH_DELAY_MS = 250;

class BinManagerPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="bm-app"></div>').appendTo(page.main);
		this.sf = null;
		this.bm = null;
		this.ctl = null;
		this.drawn = "";
		this._route_timer = null;
		this.reorder = null; // the reorder editor (public/js/reorder_levels), once its bundle is here
		this.reorder_for = null; // the location object the editor last read the rules for
		this.reorder_pref = null; // the person's switch once they have used it here; until then the boot's
		this.reorder_saving = false; // a request storing the switch is in flight (remember_reorder sends one at a time)
		this.reorder_want = null; // the latest choice to store
		this.redraw_after_blur = false; // a table redraw is waiting for a reorder box to be left
		// v16's frappe.require calls back even when a file failed to load (assets.js "resolve even on
		// error"), so both globals are checked; a page without them says so instead of waiting.
		this.ready = new Promise((resolve) => frappe.require(BM_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_kit.state ? window.sf_kit : null;
			// `views` came with v1.806.1: a copy of the bundle without it cannot draw this page.
			this.bm = window.sf_bin_manager && window.sf_bin_manager.Controller && window.sf_bin_manager.views ? window.sf_bin_manager : null;
			if (this.sf && this.bm) this.ctl = new this.bm.Controller(this.env());
		});
		// The reorder boxes are not part of `ready`: the bins draw as soon as the page's own files are here, and
		// the Reorder column joins them when its bundle arrives (a slow or failed bundle holds nothing up).
		this.ready.then(() => this.load_reorder_bundle());
		page.set_secondary_action(__("Refresh"), () => this.ctl && this.ctl.refresh(), "refresh");
		this.bind();
	}

	env() {
		return {
			call: (method, args, opts) => this.sf.call(method, args, opts),
			get_route: () => frappe.get_route(),
			set_route: (route, replace) => {
				// A filter still waiting to be written belongs to the entry being left.
				this.flush_query();
				if (replace) frappe.route_flags.replace_route = true;
				frappe.set_route(route);
				// Read by push_state, which has already run; left set, the next screen would replace too.
				delete frappe.route_flags.replace_route;
			},
			route_options: () => frappe.route_options,
			clear_route_options: () => {
				frappe.route_options = null;
			},
			history_state: () => window.history.state,
			tag_state: (tag) => {
				try {
					window.history.replaceState(Object.assign({}, window.history.state, tag), "");
				} catch (e) {
					// Untagged: "All bins" pushes the picker instead of stepping back, and Back still works.
				}
			},
			history_back: () => {
				this.flush_query();
				window.history.back();
			},
			replace_query: (query) => this.replace_query(query),
			render: () => this.render(),
			now: () => Date.now(),
			random: () => Math.random(),
		};
	}

	// The picker's filters into its own address, in place: no history entry. A moment later rather than
	// at once, because typing in the search box changes them on every key and Safari refuses more than
	// 100 history writes in 30 seconds. Only ever written onto the picker's own address.
	replace_query(query) {
		this._query = query;
		clearTimeout(this._query_timer);
		this._query_timer = setTimeout(() => this.flush_query(), 250);
	}

	flush_query() {
		clearTimeout(this._query_timer);
		this._query_timer = null;
		if (this._query === undefined || this._query === null) return;
		const query = this._query;
		this._query = null;
		const route = frappe.get_route() || [];
		if (route[0] !== BM_PAGE || route.length !== 1) return;
		// Written, never read: the filters come in through frappe.route_options (controller.show), which
		// the router fills from the address on every route change.
		try {
			window.history.replaceState(window.history.state, "", window.location.pathname + query + window.location.hash);
		} catch (e) {
			// The filters still work; only the address is a step behind.
		}
	}

	// The reorder boxes (v1.725.0): the shared editor, once its bundle has arrived, wired to this page's
	// root. Everything about them is optional: with no bundle there is no Reorder column.
	load_reorder_bundle() {
		new Promise((resolve) => {
			try {
				frappe.require(BM_REORDER_ASSETS, resolve);
			} catch (e) {
				resolve();
			}
		}).then(() => {
			this.start_reorder();
			// The bin or picker already on screen is drawn again, now with the column.
			if (this.reorder) this.render();
		});
	}

	start_reorder() {
		const ro = window.sf_reorder && window.sf_reorder.version >= 1 ? window.sf_reorder : null;
		if (!ro || !this.sf) return;
		try {
			this.reorder = new ro.ReorderEditor({
				call: (method, args, opts) => this.sf.call(method, args, opts),
				read: (code) => ro.read_boxes(this.$root[0], code),
				paint: (code, force) => this.paint_reorder(code, force),
				paint_all: () => this.paint_reorder(null),
				later: (fn, ms) => setTimeout(fn, ms),
				tr: (text) => __(text),
				confirm: (text, yes, no) => frappe.confirm(text, yes, no),
			});
			ro.bind(this.$root[0], this.reorder);
		} catch (e) {
			this.reorder = null;
		}
	}

	paint_reorder(code, force) {
		const ro = window.sf_reorder;
		if (!ro || !this.reorder) return;
		ro.paint(this.$root[0], this.reorder, (v) => this.esc(v), (t) => __(t), code, document.activeElement, !!force, this.reorder_labels());
	}

	// The items on screen, by code, for the boxes' accessible names.
	reorder_labels() {
		const loc = this.ctl && this.ctl.state.location;
		const out = {};
		((loc && loc.items) || []).forEach((r) => {
			out[r.item_code] = r.item_name;
		});
		return out;
	}

	// The rules are read once per list of items on screen: a new location object (a bin opened, a save
	// that reloaded it) is a new list, and the same one drawn again is not asked about twice.
	// Whether the Reorder column is on: the editor is here, and the person turned it on (this visit, or at
	// some earlier one: manager_boot says). Off by default, and off whenever the preference could not be read.
	reorder_shown() {
		if (!this.reorder) return false;
		if (this.reorder_pref !== null) return this.reorder_pref;
		const boot = this.ctl && this.ctl.state.boot;
		return !!(boot && boot.show_reorder === true);
	}

	// The switch in the bin's toolbar. The rules are read the first time they are shown, never before.
	toggle_reorder() {
		if (!this.reorder) return;
		const on = !this.reorder_shown();
		this.reorder_pref = on;
		// Nothing is reset: the rules are read when the boxes are first shown for this list (sync_reorder), and
		// showing them again for the same list needs no second read; a save reloads the list, which reads again.
		this.render();
		this.$root.find(".bm-reorder-toggle").trigger("focus");
		this.remember_reorder(on);
	}

	// Stores the switch. Quick taps must not leave the store disagreeing with the screen: replies can arrive out of
	// order, so only one request is in flight, and when it settles the latest choice is sent if it differs from
	// what was just sent. The last tap wins on screen and in the store.
	remember_reorder(on) {
		if (!this.sf) return;
		this.reorder_want = on;
		if (this.reorder_saving) return;
		this.reorder_saving = true;
		const send = () => {
			const sent = this.reorder_want;
			let failed = false;
			return this.sf.call(BM_REORDER_PREF, { screen: "bin_manager", value: sent ? 1 : 0 }, { quiet: true }).then(
				() => {},
				() => {
					failed = true;
				}
			).then(() => {
				if (this.reorder_want !== sent) return send();
				this.reorder_saving = false;
				if (failed) frappe.show_alert({ message: __("Could not save that choice for next time."), indicator: "orange" }, 6);
			});
		};
		send();
	}

	sync_reorder(loc) {
		if (!this.reorder_shown() || !loc || this.reorder_for === loc) return;
		this.reorder_for = loc;
		this.reorder.load(loc.warehouse, (loc.items || []).map((r) => r.item_code));
	}

	showing() {
		return (frappe.get_route() || [])[0] === BM_PAGE;
	}

	// Whether this person may open the Reorder Board. Anything unreadable is "no": a missing link is a lesser
	// fault than one that leads to a refusal.
	can_open_board() {
		try {
			return BM_BOARD_ROLES.some((role) => !!(frappe.user && frappe.user.has_role(role)));
		} catch (e) {
			return false;
		}
	}

	// The link to the Reorder Board, or nothing. One frappe.set_route: the board is its own history entry, so
	// Back lands here again (on this bin, when it was one).
	board_link() {
		if (!this.can_open_board()) return "";
		return `<button type="button" class="btn btn-sm btn-default bm-board-link" data-act="reorder-board">${this.esc(
			__("Reorder Board")
		)}</button>`;
	}

	// One show per task: a replace followed by a push fires on_page_show twice.
	route_changed() {
		if (this._route_timer) return;
		this._route_timer = setTimeout(() => {
			this._route_timer = null;
			this.ready.then(() => {
				if (!this.showing()) return;
				if (!this.ctl) {
					this.broken();
					return;
				}
				this.ctl.show();
			});
		}, 0);
	}

	broken() {
		this.drawn = "";
		this.$root.html(
			`<div class="bm-broken">${frappe.utils.escape_html(
				__("The Bin Manager could not load its files. Reload the page to try again.")
			)}</div>`
		);
	}

	esc(value) {
		return this.sf.esc(value);
	}

	// For a name that goes into frappe's own HTML: a dialog title (Dialog.set_title is .html()), a
	// field's label or description. Item, warehouse and bin names are data, never markup.
	safe(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	// ------------------------------------------------------------------ clicks

	bind() {
		this.$root.on("click", "[data-act]", (e) => {
			e.preventDefault();
			if (!this.ctl) return;
			const el = e.currentTarget;
			const act = el.getAttribute("data-act");
			const code = el.getAttribute("data-item");
			const row = code ? this.row(code) : null;
			if (act === "bin") this.ctl.open_bin(el.getAttribute("data-bin"));
			else if (act === "leave") this.ctl.leave_bin();
			else if (act === "tab") this.ctl.open_tab(el.getAttribute("data-tab"));
			else if (act === "kind") this.ctl.set_filters({ kind: el.getAttribute("data-kind") || "" });
			else if (act === "quick") {
				// A second click on the one that is on turns it off.
				const quick = el.getAttribute("data-quick") || "";
				this.ctl.set_filters({ quick: this.ctl.state.filters.quick === quick ? "" : quick });
			} else if (act === "clear-filters") {
				this.ctl.clear_filters();
				this.$root.find(".bm-filter").val("");
			} else if (act === "item-filter") this.item_filter_dialog();
			else if (act === "item-clear") this.ctl.set_filters({ item: "" });
			else if (act === "project") this.ctl.open_project(el.getAttribute("data-project"));
			else if (act === "leave-project") this.ctl.leave_project();
			else if (act === "project-move") {
				const moving = this.project_row(el.getAttribute("data-section"), code);
				if (moving) this.project_move_dialog(el.getAttribute("data-move"), moving);
			}
			else if (act === "undo") this.undo(el.getAttribute("data-log"), el);
			else if (act === "retry") this.ctl.refresh();
			else if (act === "reorder-toggle") this.toggle_reorder();
			else if (act === "reorder-board") frappe.set_route(BM_BOARD_ROUTE);
			else if (act === "add") this.add_dialog();
			else if (act === "create") this.create_dialog();
			else if (row && act === "count") this.count_dialog(row);
			else if (row && act === "move") this.move_dialog(row);
			else if (row && act === "boneyard") this.boneyard_dialog(row);
			else if (row && act === "unassign") this.unassign_dialog(row);
			else if (row && act === "keep") this.keep(row, el);
		});
		// A table redraw that waited for a reorder box (draw_items): run it once focus has left the boxes.
		this.$root.on("focusout", ".ro-input", () => {
			setTimeout(() => {
				const active = document.activeElement;
				if (!this.redraw_after_blur || (active && active.classList && active.classList.contains("ro-input"))) return;
				this.redraw_after_blur = false;
				this.render();
			}, 0);
		});
		this.$root.on("input", ".bm-filter", (e) => {
			if (!this.ctl) return;
			this.ctl.set_filters({ q: e.currentTarget.value });
		});
		this.$root.on("keydown", ".bm-filter", (e) => {
			if (e.key !== "Enter" || !this.ctl) return;
			const bins = this.ctl.shown_bins();
			if (bins.length === 1) this.ctl.open_bin(bins[0].warehouse);
		});
		this.$root.on("change", ".bm-project-filter", (e) => {
			if (!this.ctl) return;
			// Left first, so the redraw the change causes is not held back by the box it came from.
			const value = e.currentTarget.value;
			e.currentTarget.blur();
			this.ctl.set_filters({ project: value });
		});
		this.$root.on("input", ".bm-project-search", (e) => {
			if (!this.ctl) return;
			this.ctl.state.project_filter = e.currentTarget.value;
			this.draw_projects();
		});
		this.$root.on("keydown", ".bm-project-search", (e) => {
			if (e.key !== "Enter" || !this.ctl) return;
			const rows = this.bm.views.filter_projects(this.ctl.state.projects || [], this.ctl.state.project_filter);
			if (rows.length === 1) this.ctl.open_project(rows[0].project);
		});
	}

	row(code) {
		const loc = this.ctl.state.location;
		return ((loc && loc.items) || []).find((r) => r.item_code === code) || null;
	}

	boot_bins() {
		const boot = this.ctl.state.boot;
		return (boot && boot.bins) || [];
	}

	perms() {
		return this.ctl.perms();
	}

	here() {
		const t = this.ctl.state.target;
		return t && t.screen === "bin" ? t.warehouse : null;
	}

	// ------------------------------------------------------------------ drawing

	render() {
		if (!this.ctl || !this.showing()) return;
		const t = this.ctl.state.target;
		if (!t) return;
		if (t.screen === "picker") this.render_picker();
		else if (t.screen === "projects") this.render_projects();
		else if (t.screen === "project") this.render_project();
		else if (t.screen === "sites") this.render_sites();
		else if (t.screen === "problems") this.render_problems();
		else this.render_bin();
	}

	failed(el, err, retry, title) {
		err = err || {};
		if (err.session_expired) {
			return this.sf.state.render(el, "error", {
				title: __("You have been signed out."),
				body: __("Reload the page to sign in again."),
				action_label: __("Reload"),
				on_action: () => window.location.reload(),
			});
		}
		if (err.status === 403) {
			return this.sf.state.render(el, "no-access", { title: __("You do not have access to the Bin Manager."), body: err.message });
		}
		return this.sf.state.render(el, "error", { title, body: err.message, on_action: retry });
	}

	render_picker() {
		this.page.set_title(__("Bin Manager"));
		if (this.drawn !== "picker") {
			this.drawn = "picker";
			this.$root.html(`
				<div class="bm-picker">
					${this.tabs_html()}
					<div class="bm-toolbar">
						<input type="search" class="form-control bm-filter" autocomplete="off"
							placeholder="${this.esc(__("Find a bin: type part of its name"))}"
							aria-label="${this.esc(__("Find a bin"))}">
						${this.board_link()}
					</div>
					<div class="bm-filters"></div>
					<div class="bm-list"></div>
				</div>`);
			this.$root.find(".bm-filter").val(this.ctl.state.filter || "");
			setTimeout(() => this.$root.find(".bm-filter").trigger("focus"), 0);
		}
		// The box follows the filters when they changed without it (Back onto a filtered picker, Clear),
		// and is left alone while it is being typed in.
		const box = this.$root.find(".bm-filter")[0];
		if (box && document.activeElement !== box && box.value !== (this.ctl.state.filter || "")) box.value = this.ctl.state.filter || "";
		this.draw_filters();
		this.draw_bins();
	}

	draw_bins() {
		const list = this.$root.find(".bm-list")[0];
		if (!list) return;
		const st = this.ctl.state;
		if (!st.boot) {
			if (st.boot_error) this.failed(list, st.boot_error, () => this.ctl.load_boot(true), __("Could not load the bins."));
			else this.sf.state.render(list, "loading", { title: __("Loading the bins…") });
			return;
		}
		// The item filter waits for its answer: which places hold the item is the server's to say.
		const item = st.item;
		if (st.filters.item && item && item.error) {
			this.failed(list, item.error, () => this.ctl.load_item(), __("Could not find where that item is."));
			return;
		}
		if (st.filters.item && (!item || item.loading)) {
			this.sf.state.render(list, "loading", { title: __("Finding where that item is…") });
			return;
		}
		const bins = this.ctl.shown_bins();
		if (!bins.length) {
			const v = this.bm.views;
			const what = v.describe_filters(st.filters, st.boot.bins || [], item && item.data);
			const not_stock = st.filters.item && item && item.data && item.data.is_stock_item === false;
			this.sf.state.render(list, "empty", {
				title: not_stock
					? __("{0} is not kept in stock, so it is at no bin.", [item.data.item_name])
					: v.has_filters(st.filters)
					? __("No place matches: {0}.", [what])
					: __("There are no bins yet."),
			});
			return;
		}
		const parts = this.bm.group_bins(bins).map((group) => {
			const items = group.bins.map((b) => this.bin_row_html(b)).join("");
			const title = group.trail ? `<h3 class="bm-group-title">${this.esc(group.trail)}</h3>` : "";
			return `<section class="bm-group">${title}<ul class="bm-bins">${items}</ul></section>`;
		});
		list.innerHTML = parts.join("");
	}

	render_bin() {
		const st = this.ctl.state;
		const warehouse = st.target.warehouse;
		const loc = st.location && st.location.warehouse === warehouse ? st.location : null;
		this.page.set_title(loc ? loc.warehouse_name : __("Bin Manager"));
		if (this.drawn !== `bin:${warehouse}`) {
			this.drawn = `bin:${warehouse}`;
			this.$root.html(`
				<div class="bm-binview">
					<div class="bm-head">
						<button type="button" class="btn btn-default btn-sm bm-back" data-act="leave">← ${this.esc(this.back_label())}</button>
						<div class="bm-title">
							<h2 class="bm-name"></h2>
							<div class="bm-trail"></div>
						</div>
						<div class="bm-bin-actions"></div>
					</div>
					<div class="bm-notice"></div>
					<div class="bm-items"></div>
					<section class="bm-recent" aria-label="${this.esc(__("Recent saves on this bin"))}">
						<h3 class="bm-section-title">${this.esc(__("Your recent saves on this bin"))}</h3>
						<div class="bm-recent-list"></div>
					</section>
				</div>`);
		}
		const perms = this.perms();
		this.$root.find(".bm-name").text(loc ? loc.warehouse_name : warehouse);
		this.$root.find(".bm-trail").text(loc ? loc.trail || "" : "");
		const actions = st.boot ? this.bm.bin_actions(perms, warehouse) : [];
		const shown = this.reorder_shown();
		const toggle = this.reorder
			? ` <button type="button" class="btn btn-sm btn-default bm-reorder-toggle" data-act="reorder-toggle" aria-pressed="${shown}">${this.esc(
					shown ? __("Hide reorder levels") : __("Show reorder levels")
			  )}</button>`
			: "";
		this.$root.find(".bm-bin-actions").html(
			actions
				.map(
					(a) =>
						`<button type="button" class="btn btn-sm ${a === "add" ? "btn-primary" : "btn-default"}" data-act="${a}">${this.esc(
							__(this.bm.ACTION_LABELS[a])
						)}</button>`
				)
				.join(" ") + toggle + (this.board_link() ? ` ${this.board_link()}` : "")
		);
		this.draw_notice();
		this.draw_items(loc);
		this.sync_reorder(loc);
		this.draw_recent();
	}

	// Where the bin's back button goes: the screen that opened it.
	back_label() {
		const from = this.ctl.came_from();
		if (from === "sites") return __("Job sites");
		if (from === "problems") return __("Problems");
		if (from === "project") return __("Back to the job");
		return __("All bins");
	}

	draw_notice() {
		const box = this.$root.find(".bm-notice");
		const notice = this.ctl.state.notice;
		if (!notice || !notice.message) {
			box.empty();
			return;
		}
		const log = notice.log;
		const undo =
			log && log.status === "Posted" && log.can_undo
				? ` <button type="button" class="btn btn-xs btn-default" data-act="undo" data-log="${this.esc(log.name)}">${this.esc(__("Undo"))}</button>`
				: "";
		const tone = notice.error ? "bm-notice-box--error" : "";
		box.html(`<div class="bm-notice-box ${tone}" role="${notice.error ? "alert" : "status"}">${this.esc(notice.message)}${undo}</div>`);
	}

	draw_items(loc) {
		const el = this.$root.find(".bm-items")[0];
		// A redraw replaces the table, and with it a reorder box being typed in: it waits until the box is
		// left (the focusout handler in bind() draws it then), so nothing on screen goes stale.
		const active = document.activeElement;
		if (this.reorder && el && active && active.classList && active.classList.contains("ro-input") && el.contains(active)) {
			this.redraw_after_blur = true;
			return;
		}
		this.redraw_after_blur = false;
		const st = this.ctl.state;
		if (!loc) {
			if (st.location_error) this.failed(el, st.location_error, () => this.ctl.load_location(), __("Could not load this bin."));
			else this.sf.state.render(el, "loading", { title: __("Loading the bin…") });
			return;
		}
		const perms = this.perms();
		const warehouse = loc.warehouse;
		const notes = [];
		if (perms.boneyard && perms.boneyard === warehouse) {
			notes.push(__("This is the boneyard: stock here lists because it is on hand, and nothing is assigned to it."));
		}
		if (loc.truncated) notes.push(__("This bin lists more items than the page shows. Search with Add existing item to find one."));
		const note = notes.map((n) => `<p class="bm-note">${this.esc(n)}</p>`).join("");
		if (!loc.items || !loc.items.length) {
			el.innerHTML = note + this.sf.state.html("empty", { title: __("Nothing is on this bin yet.") });
			return;
		}
		const rows = loc.items
			.map((r) => {
				const chips = this.bm
					.why_listed(r)
					.map((w) => this.sf.chip(__(w.label), w.tone))
					.join(" ");
				const buttons = st.boot
					? this.bm
							.row_actions(r, perms, warehouse)
							.map(
								(a) =>
									`<button type="button" class="btn btn-xs btn-default" data-act="${a}" data-item="${this.esc(r.item_code)}">${this.esc(
										__(this.bm.ACTION_LABELS[a])
									)}</button>`
							)
							.join(" ")
					: "";
				return `<tr>
					<td class="bm-item">${this.item_cell(r, perms)}</td>
					<td class="bm-qty">${this.esc(this.bm.amount(r.on_hand, r.stock_uom))}</td>
					${this.reorder_shown() ? `<td class="bm-reorder">${window.sf_reorder.cell_html((v) => this.esc(v), (t) => __(t), this.reorder.view(r.item_code), r.item_code, r.item_name)}</td>` : ""}
					<td class="bm-why">${chips}</td>
					<td class="bm-row-actions">${buttons}</td>
				</tr>`;
			})
			.join("");
		el.innerHTML = `${note}<table class="bm-table">
			<thead><tr>
				<th scope="col">${this.esc(__("Item"))}</th>
				<th scope="col" class="bm-qty">${this.esc(__("On hand"))}</th>
				${this.reorder_shown() ? `<th scope="col">${this.esc(__("Reorder"))}</th>` : ""}
				<th scope="col">${this.esc(__("Listed because"))}</th>
				<th scope="col"><span class="bm-sr">${this.esc(__("Actions"))}</span></th>
			</tr></thead>
			<tbody>${rows}</tbody>
		</table>`;
	}

	// The item's name and code, as one link to its Item record where the person may open Items (TASK-2026-02918).
	// A real anchor to the Desk form, so the Desk's own link handling routes it in place with a history entry
	// (Back returns to the bin), Ctrl/Cmd/middle click opens a new tab, and the keyboard reaches it. The href is
	// frappe.utils.get_form_link (/desk/item/<encoded code>, never /app, which is a full page load on v16); the
	// text is escaped like every other cell. It sits outside every [data-act], so it triggers no row action.
	item_cell(r, perms) {
		const text = `<span class="bm-item-name">${this.esc(r.item_name)}</span><span class="bm-item-code">${this.esc(r.item_code)}</span>`;
		if (perms && perms.can_open_items === false) return `<div class="bm-item-plain">${text}</div>`;
		const href = frappe.utils.get_form_link("Item", r.item_code);
		return `<a class="bm-item-link" href="${this.esc(href)}" title="${this.esc(__("Open the Item"))}">${text}</a>`;
	}

	draw_recent() {
		const el = this.$root.find(".bm-recent-list")[0];
		if (!el) return;
		const rows = this.bm.bin_recent(this.ctl.state.recent, this.here());
		if (!rows.length) {
			el.innerHTML = `<p class="bm-muted">${this.esc(__("Nothing yet. Every change you make here is listed, and can be undone for a while."))}</p>`;
			return;
		}
		el.innerHTML = `<ul class="bm-recent-rows">${rows
			.map((r) => {
				const when = r.posted_at ? frappe.datetime.comment_when(r.posted_at) : "";
				const status =
					r.status === "Undone"
						? this.sf.chip(__("Undone"), "neutral")
						: r.can_undo
						? `<button type="button" class="btn btn-xs btn-default" data-act="undo" data-log="${this.esc(r.name)}">${this.esc(__("Undo"))}</button>`
						: `<span class="bm-muted" title="${this.esc(r.undo_refusal || "")}">${this.esc(__("Can't undo"))}</span>`;
				return `<li class="bm-recent-row"><span class="bm-recent-what">${this.esc(this.bm.describe_log(r))}</span>
					<span class="bm-recent-when">${when}</span> ${status}</li>`;
			})
			.join("")}</ul>`;
	}

	// ------------------------------------------------------------------ the views beyond one bin (v1.806.1)

	// The tabs across the top of every screen but a bin and one job. Each is a route, so a tab is one
	// frappe.set_route and one history entry.
	tabs_html() {
		const on = this.bm.tab_of(this.ctl.state.target);
		const tabs = this.bm.TABS.map((t) => {
			const count = t.screen === "problems" && this.problem_count() ? ` <span class="bm-tab-count">${this.esc(this.problem_count())}</span>` : "";
			return `<button type="button" class="bm-tab ${t.screen === on ? "is-on" : ""}" role="tab" aria-selected="${t.screen === on}"
				data-act="tab" data-tab="${this.esc(t.screen)}">${this.esc(__(t.label))}${count}</button>`;
		}).join("");
		return `<div class="bm-tabs" role="tablist" aria-label="${this.esc(__("Bin Manager views"))}">${tabs}</div>`;
	}

	// How many problems the last read found, once the Problems view has been opened this visit.
	problem_count() {
		const held = this.ctl.state.problems;
		return held && held.data ? this.bm.views.problem_total(held.data) : 0;
	}

	money(value) {
		try {
			if (typeof format_currency === "function") return format_currency(value);
		} catch (e) {
			// The plain formatter below.
		}
		return this.bm.views.money(value);
	}

	// The picker's filters, under its search box: the kinds of place, the quick filters, a job, an item.
	// Drawn again on every change; the search box above is not, so typing is never interrupted.
	draw_filters() {
		const el = this.$root.find(".bm-filters")[0];
		if (!el) return;
		const st = this.ctl.state;
		const v = this.bm.views;
		const bins = this.boot_bins();
		if (!st.boot) {
			el.innerHTML = "";
			return;
		}
		// A select being used is left alone: replacing it would close its list under the pointer.
		const active = document.activeElement;
		if (active && active.classList && active.classList.contains("bm-project-filter") && el.contains(active)) return;
		const f = st.filters;
		const chip = (act, attr, key, label, count, on) =>
			`<button type="button" class="bm-chipbtn ${on ? "is-on" : ""}" aria-pressed="${on}" data-act="${act}" ${attr}="${this.esc(key)}">${this.esc(
				label
			)}${count === null ? "" : ` <span class="bm-chip-count">${this.esc(count)}</span>`}</button>`;
		const kinds = v.kind_options(bins);
		const kind_row =
			kinds.length > 1
				? `<div class="bm-chiprow" role="group" aria-label="${this.esc(__("Kind of place"))}">${chip("kind", "data-kind", "", __("All"), bins.length, !f.kind)}${kinds
						.map((k) => chip("kind", "data-kind", k.key, __(k.label), k.count, f.kind === k.key))
						.join("")}</div>`
				: "";
		const quick_row = `<div class="bm-chiprow" role="group" aria-label="${this.esc(__("Quick filters"))}">${v.QUICK.map((q) =>
			chip("quick", "data-quick", q.key, __(q.label), null, f.quick === q.key)
		).join("")}</div>`;
		const jobs = v.project_options(bins);
		// A job the address names stays in the list even when it has no site, so the box says what is filtering.
		if (f.project && !jobs.some((j) => j.project === f.project)) jobs.push({ project: f.project, project_name: f.project, customer: "" });
		const job_box = jobs.length
			? `<label class="bm-filterbox">${this.esc(__("Job"))}
				<select class="form-control input-xs bm-project-filter" aria-label="${this.esc(__("Show one job's site"))}">
					<option value="">${this.esc(__("Any job"))}</option>
					${jobs
						.map((j) => `<option value="${this.esc(j.project)}" ${j.project === f.project ? "selected" : ""}>${this.esc(v.project_label(j))}</option>`)
						.join("")}
				</select></label>`
			: "";
		const item = st.item;
		const item_name = (item && item.data && item.data.item_name) || f.item;
		const item_box = f.item
			? `<span class="bm-filterbox bm-item-on">${this.esc(__("Item"))} <b>${this.esc(item_name)}</b>
				<button type="button" class="btn btn-xs btn-default" data-act="item-clear" aria-label="${this.esc(__("Stop filtering by this item"))}">✕</button></span>`
			: `<button type="button" class="btn btn-xs btn-default" data-act="item-filter">${this.esc(__("Filter by item…"))}</button>`;
		const clear = v.has_filters(f) ? `<button type="button" class="btn btn-xs btn-default" data-act="clear-filters">${this.esc(__("Clear filters"))}</button>` : "";
		const shown = this.ctl.shown_bins().length;
		const count = v.has_filters(f) ? __("{0} of {1} places", [shown, bins.length]) : __("{0} places", [bins.length]);
		const job_link = f.project
			? `<button type="button" class="btn btn-xs btn-default" data-act="project" data-project="${this.esc(f.project)}">${this.esc(
					__("See this job's shelf and site")
			  )}</button>`
			: "";
		el.innerHTML = `${kind_row}${quick_row}<div class="bm-filterrow">${job_box}${item_box}${job_link}${clear}<span class="bm-count" aria-live="polite">${this.esc(
			count
		)}</span></div>`;
	}

	// One row of the picker: the place, what kind it is when that is not a plain shop bin, whose site it is,
	// and its counts (with what the filtered item is doing there first).
	bin_row_html(b) {
		const v = this.bm.views;
		const kind = v.bin_kind(b);
		const tones = { project_shelf: "service", job_site: "good", boneyard: "warn", vehicle: "neutral" };
		const labels = { project_shelf: __("Project Shelf"), job_site: __("Job site"), boneyard: __("Boneyard"), vehicle: __("Vehicle") };
		const chip = labels[kind] ? this.sf.chip(labels[kind], tones[kind]) : "";
		const job = b.project ? `<span class="bm-bin-job">${this.esc(v.project_label(b))}</span>` : "";
		const done = b.project_closed ? this.sf.chip(__("Job finished"), "warn") : "";
		const item = this.ctl.state.item;
		const location = item && item.data && this.ctl.state.filters.item ? item.data.locations[b.warehouse] : null;
		return `<li><button type="button" class="bm-bin" data-act="bin" data-bin="${this.esc(b.warehouse)}">
			<span class="bm-bin-name">${this.esc(b.warehouse_name)}</span> ${chip} ${done} ${job}
			<span class="bm-bin-counts">${this.esc(v.bin_summary(b, location))}</span>
		</button></li>`;
	}

	// "Filter by item…": find an item, then show only the places that hold it or have it assigned.
	item_filter_dialog() {
		let timer = null;
		let seq = 0;
		const d = new frappe.ui.Dialog({
			title: __("Show the places for one item"),
			fields: [
				{ fieldtype: "Data", fieldname: "query", label: __("Find the item"), placeholder: __("Part number, name or barcode") },
				{ fieldtype: "HTML", fieldname: "results" },
			],
		});
		const results = d.fields_dict.results.$wrapper;
		const search = () => {
			const query = (d.get_value("query") || "").trim();
			const mine = ++seq;
			if (!query) {
				results.empty();
				return;
			}
			results.html(`<p class="bm-muted">${this.esc(__("Searching…"))}</p>`);
			this.ctl.search_items(query).then(
				(rows) => {
					if (mine !== seq) return;
					rows = rows || [];
					results.html(
						rows.length
							? `<ul class="bm-pick-list">${rows
									.map(
										(r) =>
											`<li><button type="button" class="bm-pick" data-bm-item="${this.esc(r.item_code)}">${this.esc(r.item_name)}
											<span class="bm-muted">${this.esc(r.item_code)}</span></button></li>`
									)
									.join("")}</ul>`
							: `<p class="bm-muted">${this.esc(__("No item matches."))}</p>`
					);
				},
				(err) => {
					if (mine === seq) results.html(`<p class="bm-dialog-msg">${this.esc((err && err.message) || "")}</p>`);
				}
			);
		};
		d.fields_dict.query.$input.on("input", () => {
			clearTimeout(timer);
			timer = setTimeout(search, BM_SEARCH_DELAY_MS);
		});
		results.on("click", "[data-bm-item]", (e) => {
			const code = e.currentTarget.getAttribute("data-bm-item");
			d.hide();
			this.ctl.set_filters({ item: code });
		});
		d.show();
		return d;
	}

	// ---- By project: the jobs that have material somewhere

	render_projects() {
		this.page.set_title(__("Bin Manager"));
		if (this.drawn !== "projects") {
			this.drawn = "projects";
			this.$root.html(`
				<div class="bm-view">
					${this.tabs_html()}
					<div class="bm-toolbar">
						<input type="search" class="form-control bm-project-search" autocomplete="off"
							placeholder="${this.esc(__("Find a job: type part of its name or customer"))}"
							aria-label="${this.esc(__("Find a job"))}">
					</div>
					<p class="bm-note">${this.esc(__("Jobs with material on the Project Shelf or at a job site. Pick one to see both, and to move items between them."))}</p>
					<div class="bm-list"></div>
				</div>`);
			this.$root.find(".bm-project-search").val(this.ctl.state.project_filter || "");
		}
		this.draw_projects();
	}

	draw_projects() {
		const list = this.$root.find(".bm-list")[0];
		if (!list) return;
		const st = this.ctl.state;
		if (!st.projects) {
			if (st.projects_error) this.failed(list, st.projects_error, () => this.ctl.load_projects(), __("Could not load the jobs."));
			else this.sf.state.render(list, "loading", { title: __("Loading the jobs…") });
			return;
		}
		const v = this.bm.views;
		const rows = v.filter_projects(st.projects, st.project_filter);
		if (!rows.length) {
			this.sf.state.render(list, "empty", {
				title: st.project_filter ? __("No job matches {0}.", [st.project_filter]) : __("No job has material on the Project Shelf or at a job site yet."),
			});
			return;
		}
		list.innerHTML = `<ul class="bm-bins bm-jobs">${rows
			.map(
				(r) => `<li><button type="button" class="bm-bin" data-act="project" data-project="${this.esc(r.project)}">
					<span class="bm-bin-name">${this.esc(v.project_label(r))}</span> ${r.closed ? this.sf.chip(__("Job finished"), "warn") : ""}
					<span class="bm-bin-job">${this.esc(r.project)}</span>
					<span class="bm-bin-counts">${this.esc(v.project_counts_text(r))}</span>
				</button></li>`
			)
			.join("")}</ul>`;
	}

	// ---- By project: one job's Project Shelf and job site, side by side

	render_project() {
		const st = this.ctl.state;
		const project = st.target.project;
		const held = st.project && st.project.project === project ? st.project : null;
		const data = held && held.data;
		this.page.set_title(data ? data.project.project_name : __("Bin Manager"));
		if (this.drawn !== `project:${project}`) {
			this.drawn = `project:${project}`;
			this.$root.html(`
				<div class="bm-view bm-projectview">
					<div class="bm-head">
						<button type="button" class="btn btn-default btn-sm bm-back" data-act="leave-project">← ${this.esc(__("All jobs"))}</button>
						<div class="bm-title">
							<h2 class="bm-name"></h2>
							<div class="bm-trail"></div>
						</div>
					</div>
					<div class="bm-notice"></div>
					<div class="bm-project-body"></div>
				</div>`);
		}
		this.$root.find(".bm-name").text(data ? data.project.project_name : project);
		const sub = data ? [data.project.project, data.project.customer, data.project.status].filter(Boolean).join(" · ") : "";
		this.$root.find(".bm-trail").text(sub);
		this.draw_notice();
		const body = this.$root.find(".bm-project-body")[0];
		if (!data) {
			if (held && held.error) this.failed(body, held.error, () => this.ctl.load_project(), __("Could not load this job."));
			else this.sf.state.render(body, "loading", { title: __("Loading the job…") });
			return;
		}
		const sections = this.bm.views.project_sections(data, this.perms());
		const parts = [];
		if (data.project.closed) parts.push(`<p class="bm-note">${this.esc(__("This job is finished. What is still held for it can be brought back to a shelf."))}</p>`);
		if (sections.shelf) parts.push(this.project_section_html(sections.shelf));
		else parts.push(`<p class="bm-note">${this.esc(__("No Project Shelf is set up yet, so there is nothing to show for the shelf."))}</p>`);
		if (sections.site) parts.push(this.project_section_html(sections.site));
		else {
			parts.push(
				`<section class="bm-section"><h3 class="bm-section-title">${this.esc(__("At the job site"))}</h3><p class="bm-muted">${this.esc(
					data.send_refusal || __("This job has no site yet. It is opened the first time something is sent to it.")
				)}</p></section>`
			);
		}
		body.innerHTML = parts.join("");
	}

	project_section_html(section) {
		const priced = section.rows.some((r) => r.value !== null && r.value !== undefined);
		const open = `<button type="button" class="btn btn-xs btn-default" data-act="bin" data-bin="${this.esc(section.warehouse)}">${this.esc(
			__("Open {0}", [section.warehouse_name])
		)}</button>`;
		const blocked =
			(section.blocked ? `<p class="bm-muted">${this.esc(section.blocked)}</p>` : "") +
			(section.note ? `<p class="bm-note">${this.esc(__(section.note))}</p>` : "");
		if (!section.rows.length) {
			return `<section class="bm-section"><h3 class="bm-section-title">${this.esc(__(section.title))} ${open}</h3>${blocked}<p class="bm-muted">${this.esc(
				__(section.empty)
			)}</p></section>`;
		}
		const rows = section.rows
			.map((r) => {
				const button = section.move
					? `<button type="button" class="btn btn-xs btn-default" data-act="project-move" data-move="${this.esc(section.move.key)}"
						data-section="${this.esc(section.key)}" data-item="${this.esc(r.item_code)}">${this.esc(__(section.move.label))}</button>`
					: "";
				return `<tr>
					<td class="bm-item">${this.item_cell(r, this.perms())}</td>
					<td class="bm-qty">${this.esc(this.bm.amount(r.on_hand, r.stock_uom))}${
					r.recorded ? ` <span class="bm-muted">${this.esc(__("of {0} on record", [this.bm.plain(r.recorded)]))}</span>` : ""
				}</td>
					${priced ? `<td class="bm-qty">${r.value === null || r.value === undefined ? "" : this.esc(this.money(r.value))}</td>` : ""}
					<td class="bm-row-actions">${button}</td>
				</tr>`;
			})
			.join("");
		return `<section class="bm-section"><h3 class="bm-section-title">${this.esc(__(section.title))} ${open}</h3>${blocked}
			<table class="bm-table">
				<thead><tr>
					<th scope="col">${this.esc(__("Item"))}</th>
					<th scope="col" class="bm-qty">${this.esc(__("Quantity"))}</th>
					${priced ? `<th scope="col" class="bm-qty">${this.esc(__("Value"))}</th>` : ""}
					<th scope="col"><span class="bm-sr">${this.esc(__("Actions"))}</span></th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table></section>`;
	}

	// The row a By project button names: its section's, by item code.
	project_row(section_key, code) {
		const held = this.ctl.state.project;
		const sections = this.bm.views.project_sections(held && held.data, this.perms());
		const section = sections[section_key];
		return section ? section.rows.find((r) => r.item_code === code) || null : null;
	}

	// "Send to job site" / "Bring back to the shelf": the page's ordinary Move, between the two places.
	project_move_dialog(direction, row) {
		const held = this.ctl.state.project;
		const move = this.bm.views.project_move(direction, held && held.data);
		if (!move || !row) return;
		const to_site = direction === "to_site";
		const here = this.bm.amount(row.on_hand, row.stock_uom);
		const about = to_site
			? move.needs_site
				? __("{0} on the Project Shelf for this job. This job has no site yet: sending opens it.", [here])
				: __("{0} on the Project Shelf for this job.", [here])
			: __("{0} at the job site.", [here]);
		this.save_dialog({
			kind: "move",
			row,
			title: to_site ? __("Send {0} to the job site", [this.safe(row.item_name)]) : __("Bring {0} back to the Project Shelf", [this.safe(row.item_name)]),
			primary_label: to_site ? __("Send") : __("Bring back"),
			fields: [
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: this.safe(about),
				},
			],
			values: (d, picked) => ({ qty: picked && picked.qty !== undefined ? picked.qty : d.get_value("qty") }),
			submit: (given, ref) => this.ctl.project_submit(direction, row, given, ref),
		});
	}

	// ---- Job sites: every site with its job, item count and value

	render_sites() {
		this.page.set_title(__("Bin Manager"));
		if (this.drawn !== "sites") {
			this.drawn = "sites";
			this.$root.html(`<div class="bm-view">${this.tabs_html()}<div class="bm-list"></div></div>`);
		}
		const list = this.$root.find(".bm-list")[0];
		const st = this.ctl.state;
		if (!st.boot) {
			if (st.boot_error) this.failed(list, st.boot_error, () => this.ctl.load_boot(true), __("Could not load the job sites."));
			else this.sf.state.render(list, "loading", { title: __("Loading the job sites…") });
			return;
		}
		const v = this.bm.views;
		const rows = v.site_rows(st.boot.bins || []);
		if (!rows.length) {
			this.sf.state.render(list, "empty", {
				title: __("There are no job sites yet."),
				body: __("A job's site is opened the first time something is sent to the job: from By project here, or Move to… then A job site in Stock Scan."),
			});
			return;
		}
		const totals = v.site_totals(rows);
		const priced = totals.value !== null;
		const body = rows
			.map((r) => {
				const flag = v.site_flag(r);
				const job = r.project
					? `<button type="button" class="bm-linkbtn" data-act="project" data-project="${this.esc(r.project)}">${this.esc(r.project_name)}</button>`
					: `<span class="bm-muted">${this.esc(__("No job"))}</span>`;
				return `<tr>
					<td><button type="button" class="bm-linkbtn" data-act="bin" data-bin="${this.esc(r.warehouse)}">${this.esc(r.warehouse_name)}</button></td>
					<td>${job} ${flag && r.project ? this.sf.chip(__(flag.label), flag.tone) : ""}</td>
					<td>${this.esc(r.customer)}</td>
					<td class="bm-qty">${this.esc(r.items)}</td>
					${priced ? `<td class="bm-qty">${r.value === null ? "" : this.esc(this.money(r.value))}</td>` : ""}
				</tr>`;
			})
			.join("");
		list.innerHTML = `<table class="bm-table bm-sites">
			<thead><tr>
				<th scope="col">${this.esc(__("Job site"))}</th>
				<th scope="col">${this.esc(__("Job"))}</th>
				<th scope="col">${this.esc(__("Customer"))}</th>
				<th scope="col" class="bm-qty">${this.esc(__("Items"))}</th>
				${priced ? `<th scope="col" class="bm-qty">${this.esc(__("Value"))}</th>` : ""}
			</tr></thead>
			<tbody>${body}</tbody>
			<tfoot><tr>
				<th scope="row" colspan="3">${this.esc(__("{0} job sites", [totals.sites]))}</th>
				<td class="bm-qty">${this.esc(totals.items)}</td>
				${priced ? `<td class="bm-qty">${this.esc(this.money(totals.value))}</td>` : ""}
			</tr></tfoot>
		</table>`;
	}

	// ---- Problems: where the records and the shelves disagree

	render_problems() {
		this.page.set_title(__("Bin Manager"));
		if (this.drawn !== "problems") {
			this.drawn = "problems";
			this.$root.html(`<div class="bm-view">${this.tabs_html()}<div class="bm-list"></div></div>`);
		}
		const list = this.$root.find(".bm-list")[0];
		const held = this.ctl.state.problems;
		if (!held || !held.data) {
			if (held && held.error) this.failed(list, held.error, () => this.ctl.load_problems(), __("Could not load the problems."));
			else this.sf.state.render(list, "loading", { title: __("Checking the bins…") });
			return;
		}
		const sections = this.bm.views.problem_sections(held.data);
		if (!sections.some((s) => s.total)) {
			this.sf.state.render(list, "empty", { title: __("Nothing to fix. Every assigned bin has stock and every item on hand has a bin.") });
			return;
		}
		list.innerHTML = sections
			.map((s) => {
				const head = `<h3 class="bm-section-title">${this.esc(__(s.title))} <span class="bm-tab-count">${this.esc(s.total)}</span></h3>`;
				if (!s.total) return `<section class="bm-section">${head}<p class="bm-muted">${this.esc(__("None."))}</p></section>`;
				const by_job = s.by === "job";
				const rows = s.rows
					.map((r) => {
						const where = by_job
							? `<button type="button" class="bm-linkbtn" data-act="project" data-project="${this.esc(r.project)}">${this.esc(r.project_name || r.project)}</button>
								<span class="bm-muted">${this.esc(r.status || "")}</span>`
							: `<button type="button" class="bm-linkbtn" data-act="bin" data-bin="${this.esc(r.warehouse)}">${this.esc(r.warehouse_name || r.warehouse)}</button>`;
						const qty = r.qty === undefined || r.qty === null ? "" : this.esc(this.bm.amount(r.qty, r.stock_uom));
						return `<tr>
							<td>${where}</td>
							<td class="bm-item">${this.item_cell(r, this.perms())}</td>
							<td class="bm-qty">${qty}</td>
						</tr>`;
					})
					.join("");
				const cut = s.truncated ? `<p class="bm-note">${this.esc(__("Showing the first {0} of {1}.", [s.rows.length, s.total]))}</p>` : "";
				return `<section class="bm-section">${head}<p class="bm-muted">${this.esc(__(s.hint))}</p>
					<table class="bm-table">
						<thead><tr>
							<th scope="col">${this.esc(by_job ? __("Job") : __("Place"))}</th>
							<th scope="col">${this.esc(__("Item"))}</th>
							<th scope="col" class="bm-qty">${this.esc(__("On hand"))}</th>
						</tr></thead>
						<tbody>${rows}</tbody>
					</table>${cut}</section>`;
			})
			.join("");
		// The tab's count is part of the skeleton, drawn before the answer came.
		const tab = this.$root.find('.bm-tab[data-tab="problems"]');
		if (tab.length && !tab.find(".bm-tab-count").length) tab.append(` <span class="bm-tab-count">${this.esc(this.problem_count())}</span>`);
	}

	// ------------------------------------------------------------------ saves without a dialog

	undo(log, el) {
		if (!log) return;
		if (el) el.disabled = true;
		this.ctl.undo(log).catch((err) => {
			this.ctl.state.notice = { message: err.message || __("Could not undo."), error: true };
			this.render();
		});
	}

	// "Keep on this bin": one click, undoable from the notice. Its reference is the controller's
	// (quick): the same one again after a dropped reply, so a second click cannot save twice.
	keep(row, el) {
		if (el) el.disabled = true;
		this.ctl.quick("keep", row, {}).catch((err) => {
			this.ctl.state.notice = { message: err.message || __("Could not save."), error: true };
			this.render();
		});
	}

	// ------------------------------------------------------------------ dialogs

	// A dialog whose primary action is one save. Its reference is minted when it opens and sent again
	// on a retry the server may already have saved (logic.keep_ref); a refusal is shown inside it.
	//
	// THE VALUES ARE THE ARGUMENT, NEVER A SECOND READING (TASK-2026-02912, "rejects 0 as a valid
	// quantity"). Frappe's primary button reads the dialog once (FieldGroup.get_values) and passes the
	// result to the action; and while it reads, a required field whose value is falsy (`!f.value`: a typed
	// 0) is refreshed, which sets the box from `me.value || null` and so BLANKS the 0. A page that calls
	// get_values()/get_value() again then finds no quantity and says "Enter the quantity you counted,
	// 0 or more." to a person who just typed 0. So the action uses `picked`, and puts a refused 0 back in
	// its box (restore_zeros) so the person sees what they typed.
	save_dialog({ kind, row, title, fields, primary_label, values, on_build, submit }) {
		let ref = this.ctl.new_ref();
		let busy = false;
		const d = new frappe.ui.Dialog({
			title,
			fields: [{ fieldtype: "HTML", fieldname: "bm_msg" }].concat(fields || []),
			primary_action_label: primary_label,
			primary_action: (picked) => {
				if (busy) return;
				busy = true;
				this.dialog_message(d, "");
				const button = d.get_primary_btn();
				button.prop("disabled", true);
				const given = values ? values(d, picked) : picked || d.get_values(true) || {};
				// `submit` is a save the controller makes another way (By project's moves): same promise.
				(submit ? submit(given, ref) : this.ctl.submit(kind, row, given, ref)).then(
					(out) => {
						busy = false;
						button.prop("disabled", false);
						// Not a save: the server asked what one unit costs (an item ERPNext has no cost for).
						if (out && out.prompt) {
							// Asked again after a price was sent: say so here, rather than closing and reopening.
							if ("rate" in given) {
								this.dialog_message(d, __("The server still has no cost for this item. Ask a Stock Manager to set its Valuation Rate."));
								return;
							}
							d.hide();
							this.cost_dialog(kind, row, given, out.prompt);
							return;
						}
						d.hide();
						// A toast as well as the notice above the list: the dialog is gone and the row has changed.
						if (out && out.message) frappe.show_alert({ message: this.safe(out.message), indicator: "green" }, 6);
					},
					(err) => {
						busy = false;
						button.prop("disabled", false);
						if (!err.keep_ref) ref = this.ctl.new_ref();
						this.restore_zeros(d, picked);
						this.dialog_message(d, err.message || __("The server refused this request."));
					}
				);
			},
		});
		if (on_build) on_build(d);
		d.show();
		return d;
	}

	// "What does one cost?" (TASK-2026-02914): a count up from an empty bin for an item with no cost on
	// record. ERPNext would refuse the Stock Reconciliation, so the server posted nothing and asked. The
	// dialog pre-fills its suggestion with where it came from, says the cost is of ONE stock unit, and
	// counts again with `rate`. The box is not `reqd` (Frappe blanks a required 0 while reading it, see
	// save_dialog), and logic.check_values refuses a blank, a zero, a negative and a cost above the cap in
	// words, inside the dialog.
	cost_dialog(kind, row, given, prompt) {
		const copy = this.bm.costPromptText(prompt, given.qty, "count in");
		this.save_dialog({
			kind,
			row,
			title: this.safe(copy.title),
			primary_label: copy.button,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "bm_cost_text",
					options: `<p>${this.esc(copy.lead)}</p><p class="bm-muted">${this.esc(copy.per)}</p><p class="bm-muted">${this.esc(copy.hint)}</p><p class="bm-muted">${this.esc(copy.note)}</p>`,
				},
				{ fieldtype: "Float", fieldname: "rate", label: this.safe(copy.label), default: copy.value === "" ? undefined : Number(copy.value) },
			],
			// Frappe's Float hands back null for a blank box and leaves it out of `picked`: the key is always
			// set, so logic.check_values asks for the cost instead of the count going out without one.
			values: (d, picked) => {
				const rate = picked && picked.rate !== undefined ? picked.rate : d.get_value("rate");
				return Object.assign({}, given, { rate: rate === undefined ? null : rate, max_value: prompt.max_value });
			},
		});
	}

	// Frappe blanked every required box holding a 0 while it read the dialog (see save_dialog): put the
	// zeros back, so a refusal does not also empty the box.
	restore_zeros(d, picked) {
		Object.keys(picked || {}).forEach((name) => {
			if (picked[name] === 0 && d.fields_dict[name]) d.set_value(name, 0);
		});
	}

	dialog_message(d, text) {
		const field = d.fields_dict.bm_msg;
		if (!field) return;
		field.$wrapper.html(text ? `<div class="bm-dialog-msg" role="alert">${this.esc(text)}</div>` : "");
	}

	count_dialog(row) {
		this.save_dialog({
			kind: "count",
			row,
			title: __("Change the quantity of {0}", [this.safe(row.item_name)]),
			primary_label: __("Save count"),
			fields: [
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Counted quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __(
						"The system shows {0}. Saving posts a Stock Reconciliation: a count correction, not parts used, so it charges no job and can go up as well as down.",
						[this.safe(this.bm.amount(row.on_hand, row.stock_uom))]
					),
				},
			],
		});
	}

	// "Which job?" in a Move or Send to boneyard dialog (v1.806.1, for the Project Shelf's v1.804.0).
	// Everything on the Project Shelf belongs to a job, and whose it is is read from the job on each
	// stock ledger row, so a move onto or off the shelf has to name one or the shelf's by-project
	// numbers drift. `rule` says when (logic.shelf_move): "off" lists the jobs that hold the item
	// there and starts on the only one when the server says those jobs account for every unit of it
	// (shelf_jobs_exact); otherwise, and always for "onto", the person picks, by name if need be.
	// With no rule the picker draws nothing and gives no job: the dialog is the one it always was.
	//
	// Drawn into an HTML field, whose wrapper always exists (a Data field that starts hidden has no
	// input to listen on: see move_dialog). The search box is drawn once per question and only the
	// results under it are replaced, so typing is never interrupted.
	job_picker(d, row) {
		const wrap = d.fields_dict.bm_job.$wrapper;
		const state = { rule: null, chosen: null, jobs: [], loaded: false, loading: false };
		const names = {};
		let timer = null;
		let seq = 0;
		const remember = (jobs) => (jobs || []).forEach((j) => (names[j.project] = j.project_name || j.project));
		const list = (jobs, label) =>
			`<ul class="bm-pick-list">${jobs
				.map((j) => `<li><button type="button" class="bm-pick" data-bm-job="${this.esc(j.project)}">${this.esc(label(j))}</button></li>`)
				.join("")}</ul>`;
		const draw = () => {
			if (!state.rule) {
				wrap.html("");
				return;
			}
			if (state.chosen) {
				wrap.html(
					`<div class="bm-chosen">${this.esc(state.rule === "off" ? __("Bought for") : __("Set aside for"))} <b>${this.esc(
						names[state.chosen] || state.chosen
					)}</b> <button type="button" class="btn btn-xs btn-default" data-bm-job-change="1">${this.esc(__("Change"))}</button></div>`
				);
				return;
			}
			let held = "";
			if (state.rule === "off") {
				if (state.loading) held = `<p class="bm-muted">${this.esc(__("Finding the jobs that hold it…"))}</p>`;
				else if (state.jobs.length) held = list(state.jobs, (j) => this.bm.job_label(j, row.stock_uom));
				else held = `<p class="bm-muted">${this.esc(__("No job is on record for this item on the shelf. Find the job by name."))}</p>`;
			}
			wrap.html(
				`<div class="bm-jobpick"><p class="bm-preview-head">${this.esc(__(this.bm.JOB_QUESTIONS[state.rule]))}</p>
				<p class="bm-muted">${this.esc(__("Everything on the Project Shelf belongs to a job, so this move names it."))}</p>
				${held}
				<input type="search" class="form-control bm-job-search" autocomplete="off"
					placeholder="${this.esc(__("Find a job: type part of its name"))}" aria-label="${this.esc(__("Find a job"))}">
				<div class="bm-job-results"></div></div>`
			);
		};
		const load = () => {
			state.loading = true;
			this.ctl.shelf_jobs(row.item_code).then((answer) => {
				state.loading = false;
				state.loaded = true;
				state.jobs = answer.jobs || [];
				remember(state.jobs);
				if (answer.preselect && !state.chosen) state.chosen = answer.preselect.project;
				draw();
			});
		};
		const search = (query) => {
			const mine = ++seq;
			const results = wrap.find(".bm-job-results");
			if (!query) {
				results.html("");
				return;
			}
			results.html(`<p class="bm-muted">${this.esc(__("Searching…"))}</p>`);
			this.ctl.search_jobs(query).then(
				(rows) => {
					if (mine !== seq) return;
					rows = rows || [];
					remember(rows);
					results.html(
						rows.length
							? list(rows, (j) => (j.customer ? `${j.project_name || j.project} · ${j.customer}` : j.project_name || j.project))
							: `<p class="bm-muted">${this.esc(__("No job matches."))}</p>`
					);
				},
				(err) => {
					if (mine === seq) results.html(`<p class="bm-dialog-msg">${this.esc((err && err.message) || "")}</p>`);
				}
			);
		};
		wrap.on("click", "[data-bm-job]", (e) => {
			state.chosen = e.currentTarget.getAttribute("data-bm-job");
			draw();
		});
		wrap.on("click", "[data-bm-job-change]", () => {
			state.chosen = null;
			draw();
		});
		wrap.on("input", ".bm-job-search", (e) => {
			const query = String(e.currentTarget.value || "").trim();
			clearTimeout(timer);
			timer = setTimeout(() => search(query), BM_SEARCH_DELAY_MS);
		});
		return {
			set_rule: (rule) => {
				if (rule === state.rule) return;
				state.rule = rule || null;
				if (state.rule === "off" && !state.loaded && !state.loading) load();
				draw();
			},
			rule: () => state.rule,
			project: () => (state.rule && state.chosen ? state.chosen : ""),
		};
	}

	boneyard_dialog(row) {
		const perms = this.perms();
		let picker = null;
		this.save_dialog({
			kind: "boneyard",
			row,
			title: __("Send {0} to the boneyard", [this.safe(row.item_name)]),
			primary_label: __("Send to boneyard"),
			// From the Project Shelf the trip names the job the stock was bought for (job_picker).
			values: (d, picked) => Object.assign({}, picked || {}, { project: picker.project(), job_rule: picker.rule() }),
			on_build: (d) => {
				picker = this.job_picker(d, row);
				picker.set_rule(this.ctl.job_rule(this.here(), perms.boneyard));
			},
			fields: [
				{ fieldtype: "HTML", fieldname: "bm_job" },
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __("Moves the stock to {0}. If that empties this bin, the item comes off it.", [this.safe(perms.boneyard_name)]),
				},
			],
		});
	}

	move_dialog(row) {
		const perms = this.perms();
		let chosen = null;
		let make_home = this.bm.make_home_after_qty(null, row.on_hand, row.on_hand);
		// "Make <bin> this item's bin" starts hidden, and Frappe builds a control's input only while the control
		// is shown (ControlBaseInput.refresh_input: make_input() is inside `if (disp_status != "None")`, and a
		// hidden df is "None"). So its $input does not exist when the dialog is built, and listening on it then
		// threw, which stopped the dialog from ever opening: the Move button did nothing (TASK-2026-02919,
		// Parker's report, on every release since v1.693.0). The listener is attached the first time the box
		// is shown, once its input exists. A programmatic set_value does not fire a DOM `change`, so only the
		// person's own tick counts as touching it.
		let home_bound = false;
		const bind_home = (d) => {
			const input = d.fields_dict.make_home.$input;
			if (home_bound || !input) return;
			home_bound = true;
			input.on("change", () => {
				make_home = this.bm.make_home_touched(input.prop("checked"));
			});
		};
		// "Which job?" when the move is onto or off the Project Shelf (job_picker); decided again each
		// time the destination changes, since a job site needs none.
		let picker = null;
		const sync_job = () => picker && picker.set_rule(this.ctl.job_rule(this.here(), chosen && chosen.warehouse));
		const sync_home = (d) => {
			const offered = this.bm.make_home_offered(perms, chosen && chosen.warehouse, chosen && this.bm.views.bin_kind(chosen));
			d.set_df_property("make_home", "hidden", offered ? 0 : 1);
			if (!offered) return;
			bind_home(d);
			d.set_df_property("make_home", "label", __("Make {0} this item's bin", [this.safe(chosen.warehouse_name)]));
			if (!!d.get_value("make_home") !== make_home.value) d.set_value("make_home", make_home.value ? 1 : 0);
		};
		const draw_dest = (d) => {
			const wrap = d.fields_dict.dest_list.$wrapper;
			if (chosen) {
				wrap.html(
					`<div class="bm-chosen">${this.esc(__("Moving to"))} <b>${this.esc(chosen.warehouse_name)}</b>
					<button type="button" class="btn btn-xs btn-default" data-bm-change="1">${this.esc(__("Change"))}</button></div>`
				);
				return;
			}
			const list = this.ctl.destinations(d.get_value("dest_search") || "");
			wrap.html(
				list.length
					? `<ul class="bm-pick-list">${list
							.map(
								(b) =>
									`<li><button type="button" class="bm-pick" data-bm-dest="${this.esc(b.warehouse)}">${this.esc(b.warehouse_name)}
									<span class="bm-muted">${this.esc(b.trail || "")}</span></button></li>`
							)
							.join("")}</ul>`
					: `<p class="bm-muted">${this.esc(__("No other bin matches."))}</p>`
			);
		};
		this.save_dialog({
			kind: "move",
			row,
			title: __("Move {0}", [this.safe(row.item_name)]),
			primary_label: __("Move"),
			fields: [
				{ fieldtype: "Data", fieldname: "dest_search", label: __("Move to bin"), placeholder: __("Type part of a bin's name") },
				{ fieldtype: "HTML", fieldname: "dest_list" },
				{ fieldtype: "HTML", fieldname: "bm_job" },
				{
					fieldtype: "Float",
					fieldname: "qty",
					label: __("Quantity ({0})", [this.safe(row.stock_uom)]),
					default: row.on_hand,
					reqd: 1,
					description: __("{0} here.", [this.safe(this.bm.amount(row.on_hand, row.stock_uom))]),
				},
				{
					fieldtype: "Check",
					fieldname: "make_home",
					label: __("Make the destination this item's bin"),
					hidden: 1,
					description: __("Ticked when everything here moves. Stock left behind keeps this bin's claim on the item."),
				},
			],
			values: (d, picked) => ({
				to_warehouse: chosen && chosen.warehouse,
				qty: picked && picked.qty !== undefined ? picked.qty : d.get_value("qty"),
				make_home:
					this.bm.make_home_offered(perms, chosen && chosen.warehouse, chosen && this.bm.views.bin_kind(chosen)) && !!d.get_value("make_home"),
				project: picker.project(),
				job_rule: picker.rule(),
			}),
			on_build: (d) => {
				picker = this.job_picker(d, row);
				d.fields_dict.dest_search.$input.on("input", () => draw_dest(d));
				d.fields_dict.dest_list.$wrapper.on("click", "[data-bm-dest]", (e) => {
					const name = e.currentTarget.getAttribute("data-bm-dest");
					// The full list, never the capped one on screen: a bin past the first 30 is still a bin.
					chosen = this.ctl.bin(name);
					draw_dest(d);
					sync_home(d);
					sync_job();
				});
				d.fields_dict.dest_list.$wrapper.on("click", "[data-bm-change]", () => {
					chosen = null;
					draw_dest(d);
					sync_home(d);
					sync_job();
					d.fields_dict.dest_search.$input.trigger("focus");
				});
				// The box is decided again on every change of quantity, until the person touches it.
				d.fields_dict.qty.$input.on("input change", () => {
					make_home = this.bm.make_home_after_qty(make_home, d.fields_dict.qty.$input.val(), row.on_hand);
					sync_home(d);
				});
				draw_dest(d);
				sync_home(d);
				sync_job();
			},
		});
	}

	unassign_dialog(row) {
		const perms = this.perms();
		const where = (this.ctl.state.location && this.ctl.state.location.warehouse_name) || this.here();
		const plan = this.bm.unassign_plan(row, perms, this.here());
		if (plan.direct) {
			this.save_dialog({
				kind: "unassign",
				row,
				title: __("Unassign {0}", [this.safe(row.item_name)]),
				primary_label: __("Unassign"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "bm_text",
						options: `<p>${this.esc(
							__("Take {0} off {1}? Nothing is on hand here, so no stock moves. You can undo it.", [row.item_name, where])
						)}</p>`,
					},
				],
			});
			return;
		}
		// Stock is still here, and unassign refuses that: first empty the bin, by Move or the boneyard.
		const d = new frappe.ui.Dialog({
			title: __("Unassign {0}", [this.safe(row.item_name)]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "bm_text",
					options: `<p>${this.esc(
						__("{0} is still on hand here. Move it to another bin or send it to the boneyard first; when this bin is empty the item comes off it.", [
							this.bm.amount(row.on_hand, row.stock_uom),
						])
					)}</p>
					<div class="bm-choices">${plan.options
						.map(
							(a) =>
								`<button type="button" class="btn btn-sm btn-default" data-bm-next="${a}">${this.esc(__(this.bm.ACTION_LABELS[a]))}…</button>`
						)
						.join(" ")}</div>
					${plan.options.length ? "" : `<p class="bm-muted">${this.esc(__("You cannot move stock. Ask a Stock Manager."))}</p>`}`,
				},
			],
		});
		d.fields_dict.bm_text.$wrapper.on("click", "[data-bm-next]", (e) => {
			const next = e.currentTarget.getAttribute("data-bm-next");
			d.hide();
			if (next === "move") this.move_dialog(row);
			else if (next === "boneyard") this.boneyard_dialog(row);
		});
		d.show();
	}

	add_dialog() {
		let timer = null;
		let seq = 0;
		const d = this.save_dialog({
			kind: "add",
			row: null,
			title: __("Add an existing item to this bin"),
			primary_label: __("Add"),
			fields: [
				{ fieldtype: "Data", fieldname: "query", label: __("Find the item"), placeholder: __("Part number, name or barcode") },
				{ fieldtype: "HTML", fieldname: "results" },
				{ fieldtype: "Data", fieldname: "item_code", label: __("Item"), read_only: 1 },
			],
			values: (dialog) => ({ item_code: dialog.get_value("item_code") }),
			on_build: (dialog) => {
				const results = dialog.fields_dict.results.$wrapper;
				const search = () => {
					const query = (dialog.get_value("query") || "").trim();
					const mine = ++seq;
					if (!query) {
						results.empty();
						return;
					}
					results.html(`<p class="bm-muted">${this.esc(__("Searching…"))}</p>`);
					this.ctl.search_items(query).then(
						(rows) => {
							if (mine !== seq) return;
							rows = rows || [];
							if (!rows.length) {
								results.html(`<p class="bm-muted">${this.esc(__("No item matches. Use New item to create it."))}</p>`);
								return;
							}
							results.html(
								`<ul class="bm-pick-list">${rows
									.map((r) => {
										const usable = !!r.is_stock_item;
										const here = r.on_hand_here ? __("{0} here", [this.bm.plain(r.on_hand_here)]) : "";
										return `<li><button type="button" class="bm-pick" data-bm-item="${this.esc(r.item_code)}" ${usable ? "" : "disabled"}>
											${this.esc(r.item_name)} <span class="bm-muted">${this.esc(r.item_code)}${
											usable ? (here ? ` · ${this.esc(here)}` : "") : ` · ${this.esc(__("not kept in stock"))}`
										}</span></button></li>`;
									})
									.join("")}</ul>`
							);
						},
						(err) => {
							if (mine === seq) results.html(`<p class="bm-dialog-msg">${this.esc(err.message || "")}</p>`);
						}
					);
				};
				dialog.fields_dict.query.$input.on("input", () => {
					clearTimeout(timer);
					timer = setTimeout(search, BM_SEARCH_DELAY_MS);
				});
				results.on("click", "[data-bm-item]", (e) => {
					dialog.set_value("item_code", e.currentTarget.getAttribute("data-bm-item"));
				});
			},
		});
		return d;
	}

	create_dialog() {
		const boot = this.ctl.state.boot || {};
		let timer = null;
		let seq = 0;
		const uoms = boot.uoms || [];
		this.save_dialog({
			kind: "create",
			row: null,
			title: __("New item on this bin"),
			primary_label: __("Create and add"),
			fields: [
				{ fieldtype: "Data", fieldname: "item_code", label: __("Part or model number"), reqd: 1, description: __("Exactly as printed on the part or its box.") },
				{ fieldtype: "Data", fieldname: "item_name", label: __("Name"), reqd: 1, description: __("What it is, for example FITTING, ELBOW, PVC, 2 IN.") },
				{ fieldtype: "Select", fieldname: "item_group", label: __("Group"), options: [""].concat(boot.item_groups || []), reqd: 1 },
				{ fieldtype: "Select", fieldname: "stock_uom", label: __("Unit"), options: uoms, default: uoms[0], reqd: 1 },
				{ fieldtype: "HTML", fieldname: "preview" },
			],
			values: (d) => ({
				item_code: d.get_value("item_code"),
				item_name: d.get_value("item_name"),
				item_group: d.get_value("item_group"),
				stock_uom: d.get_value("stock_uom"),
			}),
			on_build: (d) => {
				const preview = d.fields_dict.preview.$wrapper;
				const check = () => {
					const values = {
						item_code: d.get_value("item_code"),
						item_name: d.get_value("item_name"),
						item_group: d.get_value("item_group"),
						stock_uom: d.get_value("stock_uom"),
					};
					if (!values.item_code && !values.item_name) {
						preview.empty();
						d.get_primary_btn().prop("disabled", false);
						return;
					}
					const mine = ++seq;
					this.ctl.check_new_item(values).then(
						(out) => {
							if (mine !== seq) return;
							this.draw_preview(d, out || {});
						},
						() => {
							// The preview is advice: the naming guard still judges the Item when it is created.
							if (mine === seq) preview.empty();
						}
					);
				};
				const later = () => {
					clearTimeout(timer);
					timer = setTimeout(check, BM_SEARCH_DELAY_MS);
				};
				d.fields_dict.item_code.$input.on("input", later);
				d.fields_dict.item_name.$input.on("input", later);
				preview.on("click", "[data-bm-instead]", (e) => {
					const code = e.currentTarget.getAttribute("data-bm-instead");
					d.hide();
					this.ctl.quick("add", null, { item_code: code }).catch((err) => {
						this.ctl.state.notice = { message: err.message || __("Could not save."), error: true };
						this.render();
					});
				});
				preview.on("click", "[data-bm-group]", (e) => d.set_value("item_group", e.currentTarget.getAttribute("data-bm-group")));
			},
		});
	}

	// api.stock_scan.check_new_item's answer: already here, what the naming guard will refuse, what
	// is like it, and advice. An existing or usable similar item can be added instead.
	draw_preview(d, out) {
		const parts = [];
		const instead = (row) =>
			row.usable && row.stocked
				? ` <button type="button" class="btn btn-xs btn-default" data-bm-instead="${this.esc(row.item_code)}">${this.esc(__("Add this one instead"))}</button>`
				: row.refusal
				? ` <span class="bm-muted">${this.esc(row.refusal)}</span>`
				: "";
		if (out.exists) {
			parts.push(`<p class="bm-dialog-msg">${this.esc(__("{0} is already in ERPNext.", [out.exists.item_code]))}${instead(out.exists)}</p>`);
		}
		if (out.blocking && out.blocking.length) {
			const head = out.will_refuse ? __("Creating it will be refused:") : __("From {0} this will be refused:", [out.refuse_from || ""]);
			parts.push(
				`<div class="bm-dialog-msg"><p>${this.esc(head)}</p><ul>${out.blocking.map((f) => `<li>${this.esc(f.message)}</li>`).join("")}</ul></div>`
			);
		}
		if (out.similar && out.similar.length) {
			parts.push(
				`<p class="bm-preview-head">${this.esc(__("Already in ERPNext and alike:"))}</p><ul class="bm-similar">${out.similar
					.map((s) => `<li>${this.esc(s.item_name)} <span class="bm-muted">${this.esc(s.item_code)}</span>${instead(s)}</li>`)
					.join("")}</ul>`
			);
		}
		if (out.suggested_group && !d.get_value("item_group")) {
			parts.push(
				`<p class="bm-muted">${this.esc(__("Items like it are in {0}.", [out.suggested_group]))}
				<button type="button" class="btn btn-xs btn-default" data-bm-group="${this.esc(out.suggested_group)}">${this.esc(__("Use it"))}</button></p>`
			);
		}
		if (out.advice && out.advice.length) {
			parts.push(`<ul class="bm-advice">${out.advice.map((f) => `<li>${this.esc(f.message)}</li>`).join("")}</ul>`);
		}
		d.fields_dict.preview.$wrapper.html(parts.join(""));
		d.get_primary_btn().prop("disabled", !!(out.exists || out.will_refuse));
	}
}
