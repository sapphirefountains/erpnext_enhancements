// Count Desk (v1.737.0): physical counts and audits on a laptop, built for typing, not tapping.
//
// The Inventory Scanner Audit page is a phone screen: a 680px column, one scanned item at a time,
// nothing on screen until something is scanned. This is the laptop's: pick bins from the warehouse
// tree (with what each holds, what it is worth and when it was last counted), get a sheet of every
// item they hold, type the counts down a keyboard grid, review the variances by value, post them.
//
//   /desk/count-desk                   the plan: KPIs, the tree and its presets, open and recent counts
//   /desk/count-desk/<count>           the sheet: the grid
//   /desk/count-desk/<count>/review    the review: variances by value, reasons, post or draft
//
// THE GRID. A count cell takes a number or a sum (3*24+5, 3x24 for three cases of 24). Enter saves
// and moves down, Shift+Enter up, the arrows move, Esc puts the cell back, = takes the system
// quantity, a pasted column fills down, Alt+R flags a recount, Ctrl+Z undoes, Ctrl+Enter goes to the
// review, / or Ctrl+K is the search box, Alt+1..6 the filters, ? lists them all. A wedge scanner's
// scan into any cell or the search box is a scan (an item jumps to its row, a bin label filters to the
// bin), never a quantity: logic.looks_like_scan.
//
// SAVING is the controller's queue (public/js/count_desk/controller.js): each count is saved as it is
// entered, one request at a time, retried after a dropped connection, and the page asks before it is
// left with anything unsaved. Rows are patched in place on a save so the cell being typed in keeps its
// text and caret; the grid is redrawn whole only when the rows themselves change.
//
// The decisions are public/js/count_desk/logic.js, the state and calls controller.js, both in
// count_desk.bundle.js (window.sf_count_desk) and run under node by scripts/test_count_desk.mjs.
// This file draws and wires. Every request goes through sf_kit.call(..., { quiet: true }), so frappe's
// own dialogs never cover the grid; the CSS is count_desk.css, every selector under .cd-.

frappe.pages["count-desk"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Count Desk"),
		single_column: true,
	});
	wrapper.count_desk = new CountDeskPage(page, wrapper);
};

frappe.pages["count-desk"].on_page_show = function (wrapper) {
	if (wrapper.count_desk) wrapper.count_desk.route_changed();
};

const CD_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "count_desk.bundle.js"];
const CD_PAGE = "count-desk";
const CD_QUERY_DELAY_MS = 120;
const CD_ITEM_SEARCH_DELAY_MS = 250;

class CountDeskPage {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.$root = $('<div class="cd-app"></div>').appendTo(page.main);
		this.sf = null;
		this.cd = null;
		this.ctl = null;
		this.drawn = "";
		this.menu_for = "";
		this.drawn_keys = [];
		this.tr_by_key = new Map();
		this.drawn_bins = [];
		this.tree_ids = [];
		this.open_ids = [];
		this.recent_ids = [];
		this._route_timer = null;
		this._query_timer = null;
		this.ready = new Promise((resolve) => frappe.require(CD_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_kit.state ? window.sf_kit : null;
			this.cd = window.sf_count_desk && window.sf_count_desk.Controller ? window.sf_count_desk : null;
			if (this.sf && this.cd) this.ctl = new this.cd.Controller(this.env());
		});
		page.set_secondary_action(__("Refresh"), () => this.ctl && this.ctl.refresh(), "refresh");
		this.bind();
	}

	env() {
		return {
			call: (method, args, opts) => this.sf.call(method, args, opts),
			get_route: () => frappe.get_route(),
			set_route: (route, replace) => {
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
					// Untagged: "Counts" pushes the plan instead of stepping back, and Back still works.
				}
			},
			history_back: () => window.history.back(),
			render: (change) => this.render(change),
			set_timeout: (fn, ms) => window.setTimeout(fn, ms),
			clear_timeout: (id) => window.clearTimeout(id),
		};
	}

	showing() {
		return (frappe.get_route() || [])[0] === CD_PAGE;
	}

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
			`<div class="cd-broken">${frappe.utils.escape_html(
				__("The Count Desk could not load its files. Reload the page to try again.")
			)}</div>`
		);
	}

	esc(value) {
		return this.sf.esc(value);
	}

	// For text that goes into frappe's own HTML: a dialog title, a field's label or description.
	safe(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	toast(message, tone) {
		if (this.sf && this.sf.toast) this.sf.toast(message, { tone: tone || "info" });
	}

	// A stored Datetime (system time zone, as the server keeps it) as the user reads it: the Datetime
	// control converts to the user's zone, so a label written beside it must too.
	when(value) {
		return value ? frappe.datetime.str_to_user(frappe.datetime.convert_to_user_tz(value)) : "";
	}

	form_link(doctype, name) {
		return frappe.utils.get_form_link(doctype, name);
	}

	// ------------------------------------------------------------------ events

	bind() {
		const $r = this.$root;
		$r.on("click", "[data-act]", (e) => {
			if (!this.ctl) return;
			const el = e.currentTarget;
			const act = el.getAttribute("data-act");
			e.preventDefault();
			this.act(act, el);
		});
		$r.on("change", ".cd-check", (e) => {
			const id = this.tree_ids[Number(e.currentTarget.getAttribute("data-i"))];
			if (this.ctl && id) this.ctl.toggle_node(id);
		});
		$r.on("input", ".cd-tree-filter", (e) => {
			if (!this.ctl) return;
			this.ctl.state.tree_query = e.currentTarget.value;
			this.draw_tree();
		});
		$r.on("input", ".cd-jump", (e) => {
			const text = e.currentTarget.value;
			clearTimeout(this._query_timer);
			this._query_timer = setTimeout(() => this.ctl && this.ctl.set_view({ query: text }), CD_QUERY_DELAY_MS);
		});
		$r.on("keydown", ".cd-jump", (e) => this.on_jump_key(e));
		$r.on("change", ".cd-bin-filter", (e) => this.ctl && this.ctl.set_view({ bin: e.currentTarget.value }));
		$r.on("keydown", ".cd-qty", (e) => this.on_qty_key(e));
		$r.on("blur", ".cd-qty", (e) => this.commit(e.currentTarget));
		// Selected on focus so the next number replaces it; not when a redraw hands focus back to the
		// cell someone is typing in.
		$r.on("focus", ".cd-qty", (e) => {
			if (!this._restoring) e.currentTarget.select();
		});
		$r.on("paste", ".cd-qty", (e) => this.on_paste(e));
		$r.on("change", ".cd-reason-select", (e) => {
			const key = this.key_of(e.currentTarget);
			if (key) this.ctl.change(key, { reason_code: e.currentTarget.value });
		});
		$r.on("change", ".cd-note", (e) => {
			const key = this.key_of(e.currentTarget);
			if (key) this.ctl.change(key, { reason: e.currentTarget.value });
		});
		// Capture phase: the Desk binds some of these keys itself (Shift+/ opens frappe's own shortcut
		// list), on document in the bubble phase. A key this page handles stops there, while it shows.
		document.addEventListener("keydown", (e) => this.on_global_key(e), true);
		window.addEventListener("beforeunload", (e) => {
			if (this.ctl && this.ctl.unsaved()) {
				e.preventDefault();
				e.returnValue = "";
			}
		});
	}

	act(act, el) {
		const ctl = this.ctl;
		const key = this.key_of(el);
		switch (act) {
			case "expand":
				ctl.toggle_expand(this.tree_ids[Number(el.getAttribute("data-i"))]);
				break;
			case "preset": {
				const n = ctl.apply_preset(el.getAttribute("data-kind"));
				this.toast(n ? __("{0} locations selected.", [n]) : __("No location matches that."), n ? "info" : "warning");
				break;
			}
			case "clear-selection":
				ctl.clear_selection();
				break;
			case "start":
				this.start();
				break;
			case "open":
				ctl.open_count(this.open_ids[Number(el.getAttribute("data-i"))]);
				break;
			case "open-recent":
				ctl.open_count(this.recent_ids[Number(el.getAttribute("data-i"))]);
				break;
			case "plan":
				ctl.to_plan();
				break;
			case "review":
				this.commit_focused();
				ctl.open_review();
				break;
			case "sheet":
				ctl.back_to_sheet();
				break;
			case "filter":
				ctl.set_view({ filter: el.getAttribute("data-filter") });
				break;
			case "clear-view":
				this.$root.find(".cd-jump").val("");
				ctl.set_view({ filter: "all", query: "", bin: "" });
				break;
			case "recount":
				if (key) ctl.toggle_recount(key);
				break;
			case "reveal":
				ctl.toggle_reveal();
				break;
			case "blind":
				ctl.update({ blind: ctl.state.sheet.blind ? 0 : 1 }).catch((err) => this.toast(err.message, "warning"));
				break;
			case "zero":
				this.zero_dialog(el.getAttribute("data-bin") ? this.drawn_bins[Number(el.getAttribute("data-bin"))] : ctl.state.view.bin);
				break;
			case "add-item":
				this.add_item_dialog(null);
				break;
			case "add-bins":
				this.add_bins_dialog();
				break;
			case "keys":
				this.keys_dialog();
				break;
			case "print":
				this.print_dialog();
				break;
			case "csv":
				this.download_csv();
				break;
			case "undo":
				ctl.undo();
				break;
			case "retry":
				ctl.retry_now();
				break;
			case "clear-errors":
				ctl.clear_errors();
				break;
			case "counted-at":
				this.counted_at_dialog();
				break;
			case "reload-sheet":
				ctl.refresh();
				break;
			case "post":
				this.post_confirm(el.getAttribute("data-mode") || "post");
				break;
			case "rename":
				this.rename_dialog();
				break;
			case "retry-boot":
				ctl.load_boot(true);
				break;
			case "retry-sheet":
				ctl.load_sheet();
				break;
			case "notes":
				this.notes_dialog();
				break;
		}
	}

	key_of(el) {
		const holder = el && el.closest ? el.closest("[data-i-row]") : null;
		if (!holder) return null;
		return this.drawn_keys[Number(holder.getAttribute("data-i-row"))] || null;
	}

	// ------------------------------------------------------------------ drawing

	render(change) {
		if (!this.ctl || !this.showing()) return;
		const t = this.ctl.state.target;
		if (!t) return;
		this.menu(t);
		if (t.screen === "plan") this.render_plan();
		else if (t.screen === "review") this.render_review();
		else this.render_sheet(change);
	}

	menu(t) {
		const key = t.screen === "plan" ? "plan" : `count:${t.count}`;
		if (this.menu_for === key) return;
		this.menu_for = key;
		this.page.clear_menu();
		if (t.screen === "plan") {
			this.page.add_menu_item(__("All counts (list)"), () => frappe.set_route("List", "Inventory Count Session"));
			this.page.add_menu_item(__("Phone count page"), () => frappe.set_route("inventory-scanner-audit"));
			return;
		}
		this.page.add_menu_item(__("Open the count's form"), () => frappe.set_route("Form", "Inventory Count Session", t.count));
		this.page.add_menu_item(__("Rename this count…"), () => this.rename_dialog());
		this.page.add_menu_item(__("When it was counted…"), () => this.counted_at_dialog());
		this.page.add_menu_item(__("Notes…"), () => this.notes_dialog());
		this.page.add_menu_item(__("Keyboard shortcuts"), () => this.keys_dialog());
		this.page.add_menu_item(__("Cancel this count…"), () => this.cancel_confirm());
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
			return this.sf.state.render(el, "no-access", { title: __("You do not have access to the Count Desk."), body: err.message });
		}
		return this.sf.state.render(el, "error", { title, body: err.message, on_action: retry });
	}

	// Redraws `fn` and puts focus back where it was, by data-focus-id, with its text if it was being
	// typed in (a note on the review).
	keeping_focus(fn) {
		const active = document.activeElement;
		const id = active && active.getAttribute ? active.getAttribute("data-focus-id") : null;
		const value = id && "value" in active ? active.value : null;
		fn();
		if (!id) return;
		const again = this.$root.find(`[data-focus-id="${CSS.escape(id)}"]`)[0];
		if (!again) return;
		if (value !== null && again.value !== value) again.value = value;
		again.focus({ preventScroll: true });
	}

	// ------------------------------------------------------------------ the plan

	render_plan() {
		this.page.set_title(__("Count Desk"));
		const st = this.ctl.state;
		if (this.drawn !== "plan") {
			this.drawn = "plan";
			this.$root.html(`
				<div class="cd-plan">
					<div class="cd-stats" aria-label="${this.esc(__("Counting over the last 90 days"))}"></div>
					<div class="cd-plan-grid">
						<section class="cd-card cd-tree-card">
							<header class="cd-card-head">
								<h2 class="cd-card-title">${this.esc(__("Choose what to count"))}</h2>
								<div class="cd-presets"></div>
							</header>
							<input type="search" class="form-control cd-tree-filter" autocomplete="off"
								placeholder="${this.esc(__("Filter locations: row, bay, shelf or bin"))}"
								aria-label="${this.esc(__("Filter locations"))}">
							<div class="cd-tree-head" aria-hidden="true">
								<span></span><span>${this.esc(__("Location"))}</span><span class="n">${this.esc(__("Items"))}</span>
								<span class="n cd-node-value">${this.esc(__("Value"))}</span><span>${this.esc(__("Last counted"))}</span>
							</div>
							<div class="cd-tree" role="tree"></div>
							<footer class="cd-start-bar"></footer>
						</section>
						<aside class="cd-side">
							<section class="cd-card">
								<h2 class="cd-card-title">${this.esc(__("Open counts"))}</h2>
								<div class="cd-open-list"></div>
							</section>
							<section class="cd-card">
								<h2 class="cd-card-title">${this.esc(__("Recent counts"))}</h2>
								<div class="cd-recent-list"></div>
							</section>
						</aside>
					</div>
				</div>`);
			this.$root.find(".cd-tree-filter").val(st.tree_query || "");
		}
		const notice = st.notice;
		this.$root.find(".cd-plan-notice").remove();
		if (notice && notice.message) {
			this.$root.find(".cd-plan").prepend(this.notice_html(notice, "cd-plan-notice"));
		}
		if (!st.boot) {
			const tree = this.$root.find(".cd-tree")[0];
			if (st.boot_error) this.failed(tree, st.boot_error, () => this.ctl.load_boot(true), __("Could not load the locations."));
			else this.sf.state.render(tree, "loading", { title: __("Loading the locations…") });
			return;
		}
		this.draw_stats();
		this.draw_presets();
		this.draw_tree();
		this.draw_start_bar();
		this.draw_open();
		this.draw_recent();
	}

	notice_html(notice, extra) {
		const tone = ["danger", "warn", "good", "neutral"].includes(notice.tone) ? notice.tone : "good";
		return `<div class="cd-notice cd-notice--${tone} ${extra || ""}" role="${tone === "danger" ? "alert" : "status"}">${this.esc(
			notice.message
		)}</div>`;
	}

	draw_stats() {
		const cd = this.cd;
		const boot = this.ctl.state.boot;
		const s = boot.stats || {};
		const cover = s.coverage || {};
		const tiles = [
			{
				label: __("Coverage"),
				value: cd.percent(cover.pct),
				sub: cover.bins
					? __("{0} of {1} locations holding stock counted in {2} days · {3} never", [cover.counted, cover.bins, s.days, cover.never])
					: __("No location holds stock yet."),
				tone: cover.pct === null || cover.pct === undefined ? "" : cover.pct >= 90 ? "good" : cover.pct >= 50 ? "warn" : "danger",
			},
			{
				label: __("Accuracy"),
				value: cd.percent(s.accuracy),
				sub: s.items_counted
					? __("of {0} items counted in {1} days matched the system", [s.items_counted, s.days])
					: __("Nothing counted in the last {0} days.", [s.days]),
				tone: s.accuracy === null || s.accuracy === undefined ? "" : s.accuracy >= 95 ? "good" : s.accuracy >= 85 ? "warn" : "danger",
			},
			{
				label: __("Adjusted"),
				value: cd.money(s.adjusted_value, { signed: true }),
				sub: __("posted from {0} counts in {1} days", [s.counts || 0, s.days]),
				tone: "",
			},
			{
				label: __("Open counts"),
				value: String((boot.open_counts || []).length),
				sub: boot.supervisor ? __("across the team") : __("yours"),
				tone: "",
			},
		];
		this.$root.find(".cd-stats").html(
			tiles
				.map(
					(t) => `<div class="cd-stat ${t.tone ? `cd-stat--${t.tone}` : ""}">
						<div class="cd-stat-label">${this.esc(t.label)}</div>
						<div class="cd-stat-value">${this.esc(t.value)}</div>
						<div class="cd-stat-sub">${this.esc(t.sub)}</div>
					</div>`
				)
				.join("")
		);
	}

	draw_presets() {
		const boot = this.ctl.state.boot;
		const cd = this.cd;
		const never = cd.preset(boot.bins, "never", boot.coverage_days).length;
		const stale = cd.preset(boot.bins, "stale", boot.coverage_days).length;
		const buttons = [
			{ kind: "never", label: __("Never counted ({0})", [never]), off: !never },
			{ kind: "stale", label: __("Not counted in {0} days ({1})", [boot.coverage_days, stale]), off: !stale },
			{ kind: "value", label: __("10 most valuable of those"), off: !stale },
		];
		this.$root.find(".cd-presets").html(
			buttons
				.map(
					(b) =>
						`<button type="button" class="btn btn-xs btn-default" data-act="preset" data-kind="${b.kind}" ${b.off ? "disabled" : ""}>${this.esc(
							b.label
						)}</button>`
				)
				.join(" ")
		);
	}

	draw_tree() {
		const st = this.ctl.state;
		const el = this.$root.find(".cd-tree")[0];
		if (!el || !st.tree) return;
		const cd = this.cd;
		const days = st.boot.coverage_days;
		const matches = cd.tree_matches(st.tree, st.tree_query);
		const rows = cd.tree_rows(st.tree, st.expanded, matches);
		this.tree_ids = rows.map((n) => n.id);
		if (!rows.length) {
			this.sf.state.render(el, "empty", {
				title: st.tree_query ? __("No location matches {0}.", [st.tree_query]) : __("There are no locations to count yet."),
			});
			return;
		}
		el.innerHTML = rows
			.map((n, i) => {
				const state = cd.node_state(n, st.selected);
				const open = matches ? true : st.expanded.has(n.id);
				const toggle = n.is_bin
					? `<span class="cd-node-spacer"></span>`
					: `<button type="button" class="cd-node-toggle" data-act="expand" data-i="${i}" aria-label="${this.esc(
							open ? __("Collapse {0}", [n.label]) : __("Expand {0}", [n.label])
					  )}">${open ? "▾" : "▸"}</button>`;
				let age;
				if (n.is_bin) {
					const d = n.bin.days_since;
					if (!(Number(n.bin.on_hand_items) > 0)) age = `<span class="cd-muted">${this.esc(__("empty"))}</span>`;
					else if (d === null || d === undefined) age = this.sf.chip(__("Never counted"), "danger");
					else if (d > days) age = this.sf.chip(cd.age(d), "warn");
					else age = `<span class="cd-age-ok">${this.esc(cd.age(d))}</span>`;
				} else if (!n.holding) {
					age = `<span class="cd-muted">${this.esc(__("empty"))}</span>`;
				} else if (n.stale.length) {
					age = this.sf.chip(__("{0} of {1} due", [n.stale.length, n.holding]), n.never.length ? "danger" : "warn");
				} else {
					age = `<span class="cd-age-ok">${this.esc(__("all counted"))}</span>`;
				}
				const label = n.is_bin
					? this.esc(n.label)
					: `${this.esc(n.label)} <span class="cd-muted">${this.esc(__("{0} locations", [n.bins.length]))}</span>`;
				return `<div class="cd-node ${n.is_bin ? "cd-node--bin" : "cd-node--group"} ${state !== "none" ? "cd-node--picked" : ""}"
					role="treeitem" aria-level="${n.depth + 1}" ${n.is_bin ? "" : `aria-expanded="${open}"`} style="--cd-depth:${n.depth}">
					${toggle}
					<label class="cd-node-label"><input type="checkbox" class="cd-check" data-i="${i}" ${state === "all" ? "checked" : ""}
						data-state="${state}"> <span>${label}</span></label>
					<span class="cd-node-num">${this.esc(n.items)}</span>
					<span class="cd-node-num cd-node-value">${this.esc(cd.money(n.value, { whole: true }))}</span>
					<span class="cd-node-age">${age}</span>
				</div>`;
			})
			.join("");
		el.querySelectorAll('.cd-check[data-state="some"]').forEach((box) => {
			box.indeterminate = true;
		});
	}

	draw_start_bar() {
		const st = this.ctl.state;
		const bar = this.$root.find(".cd-start-bar");
		const totals = this.cd.selection_totals(st.tree, st.selected);
		if (!totals.bins) {
			bar.html(`<span class="cd-muted">${this.esc(__("Tick a row, bay, shelf or bin, or use a preset above. Every item they hold goes on the sheet."))}</span>`);
			return;
		}
		const over = this.cd.overlaps(st.boot.open_counts, st.selected);
		const warn = over.length
			? `<div class="cd-start-warn">${this.esc(
					__("Already in an open count: {0}.", [
						over.map((o) => `${o.count.title || o.count.name} (${o.count.counted_by_name}, ${o.shared})`).join(", "),
					])
			  )}</div>`
			: "";
		const typed = bar.find(".cd-start-title").val();
		const blind = bar.find(".cd-start-blind").prop("checked");
		bar.html(`
			<div class="cd-start-sum"><b>${this.esc(__("{0} locations", [totals.bins]))}</b> · ${this.esc(
			__("{0} items on hand", [totals.items])
		)} · ${this.esc(this.cd.money(totals.value, { whole: true }))}
				<button type="button" class="btn btn-xs btn-link" data-act="clear-selection">${this.esc(__("Clear"))}</button></div>
			${warn}
			<div class="cd-start-form">
				<input type="text" class="form-control input-sm cd-start-title" maxlength="140" placeholder="${this.esc(
					__("Title (optional)")
				)}" aria-label="${this.esc(__("Count title"))}">
				<label class="cd-start-blind-label" title="${this.esc(
					__("Hide the system quantity while counting, so it cannot steer the count. The review shows it.")
				)}"><input type="checkbox" class="cd-start-blind"> ${this.esc(__("Blind count"))}</label>
				<button type="button" class="btn btn-sm btn-primary" data-act="start">${this.esc(__("Start count"))} →</button>
			</div>`);
		if (typed) bar.find(".cd-start-title").val(typed);
		if (blind) bar.find(".cd-start-blind").prop("checked", true);
	}

	start() {
		const bar = this.$root.find(".cd-start-bar");
		const button = bar.find('[data-act="start"]').prop("disabled", true);
		this.ctl.start(bar.find(".cd-start-title").val(), bar.find(".cd-start-blind").prop("checked")).catch((err) => {
			button.prop("disabled", false);
			this.toast((err && err.message) || __("The count could not be started."), "warning");
		});
	}

	draw_open() {
		const boot = this.ctl.state.boot;
		const list = boot.open_counts || [];
		this.open_ids = list.map((c) => c.name);
		const el = this.$root.find(".cd-open-list");
		if (!list.length) {
			el.html(`<p class="cd-muted">${this.esc(__("No count is open. Choose locations to start one."))}</p>`);
			return;
		}
		el.html(
			`<ul class="cd-list">${list
				.map((c, i) => {
					const chips = [c.from_phone ? this.sf.chip(__("Phone"), "service") : "", c.blind ? this.sf.chip(__("Blind"), "neutral") : ""].join(" ");
					const when = c.modified ? frappe.datetime.comment_when(c.modified) : "";
					return `<li><button type="button" class="cd-list-row" data-act="open" data-i="${i}">
						<span class="cd-list-main">${this.esc(c.title || c.name)} ${chips}</span>
						<span class="cd-list-sub">${this.esc(c.mine ? __("You") : c.counted_by_name)} · ${this.esc(
						c.from_phone ? __("{0} lines", [c.lines]) : __("{0} locations · {1} lines", [c.scope.length, c.lines])
					)} · ${when}</span></button></li>`;
				})
				.join("")}</ul>`
		);
	}

	draw_recent() {
		const boot = this.ctl.state.boot;
		const list = boot.recent_counts || [];
		this.recent_ids = list.map((c) => c.name);
		const el = this.$root.find(".cd-recent-list");
		if (!list.length) {
			el.html(`<p class="cd-muted">${this.esc(__("Finished counts appear here with their accuracy."))}</p>`);
			return;
		}
		el.html(
			`<ul class="cd-list">${list
				.map((c, i) => {
					const acc = c.accuracy === null || c.accuracy === undefined ? "" : this.sf.chip(this.cd.percent(c.accuracy), c.accuracy >= 95 ? "good" : c.accuracy >= 85 ? "warn" : "danger");
					const more = (c.stock_reconciliations || []).length - 1;
					const sr = c.stock_reconciliation
						? `<a href="${this.esc(this.form_link("Stock Reconciliation", c.stock_reconciliation))}">${this.esc(c.stock_reconciliation)}</a> ${this.esc(
								`${more > 0 ? __("+{0} more", [more]) + " " : ""}${c.sr_status ? `(${__(c.sr_status)})` : ""}`
						  )}`
						: this.esc(__("nothing to post"));
					return `<li class="cd-recent-row"><button type="button" class="cd-list-row" data-act="open-recent" data-i="${i}">
						<span class="cd-list-main">${this.esc(c.title || c.name)} ${acc}</span>
						<span class="cd-list-sub">${this.esc(c.counted_by_name)} · ${c.end_time ? frappe.datetime.comment_when(c.end_time) : ""} · ${this.esc(
						__("{0} items", [c.items])
					)} · ${this.esc(this.cd.money(c.difference, { signed: true }))}</span></button>
						<span class="cd-recent-sr">${sr}</span></li>`;
				})
				.join("")}</ul>`
		);
	}

	// ------------------------------------------------------------------ the sheet

	render_sheet(change) {
		const st = this.ctl.state;
		const name = st.target.count;
		const sheet = st.sheet && st.sheet.name === name ? st.sheet : null;
		this.page.set_title(sheet ? sheet.title || sheet.name : __("Count Desk"));
		if (this.drawn !== `sheet:${name}`) {
			this.drawn = `sheet:${name}`;
			this.drawn_keys = [];
			this.tr_by_key = new Map();
			this.$root.html(`
				<div class="cd-sheet">
					<div class="cd-head"></div>
					<div class="cd-progress"></div>
					<div class="cd-sheet-notice"></div>
					<div class="cd-toolbar">
						<input type="search" class="form-control cd-jump" autocomplete="off" spellcheck="false"
							placeholder="${this.esc(__("Search or scan an item or location  ( / )"))}"
							aria-label="${this.esc(__("Search or scan"))}">
						<div class="cd-filters" role="group" aria-label="${this.esc(__("Show"))}"></div>
						<select class="form-control input-sm cd-bin-filter" aria-label="${this.esc(__("Location"))}"></select>
						<div class="cd-tools"></div>
					</div>
					<div class="cd-grid-wrap" tabindex="-1">
						<table class="cd-grid">
							<thead><tr>
								<th scope="col">${this.esc(__("Item"))}</th>
								<th scope="col">${this.esc(__("Unit"))}</th>
								<th scope="col" class="n">${this.esc(__("System"))}</th>
								<th scope="col" class="n">${this.esc(__("Counted"))}</th>
								<th scope="col" class="n">${this.esc(__("Variance"))}</th>
								<th scope="col" class="n">${this.esc(__("Value"))}</th>
								<th scope="col">${this.esc(__("Reason"))}</th>
								<th scope="col"><span class="cd-sr">${this.esc(__("Recount"))}</span>⚑</th>
							</tr></thead>
							<tbody></tbody>
						</table>
						<div class="cd-grid-empty"></div>
					</div>
					<div class="cd-hint">${this.esc(
						__("Enter saves and moves down · Shift+Enter up · = takes the system quantity · 3*24+5 works · a pasted column fills down · Alt+R recount · Ctrl+Z undo · Ctrl+Enter review · ? all shortcuts")
					)}</div>
				</div>`);
		}
		if (!sheet) {
			const el = this.$root.find(".cd-grid-empty")[0];
			this.$root.find(".cd-grid tbody").empty();
			if (st.sheet_error) this.failed(el, st.sheet_error, () => this.ctl.load_sheet(), __("Could not load this count."));
			else this.sf.state.render(el, "loading", { title: __("Loading the count…") });
			return;
		}
		if (change && change.rows && !change.structure) {
			this.patch_rows(change.rows);
			this.patch_bins();
			this.draw_progress();
			this.draw_filters();
			return;
		}
		this.draw_head();
		this.draw_progress();
		this.draw_sheet_notice();
		this.draw_filters();
		this.draw_bin_filter();
		this.draw_tools();
		this.draw_grid();
	}

	draw_head() {
		const st = this.ctl.state;
		const s = st.sheet;
		const finished = { Draft: __("Drafted"), Submitted: __("Posted"), Cancelled: __("Reconciliation canceled"), Mixed: __("Partly posted") };
		const status =
			s.status === "Open"
				? ""
				: s.status === "Finalized"
				? this.sf.chip(finished[s.sr_status] || __("Finished"), s.sr_status === "Cancelled" || s.sr_status === "Mixed" ? "warn" : "good")
				: this.sf.chip(__(s.status), "neutral");
		const scope = s.scoped ? __("{0} locations", [s.scope.length]) : __("counted on the phone");
		// A paper count says when it was counted; an open Count Desk count offers to say so (a phone
		// count is snapshotted as each item is scanned, and the server refuses it one).
		const when = s.counted_at ? __("counted at {0}", [this.when(s.counted_at)]) : __("live count");
		const counted_at = this.ctl.editable() && (s.scoped || s.counted_at)
			? ` · <button type="button" class="btn btn-xs btn-link cd-counted-at" data-act="counted-at" title="${this.esc(
					__("When the shelf was counted, for a count taken on paper and typed in later")
			  )}">${this.esc(when)}</button>`
			: s.counted_at
			? ` · ${this.esc(when)}`
			: "";
		const blind = s.blind
			? `<button type="button" class="btn btn-sm btn-default" data-act="reveal">${this.esc(
					st.revealed ? __("Hide the system quantity") : __("Show the system quantity")
			  )}</button>`
			: "";
		const review =
			s.status === "Open"
				? `<button type="button" class="btn btn-sm btn-primary" data-act="review">${this.esc(__("Review & post"))} →</button>`
				: `<button type="button" class="btn btn-sm btn-default" data-act="review">${this.esc(__("Result"))} →</button>`;
		this.$root.find(".cd-head").html(`
			<button type="button" class="btn btn-sm btn-default" data-act="plan">← ${this.esc(__("Counts"))}</button>
			<div class="cd-title">
				<h2 class="cd-name">${this.esc(s.title || s.name)} ${status}</h2>
				<div class="cd-sub">${this.esc(s.name)} · ${this.esc(scope)} · ${this.esc(
			__("started {0} by {1}", [s.start_time ? frappe.datetime.comment_when(s.start_time) : "", s.mine ? __("you") : s.counted_by_name])
		)}${counted_at}${s.blind ? ` · ${this.sf.chip(__("Blind"), "neutral")}` : ""}</div>
			</div>
			<div class="cd-head-actions">${blind}${review}</div>`);
	}

	draw_progress() {
		const st = this.ctl.state;
		const sum = this.ctl.summary();
		const pct = sum.expected ? Math.round((sum.counted * 100) / sum.expected) : 0;
		const hidden = this.ctl.hidden_system();
		const parts = [__("{0} of {1} counted", [sum.counted, sum.expected])];
		if (!hidden) {
			parts.push(__("{0} matched", [sum.matched]));
			if (sum.variances) parts.push(__("{0} variances, {1} net", [sum.variances, this.cd.money(sum.net_value, { signed: true })]));
		}
		if (sum.recount) parts.push(__("{0} to recount", [sum.recount]));
		let save;
		const errors = this.ctl.errors();
		if (st.save_error && st.save_error.retrying) {
			save = `<span class="cd-save cd-save--warn">${this.esc(st.save_error.message)} <button type="button" class="btn btn-xs btn-default" data-act="retry">${this.esc(
				__("Retry now")
			)}</button></span>`;
		} else if (st.save_error) {
			save = `<span class="cd-save cd-save--danger">${this.esc(st.save_error.message)}</span>`;
		} else if (st.save_stuck) {
			save = `<span class="cd-save cd-save--danger">${this.esc(st.save_stuck)}</span>`;
		} else if (st.saving || st.pending.size) {
			save = `<span class="cd-save">${this.esc(__("Saving…"))}</span>`;
		} else if (errors) {
			save = `<span class="cd-save cd-save--danger">${this.esc(
				__("{0} rows not saved: they show what is saved, and the red marks say why. Post waits for them.", [errors])
			)} <button type="button" class="btn btn-xs btn-default" data-act="clear-errors">${this.esc(__("Clear the marks"))}</button></span>`;
		} else {
			save = `<span class="cd-save cd-save--ok">${this.esc(st.sheet.status === "Open" ? __("All changes saved") : __("Read only"))}</span>`;
		}
		const undo = st.undo.length && this.ctl.editable() ? `<button type="button" class="btn btn-xs btn-link" data-act="undo">${this.esc(__("Undo"))}</button>` : "";
		this.$root.find(".cd-progress").html(`
			<div class="cd-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${pct}%"></span></div>
			<div class="cd-progress-text"><span>${this.esc(parts.join(" · "))}</span><span class="cd-progress-save">${undo}${save}</span></div>`);
	}

	draw_sheet_notice() {
		const notice = this.ctl.state.notice;
		this.$root.find(".cd-sheet-notice").html(notice && notice.message ? this.notice_html(notice) : "");
	}

	draw_filters() {
		const st = this.ctl.state;
		const counts = {};
		for (const f of this.cd.FILTERS) counts[f.key] = st.rows.filter((r) => this.cd.matches_filter(r, f.key)).length;
		const hidden = this.ctl.hidden_system();
		this.$root.find(".cd-filters").html(
			this.cd.FILTERS.filter((f) => !(hidden && (f.key === "variance" || f.key === "moved")))
				.map(
					(f, i) =>
						`<button type="button" class="cd-filter ${st.view.filter === f.key ? "cd-filter--on" : ""}" data-act="filter" data-filter="${f.key}"
						aria-pressed="${st.view.filter === f.key}" title="${this.esc(__("Alt+{0}", [i + 1]))}">${this.esc(__(f.label))} <span class="cd-filter-n">${counts[f.key]}</span></button>`
				)
				.join("")
		);
	}

	draw_bin_filter() {
		const st = this.ctl.state;
		const bins = this.cd.bins_of(st.rows);
		const select = this.$root.find(".cd-bin-filter");
		select.html(
			`<option value="">${this.esc(__("All locations ({0})", [bins.length]))}</option>` +
				bins.map((b) => `<option value="${this.esc(b.warehouse)}">${this.esc(`${b.warehouse_name} (${b.counted}/${b.rows})`)}</option>`).join("")
		);
		select.val(st.view.bin || "");
		const jump = this.$root.find(".cd-jump");
		if (document.activeElement !== jump[0] && jump.val() !== st.view.query) jump.val(st.view.query || "");
	}

	draw_tools() {
		const st = this.ctl.state;
		const s = st.sheet;
		const editable = this.ctl.editable();
		const tools = [];
		if (editable) tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="add-item">+ ${this.esc(__("Item"))}</button>`);
		if (editable && s.scoped) tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="add-bins">+ ${this.esc(__("Locations"))}</button>`);
		if (editable) tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="zero">${this.esc(__("Zero the rest…"))}</button>`);
		tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="print">${this.esc(__("Print"))}</button>`);
		tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="csv">${this.esc(__("CSV"))}</button>`);
		tools.push(`<button type="button" class="btn btn-sm btn-default" data-act="keys" aria-label="${this.esc(__("Keyboard shortcuts"))}" title="${this.esc(__("Keyboard shortcuts (?)"))}">⌨</button>`);
		this.$root.find(".cd-tools").html(tools.join(""));
	}

	draw_grid() {
		const st = this.ctl.state;
		const active = document.activeElement;
		const typing =
			active && active.classList && active.classList.contains("cd-qty")
				? { key: this.key_of(active), value: active.value, dirty: active.value !== active.dataset.drawn }
				: null;
		const keys = st.visible.filter((k) => this.ctl.by_key.has(k));
		this.drawn_keys = keys;
		const bins = new Map(this.cd.bins_of(st.rows).map((b) => [b.warehouse, b]));
		const trails = new Map((st.sheet.scope || []).map((b) => [b.warehouse, b.trail]));
		this.drawn_bins = [];
		const editable = this.ctl.editable();
		let html = "";
		let last = null;
		keys.forEach((key, i) => {
			const row = this.ctl.by_key.get(key);
			if (row.warehouse !== last) {
				last = row.warehouse;
				const b = bins.get(row.warehouse) || { counted: 0, rows: 0, todo: 0 };
				const bi = this.drawn_bins.push(row.warehouse) - 1;
				const zero = editable
					? `<button type="button" class="btn btn-xs btn-default" data-act="zero" data-bin="${bi}" ${b.todo ? "" : "hidden"}
						title="${this.esc(__("Count everything not yet counted here as 0"))}">${this.esc(__("Zero the rest…"))}</button>`
					: "";
				html += `<tr class="cd-bin-row" data-bin-row="${bi}"><th colspan="8" scope="rowgroup">
					<span class="cd-bin-name">${this.esc(row.warehouse_name)}</span>
					<span class="cd-muted">${this.esc(trails.get(row.warehouse) || "")}</span>
					<span class="cd-bin-count">${this.esc(__("{0} of {1} counted", [b.counted, b.rows]))}</span> ${zero}</th></tr>`;
			}
			html += this.row_html(row, i);
		});
		const tbody = this.$root.find(".cd-grid tbody")[0];
		tbody.innerHTML = html;
		this.tr_by_key = new Map();
		tbody.querySelectorAll("tr[data-i-row]").forEach((tr) => this.tr_by_key.set(keys[Number(tr.getAttribute("data-i-row"))], tr));
		const empty = this.$root.find(".cd-grid-empty")[0];
		if (!keys.length) {
			const filtered = st.view.filter !== "all" || st.view.query || st.view.bin;
			empty.innerHTML = this.sf.state.html("empty", {
				title: filtered ? __("No row matches. Clear the search and filters to see the whole sheet.") : __("Nothing on this count yet. Add an item to start."),
			});
			if (filtered) empty.insertAdjacentHTML("beforeend", `<p><button type="button" class="btn btn-sm btn-default" data-act="clear-view">${this.esc(__("Show everything"))}</button></p>`);
		} else {
			empty.innerHTML = "";
		}
		if (typing && this.tr_by_key.has(typing.key)) {
			const input = this.tr_by_key.get(typing.key).querySelector(".cd-qty");
			if (input) {
				if (typing.dirty) input.value = typing.value;
				this._restoring = true;
				input.focus({ preventScroll: true });
				this._restoring = false;
			}
		}
	}

	cells(row) {
		const cd = this.cd;
		const hidden = this.ctl.hidden_system();
		const status = this.ctl.state.status.get(row.key);
		// Blind: a counted row is just counted. Its colour, its reason box and its value would each
		// say whether the count matched, which is the one thing a blind count keeps from the counter.
		const tone = hidden && row.counted ? "counted" : cd.tone(row);
		const chips = [];
		if (row.storage_location) chips.push(this.esc(row.storage_location));
		if (!row.expected) chips.push(this.sf.chip(__("Not expected here"), "warn"));
		if (row.on_order > 0) chips.push(this.sf.chip(__("{0} on order", [cd.plain(row.on_order)]), "neutral"));
		if (row.counted && Math.abs(Number(row.moved) || 0) > cd.TOLERANCE && !hidden) {
			chips.push(this.sf.chip(__("moved {0} since counted", [cd.signed(row.moved)]), "service"));
		}
		if (row.group_lines > 1) chips.push(this.sf.chip(__("{0} lines, one item", [row.group_lines]), "neutral"));
		const refusal = row.refusal ? `<div class="cd-refusal">${this.esc(row.refusal)}</div>` : "";
		const item = `<div class="cd-item-name">${this.esc(row.item_name)}</div><div class="cd-item-code">${this.esc(row.item_code)}${
			chips.length ? ` · ${chips.join(" ")}` : ""
		}</div>${refusal}`;
		const system = hidden ? "•••" : cd.plain(cd.system_of(row));
		const variance = !row.counted || hidden ? "" : cd.signed(row.variance);
		const value = !row.counted || hidden || Math.abs(Number(row.value_variance) || 0) < 0.005 ? "" : cd.money(row.value_variance, { signed: true });
		const st_class = status ? `cd-st--${status.state}` : "";
		const st_title = status && status.state === "error" ? status.message : status ? __("Saving…") : "";
		return {
			class: `cd-row cd-row--${tone}${row.recount ? " cd-row--recount" : ""}${status && status.state === "error" ? " cd-row--error" : ""}`,
			item,
			uom: row.uom || "",
			system,
			counted: row.counted ? cd.plain(row.counted_qty) : "",
			variance,
			value,
			st_class,
			st_title,
			show_reason: row.counted && (tone === "over" || tone === "short" || row.reason_code),
			reason_code: row.reason_code || "",
			recount: !!row.recount,
		};
	}

	row_html(row, i) {
		const c = this.cells(row);
		const editable = this.ctl.editable() && !row.refusal;
		const reasons = (this.ctl.state.sheet.reasons || [])
			.map((r) => `<option value="${this.esc(r)}" ${r === c.reason_code ? "selected" : ""}>${this.esc(__(r))}</option>`)
			.join("");
		return `<tr class="${c.class}" data-i-row="${i}">
			<td class="cd-item">${c.item}</td>
			<td class="cd-uom">${this.esc(c.uom)}</td>
			<td class="n cd-sys" ${this.ctl.hidden_system() ? `title="${this.esc(__("Hidden while counting blind"))}"` : ""}>${this.esc(c.system)}</td>
			<td class="n cd-count"><input type="text" inputmode="decimal" class="cd-qty" autocomplete="off" spellcheck="false"
				value="${this.esc(c.counted)}" data-drawn="${this.esc(c.counted)}" ${editable ? "" : "readonly"}
				aria-label="${this.esc(__("Counted quantity of {0}", [row.item_name]))}"><span class="cd-st ${c.st_class}" title="${this.esc(c.st_title)}"></span></td>
			<td class="n cd-var">${this.esc(c.variance)}</td>
			<td class="n cd-val">${this.esc(c.value)}</td>
			<td class="cd-reason"><select class="cd-reason-select" aria-label="${this.esc(__("Reason for {0}", [row.item_name]))}" ${
			c.show_reason && this.ctl.editable() ? "" : "hidden"
		}><option value=""></option>${reasons}</select></td>
			<td class="cd-flag"><button type="button" class="cd-recount ${c.recount ? "cd-recount--on" : ""}" data-act="recount" aria-pressed="${c.recount}"
				title="${this.esc(__("Flag for a recount (Alt+R)"))}" aria-label="${this.esc(__("Recount {0}", [row.item_name]))}" ${
			row.counted && this.ctl.editable() ? "" : "hidden"
		}>⚑</button></td>
		</tr>`;
	}

	// The bin headers' "3 of 7 counted" and their Zero the rest button, after a patch.
	patch_bins() {
		const bins = new Map(this.cd.bins_of(this.ctl.state.rows).map((b) => [b.warehouse, b]));
		this.$root.find("tr[data-bin-row]").each((_, tr) => {
			const b = bins.get(this.drawn_bins[Number(tr.getAttribute("data-bin-row"))]);
			if (!b) return;
			tr.querySelector(".cd-bin-count").textContent = __("{0} of {1} counted", [b.counted, b.rows]);
			const zero = tr.querySelector('[data-act="zero"]');
			if (zero) zero.hidden = !b.todo;
		});
	}

	// Rows redrawn in place: every cell but a count the person is typing in.
	patch_rows(keys) {
		for (const key of keys || []) {
			const tr = this.tr_by_key.get(key);
			const row = this.ctl.by_key.get(key);
			if (!tr || !row) continue;
			const c = this.cells(row);
			tr.className = c.class;
			tr.querySelector(".cd-item").innerHTML = c.item;
			tr.querySelector(".cd-sys").textContent = c.system;
			tr.querySelector(".cd-var").textContent = c.variance;
			tr.querySelector(".cd-val").textContent = c.value;
			const input = tr.querySelector(".cd-qty");
			const typing = document.activeElement === input && input.value !== input.dataset.drawn;
			if (!typing) {
				input.value = c.counted;
				input.dataset.drawn = c.counted;
			}
			const st = tr.querySelector(".cd-st");
			st.className = `cd-st ${c.st_class}`;
			st.title = c.st_title;
			const select = tr.querySelector(".cd-reason-select");
			select.hidden = !(c.show_reason && this.ctl.editable());
			if (document.activeElement !== select) select.value = c.reason_code;
			const flag = tr.querySelector(".cd-recount");
			flag.hidden = !(row.counted && this.ctl.editable());
			flag.classList.toggle("cd-recount--on", c.recount);
			flag.setAttribute("aria-pressed", String(c.recount));
		}
	}

	focus_key(key, select) {
		const tr = this.tr_by_key.get(key);
		if (!tr) return false;
		const input = tr.querySelector(".cd-qty");
		input.focus({ preventScroll: true });
		tr.scrollIntoView({ block: "nearest" });
		if (select) input.select();
		return true;
	}

	focused_key() {
		const active = document.activeElement;
		return active && active.classList && active.classList.contains("cd-qty") ? this.key_of(active) : null;
	}

	commit_focused() {
		const active = document.activeElement;
		if (active && active.classList && active.classList.contains("cd-qty")) this.commit(active);
	}

	// The cell's text as a count: true when the row is done with (saved, unchanged or cleared), false
	// when it stays (a typo, or a scan that moved somewhere else).
	commit(input) {
		const key = this.key_of(input);
		const row = key ? this.ctl.row(key) : null;
		if (!row || !this.ctl.editable() || row.refusal) return true;
		const text = input.value;
		if (text === input.dataset.drawn) return true;
		this.cell_problem(input, null);
		if (!text.trim()) {
			if (row.counted) this.ctl.set_count(key, null);
			input.dataset.drawn = "";
			return true;
		}
		if (this.cd.looks_like_scan(text)) {
			input.value = input.dataset.drawn || "";
			this.jump(text);
			return false;
		}
		const got = this.cd.evaluate(text);
		const problem = got.error || this.cd.check_count(got.value, row);
		if (problem) {
			this.cell_problem(input, problem);
			return false;
		}
		input.value = this.cd.plain(got.value);
		input.dataset.drawn = input.value;
		this.ctl.set_count(key, got.value);
		if (got.expression) this.toast(__("{0} = {1}", [text.trim(), this.cd.plain(got.value)]), "info");
		return true;
	}

	cell_problem(input, problem) {
		input.classList.toggle("cd-qty--bad", !!problem);
		if (problem) {
			input.setAttribute("aria-invalid", "true");
			input.title = problem;
			this.toast(problem, "warning");
		} else {
			input.removeAttribute("aria-invalid");
			input.removeAttribute("title");
		}
	}

	move(key, step) {
		const st = this.ctl.state;
		const next = this.cd.next_key(this.drawn_keys, this.ctl.by_key, key, step, st.view.filter === "todo");
		if (next && this.focus_key(next, true)) return;
		if (step > 0) {
			const sum = this.ctl.summary();
			this.toast(
				sum.uncounted ? __("That was the last row here. {0} items on the sheet are still not counted.", [sum.uncounted]) : __("Everything on the sheet is counted. Ctrl+Enter to review."),
				"info"
			);
		}
	}

	on_qty_key(e) {
		const input = e.currentTarget;
		const key = this.key_of(input);
		if (!key) return;
		const mod = e.ctrlKey || e.metaKey;
		if (e.key === "Enter" && mod) return; // the page's capture handler: commit and review
		if (e.key === "Enter") {
			e.preventDefault();
			if (this.commit(input)) this.move(key, e.shiftKey ? -1 : 1);
			return;
		}
		if (e.key === "ArrowDown" || e.key === "ArrowUp") {
			e.preventDefault();
			if (this.commit(input)) this.move(key, e.key === "ArrowDown" ? 1 : -1);
			return;
		}
		if (e.key === "Escape") {
			e.preventDefault();
			input.value = input.dataset.drawn || "";
			this.cell_problem(input, null);
			input.select();
			return;
		}
		if (e.key === "=" && !input.value.trim() && !this.ctl.hidden_system()) {
			const row = this.ctl.row(key);
			if (!row || row.refusal) return;
			e.preventDefault();
			input.value = this.cd.plain(this.cd.system_of(row));
			if (this.commit(input)) this.move(key, 1);
			return;
		}
		if (mod && (e.key === "z" || e.key === "Z") && input.value === input.dataset.drawn) {
			e.preventDefault();
			this.ctl.undo();
			return;
		}
		if (e.altKey && e.code === "KeyR") {
			e.preventDefault();
			if (this.commit(input)) this.ctl.toggle_recount(key);
		}
	}

	on_paste(e) {
		const input = e.currentTarget;
		const key = this.key_of(input);
		const data = (e.originalEvent || e).clipboardData;
		if (!key || !data || !this.ctl.editable()) return;
		const values = this.cd.parse_paste(data.getData("text"));
		if (!values.length) return;
		e.preventDefault();
		const res = this.ctl.fill(key, values);
		const said = [__("Filled {0} rows from the pasted column.", [res.done])];
		if (res.problems.length) said.push(__("{0} values could not be used: see the red marks.", [res.problems.length]));
		if (res.refused.length) said.push(__("{0} values were on rows that cannot be counted here, and were left out.", [res.refused.length]));
		this.toast(said.join(" "), res.problems.length || res.refused.length ? "warning" : "success");
	}

	on_jump_key(e) {
		const input = e.currentTarget;
		if (e.key === "Escape") {
			e.preventDefault();
			input.value = "";
			this.ctl.set_view({ query: "" });
			return;
		}
		if (e.key === "ArrowDown") {
			e.preventDefault();
			clearTimeout(this._query_timer);
			this.ctl.set_view({ query: input.value });
			if (this.drawn_keys.length) this.focus_key(this.drawn_keys[0], true);
			return;
		}
		if (e.key !== "Enter") return;
		e.preventDefault();
		const text = input.value.trim();
		if (!text) return;
		clearTimeout(this._query_timer);
		// An exact part number on the sheet, or a search that matches rows, goes to the first match;
		// anything else (a barcode, a label URL) is resolved as a scan.
		const exact = this.ctl.find_item(text);
		if (exact) {
			input.value = "";
			this.ctl.set_view({ query: "" });
			this.focus_key(exact, true);
			return;
		}
		const matching = this.cd.visible_keys(this.ctl.state.rows, Object.assign({}, this.ctl.state.view, { query: text }));
		const scanned = /^\d{8,}$/.test(text) || /:\/\//.test(text);
		if (matching.length && !scanned) {
			this.ctl.set_view({ query: text });
			this.focus_key(matching[0], true);
			return;
		}
		input.value = "";
		this.ctl.set_view({ query: "" });
		this.jump(text);
	}

	// A scan or a typed code: go to the item's row, filter to the location, or offer to add it.
	jump(text) {
		this.ctl.resolve(text).then(
			(res) => {
				if (res.focus) {
					this.render();
					this.focus_key(res.focus, true);
				} else if (res.bin) {
					this.$root.find(".cd-jump").val("");
					this.ctl.set_view({ bin: res.bin, query: "" });
					const first = this.drawn_keys.find((k) => !this.ctl.by_key.get(k).counted) || this.drawn_keys[0];
					if (first) this.focus_key(first, true);
					const bin = this.cd.bins_of(this.ctl.state.rows).find((b) => b.warehouse === res.bin);
					this.toast(__("Showing {0}.", [bin ? bin.warehouse_name : res.bin]), "info");
				} else if (res.add) {
					this.add_item_dialog(res.add);
				} else if (res.message) {
					this.toast(res.message, "warning");
				}
			},
			(err) => this.toast((err && err.message) || __("That could not be looked up."), "warning")
		);
	}

	on_global_key(e) {
		if (!this.ctl || !this.showing() || e.defaultPrevented) return;
		if (document.querySelector(".modal.show")) return;
		const t = this.ctl.state.target;
		if (!t) return;
		const target = e.target;
		const in_field = !!(target && target.closest && target.closest("input, textarea, select, [contenteditable='true']"));
		const mod = e.ctrlKey || e.metaKey;
		const handled = () => {
			e.preventDefault();
			e.stopPropagation();
		};
		if (t.screen === "sheet" && this.ctl.state.sheet) {
			if (mod && (e.key === "k" || e.key === "K")) {
				handled();
				this.$root.find(".cd-jump").trigger("focus").trigger("select");
			} else if (mod && e.key === "Enter") {
				handled();
				this.commit_focused();
				this.ctl.open_review();
			} else if (!in_field && e.key === "/") {
				handled();
				this.$root.find(".cd-jump").trigger("focus").trigger("select");
			} else if (!in_field && e.key === "?") {
				handled();
				this.keys_dialog();
			} else if (!in_field && mod && (e.key === "z" || e.key === "Z")) {
				handled();
				this.ctl.undo();
			} else if (e.altKey && /^Digit[1-6]$/.test(e.code)) {
				handled();
				const filters = this.cd.FILTERS;
				const f = filters[Number(e.code.slice(5)) - 1];
				if (f) this.ctl.set_view({ filter: f.key });
			}
			return;
		}
		if (t.screen === "review" && mod && e.key === "Enter" && this.ctl.editable()) {
			handled();
			this.post_confirm(this.ctl.state.sheet.can_post ? "post" : "draft");
		}
	}

	// ------------------------------------------------------------------ the review

	render_review() {
		const st = this.ctl.state;
		const name = st.target.count;
		const sheet = st.sheet && st.sheet.name === name ? st.sheet : null;
		this.page.set_title(sheet ? __("Review: {0}", [sheet.title || sheet.name]) : __("Count Desk"));
		if (this.drawn !== `review:${name}`) {
			this.drawn = `review:${name}`;
			this.$root.html(`<div class="cd-review"><div class="cd-review-body"></div></div>`);
		}
		const body = this.$root.find(".cd-review-body")[0];
		if (!sheet) {
			if (st.sheet_error) this.failed(body, st.sheet_error, () => this.ctl.load_sheet(), __("Could not load this count."));
			else this.sf.state.render(body, "loading", { title: __("Loading the count…") });
			return;
		}
		this.keeping_focus(() => {
			body.innerHTML = this.review_html(sheet);
		});
		this.drawn_keys = this.review_keys || [];
	}

	review_html(s) {
		const st = this.ctl.state;
		const cd = this.cd;
		const sum = this.ctl.summary();
		const check = cd.post_check(st.rows, s.require_reason, this.ctl.save_state());
		const variances = cd.review_rows(st.rows);
		const editable = this.ctl.editable();
		this.review_keys = variances.map((g) => g.keys[0]);
		// The server refuses a post with a required reason missing; the buttons wait for it instead,
		// and for every count on screen to be saved (a refused save is not on the server).
		const blocked = check.blocked;
		const actions = [];
		if (editable) {
			if (s.can_post) {
				actions.push(`<button type="button" class="btn btn-sm btn-default" data-act="post" data-mode="draft" ${st.posting || blocked ? "disabled" : ""}>${this.esc(__("Draft for a Stock Manager"))}</button>`);
				actions.push(`<button type="button" class="btn btn-sm btn-primary" data-act="post" data-mode="post" ${st.posting || blocked ? "disabled" : ""} ${
					check.needs_reason.length ? `title="${this.esc(__("Give every variance a reason first."))}"` : ""
				}>${this.esc(st.posting ? __("Posting…") : __("Post adjustments"))}</button>`);
			} else {
				actions.push(`<button type="button" class="btn btn-sm btn-primary" data-act="post" data-mode="draft" ${st.posting || blocked ? "disabled" : ""}>${this.esc(
					st.posting ? __("Sending…") : __("Send for review")
				)}</button>`);
			}
		}
		const head = `<div class="cd-head">
			<button type="button" class="btn btn-sm btn-default" data-act="sheet">← ${this.esc(__("Back to the sheet"))}</button>
			<div class="cd-title"><h2 class="cd-name">${this.esc(__("Review"))}: ${this.esc(s.title || s.name)}</h2>
				<div class="cd-sub">${this.esc(s.name)} · ${this.esc(s.mine ? __("you") : s.counted_by_name)}${
			s.counted_at ? ` · ${this.esc(__("counted at {0}", [this.when(s.counted_at)]))}` : ""
		}</div></div>
			<div class="cd-head-actions">${actions.join("")}</div></div>`;
		const notice = st.notice && st.notice.message ? this.notice_html(st.notice) : "";
		const tiles = [
			[__("Counted"), __("{0} of {1}", [sum.counted, sum.expected])],
			[__("Accuracy"), cd.percent(sum.accuracy)],
			[__("Variances"), String(sum.variances)],
			[__("Net value"), cd.money(sum.net_value, { signed: true })],
			[__("Gross value"), cd.money(sum.gross_value)],
			[__("Moved since counted"), String(sum.moved)],
		]
			.map(([label, value]) => `<div class="cd-stat"><div class="cd-stat-label">${this.esc(label)}</div><div class="cd-stat-value">${this.esc(value)}</div></div>`)
			.join("");
		const checks = [];
		if (s.status === "Open") {
			if (check.stuck) checks.push(["danger", check.stuck]);
			else if (check.not_saved) {
				checks.push(["danger", __("{0} rows on the sheet were not saved: they show what is saved, with a red mark saying why. Count them again, or clear the marks, before posting.", [check.not_saved])]);
			}
			if (check.needs_reason.length) {
				checks.push(["danger", __("{0} variances need a reason before this can post (the settings require one).", [check.needs_reason.length])]);
			}
			if (check.recount) checks.push(["warn", __("{0} items are flagged for a recount. Recount them, or clear the flag.", [check.recount])]);
			if (check.uncounted) {
				checks.push(["neutral", __("{0} items on the sheet were not counted. Posting leaves them as they are; use Zero the rest on the sheet for ones that are not there.", [check.uncounted])]);
			}
			if (check.reserved.length) checks.push(["warn", __("{0} items have stock reserved for orders. ERPNext will not count those; they are left out.", [check.reserved.length])]);
			if (!s.can_post) checks.push(["neutral", __("Posting needs a Stock Manager. Send for review makes a draft reconciliation, dated now, for one to submit.")]);
			if (s.counted_at) {
				checks.push(["neutral", __("Counted at {0}: each variance is against what the stock ledger showed then.", [this.when(s.counted_at)])]);
			}
			checks.push(["neutral", __("What posts is each variance applied to what is on hand now, so anything taken or received since it was counted is kept.")]);
		}
		const checks_html = checks.length ? `<ul class="cd-checks">${checks.map(([tone, text]) => `<li class="cd-check-${tone}">${this.esc(text)}</li>`).join("")}</ul>` : "";
		const reasons = s.reasons || [];
		const table = variances.length
			? `<table class="cd-grid cd-review-grid"><thead><tr>
				<th scope="col">${this.esc(__("Item"))}</th><th scope="col">${this.esc(__("Location"))}</th>
				<th scope="col" class="n">${this.esc(__("System when counted"))}</th><th scope="col" class="n">${this.esc(__("Counted"))}</th>
				<th scope="col" class="n">${this.esc(__("Variance"))}</th><th scope="col" class="n">${this.esc(__("Value"))}</th>
				<th scope="col" class="n">${this.esc(__("On hand now"))}</th><th scope="col" class="n">${this.esc(__("Will post"))}</th>
				<th scope="col">${this.esc(__("Reason"))}</th><th scope="col">${this.esc(__("Note"))}</th></tr></thead><tbody>${variances
					.map((g, i) => {
						const need = s.require_reason && !g.reason_code && !g.reason;
						const options = reasons.map((r) => `<option value="${this.esc(r)}" ${r === g.reason_code ? "selected" : ""}>${this.esc(__(r))}</option>`).join("");
						const reason = editable
							? `<select class="cd-reason-select ${need ? "cd-need" : ""}" data-focus-id="reason-${i}" aria-label="${this.esc(__("Reason for {0}", [g.item_name]))}"><option value=""></option>${options}</select>`
							: this.esc(__(g.reason_code || ""));
						const note = editable
							? `<input type="text" class="form-control input-xs cd-note" data-focus-id="note-${i}" maxlength="1000" value="${this.esc(g.reason || "")}" aria-label="${this.esc(
									__("Note for {0}", [g.item_name])
							  )}">`
							: this.esc(g.reason || "");
						const flags = [g.recount_any ? this.sf.chip(__("Recount"), "warn") : "", g.clamped ? this.sf.chip(__("posts 0"), "neutral") : ""].join(" ");
						return `<tr class="cd-row cd-row--${cd.tone(g)}" data-i-row="${i}">
							<td class="cd-item"><div class="cd-item-name">${this.esc(g.item_name)}</div><div class="cd-item-code">${this.esc(g.item_code)} ${flags}</div></td>
							<td class="cd-loc">${this.esc(g.warehouse_name)}</td>
							<td class="n">${this.esc(cd.plain(g.snapshot_qty))}</td>
							<td class="n">${this.esc(cd.plain(g.counted_total))}</td>
							<td class="n cd-var">${this.esc(cd.signed(g.variance))}</td>
							<td class="n">${this.esc(cd.money(g.value_variance, { signed: true }))}</td>
							<td class="n">${this.esc(cd.plain(g.system_qty))}</td>
							<td class="n"><b>${this.esc(s.status === "Open" ? cd.plain(g.will_post) : "")}</b></td>
							<td>${reason}</td><td>${note}</td></tr>`;
					})
					.join("")}</tbody></table>`
			: `<p class="cd-muted">${this.esc(sum.counted ? __("Every item counted matched the system.") : __("Nothing has been counted yet."))}</p>`;
		const matched = st.rows.filter((r) => r.counted && cd.tone(r) === "match");
		const matched_html = matched.length
			? `<details class="cd-matched"><summary>${this.esc(__("{0} items matched", [matched.length]))}</summary><ul>${matched
					.map((r) => `<li>${this.esc(r.item_name)} <span class="cd-muted">${this.esc(r.item_code)} · ${this.esc(r.warehouse_name)} · ${this.esc(cd.plain(r.counted_qty))} ${this.esc(r.uom)}</span></li>`)
					.join("")}</ul></details>`
			: "";
		return `${head}${notice}${this.result_html(s)}<div class="cd-stats cd-stats--six">${tiles}</div>${checks_html}
			<section class="cd-card"><h2 class="cd-card-title">${this.esc(__("Variances, largest value first"))}</h2>${table}</section>${matched_html}
			${s.notes ? `<section class="cd-card"><h2 class="cd-card-title">${this.esc(__("Notes"))}</h2><div class="cd-notes">${this.esc(s.notes)}</div></section>` : ""}`;
	}

	result_html(s) {
		const res = this.ctl.state.post_result;
		if (s.status !== "Finalized" && !res) return "";
		const link = (name) => `<a href="${this.esc(this.form_link("Stock Reconciliation", name))}">${this.esc(name)}</a>`;
		const parts = [];
		if (res && res.message) parts.push(`<p>${this.esc(res.message)}</p>`);
		const made = res ? (res.posted || []).concat(res.drafts || []) : s.stock_reconciliations || (s.stock_reconciliation ? [s.stock_reconciliation] : []);
		if (made.length) {
			parts.push(
				`<p>${this.esc(made.length > 1 ? __("Reconciliations") : __("Reconciliation"))}: ${made.map(link).join(", ")}${s.sr_status ? ` (${this.esc(__(s.sr_status))})` : ""}</p>`
			);
		} else if (s.status === "Finalized") {
			parts.push(`<p>${this.esc(__("Nothing needed adjusting, so no reconciliation was made. The count stays on record."))}</p>`);
		}
		if (res && (res.skipped || []).length) {
			parts.push(
				`<p><b>${this.esc(__("Not posted"))}</b></p><ul class="cd-skipped">${res.skipped
					.map((r) => `<li>${this.esc(r.item_code)} · ${this.esc(r.warehouse)}: ${this.esc(r.reason)}</li>`)
					.join("")}</ul>`
			);
		}
		return `<div class="cd-notice cd-notice--good cd-result" role="status">${parts.join("")}</div>`;
	}

	// ------------------------------------------------------------------ dialogs

	post_confirm(mode) {
		const st = this.ctl.state;
		const s = st.sheet;
		if (!s || !this.ctl.editable()) return;
		const check = this.cd.post_check(st.rows, s.require_reason, this.ctl.save_state());
		if (check.blocked) {
			const why = check.stuck
				? check.stuck
				: check.not_saved
				? __("{0} rows were not saved: count them again, or clear the red marks, first.", [check.not_saved])
				: check.nothing
				? __("Nothing has been counted yet.")
				: __("Give every variance a reason first: {0} still need one.", [check.needs_reason.length]);
			this.toast(why, "warning");
			return;
		}
		const review = this.cd.review_rows(st.rows);
		const sum = this.ctl.summary();
		const net = this.cd.money(sum.net_value, { signed: true });
		const message =
			mode === "post"
				? __("Post {0} adjustments ({1} net) as submitted Stock Reconciliations? Stock changes now. A post of more than 100 items is split into reconciliations of 100. Each can be canceled from its form.", [review.length, net])
				: __("Make draft Stock Reconciliations with {0} adjustments ({1} net) for a Stock Manager to submit? They are dated now, so anything that moves before they are submitted is kept.", [review.length, net]);
		frappe.confirm(this.safe(message), () => {
			this.ctl.state.notice = null;
			this.ctl.post(mode).then(
				(res) => this.toast((res && res.message) || __("Done."), "success"),
				() => {}
			);
		});
	}

	cancel_confirm() {
		if (!this.ctl.editable()) {
			this.toast(__("Only an open count can be canceled."), "warning");
			return;
		}
		frappe.confirm(this.safe(__("Cancel this count? Nothing is posted, and its counts stay on the record as canceled.")), () =>
			this.ctl.cancel().catch((err) => this.toast((err && err.message) || __("Could not cancel."), "warning"))
		);
	}

	rename_dialog() {
		const s = this.ctl.state.sheet;
		if (!s || !this.ctl.editable()) return;
		frappe.prompt(
			[{ fieldtype: "Data", fieldname: "title", label: __("Title"), default: s.title || "", reqd: 1 }],
			(values) => this.ctl.update({ title: values.title }).catch((err) => this.toast(err.message, "warning")),
			__("Rename the count"),
			__("Save")
		);
	}

	// Counted At: when the shelf was counted, for a paper count typed in later. The server re-reads
	// every counted row's system quantity as of then, so the sheet comes back whole.
	counted_at_dialog() {
		const s = this.ctl.state.sheet;
		if (!s) return;
		if (!this.ctl.editable() || !(s.scoped || s.counted_at)) {
			const when = s.counted_at
				? __("Counted at {0}.", [this.when(s.counted_at)])
				: s.scoped
				? __("Counted live: each row against the system as it was typed.")
				: __("A count taken on the phone is counted as each item is scanned, so it has no Counted At.");
			frappe.msgprint({ title: __("When it was counted"), message: this.safe(when) });
			return;
		}
		frappe.prompt(
			[
				{
					fieldtype: "Datetime",
					fieldname: "counted_at",
					label: __("Counted At"),
					default: s.counted_at || "",
					description: __(
						"For a count taken on paper and typed in later: when the shelf was counted. Each row is then counted against what the stock ledger showed at that time, and anything taken or received since is kept. Leave it blank for a live count. Changing it re-reads the system quantity of every row already counted."
					),
				},
			],
			(values) =>
				this.ctl.update({ counted_at: values.counted_at || "" }).then(
					() => this.toast(values.counted_at ? __("Counted at {0}.", [this.when(values.counted_at)]) : __("This is a live count again."), "success"),
					(err) => this.toast((err && err.message) || __("Could not change when it was counted."), "warning")
				),
			__("When was this counted?"),
			__("Save")
		);
	}

	notes_dialog() {
		const s = this.ctl.state.sheet;
		if (!s) return;
		if (!this.ctl.editable()) {
			frappe.msgprint({ title: __("Notes"), message: `<div class="cd-notes">${this.safe(s.notes || __("No notes."))}</div>` });
			return;
		}
		frappe.prompt(
			[{ fieldtype: "Small Text", fieldname: "notes", label: __("Notes"), default: s.notes || "" }],
			(values) => this.ctl.update({ notes: values.notes || "" }).catch((err) => this.toast(err.message, "warning")),
			__("Notes on this count"),
			__("Save")
		);
	}

	zero_dialog(warehouse) {
		const st = this.ctl.state;
		const entries = this.cd.zero_plan(st.rows, warehouse || "");
		const where = warehouse ? (this.cd.bins_of(st.rows).find((b) => b.warehouse === warehouse) || {}).warehouse_name || warehouse : __("this count");
		if (!entries.length) {
			this.toast(__("Everything in {0} is counted already.", [where]), "info");
			return;
		}
		frappe.confirm(
			this.safe(__("Count the {0} items not yet counted in {1} as 0? Use it once you have counted everything that is there: an item you did not find is not there. Ctrl+Z undoes it.", [entries.length, where])),
			() => {
				const n = this.ctl.zero_rest(warehouse || "");
				this.toast(__("{0} items counted as 0.", [n]), "success");
			}
		);
	}

	keys_dialog() {
		const rows = [
			["Enter", __("Save the count and move down")],
			["Shift+Enter", __("Save and move up")],
			["↑ ↓", __("Save and move")],
			["Esc", __("Put the cell back as it was")],
			["=", __("In an empty cell: the system quantity (not on a blind count)")],
			["3*24+5, 3x24", __("A sum: three cases of 24 and five loose")],
			[__("Paste a column"), __("Fills down from the cell")],
			["Alt+R", __("Flag the row for a recount")],
			["Ctrl+Z", __("Undo the last change")],
			["/ , Ctrl+K", __("Search, or scan into the search box")],
			["Alt+1 … Alt+6", __("All · To count · Variances · Recount · Moved · Not expected")],
			["Ctrl+Enter", __("Review and post")],
			[__("Scan anywhere"), __("An item goes to its row; a location label shows that location")],
		];
		const d = new frappe.ui.Dialog({
			title: __("Keyboard shortcuts"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "keys",
					options: `<table class="cd-keys">${rows.map(([k, v]) => `<tr><th scope="row"><kbd>${this.safe(k)}</kbd></th><td>${this.safe(v)}</td></tr>`).join("")}</table>`,
				},
			],
		});
		d.show();
	}

	add_item_dialog(prefill) {
		const st = this.ctl.state;
		const s = st.sheet;
		if (!s || !this.ctl.editable()) return;
		const open = (bins) => {
			if (!bins.length) {
				this.toast(__("Scan or pick a location first."), "warning");
				return;
			}
			const here = st.view.bin || (this.ctl.row(this.focused_key() || "") || {}).warehouse || bins[0].warehouse;
			let picked = prefill && prefill.item_code ? prefill : null;
			let timer = null;
			let seq = 0;
			const d = new frappe.ui.Dialog({
				title: __("Add an item to the count"),
				fields: [
					{ fieldtype: "Data", fieldname: "query", label: __("Find the item"), placeholder: __("Part number, name or barcode") },
					{ fieldtype: "HTML", fieldname: "results" },
					{
						fieldtype: "Select",
						fieldname: "bin",
						label: __("Location"),
						options: bins.map((b) => ({ value: b.warehouse, label: b.warehouse_name })),
						default: bins.some((b) => b.warehouse === here) ? here : bins[0].warehouse,
						reqd: 1,
					},
				],
				primary_action_label: __("Add and count"),
				primary_action: () => {
					if (!picked) {
						this.toast(__("Pick an item."), "warning");
						return;
					}
					const bin = d.get_value("bin");
					d.hide();
					const key = this.ctl.add_row(picked, bin);
					setTimeout(() => this.focus_key(key, true), 0);
				},
			});
			const results = d.fields_dict.results.$wrapper;
			const show_picked = () => {
				results.html(
					picked
						? `<div class="cd-picked">${this.safe(picked.item_name || picked.item_code)} <span class="cd-muted">${this.safe(picked.item_code)}</span></div>`
						: ""
				);
			};
			const search = () => {
				const query = (d.get_value("query") || "").trim();
				const mine = ++seq;
				if (!query) {
					show_picked();
					return;
				}
				results.html(`<p class="cd-muted">${this.safe(__("Searching…"))}</p>`);
				this.ctl.search_items(query, d.get_value("bin")).then(
					(rows) => {
						if (mine !== seq) return;
						rows = rows || [];
						if (!rows.length) {
							results.html(`<p class="cd-muted">${this.safe(__("No item matches."))}</p>`);
							return;
						}
						results.html(
							`<ul class="cd-pick-list">${rows
								.map((r, i) => {
									const usable = !!r.is_stock_item;
									const here_qty = r.on_hand_here ? __("{0} here", [this.cd.plain(r.on_hand_here)]) : "";
									return `<li><button type="button" class="cd-pick" data-cd-pick="${i}" ${usable ? "" : "disabled"}>${this.safe(r.item_name)}
										<span class="cd-muted">${this.safe(r.item_code)}${usable ? (here_qty ? ` · ${this.safe(here_qty)}` : "") : ` · ${this.safe(__("not kept in stock"))}`}</span></button></li>`;
								})
								.join("")}</ul>`
						);
						results.off("click.cd").on("click.cd", "[data-cd-pick]", (e) => {
							picked = rows[Number(e.currentTarget.getAttribute("data-cd-pick"))];
							results.find(".cd-pick").removeClass("cd-pick--on");
							e.currentTarget.classList.add("cd-pick--on");
						});
					},
					(err) => {
						if (mine === seq) results.html(`<p class="cd-dialog-msg">${this.safe(err.message || "")}</p>`);
					}
				);
			};
			d.fields_dict.query.$input.on("input", () => {
				clearTimeout(timer);
				timer = setTimeout(search, CD_ITEM_SEARCH_DELAY_MS);
			});
			show_picked();
			d.show();
			if (!picked) setTimeout(() => d.fields_dict.query.$input.trigger("focus"), 100);
		};
		if (s.scoped) {
			open(s.scope);
			return;
		}
		// A phone count covers whatever was scanned: any location can take an item.
		this.ctl.load_boot().then((boot) => open(((boot && boot.bins) || []).map((b) => ({ warehouse: b.warehouse, warehouse_name: b.warehouse_name }))));
	}

	add_bins_dialog() {
		const s = this.ctl.state.sheet;
		if (!s || !s.scoped || !this.ctl.editable()) return;
		this.ctl.load_boot().then((boot) => {
			if (!boot) return;
			const have = new Set(s.scope.map((b) => b.warehouse));
			const choices = (boot.bins || []).filter((b) => !have.has(b.warehouse));
			const chosen = new Set();
			const d = new frappe.ui.Dialog({
				title: __("Add locations to this count"),
				fields: [
					{ fieldtype: "Data", fieldname: "filter", label: __("Find a location"), placeholder: __("Part of its name or its shelf") },
					{ fieldtype: "HTML", fieldname: "list" },
				],
				primary_action_label: __("Add"),
				primary_action: () => {
					if (!chosen.size) return;
					d.hide();
					this.ctl.update({ add_warehouses: Array.from(chosen) }).then(
						() => this.toast(__("{0} locations added.", [chosen.size]), "success"),
						(err) => this.toast((err && err.message) || __("Could not add them."), "warning")
					);
				},
			});
			const draw = () => {
				const want = (d.get_value("filter") || "").toLowerCase().split(/\s+/).filter(Boolean);
				const rows = choices.filter((b) => want.every((w) => `${b.warehouse_name} ${b.trail}`.toLowerCase().includes(w))).slice(0, 60);
				d.fields_dict.list.$wrapper.html(
					rows.length
						? `<ul class="cd-pick-list">${rows
								.map(
									(b) =>
										`<li><label class="cd-pick"><input type="checkbox" data-cd-bin="${choices.indexOf(b)}" ${chosen.has(b.warehouse) ? "checked" : ""}> ${this.safe(
											b.warehouse_name
										)} <span class="cd-muted">${this.safe(b.trail || "")} · ${this.safe(__("{0} items", [b.on_hand_items]))}</span></label></li>`
								)
								.join("")}</ul>`
						: `<p class="cd-muted">${this.safe(__("No other location matches."))}</p>`
				);
			};
			d.fields_dict.filter.$input.on("input", draw);
			d.fields_dict.list.$wrapper.on("change", "[data-cd-bin]", (e) => {
				const b = choices[Number(e.currentTarget.getAttribute("data-cd-bin"))];
				if (e.currentTarget.checked) chosen.add(b.warehouse);
				else chosen.delete(b.warehouse);
			});
			draw();
			d.show();
		});
	}

	print_dialog() {
		const s = this.ctl.state.sheet;
		if (!s) return;
		const d = new frappe.ui.Dialog({
			title: __("Print the count sheet"),
			fields: [
				{
					fieldtype: "Select",
					fieldname: "kind",
					label: __("Print"),
					options: [
						{ value: "blank", label: __("A blank sheet, to count on a clipboard and type in afterwards") },
						{ value: "counts", label: __("The sheet with the counts so far") },
					],
					default: "blank",
				},
				{ fieldtype: "Check", fieldname: "blind", label: __("Leave out the system quantity"), default: s.blind ? 1 : 0 },
				{ fieldtype: "Check", fieldname: "visible", label: __("Only the rows showing now (the search and filters)"), default: 0 },
			],
			primary_action_label: __("Print"),
			primary_action: (values) => {
				d.hide();
				const st = this.ctl.state;
				const rows = values.visible ? this.drawn_keys.map((k) => this.ctl.by_key.get(k)).filter(Boolean) : st.rows;
				const html = this.cd.print_html(s, rows, {
					blank: values.kind === "blank",
					blind: !!values.blind,
					printed_on: frappe.datetime.str_to_user(frappe.datetime.now_datetime()),
				});
				const w = window.open("", "_blank");
				if (!w) {
					this.toast(__("The browser blocked the print window. Allow pop-ups for this site and try again."), "warning");
					return;
				}
				w.document.open();
				w.document.write(html);
				w.document.close();
				w.focus();
				setTimeout(() => w.print(), 250);
			},
		});
		d.show();
	}

	download_csv() {
		const st = this.ctl.state;
		const s = st.sheet;
		if (!s) return;
		const csv = this.cd.to_csv(st.rows, { blind: this.ctl.hidden_system() });
		const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `${s.name}.csv`;
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
}
