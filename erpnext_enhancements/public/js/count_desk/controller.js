/*
 * Count Desk (v1.737.0): the page's state and its server calls, with no DOM. The page script hands
 * it an `env` and draws whatever `state` says each time `env.render(change)` is called; the node
 * harness (scripts/test_count_desk.mjs) hands it a model of the v16 router and a fake server.
 *
 *   env.call(method, args, opts)   sf_kit.call(..., { quiet: true }): a promise of r.message that
 *                                  rejects { status, message, exc_type, session_expired }
 *   env.get_route() / env.set_route(route, replace)
 *   env.route_options() / env.clear_route_options()
 *   env.history_state() / env.tag_state(obj) / env.history_back()
 *   env.render(change)             draw `state`; `change` is { rows: [keys] } when only those rows
 *                                  changed (the page patches them in place), else undefined
 *   env.set_timeout(fn, ms) / env.clear_timeout(id)
 *
 * SAVING. A count is saved the moment it is entered, through one queue: one request in flight at a
 * time, every edit made meanwhile merged per row (the last value wins), sent as the next batch. A
 * save is an absolute value, so resending one is always safe: a dropped connection or a 5xx puts
 * the batch back and retries with backoff (2 s, 4 s, ... 30 s) until it lands; a refusal (417, 403)
 * marks those rows with the server's sentence and is not retried. The server's answer replaces
 * each touched item's rows, unless the row has been edited again since (then the next answer
 * will). Leaving the page with anything unsaved asks first (the page's beforeunload).
 *
 * A REFUSED SAVE IS NEVER POSTED AROUND (v1.738.0). The review is worked out from the rows on screen, so a
 * value the server refused must not stay on screen looking counted. A row refused on its own is put
 * back as the server has it (`this.server`: each item's counted rows as the sheet or the last answer
 * gave them) and keeps its red mark; a save refused as a whole (the count finalized elsewhere,
 * access gone) leaves what was typed on screen, marked, with the reason above the grid. Either
 * holds Post back (save_state, logic.post_check) until the marks are dealt with or cleared, or the
 * page is reloaded.
 *
 * HISTORY. The plan, a sheet and its review are routes, so frappe owns history: opening a count is
 * one set_route, Back is the browser's. Entries this page pushed are tagged (history.state cd_from),
 * so "Counts" and "Back to the sheet" step back through them instead of stacking duplicates.
 */

import * as logic from "./logic.js";

const RETRY_MS = [2000, 4000, 8000, 15000, 30000];

export class Controller {
	constructor(env) {
		this.env = env;
		this.state = {
			target: null,
			boot: null,
			boot_error: null,
			boot_stale: false,
			tree: null,
			selected: new Set(),
			expanded: new Set(),
			tree_query: "",
			sheet: null,
			sheet_error: null,
			rows: [],
			visible: [],
			view: { filter: "all", query: "", bin: "" },
			revealed: false,
			pending: new Map(),
			saving: false,
			save_error: null,
			status: new Map(),
			undo: [],
			notice: null,
			post_result: null,
			posting: false,
		};
		this.by_key = new Map();
		// "item\x1fwarehouse" -> that item's counted rows as the server last gave them.
		this.server = new Map();
		this.boot_loading = null;
		this.sheet_seq = 0;
		this.retry_timer = null;
		this.retries = 0;
		this.waiters = [];
	}

	// ------------------------------------------------------------------ routing

	show() {
		const target = logic.parse_route(this.env.get_route());
		if (!target) return false;
		const before = this.state.target;
		const changed = !before || logic.route_key(before) !== logic.route_key(target);
		const other_count = !before || before.count !== target.count;
		this.state.target = target;
		if (changed) this.state.notice = this.state.carry_notice || null;
		this.state.carry_notice = null;
		const opts = this.env.route_options ? this.env.route_options() : null;
		if (opts && Object.keys(opts).length) this.env.clear_route_options();
		this.env.render();
		if (target.screen === "plan") {
			this.load_boot(this.state.boot_stale);
			return true;
		}
		if (other_count || !this.state.sheet || this.state.sheet.name !== target.count) {
			this.reset_sheet();
			this.load_sheet();
		}
		return true;
	}

	go(target, replace, tag) {
		const now = this.state.target;
		if (now && logic.route_key(now) === logic.route_key(target) && !replace) return false;
		this.env.clear_route_options();
		this.env.set_route(logic.build_route(target), !!replace);
		if (tag) this.env.tag_state(tag);
		return true;
	}

	open_count(name, notice) {
		if (!name) return false;
		const from_plan = !this.state.target || this.state.target.screen === "plan";
		if (notice) this.state.carry_notice = notice;
		return this.go({ screen: "sheet", count: name }, false, from_plan ? { cd_from: "plan" } : null);
	}

	open_review() {
		const t = this.state.target;
		if (!t || !t.count) return false;
		return this.go({ screen: "review", count: t.count }, false, { cd_from: "sheet" });
	}

	back_to_sheet() {
		const t = this.state.target;
		if (!t || !t.count) return;
		const state = this.env.history_state() || {};
		if (state.cd_from === "sheet") {
			this.env.history_back();
			return;
		}
		this.go({ screen: "sheet", count: t.count }, false);
	}

	to_plan() {
		const t = this.state.target;
		const state = this.env.history_state() || {};
		if (t && t.screen === "sheet" && state.cd_from === "plan") {
			this.env.history_back();
			return;
		}
		this.go({ screen: "plan" }, false);
	}

	// ------------------------------------------------------------------ the plan

	load_boot(force) {
		if (this.boot_loading) return this.boot_loading;
		if (this.state.boot && !force) return Promise.resolve(this.state.boot);
		this.boot_loading = this.env
			.call(logic.READS.boot, {}, { quiet: true, method: "GET" })
			.then(
				(boot) => {
					this.state.boot = boot || { groups: [], bins: [] };
					this.state.boot_error = null;
					this.state.boot_stale = false;
					this.state.tree = logic.build_tree(this.state.boot.groups, this.state.boot.bins, this.state.boot.coverage_days);
					// Opened down to the shelves: every group that holds another group. A shelf (a group of
					// bins only) stays closed, so the first screen is rows, bays and shelves with their numbers.
					if (!this.state.expanded.size) {
						const open = (node) => {
							if (node.is_bin || !node.children.some((c) => !c.is_bin)) return;
							this.state.expanded.add(node.id);
							node.children.forEach(open);
						};
						this.state.tree.roots.forEach(open);
					}
					const known = new Set(this.state.tree.by_id.keys());
					this.state.selected = new Set(Array.from(this.state.selected).filter((b) => known.has(b)));
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

	toggle_node(id) {
		const node = this.state.tree && this.state.tree.by_id.get(id);
		if (!node) return;
		this.state.selected = logic.toggle_node(node, this.state.selected);
		this.env.render();
	}

	toggle_expand(id) {
		if (this.state.expanded.has(id)) this.state.expanded.delete(id);
		else this.state.expanded.add(id);
		this.env.render();
	}

	set_tree_query(text) {
		this.state.tree_query = String(text || "");
		this.env.render();
	}

	apply_preset(kind) {
		const boot = this.state.boot || {};
		const names = logic.preset(boot.bins, kind, boot.coverage_days, 10);
		this.state.selected = new Set(names);
		this.env.render();
		return names.length;
	}

	clear_selection() {
		this.state.selected = new Set();
		this.env.render();
	}

	start(title, blind) {
		const tree = this.state.tree;
		if (!tree || !this.state.selected.size) return Promise.reject({ message: "Choose at least one location to count." });
		const warehouses = logic.selection_for_start(tree, this.state.selected);
		return this.env
			.call(logic.WRITES.start, { warehouses: JSON.stringify(warehouses), title: title || "", blind: blind ? 1 : 0 }, { quiet: true })
			.then((res) => {
				this.state.selected = new Set();
				this.state.boot_stale = true;
				const said = [];
				for (const o of res.overlaps || []) {
					said.push(`${o.bins} of these locations are also in ${o.title || o.name}, open by ${o.counted_by_name}.`);
				}
				if ((res.problems || []).length) said.push(`Left out: ${res.problems.join(" ")}`);
				this.open_count(res.name, said.length ? { message: said.join(" "), tone: "warn" } : null);
				return res;
			});
	}

	// ------------------------------------------------------------------ the sheet

	reset_sheet() {
		this.state.sheet = null;
		this.state.sheet_error = null;
		this.state.rows = [];
		this.by_key = new Map();
		this.server = new Map();
		this.state.visible = [];
		this.state.view = { filter: "all", query: "", bin: "" };
		this.state.revealed = false;
		this.state.status = new Map();
		this.state.undo = [];
		this.state.post_result = null;
	}

	load_sheet() {
		const t = this.state.target;
		if (!t || !t.count) return Promise.resolve(null);
		const seq = ++this.sheet_seq;
		const name = t.count;
		return this.env.call(logic.READS.sheet, { session: name }, { quiet: true, method: "GET" }).then(
			(payload) => {
				if (seq !== this.sheet_seq) return null;
				this.set_sheet(payload);
				return payload;
			},
			(err) => {
				if (seq !== this.sheet_seq) return null;
				this.state.sheet_error = err || {};
				this.env.render();
				return null;
			}
		);
	}

	set_sheet(payload) {
		const p = payload || {};
		this.state.sheet = Object.assign({}, p.count || {}, {
			can_post: !!p.can_post,
			require_reason: !!p.require_reason,
			reasons: p.reasons || [],
		});
		this.state.sheet_error = null;
		this.set_rows(p.rows || []);
		this.server = new Map();
		for (const r of this.state.rows) {
			if (!r.counted) continue;
			const group = logic.group_of(r);
			if (!this.server.has(group)) this.server.set(group, []);
			this.server.get(group).push(Object.assign({}, r));
		}
		this.refilter();
	}

	set_rows(rows) {
		this.state.rows = rows;
		this.by_key = new Map(rows.map((r) => [r.key, r]));
	}

	row(key) {
		return this.by_key.get(key) || null;
	}

	editable() {
		return !!(this.state.sheet && this.state.sheet.editable);
	}

	hidden_system() {
		return !!(this.state.sheet && this.state.sheet.blind && !this.state.revealed);
	}

	refilter() {
		this.state.visible = logic.visible_keys(this.state.rows, this.state.view);
		this.env.render();
	}

	set_view(patch) {
		this.state.view = Object.assign({}, this.state.view, patch || {});
		this.refilter();
	}

	summary() {
		return logic.summary(this.state.rows);
	}

	// Every change to a row goes through here: the row is redrawn at once with what was typed,
	// queued for the server, and (unless it is Undo putting something back) remembered for Undo.
	change(key, patch, opts) {
		return this.change_many([Object.assign({ key }, patch)], opts);
	}

	change_many(entries, opts) {
		if (!this.editable()) return 0;
		const o = opts || {};
		const befores = [];
		const touched = new Set();
		let rows = this.state.rows;
		for (const entry of entries || []) {
			const row = rows.find((r) => r.key === entry.key);
			if (!row) continue;
			const patch = Object.assign({}, entry);
			delete patch.key;
			if ("counted_qty" in patch) {
				const same =
					patch.counted_qty === null
						? !row.counted
						: row.counted && Math.abs((Number(row.counted_qty) || 0) - patch.counted_qty) <= logic.TOLERANCE;
				if (same) delete patch.counted_qty;
			}
			if (!Object.keys(patch).length) continue;
			befores.push(logic.before(row, patch));
			rows = logic.optimistic(rows, entry.key, patch);
			this.state.pending = logic.queue(this.state.pending, Object.assign({ key: entry.key }, patch));
			this.state.status.set(entry.key, { state: "saving" });
			const group = logic.group_of(row);
			for (const r of rows) if (logic.group_of(r) === group) touched.add(r.key);
		}
		if (!befores.length) return 0;
		this.set_rows(rows);
		if (!o.undo_step) {
			this.state.undo.push(befores);
			if (this.state.undo.length > logic.UNDO_LIMIT) this.state.undo.shift();
		}
		this.env.render({ rows: Array.from(touched) });
		this.flush();
		return befores.length;
	}

	set_count(key, qty) {
		return this.change(key, { counted_qty: qty });
	}

	toggle_recount(key) {
		const row = this.row(key);
		if (!row || !row.counted) return 0;
		return this.change(key, { recount: row.recount ? 0 : 1 });
	}

	undo() {
		const step = this.state.undo.pop();
		if (!step) return 0;
		const done = this.change_many(step, { undo_step: true });
		if (!done) this.env.render();
		return step.length;
	}

	fill(key, values) {
		const plan = logic.fill_plan(this.state.visible, this.by_key, key, values);
		const done = this.change_many(plan.entries);
		for (const p of plan.problems) this.state.status.set(p.key, { state: "error", message: `Not saved: ${p.message}` });
		if (plan.problems.length) this.env.render({ rows: plan.problems.map((p) => p.key) });
		return { done, problems: plan.problems, refused: plan.refused };
	}

	zero_rest(warehouse) {
		return this.change_many(logic.zero_plan(this.state.rows, warehouse || ""));
	}

	add_row(item, warehouse) {
		const bin = ((this.state.sheet && this.state.sheet.scope) || []).find((b) => b.warehouse === warehouse);
		const fresh = logic.added_row(this.state.rows, item, warehouse, bin ? bin.warehouse_name : warehouse);
		const key = logic.line_key(item.item_code, warehouse);
		if (!fresh) return key;
		this.set_rows(logic.insert_row(this.state.rows, fresh));
		const visible = this.state.visible.slice();
		let at = -1;
		for (let i = 0; i < visible.length; i++) {
			const r = this.by_key.get(visible[i]);
			if (r && r.warehouse === warehouse) at = i;
		}
		visible.splice(at < 0 ? visible.length : at + 1, 0, key);
		this.state.visible = visible;
		this.env.render();
		return key;
	}

	// ------------------------------------------------------------------ the save queue

	flush() {
		if (this.state.saving || !this.state.pending.size || !this.state.sheet) {
			this.settle();
			return;
		}
		if (this.retry_timer) return;
		const session = this.state.sheet.name;
		const { batch, rest } = logic.take(this.state.pending, logic.SAVE_LIMIT);
		this.state.pending = rest;
		this.state.saving = true;
		this.env.render({ rows: [] });
		this.env.call(logic.WRITES.save, { session, rows: JSON.stringify(batch) }, { quiet: true }).then(
			(res) => this.saved(session, batch, res || {}),
			(err) => this.save_failed(session, batch, err || {})
		);
	}

	saved(session, batch, res) {
		this.state.saving = false;
		this.retries = 0;
		this.state.save_error = null;
		if (!this.state.sheet || this.state.sheet.name !== session) {
			this.flush();
			return;
		}
		const changed = new Set();
		const refused = [];
		for (const r of res.results || []) {
			if (!r || !r.key) continue;
			changed.add(r.key);
			if (this.state.pending.has(r.key)) continue;
			if (r.ok) this.state.status.delete(r.key);
			else {
				this.state.status.set(r.key, { state: "error", message: `Not saved: ${r.message || "the server refused it."}` });
				refused.push(r.key);
			}
		}
		// What the server now holds for each item it answered for, edited again since or not.
		for (const g of res.groups || []) this.server.set(`${g.item_code}${logic.KEY_SEP}${g.warehouse}`, (g.rows || []).map((r) => Object.assign({}, r)));
		// An item edited again since this batch left keeps what is on screen; the next answer has it.
		const busy = new Set(Array.from(this.state.pending.keys()).map((k) => {
			const parts = logic.split_key(k);
			return parts ? `${parts.item_code}${logic.KEY_SEP}${parts.warehouse}` : k;
		}));
		const groups = (res.groups || []).filter((g) => !busy.has(`${g.item_code}${logic.KEY_SEP}${g.warehouse}`));
		let { rows, renamed } = logic.merge_groups(this.state.rows, groups);
		// A refused row whose item the answer did not redraw goes back to what the server has, so
		// the review never counts a value that is not saved.
		const merged = new Set(groups.map((g) => `${g.item_code}${logic.KEY_SEP}${g.warehouse}`));
		for (const key of refused) {
			const row = rows.find((r) => r.key === key);
			if (!row) continue;
			const group = logic.group_of(row);
			if (merged.has(group) || busy.has(group)) continue;
			const back = logic.revert(rows, key, this.server.get(group));
			rows = back.rows;
			for (const [from, to] of back.renamed) renamed.set(from, to);
			for (const r of rows) if (logic.group_of(r) === group) changed.add(r.key);
		}
		this.set_rows(rows);
		if (renamed.size) this.state.visible = this.state.visible.map((k) => renamed.get(k) || k);
		for (const g of groups) for (const r of g.rows || []) changed.add(r.key);
		for (const [from, to] of renamed) {
			changed.add(from);
			changed.add(to);
			// The mark goes with the row it was on.
			if (this.state.status.has(from) && !this.state.status.has(to)) {
				this.state.status.set(to, this.state.status.get(from));
				this.state.status.delete(from);
			}
		}
		this.env.render({ rows: Array.from(changed), structure: renamed.size > 0 });
		this.flush();
	}

	save_failed(session, batch, err) {
		this.state.saving = false;
		const transient = !err.status || err.status >= 500 || err.status === 409 || err.exc_type === "DuplicateEntryError";
		if (err.session_expired || err.status === 401) {
			this.requeue(batch);
			this.state.save_error = { message: "You have been signed out. Reload the page to sign in; nothing typed is lost until you do.", fatal: true };
			this.env.render({ rows: [] });
			this.settle();
			return;
		}
		if (transient) {
			this.requeue(batch);
			const wait = RETRY_MS[Math.min(this.retries, RETRY_MS.length - 1)];
			this.retries += 1;
			this.state.save_error = { message: "Not saved yet: the connection dropped. Trying again…", retrying: true };
			this.retry_timer = this.env.set_timeout(() => {
				this.retry_timer = null;
				this.flush();
			}, wait);
			this.env.render({ rows: [] });
			return;
		}
		// Refused as a whole (the count was finalized elsewhere, or access changed): say why on
		// every row it carried, and stop. What was typed stays on screen, marked, so it can be
		// written down; Post waits (save_state) until the page is reloaded.
		const message = err.message || "The server refused this save.";
		for (const entry of batch) this.state.status.set(entry.key, { state: "error", message: `Not saved: ${message}` });
		this.state.save_error = { message: `Not saved: ${message} Reload the page to see the count as it is saved.`, fatal: true };
		this.env.render({ rows: batch.map((e) => e.key) });
		this.settle();
	}

	// Rows on screen whose save was refused (a red mark).
	errors() {
		let n = 0;
		for (const [key, s] of this.state.status) if (s.state === "error" && this.by_key.has(key)) n += 1;
		return n;
	}

	// What holds a post back on the saving side (logic.post_check's `save`).
	save_state() {
		const e = this.state.save_error;
		return { errors: this.errors(), fatal: e && e.fatal ? e.message : null, unsaved: this.unsaved() };
	}

	// "I have seen them": a refused row is already back to what the server has, so clearing its
	// mark changes nothing saved. A save refused as a whole is not cleared this way.
	clear_errors() {
		if (this.state.save_error && this.state.save_error.fatal) return 0;
		const keys = [];
		for (const [key, s] of this.state.status) if (s.state === "error") keys.push(key);
		for (const key of keys) this.state.status.delete(key);
		this.env.render({ rows: keys });
		return keys.length;
	}

	// A batch put back in front of anything typed since: the newer value of a field wins.
	requeue(batch) {
		let next = new Map();
		for (const entry of batch) next = logic.queue(next, entry);
		for (const [key, entry] of this.state.pending) next = logic.queue(next, Object.assign({ key }, entry));
		this.state.pending = next;
	}

	retry_now() {
		if (this.retry_timer) {
			this.env.clear_timeout(this.retry_timer);
			this.retry_timer = null;
		}
		this.state.save_error = null;
		this.flush();
	}

	unsaved() {
		return this.state.pending.size + (this.state.saving ? 1 : 0);
	}

	// Resolves once nothing is waiting to be saved (or the queue is stuck on an error).
	idle() {
		if (!this.unsaved() || (this.state.save_error && this.state.save_error.fatal)) return Promise.resolve();
		return new Promise((resolve) => this.waiters.push(resolve));
	}

	settle() {
		if (this.unsaved() && !(this.state.save_error && this.state.save_error.fatal)) return;
		const waiters = this.waiters;
		this.waiters = [];
		waiters.forEach((fn) => fn());
	}

	refresh() {
		const t = this.state.target;
		if (!t) return Promise.resolve();
		if (t.screen === "plan") return this.load_boot(true);
		return this.idle().then(() => this.load_sheet());
	}

	// ------------------------------------------------------------------ jump and scan

	// What typed or scanned text in the search box (or a count cell) leads to: { focus: key },
	// { bin: warehouse }, { add: item }, or { message }. Exact matches on the sheet first, then the
	// server's resolve_scan (a barcode, a label URL, a Storage Location).
	resolve(text) {
		const code = String(text || "").trim();
		if (!code) return Promise.resolve({});
		const lower = code.toLowerCase();
		const scope = (this.state.sheet && this.state.sheet.scope) || [];
		const bin = scope.find((b) => b.warehouse.toLowerCase() === lower || (b.warehouse_name || "").toLowerCase() === lower);
		if (bin) return Promise.resolve({ bin: bin.warehouse });
		const local = this.find_item(code);
		if (local) return Promise.resolve({ focus: local });
		return this.env
			.call(logic.READS.resolve, { code, warehouse: this.state.view.bin || "" }, { quiet: true })
			.then((res) => {
				const r = res || {};
				if (r.type === "location") {
					const inside = scope.find((b) => b.warehouse === r.warehouse);
					if (inside) return { bin: r.warehouse };
					if (!scope.length) return { bin: r.warehouse, outside: true };
					return { message: `${r.location_name || r.warehouse} is not part of this count. Add it with Add locations.` };
				}
				if (r.type === "item") {
					const key = this.find_item(r.item_code);
					if (key) return { focus: key };
					return { add: { item_code: r.item_code, item_name: r.item_name, stock_uom: r.uom } };
				}
				return { message: r.message || `Nothing on this count or in ERPNext matches ${code}.` };
			});
	}

	// The key of `item_code`'s row: in the bin being shown first, then the first uncounted, then any.
	find_item(item_code) {
		const lower = String(item_code || "").toLowerCase();
		const rows = this.state.rows.filter((r) => String(r.item_code).toLowerCase() === lower);
		if (!rows.length) return null;
		const bin = this.state.view.bin;
		const pick = (bin && rows.find((r) => r.warehouse === bin)) || rows.find((r) => !r.counted) || rows[0];
		if (!this.state.visible.includes(pick.key)) {
			this.state.view = { filter: "all", query: "", bin: bin && pick.warehouse === bin ? bin : "" };
			this.state.visible = logic.visible_keys(this.state.rows, this.state.view);
		}
		return pick.key;
	}

	search_items(query, warehouse) {
		return this.env.call(logic.READS.search_items, { query, warehouse: warehouse || "" }, { quiet: true });
	}

	// ------------------------------------------------------------------ the count itself

	update(fields) {
		const sheet = this.state.sheet;
		if (!sheet) return Promise.reject({ message: "" });
		const args = Object.assign({ session: sheet.name }, fields || {});
		if (args.add_warehouses) args.add_warehouses = JSON.stringify(args.add_warehouses);
		// Adding locations or changing Counted At answers with the whole sheet, which replaces the
		// rows on screen: let what is being saved land first, so no edit is drawn over.
		const whole = !!args.add_warehouses || "counted_at" in args;
		return (whole ? this.idle() : Promise.resolve()).then(() => this.env.call(logic.WRITES.update, args, { quiet: true })).then((res) => {
			if (res && res.rows) {
				const view = this.state.view;
				this.set_sheet(res);
				this.state.view = view;
				this.refilter();
			} else if (res && res.count) {
				Object.assign(this.state.sheet, res.count);
				this.env.render();
			}
			this.state.boot_stale = true;
			return res;
		});
	}

	toggle_reveal() {
		this.state.revealed = !this.state.revealed;
		this.env.render();
	}

	post(mode) {
		const sheet = this.state.sheet;
		if (!sheet || this.state.posting) return Promise.reject({ message: "" });
		this.state.posting = true;
		this.env.render();
		return this.idle()
			.then(() => {
				const save = this.save_state();
				if (save.unsaved || save.fatal) {
					return Promise.reject({ message: "Some counts are not saved yet. Fix the rows marked in red, or retry, then post." });
				}
				if (save.errors) {
					return Promise.reject({
						message: `${save.errors} row(s) were not saved and show what the server has. Count them again, or clear the red marks, then post.`,
					});
				}
				return this.env.call(logic.WRITES.post, { session: sheet.name, mode: mode || "post" }, { quiet: true });
			})
			.then(
				(res) => {
					this.state.posting = false;
					this.state.post_result = res || {};
					this.state.boot_stale = true;
					if (res && res.count) Object.assign(this.state.sheet, res.count);
					this.env.render();
					this.load_sheet().then(() => {
						this.state.post_result = res || {};
						this.env.render();
					});
					return res;
				},
				(err) => {
					this.state.posting = false;
					this.state.notice = { message: (err && err.message) || "The count could not be posted.", tone: "danger" };
					this.env.render();
					return Promise.reject(err);
				}
			);
	}

	cancel() {
		const sheet = this.state.sheet;
		if (!sheet) return Promise.reject({ message: "" });
		return this.env.call(logic.WRITES.cancel, { session: sheet.name }, { quiet: true }).then(() => {
			this.state.boot_stale = true;
			this.state.pending = new Map();
			this.reset_sheet();
			this.state.carry_notice = { message: `Count ${sheet.name} was canceled. Nothing was posted.`, tone: "neutral" };
			this.go({ screen: "plan" }, true);
		});
	}
}
