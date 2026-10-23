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
 *   env.render()                   draw `state`
 *   env.now() / env.random()       for client_ref
 *
 * HISTORY. The picker and each bin are routes, so frappe owns history: picking a bin is one
 * set_route (one entry), Back is the browser's and lands on the picker through on_page_show, and
 * Forward lands on the bin the same way. Nothing else here routes: a save, an undo, a refresh or a
 * dialog changes `state` and redraws. The bin entry this page pushed is tagged (history.state
 * bm_from), so the page's own "All bins" steps back through it instead of stacking a second picker
 * entry; a bin opened from a link has no tag, and "All bins" then pushes the picker.
 */

import * as logic from "./logic.js";

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
			filter: "",
		};
		this.boot_loading = null;
		this.location_seq = 0;
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
		const opts = logic.read_route_options(this.env.route_options());
		if (opts.any) this.env.clear_route_options();
		if (target.screen === "picker" && opts.warehouse) {
			// /desk/bin-manager?warehouse=X: the bin, in place of the picker entry that asked for it.
			this.go({ screen: "bin", warehouse: opts.warehouse }, true);
			return true;
		}
		if (target.screen === "picker" && opts.q) this.state.filter = opts.q;
		const changed = !this.state.target || logic.route_key(this.state.target) !== logic.route_key(target);
		this.state.target = target;
		if (changed) {
			this.state.notice = null;
			if (target.screen === "bin" && (!this.state.location || this.state.location.warehouse !== target.warehouse)) {
				this.state.location = null;
			}
			this.state.location_error = null;
		}
		this.env.render();
		this.load_boot(this.state.boot_stale && target.screen === "picker");
		if (target.screen === "bin") {
			this.load_location();
			this.load_recent();
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

	open_bin(warehouse) {
		if (!warehouse) return false;
		const from_picker = !this.state.target || this.state.target.screen === "picker";
		const pushed = this.go({ screen: "bin", warehouse }, false);
		if (pushed && from_picker) this.env.tag_state({ bm_from: "picker" });
		return pushed;
	}

	// "All bins": back through the entry this page pushed, else the picker as a new entry.
	leave_bin() {
		const state = this.env.history_state() || {};
		if (state.bm_from === "picker") {
			this.env.history_back();
			return;
		}
		this.go({ screen: "picker" }, false);
	}

	set_filter(text) {
		this.state.filter = String(text || "");
		this.env.render();
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

	refresh() {
		if (this.state.target && this.state.target.screen === "bin") {
			this.load_location();
			this.load_recent();
		}
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
		const warehouse = this.state.target && this.state.target.warehouse;
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

	saved(result) {
		const r = result || {};
		if (r.log && r.log.name) this.state.recent = logic.merge_recent(this.state.recent, r.log);
		this.state.notice = { message: r.message || "", log: r.log || null };
		this.state.boot_stale = true;
		this.env.render();
		this.load_location();
		return { message: r.message || "", result: r };
	}
}
