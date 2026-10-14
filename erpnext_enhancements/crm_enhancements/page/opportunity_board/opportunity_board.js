// Opportunity Board (form kit P6, ERPNext TASK-2026-02774): the board that is to replace the
// patched stock Kanban. Part A (v1.654.0) built P6.1 (cards), P6.2 (rows by value stream), P6.5
// (the forecast strip) and P6.9 (the phone layout). Part B (v1.663.0) adds P6.3 (the board, a
// sortable table and a timeline of close dates, under one filter bar), P6.4 (the needs-attention
// list) and P6.8 (On Hold folded, snooze, quick filters and saved views). The stock Kanban, its
// patches and the list's redirect to it are untouched until P6.10; the /sales-pipeline TV board
// stays separate.
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
// and reloads the board; nothing is overwritten. A drop on Closed Won or Lost saves nothing: those
// need the ranks or the lost reasons, which the side panel (P6.6) will collect, so until then it
// opens that deal's guided page on its Close step and the card stays where it was.
//
// A card's click opens the record with frappe.set_route("Form", "Opportunity", name), which
// public/js/guided_default.js sends to the guided page (v1.644.0).
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
];

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

		// v16's frappe.require runs its callback even when a file failed to load (assets.js: "resolve
		// even on error"), so the callback is not proof the bundles are here: both are checked, and a
		// board without them says so instead of waiting for good.
		this.ready = new Promise((resolve) => frappe.require(OB_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_opportunity_board ? window.sf_kit : null;
			this.kit = this.sf ? window.sf_opportunity_board : null;
			if (this.kit) {
				this.filters = this.kit.clean_filters(this.kit.DEFAULT_FILTERS);
				this.sort = Object.assign({}, this.kit.DEFAULT_SORT);
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

	// The guided page's Close step for a deal dropped on Closed Won or Lost. From the panel the
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
		if (panel_deal) parts.push(this.panel_html(panel_deal));
		const focus = this.focus_marker();
		this.$root.html(parts.join(""));
		this.restore_focus(focus);
		document.body.classList.toggle("ob-panel-open", !!panel_deal);

		if (this.target.panel && !panel_deal) {
			// A move panel for a deal no longer on the board (moved, closed, or never readable).
			const target = Object.assign({}, this.target, { panel: "" });
			setTimeout(() => this.go(target, true), 0);
		}
		this.settle_focus(panel_deal);
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
		const owners = [option("", __("Anyone"), !f.owner)]
			.concat(this.kit.owners(this.model).map((o) => option(o.id, o.name, f.owner === o.id)))
			.join("");
		const streams = [option("", __("Any"), !f.stream)]
			.concat(
				this.kit
					.stream_choices(this.model)
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
				</div>
			</section>`;
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
		return `
			<div class="ob-head">
				<p class="ob-summary">${esc(count)} &middot; <strong>${esc(this.money(shown.total))}</strong>${hidden}</p>
				${switcher}
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
		return `
			<header class="ob-col-head">
				<h3 class="ob-col-title">${esc(col.stage.label)}</h3>
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
		const account =
			deal.account && deal.account !== deal.title ? `<span class="ob-card-account">${esc(deal.account)}</span>` : "";
		return `
			<article class="${classes.join(" ")}" data-name="${esc(deal.name)}">
				<div class="ob-band" aria-hidden="true">${band}</div>
				<div class="ob-card-body">
					<button type="button" class="ob-card-open" data-open="${esc(deal.name)}">
						<span class="ob-card-title">${esc(deal.title)}</span>${account}
					</button>
					${chips ? `<div class="ob-chips">${chips}</div>` : ""}${also_html}${this.snooze_mark_html(deal)}
					<div class="ob-facts">${amount}<span class="ob-close">${esc(close)}</span></div>
					<div class="ob-foot">
						${this.age_html(deal)}${this.meter_html(deal)}
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
				return `<tr data-name="${esc(deal.name)}">
					<td data-label="${esc(__("Opportunity"))}">
						<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
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
						(deal) => `<li class="ob-tl-item">
							<span class="ob-tl-date">${esc(deal.expected_closing ? this.date_text(deal.expected_closing) : "")}</span>
							<span class="ob-tl-main">
								<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
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
		// Each deal opens the normal way (the guided page, by default) until the side panel exists:
		// form kit P6.6 moves this to the panel (P4.4).
		const items = list.items
			.map(
				({ deal, reasons }) => `<li class="ob-att-item" data-name="${esc(deal.name)}">
					<div class="ob-att-main">
						<button type="button" class="ob-link" data-open="${esc(deal.name)}">${esc(deal.title)}</button>
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
					? __("Opens the close step")
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
					<input type="date" data-snooze-until min="${esc(this.kit.snooze_min(this.model.today))}" value="${esc(
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
			this.layout = hit.getAttribute("data-layout");
			this.remember();
			return this.go({ view: "board", layout: this.layout, stage: this.target.stage, panel: "" });
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
		hit = el.closest("[data-move]");
		if (hit) return this.open_panel(hit.getAttribute("data-move"));
		if (el.closest(".ob-panel")) {
			hit = el.closest("[data-open]");
			if (hit) this.open_record(hit.getAttribute("data-open"));
			return;
		}
		hit = el.closest(".ob-card[data-name]");
		if (hit) {
			const name = hit.getAttribute("data-name");
			// On a phone a tap picks the card up (tap to move); the panel has "Open opportunity".
			if (this.is_phone()) this.open_panel(name);
			else this.open_record(name);
			return;
		}
		// The table, the timeline and the attention list open the deal from anywhere on its row.
		// P6.4 asks for the side panel; until it exists (P6.6) the deal opens the normal way.
		if (el.closest("select, input, textarea, label")) return;
		hit = el.closest("[data-open], tr[data-name], .ob-att-item[data-name]");
		if (hit) this.open_record(hit.getAttribute("data-open") || hit.getAttribute("data-name"));
	}

	on_change(e) {
		const el = e.target;
		if (!el || !el.matches || !this.kit) return;
		if (el.matches("[data-filter]")) {
			this.set_filters({ [el.getAttribute("data-filter")]: el.value });
		} else if (el.matches("[data-saved-view]")) {
			this.apply_named_view(el.value);
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
			})
		);
	}

	// A view's filters, sort and layout on the page; with `navigate`, its view too (a new history
	// entry, as any change of view is).
	apply_state(state, navigate) {
		const s = this.kit.pick_view(state || {});
		this.filters = this.kit.clean_filters(Object.assign({}, this.kit.DEFAULT_FILTERS, s));
		this.sort = {
			sort: s.sort || this.kit.DEFAULT_SORT.sort,
			desc: s.desc === undefined ? this.kit.DEFAULT_SORT.desc : !!s.desc,
		};
		if (s.layout) this.layout = s.layout;
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
				if (last) this.apply_state(last, false);
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
			this.open_close_step(name, true);
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
		if (e.target.closest(".ob-move, .ob-panel, .ob-panel-backdrop")) return;
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
		else if (action === "close") this.open_close_step(name, false);
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
