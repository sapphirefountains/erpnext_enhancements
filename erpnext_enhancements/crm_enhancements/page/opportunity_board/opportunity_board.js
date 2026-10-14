// Opportunity Board (form kit P6, ERPNext TASK-2026-02774): the board that is to replace the
// patched stock Kanban. This part builds P6.1 (cards), P6.2 (rows by value stream), P6.5 (the
// forecast strip) and P6.9 (the phone layout). The stock Kanban, its patches and the list's
// redirect to it are untouched until P6.10; the /sales-pipeline TV board stays separate.
//
// The data is one GET (opportunity_board.py get_board: the open deals through frappe.get_list,
// so the caller's permissions apply). Every decision about it (ages, rows, totals, forecast, what
// a drop means, the routes) is a pure ES module, public/js/opportunity_board/board.js, loaded as
// opportunity_board.bundle.js (window.sf_opportunity_board) and run under node by
// tests/test_opportunity_board.py. This file draws and wires.
//
// Screens, each its own history entry so Back and Forward walk them (Nik's rule, 2026-09-24):
//
//   /desk/opportunity-board[/<layout>]                     stages | streams (rows by value stream)
//   /desk/opportunity-board/<layout>/<stage>               the phone's stage tab
//   /desk/opportunity-board/<layout>[/<stage>]/move/<name> the move panel, over the view under it
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
const OB_FOCUS_ATTRS = ["data-to", "data-move", "data-open", "data-tab", "data-layout", "data-panel-close"];

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

		// v16's frappe.require runs its callback even when a file failed to load (assets.js: "resolve
		// even on error"), so the callback is not proof the bundles are here: both are checked, and a
		// board without them says so instead of waiting for good.
		this.ready = new Promise((resolve) => frappe.require(OB_ASSETS, resolve)).then(() => {
			this.sf = window.sf_kit && window.sf_opportunity_board ? window.sf_kit : null;
			this.kit = this.sf ? window.sf_opportunity_board : null;
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
		this.target = target;
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
				if (this.showing()) this.render();
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
		const stage = this.kit.current_stage(this.model, this.target.stage);
		const panel_deal = this.target.panel ? this.model.by_name[this.target.panel] : null;
		const parts = [this.head_html(), this.strip_html(phone, stage)];
		if (!this.kit.board_stages(this.model).length) {
			parts.push(this.sf.state.html("empty", { title: __("The Opportunity stages could not be read.") }));
		} else {
			if (phone) parts.push(this.tabs_html(stage));
			if (!this.model.deals.length) {
				parts.push(this.sf.state.html("empty", { title: __("No open opportunities.") }));
			} else if (this.target.layout === "streams") {
				parts.push(this.rows_html(phone, stage));
			} else {
				parts.push(this.columns_html(phone, stage));
			}
		}
		parts.push(this.closebar_html());
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

	head_html() {
		const esc = this.sf.esc;
		const all = this.kit.totals(this.model.deals);
		const layout = this.target.layout;
		const seg = (key, label) =>
			`<button type="button" class="ob-seg" data-layout="${esc(key)}" aria-pressed="${layout === key}">${esc(label)}</button>`;
		const note = this.model.truncated
			? `<p class="ob-note">${esc(__("Showing the {0} most recently changed open deals.", [this.model.deals.length]))}</p>`
			: "";
		return `
			<div class="ob-head">
				<p class="ob-summary">${esc(
					all.count === 1 ? __("1 open deal") : __("{0} open deals", [all.count])
				)} &middot; <strong>${esc(this.money(all.total))}</strong></p>
				<div class="ob-layout" role="group" aria-label="${esc(__("Show the board"))}">
					${seg("stages", __("By stage"))}${seg("streams", __("By value stream"))}
				</div>
			</div>${note}`;
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
		const f = this.kit.forecast(this.model);
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
		const tabs = this.kit.columns(this.model).map((col) => {
			const on = col.stage.key === current;
			return `<button type="button" class="ob-tab" role="tab" data-tab="${esc(col.stage.key)}" aria-selected="${on}">
				<span class="ob-tab-label">${esc(col.stage.label)}</span>
				<span class="ob-tab-count">${col.count}</span>
			</button>`;
		});
		return `<div class="ob-tabs" role="tablist" aria-label="${esc(__("Stages"))}">${tabs.join("")}</div>`;
	}

	col_head_html(col) {
		const esc = this.sf.esc;
		return `
			<header class="ob-col-head">
				<h3 class="ob-col-title">${esc(col.stage.label)}</h3>
				<span class="ob-col-meta">${esc(col.count === 1 ? __("1 deal") : __("{0} deals", [col.count]))} &middot; ${esc(
					this.money(col.total)
				)}</span>
			</header>`;
	}

	cards_html(items) {
		if (!items.length) return `<p class="ob-empty">${this.sf.esc(__("No deals"))}</p>`;
		return items.map((item) => this.card_html(item.deal, item.also)).join("");
	}

	columns_html(phone, current) {
		const cols = this.kit.columns(this.model).filter((col) => !phone || col.stage.key === current);
		const html = cols
			.map(
				(col) => `
				<section class="ob-col ob-col--${col.stage.kind}" data-drop="${this.sf.esc(col.stage.key)}">
					${this.col_head_html(col)}
					<div class="ob-cards">${this.cards_html(col.deals.map((deal) => ({ deal, also: [] })))}</div>
				</section>`
			)
			.join("");
		return `<div class="ob-scroll"><div class="ob-grid" style="--ob-cols: ${cols.length}">${html}</div></div>`;
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
		const rows = this.kit.stream_rows(this.model);
		if (phone) {
			const sections = rows
				.map((row) => {
					const cell = this.kit.row_cells(this.model, row).find((c) => c.stage.key === current);
					if (!cell || !cell.items.length) return "";
					return `<section class="ob-row">${this.row_head_html(row, cell)}
						<div class="ob-cards">${this.cards_html(cell.items)}</div></section>`;
				})
				.join("");
			const col = this.kit.columns(this.model).find((c) => c.stage.key === current);
			return `<div class="ob-phone-rows">${col ? this.col_head_html(col) : ""}${
				sections || `<p class="ob-empty">${this.sf.esc(__("No deals"))}</p>`
			}</div>`;
		}
		const cols = this.kit.columns(this.model);
		const heads = cols
			.map((col) => `<div class="ob-col ob-col--head ob-col--${col.stage.kind}">${this.col_head_html(col)}</div>`)
			.join("");
		const body = rows
			.map((row) => {
				const cells = this.kit
					.row_cells(this.model, row)
					.map(
						(cell) =>
							`<div class="ob-cell ob-col--${cell.stage.kind}" data-drop="${this.sf.esc(cell.stage.key)}">${this.cards_html(
								cell.items
							)}</div>`
					)
					.join("");
				return `<section class="ob-row">${this.row_head_html(row)}<div class="ob-grid" style="--ob-cols: ${cols.length}">${cells}</div></section>`;
			})
			.join("");
		return `<div class="ob-scroll"><div class="ob-grid ob-grid--heads" style="--ob-cols: ${cols.length}">${heads}</div>${body}</div>`;
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
					${chips ? `<div class="ob-chips">${chips}</div>` : ""}${also_html}
					<div class="ob-facts">${amount}<span class="ob-close">${esc(close)}</span></div>
					<div class="ob-foot">
						${this.age_html(deal)}${this.meter_html(deal)}
						<button type="button" class="ob-move" data-move="${esc(deal.name)}" aria-label="${esc(
							__("Move {0} to another stage", [deal.title])
						)}">${esc(__("Move"))}</button>
					</div>
				</div>
			</article>`;
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
				<div class="ob-panel-foot">
					<button type="button" class="sf-btn sf-btn--secondary" data-open="${esc(deal.name)}">${esc(
						__("Open opportunity")
					)}</button>
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
		hit = el.closest("[data-layout]");
		if (hit) return this.go({ layout: hit.getAttribute("data-layout"), stage: this.target.stage, panel: "" });
		hit = el.closest("[data-tab]");
		if (hit) return this.go({ layout: this.target.layout, stage: hit.getAttribute("data-tab"), panel: "" });
		hit = el.closest("[data-move]");
		if (hit) return this.open_panel(hit.getAttribute("data-move"));
		if (el.closest(".ob-panel")) {
			hit = el.closest("[data-open]");
			if (hit) this.open_record(hit.getAttribute("data-open"));
			return;
		}
		hit = el.closest(".ob-card[data-name]");
		if (!hit) return;
		const name = hit.getAttribute("data-name");
		// On a phone a tap picks the card up (tap to move); the panel has "Open opportunity".
		if (this.is_phone()) this.open_panel(name);
		else this.open_record(name);
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
