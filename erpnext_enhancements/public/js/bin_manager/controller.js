/*
 * Bin Manager (v1.693.0): the page's state and its server calls, with no DOM. The page script hands
 * it an `env` and draws whatever `state` says each time `env.render()` is called; the node harness
 * (scripts/test_bin_manager.mjs) hands it a model of the v16 router instead.
 *
 *   env.call(method, args, opts)   sf_kit.call(..., { quiet: true }): a promise of r.message that
 *                                  rejects { status, message, exc_type, session_expired }
 *   env.get_route()                frappe.get_route()
 *   env.set_route(route, replace)  frappe.set_route, with frappe.route_flags.replace_route for a replace
 *   env.route_options()            frappe.route_options (the address's query lands there too)
 *   env.clear_route_options()      frappe.route_options = null
 *   env.history_state() / env.tag_state(obj) / env.history_back()
 *   env.replace_query(query)       the address's query, in place (history.replaceState): no entry
 *   env.render()                   draw `state`
 *   env.now() / env.random()       for client_ref
 *
 * HISTORY. The picker and each bin are routes, so frappe owns history: picking a bin is one
 * set_route (one entry), Back is the browser's and lands on the picker through on_page_show, and
 * Forward lands on the bin the same way. Nothing else here routes: a save, an undo, a refresh or a
 * dialog changes `state` and redraws. The bin entry this page pushed is tagged (history.state
 * bm_from), so the page's own "All bins" steps back through it instead of stacking a second picker
 * entry; a bin opened from a link has no tag, and "All bins" then pushes the picker.
 *
 * VIEWS AND FILTERS (v1.806.1). By project, one job, Job sites and Problems are routes too, so
 * each is a history entry like the picker and a bin. The picker's FILTERS are not: they are
 * written into the picker's own address in place (env.replace_query), so typing or ticking a
 * filter never adds an entry, and Back from a bin lands on the picker with its filters, read
 * back out of the address like any deep link. The address is the truth: a picker address with
 * no query is a picker with no filters.
 */

import * as logic from "./logic.js";
import * as views from "./views.js";

export class Controller {
	constructor(env) {
		this.env = env;
		this.state = {
			target: null,
			boot: null,
			boot_error: null,
			boot_stale: false,
			location: null,
			location_error: null,
			loading_location: false,
			recent: [],
			notice: null,
			// The picker's filters (views.default_filters). `filter` is what is typed, kept beside
			// them under its old name.
			filter: "",
			filters: views.default_filters(),
			// The item filter's answer: { item_code, loading, error, data } or null.
			item: null,
			// By project: the list of jobs, what is typed over it, and one job's two places.
			projects: null,
			projects_error: null,
			project_filter: "",
			project: null, // { project, loading, error, data }
			// Problems: { loading, error, data }.
			problems: null,
		};
		this.boot_loading = null;
		this.location_seq = 0;
		this.item_seq = 0;
		this.project_seq = 0;
		this.problems_seq = 0;
	}

	perms() {
		return logic.permissions(this.state.boot);
	}

	new_ref() {
		return logic.client_ref(this.env.now(), this.env.random());
	}

	// ------------------------------------------------------------------ routing

	// Every on_page_show: read the route, and a deep link's route options once.
	show() {
		const target = logic.parse_route(this.env.get_route());
		if (!target) return false;
		// Copied before they are cleared: the picker's filters are read out of the same options.
		const raw = logic.copy_route_options(this.env.route_options());
		const opts = logic.read_route_options(raw);
		if (opts.any) this.env.clear_route_options();
		if (target.screen === "picker" && opts.warehouse) {
			// /desk/bin-manager?warehouse=X: the bin, in place of the picker entry that asked for it.
			this.go({ screen: "bin", warehouse: opts.warehouse }, true);
			return true;
		}
		if (target.screen === "picker") {
			// The address is the truth: its query is the filters, and a bare address is none.
			const filters = opts.any ? views.read_filters(raw) : views.default_filters();
			if (filters.item !== this.state.filters.item) this.state.item = null;
			this.state.filters = filters;
			this.state.filter = filters.q;
		}
		const changed = !this.state.target || logic.route_key(this.state.target) !== logic.route_key(target);
		this.state.target = target;
		if (changed) {
			this.state.notice = null;
			if (target.screen === "bin" && (!this.state.location || this.state.location.warehouse !== target.warehouse)) {
				this.state.location = null;
			}
			this.state.location_error = null;
			if (target.screen === "project" && (!this.state.project || this.state.project.project !== target.project)) {
				this.state.project = { project: target.project, loading: true, error: null, data: null };
			}
		}
		this.env.render();
		// The bins, and for the picker and Job sites a fresh copy once a save has made them stale.
		this.load_boot(this.state.boot_stale && (target.screen === "picker" || target.screen === "sites"));
		if (target.screen === "bin") {
			this.load_location();
			this.load_recent();
		} else if (target.screen === "picker") {
			if (this.state.filters.item && !this.state.item) this.load_item();
		} else if (target.screen === "projects") {
			this.load_projects();
		} else if (target.screen === "project") {
			this.load_project();
			this.load_recent();
		} else if (target.screen === "problems") {
			this.load_problems();
		}
		return true;
	}

	// One screen change: a new history entry, or (replace) in place of the one on screen.
	go(target, replace) {
		const now = this.state.target;
		if (now && logic.route_key(now) === logic.route_key(target) && !replace) return false;
		this.env.clear_route_options();
		this.env.set_route(logic.build_route(target), !!replace);
		return true;
	}

	// A bin, from whichever screen listed it. The entry is tagged with that screen, so the bin's
	// way back steps back through it instead of stacking a second copy.
	open_bin(warehouse) {
		if (!warehouse) return false;
		const from = (this.state.target && this.state.target.screen) || "picker";
		const pushed = this.go({ screen: "bin", warehouse }, false);
		if (pushed && from !== "bin") this.env.tag_state({ bm_from: from });
		return pushed;
	}

	// Where the bin on screen was opened from ("picker", "sites", "problems", "project"), or null
	// for one opened by a link.
	came_from() {
		const state = this.env.history_state() || {};
		return typeof state.bm_from === "string" && state.bm_from ? state.bm_from : null;
	}

	// The bin's way back: through the entry this page pushed, else the picker as a new entry.
	leave_bin() {
		if (this.came_from()) {
			this.env.history_back();
			return;
		}
		this.go({ screen: "picker" }, false);
	}

	// A tab across the top: one screen change, one history entry.
	open_tab(screen) {
		return this.go({ screen }, false);
	}

	open_project(project) {
		if (!project) return false;
		const from_list = !!this.state.target && this.state.target.screen === "projects";
		const pushed = this.go({ screen: "project", project }, false);
		if (pushed && from_list) this.env.tag_state({ bm_from: "projects" });
		return pushed;
	}

	// "All jobs": back through the entry this page pushed, else the list as a new entry.
	leave_project() {
		if (this.came_from() === "projects") {
			this.env.history_back();
			return;
		}
		this.go({ screen: "projects" }, false);
	}

	set_filter(text) {
		this.set_filters({ q: String(text || "") });
	}

	// One or more of the picker's filters. Written into the address in place: never a history entry.
	set_filters(patch) {
		const next = Object.assign({}, this.state.filters, patch || {});
		const item_changed = next.item !== this.state.filters.item;
		this.state.filters = next;
		this.state.filter = next.q;
		if (item_changed) this.state.item = null;
		if (this.env.replace_query && this.state.target && this.state.target.screen === "picker") {
			this.env.replace_query(views.filters_query(next));
		}
		this.env.render();
		if (item_changed && next.item) this.load_item();
	}

	clear_filters() {
		this.set_filters(views.default_filters());
	}

	set_project_filter(text) {
		this.state.project_filter = String(text || "");
		this.env.render();
	}

	// The bins the picker shows: the boot's, through the filters.
	shown_bins() {
		const bins = (this.state.boot && this.state.boot.bins) || [];
		const item = this.state.item;
		return views.apply_filters(bins, this.state.filters, item && item.data ? item.data.locations : null);
	}

	// ------------------------------------------------------------------ loading

	load_boot(force) {
		if (this.boot_loading) return this.boot_loading;
		if (this.state.boot && !force) return Promise.resolve(this.state.boot);
		this.boot_loading = this.env
			.call(logic.READS.boot, {}, { quiet: true, method: "GET" })
			.then(
				(boot) => {
					this.state.boot = boot || { bins: [] };
					this.state.boot_error = null;
					this.state.boot_stale = false;
				},
				(err) => {
					if (!this.state.boot) this.state.boot_error = err || {};
				}
			)
			.then(() => {
				this.boot_loading = null;
				this.env.render();
				return this.state.boot;
			});
		return this.boot_loading;
	}

	load_location() {
		const target = this.state.target;
		if (!target || target.screen !== "bin") return Promise.resolve(null);
		const warehouse = target.warehouse;
		const seq = ++this.location_seq;
		this.state.loading_location = true;
		return this.env.call(logic.READS.location, { warehouse }, { quiet: true }).then(
			(location) => {
				if (seq !== this.location_seq) return null;
				this.state.loading_location = false;
				this.state.location = location;
				this.state.location_error = null;
				this.env.render();
				return location;
			},
			(err) => {
				if (seq !== this.location_seq) return null;
				this.state.loading_location = false;
				this.state.location_error = err || {};
				this.env.render();
				return null;
			}
		);
	}

	load_recent() {
		return this.env.call(logic.READS.recent, {}, { quiet: true }).then(
			(rows) => {
				this.state.recent = Array.isArray(rows) ? rows : [];
				this.env.render();
			},
			() => {
				// The list is a convenience: the items still show, and a later save fills it.
			}
		);
	}

	// The item filter's answer: where the item is on hand and where it is assigned.
	load_item() {
		const item_code = this.state.filters.item;
		if (!item_code) return Promise.resolve(null);
		const seq = ++this.item_seq;
		this.state.item = { item_code, loading: true, error: null, data: null };
		this.env.render();
		return this.env.call(logic.READS.item_locations, { item_code }, { quiet: true, method: "GET" }).then(
			(data) => {
				if (seq !== this.item_seq) return null;
				this.state.item = { item_code, loading: false, error: null, data: data || { locations: {} } };
				this.env.render();
				return data;
			},
			(err) => {
				if (seq !== this.item_seq) return null;
				this.state.item = { item_code, loading: false, error: err || {}, data: null };
				this.env.render();
				return null;
			}
		);
	}

	load_projects() {
		return this.env.call(logic.READS.project_index, {}, { quiet: true, method: "GET" }).then(
			(rows) => {
				this.state.projects = Array.isArray(rows) ? rows : [];
				this.state.projects_error = null;
				this.env.render();
			},
			(err) => {
				if (!this.state.projects) this.state.projects_error = err || {};
				this.env.render();
			}
		);
	}

	load_project() {
		const target = this.state.target;
		if (!target || target.screen !== "project") return Promise.resolve(null);
		const project = target.project;
		const seq = ++this.project_seq;
		const held = this.state.project && this.state.project.project === project ? this.state.project.data : null;
		this.state.project = { project, loading: true, error: null, data: held };
		return this.env.call(logic.READS.project_view, { project }, { quiet: true, method: "GET" }).then(
			(data) => {
				if (seq !== this.project_seq) return null;
				this.state.project = { project, loading: false, error: null, data };
				this.env.render();
				return data;
			},
			(err) => {
				if (seq !== this.project_seq) return null;
				this.state.project = { project, loading: false, error: err || {}, data: null };
				this.env.render();
				return null;
			}
		);
	}

	load_problems() {
		const seq = ++this.problems_seq;
		const held = this.state.problems ? this.state.problems.data : null;
		this.state.problems = { loading: true, error: null, data: held };
		return this.env.call(logic.READS.problems, {}, { quiet: true, method: "GET" }).then(
			(data) => {
				if (seq !== this.problems_seq) return null;
				this.state.problems = { loading: false, error: null, data: data || {} };
				this.env.render();
				return data;
			},
			(err) => {
				if (seq !== this.problems_seq) return null;
				this.state.problems = { loading: false, error: err || {}, data: null };
				this.env.render();
				return null;
			}
		);
	}

	refresh() {
		const screen = this.state.target && this.state.target.screen;
		if (screen === "bin") {
			this.load_location();
			this.load_recent();
		} else if (screen === "projects") this.load_projects();
		else if (screen === "project") this.load_project();
		else if (screen === "problems") this.load_problems();
		else if (screen === "picker" && this.state.filters.item) this.load_item();
		return this.load_boot(true);
	}

	// ------------------------------------------------------------------ reads for the dialogs

	search_items(query) {
		const warehouse = this.state.target && this.state.target.warehouse;
		return this.env.call(logic.READS.search_items, { query, warehouse }, { quiet: true });
	}

	check_new_item(values) {
		const v = values || {};
		return this.env.call(
			logic.READS.check_new_item,
			{ item_code: v.item_code || "", item_name: v.item_name || "", item_group: v.item_group || "", stock_uom: v.stock_uom || "" },
			{ quiet: true }
		);
	}

	destinations(query) {
		const bins = (this.state.boot && this.state.boot.bins) || [];
		return logic.destinations(bins, this.state.target && this.state.target.warehouse, query);
	}

	// ------------------------------------------------------------------ saves

	// One save from a dialog. Resolves { message, result }; rejects { message, keep_ref }, the
	// dialog showing the message inline and sending the same reference again only when keep_ref.
	// Resolves { prompt } instead when the server asked for a unit cost (logic.isCostPrompt): nothing was
	// saved, so nothing is reported, listed or reloaded, and the page asks and submits again.
	submit(kind, row, values, ref) {
		// The place the row is at: the bin on screen, or the one a view's row names (By project
		// lists rows of two places on one screen).
		const warehouse = (values && values.from_warehouse) || (this.state.target && this.state.target.warehouse);
		const problem = logic.check_values(kind, row, values, warehouse);
		if (problem) return Promise.reject({ message: problem, keep_ref: true });
		let req;
		try {
			req = logic.request(kind, warehouse, row, values, ref);
		} catch (e) {
			return Promise.reject({ message: String(e && e.message), keep_ref: false });
		}
		return this.env.call(req.method, req.args, { quiet: true }).then(
			(result) => {
				if (logic.isCostPrompt(result)) return { prompt: result, message: "" };
				// The row changes now; saved() reads the list again and that is the truth. Not for a repeated save
				// (the server returned a save it had already made): the list may have been read since.
				if (!(result && result.repeated)) logic.apply_local(kind, row, values);
				return this.saved(result);
			},
			(err) => Promise.reject({ message: (err && err.message) || "", keep_ref: logic.keep_ref(err), error: err })
		);
	}

	// A save with no dialog of its own (Keep on this bin, a preview's "Add this one instead"). Its
	// reference is kept per action, bin and item until the save lands or is refused, exactly as a
	// dialog keeps its own: after a dropped reply (status 0, 409) the next click must send the SAME
	// reference, so the server answers with the first save instead of "already on this bin".
	quick(kind, row, values) {
		const warehouse = this.state.target && this.state.target.warehouse;
		const item_code = (row && row.item_code) || (values && values.item_code) || "";
		const key = `${kind}|${warehouse}|${item_code}`;
		if (!this.quick_refs) this.quick_refs = new Map();
		if (!this.quick_refs.has(key)) this.quick_refs.set(key, this.new_ref());
		return this.submit(kind, row, values || {}, this.quick_refs.get(key)).then(
			(done) => {
				this.quick_refs.delete(key);
				return done;
			},
			(err) => {
				if (!err || !err.keep_ref) this.quick_refs.delete(key);
				return Promise.reject(err);
			}
		);
	}

	// Every bin, uncapped: a clicked destination is looked up here, never in a filtered or capped
	// list (logic.destinations shows the first 30 that match what is typed).
	bin(warehouse) {
		const bins = (this.state.boot && this.state.boot.bins) || [];
		return bins.find((b) => b.warehouse === warehouse) || null;
	}

	undo(log_name) {
		return this.submit("undo", null, { log: log_name }, null);
	}

	/**
	 * By project: move a row between the Project Shelf and the job's site. `direction` is
	 * "to_site" or "to_shelf" (views.project_move). The transfer is the page's ordinary Move
	 * (bin_assignments.move_to), so it is logged, carries the job, and can be undone; nothing is
	 * ever made an item's bin from here.
	 *
	 * A job with no site yet has it opened first (api.stock_scan.job_site, which refuses what
	 * the move itself would refuse). Opening a site posts no stock, so a failure there saved
	 * nothing and the next try may use a fresh reference.
	 */
	project_submit(direction, row, values, ref) {
		const held = this.state.project;
		const move = views.project_move(direction, held && held.data);
		if (!move) return Promise.reject({ message: "That move cannot be made from here.", keep_ref: false });
		const send = (to_warehouse) =>
			this.submit(
				"move",
				row,
				{ qty: (values || {}).qty, to_warehouse, from_warehouse: move.from_warehouse, make_home: false },
				ref
			);
		if (!move.needs_site) return send(move.to_warehouse);
		return this.env.call(logic.OPEN_SITE, { project: held.project }, { quiet: true }).then(
			(site) => {
				if (!site || !site.warehouse) return Promise.reject({ message: "The job site could not be opened.", keep_ref: false });
				// Remembered at once: a retry of the move must not open the site a second time.
				if (this.state.project && this.state.project.data && !this.state.project.data.site) {
					this.state.project.data.site = { warehouse: site.warehouse, warehouse_name: site.warehouse_name, items: [], disabled: false };
				}
				this.state.boot_stale = true;
				return send(site.warehouse);
			},
			(err) => Promise.reject({ message: (err && err.message) || "", keep_ref: false, error: err })
		);
	}

	saved(result) {
		const r = result || {};
		if (r.log && r.log.name) this.state.recent = logic.merge_recent(this.state.recent, r.log);
		this.state.notice = { message: r.message || "", log: r.log || null };
		this.state.boot_stale = true;
		this.env.render();
		if (this.state.target && this.state.target.screen === "project") this.load_project();
		else this.load_location();
		return { message: r.message || "", result: r };
	}
}
